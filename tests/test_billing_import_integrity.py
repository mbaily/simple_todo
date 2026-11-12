"""Imports cannot duplicate tracked charges or cross account boundaries."""
import copy
import json
from datetime import date

import pytest

from billing_service import record_cash_payment, record_invoice, sync_invoice_due_date
from import_logic import import_data_from_json
from models import Bill, BillPayment, BillInvoice, BillAdjustment, BillStatement
from test_billing_accounts import account, day, post


@pytest.fixture(autouse=True)
def cash_day(monkeypatch):
    class ReviewDate(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 8)
    monkeypatch.setattr('billing_service.date_type', ReviewDate)
    monkeypatch.setattr('main.date_type', ReviewDate)


def settled(account):
    db, bill, _ = account
    inv = record_invoice(db, bill, '100', day('2026-09-26'), invoice_date=day('2026-09-10'))
    receipt = record_cash_payment(db, bill, 10000, day('2026-09-20'), inv)
    db.commit()
    return inv, receipt


def second_account(db):
    other = Bill(name='Other supplier', frequency='quarterly')
    db.add(other)
    db.flush()
    inv = record_invoice(db, other, '50', day('2026-10-26'), invoice_date=day('2026-10-01'))
    db.commit()
    return other, inv


def rejected(account, data, message):
    db, bill, client = account
    before = client.get('/api/export').json()['data']
    data = dict(data, folders=[dict(id=99, name='Do not create')])
    payload = {'data': data}
    with pytest.raises(ValueError, match=message):
        import_data_from_json(db, copy.deepcopy(payload))
    db.rollback()
    assert client.get('/api/export').json()['data'] == before
    response = client.post('/api/import', files={'file': ('bad.json', json.dumps(payload), 'application/json')},
                           follow_redirects=False)
    assert response.status_code == 303 and 'import_error=' in response.headers['location']
    assert client.get('/api/export').json()['data'] == before


def test_disabling_import_cannot_duplicate_settled_invoice_on_next_payment(account):
    db, bill, client = account
    inv, receipt = settled(account)
    rejected(account, {'bills': [dict(id=bill.id, ledger_enabled=False)]}, 'tracking must stay enabled')
    post(client, f'/bills/{bill.id}/extra-payment', {'amount_paid': '10', 'paid_date': '2026-09-21'})
    assert bill.ledger_enabled and [i.id for i in bill.invoices] == [inv.id]
    assert sum(i.amount_cents for i in bill.invoices) == 10000
    assert sum(p.amount_cents for p in bill.payments) == 11000
    assert bill.outstanding_cents == 0 and bill.credit_cents == 1000


@pytest.mark.parametrize('kind', ['invoice', 'payment', 'adjustment', 'statement'])
def test_tracking_cannot_be_disabled_with_any_existing_history(account, kind):
    db, bill, client = account
    bill.ledger_enabled = True
    if kind == 'invoice':
        db.add(BillInvoice(bill=bill, amount_cents=10000))
    elif kind == 'payment':
        db.add(BillPayment(bill=bill, amount_paid=None, due_date=day('2026-09-26'), paid_date=day('2026-09-20')))
    elif kind == 'adjustment':
        db.add(BillAdjustment(bill=bill, kind='opening_credit', amount_cents=1000, effective_date=day('2026-09-01')))
    else:
        db.add(BillStatement(bill=bill, balance_cents=0, as_of_date=day('2026-09-01')))
    db.commit()
    rejected(account, {'bills': [dict(id=bill.id, ledger_enabled=False)]}, 'tracking must stay enabled')


@pytest.mark.parametrize('enabled', [None, 0, 1, 'false', 'true'])
def test_import_tracking_requires_a_boolean(account, enabled):
    db, bill, client = account
    rejected(account, {'bills': [dict(id=bill.id, ledger_enabled=enabled)]}, 'boolean invoice tracking')


def test_empty_tracking_can_be_disabled_and_legacy_cash_stays_untracked(account):
    db, bill, client = account
    bill.ledger_enabled = True
    db.commit()
    assert import_data_from_json(db, {'data': {'bills': [dict(id=bill.id, ledger_enabled=False)]}})['errors'] == 0
    assert not bill.ledger_enabled
    receipt = dict(id=99, bill_id=bill.id, amount_paid=42, paid_date='2026-09-20', due_date='2026-09-26')
    assert import_data_from_json(db, {'data': {'bill_payments': [receipt]}})['errors'] == 0
    assert not bill.ledger_enabled and not bill.invoices
    assert bill.payments[0].amount_paid == 42 and bill.payments[0].amount_cents is None
    exported = client.get('/api/export').json()
    assert import_data_from_json(db, copy.deepcopy(exported))['errors'] == 0
    assert not bill.ledger_enabled and not bill.invoices and len(bill.payments) == 1


@pytest.mark.parametrize('collection,fields', [
    ('bill_invoices', dict(amount_cents=10000, invoice_date='2026-09-10')),
    ('bill_payments', dict(amount_cents=1000, paid_date='2026-09-20', due_date='2026-09-26')),
    ('bill_adjustments', dict(kind='opening_credit', amount_cents=1000, effective_date='2026-09-01')),
    ('bill_statements', dict(balance_cents=0, as_of_date='2026-09-01')),
])
def test_new_history_cannot_be_imported_into_disabled_tracking(account, collection, fields):
    db, bill, client = account
    for bill_id in (bill.id, 50):
        rejected(account, {'bills': [dict(id=bill_id, name='Disabled', ledger_enabled=False)],
                           collection: [dict(id=99, bill_id=bill_id, **fields)]}, 'tracking must stay enabled')


@pytest.mark.parametrize('change', ['receipt_invoice', 'receipt_account', 'invoice_account', 'new_receipt', 'new_invoice'])
def test_cross_account_links_are_rejected_including_invoice_only_changes(account, change):
    db, bill, client = account
    inv, receipt = settled(account)
    other, foreign = second_account(db)
    if change == 'receipt_invoice':
        data = {'bill_payments': [dict(id=receipt.id, invoice_id=foreign.id)]}
    elif change == 'receipt_account':
        data = {'bill_payments': [dict(id=receipt.id, bill_id=other.id)]}
    elif change == 'invoice_account':
        data = {'bill_invoices': [dict(id=inv.id, bill_id=other.id)]}
    elif change == 'new_receipt':
        data = {'bill_payments': [dict(id=99, bill_id=bill.id, invoice_id=foreign.id, amount_paid=10,
                                      paid_date='2026-09-20', due_date='2026-09-26')]}
    else:
        data = {'bill_invoices': [dict(id=99, bill_id=other.id, amount_cents=1000)],
                'bill_payments': [dict(id=receipt.id, invoice_id=99)]}
    rejected(account, data, 'must belong to the same bill account')
    assert inv.due_date == day('2026-09-26') and foreign.due_date == day('2026-10-26')


@pytest.mark.parametrize('change', ['invoice_missing', 'invoice_account_missing', 'payment_account_missing'])
def test_missing_references_are_rejected_before_any_write(account, change):
    db, bill, client = account
    inv, receipt = settled(account)
    if change == 'invoice_missing':
        data = {'bill_payments': [dict(id=receipt.id, invoice_id=999)]}
    elif change == 'invoice_account_missing':
        data = {'bill_invoices': [dict(id=inv.id, bill_id=999)]}
    else:
        data = {'bill_payments': [dict(id=receipt.id, bill_id=999)]}
    rejected(account, data, 'missing (invoice|bill account)')


def test_failed_invoice_upsert_cannot_leave_a_cross_account_payment(account):
    db, bill, client = account
    inv, receipt = settled(account)
    other, foreign = second_account(db)
    # A computed property raises before bill_id is applied by the generic upsert.
    rejected(account, {'bill_invoices': [dict(id=inv.id, outstanding_cents=0, bill_id=other.id)],
                       'bill_payments': [dict(id=receipt.id, bill_id=other.id)]}, 'Import failed for bill_invoices')


def test_valid_new_account_import_and_repeated_restore_preserve_charges(account):
    db, bill, client = account
    data = {'bills': [dict(id=50, name='New supplier', ledger_enabled=True)],
            'bill_invoices': [dict(id=99, bill_id=50, amount_cents=10000, invoice_date='2026-09-10', due_date='2026-09-26')],
            'bill_payments': [dict(id=99, bill_id=50, invoice_id=99, amount_paid=110, paid_date='2026-09-20', due_date='2026-09-26')]}
    for payload in ({'data': data}, client.get('/api/export').json()):
        assert import_data_from_json(db, copy.deepcopy(payload))['errors'] == 0
    exported = client.get('/api/export').json()
    for _ in range(2):
        assert import_data_from_json(db, copy.deepcopy(exported))['errors'] == 0
        other = db.get(Bill, 50)
        assert len(other.invoices) == len(other.payments) == 1
        assert other.credit_cents == 1000 and other.outstanding_cents == 0


def test_consistent_account_transfer_rebuilds_loaded_relationships(account):
    db, bill, client = account
    inv, receipt = settled(account)
    other, foreign = second_account(db)
    assert bill.invoices and bill.payments and len(other.invoices) == 1
    data = {'bill_invoices': [dict(id=inv.id, bill_id=other.id)],
            'bill_payments': [dict(id=receipt.id, bill_id=other.id)]}
    assert import_data_from_json(db, {'data': data})['errors'] == 0
    assert not bill.invoices and not bill.payments
    assert len(other.invoices) == 2 and len(other.payments) == 1
    assert bill.outstanding_cents == bill.credit_cents == other.credit_cents == 0
    assert other.outstanding_cents == 5000


@pytest.mark.parametrize('due', ['', '2026-10-27'])
def test_edit_of_existing_foreign_link_is_rejected_without_mutating_either_account(account, due):
    db, bill, client = account
    inv, receipt = settled(account)
    other, foreign = second_account(db)
    receipt.invoice_id = foreign.id
    db.commit()
    before = client.get('/api/export').json()['data']
    response = client.post(f'/bills/payments/{receipt.id}/edit',
                           data=dict(paid_date='2026-09-21', due_date=due, amount_paid='90',
                                     note='Do not change', request_token='foreign-edit'), follow_redirects=False)
    assert response.status_code == 400 and 'same bill account' in response.text
    assert client.get('/api/export').json()['data'] == before


@pytest.mark.parametrize('operation', ['receipt', 'invoice', 'due'])
def test_service_rejects_foreign_invoice_before_mutating_anything(account, operation):
    db, bill, client = account
    other, foreign = second_account(db)
    before = client.get('/api/export').json()['data']
    with pytest.raises(ValueError, match='same bill account'):
        if operation == 'receipt':
            record_cash_payment(db, bill, 1000, day('2026-09-20'), foreign)
        elif operation == 'invoice':
            record_invoice(db, bill, '90', day('2026-10-27'), invoice=foreign)
        else:
            sync_invoice_due_date(bill, foreign, day('2026-10-27'))
    assert client.get('/api/export').json()['data'] == before
    assert not bill.ledger_enabled


def test_same_account_link_and_unlinked_extra_cash_remain_supported(account):
    db, bill, client = account
    inv, receipt = settled(account)
    for invoice_id in (None, inv.id):
        assert import_data_from_json(db, {'data': {'bill_payments': [dict(id=receipt.id, invoice_id=invoice_id)]}})['errors'] == 0
        post(client, f'/bills/payments/{receipt.id}/edit',
             dict(paid_date='2026-09-20', due_date='2026-09-27', amount_paid='100'))
        assert bill.outstanding_cents == bill.credit_cents == 0
    assert inv.due_date == day('2026-09-27')

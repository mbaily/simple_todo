"""Opening balances must obey the same account rules in forms and imports."""
import copy
import json
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from billing_accounts import record_adjustment
from billing_service import record_invoice
from import_logic import import_data_from_json
from models import Base, Bill, BillAdjustment, BillInvoice, BillPayment
from test_billing_accounts import account, day


def rejects(db, client, data, message):
    client.get('/customization')
    before = client.get('/api/export').json()['data']
    data = dict(data, folders=[dict(id=99, name='Must not create')])
    payload = {'data': data}
    with pytest.raises(ValueError, match=message + '.*No changes were saved'):
        import_data_from_json(db, copy.deepcopy(payload))
    assert client.get('/api/export').json()['data'] == before
    response = client.post('/api/import', files={'file': ('invalid-opening.json', json.dumps(payload), 'application/json')},
                           follow_redirects=False)
    query = parse_qs(urlparse(response.headers['location']).query)
    assert 'import_success' not in query and 'opening balance' in query['import_error'][0].lower()
    assert 'No changes were saved' in query['import_error'][0]
    assert client.get('/api/export').json()['data'] == before
    assert client.get('/classic/bills').status_code == client.get('/expenses').status_code == 200


@pytest.mark.parametrize('first,second', [('opening_credit', 'opening_credit'), ('opening_credit', 'opening_debt'),
                                        ('opening_debt', 'opening_credit'), ('opening_debt', 'opening_debt')])
@pytest.mark.parametrize('existing', [False, True])
def test_duplicate_openings_in_database_or_same_upload_cannot_change_credit_or_debt(account, first, second, existing):
    db, bill, client = account
    bill.ledger_enabled = True
    db.commit()
    rows = []
    if existing:
        record_adjustment(db, bill, first, '100', day('2026-09-01'))
        db.commit()
    else:
        rows.append(dict(id=98, bill_id=bill.id, kind=first, amount_cents=10000, effective_date='2026-09-01'))
    rows.append(dict(id=99, bill_id=bill.id, kind=second, amount_cents=10000, effective_date='2026-09-01'))
    rejects(db, client, {'bill_adjustments': rows}, 'Bill #1.*one opening balance')


@pytest.mark.parametrize('source', ['invoice_issue', 'invoice_due', 'invoice_created', 'payment', 'supplier_adjustment'])
def test_opening_cannot_be_imported_after_first_account_event(account, source):
    db, bill, client = account
    bill.ledger_enabled = True
    if source.startswith('invoice'):
        dates = {'invoice_date': day('2026-09-10')} if source == 'invoice_issue' else (
            {'due_date': day('2026-09-10')} if source == 'invoice_due' else {'created_at': day('2026-09-10')})
        db.add(BillInvoice(bill=bill, amount_cents=10000, **dates))
    elif source == 'payment':
        db.add(BillPayment(bill=bill, amount_paid=100, amount_cents=10000,
                           paid_date=day('2026-09-10'), due_date=day('2026-09-26')))
    else:
        db.add(BillAdjustment(bill=bill, kind='supplier_credit', amount_cents=10000, effective_date=day('2026-09-10')))
    db.commit()
    rejects(db, client, {'bill_adjustments': [dict(id=99, bill_id=bill.id, kind='opening_credit',
            amount_cents=10000, effective_date='2026-09-11')]}, 'Opening balance #99.*first recorded account event')


@pytest.mark.parametrize('source', ['invoice', 'payment', 'adjustment'])
def test_history_only_correction_cannot_move_an_event_before_existing_opening(account, source):
    db, bill, client = account
    opening = record_adjustment(db, bill, 'opening_credit', '100', day('2026-09-01'))
    if source == 'invoice':
        item = record_invoice(db, bill, '100', day('2026-09-26'), invoice_date=day('2026-09-10'))
        collection, changes = 'bill_invoices', dict(invoice_date='2026-08-31')
    elif source == 'payment':
        item = BillPayment(bill=bill, amount_paid=10, amount_cents=1000,
                           paid_date=day('2026-09-10'), due_date=day('2026-09-26'))
        db.add(item)
        collection, changes = 'bill_payments', dict(paid_date='2026-08-31')
    else:
        item = BillAdjustment(bill=bill, kind='supplier_credit', amount_cents=1000, effective_date=day('2026-09-10'))
        db.add(item)
        collection, changes = 'bill_adjustments', dict(effective_date='2026-08-31')
    db.commit()
    rejects(db, client, {collection: [dict(id=item.id, **changes)]}, f'Opening balance #{opening.id}.*first recorded account event')


def test_partial_kind_or_account_change_cannot_introduce_duplicate_opening(account):
    db, bill, client = account
    record_adjustment(db, bill, 'opening_credit', '100', day('2026-09-01'))
    extra = record_adjustment(db, bill, 'supplier_credit', '10', day('2026-09-01'))
    other = Bill(name='Other', ledger_enabled=True)
    db.add(other)
    db.flush()
    second = record_adjustment(db, other, 'opening_debt', '20', day('2026-09-01'))
    db.commit()
    rejects(db, client, {'bill_adjustments': [dict(id=extra.id, kind='opening_credit')]}, 'Bill #1.*one opening balance')
    rejects(db, client, {'bill_adjustments': [dict(id=second.id, bill_id=bill.id)]}, 'Bill #1.*one opening balance')


def test_valid_merged_opening_corrections_and_exports_do_not_duplicate_balances(account):
    db, bill, client = account
    opening = record_adjustment(db, bill, 'opening_credit', '100', day('2026-09-01'))
    invoice = record_invoice(db, bill, '120', day('2026-09-26'), invoice_date=day('2026-09-10'))
    db.commit()
    assert import_data_from_json(db, {'data': {'bill_adjustments': [dict(id=opening.id, effective_date='2026-08-31', amount_cents=15000)],
                                               'bill_invoices': [dict(id=invoice.id, invoice_date='2026-08-31')]}})['errors'] == 0
    assert bill.credit_cents == 3000 and bill.outstanding_cents == 0
    assert import_data_from_json(db, {'data': {'bill_adjustments': [dict(id=opening.id, note='Only a note')]}})['errors'] == 0
    exported = client.get('/api/export').json()
    for _ in range(2):
        assert import_data_from_json(db, copy.deepcopy(exported))['errors'] == 0
        assert bill.credit_cents == 3000 and len(bill.adjustments) == 1
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as restored:
            assert import_data_from_json(restored, copy.deepcopy(exported))['errors'] == 0
            assert restored.get(Bill, bill.id).credit_cents == 3000
    finally:
        engine.dispose()


def test_one_opening_per_account_and_same_day_events_are_valid(account):
    db, bill, client = account
    bill.ledger_enabled = True
    other = Bill(name='Other', ledger_enabled=True)
    db.add(other)
    db.commit()
    rows = [dict(id=98, bill_id=bill.id, kind='opening_credit', amount_cents=10000, effective_date='2026-09-01'),
            dict(id=99, bill_id=other.id, kind='opening_debt', amount_cents=2000, effective_date='2026-09-01')]
    assert import_data_from_json(db, {'data': {'bill_adjustments': rows,
            'bill_invoices': [dict(id=99, bill_id=bill.id, amount_cents=5000, invoice_date='2026-09-01')]}})['errors'] == 0
    assert bill.credit_cents == 5000 and other.outstanding_cents == 2000


def test_final_import_can_replace_an_opening_kind_without_a_transient_duplicate(account):
    db, bill, client = account
    opening = record_adjustment(db, bill, 'opening_credit', '100', day('2026-09-01'))
    db.commit()
    rows = [dict(id=99, bill_id=bill.id, kind='opening_credit', amount_cents=1000, effective_date='2026-09-01'),
            dict(id=opening.id, kind='supplier_credit')]
    assert import_data_from_json(db, {'data': {'bill_adjustments': rows}})['errors'] == 0
    assert bill.credit_cents == 11000 and len(bill.adjustments) == 2

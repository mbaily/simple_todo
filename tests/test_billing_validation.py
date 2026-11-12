"""Keep adjustment dates, imported cents and partial budget updates consistent."""
import copy
from datetime import date

import pytest
from sqlalchemy import text

from billing_accounts import record_adjustment, statement_comparisons
from billing_service import record_invoice, balance_as_of
from import_logic import import_data_from_json
from models import Bill, BillAdjustment
from test_billing_accounts import account, day, post
from test_billing_import_integrity import rejected


@pytest.fixture(autouse=True)
def billing_day(monkeypatch):
    class ReviewDate(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 8)
    monkeypatch.setattr('billing_service.date_type', ReviewDate)
    monkeypatch.setattr('main.date_type', ReviewDate)


def unpaid(account):
    db, bill, _ = account
    inv = record_invoice(db, bill, '100', day('2026-10-26'), invoice_date=day('2026-10-07'))
    db.commit()
    return inv


def existing_adjustment(db, bill, kind):
    if kind == 'refund':
        record_adjustment(db, bill, 'opening_credit', '100', day('2026-09-01'))
    row = record_adjustment(db, bill, kind, '20', day('2026-10-01'), request_token='original-adjustment')
    db.commit()
    return row


@pytest.mark.parametrize('kind', list(BillAdjustment.LABELS))
@pytest.mark.parametrize('editing', [False, True])
def test_future_adjustment_forms_reject_before_changing_any_account_data(account, kind, editing):
    db, bill, client = account
    row = existing_adjustment(db, bill, kind) if editing else None
    before = client.get('/api/export').json()['data']
    fields = dict(kind=kind, amount='40', effective_date='2026-10-09',
                  note='Must not change', request_token='future-adjustment')
    if row:
        fields['adjustment_id'] = row.id
    response = client.post(f'/bills/{bill.id}/adjustments', data=fields, follow_redirects=False)
    assert response.status_code == 400 and 'cannot be dated in the future' in response.text
    assert client.get('/api/export').json()['data'] == before
    if not editing:
        assert not bill.ledger_enabled


def test_future_credit_cannot_clear_current_invoice_or_advance_its_reminder(account):
    db, bill, client = account
    inv = unpaid(account)
    response = client.post(f'/bills/{bill.id}/adjustments',
                           data=dict(kind='supplier_credit', amount='100', effective_date='2026-11-01'))
    assert response.status_code == 400
    assert inv.outstanding_cents == bill.outstanding_cents == 10000
    assert bill.next_due_date == day('2026-10-26') and inv.settled_at is None
    assert balance_as_of(bill, day('2026-10-08'))[0] == bill.outstanding_cents
    post(client, f'/bills/{bill.id}/adjustments',
         dict(kind='supplier_credit', amount='100', effective_date='2026-10-08', request_token='today-credit'))
    assert bill.outstanding_cents == bill.credit_cents == 0
    assert inv.settled_at == day('2026-10-08') and bill.next_due_date == day('2027-01-26')
    post(client, f'/bills/{bill.id}/adjustments',
         dict(kind='supplier_credit', amount='100', effective_date='2026-10-08', request_token='today-credit'))
    assert len(bill.adjustments) == 1 and bill.next_due_date == day('2027-01-26')


def test_service_future_adjustment_does_not_enable_tracking(account):
    db, bill, client = account
    before = client.get('/api/export').json()['data']
    with pytest.raises(ValueError, match='future'):
        record_adjustment(db, bill, 'supplier_credit', '100', day('2026-11-01'))
    assert client.get('/api/export').json()['data'] == before


@pytest.mark.parametrize('kind', list(BillAdjustment.LABELS))
@pytest.mark.parametrize('editing', [False, True])
def test_import_future_adjustments_are_rejected_atomically_including_partial_edits(account, kind, editing):
    db, bill, client = account
    if editing:
        row = existing_adjustment(db, bill, kind)
        changed = dict(id=row.id, effective_date='2026-10-09')  # Kind and amount are inherited.
    else:
        unpaid(account)
        changed = dict(id=99, bill_id=bill.id, kind=kind, amount_cents=10000, effective_date='2026-11-01')
    rejected(account, {'bills': [dict(id=bill.id, name='Must not rename')],
                       'bill_adjustments': [changed]}, 'future')


@pytest.mark.parametrize('effective', [None, 'invalid', 42])
def test_import_adjustment_needs_a_valid_effective_date_before_other_writes(account, effective):
    db, bill, client = account
    unpaid(account)
    rejected(account, {'bill_adjustments': [dict(id=99, bill_id=bill.id, kind='supplier_credit',
                                                amount_cents=10000, effective_date=effective)]}, 'valid effective date')


def test_partial_adjustment_import_preserves_date_and_uses_today_when_explicit(account):
    db, bill, client = account
    unpaid(account)
    row = record_adjustment(db, bill, 'supplier_credit', '20', day('2026-10-07'))
    db.commit()
    assert import_data_from_json(db, {'data': {'bill_adjustments': [dict(id=row.id, amount_cents=4000)]}})['errors'] == 0
    assert row.effective_date == day('2026-10-07') and bill.outstanding_cents == 6000
    assert import_data_from_json(db, {'data': {'bill_adjustments': [dict(id=row.id, amount_cents=10000,
                                                                     effective_date='2026-10-08')]}})['errors'] == 0
    assert row.effective_date == day('2026-10-08') and bill.outstanding_cents == 0
    assert bill.next_due_date == day('2027-01-26')


@pytest.mark.parametrize('strategy', ['fixed', 'last_payment', 'rolling_avg'])
def test_name_only_bill_import_preserves_budget_strategy_and_monthly_totals(account, strategy):
    db, bill, client = account
    bill.amount_strategy, bill.expected_amount = strategy, 500
    unpaid(account)
    before = (bill.budget_amount, bill.expected_amount, bill.frequency, bill.next_due_date)
    monthly = client.get('/expenses').context['bills_monthly']
    for _ in range(2):
        assert import_data_from_json(db, {'data': {'bills': [dict(id=bill.id, name='Renamed utility')]}})['errors'] == 0
        assert bill.name == 'Renamed utility' and bill.amount_strategy == strategy
        assert (bill.budget_amount, bill.expected_amount, bill.frequency, bill.next_due_date) == before
        assert client.get('/classic/bills').context['monthly_total'] == monthly
        assert client.get('/expenses').context['bills_monthly'] == monthly


def test_import_can_explicitly_change_strategy_and_new_bills_keep_default(account):
    db, bill, client = account
    bill.expected_amount = 500
    unpaid(account)
    assert bill.budget_amount == 100
    assert import_data_from_json(db, {'data': {'bills': [dict(id=bill.id, amount_strategy='fixed'),
                                                       dict(id=99, name='New reminder', expected_amount=42)]}})['errors'] == 0
    assert bill.amount_strategy == 'fixed' and bill.budget_amount == 500
    new = db.get(Bill, 99)
    assert new.amount_strategy == 'fixed' and new.budget_amount == 42 and not new.ledger_enabled


CENT_FIELDS = [('bill_invoices', 'amount_cents'), ('bill_invoices', 'statement_balance_cents'),
               ('bill_adjustments', 'amount_cents'), ('bill_statements', 'balance_cents')]


@pytest.mark.parametrize('collection,field', CENT_FIELDS)
@pytest.mark.parametrize('value', [0.5, 10000.5, 100.0, True, '100', float('nan'), float('inf'),
                                  9223372036854775808, -9223372036854775809])
def test_noninteger_or_out_of_range_cents_stop_the_whole_import(account, collection, field, value):
    db, bill, client = account
    inv = unpaid(account)
    if collection == 'bill_invoices':
        changed = dict(id=inv.id, **{field: value})
    elif collection == 'bill_adjustments':
        changed = dict(id=99, bill_id=bill.id, kind='supplier_credit', amount_cents=value, effective_date='2026-10-08')
    else:
        changed = dict(id=99, bill_id=bill.id, balance_cents=value, as_of_date='2026-10-08')
    rejected(account, {'bills': [dict(id=bill.id, name='Must not rename')], collection: [changed]}, field + ' needs a .*integer')


@pytest.mark.parametrize('collection,field,value', [
    ('bill_invoices', 'amount_cents', None), ('bill_invoices', 'amount_cents', -1),
    ('bill_adjustments', 'amount_cents', None), ('bill_adjustments', 'amount_cents', 0),
    ('bill_adjustments', 'amount_cents', -1), ('bill_statements', 'balance_cents', None),
])
def test_required_charges_and_adjustments_keep_their_amount_constraints(account, collection, field, value):
    db, bill, client = account
    inv = unpaid(account)
    changed = dict(id=inv.id if collection == 'bill_invoices' else 99, bill_id=bill.id,
                   **{field: value})
    if collection == 'bill_adjustments':
        changed.update(kind='supplier_credit', effective_date='2026-10-08')
    elif collection == 'bill_statements':
        changed.update(as_of_date='2026-10-08')
    rejected(account, {collection: [changed]}, field + ' needs a .*integer')


def test_valid_integer_history_and_signed_credit_roundtrip_without_rounding(account):
    db, bill, client = account
    payload = {'data': {
        'bills': [dict(id=bill.id, ledger_enabled=True)],
        'bill_invoices': [dict(id=99, bill_id=bill.id, amount_cents=0, invoice_date='2026-10-05', statement_balance_cents=None),
                          dict(id=100, bill_id=bill.id, amount_cents=10000, invoice_date='2026-10-07',
                               due_date='2026-10-26', statement_balance_cents=-1670)],
        'bill_adjustments': [dict(id=99, bill_id=bill.id, kind='supplier_credit', amount_cents=11670,
                                 effective_date='2026-10-06')],
        'bill_statements': [dict(id=99, bill_id=bill.id, balance_cents=-1670, as_of_date='2026-10-07')],
    }}
    assert import_data_from_json(db, copy.deepcopy(payload))['errors'] == 0
    assert bill.amount_strategy == 'last_payment' and bill.credit_cents == 1670 and bill.outstanding_cents == 0
    exported = client.get('/api/export').json()
    for _ in range(2):
        assert import_data_from_json(db, copy.deepcopy(exported))['errors'] == 0
        assert bill.credit_cents == 1670 and bill.outstanding_cents == 0
        assert all(row['status'] == 'Match' for row in statement_comparisons(bill))
        for collection, field in CENT_FIELDS:
            assert not db.execute(text(f"select id from {collection} where {field} is not null and typeof({field}) != 'integer'")).all()
    assert import_data_from_json(db, {'data': {'bill_invoices': [dict(id=100, statement_balance_cents=None)]}})['errors'] == 0
    assert db.execute(text('select statement_balance_cents from bill_invoices where id=100')).scalar() is None


@pytest.mark.parametrize('value', [-9223372036854775808, 0, 9223372036854775807])
def test_signed_supplier_balances_store_exact_integer_boundaries(account, value):
    db, bill, client = account
    unpaid(account)
    payload = {'data': {'bill_statements': [dict(id=99, bill_id=bill.id, balance_cents=value, as_of_date='2026-10-08')],
                        'bill_invoices': [dict(id=bill.invoices[0].id, statement_balance_cents=value)]}}
    assert import_data_from_json(db, payload)['errors'] == 0
    assert db.execute(text('select balance_cents from bill_statements where id=99')).scalar() == value
    assert bill.invoices[0].statement_balance_cents == value

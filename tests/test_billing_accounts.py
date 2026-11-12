import copy
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from main import app, get_db
from models import Base, Bill, BillPayment, BillInvoice, BillAdjustment, BillStatement, JournalEntry
from billing_service import record_invoice, record_cash_payment
from billing_accounts import record_adjustment, record_statement, balance_as_of, statement_comparisons
from import_logic import import_data_from_json
from database import run_migrations


def day(value):
    return datetime.fromisoformat(value)


@pytest.fixture
def account(monkeypatch):
    monkeypatch.setenv('SIMPLE_TODO_BASIC_AUTH', '0')
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        bill = Bill(name='Utility', frequency='quarterly', amount_strategy='last_payment',
                    next_due_date=day('2026-10-26'))
        db.add(bill)
        db.commit()
        app.dependency_overrides[get_db] = lambda: db
        client = TestClient(app)
        try:
            yield db, bill, client
        finally:
            client.close()
            app.dependency_overrides.clear()
    engine.dispose()


def post(client, path, data):
    response = client.post(path, data=data, follow_redirects=False)
    assert response.status_code == 303, response.text


def adjustment(client, bill, kind, amount, date='2026-10-01', **extra):
    post(client, f'/bills/{bill.id}/adjustments',
         dict(kind=kind, amount=amount, effective_date=date, **extra))


def invoice(client, bill, amount='60', date='2026-10-02', **extra):
    post(client, f'/bills/{bill.id}/invoices', dict(amount=amount, invoice_date=date, **extra))
    return bill.invoices[-1]


def test_create_and_edit_expose_tracking_without_inventing_invoice(account):
    db, bill, client = account
    assert 'Track invoices, payments and account credit' in client.get('/classic/bills').text
    assert 'name="ledger_enabled"' in client.get(f'/bills/{bill.id}/edit').text
    post(client, '/bills/create', {'name': 'New account', 'ledger_enabled': 'true',
                                 'expected_amount': '100', 'next_due_date': '2026-10-26'})
    new = db.query(Bill).filter_by(name='New account').one()
    assert new.ledger_enabled and not new.invoices and not new.payments
    db.add(BillPayment(bill=bill, amount_paid=42, due_date=day('2026-09-26'), paid_date=day('2026-09-25')))
    db.commit()
    before = [(p.id, p.amount_paid, p.paid_date) for p in bill.payments]
    post(client, f'/bills/{bill.id}/update', {'name': bill.name, 'next_due_date': '2026-10-26', 'ledger_enabled': 'true'})
    assert bill.ledger_enabled and bill.outstanding_cents == bill.credit_cents == 0
    assert [(p.id, p.amount_paid, p.paid_date) for p in bill.payments] == before
    assert bill.invoices[0].is_legacy
    response = client.post(f'/bills/{bill.id}/update', data={'name': bill.name, 'next_due_date': '', 'ledger_enabled': 'false'})
    assert response.status_code == 400 and bill.ledger_enabled


def test_omitted_due_date_does_not_clear_schedule_or_change_bill(account):
    db, bill, client = account
    before = client.get('/api/export').json()['data']
    response = client.post(f'/bills/{bill.id}/update', data={'name': 'Must not rename'},
                           follow_redirects=False)
    assert response.status_code == 422
    assert response.json()['detail'][0]['loc'] == ['body', 'next_due_date']
    assert client.get('/api/export').json()['data'] == before


def test_opening_credit_pays_invoice_without_cash_and_forecasts_charge(account):
    db, bill, client = account
    adjustment(client, bill, 'opening_credit', '100')
    inv = invoice(client, bill, '60', due_date='2026-10-26')
    assert bill.credit_cents == 4000 and bill.outstanding_cents == 0
    assert inv.credit_applied_cents == 6000 and not bill.payments
    assert bill.estimated_amount == 60
    assert bill.next_due_date == day('2027-01-26')
    assert client.get('/expenses').context['all_time_bills'] == 0
    html = client.get(f'/bills/{bill.id}/history').text
    assert 'Opening credit' in html and 'Check against supplier statement' in html


def test_opening_debt_accepts_cash_without_fake_invoice_or_advancing_schedule(account):
    db, bill, client = account
    adjustment(client, bill, 'opening_debt', '100')
    assert bill.outstanding_cents == 10000 and bill.effective_amount == 100 and not bill.is_estimated
    post(client, f'/bills/{bill.id}/extra-payment', {'amount_paid': '120', 'paid_date': '2026-10-03'})
    assert bill.credit_cents == 2000 and bill.outstanding_cents == 0
    assert not bill.invoices and bill.next_due_date == day('2026-10-26')
    assert bill.payments[0].unallocated_cents == 2000
    assert client.get('/expenses').context['all_time_bills'] == 120


def test_supplier_adjustments_and_cash_allocate_once(account):
    db, bill, client = account
    inv = invoice(client, bill, '100')
    adjustment(client, bill, 'supplier_credit', '40', date='2026-10-03')
    adjustment(client, bill, 'supplier_debit', '10', date='2026-10-04')
    assert bill.outstanding_cents == 7000 and inv.credit_applied_cents == 4000
    post(client, f'/bills/{bill.id}/extra-payment', {'amount_paid': '75', 'paid_date': '2026-10-05'})
    assert bill.outstanding_cents == 0 and bill.credit_cents == 500
    assert sum(a.amount_cents for a in bill.payments[0].allocations) == 6000
    assert sum(a.amount_cents for a in bill.payments[0].adjustment_allocations) == 1000
    assert client.get('/expenses').context['all_time_bills'] == 75


@pytest.mark.parametrize('kind,amount,date', [('bogus', '10', '2026-10-01'), ('supplier_credit', '0', '2026-10-01'),
                                           ('supplier_debit', '-1', '2026-10-01'), ('refund', '10', '2026-10-01'),
                                           ('supplier_credit', 'nan', '2026-10-01'), ('supplier_credit', '1.001', '2026-10-01'),
                                           ('supplier_credit', '10', 'bad-date')])
def test_invalid_adjustment_is_atomic(account, kind, amount, date):
    db, bill, client = account
    response = client.post(f'/bills/{bill.id}/adjustments', data={'kind': kind, 'amount': amount, 'effective_date': date})
    assert response.status_code == 400
    db.refresh(bill)
    assert not bill.ledger_enabled and not bill.adjustments and not bill.invoices


def test_opening_balance_date_and_uniqueness(account):
    db, bill, client = account
    invoice(client, bill)
    response = client.post(f'/bills/{bill.id}/adjustments', data={'kind': 'opening_credit', 'amount': '100', 'effective_date': '2026-10-03'})
    assert response.status_code == 400
    adjustment(client, bill, 'opening_credit', '100')
    response = client.post(f'/bills/{bill.id}/adjustments', data={'kind': 'opening_debt', 'amount': '20', 'effective_date': '2026-10-01'})
    assert response.status_code == 400 and len(bill.adjustments) == 1


def test_refund_reduces_credit_and_cash_spending_on_effective_date(account, monkeypatch):
    # "This month" and "year to date" must include the October events whenever the test runs.
    class ReviewDate(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 8)
    monkeypatch.setattr('main.date_type', ReviewDate)
    db, bill, client = account
    invoice(client, bill)
    post(client, f'/bills/{bill.id}/extra-payment', {'amount_paid': '100', 'paid_date': '2026-10-03'})
    adjustment(client, bill, 'refund', '25', date='2026-10-04')
    assert bill.credit_cents == 1500 and bill.outstanding_cents == 0
    assert sum(p.amount_cents for p in bill.payments) == 10000
    context = client.get('/expenses').context
    assert context['all_time_bills'] == context['bills_this_month'] == context['bills_ytd'] == 75
    assert balance_as_of(bill, day('2026-10-03')) == (-4000, True)
    assert balance_as_of(bill, day('2026-10-04')) == (-1500, True)
    html = client.get(f'/bills/{bill.id}/history').text
    assert 'Refunded:' in html and 'Net paid:' in html and '$75.00' in html


def test_refund_cannot_use_future_credit(account, monkeypatch):
    class ReviewDate(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 8)
    monkeypatch.setattr('main.date_type', ReviewDate)
    db, bill, client = account
    post(client, f'/bills/{bill.id}/extra-payment', {'amount_paid': '100', 'paid_date': '2026-10-05'})
    response = client.post(f'/bills/{bill.id}/adjustments', data={'kind': 'refund', 'amount': '10', 'effective_date': '2026-10-01'})
    assert response.status_code == 400 and not bill.adjustments
    adjustment(client, bill, 'supplier_credit', '20', date='2026-10-01')
    adjustment(client, bill, 'refund', '20', date='2026-10-01')
    assert bill.credit_cents == 10000
    # Later receipts cannot fund a refund with an earlier effective date.
    assert client.get('/expenses').context['all_time_bills'] == 80


def test_supplier_credit_refund_without_cash_is_negative_net_spending(account):
    db, bill, client = account
    adjustment(client, bill, 'supplier_credit', '20')
    adjustment(client, bill, 'refund', '20')
    assert bill.credit_cents == bill.outstanding_cents == 0 and not bill.payments
    assert client.get('/expenses').context['all_time_bills'] == -20


def test_adjustment_edit_delete_and_repeat_submission(account):
    db, bill, client = account
    invoice(client, bill, '100')
    for _ in range(2):
        adjustment(client, bill, 'supplier_credit', '80', request_token='adjustment-once')
    assert len(bill.adjustments) == 1 and bill.outstanding_cents == 2000
    assert db.query(JournalEntry).filter_by(action='Recorded Bill Adjustment').count() == 1
    row = bill.adjustments[0]
    adjustment(client, bill, 'supplier_credit', '120', adjustment_id=row.id)
    assert bill.credit_cents == 2000 and bill.outstanding_cents == 0
    response = client.post(f'/bills/{bill.id}/adjustments', data={'kind': 'supplier_credit', 'amount': '81',
                           'effective_date': '2026-10-01', 'request_token': 'adjustment-once'})
    assert response.status_code == 400
    post(client, f'/bills/{bill.id}/adjustments/{row.id}/delete', {})
    assert bill.credit_cents == 0 and bill.outstanding_cents == 10000
    assert db.execute(text('PRAGMA foreign_key_check')).all() == []


def test_refund_edit_delete_rebuilds_balances(account):
    db, bill, client = account
    adjustment(client, bill, 'opening_credit', '100')
    adjustment(client, bill, 'refund', '30', date='2026-10-02')
    refund = next(a for a in bill.adjustments if a.kind == 'refund')
    adjustment(client, bill, 'refund', '50', date='2026-10-02', adjustment_id=refund.id)
    assert bill.credit_cents == 5000
    post(client, f'/bills/{bill.id}/adjustments/{refund.id}/delete', {})
    assert bill.credit_cents == 10000 and not bill.payments
    assert client.get('/expenses').context['all_time_bills'] == 0


def test_invoice_numbers_normalized_unique_per_account_and_hard_blocked(account):
    db, bill, client = account
    inv = invoice(client, bill, invoice_number=' Inv-123 ')
    assert inv.invoice_number == 'Inv-123' and inv.invoice_number_key == 'inv-123'
    response = client.post(f'/bills/{bill.id}/invoices', data={'amount': '200', 'invoice_number': 'INV-123', 'allow_duplicate': 'true'})
    assert response.status_code == 409
    assert 'Invoice number already recorded' in response.text and 'Record as a separate invoice' not in response.text
    assert len(bill.invoices) == 1 and bill.outstanding_cents == 6000
    second = invoice(client, bill, invoice_number='Inv-124')
    assert len(bill.invoices) == 2
    response = client.post(f'/bills/{bill.id}/invoices', data={'invoice_id': second.id, 'amount': '60', 'invoice_number': 'inv-123'})
    assert response.status_code == 409 and second.invoice_number == 'Inv-124'
    other = Bill(name='Other supplier account')
    db.add(other)
    db.commit()
    invoice(client, other, invoice_number='Inv-123')
    assert len(other.invoices) == 1
    post(client, f'/bills/{bill.id}/invoices', {'invoice_id': inv.id, 'amount': '60', 'invoice_number': ''})
    assert inv.invoice_number is None and inv.invoice_number_key is None


def test_confirm_invoice_different_number_creates_separate_debt(account):
    db, bill, client = account
    for number in ('A', 'B'):
        post(client, f'/bills/{bill.id}/confirm-invoice', {'actual_amount': '60', 'actual_due_date': '2026-10-26',
                                                       'invoice_number': number})
    assert len(bill.invoices) == 2 and bill.outstanding_cents == 12000


def test_statement_dates_exclude_future_events_and_are_read_only(account):
    db, bill, client = account
    invoice(client, bill, '60', statement_balance='60')
    post(client, f'/bills/{bill.id}/extra-payment', {'amount_paid': '100', 'paid_date': '2026-10-03'})
    invoice(client, bill, '90', date='2026-11-02')
    for date, amount, direction in [('2026-10-02', '60', 'owing'), ('2026-10-03', '40', 'credit'), ('2026-11-02', '50', 'owing')]:
        post(client, f'/bills/{bill.id}/statements', {'amount': amount, 'balance_type': direction, 'as_of_date': date})
    assert bill.outstanding_cents == 5000 and bill.credit_cents == 0 and len(bill.payments) == 1
    assert all(row['status'] == 'Match' for row in statement_comparisons(bill))
    post(client, f'/bills/{bill.id}/statements', {'amount': '40.01', 'as_of_date': '2026-10-03', 'balance_type': 'credit'})
    mismatch = next(row for row in statement_comparisons(bill) if row['status'] == 'Difference')
    assert mismatch['difference_cents'] == -1
    assert 'Difference' in client.get(f'/bills/{bill.id}/history').text


def test_zero_statement_repeat_edit_delete_and_history_changes(account):
    db, bill, client = account
    data = {'amount': '0', 'as_of_date': '2026-10-08', 'request_token': 'statement-once'}
    for _ in range(2):
        post(client, f'/bills/{bill.id}/statements', data)
    assert len(bill.statements) == 1 and statement_comparisons(bill)[0]['status'] == 'Match'
    assert db.query(JournalEntry).filter_by(action='Recorded Supplier Balance').count() == 1
    row = bill.statements[0]
    invoice(client, bill, '60')
    assert statement_comparisons(bill)[0]['difference_cents'] == -6000
    post(client, f'/bills/{bill.id}/statements', {'statement_id': row.id, 'amount': '60', 'as_of_date': '2026-10-08'})
    assert statement_comparisons(bill)[0]['status'] == 'Match'
    response = client.post(f'/bills/{bill.id}/statements', data={**data, 'amount': '10'})
    assert response.status_code == 400
    post(client, f'/bills/{bill.id}/statements/{row.id}/delete', {})
    assert not db.query(BillStatement).all()


def test_unknown_issue_dates_make_comparison_provisional(account):
    db, bill, client = account
    post(client, f'/bills/{bill.id}/invoices', {'amount': '60', 'due_date': '2026-10-26', 'statement_balance': '60'})
    post(client, f'/bills/{bill.id}/statements', {'amount': '60', 'as_of_date': '2026-10-26'})
    rows = statement_comparisons(bill)
    assert {row['status'] for row in rows} == {'Issue date unknown', 'Missing invoice issue dates'}
    assert 'Provisional comparison' in client.get(f'/bills/{bill.id}/history').text


def test_adjustments_and_statements_cannot_edit_another_account(account):
    db, bill, client = account
    adjustment(client, bill, 'supplier_credit', '100')
    post(client, f'/bills/{bill.id}/statements', {'amount': '100', 'balance_type': 'credit', 'as_of_date': '2026-10-01'})
    other = Bill(name='Other')
    db.add(other)
    db.commit()
    a, s = bill.adjustments[0], bill.statements[0]
    for path, data in [(f'adjustments/{a.id}/delete', {}), ('adjustments', {'adjustment_id': a.id, 'kind': 'supplier_debit', 'amount': '100', 'effective_date': '2026-10-01'}),
                       (f'statements/{s.id}/delete', {}), ('statements', {'statement_id': s.id, 'amount': '0', 'as_of_date': '2026-10-01'})]:
        assert client.post(f'/bills/{other.id}/{path}', data=data).status_code == 404
    assert bill.credit_cents == 10000 and len(bill.statements) == 1


def test_export_import_roundtrip_adjustments_refunds_statements_and_tokens(account):
    db, bill, client = account
    adjustment(client, bill, 'opening_credit', '100', request_token='opening-once')
    invoice(client, bill, '60', invoice_number='INV-1', request_token='invoice-once')
    adjustment(client, bill, 'refund', '25', date='2026-10-03', request_token='refund-once')
    post(client, f'/bills/{bill.id}/statements', {'amount': '15', 'balance_type': 'credit', 'as_of_date': '2026-10-03', 'request_token': 'balance-once'})
    payload = client.get('/api/export').json()
    assert payload['data']['bill_adjustment_allocations']
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as restored:
        for _ in range(2):
            assert import_data_from_json(restored, copy.deepcopy(payload))['errors'] == 0
            restored.commit()
            restored.expire_all()
            target = restored.get(Bill, bill.id)
            assert target.credit_cents == 1500 and target.outstanding_cents == 0
            assert len(target.adjustments) == 2 and len(target.statements) == len(target.invoices) == 1
            assert statement_comparisons(target)[0]['status'] == 'Match'
            assert target.invoices[0].invoice_number_key == 'inv-1'
            record_invoice(restored, target, '60', invoice_date=day('2026-10-02'), invoice_number='INV-1', request_token='invoice-once')
            record_adjustment(restored, target, 'refund', '25', day('2026-10-03'), request_token='refund-once')
            record_statement(restored, target, '15', 'credit', day('2026-10-03'), request_token='balance-once')
            assert restored.execute(text('PRAGMA foreign_key_check')).all() == []
        restored.delete(target)
        restored.commit()
        assert not restored.query(BillAdjustment).all() and not restored.query(BillStatement).all()
        assert restored.execute(text('PRAGMA foreign_key_check')).all() == []
    engine.dispose()


def test_invoice_number_migration_keeps_rows_and_unique_index():
    engine = create_engine('sqlite://')
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE bill_invoices (id INTEGER PRIMARY KEY, bill_id INTEGER, amount_cents INTEGER)'))
        connection.execute(text('INSERT INTO bill_invoices VALUES(1, 1, 6000)'))
    Base.metadata.create_all(engine)
    run_migrations(engine)
    run_migrations(engine)
    assert {'bill_adjustments', 'bill_adjustment_allocations', 'bill_statements'} <= set(inspect(engine).get_table_names())
    with engine.begin() as connection:
        assert connection.execute(text('SELECT amount_cents FROM bill_invoices WHERE id=1')).scalar() == 6000
        connection.execute(text("UPDATE bill_invoices SET invoice_number='Inv-1', invoice_number_key='inv-1' WHERE id=1"))
        with pytest.raises(IntegrityError):
            connection.execute(text("INSERT INTO bill_invoices(id, bill_id, amount_cents, invoice_number_key) VALUES(2, 1, 6000, 'inv-1')"))
        connection.execute(text("INSERT INTO bill_invoices(id, bill_id, amount_cents, invoice_number_key) VALUES(3, 2, 6000, 'inv-1')"))
    engine.dispose()


def test_example_statements_keep_credit_and_later_net_owing(account):
    db, bill, client = account
    first = invoice(client, bill, '150.00', date='2018-02-20', statement_balance='150.00')
    for _ in range(2):
        post(client, f'/bills/{bill.id}/extra-payment', {'amount_paid': '150.00', 'paid_date': '2018-03-10'})
    invoice(client, bill, '120.00', date='2018-04-20', statement_balance='30.00', balance_type='credit')
    invoice(client, bill, '110.00', date='2018-06-20', statement_balance='80.00')
    assert bill.credit_cents == 0 and bill.outstanding_cents == 8000
    assert [r['status'] for r in statement_comparisons(bill)] == ['Match', 'Match', 'Match']
    assert balance_as_of(bill, day('2018-04-20')) == (-3000, True)
    assert len(bill.payments) == 2 and sum(p.amount_cents for p in bill.payments) == 30000


def test_statement_invalid_values_do_not_enable_tracking(account):
    db, bill, client = account
    for fields in [{'amount': '-1', 'as_of_date': '2026-10-01'},
                   {'amount': '1', 'as_of_date': 'bad-date'},
                   {'amount': '1', 'as_of_date': '2026-10-01', 'balance_type': 'bogus'}]:
        assert client.post(f'/bills/{bill.id}/statements', data=fields).status_code == 400
        db.refresh(bill)
        assert not bill.ledger_enabled and not bill.statements

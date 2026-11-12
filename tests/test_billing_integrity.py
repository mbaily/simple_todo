import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from test_billing_accounts import account, invoice, adjustment, post, day
from main import app, get_db
from models import Base, Bill, BillPayment, JournalEntry
from billing_service import record_invoice
from billing_accounts import balance_as_of
from database import run_migrations
from import_logic import import_data_from_json


@pytest.mark.parametrize('endpoint', ['pay', 'extra-payment'])
def test_payment_retries_record_cash_and_journal_once(account, endpoint):
    db, bill, client = account
    inv = invoice(client, bill, '100')
    data = {'amount_paid': '100', 'paid_date': '2026-10-03', 'request_token': 'payment-once'}
    if endpoint == 'pay':
        data['invoice_id'] = inv.id
    for _ in range(2):
        post(client, f'/bills/{bill.id}/{endpoint}', data)
    assert len(bill.payments) == 1 and bill.credit_cents == bill.outstanding_cents == 0
    assert bill.payments[0].amount_cents == 10000
    action = 'Paid Bill' if endpoint == 'pay' else 'Extra Bill Payment'
    assert db.query(JournalEntry).filter_by(action=action).count() == 1


def test_retry_does_not_select_new_invoice_or_reset_forecast(account):
    db, bill, client = account
    invoice(client, bill, '100', due_date='2026-10-26')
    data = {'amount_paid': '100', 'paid_date': '2026-10-03', 'request_token': 'automatic-selection'}
    post(client, f'/bills/{bill.id}/pay', data)
    newer = invoice(client, bill, '200', date='2027-01-02', due_date='2027-01-26')
    post(client, f'/bills/{bill.id}/pay', data)
    assert len(bill.payments) == 1 and newer.outstanding_cents == 20000
    assert bill.next_due_date == day('2027-01-26')


def test_retry_with_default_amount_and_date_survives_changed_state(account, monkeypatch):
    db, bill, client = account
    bill.expected_amount = 100
    db.commit()
    data = {'request_token': 'defaults-once'}
    post(client, f'/bills/{bill.id}/pay', data)
    old_paid_date = bill.payments[0].paid_date
    bill.expected_amount = 999
    bill.next_due_date = day('2027-04-26')
    db.commit()
    class NextDay(datetime):
        @classmethod
        def now(cls, tz=None):
            return old_paid_date + timedelta(days=1)
    monkeypatch.setattr('main.datetime', NextDay)
    post(client, f'/bills/{bill.id}/pay', data)
    assert len(bill.payments) == len(bill.invoices) == 1
    assert bill.payments[0].amount_cents == 10000 and bill.payments[0].paid_date == old_paid_date
    assert bill.expected_amount == 999 and bill.next_due_date == day('2027-04-26')


def test_genuine_identical_payments_use_distinct_forms(account):
    db, bill, client = account
    invoice(client, bill, '150.00')
    for token in ('actual-first-receipt', 'actual-second-receipt'):
        post(client, f'/bills/{bill.id}/extra-payment', {'amount_paid': '150.00', 'paid_date': '2026-10-03', 'request_token': token})
    assert len(bill.payments) == 2 and sum(p.amount_cents for p in bill.payments) == 30000
    assert bill.credit_cents == 15000


@pytest.mark.parametrize('changed', [{'amount_paid': '101'}, {'paid_date': '2026-10-04'}, {'note': 'changed'},
                                   {'actual_due_date': '2026-10-27'}, {'next_due_override': '2027-01-27'}])
def test_changed_payment_submission_token_rejected(account, changed):
    db, bill, client = account
    invoice(client, bill, '100')
    data = {'amount_paid': '100', 'paid_date': '2026-10-03', 'request_token': 'unchanged-fields'}
    post(client, f'/bills/{bill.id}/pay', data)
    response = client.post(f'/bills/{bill.id}/pay', data={**data, **changed})
    assert response.status_code == 400 and len(bill.payments) == 1
    assert bill.credit_cents == 0


def test_payment_token_cannot_cross_accounts_or_operations(account):
    db, bill, client = account
    invoice(client, bill, '100')
    data = {'amount_paid': '100', 'paid_date': '2026-10-03', 'request_token': 'one-operation'}
    post(client, f'/bills/{bill.id}/pay', data)
    other = Bill(name='Other account')
    db.add(other); db.commit()
    for path in (f'/bills/{other.id}/extra-payment', f'/bills/{bill.id}/extra-payment'):
        assert client.post(path, data=data).status_code == 400
    assert not other.payments and len(bill.payments) == 1


def test_payment_edit_is_not_reverted_by_original_submission_retry(account):
    db, bill, client = account
    inv = invoice(client, bill, '100')
    data = {'invoice_id': inv.id, 'amount_paid': '100', 'paid_date': '2026-10-03', 'request_token': 'edited-receipt'}
    post(client, f'/bills/{bill.id}/pay', data)
    receipt = bill.payments[0]
    post(client, f'/bills/payments/{receipt.id}/edit', {'amount_paid': '80', 'paid_date': '2026-10-03'})
    post(client, f'/bills/{bill.id}/pay', data)
    assert len(bill.payments) == 1 and receipt.amount_cents == 8000
    assert bill.outstanding_cents == 2000


def test_payment_protection_survives_repeated_import(account):
    db, bill, client = account
    invoice(client, bill, '100')
    data = {'amount_paid': '100', 'paid_date': '2026-10-03', 'request_token': 'exported-payment'}
    post(client, f'/bills/{bill.id}/pay', data)
    payload = client.get('/api/export').json()
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as restored:
        for _ in range(2):
            assert import_data_from_json(restored, copy.deepcopy(payload))['errors'] == 0
        app.dependency_overrides[get_db] = lambda: restored
        try:
            post(client, f'/bills/{bill.id}/pay', data)
            assert restored.query(BillPayment).count() == 1
            assert restored.get(Bill, bill.id).credit_cents == 0
        finally:
            app.dependency_overrides[get_db] = lambda: db
    engine.dispose()


@pytest.mark.parametrize('endpoint', ['pay', 'extra-payment'])
def test_concurrent_cash_submissions_save_once(monkeypatch, tmp_path, endpoint):
    monkeypatch.setenv('SIMPLE_TODO_BASIC_AUTH', '0')
    engine = create_engine(f'sqlite:///{tmp_path / "concurrent-payments.db"}', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        bill = Bill(id=1, name='Concurrent account')
        db.add(bill)
        record_invoice(db, bill, '100', invoice_date=day('2026-10-02'))
        db.commit()
    def connection():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = connection
    try:
        def submit(_):
            client = TestClient(app)
            try:
                return client.post(f'/bills/1/{endpoint}', data={'amount_paid': '100', 'request_token': 'concurrent-cash'}, follow_redirects=False).status_code
            finally:
                client.close()
        with ThreadPoolExecutor(max_workers=2) as workers:
            assert list(workers.map(submit, range(2))) == [303, 303]
        with Session(engine) as db:
            assert db.query(BillPayment).count() == 1
            assert db.get(Bill, 1).credit_cents == db.get(Bill, 1).outstanding_cents == 0
    finally:
        app.dependency_overrides.clear(); engine.dispose()


def test_payment_token_migration_preserves_legacy_cash():
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE bill_payments(id INTEGER PRIMARY KEY, bill_id INTEGER, amount_paid FLOAT)'))
        conn.execute(text('INSERT INTO bill_payments VALUES(5, 2, 150.00), (8, 2, 150.00)'))
    run_migrations(engine); run_migrations(engine)
    assert {'request_token', 'request_fingerprint'} <= {c['name'] for c in inspect(engine).get_columns('bill_payments')}
    with engine.begin() as conn:
        assert conn.execute(text('SELECT id, amount_paid, request_token FROM bill_payments ORDER BY id')).all() == [(5,150.00,None),(8,150.00,None)]
        conn.execute(text("UPDATE bill_payments SET request_token='receipt' WHERE id=5"))
        with pytest.raises(IntegrityError):
            conn.execute(text("UPDATE bill_payments SET request_token='receipt' WHERE id=8"))
    engine.dispose()


def test_backdated_net_invoice_excludes_later_payment(account):
    db, bill, client = account
    post(client, f'/bills/{bill.id}/extra-payment', {'amount_paid': '50', 'paid_date': '2026-09-20'})
    inv = invoice(client, bill, '100', date='2026-09-10', amount_type='net')
    assert inv.amount_cents == 10000 and bill.outstanding_cents == 5000
    assert balance_as_of(bill, day('2026-09-10')) == (10000, True)


def test_net_invoice_uses_historical_credit_after_earlier_charges(account):
    db, bill, client = account
    post(client, f'/bills/{bill.id}/extra-payment', {'amount_paid': '100', 'paid_date': '2026-09-01'})
    invoice(client, bill, '40', date='2026-09-03')
    invoice(client, bill, '200', date='2026-10-01')
    inv = invoice(client, bill, '20', date='2026-09-10', amount_type='net')
    assert inv.amount_cents == 8000
    assert balance_as_of(bill, day('2026-09-10')) == (2000, True)


def test_net_invoice_includes_only_adjustments_effective_at_issue(account):
    db, bill, client = account
    adjustment(client, bill, 'opening_credit', '100', date='2026-09-01')
    adjustment(client, bill, 'supplier_debit', '30', date='2026-09-03')
    adjustment(client, bill, 'supplier_credit', '50', date='2026-09-20')
    inv = invoice(client, bill, '20', date='2026-09-10', amount_type='net')
    assert inv.amount_cents == 9000 and bill.credit_cents == 3000
    assert balance_as_of(bill, day('2026-09-10')) == (2000, True)


def test_net_edit_excludes_later_credit_and_own_cash_on_issue_date(account):
    db, bill, client = account
    post(client, f'/bills/{bill.id}/extra-payment', {'amount_paid': '50', 'paid_date': '2026-09-01'})
    inv = invoice(client, bill, '150', date='2026-09-10')
    post(client, f'/bills/{bill.id}/pay', {'invoice_id': inv.id, 'amount_paid': '100', 'paid_date': '2026-09-10'})
    post(client, f'/bills/{bill.id}/extra-payment', {'amount_paid': '25', 'paid_date': '2026-09-20'})
    fields = {'invoice_id': inv.id, 'amount': '100', 'amount_type': 'net'}
    response = client.post(f'/bills/{bill.id}/invoices', data=fields)
    assert response.status_code == 400 and inv.amount_cents == 15000
    post(client, f'/bills/{bill.id}/invoices', {**fields, 'credit_applied': '50'})
    assert inv.amount_cents == 15000 and bill.credit_cents == 2500
    assert sum(p.amount_cents for p in bill.payments) == 17500


def test_net_edit_keeps_order_of_same_day_invoices(account):
    db, bill, client = account
    post(client, f'/bills/{bill.id}/extra-payment', {'amount_paid': '100', 'paid_date': '2026-09-01'})
    first = invoice(client, bill, '120', date='2026-09-10', invoice_number='A')
    invoice(client, bill, '50', date='2026-09-10', invoice_number='B')
    fields = {'invoice_id': first.id, 'amount': '20', 'amount_type': 'net'}
    response = client.post(f'/bills/{bill.id}/invoices', data=fields)
    assert response.status_code == 400 and first.amount_cents == 12000
    post(client, f'/bills/{bill.id}/invoices', {**fields, 'credit_applied': '100'})
    assert first.amount_cents == 12000 and bill.outstanding_cents == 7000


def test_net_invoice_requires_issue_date_without_changing_ledger(account):
    db, bill, client = account
    response = client.post(f'/bills/{bill.id}/invoices', data={'amount': '100', 'amount_type': 'net'})
    assert response.status_code == 400 and 'invoice issue date' in response.text
    db.refresh(bill)
    assert not bill.ledger_enabled and not bill.invoices


def test_net_invoice_with_unknown_other_issue_dates_rejected(account):
    db, bill, client = account
    post(client, f'/bills/{bill.id}/invoices', {'amount': '40', 'due_date': '2026-10-26'})
    response = client.post(f'/bills/{bill.id}/invoices', data={'amount': '100', 'amount_type': 'net', 'invoice_date': '2026-09-10'})
    assert response.status_code == 400 and 'missing invoice issue dates' in response.text
    assert len(bill.invoices) == 1 and bill.outstanding_cents == 4000


def test_confirm_form_records_issue_date_and_dated_net_amount(account):
    db, bill, client = account
    post(client, f'/bills/{bill.id}/extra-payment', {'amount_paid': '50', 'paid_date': '2026-09-20'})
    data = {'actual_amount': '100', 'amount_type': 'net', 'invoice_date': '2026-09-10',
            'actual_due_date': '2026-09-26', 'request_token': 'dated-confirm'}
    for _ in range(2):
        post(client, f'/bills/{bill.id}/confirm-invoice', data)
    assert len(bill.invoices) == 1 and bill.invoices[0].amount_cents == 10000
    assert bill.invoices[0].invoice_date == day('2026-09-10')


def test_refund_rejects_unknown_issue_date_then_validates_real_dated_credit(account):
    db, bill, client = account
    post(client, f'/bills/{bill.id}/invoices', {'amount': '100', 'due_date': '2026-10-26'})
    inv = bill.invoices[0]
    post(client, f'/bills/{bill.id}/extra-payment', {'amount_paid': '100', 'paid_date': '2026-10-03'})
    data = {'kind': 'refund', 'amount': '100', 'effective_date': '2026-10-04', 'request_token': 'dated-refund'}
    response = client.post(f'/bills/{bill.id}/adjustments', data=data)
    assert response.status_code == 400 and 'missing invoice issue dates' in response.text
    assert not bill.adjustments and bill.outstanding_cents == bill.credit_cents == 0
    post(client, f'/bills/{bill.id}/invoices', {'invoice_id': inv.id, 'amount': '100', 'invoice_date': '2026-10-01'})
    response = client.post(f'/bills/{bill.id}/adjustments', data=data)
    assert response.status_code == 400 and 'exceeds the credit' in response.text
    post(client, f'/bills/{bill.id}/extra-payment', {'amount_paid': '40', 'paid_date': '2026-10-03'})
    post(client, f'/bills/{bill.id}/adjustments', {**data, 'amount': '30'})
    assert bill.credit_cents == 1000 and bill.outstanding_cents == 0
    assert client.get('/expenses').context['all_time_bills'] == 110

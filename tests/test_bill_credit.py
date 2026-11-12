"""Billing regressions using fictional amounts and dates."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from main import app, get_db
from models import Base, Bill, BillPayment, BillInvoice, JournalEntry
from billing_service import record_invoice, record_cash_payment, enable_ledger, reconcile_allocations
from import_logic import import_data_from_json
from serializers import serialize_bill, serialize_bill_payment, serialize_bill_invoice, serialize_bill_allocation
from database import run_migrations


@pytest.fixture
def account(monkeypatch):
    monkeypatch.setenv('SIMPLE_TODO_BASIC_AUTH', '0')
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        bill = Bill(name='Fictional utility', frequency='quarterly', expected_amount=150.00,
                    next_due_date=datetime(2018, 6, 29))
        db.add(bill)
        db.commit()
        app.dependency_overrides[get_db] = lambda: db
        try:
            yield db, bill, TestClient(app)
        finally:
            app.dependency_overrides.clear()
    engine.dispose()


def post(client, path, data):
    response = client.post(path, data=data, follow_redirects=False)
    assert response.status_code == 303, response.text


def credit_sequence(account):
    db, bill, client = account
    post(client, f'/bills/{bill.id}/invoices', {'amount': '150.00', 'due_date': '2018-06-29', 'invoice_date': '2018-06-10'})
    post(client, f'/bills/{bill.id}/pay', {'amount_paid': '150.00', 'paid_date': '2018-07-15'})
    due = bill.next_due_date
    post(client, f'/bills/{bill.id}/extra-payment', {'amount_paid': '150.00', 'paid_date': '2018-07-15'})
    assert bill.next_due_date == due == datetime(2018, 9, 29)
    assert bill.credit_cents == 15000
    post(client, f'/bills/{bill.id}/invoices', {'amount': '120.00', 'invoice_date': '2018-08-10'})
    assert bill.credit_cents == 3000 and bill.outstanding_cents == 0
    post(client, f'/bills/{bill.id}/invoices', {'amount': '80.00', 'amount_type': 'net', 'invoice_date': '2018-10-07'})
    return db, bill, client


def test_credit_sequence_and_ui(account):
    db, bill, client = credit_sequence(account)
    assert bill.credit_cents == 0 and bill.outstanding_cents == 8000
    assert bill.invoices[-1].amount_cents == 11000
    assert [i.credit_applied_cents for i in bill.invoices] == [0, 12000, 3000]
    assert sum(p.amount_cents for p in bill.payments) == 30000
    assert len(bill.payments) == 2  # Credit applications create no fictional bank payments.
    history = client.get(f'/bills/{bill.id}/history')
    assert history.status_code == 200
    for text in ('$110.00', '$30.00', '$80.00', 'Unknown', 'Record extra payment'):
        assert text in history.text
    assert 'Owing $80.00' in client.get('/classic/bills').text


def test_partial_payment_and_duplicate_do_not_advance_twice(account):
    db, bill, client = account
    post(client, f'/bills/{bill.id}/invoices', {'amount': '100', 'due_date': '2018-06-29'})
    invoice = bill.invoices[0]
    for amount, owing in [('40', 6000), ('60', 0)]:
        post(client, f'/bills/{bill.id}/pay', {'amount_paid': amount, 'paid_date': '2018-07-15', 'invoice_id': invoice.id})
        assert bill.outstanding_cents == owing
        assert bill.next_due_date == datetime(2018, 6, 29) if owing else bill.next_due_date == datetime(2018, 9, 29)
    post(client, f'/bills/{bill.id}/pay', {'amount_paid': '100', 'paid_date': '2018-07-15', 'invoice_id': invoice.id})
    assert bill.credit_cents == 10000
    assert bill.next_due_date == datetime(2018, 9, 29)


def test_edit_and_delete_recalculate_credit(account):
    db, bill, client = credit_sequence(account)
    duplicate = max(bill.payments, key=lambda p: p.id)
    post(client, f'/bills/payments/{duplicate.id}/edit', {
        'amount_paid': '160.00', 'paid_date': '2018-07-15', 'due_date': '2018-06-29'})
    assert bill.outstanding_cents == 7000
    post(client, f'/bills/payments/{duplicate.id}/delete', {})
    assert bill.outstanding_cents == 23000
    assert bill.credit_cents == 0


def test_net_invoice_edit_does_not_apply_credit_twice(account):
    db, bill, client = credit_sequence(account)
    invoice = bill.invoices[-1]
    post(client, f'/bills/{bill.id}/invoices', {'invoice_id': invoice.id, 'amount': '80.00', 'amount_type': 'net'})
    assert invoice.amount_cents == 11000
    assert invoice.credit_applied_cents == 3000
    assert bill.outstanding_cents == 8000


def test_confirmed_invoice_overrides_forecast(account):
    db, bill, client = credit_sequence(account)
    bill.amount_strategy = 'last_payment'
    db.commit()
    assert bill.effective_amount == 80.00
    assert not bill.is_estimated
    post(client, f'/bills/{bill.id}/pay', {'invoice_id': bill.invoices[-1].id, 'amount_paid': '80.00'})
    assert bill.estimated_amount == 110.00  # Forecast uses the charge before credit.


@pytest.mark.parametrize('amount', ['-1', 'nan', 'inf', '0.001', '', 'bogus'])
def test_invalid_money_does_not_change_account(account, amount):
    db, bill, client = account
    response = client.post(f'/bills/{bill.id}/extra-payment', data={'amount_paid': amount}, follow_redirects=False)
    assert response.status_code == 400
    db.refresh(bill)
    assert not bill.ledger_enabled
    assert len(bill.payments) == 0


def test_export_import_preserves_balances_and_is_idempotent(account):
    db, bill, client = credit_sequence(account)
    payload = {'data': {
        'bills': [serialize_bill(bill)],
        'bill_payments': [serialize_bill_payment(p) for p in bill.payments],
        'bill_invoices': [serialize_bill_invoice(i) for i in bill.invoices],
        'bill_allocations': [serialize_bill_allocation(a) for i in bill.invoices for a in i.allocations],
    }}
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as target:
        for _ in range(2):
            stats = import_data_from_json(target, copy.deepcopy(payload))
            target.commit()
            target.expire_all()
            restored = target.get(Bill, bill.id)
            assert stats['errors'] == 0
            assert restored.credit_cents == 0 and restored.outstanding_cents == 8000
            assert sum(p.amount_cents for p in restored.payments) == 30000
    engine.dispose()


def test_no_implicit_next_invoice_after_settlement(account):
    db, bill, client = account
    post(client, f'/bills/{bill.id}/invoices', {'amount': '150.00', 'due_date': '2018-06-29'})
    post(client, f'/bills/{bill.id}/pay', {'amount_paid': '150.00'})
    response = client.post(f'/bills/{bill.id}/pay', data={'amount_paid': '150.00'}, follow_redirects=False)
    assert response.status_code == 400
    db.refresh(bill)
    assert bill.credit_cents == 0 and len(bill.payments) == 1
    assert bill.next_due_date == datetime(2018, 9, 29)


def test_create_confirmed_bill_retains_partial_balance(account):
    db, bill, client = account
    post(client, '/bills/create', {'name': 'Confirmed utility', 'expected_amount': '100',
                                 'next_due_date': '2018-06-29', 'is_due_date_confirmed': 'true'})
    created = db.query(Bill).filter_by(name='Confirmed utility').one()
    post(client, f'/bills/{created.id}/pay', {'amount_paid': '40'})
    assert created.outstanding_cents == 6000
    assert created.next_due_date == datetime(2018, 6, 29)


def test_edit_due_date_updates_unknown_invoice(account):
    db, bill, client = credit_sequence(account)
    post(client, f'/bills/{bill.id}/update', {'name': bill.name, 'next_due_date': '2018-10-25',
                                         'frequency': 'quarterly', 'expected_amount': '110.00'})
    assert bill.open_invoice.due_date == datetime(2018, 10, 25)
    assert bill.effective_amount == 80.00


def test_legacy_duplicate_receipts_preserve_cash_when_reconciled(account):
    db, unused, client = account
    bill = Bill(name='Fictional legacy account', frequency='quarterly')
    db.add(bill)
    db.flush()
    receipts = [BillPayment(bill=bill, due_date=datetime(2018, 2, 20),
                           paid_date=datetime(2018, 2, 10), amount_paid=150) for _ in range(2)]
    db.add_all(receipts)
    db.flush()
    enable_ledger(db, bill)
    duplicate_invoice = db.get(BillInvoice, receipts[1].invoice_id)
    receipts[1].invoice_id = None
    receipts[1].invoice = None
    db.delete(duplicate_invoice)
    db.flush()
    db.expire(bill, ['invoices'])
    reconcile_allocations(db, bill, update_schedule=False)
    record_invoice(db, bill, '120', invoice_date=datetime(2018, 4, 1))
    record_invoice(db, bill, '110', invoice_date=datetime(2018, 6, 1))
    db.commit()
    enable_ledger(db, bill)
    assert len(bill.payments) == 2 and sum(p.amount_cents for p in bill.payments) == 30000
    assert len(bill.invoices) == 3 and bill.outstanding_cents == 8000 and bill.credit_cents == 0


@pytest.mark.parametrize('original_due', ['', '2018-10-02'])
def test_editing_paid_invoice_updates_cash_timing_and_forecast(account, original_due):
    db, bill, client = account
    post(client, f'/bills/{bill.id}/invoices', {'amount': '100', 'due_date': original_due,
                                             'invoice_date': '2018-09-11'})
    invoice = bill.invoices[0]
    post(client, f'/bills/{bill.id}/pay', {'invoice_id': invoice.id, 'amount_paid': '40', 'paid_date': '2018-09-24'})
    post(client, f'/bills/{bill.id}/pay', {'invoice_id': invoice.id, 'amount_paid': '60', 'paid_date': '2018-09-25'})
    before_cash = [(p.id, p.amount_cents, p.paid_date) for p in bill.payments]
    post(client, f'/bills/{bill.id}/invoices', {'invoice_id': invoice.id, 'amount': '100',
                                             'due_date': '2018-10-09', 'invoice_date': '2018-09-11'})
    assert all(p.due_date == datetime(2018, 10, 9) for p in bill.payments)
    assert sorted(p.days_difference for p in bill.payments) == [14, 15]
    assert [(p.id, p.amount_cents, p.paid_date) for p in bill.payments] == before_cash
    assert bill.next_due_date == datetime(2019, 1, 9) and not bill.is_due_date_confirmed
    assert bill.credit_cents == bill.outstanding_cents == 0
    html = client.get(f'/bills/{bill.id}/history').text
    assert '15 days early' in html and '14 days early' in html


def test_editing_old_paid_invoice_keeps_newer_and_explicit_due_dates(account):
    db, bill, client = account
    older = record_invoice(db, bill, '100', datetime(2018, 10, 2), invoice_date=datetime(2018, 9, 11))
    record_cash_payment(db, bill, 10000, datetime(2018, 9, 24), older)
    newer = record_invoice(db, bill, '120', datetime(2019, 1, 2), invoice_date=datetime(2018, 12, 11))
    db.commit()
    post(client, f'/bills/{bill.id}/invoices', {'invoice_id': older.id, 'amount': '100',
                                             'due_date': '2018-10-09', 'invoice_date': '2018-09-11'})
    assert bill.open_invoice == newer and bill.next_due_date == datetime(2019, 1, 2)
    post(client, f'/bills/{bill.id}/pay', {'invoice_id': newer.id, 'amount_paid': '120',
                                       'next_due_override': '2019-04-15'})
    post(client, f'/bills/{bill.id}/invoices', {'invoice_id': newer.id, 'amount': '120',
                                             'due_date': '2019-01-08', 'invoice_date': '2018-12-11'})
    assert bill.next_due_date == datetime(2019, 4, 15) and bill.is_due_date_confirmed


def test_clearing_paid_invoice_due_date_hides_timing(account):
    db, bill, client = account
    invoice = record_invoice(db, bill, '100', datetime(2018, 10, 2))
    payment = record_cash_payment(db, bill, 10000, datetime(2018, 9, 24), invoice)
    db.commit()
    post(client, f'/bills/{bill.id}/invoices', {'invoice_id': invoice.id, 'amount': '100', 'due_date': ''})
    assert payment.due_date_iso == '' and payment.timing_label == '' and not payment.is_late
    assert bill.next_due_date is None


def test_cash_history_date_edit_updates_linked_invoice(account):
    db, bill, client = account
    invoice = record_invoice(db, bill, '100', datetime(2018, 10, 2))
    payment = record_cash_payment(db, bill, 10000, datetime(2018, 9, 24), invoice)
    db.commit()
    post(client, f'/bills/payments/{payment.id}/edit', {'due_date': '2018-10-09',
        'paid_date': '2018-09-24', 'amount_paid': '100'})
    assert invoice.due_date == payment.due_date == datetime(2018, 10, 9)
    assert bill.next_due_date == datetime(2019, 1, 9)


def test_linked_payment_uses_invoice_date_even_for_old_mismatched_storage(account):
    db, bill, client = account
    invoice = record_invoice(db, bill, '100', datetime(2018, 10, 2))
    payment = record_cash_payment(db, bill, 10000, datetime(2018, 9, 24), invoice)
    payment.due_date = payment.paid_date
    db.commit()
    assert payment.due_date_iso == '2018-10-02'
    assert payment.days_difference == 8 and payment.timing_label == '8 days early'


def test_repeated_net_invoice_submission_does_not_consume_credit_twice(account):
    db, bill, client = account
    post(client, f'/bills/{bill.id}/extra-payment', {'amount_paid': '45', 'paid_date': '2018-09-11'})
    fields = {'amount': '10', 'amount_type': 'net', 'due_date': '2018-10-02', 'invoice_date': '2018-09-12',
              'request_token': 'net-invoice-once'}
    for _ in range(2):
        post(client, f'/bills/{bill.id}/invoices', fields)
    assert len(bill.invoices) == len(bill.payments) == 1
    assert bill.invoices[0].amount_cents == 5500
    assert bill.outstanding_cents == 1000 and bill.credit_cents == 0
    assert db.query(JournalEntry).filter_by(action='Recorded Bill Invoice').count() == 1


def test_possible_duplicate_warns_and_explicit_separate_invoice_saves_once(account):
    db, bill, client = account
    fields = {'amount': '100', 'due_date': '2018-10-02', 'invoice_date': '2018-09-11',
              'request_token': 'first-invoice'}
    post(client, f'/bills/{bill.id}/invoices', fields)
    fields['request_token'] = 'separate-invoice'
    response = client.post(f'/bills/{bill.id}/invoices', data=fields, follow_redirects=False)
    assert response.status_code == 409 and 'Possible duplicate invoice' in response.text
    assert 'Record as a separate invoice' in response.text
    assert len(bill.invoices) == 1 and bill.outstanding_cents == 10000
    fields['allow_duplicate'] = 'true'
    for _ in range(2):
        post(client, f'/bills/{bill.id}/invoices', fields)
    assert len(bill.invoices) == 2 and bill.outstanding_cents == 20000


def test_repeated_confirm_after_payment_does_not_add_invoice_or_reset_forecast(account):
    db, bill, client = account
    fields = {'actual_amount': '100', 'actual_due_date': '2018-10-02', 'request_token': 'confirm-once'}
    post(client, f'/bills/{bill.id}/confirm-invoice', fields)
    post(client, f'/bills/{bill.id}/pay', {'amount_paid': '100'})
    bill.expected_amount = 300
    db.commit()
    post(client, f'/bills/{bill.id}/confirm-invoice', fields)
    assert len(bill.invoices) == 1 and bill.outstanding_cents == 0
    assert bill.expected_amount == 300 and bill.next_due_date == datetime(2019, 1, 2)
    fields.pop('request_token')
    response = client.post(f'/bills/{bill.id}/confirm-invoice', data=fields, follow_redirects=False)
    assert response.status_code == 409 and len(bill.invoices) == 1
    newer = record_invoice(db, bill, '25', invoice_date=datetime(2018, 12, 11))
    db.commit()
    fields['request_token'] = 'confirm-once'
    post(client, f'/bills/{bill.id}/confirm-invoice', fields)
    assert bill.open_invoice == newer and bill.outstanding_cents == 2500


def test_reused_invoice_form_with_changed_details_is_rejected(account):
    db, bill, client = account
    fields = {'amount': '100', 'request_token': 'same-form'}
    post(client, f'/bills/{bill.id}/invoices', fields)
    fields['amount'] = '200'
    response = client.post(f'/bills/{bill.id}/invoices', data=fields, follow_redirects=False)
    assert response.status_code == 400 and len(bill.invoices) == 1
    assert bill.outstanding_cents == 10000


def test_distinct_billing_periods_with_same_amount_can_be_recorded(account):
    db, bill, client = account
    for issued in ('2018-09-11', '2018-12-11'):
        post(client, f'/bills/{bill.id}/invoices', {'amount': '100', 'invoice_date': issued})
    assert len(bill.invoices) == 2 and bill.outstanding_cents == 20000


def test_invoice_submission_protection_survives_export_import(account):
    db, bill, client = account
    fields = {'amount': '100', 'due_date': '2018-10-02', 'request_token': 'exported-form'}
    post(client, f'/bills/{bill.id}/invoices', fields)
    payload = client.get('/api/export').json()
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as restored:
        assert import_data_from_json(restored, payload)['errors'] == 0
        restored.commit()
        copy_bill = restored.get(Bill, bill.id)
        record_invoice(restored, copy_bill, '100', datetime(2018, 10, 2), request_token='exported-form')
        assert len(copy_bill.invoices) == 1 and copy_bill.outstanding_cents == 10000
    engine.dispose()


def test_invoice_submission_migration_preserves_rows_and_enforces_unique_keys():
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE bill_invoices (id INTEGER PRIMARY KEY, bill_id INTEGER, amount_cents INTEGER)'))
        conn.execute(text('INSERT INTO bill_invoices VALUES (1, 1, 10000)'))
    run_migrations(engine)
    run_migrations(engine)
    assert {'request_token', 'request_fingerprint'} <= {c['name'] for c in inspect(engine).get_columns('bill_invoices')}
    with engine.begin() as conn:
        assert conn.execute(text('SELECT amount_cents FROM bill_invoices WHERE id=1')).scalar() == 10000
        conn.execute(text("UPDATE bill_invoices SET request_token='one-key' WHERE id=1"))
        with pytest.raises(IntegrityError):
            conn.execute(text("INSERT INTO bill_invoices(id, bill_id, amount_cents, request_token) VALUES (2, 1, 10000, 'one-key')"))
    engine.dispose()


def test_concurrent_invoice_submissions_save_once(monkeypatch, tmp_path):
    monkeypatch.setenv('SIMPLE_TODO_BASIC_AUTH', '0')
    engine = create_engine(f'sqlite:///{tmp_path / "concurrent.db"}', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Bill(id=1, name='Concurrent utility'))
        db.commit()
    def connection():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = connection
    try:
        def submit(_):
            client = TestClient(app)
            try:
                return client.post('/bills/1/invoices', data={'amount': '100', 'request_token': 'concurrent-form'}, follow_redirects=False).status_code
            finally:
                client.close()
        with ThreadPoolExecutor(max_workers=2) as workers:
            assert list(workers.map(submit, range(2))) == [303, 303]
        with Session(engine) as db:
            assert db.query(BillInvoice).count() == 1
            assert db.get(Bill, 1).outstanding_cents == 10000
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

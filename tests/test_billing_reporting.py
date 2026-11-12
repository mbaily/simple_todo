"""Keep recurring budgets, saved confirmations and cash reporting consistent."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from main import app, get_db
from models import Base, Bill, BillDueDateSubmission, BillPayment, BillAdjustment, Expense, JournalEntry
from billing_service import reconcile_allocations
from import_logic import import_data_from_json
from test_billing_accounts import account, adjustment, invoice, post, day


@pytest.fixture
def review_day(monkeypatch):
    class ReviewDate(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 8)

    class ReviewTime(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
            return instant.astimezone(tz) if tz is not None else instant.replace(tzinfo=None)

    # Bills uses datetime.now(), while Expenses uses date_type.today(). Freeze
    # both so the recent-payments check does not depend on the real calendar.
    monkeypatch.setattr('main.datetime', ReviewTime)
    monkeypatch.setattr('main.date_type', ReviewDate)
    monkeypatch.setattr('main.utcnow', lambda: datetime(2026, 10, 8, 12))


@pytest.mark.parametrize('today', [date(2026, 10, 8), date(2026, 1, 2), date(2024, 3, 1)])
def test_past_seven_days_includes_exactly_seven_calendar_dates_for_all_transactions(account, monkeypatch, today):
    db, bill, client = account
    class ReviewDate(date):
        @classmethod
        def today(cls):
            return today
    monkeypatch.setattr('main.date_type', ReviewDate)
    for offset in range(8):
        transaction_date = datetime.combine(today - timedelta(days=offset), datetime.min.time())
        db.add(Expense(amount=10, description='Daily expense', date=transaction_date))
        db.add(BillPayment(bill=bill, amount_paid=20, paid_date=transaction_date, due_date=transaction_date))
        db.add(BillAdjustment(bill=bill, kind='refund', amount_cents=100, effective_date=transaction_date))
    db.commit()
    before = client.get('/api/export').json()['data']
    context = client.get('/expenses').context
    assert context['past_7_days_total'] == 70
    assert context['bills_past_7_days'] == 133
    assert context['combined_past_7_days'] == 203
    assert context['total_spending'] == 80 and context['all_time_bills'] == 152
    assert client.get('/api/export').json()['data'] == before


def test_seven_day_window_handles_exact_start_end_and_future_dates(account, review_day):
    db, bill, client = account
    samples = [('2026-10-01T23:59:59', 60, 100, 3), ('2026-10-02T00:00:00', 15.25, 23, 1),
               ('2026-10-08T23:59:59', 30, 50, 2), ('2026-10-09T00:00:00', 1000, 1000, 10)]
    for timestamp, expense_amount, paid, refund in samples:
        transaction_date = day(timestamp)
        db.add(Expense(amount=expense_amount, date=transaction_date))
        db.add(BillPayment(bill=bill, amount_paid=paid, paid_date=transaction_date, due_date=transaction_date))
        db.add(BillAdjustment(bill=bill, kind='refund', amount_cents=refund * 100, effective_date=transaction_date))
    db.commit()
    context = client.get('/expenses').context
    assert context['past_7_days_total'] == 45.25
    assert context['bills_past_7_days'] == 70
    assert context['combined_past_7_days'] == 115.25
    assert context['this_month_total'] == context['ytd_total'] == context['total_spending'] == 105.25
    assert context['bills_this_month'] == context['bills_ytd'] == context['all_time_bills'] == 167


@pytest.mark.parametrize('strategy', ['fixed', 'last_payment', 'rolling_avg'])
def test_partial_payment_does_not_shrink_recurring_budget(account, strategy):
    db, bill, client = account
    bill.amount_strategy = strategy
    bill.expected_amount = 100
    db.commit()
    inv = invoice(client, bill, '100', due_date='2026-10-26')
    post(client, f'/bills/{bill.id}/pay', dict(invoice_id=inv.id, amount_paid='80', paid_date='2026-10-03'))
    assert bill.effective_amount == 20 and bill.budget_amount == 100
    assert client.get('/classic/bills').context['monthly_total'] == pytest.approx(100 / 3)
    assert client.get('/expenses').context['bills_monthly'] == pytest.approx(100 / 3)
    adjustment(client, bill, 'supplier_credit', '20', date='2026-10-04')
    assert bill.outstanding_cents == 0 and bill.budget_amount == 100
    adjustment(client, bill, 'supplier_debit', '200', date='2026-10-05')
    assert bill.outstanding_cents == 20000 and bill.budget_amount == 100
    assert client.get('/classic/bills').context['monthly_total'] == pytest.approx(100 / 3)


def test_simple_reminder_keeps_confirmed_amount_for_budget(account):
    db, bill, client = account
    bill.expected_amount = 150
    bill.is_due_date_confirmed = True
    db.add(BillPayment(bill=bill, amount_paid=60, due_date=day('2026-09-26'), paid_date=day('2026-09-01')))
    db.commit()
    assert not bill.ledger_enabled and bill.estimated_amount == 60 and bill.budget_amount == 150
    assert client.get('/classic/bills').context['monthly_total'] == 50
    assert client.get('/expenses').context['bills_monthly'] == 50


def test_old_confirmed_invoice_correction_preserves_fixed_future_estimate(account):
    db, bill, client = account
    bill.amount_strategy = 'fixed'
    db.commit()
    path = f'/bills/{bill.id}/confirm-invoice'
    post(client, path, dict(actual_amount='100', invoice_date='2026-04-01', actual_due_date='2026-04-26'))
    old = bill.ordered_invoices[-1]
    post(client, path, dict(actual_amount='200', invoice_date='2026-07-01', actual_due_date='2026-07-26'))
    latest = bill.ordered_invoices[-1]
    post(client, f'/bills/{bill.id}/extra-payment', dict(amount_paid='400', paid_date='2026-07-10'))
    expected_due = bill.next_due_date
    post(client, path, dict(invoice_target=old.id, actual_amount='120', invoice_date='2026-04-01', actual_due_date='2026-04-26'))
    assert old.amount_cents == 12000 and bill.expected_amount == bill.budget_amount == 200
    assert bill.next_due_date == expected_due
    post(client, path, dict(actual_amount='50', invoice_date='2026-01-01', actual_due_date='2026-01-26'))
    assert bill.expected_amount == 200 and bill.next_due_date == expected_due
    post(client, path, dict(invoice_target=latest.id, actual_amount='220', invoice_date='2026-07-01', actual_due_date='2026-07-26'))
    assert bill.expected_amount == bill.budget_amount == 220


def date_confirmation(due='2026-09-26', token='date-once'):
    return dict(actual_due_date=due, actual_amount='', invoice_target='new', request_token=token)


@pytest.mark.parametrize('new_invoice', [False, True])
def test_old_date_confirmation_retry_preserves_newer_state(account, new_invoice):
    db, bill, client = account
    path = f'/bills/{bill.id}/confirm-invoice'
    first = date_confirmation()
    post(client, path, first)
    post(client, path, date_confirmation('2026-10-26', 'later-date'))
    if new_invoice:
        invoice(client, bill, '100', due_date='2026-10-26')
    before = (bill.next_due_date, bill.expected_amount, bill.is_due_date_confirmed,
              db.query(JournalEntry).count())
    for _ in range(2):
        post(client, path, first)
    assert (bill.next_due_date, bill.expected_amount, bill.is_due_date_confirmed,
            db.query(JournalEntry).count()) == before
    assert db.query(BillDueDateSubmission).count() == 2
    changed = client.post(path, data=date_confirmation('2026-11-26'))
    assert changed.status_code == 400


@pytest.mark.parametrize('invoice_first', [False, True])
def test_same_confirmation_token_cannot_switch_between_date_and_charge(account, invoice_first):
    db, bill, client = account
    path = f'/bills/{bill.id}/confirm-invoice'
    dated = date_confirmation()
    charged = {**dated, 'actual_amount': '100', 'invoice_date': '2026-09-01'}
    first, second = (charged, dated) if invoice_first else (dated, charged)
    post(client, path, first)
    response = client.post(path, data=second)
    assert response.status_code == 400
    assert len(bill.invoices) == int(invoice_first)


def test_date_confirmation_tokens_stay_bound_to_original_account(account):
    db, bill, client = account
    fields = date_confirmation()
    post(client, f'/bills/{bill.id}/confirm-invoice', fields)
    replacement = Bill(name='Another account')
    db.add(replacement); db.commit()
    assert client.post(f'/bills/{replacement.id}/confirm-invoice', data=fields).status_code == 400
    original_id = bill.id
    post(client, f'/bills/{original_id}/delete', {})
    assert db.query(BillDueDateSubmission).one().is_deleted
    reused = Bill(id=original_id, name='Reused account id')
    db.add(reused); db.commit()
    assert client.post(f'/bills/{original_id}/confirm-invoice', data=fields).status_code == 400
    assert reused.next_due_date is None


def test_date_confirmation_export_restore_remembers_old_retries(account):
    db, bill, client = account
    path = f'/bills/{bill.id}/confirm-invoice'
    first = date_confirmation()
    post(client, path, first)
    post(client, path, date_confirmation('2026-10-26', 'later-date'))
    payload = client.get('/api/export').json()
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as restored:
            for _ in range(2):
                assert import_data_from_json(restored, copy.deepcopy(payload))['errors'] == 0
            app.dependency_overrides[get_db] = lambda: restored
            post(client, path, first)
            assert restored.get(Bill, bill.id).next_due_date == day('2026-10-26')
            assert restored.query(BillDueDateSubmission).count() == 2
            active = copy.deepcopy(payload['data']['bill_due_date_submissions'])
            post(client, f'/bills/{bill.id}/delete', {})
            assert import_data_from_json(restored, {'data': {'bill_due_date_submissions': active}})['errors'] == 0
            assert all(row.is_deleted for row in restored.query(BillDueDateSubmission))
            assert restored.execute(text('PRAGMA foreign_key_check')).all() == []
    finally:
        app.dependency_overrides[get_db] = lambda: db
        engine.dispose()


def test_simultaneous_date_confirmation_creates_one_journal(tmp_path, monkeypatch):
    monkeypatch.setenv('SIMPLE_TODO_BASIC_AUTH', '0')
    engine = create_engine(f'sqlite:///{tmp_path / "dates.db"}', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Bill(id=1, name='Concurrent dates')); db.commit()
    def connection():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = connection
    def submit(_):
        client = TestClient(app)
        try:
            return client.post('/bills/1/confirm-invoice', data=date_confirmation(), follow_redirects=False).status_code
        finally:
            client.close()
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            assert list(workers.map(submit, range(2))) == [303, 303]
        with Session(engine) as db:
            assert db.query(BillDueDateSubmission).count() == 1
            assert db.query(JournalEntry).filter_by(action='Confirmed Bill Due Date').count() == 1
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


@pytest.mark.parametrize('bad_amounts', [
    {'amount_paid': 100, 'amount_cents': 9000},
    {'amount_paid': 100, 'amount_cents': -1},
    {'amount_paid': 100, 'amount_cents': True},
    {'amount_paid': 100, 'amount_cents': 10000.0},
    {'amount_paid': 'NaN', 'amount_cents': 10000},
    {'amount_paid': '1.001', 'amount_cents': 100},
    {'amount_paid': None, 'amount_cents': 10000},
    {'amount_cents': 9007199254740993},
])
def test_invalid_imported_cash_changes_nothing(account, bad_amounts):
    db, bill, client = account
    invoice(client, bill, '100')
    post(client, f'/bills/{bill.id}/extra-payment', dict(amount_paid='100', paid_date='2026-10-03'))
    before = client.get('/api/export').json()['data']
    payload = {'data': {'folders': [{'id': 99, 'name': 'Must not be written'}],
                        'bills': [dict(id=bill.id, name='Must not be renamed')],
                        'bill_payments': [dict(id=bill.payments[0].id, **bad_amounts)]}}
    with pytest.raises(ValueError):
        import_data_from_json(db, payload)
    db.rollback()
    assert client.get('/api/export').json()['data'] == before


def test_import_error_identifies_payment_and_preserves_original_cash(account):
    db, bill, client = account
    post(client, f'/bills/{bill.id}/extra-payment', dict(amount_paid='100', paid_date='2026-10-03'))
    payment_id = bill.payments[0].id
    payload = {'data': {'bill_payments': [dict(id=payment_id, amount_paid=100, amount_cents=9000)]}}
    response = client.post('/api/import', files={'file': ('bad.json', json.dumps(payload), 'application/json')}, follow_redirects=False)
    assert response.status_code == 303
    url = urlparse(response.headers['location'])
    assert not url.fragment
    error = parse_qs(url.query)['import_error'][0]
    assert f'Payment #{payment_id}' in error and 'conflicting monetary amounts' in error
    assert error in client.get(response.headers['location']).text
    assert bill.payments[0].amount_paid == 100 and bill.payments[0].amount_cents == 10000


@pytest.mark.parametrize('amounts', [{'amount_paid': 90}, {'amount_cents': 9000}, {'amount_paid': 90, 'amount_cents': None}])
def test_single_imported_cash_value_updates_both_fields_for_ledger(account, amounts):
    db, bill, client = account
    invoice(client, bill, '100')
    post(client, f'/bills/{bill.id}/extra-payment', dict(amount_paid='100', paid_date='2026-10-03'))
    payload = {'data': {'bill_payments': [dict(id=bill.payments[0].id, **amounts)]}}
    for _ in range(2):
        assert import_data_from_json(db, copy.deepcopy(payload))['errors'] == 0
    assert bill.payments[0].amount_paid == 90 and bill.payments[0].amount_cents == 9000
    assert bill.outstanding_cents == 1000
    assert client.get('/expenses').context['all_time_bills'] == 90
    exported = client.get('/api/export').json()
    assert import_data_from_json(db, copy.deepcopy(exported))['errors'] == 0
    assert bill.outstanding_cents == 1000


def test_legacy_import_preserves_unknown_payment_and_simple_reminder(account):
    db, bill, client = account
    payload = {'data': {'bill_payments': [dict(id=1, bill_id=bill.id, amount_paid=None,
                                              amount_cents=None, paid_date='2026-09-01'),
                                         dict(id=2, bill_id=bill.id, amount_paid=20, paid_date='2026-10-01')]}}
    assert import_data_from_json(db, payload)['errors'] == 0
    assert db.get(BillPayment, 1).amount_paid is None
    assert db.get(BillPayment, 2).amount_cents is None and not bill.ledger_enabled
    assert client.get('/expenses').context['all_time_bills'] == 20


def test_future_cash_refunds_and_expenses_enter_reporting_on_their_dates(account, review_day, monkeypatch):
    db, bill, client = account
    for amount, paid_date in [('40', '2026-09-30'), ('100', '2026-10-02'), ('50', '2026-10-08T23:59:59'),
                               ('20', '2026-10-09'), ('30', '2026-11-22'), ('60', '2027-01-02')]:
        if day(paid_date).date() <= date(2026, 10, 8):
            post(client, f'/bills/{bill.id}/extra-payment', dict(amount_paid=amount, paid_date=paid_date))
        else:
            # Older databases may contain future cash recorded before validation.
            db.add(BillPayment(bill=bill, amount_paid=float(amount), amount_cents=int(amount)*100,
                               paid_date=day(paid_date), due_date=day(paid_date)))
    db.commit()
    adjustment(client, bill, 'refund', '10', date='2026-10-03')
    db.add(BillAdjustment(bill=bill, kind='refund', amount_cents=2000, effective_date=day('2026-11-22')))
    reconcile_allocations(db, bill)
    db.add_all([Expense(amount=5, category='Food', date=day('2026-10-08T23:59:59')),
                Expense(amount=100, category='Travel', date=day('2026-10-09'))])
    db.commit()
    before = client.get('/api/export').json()['data']
    response = client.get('/expenses')
    context = response.context
    assert context['bills_past_7_days'] == context['bills_this_month'] == 140
    assert context['bills_ytd'] == context['all_time_bills'] == 180
    assert context['total_spending'] == context['this_month_total'] == context['ytd_total'] == 5
    assert context['total_count'] == 1 and len(context['expenses']) == 2
    assert [c['name'] for c in context['category_breakdown']] == ['Food']
    assert 'Actual spending includes transactions through 2026-10-08' in response.text
    assert len(client.get('/classic/bills').context['recent_payments']) == 3
    assert client.get('/api/export').json()['data'] == before
    class LaterDate(date):
        @classmethod
        def today(cls):
            return date(2026, 11, 22)
    monkeypatch.setattr('main.date_type', LaterDate)
    later = client.get('/expenses').context
    assert later['all_time_bills'] == later['bills_ytd'] == 210
    assert later['bills_this_month'] == 10
    assert later['total_spending'] == 105 and later['total_count'] == 2

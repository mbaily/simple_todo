"""Regressions for opening history, receipt forms and recurrence bounds."""
import copy
import json
from datetime import date, datetime
from html.parser import HTMLParser

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from billing_service import advance_due_date, record_cash_payment, format_cents
from import_logic import import_data_from_json
from main import app, get_db
from models import Base, Bill, BillPayment, Expense, JournalEntry
from test_billing_accounts import account, adjustment, day, invoice, post
from test_billing_import_integrity import rejected


class Forms(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.forms = []
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form':
            self.current = (attrs, {})
            self.forms.append(self.current)
        elif tag == 'input' and self.current is not None and 'name' in attrs:
            if 'disabled' not in attrs and (attrs.get('type') != 'checkbox' or 'checked' in attrs):
                self.current[1][attrs['name']] = attrs.get('value', '')

    def handle_endtag(self, tag):
        if tag == 'form':
            self.current = None

    def fields(self, **matching):
        return next(fields for attrs, fields in self.forms
                    if all(attrs.get(key) == value for key, value in matching.items()))


@pytest.fixture(autouse=True)
def review_clock(monkeypatch):
    class ReviewDate(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 8)

    class ReviewTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 8, 12)

    monkeypatch.setattr('main.datetime', ReviewTime)
    monkeypatch.setattr('main.date_type', ReviewDate)
    monkeypatch.setattr('billing_service.date_type', ReviewDate)


@pytest.mark.parametrize('operation', [
    'invoice', 'confirm', 'invoice-edit', 'bill-edit', 'payment', 'extra-payment',
    'payment-edit', 'adjustment', 'adjustment-edit',
])
def test_history_forms_cannot_move_events_before_opening_balance(account, operation):
    db, bill, client = account
    adjustment(client, bill, 'opening_credit', '10', date='2026-09-01')
    inv = invoice(client, bill, '100', date='2026-09-10', due_date='2026-09-26')
    if operation == 'bill-edit':
        post(client, f'/bills/{bill.id}/invoices',
             dict(invoice_id=inv.id, amount='100', invoice_date='', due_date='2026-09-26'))
    elif operation == 'payment-edit':
        post(client, f'/bills/{bill.id}/pay',
             dict(invoice_id=inv.id, amount_paid='20', paid_date='2026-09-20'))
    elif operation == 'adjustment-edit':
        adjustment(client, bill, 'supplier_credit', '5', date='2026-09-20')
    before = client.get('/api/export').json()['data']
    fields = {}
    if operation in ('invoice', 'invoice-edit'):
        path = f'/bills/{bill.id}/invoices'
        fields = dict(amount='50', invoice_date='2026-08-31', due_date='2026-09-26')
        if operation == 'invoice-edit':
            fields['invoice_id'] = inv.id
    elif operation == 'confirm':
        path = f'/bills/{bill.id}/confirm-invoice'
        fields = dict(actual_amount='50', invoice_date='2026-08-31', actual_due_date='2026-09-26')
    elif operation == 'bill-edit':
        path = f'/bills/{bill.id}/update'
        fields = dict(name='Must not rename', next_due_date='2026-08-31')
    elif operation in ('payment', 'extra-payment'):
        action = 'pay' if operation == 'payment' else operation
        path = f'/bills/{bill.id}/{action}'
        fields = dict(amount_paid='20', paid_date='2026-08-31', invoice_id=inv.id)
    elif operation == 'payment-edit':
        path = f'/bills/payments/{bill.payments[0].id}/edit'
        fields = dict(amount_paid='20', paid_date='2026-08-31')
    else:
        path = f'/bills/{bill.id}/adjustments'
        fields = dict(kind='supplier_credit', amount='5', effective_date='2026-08-31')
        if operation == 'adjustment-edit':
            fields['adjustment_id'] = next(a.id for a in bill.adjustments if a.kind == 'supplier_credit')
    response = client.post(path, data=fields, follow_redirects=False)
    assert response.status_code == 400 and 'first recorded account event' in response.text
    assert client.get('/api/export').json()['data'] == before


def test_correcting_opening_date_allows_backfill_and_export_restore(account):
    db, bill, client = account
    adjustment(client, bill, 'opening_credit', '100', date='2026-09-01')
    opening = bill.adjustments[0]
    post(client, f'/bills/{bill.id}/adjustments',
         dict(adjustment_id=opening.id, kind='opening_credit', amount='100', effective_date='2026-08-01'))
    inv = invoice(client, bill, '50', date='2026-08-01', due_date='2026-08-26')
    assert inv.outstanding_cents == 0 and bill.credit_cents == 5000
    exported = client.get('/api/export').json()
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as restored:
            assert import_data_from_json(restored, copy.deepcopy(exported))['errors'] == 0
            assert restored.get(Bill, bill.id).credit_cents == 5000
    finally:
        engine.dispose()


@pytest.mark.parametrize('confirmed', [False, True])
@pytest.mark.parametrize('restore', [False, True])
def test_deleting_extra_receipt_preserves_settled_account_schedule(account, confirmed, restore):
    db, bill, client = account
    inv = invoice(client, bill, '100', date='2026-09-01', due_date='2026-09-26')
    post(client, f'/bills/{bill.id}/pay', dict(invoice_id=inv.id, amount_paid='100', paid_date='2026-09-20'))
    if confirmed:
        post(client, f'/bills/{bill.id}/confirm-invoice', dict(actual_due_date='2027-01-26'))
    post(client, f'/bills/{bill.id}/extra-payment', dict(amount_paid='10', paid_date='2026-09-27'))
    extra = next(p for p in bill.payments if p.invoice_id is None)
    expected = (bill.next_due_date, bill.is_due_date_confirmed)
    history = client.get(f'/bills/{bill.id}/history')
    assert history.context['payments'][0].id == extra.id
    fields = Forms(history.text).fields(action=f'/bills/payments/{extra.id}/delete')
    assert 'restore_due_date' not in fields
    post(client, f'/bills/payments/{extra.id}/delete', {'restore_due_date': str(restore).lower()})
    assert (bill.next_due_date, bill.is_due_date_confirmed) == expected
    assert inv.outstanding_cents == bill.outstanding_cents == bill.credit_cents == 0


def test_deleting_invoice_receipt_still_restores_outstanding_due_date(account):
    db, bill, client = account
    inv = invoice(client, bill, '100', date='2026-09-01', due_date='2026-09-26')
    post(client, f'/bills/{bill.id}/pay', dict(invoice_id=inv.id, amount_paid='100', paid_date='2026-09-20'))
    post(client, f'/bills/payments/{bill.payments[0].id}/delete', {})
    assert bill.next_due_date == inv.due_date and bill.is_due_date_confirmed
    assert inv.outstanding_cents == bill.outstanding_cents == 10000


def test_legacy_payment_deletion_retains_restore_option(account):
    db, bill, client = account
    receipt = BillPayment(bill=bill, amount_paid=100, due_date=day('2026-09-26'), paid_date=day('2026-09-20'))
    db.add(receipt)
    db.commit()
    fields = Forms(client.get(f'/bills/{bill.id}/history').text).fields(
        action=f'/bills/payments/{receipt.id}/delete')
    assert fields['restore_due_date'] == 'true'
    post(client, f'/bills/payments/{receipt.id}/delete', fields)
    assert bill.next_due_date == day('2026-09-26') and not bill.ledger_enabled


def test_payment_form_from_older_tab_preserves_later_invoice_correction(account):
    db, bill, client = account
    inv = invoice(client, bill, '100', date='2026-09-01', due_date='2026-09-26')
    post(client, f'/bills/{bill.id}/pay', dict(invoice_id=inv.id, amount_paid='100', paid_date='2026-09-20'))
    receipt = bill.payments[0]
    path = f'/bills/payments/{receipt.id}/edit'
    fields = Forms(client.get(f'/bills/{bill.id}/history').text).fields(action=path)
    assert 'due_date' not in fields
    post(client, f'/bills/{bill.id}/invoices',
         dict(invoice_id=inv.id, amount='100', invoice_date='2026-09-01', due_date='2026-09-25'))
    expected_due = bill.next_due_date
    post(client, path, {**fields, 'paid_date': '2026-09-21'})
    assert inv.due_date == receipt.due_date == day('2026-09-25')
    assert bill.next_due_date == expected_due == day('2026-12-25')


def test_history_payment_creation_preserves_later_invoice_correction(account):
    db, bill, client = account
    inv = invoice(client, bill, '100', date='2026-09-01', due_date='2026-09-26')
    path = f'/bills/{bill.id}/pay'
    fields = Forms(client.get(f'/bills/{bill.id}/history').text).fields(action=path)
    assert fields['invoice_id'] == str(inv.id) and 'actual_due_date' not in fields
    post(client, f'/bills/{bill.id}/invoices',
         dict(invoice_id=inv.id, amount='100', invoice_date='2026-09-01', due_date='2026-09-25'))
    post(client, path, fields)
    assert inv.due_date == bill.payments[0].due_date == day('2026-09-25')
    assert bill.outstanding_cents == 0


@pytest.mark.parametrize('due', ['2026-09-26', '2026-10-08', '2026-10-15', '2026-11-26'])
def test_dashboard_payment_corrects_bound_invoice_due_date(account, due):
    db, bill, client = account
    inv = invoice(client, bill, '100', date='2026-09-01', due_date=due)
    # Exercise the Future Estimates form as well as the three actionable sections.
    if due == '2026-11-26':
        bill.is_due_date_confirmed = False
        db.commit()
    fields = Forms(client.get('/classic/bills').text).fields(id=f'pay-form-{bill.id}')
    assert fields['invoice_id'] == str(inv.id)
    fields.update(actual_due_date='2026-10-09', paid_date='2026-10-08')
    post(client, f'/bills/{bill.id}/pay', fields)
    assert len(bill.invoices) == len(bill.payments) == 1
    assert bill.payments[0].invoice_id == inv.id
    assert inv.due_date == bill.payments[0].due_date == day('2026-10-09')
    assert bill.outstanding_cents == 0 and bill.next_due_date == day('2027-01-09')


def test_dashboard_due_correction_does_not_select_another_matching_invoice(account):
    db, bill, client = account
    first = invoice(client, bill, '100', date='2026-09-01', due_date='2026-09-26', invoice_number='A')
    second = invoice(client, bill, '200', date='2026-09-02', due_date='2026-10-09', invoice_number='B')
    fields = Forms(client.get('/classic/bills').text).fields(id=f'pay-form-{bill.id}')
    fields.update(actual_due_date='2026-10-09', amount_paid='100', paid_date='2026-10-08')
    post(client, f'/bills/{bill.id}/pay', fields)
    assert bill.payments[0].invoice_id == first.id
    assert first.due_date == second.due_date == day('2026-10-09')
    assert first.outstanding_cents == 0 and second.outstanding_cents == 20000


def test_payment_without_explicit_invoice_can_correct_outstanding_due_date(account):
    db, bill, client = account
    inv = invoice(client, bill, '100', date='2026-09-01', due_date='2026-09-26')
    post(client, f'/bills/{bill.id}/pay',
         dict(amount_paid='100', paid_date='2026-09-20', actual_due_date='2026-09-25'))
    assert inv.due_date == bill.payments[0].due_date == day('2026-09-25')
    assert bill.outstanding_cents == 0


@pytest.mark.parametrize('operation', ['create', 'update'])
@pytest.mark.parametrize('due,interval', [('2026-09-26', '4000000'), ('2026-09-26', '3652058'),
                                       ('9999-12-30', '2')])
def test_form_recurrence_overflow_is_rejected_atomically(account, operation, due, interval):
    db, bill, client = account
    db.add(BillPayment(bill=bill, amount_paid=20, due_date=day('2026-09-26'), paid_date=day('2026-09-20')))
    db.commit()
    before = client.get('/api/export').json()['data']
    path = '/bills/create' if operation == 'create' else f'/bills/{bill.id}/update'
    fields = dict(name='Must not create or rename', frequency='custom', frequency_days=interval,
                  next_due_date=due, expected_amount='100', ledger_enabled='true')
    response = client.post(path, data=fields, follow_redirects=False)
    assert response.status_code == 400 and 'supported date range' in response.text
    assert client.get('/api/export').json()['data'] == before


@pytest.mark.parametrize('operation', ['create', 'update'])
def test_valid_custom_recurrence_can_be_paid(account, operation):
    db, bill, client = account
    path = '/bills/create' if operation == 'create' else f'/bills/{bill.id}/update'
    post(client, path, dict(name='Custom account', frequency='custom', frequency_days='45',
                           next_due_date='2026-09-26', expected_amount='100'))
    custom = db.query(Bill).filter_by(name='Custom account').one()
    post(client, f'/bills/{custom.id}/pay', dict(amount_paid='100', paid_date='2026-09-20'))
    assert custom.next_due_date == day('2026-11-10')


@pytest.mark.parametrize('frequency,interval', [('custom', 4000000), ('weekly', None), ('monthly', None)])
def test_advance_date_overflow_reports_validation_error(frequency, interval):
    with pytest.raises(ValueError, match='supported date range'):
        advance_due_date(day('9999-12-31'), frequency, interval)


def test_payment_overflow_on_older_malformed_recurrence_is_atomic(account):
    db, bill, client = account
    inv = invoice(client, bill, '100', date='2026-09-01', due_date='2026-09-26')
    bill.frequency, bill.frequency_days = 'custom', 4000000
    db.commit()
    before = client.get('/api/export').json()['data']
    response = client.post(f'/bills/{bill.id}/pay',
                           data=dict(invoice_id=inv.id, amount_paid='100', paid_date='2026-09-20'),
                           follow_redirects=False)
    assert response.status_code == 400 and 'supported date range' in response.text
    assert client.get('/api/export').json()['data'] == before


@pytest.mark.parametrize('kind', ['extra', 'unknown-invoice', 'known-invoice', 'legacy'])
def test_dashboard_timing_requires_effective_due_date(account, kind):
    db, bill, client = account
    if kind == 'legacy':
        db.add(BillPayment(bill=bill, amount_paid=10, due_date=day('2026-10-08'), paid_date=day('2026-10-08')))
        db.commit()
    elif kind == 'extra':
        post(client, f'/bills/{bill.id}/extra-payment', dict(amount_paid='10', paid_date='2026-10-08'))
    else:
        due = '2026-10-08' if kind == 'known-invoice' else ''
        inv = invoice(client, bill, '10', date='2026-09-01', due_date=due)
        post(client, f'/bills/{bill.id}/pay', dict(invoice_id=inv.id, amount_paid='10', paid_date='2026-10-08'))
    # Keep an upcoming forecast to cover both the covered-cycle and recent-payment badges.
    post(client, f'/bills/{bill.id}/confirm-invoice', dict(actual_due_date='2026-11-08'))
    dashboard = client.get('/classic/bills')
    assert bill in dashboard.context['covered_early']
    history = client.get(f'/bills/{bill.id}/history')
    known_due = kind in ('known-invoice', 'legacy')
    assert ('>On time</span>' in dashboard.text) == known_due
    assert ('>On time</span>' in history.text) == known_due


@pytest.mark.parametrize('due', ['2027-01-26', None])
def test_bill_only_import_keeps_outstanding_invoice_reminder(account, due):
    db, bill, client = account
    inv = invoice(client, bill, '100', date='2026-09-01', due_date='2026-09-26')
    assert bill in client.get('/classic/bills').context['overdue']
    assert import_data_from_json(db, {'data': {'bills': [dict(id=bill.id, next_due_date=due,
                                                           is_due_date_confirmed=False)]}})['errors'] == 0
    assert bill.next_due_date == inv.due_date == day('2026-09-26')
    assert bill.is_due_date_confirmed and bill.outstanding_cents == 10000
    assert bill in client.get('/classic/bills').context['overdue']


@pytest.mark.parametrize('older', [False, True])
@pytest.mark.parametrize('confirmed', [False, True])
@pytest.mark.parametrize('new_due', ['2026-09-27', None])
def test_imported_invoice_due_correction_syncs_cash_and_settled_forecast(account, older, confirmed, new_due):
    db, bill, client = account
    first = invoice(client, bill, '100', date='2026-06-01', due_date='2026-06-26')
    post(client, f'/bills/{bill.id}/pay', dict(invoice_id=first.id, amount_paid='100', paid_date='2026-06-20'))
    latest = invoice(client, bill, '100', date='2026-09-01', due_date='2026-09-26')
    post(client, f'/bills/{bill.id}/pay', dict(invoice_id=latest.id, amount_paid='100', paid_date='2026-09-20'))
    if confirmed:
        post(client, f'/bills/{bill.id}/confirm-invoice', dict(actual_due_date='2027-01-15'))
    inv = first if older else latest
    receipt = next(p for p in bill.payments if p.invoice_id == inv.id)
    original_due = bill.next_due_date
    payload = {'data': {'bill_invoices': [dict(id=inv.id, due_date=new_due)]}}
    for _ in range(2):
        assert import_data_from_json(db, copy.deepcopy(payload))['errors'] == 0
        assert inv.due_date == (day(new_due) if new_due else None)
        if new_due:
            assert receipt.due_date == day(new_due)
        assert receipt.effective_due_date == inv.due_date
        expected = original_due if older or confirmed else day('2026-12-27') if new_due else None
        assert bill.next_due_date == expected and bill.outstanding_cents == 0


def test_final_import_confirmation_flag_is_not_taken_from_an_earlier_row(account):
    db, bill, client = account
    inv = invoice(client, bill, '100', date='2026-09-01', due_date='2026-09-26')
    post(client, f'/bills/{bill.id}/pay', dict(invoice_id=inv.id, amount_paid='100', paid_date='2026-09-20'))
    payload = {'data': {'bills': [dict(id=bill.id, is_due_date_confirmed=True),
                                dict(id=bill.id, is_due_date_confirmed=False)],
                        'bill_invoices': [dict(id=inv.id, due_date='2026-09-27')]}}
    assert import_data_from_json(db, payload)['errors'] == 0
    assert bill.next_due_date == day('2026-12-27') and not bill.is_due_date_confirmed


@pytest.mark.parametrize('changes', [
    dict(period_start='not-a-date'), dict(period_end='not-a-date'), dict(period_start=''),
    dict(period_start=42), dict(period_end=False),
    dict(period_start='2026-09-30', period_end='2026-08-01'),
    dict(period_start='2026-08-01T12:00:00', period_end='2026-08-01T11:00:00'),
])
def test_invalid_imported_service_period_rolls_back_entire_upload(account, changes):
    db, bill, client = account
    inv = invoice(client, bill, '100', date='2026-09-01', due_date='2026-09-26',
                  period_start='2026-08-01', period_end='2026-08-31')
    rejected(account, {'bill_invoices': [dict(id=inv.id, **changes)]}, 'Invoice #1')


def test_merged_service_period_corrections_and_null_clearing_remain_supported(account):
    db, bill, client = account
    inv = invoice(client, bill, '100', date='2026-09-01', period_start='2026-08-01', period_end='2026-08-31')
    rows = [dict(id=inv.id, period_start='2026-09-01'), dict(id=inv.id, period_end='2026-09-30')]
    assert import_data_from_json(db, {'data': {'bill_invoices': rows}})['errors'] == 0
    assert inv.period_start == day('2026-09-01') and inv.period_end == day('2026-09-30')
    assert import_data_from_json(db, {'data': {'bill_invoices': [dict(id=inv.id, period_start=None)]}})['errors'] == 0
    assert inv.period_start is None and inv.period_end == day('2026-09-30')


@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('blank', [False, True])
def test_multiple_partial_receipt_imports_preserve_latest_dates(account, existing, blank):
    db, bill, client = account
    if existing:
        receipt = BillPayment(bill=bill, amount_paid=100, paid_date=day('2026-09-20'), due_date=day('2026-09-26'))
        db.add(receipt)
        db.commit()
        receipt_id = receipt.id
    else:
        receipt_id = 99
    first = dict(id=receipt_id, bill_id=bill.id, amount_paid=100,
                 paid_date='2026-09-21', due_date='2026-09-27')
    second = dict(id=receipt_id, note='Note-only correction')
    if blank:
        second.update(paid_date='', due_date=None)
    assert import_data_from_json(db, {'data': {'bill_payments': [first, second]}})['errors'] == 0
    receipt = db.get(BillPayment, receipt_id)
    assert receipt.paid_date == day('2026-09-21') and receipt.due_date == day('2026-09-27')
    assert receipt.note == 'Note-only correction' and receipt.amount_paid == 100


def test_multiple_partial_adjustment_imports_preserve_latest_effective_date(account):
    db, bill, client = account
    adjustment(client, bill, 'supplier_credit', '10', date='2026-09-20')
    row = bill.adjustments[0]
    payload = {'data': {'bill_adjustments': [dict(id=row.id, effective_date='2026-09-21'),
                                           dict(id=row.id, note='Note-only correction')]}}
    assert import_data_from_json(db, payload)['errors'] == 0
    assert row.effective_date == day('2026-09-21') and row.note == 'Note-only correction'


def test_multiple_partial_bill_imports_derive_cash_cents_from_final_tracking(account):
    db, bill, client = account
    payload = {'data': {'bills': [dict(id=bill.id, ledger_enabled=True), dict(id=bill.id, name='Renamed')],
                        'bill_payments': [dict(id=99, bill_id=bill.id, amount_paid=100,
                                              paid_date='2026-09-20', due_date='2026-09-26')]}}
    assert import_data_from_json(db, payload)['errors'] == 0
    assert bill.credit_cents == 10000 and db.get(BillPayment, 99).amount_cents == 10000


@pytest.mark.parametrize('case', ['unfunded', 'later-cash', 'unknown-issue-date', 'excessive-correction'])
def test_ordinary_refund_imports_require_dated_credit(account, case):
    db, bill, client = account
    bill.ledger_enabled = True
    db.commit()
    row = dict(id=99, bill_id=bill.id, kind='refund', amount_cents=5000, effective_date='2026-09-10')
    if case == 'later-cash':
        post(client, f'/bills/{bill.id}/extra-payment', dict(amount_paid='100', paid_date='2026-09-20'))
    elif case == 'unknown-issue-date':
        post(client, f'/bills/{bill.id}/invoices', dict(amount='10', due_date='2026-09-26'))
        post(client, f'/bills/{bill.id}/extra-payment', dict(amount_paid='100', paid_date='2026-09-01'))
    elif case == 'excessive-correction':
        adjustment(client, bill, 'supplier_credit', '100', date='2026-09-01')
        adjustment(client, bill, 'refund', '20', date='2026-09-10')
        refund = next(a for a in bill.adjustments if a.kind == 'refund')
        row = dict(id=refund.id, amount_cents=15000)
    rejected(account, {'bill_adjustments': [row]}, 'bill_adjustments.*(credit recorded|missing invoice issue dates)')


def test_refund_import_uses_complete_upload_and_retains_unchanged_history(account):
    db, bill, client = account
    payload = {'data': {'bills': [dict(id=bill.id, ledger_enabled=True)],
                        'bill_adjustments': [dict(id=99, bill_id=bill.id, kind='refund', amount_cents=5000,
                                                 effective_date='2026-09-10')],
                        'bill_payments': [dict(id=99, bill_id=bill.id, amount_paid=100,
                                              paid_date='2026-09-01', due_date='2026-09-26')]}}
    assert import_data_from_json(db, copy.deepcopy(payload))['errors'] == 0
    assert bill.credit_cents == 5000 and bill.outstanding_cents == 0
    post(client, '/bills/payments/99/delete', {})
    assert bill.outstanding_cents == 5000
    assert import_data_from_json(db, {'data': {'bill_adjustments': [dict(id=99, note='Keep actual refund')]}})['errors'] == 0
    assert bill.outstanding_cents == 5000
    assert import_data_from_json(db, copy.deepcopy(client.get('/api/export').json()))['errors'] == 0


def test_explicit_backup_restore_preserves_historical_overdrawn_refunds(account):
    db, bill, client = account
    post(client, f'/bills/{bill.id}/extra-payment', dict(amount_paid='100', paid_date='2026-09-01'))
    adjustment(client, bill, 'refund', '50', date='2026-09-10')
    post(client, f'/bills/payments/{bill.payments[0].id}/delete', {})
    exported = client.get('/api/export').json()
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as restored:
            with pytest.raises(ValueError, match='credit recorded.*No changes were saved'):
                import_data_from_json(restored, copy.deepcopy(exported))
            assert not restored.query(Bill).all()
            app.dependency_overrides[get_db] = lambda: restored
            try:
                response = client.post('/api/import',
                                       files={'file': ('backup.json', json.dumps(exported), 'application/json')},
                                       follow_redirects=False)
                assert response.status_code == 303 and 'import_error=' in response.headers['location']
                assert not restored.query(Bill).all()
                response = client.post('/api/import', data={'restore_history': 'true'},
                                       files={'file': ('backup.json', json.dumps(exported), 'application/json')},
                                       follow_redirects=False)
                assert response.status_code == 303 and 'import_success=1' in response.headers['location']
                assert restored.get(Bill, bill.id).outstanding_cents == 5000
            finally:
                app.dependency_overrides[get_db] = lambda: db
            for _ in range(2):
                assert import_data_from_json(restored, copy.deepcopy(exported), restore_history=True)['errors'] == 0
                assert restored.get(Bill, bill.id).outstanding_cents == 5000
    finally:
        engine.dispose()
    assert 'name="restore_history"' in client.get('/customization').text
    before = client.get('/api/export').json()['data']
    exported['data']['bill_adjustments'][0]['effective_date'] = '2026-10-09'
    with pytest.raises(ValueError, match='future.*No changes were saved'):
        import_data_from_json(db, exported, restore_history=True)
    assert client.get('/api/export').json()['data'] == before


def test_historical_refund_restore_rejects_partial_uploads(account):
    db, bill, client = account
    before = client.get('/api/export').json()['data']
    with pytest.raises(ValueError, match='full JSON backup.*No changes were saved'):
        import_data_from_json(db, {'data': {'bills': [dict(id=bill.id, name='Do not rename')]}},
                              restore_history=True)
    assert client.get('/api/export').json()['data'] == before


@pytest.mark.parametrize('operation', ['pay', 'extra-payment', 'edit', 'legacy-pay'])
@pytest.mark.parametrize('amount', ['90071992547409.91', '90071992547409.90'])
def test_imprecise_cash_form_is_rejected_without_mutating_history(account, operation, amount):
    db, bill, client = account
    if operation != 'legacy-pay':
        inv = invoice(client, bill, '100', date='2026-09-01', due_date='2026-09-26')
    if operation == 'edit':
        post(client, f'/bills/{bill.id}/pay', dict(invoice_id=inv.id, amount_paid='100', paid_date='2026-09-20'))
        path = f'/bills/payments/{bill.payments[0].id}/edit'
    else:
        path = f'/bills/{bill.id}/{"pay" if operation == "legacy-pay" else operation}'
    before = client.get('/api/export').json()['data']
    response = client.post(path, data=dict(amount_paid=amount, paid_date='2026-09-21', due_date='2026-09-27',
                                          note='Must not change', request_token='imprecise-cash'),
                           follow_redirects=False)
    assert response.status_code == 400 and 'cannot be stored precisely' in response.text
    assert client.get('/api/export').json()['data'] == before


def test_cash_service_rejects_imprecision_before_enabling_tracking(account):
    db, bill, client = account
    before = client.get('/api/export').json()['data']
    with pytest.raises(ValueError, match='cannot be stored precisely'):
        record_cash_payment(db, bill, 9007199254740991, day('2026-09-20'))
    assert client.get('/api/export').json()['data'] == before


def test_precise_large_cash_remains_exportable(account):
    db, bill, client = account
    post(client, f'/bills/{bill.id}/extra-payment', dict(amount_paid='10000000000000.01', paid_date='2026-09-20'))
    assert bill.payments[0].amount_cents == 1000000000000001
    assert f'{bill.payments[0].amount_paid:.2f}' == '10000000000000.01'
    assert import_data_from_json(db, copy.deepcopy(client.get('/api/export').json()))['errors'] == 0


@pytest.mark.parametrize('currency,symbol', [('GBP', '\u00a3'), ('EUR', '\u20ac'), ('AUD', '$')])
def test_cash_displays_and_journal_entries_use_bill_currency(account, currency, symbol):
    db, bill, client = account
    bill.currency = currency
    db.commit()
    inv = invoice(client, bill, '100', date='2026-09-01', due_date='2026-10-26')
    post(client, f'/bills/{bill.id}/pay', dict(invoice_id=inv.id, amount_paid='100', paid_date='2026-10-08'))
    history = client.get(f'/bills/{bill.id}/history').text
    dashboard = client.get('/classic/bills').text
    assert f'<td>{symbol}100.00' in history
    assert f'<span class="bill-amount">{symbol}100.00' in dashboard
    assert f'Paid {symbol}100.00' in dashboard
    assert f'{symbol}100.00' in db.query(JournalEntry).filter_by(action='Paid Bill').one().details
    post(client, f'/bills/{bill.id}/extra-payment', dict(amount_paid='10', paid_date='2026-10-08'))
    assert f'{symbol}10.00' in db.query(JournalEntry).filter_by(action='Extra Bill Payment').one().details


@pytest.mark.parametrize('operation', ['invoice', 'confirm', 'import'])
def test_reminder_uses_earliest_due_date_without_changing_allocation_order(account, operation):
    db, bill, client = account
    first = invoice(client, bill, '100', date='2026-09-01', due_date='2026-10-30', invoice_number='A')
    if operation == 'invoice':
        second = invoice(client, bill, '100', date='2026-10-01', due_date='2026-10-03', invoice_number='B')
    elif operation == 'confirm':
        post(client, f'/bills/{bill.id}/confirm-invoice',
             dict(actual_amount='100', invoice_date='2026-10-01', actual_due_date='2026-10-03', invoice_number='B'))
        second = bill.invoices[-1]
    else:
        import_data_from_json(db, {'data': {'bill_invoices': [dict(id=first.id + 1, bill_id=bill.id,
                             amount_cents=10000, invoice_date='2026-10-01', due_date='2026-10-03', invoice_number='B')]}})
        second = bill.invoices[-1]
    assert bill.open_invoice == second and bill.next_due_date == day('2026-10-03')
    assert client.get('/classic/bills').context['overdue'] == [bill]
    fields = Forms(client.get('/classic/bills').text).fields(id=f'pay-form-{bill.id}')
    assert fields['invoice_id'] == str(second.id)
    post(client, f'/bills/{bill.id}/extra-payment', dict(amount_paid='50', paid_date='2026-10-02'))
    assert first.outstanding_cents == 5000 and second.outstanding_cents == 10000
    assert bill.next_due_date == day('2026-10-03')
    assert import_data_from_json(db, copy.deepcopy(client.get('/api/export').json()))['errors'] == 0
    assert bill.next_due_date == day('2026-10-03')


def test_known_due_invoice_is_not_hidden_by_an_unknown_due_invoice(account):
    db, bill, client = account
    known = invoice(client, bill, '100', date='2026-09-01', due_date='2026-10-03', invoice_number='Known')
    unknown = invoice(client, bill, '100', date='2026-09-02', due_date='', invoice_number='Unknown')
    assert bill.open_invoice == known and client.get('/classic/bills').context['overdue'] == [bill]
    post(client, f'/bills/{bill.id}/pay', dict(invoice_id=known.id, amount_paid='100', paid_date='2026-10-03'))
    assert bill.open_invoice == unknown and bill.next_due_date is None
    assert client.get('/classic/bills').context['unscheduled'] == [bill]


def test_bill_due_edit_reconciles_changed_invoice_order_and_reminder(account):
    db, bill, client = account
    first = invoice(client, bill, '100', date='', due_date='2026-10-01', invoice_number='A')
    second = invoice(client, bill, '100', date='', due_date='2026-10-05', invoice_number='B')
    post(client, f'/bills/{bill.id}/extra-payment', dict(amount_paid='50', paid_date='2026-10-01'))
    fields = Forms(client.get(f'/bills/{bill.id}/edit').text).fields(
        action=f'/bills/{bill.id}/update')
    fields.update(next_due_date='2026-10-20', frequency=bill.frequency)
    post(client, f'/bills/{bill.id}/update', fields)
    assert first.due_date == day('2026-10-20')
    assert first.outstanding_cents == 10000 and second.outstanding_cents == 5000
    assert bill.open_invoice == second and bill.next_due_date == day('2026-10-05')
    assert client.get('/classic/bills').context['overdue'] == [bill]
    assert import_data_from_json(db, copy.deepcopy(client.get('/api/export').json()))['errors'] == 0
    assert bill.next_due_date == day('2026-10-05')


@pytest.mark.parametrize('cents,expected', [
    (0, '0.00'), (1, '0.01'), (-1, '-0.01'), (100, '1.00'),
    (9007199254740990, '90071992547409.90'),
    (9223372036854775807, '92233720368547758.07'),
    (-9223372036854775808, '-92233720368547758.08'),
])
def test_integer_cent_formatting_is_exact(cents, expected):
    assert format_cents(cents) == expected


@pytest.mark.parametrize('kind', ['invoice', 'invoice-statement', 'adjustment', 'statement'])
@pytest.mark.parametrize('amount', ['90071992547409.90', '92233720368547758.07'])
def test_note_only_ledger_edits_preserve_exact_large_amounts(account, kind, amount):
    db, bill, client = account
    if kind in ('invoice', 'invoice-statement'):
        metadata = dict(statement_balance=amount, balance_type='credit') if kind == 'invoice-statement' else {}
        record = invoice(client, bill, amount if kind == 'invoice' else '100',
                         date='2026-09-01', due_date='2026-09-26', **metadata)
        field, route = 'invoice_id', 'invoices'
    elif kind == 'adjustment':
        adjustment(client, bill, 'supplier_debit', amount, date='2026-09-01')
        record = bill.adjustments[-1]
        field, route = 'adjustment_id', 'adjustments'
    else:
        post(client, f'/bills/{bill.id}/statements',
             dict(amount=amount, balance_type='credit', as_of_date='2026-09-01'))
        record = bill.statements[-1]
        field, route = 'statement_id', 'statements'
    before = client.get('/api/export').json()['data']
    forms = Forms(client.get(f'/bills/{bill.id}/history').text)
    fields = next(fields for _, fields in forms.forms if fields.get(field) == str(record.id))
    assert fields['statement_balance' if kind == 'invoice-statement' else 'amount'] == amount
    fields = {**fields, 'note': 'Changed note only'}
    if kind == 'adjustment':
        fields['kind'] = 'supplier_debit'
    elif kind in ('statement', 'invoice-statement'):
        fields['balance_type'] = 'credit'
    post(client, f'/bills/{bill.id}/{route}', fields)
    after = client.get('/api/export').json()['data']
    collection = {'invoice': 'bill_invoices', 'invoice-statement': 'bill_invoices',
                  'adjustment': 'bill_adjustments', 'statement': 'bill_statements'}[kind]
    saved = next(item for item in after[collection] if item['id'] == record.id)
    original = next(item for item in before[collection] if item['id'] == record.id)
    assert saved['note'] == 'Changed note only'
    for monetary_field in ('amount_cents', 'statement_balance_cents', 'balance_cents'):
        if monetary_field in original:
            assert saved[monetary_field] == original[monetary_field]
    assert import_data_from_json(db, copy.deepcopy(client.get('/api/export').json()))['errors'] == 0
    if kind == 'invoice':
        dashboard = client.get('/classic/bills')
        fields = Forms(dashboard.text).fields(id=f'pay-form-{bill.id}')
        assert fields['amount_paid'] == amount
        assert f'data-amount="{amount}"' in dashboard.text
        assert f'Owing ${amount}' in dashboard.text


@pytest.mark.parametrize('correction', [False, True])
def test_confirmed_invoice_rejects_imprecise_derived_estimate_atomically(account, correction):
    db, bill, client = account
    fields = dict(actual_amount='90071992547409.91', invoice_date='2026-09-01',
                  actual_due_date='2026-09-26', request_token='large-estimate')
    if correction:
        inv = invoice(client, bill, '100', date='2026-09-01', due_date='2026-09-26')
        fields['invoice_target'] = inv.id
    before = client.get('/api/export').json()['data']
    response = client.post(f'/bills/{bill.id}/confirm-invoice', data=fields, follow_redirects=False)
    assert response.status_code == 400 and 'Recurring estimate cannot be stored precisely' in response.text
    assert client.get('/api/export').json()['data'] == before


def test_confirmed_precise_large_estimate_remains_exportable(account):
    db, bill, client = account
    post(client, f'/bills/{bill.id}/confirm-invoice',
         dict(actual_amount='10000000000000.01', invoice_date='2026-09-01', actual_due_date='2026-09-26'))
    assert f'{bill.expected_amount:.2f}' == '10000000000000.01'
    assert import_data_from_json(db, copy.deepcopy(client.get('/api/export').json()))['errors'] == 0


@pytest.mark.parametrize('operation', ['default-payment', 'partial-payment'])
def test_cash_against_large_invoice_cannot_create_imprecise_cash_or_estimate(account, operation):
    db, bill, client = account
    inv = invoice(client, bill, '90071992547409.93', date='2026-09-01', due_date='2026-09-26')
    fields = dict(invoice_id=inv.id, paid_date='2026-09-20')
    if operation == 'partial-payment':
        fields['amount_paid'] = '100'
    before = client.get('/api/export').json()['data']
    response = client.post(f'/bills/{bill.id}/pay', data=fields, follow_redirects=False)
    assert response.status_code == 400 and 'cannot be stored precisely' in response.text
    assert client.get('/api/export').json()['data'] == before


@pytest.mark.parametrize('include_bills', [False, True])
def test_bills_and_expenses_keep_budget_cash_refunds_and_categories_in_each_currency(account, include_bills):
    db, bill, client = account
    bill.currency, bill.frequency, bill.amount_strategy = 'GBP', 'monthly', 'fixed'
    bill.expected_amount = 100
    euro = Bill(name='Euro account', currency='EUR', frequency='monthly', expected_amount=200)
    inactive = Bill(name='Inactive dollar account', currency='USD', frequency='monthly',
                    expected_amount=50, is_active=False)
    db.add_all([euro, inactive])
    db.commit()
    inv = invoice(client, bill, '100', date='2026-09-01', due_date='2026-09-26')
    post(client, f'/bills/{bill.id}/pay', dict(invoice_id=inv.id, amount_paid='120', paid_date='2026-10-03'))
    adjustment(client, bill, 'refund', '10', date='2026-10-04')
    for account_bill, amount in ((euro, '200'), (inactive, '50')):
        post(client, f'/bills/{account_bill.id}/extra-payment', dict(amount_paid=amount, paid_date='2026-10-03'))
    for currency, amount in (('AUD', 25), ('GBP', 5), ('EUR', 7)):
        db.add(Expense(amount=amount, currency=currency, category='Food', date=day('2026-10-03')))
    db.add(Expense(amount=1000, currency='GBP', category='Future', date=day('2026-10-09')))
    db.add(BillPayment(bill=bill, amount_paid=500, due_date=day('2026-10-09'), paid_date=day('2026-10-09')))
    db.commit()
    dashboard = client.get('/classic/bills')
    budgets = {row['currency']: row['bills_monthly'] for row in dashboard.context['currency_summaries']}
    assert budgets == {'EUR': 200, 'GBP': 100} and dashboard.context['monthly_total'] is None
    assert 'EUR <strong>\u20ac200.00</strong>' in dashboard.text
    assert 'GBP <strong>\u00a3100.00</strong>' in dashboard.text
    response = client.get(f'/expenses?include_bills={int(include_bills)}')
    summaries = {row['currency']: row for row in response.context['currency_summaries']}
    assert set(summaries) == {'AUD', 'EUR', 'GBP', 'USD'}
    assert summaries['GBP']['bills_monthly'] == 100
    for currency, spending, net_cash in [('AUD', 25, 0), ('GBP', 5, 110), ('EUR', 7, 200), ('USD', 0, 50)]:
        row = summaries[currency]
        assert row['total_spending'] == spending
        assert row['all_time_bills'] == row['bills_past_7_days'] == row['bills_this_month'] == row['bills_ytd'] == net_cash
        assert row['combined_total_spending'] == row['combined_past_7_days'] == spending + net_cash
        assert row['combined_monthly_avg'] == pytest.approx(row['monthly_avg'] + row['bills_monthly'])
        assert row['combined_yearly_avg'] == pytest.approx(row['yearly_avg'] + row['bills_yearly'])
    assert summaries['USD']['bills_monthly'] == 0
    assert 'combined_total_spending' not in response.context
    assert len(response.context['category_breakdown']) == 3
    assert all(category['percent'] == 100 for category in response.context['category_breakdown'])
    assert 'id="val-all-time-GBP"' in response.text
    assert 'data-exp="\u00a35.00"' in response.text and 'data-comb="\u00a3115.00"' in response.text
    ledger = client.get('/expenses/list')
    totals = {row['currency']: row['total_spending'] for row in ledger.context['currency_summaries']}
    assert totals == {'AUD': 25, 'EUR': 7, 'GBP': 1005}
    assert 'GBP \u00a31005.00' in ledger.text and 'EUR \u20ac7.00' in ledger.text
    assert client.get('/expenses/all').context['currency_summaries'] == ledger.context['currency_summaries']


@pytest.mark.parametrize('currency,symbol', [(' gbp ', '\u00a3'), ('EUR', '\u20ac'), ('USD', '$')])
def test_refund_only_inactive_account_retains_its_currency_in_spending(account, currency, symbol):
    db, bill, client = account
    bill.currency, bill.is_active = currency, False
    db.commit()
    adjustment(client, bill, 'supplier_credit', '20', date='2026-09-01')
    adjustment(client, bill, 'refund', '20', date='2026-10-04')
    response = client.get('/expenses?include_bills=1')
    summary = response.context['currency_summaries'][0]
    assert summary['currency'] == currency.strip().upper()
    assert summary['currency_symbol'] == symbol
    assert summary['all_time_bills'] == summary['combined_total_spending'] == -20
    assert f'data-comb="{symbol}-20.00"' in response.text

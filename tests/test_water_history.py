"""Quarterly history regressions using fictional statements, never a user's bills."""
import copy
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from main import app, get_db
from models import Base, Bill
from billing_service import record_invoice, record_cash_payment
from import_logic import import_data_from_json

# Synthetic, evenly spaced examples; these are not supplier records.
QUARTERS = [
    ('2018-01-01', '2018-02-01', '2018-01-15', 60, 'Example quarter A'),
    ('2018-04-01', '2018-05-01', '2018-04-15', 75, 'Example quarter B'),
    ('2018-07-01', '2018-08-01', '2018-07-15', 90, 'Example quarter C'),
    ('2018-10-01', '2018-11-01', '2018-10-15', 105, 'Example quarter D'),
]


def day(value):
    return datetime.fromisoformat(value)


@pytest.fixture
def water_account(monkeypatch):
    monkeypatch.setenv('SIMPLE_TODO_BASIC_AUTH', '0')
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        water = Bill(name='Example quarterly account', frequency='quarterly', expected_amount=105)
        other = Bill(name='Unrelated example account', frequency='bimonthly', expected_amount=45)
        db.add_all([water, other])
        db.flush()
        for issued, due, received, amount, label in QUARTERS:
            invoice = record_invoice(db, water, str(amount), invoice_date=day(issued), note=label)
            payment = record_cash_payment(db, water, amount * 100, day(received), invoice, note='Example receipt')
            payment.payment_method = 'Example card'
        water.payments[0].is_late_overridden = True
        db.commit()
        app.dependency_overrides[get_db] = lambda: db
        try:
            with TestClient(app) as client:
                yield db, water, other, client
        finally:
            app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def set_due_dates(db, bill):
    for invoice, (issued, due, received, amount, label) in zip(bill.ordered_invoices, QUARTERS):
        record_invoice(db, bill, str(amount), day(due), invoice=invoice)
    db.commit()


def test_quarterly_history_preserves_individual_receipts_and_totals(water_account):
    db, water, other, client = water_account
    assert water.ledger_enabled and water.outstanding_cents == water.credit_cents == 0
    assert len(water.invoices) == len(water.payments) == 4
    assert sum(i.amount_cents for i in water.invoices) == sum(p.amount_cents for p in water.payments) == 33000
    assert [i.amount_cents for i in water.ordered_invoices] == [6000, 7500, 9000, 10500]
    assert [i.settled_at for i in water.ordered_invoices] == [day(row[2]) for row in QUARTERS]
    overridden = [p for p in water.payments if p.is_late_overridden]
    assert len(overridden) == 1 and overridden[0].note == 'Example receipt'
    assert not other.ledger_enabled and not other.invoices


def test_missing_due_dates_remain_unknown_without_lateness_claims(water_account):
    db, water, other, client = water_account
    assert all(i.due_date is None and i.period_start is None and i.period_end is None for i in water.invoices)
    assert all(not p.timing_label and not p.is_late for p in water.payments)
    html = client.get(f'/bills/{water.id}/history').text
    assert html.count('<td>Unknown</td>') == 8
    assert '>LATE<' not in html and '>On time<' not in html
    for value in ['Example quarter A', 'Example quarter D', 'Example card', '$75.00']:
        assert value in html


def test_backfilled_due_dates_preserve_cash_metadata_and_allocations(water_account):
    db, water, other, client = water_account
    invoice_ids = {i.id for i in water.invoices}
    payments = {p.id: (p.paid_date, p.amount_cents, p.note, p.is_late_overridden) for p in water.payments}
    allocations = [(a.id, a.invoice_id, a.payment_id, a.amount_cents) for i in water.invoices for a in i.allocations]
    set_due_dates(db, water)
    assert {i.id for i in water.invoices} == invoice_ids
    assert {p.id: (p.paid_date, p.amount_cents, p.note, p.is_late_overridden) for p in water.payments} == payments
    assert [(a.id, a.invoice_id, a.payment_id, a.amount_cents) for i in water.invoices for a in i.allocations] == allocations
    assert [i.due_date for i in water.ordered_invoices] == [day(row[1]) for row in QUARTERS]
    assert all(p.due_date == p.invoice.due_date and p.days_difference > 0 for p in water.payments)
    assert water.outstanding_cents == water.credit_cents == 0
    assert water.next_due_date == day('2019-02-01') and not water.is_due_date_confirmed
    assert water.anchor_day == 1


def test_correcting_latest_due_date_updates_forecast_and_timing(water_account):
    db, water, other, client = water_account
    set_due_dates(db, water)
    latest = water.ordered_invoices[-1]
    response = client.post(f'/bills/{water.id}/invoices', data={
        'invoice_id': latest.id, 'amount': '105', 'due_date': '2018-11-05'
    }, follow_redirects=False)
    assert response.status_code == 303
    assert latest.due_date == day('2018-11-05')
    assert all(p.due_date == day('2018-11-05') for p in water.payments if p.invoice_id == latest.id)
    assert water.next_due_date == day('2019-02-05') and not water.is_due_date_confirmed


def test_backfilled_dates_preserve_newer_outstanding_invoice(water_account):
    db, water, other, client = water_account
    newer = record_invoice(db, water, '125', day('2019-02-07'))
    db.commit()
    set_due_dates(db, water)
    assert water.open_invoice == newer and water.outstanding_cents == 12500
    assert water.next_due_date == day('2019-02-07') and water.is_due_date_confirmed


def test_quarterly_history_export_import_remains_idempotent(water_account):
    db, water, other, client = water_account
    set_due_dates(db, water)
    payload = client.get('/api/export').json()
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as restored:
        for _ in range(2):
            stats = import_data_from_json(restored, copy.deepcopy(payload))
            restored.commit()
            restored.expire_all()
            assert stats['errors'] == 0
            account = restored.get(Bill, water.id)
            assert len(account.invoices) == len(account.payments) == 4
            assert sum(p.amount_cents for p in account.payments) == 33000
            assert account.outstanding_cents == account.credit_cents == 0
            assert [i.due_date for i in account.ordered_invoices] == [day(row[1]) for row in QUARTERS]
    engine.dispose()

"""Application billing history regressions with entirely fictional statements."""
import copy
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from main import app, get_db
from models import Base, Bill
from billing_service import record_invoice, record_cash_payment, advance_due_date
from import_logic import import_data_from_json


def day(value):
    return datetime.fromisoformat(value)


@pytest.fixture
def utilities(monkeypatch):
    monkeypatch.setenv('SIMPLE_TODO_BASIC_AUTH', '0')
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        gas = Bill(name='Example heating account', frequency='bimonthly')
        electricity = Bill(name='Example power account', frequency='quarterly')
        db.add_all([gas, electricity])
        db.flush()
        first = record_invoice(db, gas, '150', day('2018-02-20'), invoice_date=day('2018-02-01'),
                               period_start=day('2017-12-01'), period_end=day('2018-01-31'))
        record_cash_payment(db, gas, 15000, day('2018-02-10'), first)
        record_cash_payment(db, gas, 15000, day('2018-02-10'))
        record_invoice(db, gas, '120', day('2018-04-20'), invoice_date=day('2018-04-01'),
                       period_start=day('2018-02-01'), period_end=day('2018-03-31'), statement_balance_cents=-3000)
        record_invoice(db, gas, '110', day('2018-06-20'), invoice_date=day('2018-06-01'),
                       period_start=day('2018-04-01'), period_end=day('2018-05-31'), statement_balance_cents=8000)
        split = record_invoice(db, electricity, '200', day('2018-01-25'), invoice_date=day('2018-01-05'))
        record_cash_payment(db, electricity, 2500, day('2018-01-10'), split)
        record_cash_payment(db, electricity, 17500, day('2018-01-15'), split)
        next_invoice = record_invoice(db, electricity, '180', day('2018-04-25'), invoice_date=day('2018-04-05'))
        record_cash_payment(db, electricity, 18000, day('2018-04-20'), next_invoice)
        db.commit()
        app.dependency_overrides[get_db] = lambda: db
        try:
            with TestClient(app) as client:
                yield db, gas, electricity, client
        finally:
            app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def test_fictional_supplier_history_preserves_charges_and_cash(utilities):
    db, gas, electricity, client = utilities
    assert sum(i.amount_cents for i in gas.invoices) == 38000
    assert sum(p.amount_cents for p in gas.payments) == 30000
    assert gas.outstanding_cents == 8000 and gas.credit_cents == 0
    assert len(gas.payments) == 2 and len(gas.invoices) == 3
    assert sum(i.amount_cents for i in electricity.invoices) == 38000
    assert sum(p.amount_cents for p in electricity.payments) == 38000
    assert electricity.outstanding_cents == electricity.credit_cents == 0


def test_invoice_dates_credit_statements_and_billing_cycles(utilities):
    db, gas, electricity, client = utilities
    second, third = gas.ordered_invoices[1:]
    assert gas.frequency == 'bimonthly' and gas.next_due_date == day('2018-06-20')
    assert second.statement_balance_cents == -3000 and second.credit_applied_cents == 12000
    assert third.amount_cents == 11000 and third.statement_balance_cents == 8000
    assert third.credit_applied_cents == 3000 and third.period_end == day('2018-05-31')
    assert electricity.next_due_date == day('2018-07-25')
    html = client.get(f'/bills/{gas.id}/history').text
    for value in ['2018-06-20', '2018-04-01', '2018-05-31', '$30.00 credit', 'every 2 months']:
        assert value in html


def test_split_payment_settles_one_invoice(utilities):
    db, gas, electricity, client = utilities
    invoice = electricity.ordered_invoices[0]
    assert invoice.amount_cents == 20000 and invoice.outstanding_cents == 0
    assert sorted(a.amount_cents for a in invoice.allocations) == [2500, 17500]
    assert all(a.payment.invoice_id == invoice.id and not a.is_credit for a in invoice.allocations)
    assert invoice.settled_at == day('2018-01-15')


def test_history_import_is_idempotent_and_survives_export(utilities):
    db, gas, electricity, client = utilities
    payload = client.get('/api/export').json()
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as restored:
        for _ in range(2):
            stats = import_data_from_json(restored, copy.deepcopy(payload))
            restored.commit()
            restored.expire_all()
            assert stats['errors'] == 0
            gas_copy = restored.get(Bill, gas.id)
            assert gas_copy.outstanding_cents == 8000 and len(gas_copy.payments) == 2
            assert gas_copy.open_invoice.period_end == day('2018-05-31')
            assert gas_copy.ordered_invoices[0].invoice_date == day('2018-02-01')
            assert restored.get(Bill, electricity.id).outstanding_cents == 0
    engine.dispose()


def test_backfilled_old_invoice_allocates_before_newer_invoice(utilities):
    db, gas, electricity, client = utilities
    bill = Bill(name='Backfill example')
    db.add(bill)
    db.commit()
    newer = record_invoice(db, bill, '50', day('2018-06-20'), invoice_date=day('2018-06-01'))
    record_cash_payment(db, bill, 4000, day('2018-06-15'), newer)
    older = record_invoice(db, bill, '100', day('2017-12-20'), invoice_date=day('2017-12-01'))
    record_cash_payment(db, bill, 10000, day('2017-12-15'), older)
    db.commit()
    assert older.id > newer.id
    assert older.outstanding_cents == 0 and newer.outstanding_cents == 1000
    assert bill.open_invoice == newer and bill.next_due_date == day('2018-06-20')


def test_invoice_metadata_form_accepts_credit_and_rejects_reversed_period(utilities):
    db, gas, electricity, client = utilities
    response = client.post(f'/bills/{gas.id}/invoices', data={'amount': '90', 'invoice_date': '2018-08-01',
        'period_start': '2018-06-01', 'period_end': '2018-07-31', 'due_date': '2018-08-20',
        'statement_balance': '15', 'balance_type': 'credit'}, follow_redirects=False)
    assert response.status_code == 303
    invoice = next(i for i in gas.invoices if i.invoice_date == day('2018-08-01'))
    assert invoice.statement_balance_cents == -1500
    response = client.post(f'/bills/{gas.id}/invoices', data={'amount': '100', 'period_start': '2018-08-01',
                                                           'period_end': '2018-06-01'}, follow_redirects=False)
    assert response.status_code == 400 and len(gas.invoices) == 4


def test_bimonthly_forecasts_and_monthly_budget(utilities):
    db, gas, electricity, client = utilities
    gas.is_active = electricity.is_active = False
    db.commit()
    response = client.post('/bills/create', data={'name': 'Every two months', 'frequency': 'bimonthly',
                                                'expected_amount': '100', 'next_due_date': '2018-10-26'}, follow_redirects=False)
    assert response.status_code == 303
    bill = db.query(Bill).filter_by(name='Every two months').one()
    assert bill.frequency == 'bimonthly'
    assert advance_due_date(day('2018-10-26'), 'bimonthly', anchor_day=26) == day('2018-12-26')
    assert advance_due_date(day('2018-12-31'), 'bimonthly', anchor_day=31) == day('2019-02-28')
    assert client.get('/classic/bills').context['monthly_total'] == 50
    assert client.get('/expenses').context['bills_monthly'] == 50

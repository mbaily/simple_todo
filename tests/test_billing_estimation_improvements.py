from datetime import datetime
from fastapi.testclient import TestClient

from main import app, get_db
from models import Bill, BillInvoice, BillPayment
from billing_service import forecast_due_date, refresh_invoice_forecast


def test_forecast_due_date_rolls_weekend_to_monday_when_history_is_weekday_only(db_session):
    bill = Bill(name="Gas", frequency="bimonthly", anchor_day=26, ledger_enabled=True)
    db_session.add(bill)
    db_session.flush()
    # Two historical weekday due dates (Fri 2026-06-26, Wed 2026-08-26)
    inv1 = BillInvoice(bill=bill, amount_cents=18000, due_date=datetime(2026, 6, 26))
    inv2 = BillInvoice(bill=bill, amount_cents=18500, due_date=datetime(2026, 8, 26))
    inv3 = BillInvoice(bill=bill, amount_cents=18112, due_date=datetime(2026, 10, 26))
    db_session.add_all([inv1, inv2, inv3])
    db_session.commit()
    db_session.refresh(bill)

    # 2026-10-26 + 2 months -> 2026-12-26 (Saturday) -> rolls to Monday 2026-12-28
    next_due = forecast_due_date(bill, datetime(2026, 10, 26))
    assert next_due == datetime(2026, 12, 28)
    assert bill.anchor_day == 26  # Anchor remains unshifted


def test_forecast_due_date_keeps_weekend_if_history_has_weekend(db_session):
    bill = Bill(name="Sub", frequency="monthly", anchor_day=26, ledger_enabled=True)
    db_session.add(bill)
    db_session.flush()
    # 2026-09-26 is Saturday, 2026-10-26 is Monday
    inv1 = BillInvoice(bill=bill, amount_cents=3000, due_date=datetime(2026, 9, 26))
    inv2 = BillInvoice(bill=bill, amount_cents=3000, due_date=datetime(2026, 10, 26))
    db_session.add_all([inv1, inv2])
    db_session.commit()
    db_session.refresh(bill)

    # 2026-12-26 is Saturday; since history includes a Saturday, do not roll
    next_due = forecast_due_date(bill, datetime(2026, 11, 26))
    assert next_due == datetime(2026, 12, 26)


def test_refresh_invoice_forecast_uses_median_anchor_day(db_session):
    bill = Bill(name="Electricity", frequency="quarterly", anchor_day=None, ledger_enabled=True)
    db_session.add(bill)
    db_session.flush()
    inv1 = BillInvoice(bill=bill, amount_cents=28000, invoice_date=datetime(2025, 10, 27), due_date=datetime(2025, 11, 14))
    inv2 = BillInvoice(bill=bill, amount_cents=19000, invoice_date=datetime(2026, 1, 27), due_date=datetime(2026, 2, 13))
    inv3 = BillInvoice(bill=bill, amount_cents=31000, invoice_date=datetime(2026, 7, 27), due_date=datetime(2026, 8, 13))
    # Latest invoice shifted to Monday 16th because 14th/15th was a weekend
    inv4 = BillInvoice(bill=bill, amount_cents=26000, invoice_date=datetime(2026, 10, 28), due_date=datetime(2026, 11, 16))
    db_session.add_all([inv1, inv2, inv3, inv4])
    db_session.flush()
    for inv in (inv1, inv2, inv3, inv4):
        db_session.add(BillPayment(bill=bill, invoice_id=inv.id, due_date=inv.due_date,
                                   paid_date=inv.due_date, amount_paid=inv.amount_cents / 100,
                                   amount_cents=inv.amount_cents))
    db_session.commit()
    db_session.refresh(bill)
    from billing_service import reconcile_allocations
    reconcile_allocations(db_session, bill)

    bill.anchor_day = None
    refresh_invoice_forecast(bill, inv4, previous_due=None)
    # Median of [13, 13, 14, 16] is 13.5 -> round(13.5) = 14, not 16
    assert bill.anchor_day == 14
    assert bill.next_due_date == datetime(2027, 2, 15)  # 2027-02-14 is Sunday -> Monday 15th


def test_estimated_amount_period_normalization_and_seasonal_blend(db_session):
    bill = Bill(name="Gas", frequency="bimonthly", amount_strategy="rolling_avg", ledger_enabled=True)
    db_session.add(bill)
    db_session.flush()
    # 6 bimonthly invoices (1 full year). Last one is 68 days (should be normalized to ~60 days).
    invs = [
        BillInvoice(bill=bill, amount_cents=18000, invoice_date=datetime(2025, 12, 4),
                    due_date=datetime(2025, 12, 23), period_start=datetime(2025, 10, 7), period_end=datetime(2025, 12, 5)),   # 60d -> 180.00
        BillInvoice(bill=bill, amount_cents=20400, invoice_date=datetime(2026, 2, 10),
                    due_date=datetime(2026, 2, 27), period_start=datetime(2025, 12, 6), period_end=datetime(2026, 2, 11)),   # 68d -> 180.00
        BillInvoice(bill=bill, amount_cents=18000, invoice_date=datetime(2026, 4, 10),
                    due_date=datetime(2026, 4, 30), period_start=datetime(2026, 2, 12), period_end=datetime(2026, 4, 12)),   # 60d -> 180.00
        BillInvoice(bill=bill, amount_cents=18000, invoice_date=datetime(2026, 6, 10),
                    due_date=datetime(2026, 6, 29), period_start=datetime(2026, 4, 13), period_end=datetime(2026, 6, 11)),   # 60d -> 180.00
        BillInvoice(bill=bill, amount_cents=18000, invoice_date=datetime(2026, 8, 10),
                    due_date=datetime(2026, 8, 27), period_start=datetime(2026, 6, 12), period_end=datetime(2026, 8, 10)),   # 60d -> 180.00
        BillInvoice(bill=bill, amount_cents=18000, invoice_date=datetime(2026, 10, 7),
                    due_date=datetime(2026, 10, 26), period_start=datetime(2026, 8, 11), period_end=datetime(2026, 10, 9)),   # 60d -> 180.00
    ]
    db_session.add_all(invs)
    db_session.commit()
    db_session.refresh(bill)

    assert bill.estimated_amount == 180.0


def test_estimated_amount_seasonal_blend_quarterly(db_session):
    bill = Bill(name="Power", frequency="quarterly", amount_strategy="rolling_avg", ledger_enabled=True)
    db_session.add(bill)
    db_session.flush()
    # Q1=300, Q2=200, Q3=200, Q4=200 -> predicting next Q1:
    # base_wma(200, 200, 200, 300) = (4*200 + 3*200 + 2*200 + 1*300)/10 = 210.0
    # yoy = Q1 = 300.0 -> 0.65 * 210 + 0.35 * 300 = 136.5 + 105.0 = 241.5
    invs = [
        BillInvoice(bill=bill, amount_cents=30000, invoice_date=datetime(2026, 1, 15), due_date=datetime(2026, 2, 1)),
        BillInvoice(bill=bill, amount_cents=20000, invoice_date=datetime(2026, 4, 15), due_date=datetime(2026, 5, 1)),
        BillInvoice(bill=bill, amount_cents=20000, invoice_date=datetime(2026, 7, 15), due_date=datetime(2026, 8, 1)),
        BillInvoice(bill=bill, amount_cents=20000, invoice_date=datetime(2026, 10, 15), due_date=datetime(2026, 11, 1)),
    ]
    db_session.add_all(invs)
    db_session.commit()
    db_session.refresh(bill)

    assert bill.estimated_amount == 241.5


def test_dedup_unlinked_payments_and_variable_history_range(db_session):
    bill = Bill(name="Water", frequency="quarterly", amount_strategy="fixed", expected_amount=227.08, ledger_enabled=False)
    db_session.add(bill)
    db_session.flush()
    p1 = BillPayment(bill=bill, due_date=datetime(2026, 1, 2), paid_date=datetime(2025, 12, 14), amount_paid=204.22)
    p2 = BillPayment(bill=bill, due_date=datetime(2026, 4, 7), paid_date=datetime(2026, 3, 25), amount_paid=194.81)
    p3 = BillPayment(bill=bill, due_date=datetime(2026, 7, 7), paid_date=datetime(2026, 6, 18), amount_paid=242.40)
    # Duplicate unlinked payment on same date & amount
    p3_dup = BillPayment(bill=bill, due_date=datetime(2026, 7, 7), paid_date=datetime(2026, 6, 18), amount_paid=242.40)
    db_session.add_all([p1, p2, p3, p3_dup])
    db_session.commit()
    db_session.refresh(bill)

    assert len(bill._historical_charge_amounts()) == 3
    assert bill.estimated_amount == 227.08  # fixed strategy preserved
    assert bill.has_variable_history is True
    rng = bill.estimated_amount_range
    assert rng is not None and rng[0] < rng[1]

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        resp = client.get(f"/bills/{bill.id}/history")
        assert resp.status_code == 200
        assert "History varies (typical" in resp.text
    finally:
        app.dependency_overrides.clear()

from datetime import datetime, timedelta
import random
from fastapi.testclient import TestClient

from main import app, get_db
from models import Bill, BillInvoice, BillPayment, BillAdjustment
from billing_service import (
    advance_due_date,
    balance_as_of,
    forecast_due_date,
    reconcile_allocations,
    record_cash_payment,
    record_invoice,
)
from import_logic import import_data_from_json


def test_sandbox_fake_data_all_frequencies_and_anchor_edge_cases():
    """Exercise advance_due_date across all frequencies and month-end/leap-year anchors."""
    cases = [
        ("weekly", None, None, datetime(2026, 1, 5), datetime(2026, 1, 12)),
        ("fortnightly", None, None, datetime(2026, 1, 5), datetime(2026, 1, 19)),
        ("monthly", None, 31, datetime(2024, 1, 31), datetime(2024, 2, 29)),
        ("monthly", None, 31, datetime(2025, 1, 31), datetime(2025, 2, 28)),
        ("bimonthly", None, 30, datetime(2026, 12, 30), datetime(2027, 2, 28)),
        ("quarterly", None, 31, datetime(2026, 11, 30), datetime(2027, 2, 28)),
        ("yearly", None, 29, datetime(2024, 2, 29), datetime(2025, 2, 28)),
        ("custom", 45, None, datetime(2026, 1, 1), datetime(2026, 2, 15)),
    ]
    for freq, fdays, anchor, start, expected in cases:
        assert advance_due_date(start, freq, fdays, anchor) == expected


def test_sandbox_fake_utility_lifecycle_ledger_credit_adjustments_and_ui(db_session):
    """End-to-end fake-data test: invoices, split/over-payments, net-after-credit, adjustments, UI & API."""
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    rng = random.Random(42)

    try:
        # 1. Create a fake quarterly utility bill via HTTP POST
        resp = client.post("/bills/create", data={
            "name": "Sandbox Power",
            "payee": "Fake Energy Co",
            "expected_amount": "250.00",
            "amount_strategy": "rolling_avg",
            "currency": "AUD",
            "next_due_date": "2025-02-14",
            "anchor_day": "14",
            "frequency": "quarterly",
            "ledger_enabled": "true",
            "category": "Utilities",
        }, follow_redirects=False)
        assert resp.status_code == 303

        bill = db_session.query(Bill).filter_by(name="Sandbox Power").one()

        # 2. Record opening credit adjustment dated before first invoice
        adj = BillAdjustment(
            bill=bill,
            kind="opening_credit",
            amount_cents=5000,
            effective_date=datetime(2024, 10, 1),
            note="Fake opening credit",
        )
        db_session.add(adj)
        db_session.commit()

        # 3. Simulate 6 quarterly cycles with varying period lengths (88-93 days) and amounts
        base_period_start = datetime(2024, 10, 20)
        for cycle in range(6):
            p_len = 89 + (cycle % 4)
            p_start = base_period_start
            p_end = p_start + timedelta(days=p_len - 1)
            inv_date = p_end + timedelta(days=3)
            # Weekday due date ~18 days after issue
            due_date = inv_date + timedelta(days=18)
            if due_date.weekday() >= 5:
                due_date += timedelta(days=7 - due_date.weekday())

            charge_dollars = round(220.0 + 40.0 * (cycle % 2) + rng.uniform(-15, 15), 2)
            if cycle == 0:
                # Cycle 0: net-after-credit using the $50 opening credit
                net_dollars = round(charge_dollars - 50.0, 2)
                inv = record_invoice(
                    db_session, bill, net_dollars, due_date=due_date, amount_type="net",
                    invoice_date=inv_date, period_start=p_start, period_end=p_end,
                    invoice_number=f"SBX-{cycle+1}",
                )
                assert inv.amount_cents == round(charge_dollars * 100)
            else:
                inv = record_invoice(
                    db_session, bill, charge_dollars, due_date=due_date, amount_type="gross",
                    invoice_date=inv_date, period_start=p_start, period_end=p_end,
                    invoice_number=f"SBX-{cycle+1}",
                )

            # Split payment on cycle 2, overpayment on cycle 4, normal payment otherwise
            if cycle == 2:
                record_cash_payment(db_session, bill, 10000, inv_date + timedelta(days=2), invoice=inv)
                rem = inv.amount_cents - 10000
                record_cash_payment(db_session, bill, rem, inv_date + timedelta(days=5), invoice=inv)
            elif cycle == 4:
                # Overpay by $25.00 to generate credit
                record_cash_payment(db_session, bill, inv.amount_cents + 2500, inv_date + timedelta(days=4), invoice=inv)
            elif cycle == 5:
                # Cycle 5: partial payment leaving balance owing
                record_cash_payment(db_session, bill, max(100, inv.amount_cents - 2500), inv_date + timedelta(days=4), invoice=inv)
            else:
                record_cash_payment(db_session, bill, inv.amount_cents, inv_date + timedelta(days=4), invoice=inv)

            base_period_start = p_end + timedelta(days=1)

        db_session.commit()
        db_session.refresh(bill)
        reconcile_allocations(db_session, bill)
        db_session.commit()
        db_session.refresh(bill)

        # $50 opening credit + $25 overpay on cycle 4 covered the $25 short-pay on cycle 5 -> $50 credit left!
        assert bill.outstanding_cents == 0
        assert bill.credit_cents == 5000
        bal, complete = balance_as_of(bill, datetime(2027, 1, 1))
        assert complete is True
        assert bal == -5000

        # Estimation & range checks
        est = bill.estimated_amount
        est_rng = bill.estimated_amount_range
        assert est is not None and est > 0
        assert est_rng is not None and est_rng[0] <= est <= est_rng[1]
        assert bill.budget_amount == est
        assert forecast_due_date(bill, bill.ordered_invoices[-1].due_date).weekday() < 5

        # 4. Exercise Web UI & DaisyUI & JSON API routes
        for url in [
            "/bills",
            f"/bills/{bill.id}/history",
            f"/bills/{bill.id}/edit",
            "/daisyui_test/bills",
            f"/daisyui_test/bills/{bill.id}",
        ]:
            r = client.get(url)
            assert r.status_code == 200, f"Failed GET {url}: {r.status_code}"

        api_list = client.get("/api/bills")
        assert api_list.status_code == 200
        bills_json = api_list.json()
        bill_items = [
            b for g in ("overdue", "due_today", "upcoming", "covered_early", "future_estimates", "unscheduled")
            for b in bills_json.get(g, [])
        ]
        assert any(b["name"] == "Sandbox Power" for b in bill_items)

        # 5. Serialize -> Import roundtrip in-memory
        from serializers import serialize_bill
        serialized = serialize_bill(bill)
        stats = import_data_from_json(db_session, {"version": 2, "data": {"bills": [serialized]}})
        assert stats["errors"] == 0
    finally:
        app.dependency_overrides.clear()

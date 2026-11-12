from datetime import datetime, timedelta, date, timezone
import pytest
from fastapi.testclient import TestClient

from main import app, _advance_due_date, get_db
from models import Bill, BillPayment, JournalEntry, format_calendar_date, _as_utc_aware, _to_date
from helpers import _midnight, _parse_date_or_datetime
from import_logic import import_data_from_json


@pytest.fixture(autouse=True)
def received_cash_clock(monkeypatch):
    # These historical lifecycle samples span the full 2026 calendar year.
    class ReviewDate(date):
        @classmethod
        def today(cls):
            return max(date.today(), date(2026, 12, 31))
    monkeypatch.setattr('billing_service.date_type', ReviewDate)

def test_advance_due_date_anchor_day():
    # Jan 31st with anchor_day 31 should advance to Feb 28th, then Mar 31st, then Apr 30th
    d1 = datetime(2026, 1, 31)
    d2 = _advance_due_date(d1, "monthly", anchor_day=31)
    assert d2 == datetime(2026, 2, 28)

    d3 = _advance_due_date(d2, "monthly", anchor_day=31)
    assert d3 == datetime(2026, 3, 31)

    d4 = _advance_due_date(d3, "monthly", anchor_day=31)
    assert d4 == datetime(2026, 4, 30)

    d5 = _advance_due_date(d4, "monthly", anchor_day=31)
    assert d5 == datetime(2026, 5, 31)


def test_advance_due_date_quarterly_and_yearly():
    d1 = datetime(2026, 1, 31)
    d_q = _advance_due_date(d1, "quarterly", anchor_day=31)
    assert d_q == datetime(2026, 4, 30)

    d_q2 = _advance_due_date(d_q, "quarterly", anchor_day=31)
    assert d_q2 == datetime(2026, 7, 31)

    # Leap year handling for yearly
    d_leap = datetime(2028, 2, 29)
    d_year = _advance_due_date(d_leap, "yearly", anchor_day=29)
    assert d_year == datetime(2029, 2, 28)


def test_bill_payment_timing_properties():
    # Paid 21 days early
    due = datetime(2026, 10, 25)
    paid = datetime(2026, 10, 4)
    p_early = BillPayment(due_date=due, paid_date=paid, amount_paid=80.0)
    assert p_early.days_difference == 21
    assert p_early.timing_label == "21 days early"
    assert not p_early.is_late

    # Paid on due date
    p_ontime = BillPayment(due_date=due, paid_date=due, amount_paid=80.0)
    assert p_ontime.days_difference == 0
    assert p_ontime.timing_label == "On time"
    assert not p_ontime.is_late

    # Paid 3 days late
    paid_late = datetime(2026, 10, 28)
    p_late = BillPayment(due_date=due, paid_date=paid_late, amount_paid=80.0)
    assert p_late.days_difference == -3
    assert p_late.timing_label == "3 days late"
    assert p_late.is_late

    # Late with override
    p_late.is_late_overridden = True
    assert not p_late.is_late


def test_bill_is_covered_early():
    bill = Bill(
        name="Internet",
        next_due_date=datetime(2026, 11, 25),
        anchor_day=25,
        frequency="monthly"
    )
    # Payment was made for October 25 due date on October 4
    payment = BillPayment(
        due_date=datetime(2026, 10, 25),
        paid_date=datetime(2026, 10, 4),
        amount_paid=80.0
    )
    bill.payments.append(payment)

    # Today is Oct 5 (before Oct 25) -> It was paid early and covers the current cycle
    now_oct5 = datetime(2026, 10, 5)
    assert bill.is_covered_early(now_oct5) is True

    # Today is Nov 1 (past Oct 25) -> The October cycle has passed, November is now upcoming
    now_nov1 = datetime(2026, 11, 1)
    assert bill.is_covered_early(now_nov1) is False


def test_bill_lifecycle_via_client(db_session, monkeypatch):
    class MockDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 5)
    monkeypatch.setattr("main.datetime", MockDatetime)

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    # 1. Create a bill
    res = client.post("/bills/create", data={
        "name": "Electricity",
        "next_due_date": "2026-10-25",
        "frequency": "monthly",
        "expected_amount": "150.00",
        "payee": "Power Co",
        "anchor_day": "25"
    }, follow_redirects=False)
    assert res.status_code == 303

    bill = db_session.query(Bill).filter(Bill.name == "Electricity").first()
    assert bill is not None
    assert bill.anchor_day == 25
    assert bill.next_due_date == datetime(2026, 10, 25)
    assert bill.is_due_date_confirmed is False

    # 2. Confirm invoice: provider bill arrived with due date 2026-10-22 and amount 164.50
    res_conf = client.post(f"/bills/{bill.id}/confirm-invoice", data={
        "actual_due_date": "2026-10-22",
        "actual_amount": "164.50"
    }, follow_redirects=False)
    assert res_conf.status_code == 303

    db_session.refresh(bill)
    assert bill.next_due_date == datetime(2026, 10, 22)
    assert bill.expected_amount == 164.50
    assert bill.is_due_date_confirmed is True

    # 3. Pay bill 18 days early (on Oct 4)
    res_pay = client.post(f"/bills/{bill.id}/pay", data={
        "amount_paid": "164.50",
        "paid_date": "2026-10-04",
        "note": "Paid via BPAY"
    }, follow_redirects=False)
    assert res_pay.status_code == 303

    db_session.refresh(bill)
    # Payment record
    assert len(bill.payments) == 1
    payment = bill.payments[0]
    assert payment.due_date == datetime(2026, 10, 22)
    assert payment.paid_date == datetime(2026, 10, 4)
    assert payment.amount_paid == 164.50
    assert payment.days_difference == 18
    assert payment.timing_label == "18 days early"

    # Next due date should advance using anchor_day 25 -> 2026-11-25
    assert bill.next_due_date == datetime(2026, 11, 25)
    assert bill.is_due_date_confirmed is False

    # 4. Check dashboard view
    res_dash = client.get("/classic/bills")
    assert res_dash.status_code == 200
    assert "Paid for Current Cycle" in res_dash.text
    assert "18 days early" in res_dash.text

    # 5. Edit payment
    res_edit = client.post(f"/bills/payments/{payment.id}/edit", data={
        "paid_date": "2026-10-05",
        "due_date": "2026-10-22",
        "amount_paid": "165.00",
        "note": "Updated note"
    }, follow_redirects=False)
    assert res_edit.status_code == 303

    db_session.refresh(payment)
    assert payment.paid_date == datetime(2026, 10, 5)
    assert payment.amount_paid == 165.00
    assert payment.days_difference == 17

    # 6. Delete payment and restore due date
    res_del = client.post(f"/bills/payments/{payment.id}/delete", data={
        "restore_due_date": "true"
    }, follow_redirects=False)
    assert res_del.status_code == 303

    db_session.refresh(bill)
    assert len(bill.payments) == 0
    assert bill.next_due_date == datetime(2026, 10, 22)

    app.dependency_overrides.clear()

def test_monthly_end_of_month_advancement_chain(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    # Bill created on Jan 31
    client.post("/bills/create", data={
        "name": "Gym",
        "next_due_date": "2026-01-31",
        "frequency": "monthly",
        "expected_amount": "50.00",
    }, follow_redirects=False)

    bill = db_session.query(Bill).filter(Bill.name == "Gym").first()
    assert bill.anchor_day == 31

    # Pay Jan 31 bill -> advances to Feb 28
    client.post(f"/bills/{bill.id}/pay", data={"paid_date": "2026-01-31"})
    db_session.refresh(bill)
    assert bill.next_due_date == datetime(2026, 2, 28)

    # Pay Feb 28 bill -> advances to Mar 31 (recovering the 31st!)
    client.post(f"/bills/{bill.id}/confirm-invoice", data={"actual_due_date": "2026-02-28", "actual_amount": "50.00"}, follow_redirects=False)
    client.post(f"/bills/{bill.id}/pay", data={"paid_date": "2026-02-28"})
    db_session.refresh(bill)
    assert bill.next_due_date == datetime(2026, 3, 31)

    # Pay Mar 31 bill -> advances to Apr 30
    client.post(f"/bills/{bill.id}/confirm-invoice", data={"actual_due_date": "2026-03-31", "actual_amount": "50.00"}, follow_redirects=False)
    client.post(f"/bills/{bill.id}/pay", data={"paid_date": "2026-03-31"})
    db_session.refresh(bill)
    assert bill.next_due_date == datetime(2026, 4, 30)

    # Pay Apr 30 bill -> advances to May 31
    client.post(f"/bills/{bill.id}/confirm-invoice", data={"actual_due_date": "2026-04-30", "actual_amount": "50.00"}, follow_redirects=False)
    client.post(f"/bills/{bill.id}/pay", data={"paid_date": "2026-04-30"})
    db_session.refresh(bill)
    assert bill.next_due_date == datetime(2026, 5, 31)

    app.dependency_overrides.clear()


def test_recent_payments_30_day_window(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    now = datetime.now()
    bill = Bill(name="Water", next_due_date=now + timedelta(days=20), frequency="quarterly")
    db_session.add(bill)
    db_session.commit()

    # Payment made 20 days ago (within 30 days window)
    p1 = BillPayment(bill_id=bill.id, due_date=now - timedelta(days=5), paid_date=now - timedelta(days=20), amount_paid=100.0)
    # Payment made 45 days ago (outside 30 days window)
    p2 = BillPayment(bill_id=bill.id, due_date=now - timedelta(days=40), paid_date=now - timedelta(days=45), amount_paid=90.0)
    db_session.add_all([p1, p2])
    db_session.commit()

    res = client.get("/classic/bills")
    assert res.status_code == 200
    assert "$100.00" in res.text
    # $90.00 should not appear in recent payments because it's older than 30 days
    assert "$90.00" not in res.text

    app.dependency_overrides.clear()

def test_pay_with_actual_due_date_direct(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    # Bill estimated for Nov 15 with anchor day 15
    client.post("/bills/create", data={
        "name": "Gas",
        "next_due_date": "2026-11-15",
        "frequency": "monthly",
        "expected_amount": "95.00",
        "anchor_day": "15"
    }, follow_redirects=False)

    bill = db_session.query(Bill).filter(Bill.name == "Gas").first()

    # User clicks Pay directly and changes actual_due_date to 2026-11-19 (printed on invoice)
    # and pays on 2026-11-01 (18 days early)
    res_pay = client.post(f"/bills/{bill.id}/pay", data={
        "actual_due_date": "2026-11-19",
        "amount_paid": "98.40",
        "paid_date": "2026-11-01",
        "note": "Paid invoice directly"
    }, follow_redirects=False)
    assert res_pay.status_code == 303

    db_session.refresh(bill)
    assert len(bill.payments) == 1
    payment = bill.payments[0]
    # Stamped with the actual invoice due date
    assert payment.due_date == datetime(2026, 11, 19)
    assert payment.paid_date == datetime(2026, 11, 1)
    assert payment.amount_paid == 98.40
    assert payment.days_difference == 18
    assert payment.timing_label == "18 days early"

    # Following cycle automatically advances using anchor day 15 -> Dec 15
    assert bill.next_due_date == datetime(2026, 12, 15)

    app.dependency_overrides.clear()


def test_edit_bill_payment_preserves_note(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    bill = Bill(name="Phone", next_due_date=datetime(2026, 10, 10), frequency="monthly")
    db_session.add(bill)
    db_session.commit()

    # Create payment with a note
    payment = BillPayment(
        bill_id=bill.id,
        due_date=datetime(2026, 10, 10),
        paid_date=datetime(2026, 10, 9),
        amount_paid=55.0,
        note='[{"text": "Receipt #98765", "created_at": "2026-10-09 14:00"}]',
    )
    db_session.add(payment)
    db_session.commit()

    # Edit payment details without specifying a note (empty note)
    res = client.post(f"/bills/payments/{payment.id}/edit", data={
        "due_date": "2026-10-12",
        "paid_date": "2026-10-09",
        "amount_paid": "59.95",
        "note": "",
    }, follow_redirects=False)
    assert res.status_code == 303

    db_session.refresh(payment)
    assert payment.due_date == datetime(2026, 10, 12)
    assert payment.amount_paid == 59.95
    # The note should NOT be erased!
    assert payment.note is not None
    assert len(payment.notes_list) == 1
    assert payment.notes_list[0]["text"] == "Receipt #98765"

    app.dependency_overrides.clear()


def test_delete_bill_payment_restore_due_date(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    bill = Bill(name="Water", next_due_date=datetime(2026, 10, 1), frequency="monthly", anchor_day=1, expected_amount=100.0)
    db_session.add(bill)
    db_session.commit()

    # Pay Oct 1 bill -> advances to Nov 1
    client.post(f"/bills/{bill.id}/pay", data={"paid_date": "2026-10-01"})
    db_session.refresh(bill)
    assert bill.next_due_date == datetime(2026, 11, 1)

    client.post(f"/bills/{bill.id}/confirm-invoice", data={"actual_due_date": "2026-11-01", "actual_amount": "100.00"}, follow_redirects=False)

    # Pay Nov 1 bill -> advances to Dec 1
    client.post(f"/bills/{bill.id}/pay", data={"paid_date": "2026-11-01"})
    db_session.refresh(bill)
    assert bill.next_due_date == datetime(2026, 12, 1)

    payments = (
        db_session.query(BillPayment)
        .filter(BillPayment.bill_id == bill.id)
        .order_by(BillPayment.due_date.desc(), BillPayment.id.desc())
        .all()
    )
    assert len(payments) == 2
    latest_p = payments[0]   # Nov 1 payment
    older_p = payments[1]    # Oct 1 payment

    # Removing cash reallocates the remaining receipt to October; November becomes outstanding.
    client.post(f"/bills/payments/{older_p.id}/delete", data={"restore_due_date": "true"}, follow_redirects=False)
    db_session.refresh(bill)
    assert bill.next_due_date == datetime(2026, 11, 1)

    # Removing the last receipt leaves both invoices outstanding; October is due first.
    client.post(f"/bills/payments/{latest_p.id}/delete", data={"restore_due_date": "true"}, follow_redirects=False)
    db_session.refresh(bill)
    assert bill.next_due_date == datetime(2026, 10, 1)
    assert bill.outstanding_cents == 20000

    app.dependency_overrides.clear()


def test_invalid_date_resilience(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    # 1. Invalid date on create falls back gracefully to today
    res = client.post("/bills/create", data={
        "name": "Invalid Date Bill",
        "next_due_date": "not-a-valid-date",
        "frequency": "monthly",
    }, follow_redirects=False)
    assert res.status_code == 303
    bill = db_session.query(Bill).filter(Bill.name == "Invalid Date Bill").first()
    assert bill is not None
    assert bill.next_due_date is not None

    # 2. Invalid payment dates reject the receipt without changing balances
    res = client.post(f"/bills/{bill.id}/pay", data={
        "amount_paid": "75.00",
        "paid_date": "nonsense",
        "actual_due_date": "rubbish",
        "next_due_override": "bogus",
    }, follow_redirects=False)
    assert res.status_code == 400
    assert len(bill.payments) == 0

    # 3. Invalid date on confirm invoice does not crash
    res = client.post(f"/bills/{bill.id}/confirm-invoice", data={
        "actual_due_date": "bad-date-format",
    }, follow_redirects=False)
    assert res.status_code == 303

    app.dependency_overrides.clear()


def test_advance_due_date_out_of_bounds():
    # Negative or extreme anchor days should be clamped safely
    d = datetime(2026, 5, 10)
    d_neg = _advance_due_date(d, "monthly", anchor_day=-5)
    # Falls back to current_due.day (10)
    assert d_neg == datetime(2026, 6, 10)

    d_high = _advance_due_date(d, "monthly", anchor_day=50)
    # Clamped to current_due.day (10)
    assert d_high == datetime(2026, 6, 10)


def test_dashboard_14_day_threshold(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    now = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # Bill A: 10 days away (unconfirmed) -> should be upcoming
    bill_a = Bill(name="A_10Days", next_due_date=now + timedelta(days=10), is_due_date_confirmed=False)
    # Bill B: 25 days away (unconfirmed) -> should be in future estimates
    bill_b = Bill(name="B_25Days", next_due_date=now + timedelta(days=25), is_due_date_confirmed=False)
    # Bill C: 25 days away (confirmed) -> should be in upcoming
    bill_c = Bill(name="C_25DaysConfirmed", next_due_date=now + timedelta(days=25), is_due_date_confirmed=True)

    db_session.add_all([bill_a, bill_b, bill_c])
    db_session.commit()

    res = client.get("/classic/bills")
    assert res.status_code == 200
    html = res.text

    # Bill A and Bill C should be under Upcoming
    upcoming_part = html.split("<h3>📋 Upcoming</h3>")[1].split("<h3>")[0]
    assert "A_10Days" in upcoming_part
    assert "C_25DaysConfirmed" in upcoming_part
    assert "B_25Days" not in upcoming_part

    # Bill B should be under Future Estimates
    future_part = html.split("<h3>📅 Future Estimates</h3>")[1].split("</div>")[0]
    assert "B_25Days" in future_part

    app.dependency_overrides.clear()


def test_format_calendar_date_no_shift():
    # A calendar date at midnight should format strictly to its year-month-day without timezone offset
    d = datetime(2026, 10, 25, 0, 0, 0)
    assert format_calendar_date(d) == "2026-10-25"
    assert format_calendar_date(None) == ""


def test_display_note_clean_summary():
    # Multiple notes serialized as JSON should be formatted cleanly as human-readable text
    p = BillPayment(
        due_date=datetime(2026, 10, 1),
        paid_date=datetime(2026, 10, 1),
        note='[{"text": "BPay Ref 1122", "created_at": "2026-10-01 10:00"}, {"text": "Receipt #5544", "created_at": "2026-10-01 10:05"}]'
    )
    assert p.display_note == "BPay Ref 1122, Receipt #5544"

    # Legacy plain string note should also be formatted cleanly
    p_plain = BillPayment(
        due_date=datetime(2026, 10, 1),
        paid_date=datetime(2026, 10, 1),
        note="Simple plain note"
    )
    assert p_plain.display_note == "Simple plain note"

    # Empty note returns empty string
    p_empty = BillPayment(due_date=datetime(2026, 10, 1), paid_date=datetime(2026, 10, 1))
    assert p_empty.display_note == ""


def test_inactive_bills_separation(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    now = datetime.now()
    # Create an inactive bill whose due date is in the past
    inactive_bill = Bill(name="Old Deactivated ISP", next_due_date=now - timedelta(days=40), is_active=False)
    # Create an active overdue bill
    active_overdue = Bill(name="Active Overdue Rent", next_due_date=now - timedelta(days=2), is_active=True)
    db_session.add_all([inactive_bill, active_overdue])
    db_session.commit()

    # 1. Default dashboard (show_inactive=0): inactive bill is not shown at all
    res = client.get("/classic/bills")
    assert res.status_code == 200
    assert "Active Overdue Rent" in res.text
    assert "Old Deactivated ISP" not in res.text

    # 2. Show inactive dashboard (show_inactive=1):
    # Inactive bill must NOT appear in Overdue, but must appear in Inactive Bills
    res_inact = client.get("/classic/bills?show_inactive=1")
    assert res_inact.status_code == 200
    html = res_inact.text

    overdue_section = html.split("<h3>⚠️ Overdue</h3>")[1].split("<h3>")[0]
    assert "Active Overdue Rent" in overdue_section
    assert "Old Deactivated ISP" not in overdue_section

    inactive_section = html.split("<h3>📦 Inactive Bills</h3>")[1].split("</div>")[0]
    assert "Old Deactivated ISP" in inactive_section

    # 3. Reactivate button works
    res_react = client.post(f"/bills/{inactive_bill.id}/toggle-active", follow_redirects=False)
    assert res_react.status_code == 303
    db_session.refresh(inactive_bill)
    assert inactive_bill.is_active is True

    app.dependency_overrides.clear()


def test_delete_bill_and_cascade(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    bill = Bill(name="Mistake Bill", next_due_date=datetime(2026, 12, 1))
    db_session.add(bill)
    db_session.commit()

    p = BillPayment(bill_id=bill.id, due_date=datetime(2026, 12, 1), paid_date=datetime(2026, 11, 28), amount_paid=50.0)
    db_session.add(p)
    db_session.commit()

    bill_id = bill.id
    payment_id = p.id

    res = client.post(f"/bills/{bill_id}/delete", follow_redirects=False)
    assert res.status_code == 303

    # Bill and all cascaded payments deleted
    assert db_session.query(Bill).filter(Bill.id == bill_id).first() is None
    assert db_session.query(BillPayment).filter(BillPayment.id == payment_id).first() is None

    app.dependency_overrides.clear()


def test_bill_history_avg_amount_ignores_none(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    bill = Bill(name="Streaming Service", next_due_date=datetime(2026, 12, 1))
    db_session.add(bill)
    db_session.commit()

    # Payment 1: $100
    p1 = BillPayment(bill_id=bill.id, due_date=datetime(2026, 10, 1), paid_date=datetime(2026, 10, 1), amount_paid=100.0)
    # Payment 2: $200
    p2 = BillPayment(bill_id=bill.id, due_date=datetime(2026, 11, 1), paid_date=datetime(2026, 11, 1), amount_paid=200.0)
    # Payment 3: amount not recorded (None)
    p3 = BillPayment(bill_id=bill.id, due_date=datetime(2026, 12, 1), paid_date=datetime(2026, 12, 1), amount_paid=None)
    db_session.add_all([p1, p2, p3])
    db_session.commit()

    res = client.get(f"/bills/{bill.id}/history")
    assert res.status_code == 200
    # Average should be (100 + 200) / 2 = $150.00, NOT 300 / 3 = $100.00
    assert "Average: <strong>$150.00</strong>" in res.text

    app.dependency_overrides.clear()


def test_pay_bill_structured_note_and_amount_default(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    bill = Bill(name="Utility Electric", next_due_date=datetime(2026, 11, 1), expected_amount=None)
    db_session.add(bill)
    db_session.commit()

    res = client.post(f"/bills/{bill.id}/pay", data={
        "amount_paid": "135.50",
        "paid_date": "2026-10-20",
        "note": "First electricity payment"
    }, follow_redirects=False)
    assert res.status_code == 303

    db_session.refresh(bill)
    assert bill.expected_amount == 135.50
    assert len(bill.payments) == 1
    payment = bill.payments[0]
    assert len(payment.notes_list) == 1
    assert payment.notes_list[0]["text"] == "First electricity payment"
    assert payment.notes_list[0]["created_at"] is not None
    assert payment.display_note == "First electricity payment"

    app.dependency_overrides.clear()


def test_zero_amount_payment_display(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    bill = Bill(name="Free Credit Month", next_due_date=datetime(2026, 11, 1), expected_amount=0.0)
    db_session.add(bill)
    db_session.commit()

    # Record $0.00 payment
    client.post(f"/bills/{bill.id}/pay", data={
        "amount_paid": "0.00",
        "paid_date": "2026-10-25"
    }, follow_redirects=False)

    # Check dashboard and history show $0.00 rather than empty or dash
    res_dash = client.get("/classic/bills")
    assert "$0.00" in res_dash.text

    res_hist = client.get(f"/bills/{bill.id}/history")
    assert "$0.00" in res_hist.text

    app.dependency_overrides.clear()


def test_is_covered_early_false_if_next_due_today_or_overdue():
    bill = Bill(
        name="Council Rates",
        next_due_date=datetime(2026, 11, 20),
        anchor_day=20,
        frequency="quarterly"
    )
    # Payment covered due date of Oct 20 (future relative to Oct 5)
    payment = BillPayment(
        due_date=datetime(2026, 10, 20),
        paid_date=datetime(2026, 10, 4),
        amount_paid=250.0
    )
    bill.payments.append(payment)

    now = datetime(2026, 10, 5)
    # With next_due_date in future (Nov 20), it is covered early
    assert bill.is_covered_early(now) is True

    # If next_due_date was edited/reset to today (Oct 5), it is due today, so NOT covered early
    bill.next_due_date = datetime(2026, 10, 5)
    assert bill.is_covered_early(now) is False

    # If next_due_date was edited/reset to a past date (Oct 1), it is overdue, so NOT covered early
    bill.next_due_date = datetime(2026, 10, 1)
    assert bill.is_covered_early(now) is False

    # If next_due_date is None, it should return False safely
    bill.next_due_date = None
    assert bill.is_covered_early(now) is False


def test_pay_bill_explicitly_cleared_amount(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    bill = Bill(name="Water Utility", next_due_date=datetime(2026, 10, 15), expected_amount=150.0)
    db_session.add(bill)
    db_session.commit()

    # User explicitly cleared the amount field in form (amount_paid="")
    res = client.post(f"/bills/{bill.id}/pay", data={
        "amount_paid": "",
        "paid_date": "2026-10-15"
    }, follow_redirects=False)
    assert res.status_code == 400

    db_session.refresh(bill)
    assert len(bill.payments) == 0
    assert bill.next_due_date == datetime(2026, 10, 15)

    # Next payment omitting amount_paid completely (e.g. quick API call) should default to bill.expected_amount
    client.post(f"/bills/{bill.id}/pay", data={
        "paid_date": "2026-11-15"
    }, follow_redirects=False)

    db_session.refresh(bill)
    latest_payment = sorted(bill.payments, key=lambda p: p.id, reverse=True)[0]
    assert latest_payment.amount_paid == 150.0

    app.dependency_overrides.clear()


def test_custom_frequency_fallback(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    # 1. Custom frequency without frequency_days falls back to monthly
    client.post("/bills/create", data={
        "name": "Custom No Days",
        "next_due_date": "2026-10-01",
        "frequency": "custom",
        "frequency_days": "",
    }, follow_redirects=False)

    bill1 = db_session.query(Bill).filter(Bill.name == "Custom No Days").first()
    assert bill1 is not None
    assert bill1.frequency == "monthly"
    assert bill1.frequency_days is None

    # 2. Custom frequency with frequency_days <= 0 falls back to monthly
    client.post("/bills/create", data={
        "name": "Custom Zero Days",
        "next_due_date": "2026-10-01",
        "frequency": "custom",
        "frequency_days": "0",
    }, follow_redirects=False)

    bill2 = db_session.query(Bill).filter(Bill.name == "Custom Zero Days").first()
    assert bill2 is not None
    assert bill2.frequency == "monthly"
    assert bill2.frequency_days is None

    # 3. Valid custom frequency is saved as custom
    client.post("/bills/create", data={
        "name": "Custom 45 Days",
        "next_due_date": "2026-10-01",
        "frequency": "custom",
        "frequency_days": "45",
    }, follow_redirects=False)

    bill3 = db_session.query(Bill).filter(Bill.name == "Custom 45 Days").first()
    assert bill3 is not None
    assert bill3.frequency == "custom"
    assert bill3.frequency_days == 45

    # 4. Updating a bill with frequency=custom and blank days falls back to monthly
    client.post(f"/bills/{bill3.id}/update", data={
        "name": "Custom 45 Days",
        "next_due_date": "2026-10-01",
        "frequency": "custom",
        "frequency_days": "",
    }, follow_redirects=False)

    db_session.refresh(bill3)
    assert bill3.frequency == "monthly"
    assert bill3.frequency_days is None

    app.dependency_overrides.clear()


def test_advance_due_date_none_fallback():
    # Calling _advance_due_date with None should not crash with AttributeError
    res = _advance_due_date(None, "monthly")
    assert isinstance(res, datetime)


def test_zero_dollar_prefill_and_timing_badges(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    # Overdue bill
    overdue_bill = Bill(name="Overdue Bill", next_due_date=datetime(2026, 1, 1), expected_amount=0.0)
    # Due today bill
    now_date = datetime.now().date()
    today_bill = Bill(name="Today Bill", next_due_date=datetime(now_date.year, now_date.month, now_date.day), expected_amount=0.0)
    # Upcoming bill
    upcoming_bill = Bill(name="Future Bill", next_due_date=datetime.now() + timedelta(days=5), expected_amount=0.0)

    db_session.add_all([overdue_bill, today_bill, upcoming_bill])
    db_session.commit()

    res = client.get("/classic/bills")
    assert res.status_code == 200

    # $0.00 should be visible and prefilled as 0.00
    assert "$0.00" in res.text
    assert 'value="0.00"' in res.text

    # Timing badges should appear in pay forms
    assert "paying" in res.text and "late" in res.text
    assert "(due today)" in res.text
    assert "paying" in res.text and "early" in res.text

    # Edit bill page should prefill 0.00 in expected_amount
    res_edit = client.get(f"/bills/{overdue_bill.id}/edit")
    assert res_edit.status_code == 200
    assert 'name="expected_amount" value="0.00"' in res_edit.text

    app.dependency_overrides.clear()


def test_covered_early_late_payment_badge_styling(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    # Next due date well in the future so it falls into covered_early
    today = datetime.now().date()
    bill = Bill(
        name="Late Covered Bill",
        next_due_date=datetime(today.year, today.month, today.day) + timedelta(days=25),
        anchor_day=today.day,
        frequency="monthly"
    )
    db_session.add(bill)
    db_session.commit()

    # Payment paid 2 days late relative to its cycle due date, but paid today (within 3 days)
    payment = BillPayment(
        bill_id=bill.id,
        due_date=datetime(today.year, today.month, today.day) - timedelta(days=2),
        paid_date=datetime(today.year, today.month, today.day),
        amount_paid=75.0
    )
    db_session.add(payment)
    db_session.commit()

    res = client.get("/classic/bills")
    assert res.status_code == 200
    # The payment badge in covered early section must be timing-late, NOT timing-early
    assert 'class="bill-timing-badge timing-late"' in res.text
    assert "2 days late" in res.text

    app.dependency_overrides.clear()


def test_next_due_override_confirms_bill(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    bill = Bill(
        name="Override Due Bill",
        next_due_date=datetime(2026, 10, 15),
        is_due_date_confirmed=False
    )
    db_session.add(bill)
    db_session.commit()

    # Pay with explicit next_due_override
    client.post(f"/bills/{bill.id}/pay", data={
        "amount_paid": "50.00",
        "paid_date": "2026-10-15",
        "next_due_override": "2026-11-20"
    }, follow_redirects=False)

    db_session.refresh(bill)
    assert bill.next_due_date == datetime(2026, 11, 20)
    assert bill.is_due_date_confirmed is True

    client.post(f"/bills/{bill.id}/confirm-invoice", data={"actual_due_date": "2026-11-20", "actual_amount": "50.00"}, follow_redirects=False)

    # Pay again without next_due_override -> auto-advanced date should have is_due_date_confirmed = False
    client.post(f"/bills/{bill.id}/pay", data={
        "amount_paid": "50.00",
        "paid_date": "2026-11-20",
        "next_due_override": ""
    }, follow_redirects=False)

    db_session.refresh(bill)
    assert bill.is_due_date_confirmed is False

    app.dependency_overrides.clear()


def test_journal_entry_zero_dollar_payment(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    bill = Bill(name="Zero Journal Bill", next_due_date=datetime(2026, 10, 15))
    db_session.add(bill)
    db_session.commit()

    client.post(f"/bills/{bill.id}/pay", data={
        "amount_paid": "0.00",
        "paid_date": "2026-10-15"
    }, follow_redirects=False)

    entry = db_session.query(JournalEntry).filter(
        JournalEntry.entity_type == "bill",
        JournalEntry.entity_id == bill.id,
        JournalEntry.action == "Paid Bill"
    ).order_by(JournalEntry.id.desc()).first()

    assert entry is not None
    assert "$0.00" in entry.details
    assert "$?" not in entry.details

    app.dependency_overrides.clear()


def test_confirm_bill_invoice_amount_and_journal(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    bill = Bill(name="Confirm Invoice Bill", next_due_date=datetime(2026, 10, 15), expected_amount=80.0)
    db_session.add(bill)
    db_session.commit()

    # Confirm with cleared amount
    res = client.post(f"/bills/{bill.id}/confirm-invoice", data={
        "actual_due_date": "2026-10-25",
        "actual_amount": ""
    }, follow_redirects=False)
    assert res.status_code == 303

    db_session.refresh(bill)
    assert bill.next_due_date == datetime(2026, 10, 25)
    assert bill.expected_amount is None
    assert bill.is_due_date_confirmed is True

    # Invalid date should not create a confirmed journal entry or alter date
    entry_count_before = db_session.query(JournalEntry).filter(
        JournalEntry.entity_type == "bill",
        JournalEntry.entity_id == bill.id
    ).count()

    client.post(f"/bills/{bill.id}/confirm-invoice", data={
        "actual_due_date": "invalid-date",
        "actual_amount": "90.00"
    }, follow_redirects=False)

    db_session.refresh(bill)
    assert bill.next_due_date == datetime(2026, 10, 25)
    entry_count_after = db_session.query(JournalEntry).filter(
        JournalEntry.entity_type == "bill",
        JournalEntry.entity_id == bill.id
    ).count()
    assert entry_count_after == entry_count_before

    app.dependency_overrides.clear()


def test_edit_bill_clearing_anchor_day_reanchors(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    bill = Bill(name="Reanchor Bill", next_due_date=datetime(2026, 10, 15), anchor_day=15)
    db_session.add(bill)
    db_session.commit()

    # User changes due date to 22nd and clears anchor_day
    client.post(f"/bills/{bill.id}/update", data={
        "name": "Reanchor Bill",
        "next_due_date": "2026-10-22",
        "frequency": "monthly",
        "anchor_day": ""
    }, follow_redirects=False)

    db_session.refresh(bill)
    assert bill.anchor_day == 22

    app.dependency_overrides.clear()


def test_empty_name_bill_rejected(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    # Creating a bill with empty or whitespace name should redirect and not create
    res = client.post("/bills/create", data={
        "name": "   ",
        "next_due_date": "2026-10-15"
    }, follow_redirects=False)
    assert res.status_code == 303

    assert db_session.query(Bill).filter(Bill.name == "   ").first() is None
    assert db_session.query(Bill).filter(Bill.name == "").first() is None

    # Updating a bill with empty name preserves previous name
    bill = Bill(name="Valid Bill", next_due_date=datetime(2026, 10, 15))
    db_session.add(bill)
    db_session.commit()

    client.post(f"/bills/{bill.id}/update", data={
        "name": "   ",
        "next_due_date": "2026-10-16"
    }, follow_redirects=False)

    db_session.refresh(bill)
    assert bill.name == "Valid Bill"
    assert bill.next_due_date == datetime(2026, 10, 16)

    app.dependency_overrides.clear()


def test_payment_notes_list_legacy_string_list_safe():
    p = BillPayment(bill_id=1, due_date=datetime(2026, 10, 1), paid_date=datetime(2026, 10, 1))
    p.note = '["legacy note 1", "legacy note 2"]'

    notes = p.notes_list
    assert len(notes) == 2
    assert notes[0]["text"] == "legacy note 1"
    assert notes[1]["text"] == "legacy note 2"
    assert p.display_note == "legacy note 1, legacy note 2"


def test_toggle_bill_active_preserves_show_inactive(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    bill = Bill(name="Inactive Toggle Bill", next_due_date=datetime(2026, 10, 15), is_active=False)
    db_session.add(bill)
    db_session.commit()

    res = client.post(f"/bills/{bill.id}/toggle-active?show_inactive=1", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/classic/bills?show_inactive=1"

    db_session.refresh(bill)
    assert bill.is_active is True

    res2 = client.post(f"/bills/{bill.id}/toggle-active", follow_redirects=False)
    assert res2.status_code == 303
    assert res2.headers["location"] == "/classic/bills"

    app.dependency_overrides.clear()


def test_bill_note_visible_in_dashboard_and_history(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    bill = Bill(
        name="Account Bill",
        next_due_date=datetime.now() + timedelta(days=3),
        note="Account #987654321 ref"
    )
    db_session.add(bill)
    db_session.commit()

    # Dashboard shows tooltip
    res_dash = client.get("/classic/bills")
    assert res_dash.status_code == 200
    assert 'title="Account #987654321 ref"' in res_dash.text
    assert "📝" in res_dash.text

    # History shows note
    res_hist = client.get(f"/bills/{bill.id}/history")
    assert res_hist.status_code == 200
    assert "Note: Account #987654321 ref" in res_hist.text

    app.dependency_overrides.clear()


def test_date_type_safety_in_models():
    # BillPayment.days_difference with pure date objects
    p = BillPayment(
        bill_id=1,
        due_date=date(2026, 10, 15),
        paid_date=date(2026, 10, 10)
    )
    assert p.days_difference == 5
    assert p.timing_label == "5 days early"

    # Bill.last_payment with pure date objects
    bill = Bill(name="Pure Date Bill", next_due_date=date(2026, 11, 15))
    p2 = BillPayment(
        bill_id=bill.id,
        due_date=date(2026, 10, 15),
        paid_date=date(2026, 10, 10)
    )
    bill.payments.append(p2)
    assert bill.last_payment == p2

    # is_covered_early with pure date objects
    now_dt = datetime(2026, 10, 12)
    assert bill.is_covered_early(now_dt) is True


def test_add_payment_note_empty_redirects_to_history(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    bill = Bill(name="Note Redirect Bill", next_due_date=datetime(2026, 10, 15))
    db_session.add(bill)
    db_session.commit()

    payment = BillPayment(
        bill_id=bill.id,
        due_date=datetime(2026, 10, 15),
        paid_date=datetime(2026, 10, 15)
    )
    db_session.add(payment)
    db_session.commit()

    # Submitting empty note redirects to payment history, NOT /bills
    res = client.post(f"/bills/payments/{payment.id}/add-note", data={
        "note_text": "   "
    }, follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == f"/bills/{bill.id}/history"

    app.dependency_overrides.clear()


def test_midnight_and_as_utc_aware_date_type_resilience():
    # 1. _midnight with pure date object returns midnight datetime
    d = date(2026, 10, 15)
    res_m = _midnight(d)
    assert isinstance(res_m, datetime)
    assert res_m == datetime(2026, 10, 15, 0, 0, 0)

    # 2. _advance_due_date with pure date object
    res_adv = _advance_due_date(d, "monthly")
    assert isinstance(res_adv, datetime)
    assert res_adv == datetime(2026, 11, 15, 0, 0, 0)

    # 3. _as_utc_aware with pure date object returns UTC aware datetime
    res_utc = _as_utc_aware(d)
    assert isinstance(res_utc, datetime)
    assert res_utc.tzinfo == timezone.utc
    assert res_utc.year == 2026 and res_utc.month == 10 and res_utc.day == 15


def test_update_bill_invalid_anchor_day_fallback(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    bill = Bill(name="Fallback Anchor Bill", next_due_date=datetime(2026, 10, 18), anchor_day=18)
    db_session.add(bill)
    db_session.commit()

    # User submits invalid anchor day (99) -> falls back to new_due.day (24)
    client.post(f"/bills/{bill.id}/update", data={
        "name": "Fallback Anchor Bill",
        "next_due_date": "2026-10-24",
        "frequency": "monthly",
        "anchor_day": "99"
    }, follow_redirects=False)

    db_session.refresh(bill)
    assert bill.anchor_day == 24

    # User submits anchor day 0 -> falls back to new_due.day (25)
    client.post(f"/bills/{bill.id}/update", data={
        "name": "Fallback Anchor Bill",
        "next_due_date": "2026-10-25",
        "frequency": "monthly",
        "anchor_day": "0"
    }, follow_redirects=False)

    db_session.refresh(bill)
    assert bill.anchor_day == 25

    app.dependency_overrides.clear()


def test_toggle_late_override_creates_journal_entry(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    bill = Bill(name="Journal Late Bill", next_due_date=datetime(2026, 10, 15))
    db_session.add(bill)
    db_session.commit()

    payment = BillPayment(
        bill_id=bill.id,
        due_date=datetime(2026, 10, 15),
        paid_date=datetime(2026, 10, 18),
        amount_paid=50.0
    )
    db_session.add(payment)
    db_session.commit()

    # Toggle late override
    client.post(f"/bills/payments/{payment.id}/toggle-late-override", follow_redirects=False)

    entry = db_session.query(JournalEntry).filter(
        JournalEntry.entity_type == "bill",
        JournalEntry.entity_id == bill.id,
        JournalEntry.action == "Marked Bill Payment On Time"
    ).first()
    assert entry is not None
    assert f"payment #{payment.id}" in entry.details

    # Toggle back
    client.post(f"/bills/payments/{payment.id}/toggle-late-override", follow_redirects=False)

    entry_undo = db_session.query(JournalEntry).filter(
        JournalEntry.entity_type == "bill",
        JournalEntry.entity_id == bill.id,
        JournalEntry.action == "Removed Bill Payment On Time Override"
    ).first()
    assert entry_undo is not None

    app.dependency_overrides.clear()


def test_add_payment_note_creates_journal_entry(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    bill = Bill(name="Journal Note Bill", next_due_date=datetime(2026, 10, 15))
    db_session.add(bill)
    db_session.commit()

    payment = BillPayment(
        bill_id=bill.id,
        due_date=datetime(2026, 10, 15),
        paid_date=datetime(2026, 10, 15)
    )
    db_session.add(payment)
    db_session.commit()

    client.post(f"/bills/payments/{payment.id}/add-note", data={
        "note_text": "Confirmation #XYZ987"
    }, follow_redirects=False)

    entry = db_session.query(JournalEntry).filter(
        JournalEntry.entity_type == "bill",
        JournalEntry.entity_id == bill.id,
        JournalEntry.action == "Added Bill Payment Note"
    ).first()
    assert entry is not None
    assert f"payment #{payment.id}" in entry.details

    app.dependency_overrides.clear()


def test_edit_bill_payment_note_json_safe_and_non_negative(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    bill = Bill(name="Edit Payment Bill", next_due_date=datetime(2026, 10, 15))
    db_session.add(bill)
    db_session.commit()

    payment = BillPayment(
        bill_id=bill.id,
        due_date=datetime(2026, 10, 15),
        paid_date=datetime(2026, 10, 15),
        amount_paid=45.0
    )
    db_session.add(payment)
    db_session.commit()

    # Reject the entire invalid correction before dates, notes or cash change.
    response = client.post(f"/bills/payments/{payment.id}/edit", data={
        "due_date": "2026-10-15",
        "paid_date": "2026-10-16",
        "amount_paid": "-10.00",
        "note": "Edited payment note text"
    }, follow_redirects=False)
    assert response.status_code == 400
    db_session.refresh(payment)
    assert payment.amount_paid == 45.0
    assert payment.paid_date == datetime(2026, 10, 15) and payment.note is None
    response = client.post(f"/bills/payments/{payment.id}/edit", data={
        'paid_date': '2026-10-16', 'amount_paid': '45.00', 'note': 'Edited payment note text'
    }, follow_redirects=False)
    assert response.status_code == 303
    db_session.refresh(payment)
    # Note must be structured JSON, not raw string
    assert len(payment.notes_list) == 1
    assert payment.notes_list[0]["text"] == "Edited payment note text"
    assert payment.display_note == "Edited payment note text"

    app.dependency_overrides.clear()


def test_covered_early_confirm_invoice(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    today = datetime.now().date()
    bill = Bill(
        name="Early Confirm Bill",
        next_due_date=datetime(today.year, today.month, today.day) + timedelta(days=20),
        anchor_day=today.day,
        frequency="monthly",
        expected_amount=100.0,
        category="Utilities",
        is_auto_pay=True
    )
    db_session.add(bill)
    db_session.commit()

    payment = BillPayment(
        bill_id=bill.id,
        due_date=datetime(today.year, today.month, today.day) - timedelta(days=1),
        paid_date=datetime(today.year, today.month, today.day),
        amount_paid=100.0
    )
    db_session.add(payment)
    db_session.commit()

    # Dashboard should show Confirm Bill button and form in covered_early section
    res = client.get("/classic/bills")
    assert res.status_code == 200
    assert f'id="confirm-form-{bill.id}"' in res.text
    assert "Confirm Bill" in res.text

    # User confirms the next cycle invoice
    res_conf = client.post(f"/bills/{bill.id}/confirm-invoice", data={
        "actual_due_date": "2026-11-25",
        "actual_amount": "115.50"
    }, follow_redirects=False)
    assert res_conf.status_code == 303

    db_session.refresh(bill)
    assert bill.next_due_date == datetime(2026, 11, 25)
    assert bill.expected_amount == 115.50
    assert bill.is_due_date_confirmed is True

    app.dependency_overrides.clear()


def test_invalid_frequency_fallback(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    # 1. create_bill with unknown frequency falls back to monthly
    client.post("/bills/create", data={
        "name": "Invalid Freq Bill",
        "next_due_date": "2026-10-15",
        "frequency": "bogus_frequency",
    }, follow_redirects=False)

    bill = db_session.query(Bill).filter(Bill.name == "Invalid Freq Bill").first()
    assert bill is not None
    assert bill.frequency == "monthly"

    # 2. update_bill with unknown frequency falls back to monthly
    client.post(f"/bills/{bill.id}/update", data={
        "name": "Invalid Freq Bill",
        "next_due_date": "2026-10-15",
        "frequency": "not_a_real_freq",
    }, follow_redirects=False)

    db_session.refresh(bill)
    assert bill.frequency == "monthly"

    app.dependency_overrides.clear()


def test_bill_history_badges_rendered(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    bill = Bill(
        name="Badge Check Bill",
        next_due_date=datetime(2026, 10, 15),
        is_due_date_confirmed=True,
        is_auto_pay=True,
        category="Streaming"
    )
    db_session.add(bill)
    db_session.commit()

    res = client.get(f"/bills/{bill.id}/history")
    assert res.status_code == 200
    assert "Invoice confirmed" in res.text
    assert "auto" in res.text
    assert "Streaming" in res.text

    app.dependency_overrides.clear()


def test_recent_payments_history_link(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    today = datetime.now().date()
    bill = Bill(name="Recent Link Bill", next_due_date=datetime(today.year, today.month, today.day) + timedelta(days=20))
    db_session.add(bill)
    db_session.commit()

    payment = BillPayment(
        bill_id=bill.id,
        due_date=datetime(today.year, today.month, today.day),
        paid_date=datetime(today.year, today.month, today.day),
        amount_paid=30.0
    )
    db_session.add(payment)
    db_session.commit()

    res = client.get("/classic/bills")
    assert res.status_code == 200
    assert f'href="/bills/{bill.id}/history"' in res.text

    app.dependency_overrides.clear()


def test_relationship_order_by_tiebreaker(db_session):
    bill = Bill(name="Tiebreaker Bill", next_due_date=datetime(2026, 10, 15))
    db_session.add(bill)
    db_session.commit()

    p1 = BillPayment(bill_id=bill.id, due_date=datetime(2026, 10, 1), paid_date=datetime(2026, 10, 1))
    db_session.add(p1)
    db_session.commit()

    p2 = BillPayment(bill_id=bill.id, due_date=datetime(2026, 10, 1), paid_date=datetime(2026, 10, 1))
    db_session.add(p2)
    db_session.commit()

    db_session.expire_all()
    bill_refreshed = db_session.query(Bill).filter(Bill.id == bill.id).first()
    # payments ordered by due_date desc, id desc -> p2 (higher id) is first
    assert bill_refreshed.payments[0].id == p2.id
    assert bill_refreshed.payments[1].id == p1.id


def test_to_date_and_days_until_due():
    # 1. _to_date handling
    assert _to_date(datetime(2026, 9, 24, 15, 30)) == date(2026, 9, 24)
    assert _to_date(date(2026, 9, 24)) == date(2026, 9, 24)
    assert _to_date("2026-09-24T00:00:00") == date(2026, 9, 24)
    assert _to_date("2026-09-24") == date(2026, 9, 24)
    assert _to_date(None) is None
    assert _to_date("invalid-date") is None
    assert _to_date("") is None

    # 2. Bill.days_until_due handling
    bill = Bill(name="Due Test", next_due_date=date(2026, 9, 24))
    # Comparison with datetime
    assert bill.days_until_due(datetime(2026, 9, 20, 10, 0)) == 4
    # Comparison with date
    assert bill.days_until_due(date(2026, 9, 24)) == 0
    assert bill.days_until_due(date(2026, 9, 26)) == -2
    # None next_due_date
    bill_no_due = Bill(name="No Due")
    assert bill_no_due.days_until_due() is None


def test_date_iso_properties():
    # Bill next_due_date_iso
    b1 = Bill(name="ISO Bill", next_due_date=datetime(2026, 9, 24, 10, 30))
    assert b1.next_due_date_iso == "2026-09-24"
    b2 = Bill(name="ISO Bill Date", next_due_date=date(2026, 9, 24))
    assert b2.next_due_date_iso == "2026-09-24"
    b3 = Bill(name="ISO Bill None", next_due_date=None)
    assert b3.next_due_date_iso == ""

    # BillPayment due_date_iso and paid_date_iso
    p1 = BillPayment(due_date=datetime(2026, 9, 24, 8, 0), paid_date=date(2026, 9, 23))
    assert p1.due_date_iso == "2026-09-24"
    assert p1.paid_date_iso == "2026-09-23"
    p2 = BillPayment(due_date=None, paid_date=None)
    assert p2.due_date_iso == ""
    assert p2.paid_date_iso == ""


def test_unscheduled_active_bills_dashboard(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    # Create an active bill with no due date
    bill = Bill(name="Unscheduled Phone Bill", next_due_date=None, is_active=True, expected_amount=80.0)
    db_session.add(bill)
    db_session.commit()

    res = client.get("/classic/bills")
    assert res.status_code == 200
    assert "Unscheduled Active Bills" in res.text
    assert "Unscheduled Phone Bill" in res.text
    assert "No due date set" in res.text
    assert f'id="confirm-form-{bill.id}"' in res.text
    assert "Set Due Date" in res.text
    # Should NOT display "No bills configured"
    assert "No bills configured" not in res.text

    app.dependency_overrides.clear()


def test_export_and_import_bills(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    bill = Bill(
        name="Exported Electric Bill",
        payee="PowerCorp",
        expected_amount=145.50,
        next_due_date=datetime(2026, 10, 20),
        frequency="monthly",
        anchor_day=20,
        is_due_date_confirmed=True,
        is_auto_pay=True,
        category="Utilities",
        color="#22c55e",
        note="Summer rate"
    )
    db_session.add(bill)
    db_session.commit()

    payment = BillPayment(
        bill_id=bill.id,
        due_date=datetime(2026, 9, 20),
        paid_date=datetime(2026, 9, 19),
        amount_paid=140.00,
        note='[{"text": "Ref #12345", "created_at": "2026-09-19 10:00"}]',
        is_late_overridden=False
    )
    db_session.add(payment)
    db_session.commit()

    # 1. Test /api/export includes bills and payments
    res = client.get("/api/export")
    assert res.status_code == 200
    export_json = res.json()
    assert "bills" in export_json["data"]
    assert "bill_payments" in export_json["data"]
    assert any(b["name"] == "Exported Electric Bill" for b in export_json["data"]["bills"])
    assert any(p["bill_id"] == bill.id for p in export_json["data"]["bill_payments"])

    # 2. Test import_data_from_json upsert
    import_payload = {
        "version": 2,
        "data": {
            "bills": [
                {
                    "id": 9999,
                    "name": "Imported Water Bill",
                    "payee": "AquaNet",
                    "expected_amount": 62.00,
                    "next_due_date": "2026-11-05T00:00:00",
                    "frequency": "quarterly",
                    "anchor_day": 5,
                    "is_due_date_confirmed": False,
                    "is_auto_pay": False,
                    "is_active": True,
                    "category": "Utilities",
                    "color": "#3b82f6",
                    "note": "Quarterly water meter",
                }
            ],
            "bill_payments": [
                {
                    "id": 8888,
                    "bill_id": 9999,
                    "due_date": "2026-08-05T00:00:00",
                    "paid_date": "2026-08-05T00:00:00",
                    "amount_paid": 60.00,
                    "note": '[{"text": "Auto debit", "created_at": null}]',
                    "is_late_overridden": False,
                }
            ]
        }
    }
    stats = import_data_from_json(db_session, import_payload)
    assert stats["errors"] == 0
    assert stats["created"] >= 2

    imported_bill = db_session.query(Bill).filter(Bill.id == 9999).first()
    assert imported_bill is not None
    assert imported_bill.name == "Imported Water Bill"
    assert imported_bill.frequency == "quarterly"

    imported_payment = db_session.query(BillPayment).filter(BillPayment.id == 8888).first()
    assert imported_payment is not None
    assert imported_payment.bill_id == 9999
    assert imported_payment.amount_paid == 60.00

    app.dependency_overrides.clear()


def test_journal_entry_bill_link(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    bill = Bill(name="Journal Link Bill", next_due_date=datetime(2026, 10, 15))
    db_session.add(bill)
    db_session.commit()

    entry_create = JournalEntry(
        action="Created Bill",
        details="Created bill 'Journal Link Bill'",
        entity_type="bill",
        entity_id=bill.id
    )
    entry_delete = JournalEntry(
        action="Deleted Bill",
        details="Deleted bill 'Old Bill'",
        entity_type="bill",
        entity_id=1234
    )
    db_session.add_all([entry_create, entry_delete])
    db_session.commit()

    res = client.get("/journal")
    assert res.status_code == 200
    assert f'href="/bills/{bill.id}/history">View Bill</a>' in res.text
    # Deleted action should not link
    assert f'href="/bills/1234/history"' not in res.text
    assert '<span class="dates">Deleted</span>' in res.text

    app.dependency_overrides.clear()


def test_confirm_invoice_negative_amount_rejected(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    bill = Bill(name="Negative Amount Bill", next_due_date=datetime(2026, 10, 15), expected_amount=120.00)
    db_session.add(bill)
    db_session.commit()

    # Confirm invoice with negative amount
    res = client.post(f"/bills/{bill.id}/confirm-invoice", data={
        "actual_due_date": "2026-10-18",
        "actual_amount": "-25.00"
    }, follow_redirects=False)
    assert res.status_code == 400

    db_session.refresh(bill)
    assert bill.next_due_date == datetime(2026, 10, 15)
    # Existing expected amount must NOT be wiped
    assert bill.expected_amount == 120.00

    app.dependency_overrides.clear()


def test_edit_payment_clear_note_flag(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    bill = Bill(name="Clear Note Bill", next_due_date=datetime(2026, 10, 15))
    db_session.add(bill)
    db_session.commit()

    payment = BillPayment(
        bill_id=bill.id,
        due_date=datetime(2026, 10, 15),
        paid_date=datetime(2026, 10, 15),
        amount_paid=50.0,
        note='[{"text": "To be cleared", "created_at": "2026-10-15 10:00"}]'
    )
    db_session.add(payment)
    db_session.commit()

    # Post edit with clear_note=True
    res = client.post(f"/bills/payments/{payment.id}/edit", data={
        "due_date": "2026-10-15",
        "paid_date": "2026-10-15",
        "amount_paid": "50.0",
        "clear_note": "true"
    }, follow_redirects=False)
    assert res.status_code == 303

    db_session.refresh(payment)
    assert payment.note is None
    assert payment.notes_list == []
    assert payment.display_note == ""

    app.dependency_overrides.clear()


def test_bill_history_unrecorded_amounts_display(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    bill = Bill(name="Unrecorded Amounts Bill", next_due_date=datetime(2026, 10, 15))
    db_session.add(bill)
    db_session.commit()

    # Payments with no amounts recorded (amount_paid=None)
    p1 = BillPayment(bill_id=bill.id, due_date=datetime(2026, 9, 15), paid_date=datetime(2026, 9, 15), amount_paid=None)
    p2 = BillPayment(bill_id=bill.id, due_date=datetime(2026, 8, 15), paid_date=datetime(2026, 8, 15), amount_paid=None)
    db_session.add_all([p1, p2])
    db_session.commit()

    res = client.get(f"/bills/{bill.id}/history")
    assert res.status_code == 200
    # Should display — rather than $0.00 for total and average
    assert "Total paid: <strong>—</strong>" in res.text
    assert "Average: <strong>—</strong>" in res.text

    app.dependency_overrides.clear()


def test_midnight_string_and_tzinfo_stripping():
    # 1. ISO string to midnight datetime
    d_str = _midnight("2026-09-24T15:45:00")
    assert d_str == datetime(2026, 9, 24, 0, 0, 0)
    assert d_str.tzinfo is None

    # 2. Timezone-aware datetime stripped to naive UTC
    d_aware = datetime(2026, 9, 24, 15, 30, tzinfo=timezone.utc)
    d_naive = _midnight(d_aware)
    assert d_naive == datetime(2026, 9, 24, 0, 0, 0)
    assert d_naive.tzinfo is None


def test_parse_date_or_datetime_polymorphic():
    # 1. string ISO
    assert _parse_date_or_datetime("2026-09-24") == datetime(2026, 9, 24)
    # 2. datetime object
    dt = datetime(2026, 9, 24, 18, 20)
    assert _parse_date_or_datetime(dt) == datetime(2026, 9, 24)
    # 3. date object
    d = date(2026, 9, 24)
    assert _parse_date_or_datetime(d) == datetime(2026, 9, 24)
    # 4. invalid/empty raises ValueError
    with pytest.raises(ValueError):
        _parse_date_or_datetime("")
    with pytest.raises(ValueError):
        _parse_date_or_datetime(None)


def test_edit_bill_payment_unchanged_note_preserves_timestamp_and_structure(db_session):
    import json
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    bill = Bill(name="Internet", next_due_date=datetime(2026, 11, 1), frequency="monthly")
    db_session.add(bill)
    db_session.commit()

    # Two distinct notes with different original timestamps
    initial_note = json.dumps([
        {"text": "Called support", "created_at": "2026-10-15 10:30"},
        {"text": "Discount applied", "created_at": "2026-10-16 11:00"}
    ])
    payment = BillPayment(
        bill_id=bill.id,
        due_date=datetime(2026, 11, 1),
        paid_date=datetime(2026, 11, 1),
        amount_paid=79.0,
        note=initial_note
    )
    db_session.add(payment)
    db_session.commit()

    # Form submits with p.display_note pre-filled, user only changes amount_paid
    res = client.post(f"/bills/payments/{payment.id}/edit", data={
        "due_date": "2026-11-01",
        "paid_date": "2026-11-01",
        "amount_paid": "75.00",
        "note": payment.display_note,
    }, follow_redirects=False)
    assert res.status_code == 303

    db_session.refresh(payment)
    assert payment.amount_paid == 75.0
    # Original multi-note structure and timestamps MUST be preserved
    assert len(payment.notes_list) == 2
    assert payment.notes_list[0]["text"] == "Called support"
    assert payment.notes_list[0]["created_at"] == "2026-10-15 10:30"
    assert payment.notes_list[1]["text"] == "Discount applied"
    assert payment.notes_list[1]["created_at"] == "2026-10-16 11:00"

    app.dependency_overrides.clear()


def test_serialize_bill_includes_currency():
    from serializers import serialize_bill
    b = Bill(id=1, name="Netflix US", expected_amount=15.99, currency="USD", next_due_date=datetime(2026, 12, 1))
    data = serialize_bill(b)
    assert data["currency"] == "USD"
    assert data["name"] == "Netflix US"

    b_default = Bill(id=2, name="Netflix AU", expected_amount=18.99, next_due_date=datetime(2026, 12, 1))
    data_default = serialize_bill(b_default)
    assert data_default["currency"] == "AUD"


def test_create_unscheduled_bill(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    # Post with empty next_due_date
    res = client.post("/bills/create", data={
        "name": "Gym Annual Pass",
        "next_due_date": "",
        "expected_amount": "500.0",
        "frequency": "yearly",
    }, follow_redirects=False)
    assert res.status_code == 303

    bill = db_session.query(Bill).filter(Bill.name == "Gym Annual Pass").first()
    assert bill is not None
    assert bill.next_due_date is None
    assert bill.is_due_date_confirmed is False
    assert bill.is_active is True

    # Check dashboard renders it under Unscheduled Active Bills
    dash_res = client.get("/classic/bills")
    assert dash_res.status_code == 200
    assert "Unscheduled Active Bills" in dash_res.text
    assert "Gym Annual Pass" in dash_res.text

    app.dependency_overrides.clear()


def test_update_bill_clear_due_date_unschedules(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    bill = Bill(name="Water Utility", next_due_date=datetime(2026, 8, 20), is_due_date_confirmed=True)
    db_session.add(bill)
    db_session.commit()

    # Clear due date via update
    res = client.post(f"/bills/{bill.id}/update", data={
        "name": "Water Utility",
        "next_due_date": "",
        "expected_amount": "90",
        "frequency": "monthly",
    }, follow_redirects=False)
    assert res.status_code == 303

    db_session.refresh(bill)
    assert bill.next_due_date is None
    assert bill.is_due_date_confirmed is False

    app.dependency_overrides.clear()


def test_covered_early_confirmed_badge_rendered(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    # Bill due next month, paid early, and confirmed
    bill = Bill(
        name="Council Rates",
        next_due_date=datetime(2026, 11, 15),
        is_due_date_confirmed=True,
        frequency="quarterly",
    )
    db_session.add(bill)
    db_session.commit()

    p = BillPayment(
        bill_id=bill.id,
        due_date=datetime(2026, 11, 15),
        paid_date=datetime.now(),
        amount_paid=400.0,
    )
    db_session.add(p)
    db_session.commit()

    res = client.get("/classic/bills")
    assert res.status_code == 200
    assert "Council Rates" in res.text
    # Should show confirmed badge rather than plain "Next estimated due"
    assert "Invoice confirmed" in res.text

    app.dependency_overrides.clear()


def test_bill_history_inactive_badge_and_color(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    bill = Bill(
        name="Cancelled Subscription",
        next_due_date=datetime(2026, 5, 1),
        is_active=False,
        color="#8b5cf6"
    )
    db_session.add(bill)
    db_session.commit()

    res = client.get(f"/bills/{bill.id}/history")
    assert res.status_code == 200
    assert "Inactive" in res.text
    assert 'color: #8b5cf6' in res.text

    app.dependency_overrides.clear()


def test_import_scrubs_relationships(db_session):
    from import_logic import import_data_from_json

    payload = {
        "version": 2,
        "data": {
            "bills": [{
                "id": 9999,
                "name": "Import Test Scrub",
                "next_due_date": "2026-10-01T00:00:00",
                "frequency": "monthly",
                "payments": [{"fake": "data"}],
                "days_until_due": 10,
            }],
            "bill_payments": [{
                "id": 99991,
                "bill_id": 9999,
                "due_date": "2026-10-01T00:00:00",
                "paid_date": "2026-09-30T00:00:00",
                "amount_paid": 50.0,
                "bill": {"fake": "relationship"},
            }]
        }
    }

    stats = import_data_from_json(db_session, payload)
    assert stats["errors"] == 0
    bill = db_session.get(Bill, 9999)
    assert bill is not None
    assert bill.name == "Import Test Scrub"
    payment = db_session.get(BillPayment, 99991)
    assert payment is not None
    assert payment.bill_id == 9999


def test_update_bill_negative_amount_preserves_expected_amount(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    bill = Bill(name="Negative Amount Test", next_due_date=datetime(2026, 10, 15), expected_amount=150.0)
    db_session.add(bill)
    db_session.commit()

    # Attempt to update with a negative amount
    client.post(f"/bills/{bill.id}/update", data={
        "name": "Negative Amount Test",
        "next_due_date": "2026-10-15",
        "expected_amount": "-45.00"
    }, follow_redirects=False)

    db_session.refresh(bill)
    assert bill.expected_amount == 150.0

    # Updating with empty string properly clears it
    client.post(f"/bills/{bill.id}/update", data={
        "name": "Negative Amount Test",
        "next_due_date": "2026-10-15",
        "expected_amount": ""
    }, follow_redirects=False)

    db_session.refresh(bill)
    assert bill.expected_amount is None

    app.dependency_overrides.clear()


def test_color_black_normalized_to_none(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    # Create bill with #000000
    res = client.post("/bills/create", data={
        "name": "Color Black Test",
        "next_due_date": "2026-10-15",
        "color": "#000000"
    }, follow_redirects=False)
    assert res.status_code == 303

    bill = db_session.query(Bill).filter(Bill.name == "Color Black Test").first()
    assert bill is not None
    assert bill.color is None

    # Update bill with non-black color
    client.post(f"/bills/{bill.id}/update", data={
        "name": "Color Black Test",
        "next_due_date": "2026-10-15",
        "color": "#3b82f6"
    }, follow_redirects=False)

    db_session.refresh(bill)
    assert bill.color == "#3b82f6"

    # Update bill back to #000000
    client.post(f"/bills/{bill.id}/update", data={
        "name": "Color Black Test",
        "next_due_date": "2026-10-15",
        "color": "#000000"
    }, follow_redirects=False)

    db_session.refresh(bill)
    assert bill.color is None

    app.dependency_overrides.clear()


def test_delete_payment_restore_due_date_sets_anchor_day(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    bill = Bill(name="Restore Anchor Day Bill", next_due_date=datetime(2026, 11, 1), anchor_day=None)
    db_session.add(bill)
    db_session.commit()

    payment = BillPayment(
        bill_id=bill.id,
        due_date=datetime(2026, 10, 18),
        paid_date=datetime(2026, 10, 18),
        amount_paid=75.0,
    )
    db_session.add(payment)
    db_session.commit()

    client.post(f"/bills/payments/{payment.id}/delete", data={
        "restore_due_date": "true"
    }, follow_redirects=False)

    db_session.refresh(bill)
    assert bill.next_due_date == datetime(2026, 10, 18)
    assert bill.anchor_day == 18

    app.dependency_overrides.clear()


def test_empty_state_with_inactive_bills_prompt(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    # Ensure no active bills
    db_session.query(BillPayment).delete()
    db_session.query(Bill).delete()
    db_session.commit()

    inactive_bill = Bill(
        name="Only Inactive Bill",
        is_active=False,
        next_due_date=None,
    )
    db_session.add(inactive_bill)
    db_session.commit()

    # When show_inactive=0, should display inactive count prompt
    res = client.get("/classic/bills")
    assert res.status_code == 200
    assert "No active bills (1 inactive)" in res.text
    assert "show inactive bills" in res.text

    # When show_inactive=1, displays inactive bills and renders 'No due date'
    res_inactive = client.get("/classic/bills?show_inactive=1")
    assert res_inactive.status_code == 200
    assert "No due date" in res_inactive.text
    assert "last due None" not in res_inactive.text

    # Edit bill page displays bill name in title and header
    res_edit = client.get(f"/bills/{inactive_bill.id}/edit")
    assert res_edit.status_code == 200
    assert "Edit Bill — Only Inactive Bill" in res_edit.text

    app.dependency_overrides.clear()


def test_monthly_total_ignores_negative_expected_amount(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    db_session.query(BillPayment).delete()
    db_session.query(Bill).delete()
    db_session.commit()

    # Normal bill + corrupted negative amount bill
    b1 = Bill(name="Good Bill", next_due_date=datetime(2026, 10, 15), expected_amount=100.0, frequency="monthly")
    b2 = Bill(name="Negative Bill", next_due_date=datetime(2026, 10, 20), expected_amount=-50.0, frequency="monthly")
    db_session.add_all([b1, b2])
    db_session.commit()

    res = client.get("/classic/bills")
    assert res.status_code == 200
    # Monthly total should only count b1 ($100.00), not $50.00
    assert "$100.00" in res.text

    app.dependency_overrides.clear()


def test_bill_estimated_amount_wma_progression(db_session):
    bill = Bill(name="Power", expected_amount=100.0, amount_strategy="rolling_avg")
    db_session.add(bill)
    db_session.commit()

    # 0 payments -> fallback to expected_amount
    assert bill.estimated_amount == 100.0
    assert bill.effective_amount == 100.0
    assert not bill.is_estimated

    # 1 payment: 120.0
    p1 = BillPayment(bill_id=bill.id, due_date=datetime(2026, 1, 1), paid_date=datetime(2026, 1, 1), amount_paid=120.0)
    db_session.add(p1)
    db_session.commit()
    db_session.refresh(bill)
    assert bill.estimated_amount == 120.0
    assert bill.is_estimated

    # 2 payments: p1=120 (older), p2=150 (newer)
    p2 = BillPayment(bill_id=bill.id, due_date=datetime(2026, 2, 1), paid_date=datetime(2026, 2, 1), amount_paid=150.0)
    db_session.add(p2)
    db_session.commit()
    db_session.refresh(bill)
    # WMA(150, 120) with weights (2, 1) -> (2*150 + 1*120)/3 = 420/3 = 140.0
    assert bill.estimated_amount == 140.0

    # 3 payments: p3=180 (newest), p2=150, p1=120
    p3 = BillPayment(bill_id=bill.id, due_date=datetime(2026, 3, 1), paid_date=datetime(2026, 3, 1), amount_paid=180.0)
    db_session.add(p3)
    db_session.commit()
    db_session.refresh(bill)
    # WMA(180, 150, 120) with weights (3, 2, 1) -> (3*180 + 2*150 + 1*120)/6 = (540+300+120)/6 = 960/6 = 160.0
    assert bill.estimated_amount == 160.0

    # 4 payments: p4=200 (newest), p3=180, p2=150, p1=120
    p4 = BillPayment(bill_id=bill.id, due_date=datetime(2026, 4, 1), paid_date=datetime(2026, 4, 1), amount_paid=200.0)
    db_session.add(p4)
    db_session.commit()
    db_session.refresh(bill)
    # WMA(200, 180, 150, 120) with weights (4, 3, 2, 1) -> (4*200 + 3*180 + 2*150 + 1*120)/10 = (800+540+300+120)/10 = 176.0
    assert bill.estimated_amount == 176.0


def test_bill_estimated_amount_outlier_filtering(db_session):
    bill = Bill(name="Water", expected_amount=100.0, amount_strategy="rolling_avg")
    db_session.add(bill)
    db_session.commit()

    # 4 payments: 3 normal (~100) and 1 extreme catch-up outlier (1000)
    p1 = BillPayment(bill_id=bill.id, due_date=datetime(2026, 1, 1), paid_date=datetime(2026, 1, 1), amount_paid=95.0)
    p2 = BillPayment(bill_id=bill.id, due_date=datetime(2026, 2, 1), paid_date=datetime(2026, 2, 1), amount_paid=105.0)
    p3 = BillPayment(bill_id=bill.id, due_date=datetime(2026, 3, 1), paid_date=datetime(2026, 3, 1), amount_paid=100.0)
    p4 = BillPayment(bill_id=bill.id, due_date=datetime(2026, 4, 1), paid_date=datetime(2026, 4, 1), amount_paid=1000.0)
    db_session.add_all([p1, p2, p3, p4])
    db_session.commit()
    db_session.refresh(bill)

    # Median of [95, 100, 105, 1000] is (100 + 105) / 2 = 102.5
    # 1000 is > 3 * 102.5 = 307.5 -> filtered out!
    # Remaining: [100, 105, 95] with weights 3, 2, 1 -> (3*100 + 2*105 + 1*95)/6 = (300+210+95)/6 = 605/6 = 100.83
    assert bill.estimated_amount == 100.83


def test_bill_amount_strategy_last_payment(db_session):
    bill = Bill(name="Phone", expected_amount=80.0, amount_strategy="last_payment")
    db_session.add(bill)
    db_session.commit()

    p1 = BillPayment(bill_id=bill.id, due_date=datetime(2026, 1, 1), paid_date=datetime(2026, 1, 1), amount_paid=85.0)
    p2 = BillPayment(bill_id=bill.id, due_date=datetime(2026, 2, 1), paid_date=datetime(2026, 2, 1), amount_paid=92.5)
    db_session.add_all([p1, p2])
    db_session.commit()
    db_session.refresh(bill)

    assert bill.estimated_amount == 92.5
    assert bill.effective_amount == 92.5
    assert bill.is_estimated


def test_bill_amount_strategy_fixed(db_session):
    bill = Bill(name="Netflix", expected_amount=22.99, amount_strategy="fixed")
    db_session.add(bill)
    db_session.commit()

    p1 = BillPayment(bill_id=bill.id, due_date=datetime(2026, 1, 1), paid_date=datetime(2026, 1, 1), amount_paid=15.0)
    p2 = BillPayment(bill_id=bill.id, due_date=datetime(2026, 2, 1), paid_date=datetime(2026, 2, 1), amount_paid=30.0)
    db_session.add_all([p1, p2])
    db_session.commit()
    db_session.refresh(bill)

    # Fixed strategy must stick to expected_amount regardless of past payments
    assert bill.estimated_amount == 22.99
    assert bill.effective_amount == 22.99
    assert not bill.is_estimated


def test_dashboard_monthly_total_uses_effective_amount(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    db_session.query(BillPayment).delete()
    db_session.query(Bill).delete()
    db_session.commit()

    # Active bill with rolling_avg: expected_amount=50, but last payments are 120 and 150 -> effective=140
    b = Bill(name="Gas", next_due_date=datetime(2026, 10, 15), expected_amount=50.0, frequency="monthly", amount_strategy="rolling_avg")
    db_session.add(b)
    db_session.commit()

    p1 = BillPayment(bill_id=b.id, due_date=datetime(2026, 8, 1), paid_date=datetime(2026, 8, 1), amount_paid=120.0)
    p2 = BillPayment(bill_id=b.id, due_date=datetime(2026, 9, 1), paid_date=datetime(2026, 9, 1), amount_paid=150.0)
    db_session.add_all([p1, p2])
    db_session.commit()

    res = client.get("/classic/bills")
    assert res.status_code == 200
    # Monthly total should be 140.00 (from rolling avg), not 50.00
    assert "$140.00" in res.text
    # Should display the ~ and Est. badge
    assert "~$140.00" in res.text

    app.dependency_overrides.clear()


def test_create_and_update_bill_amount_strategy(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    # Create with rolling_avg
    res = client.post("/bills/create", data={
        "name": "Council Rates",
        "expected_amount": "250",
        "amount_strategy": "rolling_avg",
        "frequency": "quarterly",
    }, follow_redirects=False)
    assert res.status_code == 303

    bill = db_session.query(Bill).filter(Bill.name == "Council Rates").first()
    assert bill is not None
    assert bill.amount_strategy == "rolling_avg"

    # Update to last_payment
    res_update = client.post(f"/bills/{bill.id}/update", data={
        "name": "Council Rates",
        "next_due_date": "2026-11-01",
        "frequency": "quarterly",
        "expected_amount": "260",
        "amount_strategy": "last_payment",
    }, follow_redirects=False)
    assert res_update.status_code == 303

    db_session.refresh(bill)
    assert bill.amount_strategy == "last_payment"

    app.dependency_overrides.clear()


def test_export_import_preserves_amount_strategy(db_session):
    from serializers import serialize_bill
    from import_logic import import_data_from_json

    bill = Bill(name="Electricity", expected_amount=150.0, amount_strategy="rolling_avg")
    db_session.add(bill)
    db_session.commit()

    serialized = serialize_bill(bill)
    assert serialized["amount_strategy"] == "rolling_avg"

    # Simulate import
    db_session.query(BillPayment).delete()
    db_session.query(Bill).delete()
    db_session.commit()

    import_data = {
        "version": 2,
        "data": {
            "bills": [serialized]
        }
    }
    stats = import_data_from_json(db_session, import_data)
    assert stats["errors"] == 0

    imported_bill = db_session.query(Bill).filter(Bill.name == "Electricity").first()
    assert imported_bill is not None
    assert imported_bill.amount_strategy == "rolling_avg"






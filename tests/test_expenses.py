from datetime import datetime, timedelta, date
import pytest
from fastapi.testclient import TestClient

from main import app, get_db
from models import Expense, JournalEntry, Bill, BillPayment
from serializers import serialize_expense
from import_logic import import_data_from_json


def test_expense_model_properties():
    dt = datetime(2026, 9, 24, 14, 30)
    exp = Expense(
        amount=25.50,
        description="Groceries",
        category="Food",
        date=dt,
        currency="AUD",
    )
    assert exp.amount == 25.50
    assert exp.currency_symbol == "$"
    assert exp.date_iso == "2026-09-24"
    assert "2026" in exp.date_formatted

    exp_eur = Expense(amount=10.0, currency="EUR", date=dt)
    assert exp_eur.currency_symbol == "€"


def test_create_expense_endpoint(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    db_session.query(Expense).delete()
    db_session.commit()

    res = client.post("/expenses/create", data={
        "amount": "42.80",
        "description": "Weekly Bus Pass",
        "category": "Transport",
        "date": "2026-09-24",
        "payment_method": "Card",
        "currency": "AUD",
        "note": "Zone 1-2",
    }, follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/expenses"

    expense = db_session.query(Expense).first()
    assert expense is not None
    assert expense.amount == 42.80
    assert expense.description == "Weekly Bus Pass"
    assert expense.category == "Transport"
    assert expense.payment_method == "Card"
    assert expense.currency == "AUD"
    assert expense.note == "Zone 1-2"

    # Journal entry should be logged
    journal = db_session.query(JournalEntry).filter(JournalEntry.entity_type == "expense").first()
    assert journal is not None
    assert "Weekly Bus Pass" in journal.details

    app.dependency_overrides.clear()


def test_create_expense_validation(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    db_session.query(Expense).delete()
    db_session.commit()

    # Invalid string
    res = client.post("/expenses/create", data={"amount": "invalid"}, follow_redirects=False)
    assert res.status_code == 400
    assert db_session.query(Expense).count() == 0

    # Negative / zero
    res = client.post("/expenses/create", data={"amount": "-15.00"}, follow_redirects=False)
    assert res.status_code == 400
    assert db_session.query(Expense).count() == 0

    res = client.post("/expenses/create", data={"amount": "0"}, follow_redirects=False)
    assert res.status_code == 400
    assert db_session.query(Expense).count() == 0

    app.dependency_overrides.clear()


def test_delete_expense_endpoint(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    exp = Expense(amount=19.99, description="Book", category="Entertainment", date=datetime.now())
    db_session.add(exp)
    db_session.commit()

    exp_id = exp.id
    res = client.post(f"/expenses/{exp_id}/delete", follow_redirects=False)
    assert res.status_code == 303

    deleted = db_session.query(Expense).filter(Expense.id == exp_id).first()
    assert deleted is None

    # Deleting non-existent redirects gracefully
    res_none = client.post("/expenses/99999/delete", follow_redirects=False)
    assert res_none.status_code == 303

    app.dependency_overrides.clear()


def test_update_expense_endpoint(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    exp = Expense(amount=15.00, description="Snack", category="Food", date=datetime(2026, 9, 20))
    db_session.add(exp)
    db_session.commit()

    res = client.post(f"/expenses/{exp.id}/update", data={
        "amount": "18.50",
        "description": "Lunch Meal",
        "category": "Food & Dining",
        "date": "2026-09-21",
        "payment_method": "Cash",
        "note": "Paid cash",
    }, follow_redirects=False)
    assert res.status_code == 303

    db_session.refresh(exp)
    assert exp.amount == 18.50
    assert exp.description == "Lunch Meal"
    assert exp.category == "Food & Dining"
    assert exp.payment_method == "Cash"
    assert exp.note == "Paid cash"

    app.dependency_overrides.clear()


def test_expenses_dashboard_metrics(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    db_session.query(Expense).delete()
    db_session.commit()

    today = date.today()
    exp1 = Expense(amount=50.0, description="Train", category="Transport", date=datetime.combine(today, datetime.min.time()))
    exp2 = Expense(amount=150.0, description="Groceries", category="Food", date=datetime.combine(today - timedelta(days=2), datetime.min.time()))
    db_session.add_all([exp1, exp2])
    db_session.commit()

    res = client.get("/expenses")
    assert res.status_code == 200
    # Total spending is $200.00
    assert "$200.00" in res.text
    # Descriptions present
    assert "Train" in res.text
    assert "Groceries" in res.text
    # Category badges present
    assert "Transport" in res.text
    assert "Food" in res.text
    # Quick Expenses heading
    assert "Quick Expenses" in res.text
    # Edit button and modal elements present
    assert "btn-edit" in res.text
    assert "Edit Expense" in res.text
    assert "modal-edit-amount" in res.text

    app.dependency_overrides.clear()


def test_export_import_expenses(db_session):
    exp = Expense(
        amount=89.95,
        description="Headphones",
        category="Tech",
        date=datetime(2026, 9, 24, 10, 0),
        payment_method="Card",
        currency="AUD",
        note="Wireless",
    )
    db_session.add(exp)
    db_session.commit()

    serialized = serialize_expense(exp)
    assert serialized["amount"] == 89.95
    assert serialized["description"] == "Headphones"
    assert serialized["category"] == "Tech"
    assert serialized["currency"] == "AUD"

    db_session.query(Expense).delete()
    db_session.commit()

    payload = {
        "version": 2,
        "data": {
            "expenses": [serialized]
        }
    }
    stats = import_data_from_json(db_session, payload)
    assert stats["errors"] == 0

    restored = db_session.query(Expense).filter(Expense.description == "Headphones").first()
    assert restored is not None
    assert restored.amount == 89.95
    assert restored.category == "Tech"
    assert restored.note == "Wireless"

    app.dependency_overrides.clear()


def test_expenses_all_list_view(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    db_session.query(Expense).delete()
    db_session.commit()

    e1 = Expense(amount=12.50, description="Morning Coffee", category="Food & Dining", date=datetime(2026, 9, 23))
    e2 = Expense(amount=95.00, description="Electric Scooter Repair", category="Transport", date=datetime(2026, 9, 24))
    db_session.add_all([e1, e2])
    db_session.commit()

    res = client.get("/expenses/list")
    assert res.status_code == 200
    assert "All Expenses" in res.text
    assert "Morning Coffee" in res.text
    assert "Electric Scooter Repair" in res.text
    assert "$107.50" in res.text
    assert "Export CSV" in res.text

    # /expenses/all alias
    res_alias = client.get("/expenses/all")
    assert res_alias.status_code == 200
    assert "All Expenses" in res_alias.text

    app.dependency_overrides.clear()


def test_export_expenses_csv(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    db_session.query(Expense).delete()
    db_session.commit()

    e = Expense(amount=55.20, description="Groceries Store", category="Groceries", date=datetime(2026, 9, 22), payment_method="Card", note="Fresh fruits")
    db_session.add(e)
    db_session.commit()

    res = client.get("/expenses/export/csv")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=" in res.headers["content-disposition"]
    csv_text = res.text
    assert "ID,Date,Description,Category,Payment Method,Amount,Currency,Note" in csv_text
    assert "Groceries Store" in csv_text
    assert "55.20" in csv_text
    assert "Fresh fruits" in csv_text

    app.dependency_overrides.clear()


def test_update_and_delete_expense_return_to(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    exp = Expense(amount=25.00, description="Fuel", category="Transport", date=datetime(2026, 9, 24))
    db_session.add(exp)
    db_session.commit()

    # Update with return_to
    res_update = client.post(f"/expenses/{exp.id}/update", data={
        "amount": "30.00",
        "description": "Fuel Petrol",
        "category": "Transport",
        "date": "2026-09-24",
        "return_to": "/expenses/list",
    }, follow_redirects=False)
    assert res_update.status_code == 303
    assert res_update.headers["location"] == "/expenses/list"

    # Delete with return_to
    res_delete = client.post(f"/expenses/{exp.id}/delete", data={
        "return_to": "/expenses/list",
    }, follow_redirects=False)
    assert res_delete.status_code == 303
    assert res_delete.headers["location"] == "/expenses/list"

    app.dependency_overrides.clear()


def test_expenses_include_bills_toggle(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    db_session.query(Expense).delete()
    db_session.query(BillPayment).delete()
    db_session.query(Bill).delete()
    db_session.commit()

    # Add an active bill: Monthly Internet $80.00
    bill = Bill(
        name="Fiber Internet",
        expected_amount=80.0,
        frequency="monthly",
        is_active=True,
        next_due_date=datetime(2026, 10, 1),
    )
    db_session.add(bill)

    # Add an expense: $20.00
    today = date.today()
    exp = Expense(
        amount=20.0,
        description="Lunch",
        category="Food",
        date=datetime.combine(today, datetime.min.time()),
    )
    db_session.add(exp)
    db_session.commit()

    # GET /expenses (toggle off by default in request)
    res = client.get("/expenses")
    assert res.status_code == 200
    assert "Include Bills" in res.text
    assert "toggle-bills-averages" in res.text
    assert "+$80.00/mo" in res.text

    # GET /expenses?include_bills=1 (toggle on)
    res_incl = client.get("/expenses?include_bills=1")
    assert res_incl.status_code == 200
    # Combined monthly should include $20 (scaled) + $80 bills
    assert "$100.00" in res_incl.text
    assert "incl. bills" in res_incl.text

    app.dependency_overrides.clear()


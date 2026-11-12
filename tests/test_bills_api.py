from datetime import datetime, timedelta
import hashlib

from fastapi import Request
from fastapi.testclient import TestClient

from main import app, get_db
from auth import get_authenticated_user
from models import Bill, BillPayment


def test_bills_api_dashboard_groups_and_auth(db_session, monkeypatch, tmp_path):
    now = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    bills = [
        Bill(name='Overdue', next_due_date=now - timedelta(days=1), expected_amount=10),
        Bill(name='Today', next_due_date=now, expected_amount=20),
        Bill(name='Upcoming', next_due_date=now + timedelta(days=14), expected_amount=30),
        Bill(name='Confirmed', next_due_date=now + timedelta(days=40), is_due_date_confirmed=True),
        Bill(name='Future', next_due_date=now + timedelta(days=15), expected_amount=50),
        Bill(name='Covered', next_due_date=now + timedelta(days=45)),
        Bill(name='Unscheduled'),
        Bill(name='Inactive', is_active=False),
    ]
    db_session.add_all(bills)
    db_session.flush()
    db_session.add(BillPayment(bill=bills[5], due_date=now + timedelta(days=5),
                               paid_date=now, amount_paid=12))
    db_session.commit()
    app.dependency_overrides[get_db] = lambda: db_session
    monkeypatch.setenv('SIMPLE_TODO_BASIC_AUTH', '1')
    password_file = tmp_path / 'htpasswd'
    password_file.write_text('apiuser:password\n')
    monkeypatch.setenv('SIMPLE_TODO_HTPASSWD_PATH', str(password_file))
    try:
        client = TestClient(app)
        assert client.get('/api/bills').status_code == 401
        assert client.get('/api/bills', auth=('apiuser', 'bad')).status_code == 401
        response = client.get('/api/bills?show_inactive=1', auth=('apiuser', 'password'))
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        data = response.json()
        for group, names in {
            'overdue': ['Overdue'], 'due_today': ['Today'],
            'upcoming': ['Upcoming', 'Confirmed'], 'covered_early': ['Covered'],
            'future_estimates': ['Future'], 'unscheduled': ['Unscheduled'],
            'inactive_bills': ['Inactive'],
        }.items():
            assert {bill['name'] for bill in data[group]} == set(names)
        assert data['future_estimates'][0]['effective_amount'] == 50
        assert data['recent_payments'][0]['bill_name'] == 'Covered'
        assert data['recent_payments'][0]['due_date'] == (now + timedelta(days=5)).date().isoformat()
        assert data['monthly_estimates'][0]['currency'] == 'AUD'
        assert client.get('/api/bills', auth=('apiuser', 'password')).json()['inactive_bills'] == []
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_bills_key_is_scoped_and_selects_configured_user(db_session, monkeypatch):
    monkeypatch.setenv('SIMPLE_TODO_BASIC_AUTH', '1')
    monkeypatch.setenv('SIMPLE_TODO_BILLS_API_USER', 'alice')
    monkeypatch.setenv('SIMPLE_TODO_BILLS_API_KEY_SHA256', hashlib.sha256(b'secret-key').hexdigest())
    users = []

    def database_for_request(request: Request):
        users.append(get_authenticated_user(request))
        return db_session

    app.dependency_overrides[get_db] = database_for_request
    try:
        client = TestClient(app)
        headers = {'Authorization': 'Bearer secret-key'}
        assert client.get('/api/bills', headers=headers).status_code == 200
        assert users == ['alice']
        assert client.get('/api/bills', headers={'Authorization': 'Bearer wrong'}).status_code == 401
        assert client.get('/api/export', headers=headers).status_code == 401
        assert client.post('/api/bills', headers=headers).status_code == 401
        monkeypatch.setenv('SIMPLE_TODO_BILLS_API_KEY_SHA256', hashlib.sha256(b'new-key').hexdigest())
        assert client.get('/api/bills', headers=headers).status_code == 401
        assert client.get('/api/bills', headers={'Authorization': 'Bearer new-key'}).status_code == 200
        monkeypatch.delenv('SIMPLE_TODO_BILLS_API_USER')
        assert client.get('/api/bills', headers={'Authorization': 'Bearer new-key'}).status_code == 401
    finally:
        app.dependency_overrides.pop(get_db, None)

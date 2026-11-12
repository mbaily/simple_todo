from datetime import datetime
import hashlib

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from auth import _sign_session, get_authenticated_user
from main import app, get_db
from models import CalendarEvent, CalendarEventCompletion


@pytest.fixture
def calendar_client(db_session, monkeypatch, tmp_path):
    monkeypatch.setenv('SIMPLE_TODO_BASIC_AUTH', '1')
    password_file = tmp_path / 'htpasswd'
    password_file.write_text('apiuser:password\n')
    monkeypatch.setenv('SIMPLE_TODO_HTPASSWD_PATH', str(password_file))
    monkeypatch.setenv('SIMPLE_TODO_CALENDAR_API_USER', 'calendar-user')
    monkeypatch.setenv('SIMPLE_TODO_CALENDAR_API_KEY_SHA256', hashlib.sha256(b'calendar-key').hexdigest())
    monkeypatch.setenv('SIMPLE_TODO_BILLS_API_USER', 'bills-user')
    monkeypatch.setenv('SIMPLE_TODO_BILLS_API_KEY_SHA256', hashlib.sha256(b'bills-key').hexdigest())
    users = []

    def database_for_request(request: Request):
        users.append(get_authenticated_user(request))
        return db_session

    app.dependency_overrides[get_db] = database_for_request
    try:
        with TestClient(app) as client:
            yield db_session, client, users
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_calendar_range_occurrences_and_boundaries(calendar_client):
    db, client, users = calendar_client
    daily = CalendarEvent(title='Daily', start_at=datetime(2026, 10, 31, 9),
                          all_day=False, duration_minutes=60, rrule='FREQ=DAILY', note='Private')
    first = CalendarEvent(title='First', start_at=datetime(2026, 11, 1))
    last = CalendarEvent(title='Last', start_at=datetime(2026, 11, 2, 23, 59, 59, 999999), all_day=False)
    overlap = CalendarEvent(title='Overlapping', start_at=datetime(2026, 10, 31, 23),
                            all_day=False, duration_minutes=120)
    outside = CalendarEvent(title='Outside', start_at=datetime(2026, 11, 3))
    db.add_all([daily, first, last, overlap, outside])
    db.flush()
    db.add(CalendarEventCompletion(event=daily, occurrence_at=datetime(2026, 11, 2, 9)))
    db.commit()
    response = client.get('/api/calendar/events', params={'start_date': '2026-11-01', 'end_date': '2026-11-02'},
                          headers={'Authorization': 'Bearer calendar-key'})
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert users == ['calendar-user']
    data = response.json()
    assert (data['start_date'], data['end_date']) == ('2026-11-01', '2026-11-02')
    rows = data['occurrences']
    assert [(row['event']['title'], row['occurrence_at'], row['completed']) for row in rows] == [
        ('First', '2026-11-01T00:00:00', False),
        ('Daily', '2026-11-01T09:00:00', False),
        ('Daily', '2026-11-02T09:00:00', True),
        ('Last', '2026-11-02T23:59:59.999999', False),
    ]
    assert rows[1]['event']['start_at'] == '2026-10-31T09:00:00'
    assert rows[1]['event']['note'] == 'Private'
    same_day = client.get('/api/calendar/events?start_date=2026-11-02&end_date=2026-11-02',
                          auth=('apiuser', 'password'))
    assert [row['event']['title'] for row in same_day.json()['occurrences']] == ['Daily', 'Last']
    assert users[-1] == 'apiuser'


@pytest.mark.parametrize('query', [
    '', '?start_date=2026-11-01', '?start_date=2026-11-01&end_date=2026-10-31',
    '?start_date=2026-02-30&end_date=2026-03-01',
    '?start_date=2026-1-01&end_date=2026-01-02',
    '?start_date=2026-01-01&end_date=2027-01-02',
])
def test_calendar_rejects_invalid_ranges(calendar_client, query):
    _, client, _ = calendar_client
    assert client.get('/api/calendar/events' + query, auth=('apiuser', 'password')).status_code == 422


def test_calendar_accepts_maximum_range(calendar_client):
    _, client, _ = calendar_client
    response = client.get('/api/calendar/events?start_date=2026-01-01&end_date=2027-01-01',
                          auth=('apiuser', 'password'))
    assert response.status_code == 200
    assert response.json()['occurrences'] == []


def test_calendar_key_scope_and_rotation(calendar_client, monkeypatch):
    _, client, users = calendar_client
    url = '/api/calendar/events?start_date=2026-11-01&end_date=2026-11-01'
    calendar = {'Authorization': 'Bearer calendar-key'}
    bills = {'Authorization': 'Bearer bills-key'}
    assert client.get(url).status_code == 401
    assert client.get(url, headers=bills).status_code == 401
    assert client.get('/api/bills', headers=calendar).status_code == 401
    assert client.get('/api/export', headers=calendar).status_code == 401
    assert client.post('/api/calendar/events', headers=calendar).status_code == 401
    assert client.get(url, headers=calendar).status_code == 200
    assert users == ['calendar-user']
    monkeypatch.setenv('SIMPLE_TODO_CALENDAR_API_KEY_SHA256', hashlib.sha256(b'new-key').hexdigest())
    assert client.get(url, headers=calendar).status_code == 401
    assert client.get(url, headers={'Authorization': 'Bearer new-key'}).status_code == 200
    monkeypatch.delenv('SIMPLE_TODO_CALENDAR_API_USER')
    assert client.get(url, headers={'Authorization': 'Bearer new-key'}).status_code == 401


def test_calendar_bearer_selects_its_user_over_session(calendar_client):
    _, client, users = calendar_client
    url = '/api/calendar/events?start_date=2026-11-01&end_date=2026-11-01'
    client.cookies.set('simple_todo_session', _sign_session('apiuser'))
    assert client.get(url).status_code == 200
    assert users[-1] == 'apiuser'
    assert client.get(url, headers={'Authorization': 'Bearer calendar-key'}).status_code == 200
    assert users[-1] == 'calendar-user'
    assert client.get(url, headers={'Authorization': 'Bearer invalid'}).status_code == 401


def test_calendar_without_global_auth_is_public(calendar_client, monkeypatch):
    _, client, users = calendar_client
    monkeypatch.setenv('SIMPLE_TODO_BASIC_AUTH', '0')
    url = '/api/calendar/events?start_date=2026-11-01&end_date=2026-11-01'
    assert client.get(url).status_code == 200
    assert users[-1] == 'default'


def test_calendar_reports_excess_and_invalid_recurrence(calendar_client):
    db, client, _ = calendar_client
    event = CalendarEvent(title='Dense', start_at=datetime(2026, 11, 1), rrule='FREQ=SECONDLY')
    db.add(event)
    db.commit()
    url = '/api/calendar/events?start_date=2026-11-01&end_date=2026-11-01'
    response = client.get(url, auth=('apiuser', 'password'))
    assert response.status_code == 422
    assert '10000' in response.json()['detail']
    event.rrule = 'FREQ=BOGUS'
    db.commit()
    response = client.get(url, auth=('apiuser', 'password'))
    assert response.status_code == 500
    assert f'#{event.id}' in response.json()['detail']


def test_calendar_occurrence_limit_is_inclusive(calendar_client):
    db, client, _ = calendar_client
    event = CalendarEvent(title='Dense', start_at=datetime(2026, 11, 1),
                          rrule='FREQ=SECONDLY;COUNT=10000')
    db.add(event)
    db.commit()
    url = '/api/calendar/events?start_date=2026-11-01&end_date=2026-11-01'
    response = client.get(url, auth=('apiuser', 'password'))
    assert response.status_code == 200
    assert len(response.json()['occurrences']) == 10000
    event.rrule = 'FREQ=SECONDLY;COUNT=10001'
    db.commit()
    assert client.get(url, auth=('apiuser', 'password')).status_code == 422


def test_calendar_reports_unavailable_recurrence_support(calendar_client, monkeypatch):
    import calendar_service
    db, client, _ = calendar_client
    db.add(CalendarEvent(title='Recurring', start_at=datetime(2026, 11, 1), rrule='FREQ=DAILY'))
    db.commit()
    monkeypatch.setattr(calendar_service, 'rrulestr', None)
    response = client.get('/api/calendar/events?start_date=2026-11-01&end_date=2026-11-01',
                          auth=('apiuser', 'password'))
    assert response.status_code == 500
    assert 'unavailable' in response.json()['detail']


def test_calendar_api_important_if_late_and_late_status(calendar_client):
    from datetime import date, timedelta
    db, client, _ = calendar_client
    today = date.today()
    past_date = today - timedelta(days=2)
    future_date = today + timedelta(days=2)

    past_important = CalendarEvent(
        title='Past Important Event',
        start_at=datetime.combine(past_date, datetime.min.time()),
        important_if_late=True,
    )
    past_normal = CalendarEvent(
        title='Past Normal Event',
        start_at=datetime.combine(past_date, datetime.min.time()),
        important_if_late=False,
    )
    past_completed = CalendarEvent(
        title='Past Completed Event',
        start_at=datetime.combine(past_date, datetime.min.time()),
        important_if_late=True,
    )
    future_important = CalendarEvent(
        title='Future Important Event',
        start_at=datetime.combine(future_date, datetime.min.time()),
        important_if_late=True,
    )
    db.add_all([past_important, past_normal, past_completed, future_important])
    db.flush()
    db.add(CalendarEventCompletion(event=past_completed, occurrence_at=past_completed.start_at))
    db.commit()

    url = f'/api/calendar/events?start_date={past_date.isoformat()}&end_date={future_date.isoformat()}'
    response = client.get(url, auth=('apiuser', 'password'))
    assert response.status_code == 200
    occurrences = response.json()['occurrences']

    by_title = {occ['event']['title']: occ for occ in occurrences}

    # Past unchecked important event
    occ = by_title['Past Important Event']
    assert occ['event']['important_if_late'] is True
    assert occ['completed'] is False
    assert occ['is_late'] is True
    assert occ['is_important'] is True

    # Past unchecked normal event (default: not important if late)
    occ = by_title['Past Normal Event']
    assert occ['event']['important_if_late'] is False
    assert occ['completed'] is False
    assert occ['is_late'] is True
    assert occ['is_important'] is False

    # Past completed event (even if marked important_if_late, it is completed so neither late nor important)
    occ = by_title['Past Completed Event']
    assert occ['event']['important_if_late'] is True
    assert occ['completed'] is True
    assert occ['is_late'] is False
    assert occ['is_important'] is False

    # Future important event (not late yet)
    occ = by_title['Future Important Event']
    assert occ['event']['important_if_late'] is True
    assert occ['completed'] is False
    assert occ['is_late'] is False
    assert occ['is_important'] is False

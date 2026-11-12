from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text

from calendar_service import CALENDAR_SORT_OPTIONS, sort_calendar_occurrences
from database import run_migrations
from models import CalendarEvent, UserSettings
from test_billing_accounts import account


@pytest.mark.parametrize('sort_order, expected', [
    ('created_asc', [1, 2, 4, 3, 5]),
    ('created_desc', [3, 2, 4, 1, 5]),
    ('title_asc', [2, 4, 3, 5, 1]),
    ('title_desc', [1, 5, 3, 2, 4]),
    ('modified_desc', [1, 3, 2, 4, 5]),
    ('start_asc', [1, 2, 4, 3, 5]),
])
def test_sort_keeps_days_chronological_with_stable_ties_and_missing_dates_last(sort_order, expected):
    today = datetime(2026, 10, 27)
    events = [
        SimpleNamespace(id=identifier, title=title,
                        created_at=today + timedelta(days=created) if created else None,
                        modified_at=today + timedelta(days=modified) if modified else None,
                        start_at=today + timedelta(days=start))
        for identifier, title, created, modified, start in [
            (1, 'Zebra', 1, 3, -3), (2, 'alpha', 2, 1, -2),
            (3, 'Beta', 3, 2, -1), (4, 'Alpha', 2, 1, -2),
            (5, 'Missing dates', None, None, 0),
        ]
    ]
    rows = [dict(event=event, occurrence_at=day)
            for day in [today + timedelta(days=1), today]
            for event in reversed(events)]
    sort_calendar_occurrences(rows, sort_order)
    assert [row['event'].id for row in rows] == expected * 2
    assert [row['occurrence_at'] for row in rows] == [today] * 5 + [today + timedelta(days=1)] * 5


@pytest.mark.parametrize('monthly', [False, True])
def test_sort_dropdown_persists_and_orders_recurring_and_single_events(account, monthly):
    db, _, client = account
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    db.add_all([
        CalendarEvent(title='Zebra', start_at=today - timedelta(days=10),
                      rrule='FREQ=DAILY', created_at=today - timedelta(days=3)),
        CalendarEvent(title='alpha', start_at=today, created_at=today - timedelta(days=2)),
        CalendarEvent(title='Beta', start_at=today, created_at=today - timedelta(days=1)),
    ])
    settings = UserSettings(id=1, calendar_traditional_view=monthly)
    db.add(settings)
    db.commit()
    for order, expected in [('created_asc', ['Zebra', 'alpha', 'Beta']),
                            ('created_desc', ['Beta', 'alpha', 'Zebra']),
                            ('title_asc', ['alpha', 'Beta', 'Zebra']),
                            ('title_desc', ['Zebra', 'Beta', 'alpha'])]:
        response = client.post('/calendar/set-sort', data={'calendar_sort_order': order}, follow_redirects=True)
        assert response.status_code == 200
        assert db.get(UserSettings, 1).calendar_sort_order == order
        assert f'title="Sort events within each day ({CALENDAR_SORT_OPTIONS[order]})"' in response.text
        for label in CALENDAR_SORT_OPTIONS.values():
            assert label in response.text
        if monthly:
            rows = next(cell['events'] for week in response.context['weeks'] for cell in week
                        if cell['day'] == today.day)
        else:
            rows = [row for row in response.context['occurrences'] if row['occurrence_at'] == today]
        assert [row['event'].title for row in rows] == expected

    response = client.post('/calendar/toggle-view', follow_redirects=True)
    assert 'title="Sort events within each day (Title: Z–A)"' in response.text
    assert client.get('/api/export').json()['data']['user_settings'][0]['calendar_sort_order'] == 'title_desc'
    assert client.post('/calendar/set-sort', data={'calendar_sort_order': 'unknown'}).status_code == 400
    assert db.get(UserSettings, 1).calendar_sort_order == 'title_desc'


def test_sort_preserves_calendar_navigation(account):
    _, _, client = account
    response = client.post('/calendar/set-sort', data={'calendar_sort_order': 'title_asc', 'month': '2027-04'}, follow_redirects=False)
    assert response.headers['location'] == '/classic/calendar?month=2027-04'
    response = client.post('/calendar/set-sort', data={'calendar_sort_order': 'title_asc', 'offset': -2}, follow_redirects=False)
    assert response.headers['location'] == '/classic/calendar?offset=-2'


def test_existing_settings_migrate_to_created_oldest_first():
    engine = create_engine('sqlite://')
    try:
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE user_settings (id INTEGER PRIMARY KEY)'))
            connection.execute(text('INSERT INTO user_settings (id) VALUES (1)'))
        run_migrations(engine)
        with engine.connect() as connection:
            assert connection.execute(text('SELECT calendar_sort_order FROM user_settings')).scalar_one() == 'created_asc'
    finally:
        engine.dispose()

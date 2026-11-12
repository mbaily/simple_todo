"""Invalid calendar recurrence must not save or make pages unavailable."""
import copy
import json
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from helpers import _event_occurrences
from import_logic import import_data_from_json
from models import Base, CalendarEvent, CalendarEventCompletion, UserSettings
from test_billing_accounts import account, day


INVALID = ['FREQ=BOGUS', 'FREQ=DAILY;INTERVAL=0', 'FREQ=DAILY;INTERVAL=-1',
           'FREQ=DAILY;COUNT=0', 'FREQ=DAILY;BYHOUR=99', 'FREQ=DAILY;BYSECOND=60',
           'FREQ=DAILY;BYMONTH=13', 'FREQ=DAILY;BYMONTHDAY=0', 'FREQ=WEEKLY;BYDAY=ZZ',
           'FREQ=MONTHLY;BYDAY=0MO', 'FREQ=DAILY;UNTIL=not-a-date', 'RRULE:',
           'FREQ=DAILY;FREQ=WEEKLY', 'FREQ=DAILY;BYSETPOS=0']


@pytest.mark.parametrize('rule', INVALID)
@pytest.mark.parametrize('operation', ['create', 'edit'])
def test_invalid_calendar_form_rejects_rule_without_changing_any_records(account, rule, operation):
    db, _, client = account
    event = CalendarEvent(title='Original', start_at=day('2026-10-08'), rrule='FREQ=DAILY')
    db.add(event); db.commit()
    client.get('/customization')
    before = client.get('/api/export').json()['data']
    path = '/calendar/create' if operation == 'create' else f'/calendar/event/{event.id}/update'
    response = client.post(path, data={'title': 'Must not save', 'start_at': '2026-10-08', 'rrule': rule}, follow_redirects=False)
    assert response.status_code == 400 and 'recurrence' in response.text.lower()
    assert client.get('/api/export').json()['data'] == before
    assert client.get('/classic').status_code == client.get('/classic/calendar').status_code == 200


@pytest.mark.parametrize('rule', INVALID + [True, [], {}])
def test_invalid_calendar_import_rolls_back_other_records(account, rule):
    db, _, client = account
    client.get('/customization')
    before = client.get('/api/export').json()['data']
    payload = {'data': {'folders': [dict(id=99, name='Must not persist')],
                       'calendar_events': [dict(id=99, title='Invalid', start_at='2026-10-08', rrule=rule)]}}
    with pytest.raises(ValueError, match='calendar_events.*recurrence.*No changes were saved'):
        import_data_from_json(db, copy.deepcopy(payload))
    response = client.post('/api/import', files={'file': ('invalid.json', json.dumps(payload), 'application/json')}, follow_redirects=False)
    query = parse_qs(urlparse(response.headers['location']).query)
    assert 'import_success' not in query and 'No changes were saved' in query['import_error'][0]
    assert client.get('/api/export').json()['data'] == before


@pytest.mark.parametrize('rule', ['FREQ=DAILY;INTERVAL=2', 'RRULE:FREQ=WEEKLY;BYDAY=MO,WE',
    'FREQ=MONTHLY;BYDAY=-1FR', 'FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=29',
    'FREQ=DAILY;UNTIL=20261231', 'FREQ=HOURLY;COUNT=2', 'FREQ=MONTHLY;BYMONTHDAY=-1', ''])
def test_valid_rules_work_in_forms_partial_imports_and_repeated_restores(account, rule):
    db, _, client = account
    response = client.post('/calendar/create', data={'title': 'Valid', 'start_at': '2026-10-08', 'rrule': rule}, follow_redirects=False)
    assert response.status_code == 303
    event = db.query(CalendarEvent).one()
    completion = CalendarEventCompletion(event=event, occurrence_at=day('2026-10-08'))
    db.add(completion); db.commit()
    assert import_data_from_json(db, {'data': {'calendar_events': [dict(id=event.id, title='Renamed')]}})['errors'] == 0
    exported = client.get('/api/export').json()
    for _ in range(2):
        assert import_data_from_json(db, copy.deepcopy(exported))['errors'] == 0
        assert client.get('/api/export').json()['data'] == exported['data']
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as restored:
            assert import_data_from_json(restored, copy.deepcopy(exported))['errors'] == 0
            assert restored.get(CalendarEvent, event.id).rrule == event.rrule
            assert restored.query(CalendarEventCompletion).count() == 1
    finally:
        engine.dispose()
    assert client.get('/classic').status_code == client.get('/classic/calendar').status_code == 200


@pytest.mark.parametrize('rule', ['FREQ=BOGUS', 'FREQ=DAILY;BYHOUR=99', 'FREQ=DAILY;UNTIL=not-a-date',
                                 'FREQ=DAILY;INTERVAL=0', 'RRULE:'])
def test_existing_invalid_rules_leave_pages_usable_and_calendar_offers_edit_link(account, rule):
    db, _, client = account
    start = datetime.combine(date.today(), datetime.min.time())
    broken = CalendarEvent(title='Needs recurrence repair', start_at=start, rrule=rule)
    good = CalendarEvent(title='Healthy daily event', start_at=start, rrule='FREQ=DAILY')
    db.add_all([broken, good]); db.commit()
    assert client.get('/classic').status_code == 200
    assert client.get('/api/data/calendar').status_code == 200
    for monthly in (False, True):
        settings = db.get(UserSettings, 1)
        settings.calendar_traditional_view = monthly
        db.commit()
        response = client.get('/classic/calendar')
        assert response.status_code == 200 and 'Healthy daily event' in response.text
        assert 'Needs recurrence repair' in response.text
        assert f'/calendar/event/{broken.id}/edit' in response.text
        assert 'Invalid calendar recurrence' in response.text
    assert broken.rrule == rule
    corrected = client.post(f'/calendar/event/{broken.id}/update', data={'title': broken.title, 'start_at': start.date().isoformat(), 'rrule': 'FREQ=WEEKLY'}, follow_redirects=False)
    assert corrected.status_code == 303
    assert 'Invalid calendar recurrence' not in client.get('/classic/calendar').text


def test_calendar_import_validates_final_partial_rule_and_start_date(account):
    db, _, client = account
    event = CalendarEvent(title='Existing', start_at=day('2026-10-08'), rrule='FREQ=DAILY')
    db.add(event); db.commit()
    assert import_data_from_json(db, {'data': {'calendar_events': [dict(id=event.id, rrule='FREQ=BOGUS'),
                          dict(id=event.id, rrule='FREQ=WEEKLY', start_at='2026-10-09')]}})['errors'] == 0
    assert event.rrule == 'FREQ=WEEKLY' and event.start_at == day('2026-10-09')


def test_calendar_occurrences_stop_at_limit_and_range_end():
    start = day('2026-10-08')
    event = CalendarEvent(title='Dense valid rule', start_at=start, rrule='FREQ=SECONDLY')
    occurrences = _event_occurrences(event, start, start + timedelta(minutes=10))
    from config import CALENDAR_MAX_OCCURRENCES
    assert len(occurrences) == CALENDAR_MAX_OCCURRENCES
    assert occurrences[0] == start and occurrences[-1] == start + timedelta(seconds=CALENDAR_MAX_OCCURRENCES - 1)
    assert _event_occurrences(event, start, start + timedelta(seconds=2)) == [start, start + timedelta(seconds=1), start + timedelta(seconds=2)]

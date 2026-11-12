import copy
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from database import run_migrations
from import_logic import import_data_from_json
from merkle_sync import build_data_tree
from models import Base, CalendarDay, CalendarEvent, CalendarEventCompletion, EventGroup, UserSettings
from test_billing_accounts import account
from test_calendar_grid import serve_calendar


@pytest.mark.parametrize('event_filter, expected', [
    ('all', {'Daily', 'One-time', 'Other group'}),
    ('recurring', {'Daily'}),
    ('non_recurring', {'One-time', 'Other group'}),
    ('group', {'Daily', 'One-time'}),
])
def test_complete_day_respects_filters_and_occurrence_dates_and_is_idempotent(account, event_filter, expected):
    db, _, client = account
    day = datetime(2026, 10, 27)
    group = EventGroup(name='Appointments')
    db.add(group)
    db.flush()
    db.add(UserSettings(id=1, calendar_event_filter=f'group:{group.id}' if event_filter == 'group' else event_filter))
    daily = CalendarEvent(title='Daily', start_at=day - timedelta(days=5), rrule='FREQ=DAILY', event_group=group)
    db.add_all([
        daily, CalendarEvent(title='One-time', start_at=day, event_group=group),
        CalendarEvent(title='Other group', start_at=day),
        CalendarEvent(title='Tomorrow', start_at=day + timedelta(days=1)),
        CalendarEvent(title='Broken rule', start_at=day, rrule='FREQ=INVALID'),
    ])
    db.flush()
    db.add(CalendarEventCompletion(event=daily, occurrence_at=day - timedelta(days=1)))
    db.commit()
    for _ in range(2):
        response = client.post('/calendar/day/2026-10-27/complete', follow_redirects=False)
        assert response.status_code == 303
        assert response.headers['location'] == '/classic/calendar?month=2026-10'
        rows = db.query(CalendarEventCompletion).filter_by(occurrence_at=day).all()
        assert {row.event.title for row in rows} == expected
        assert len(rows) == len(expected)
        assert db.query(CalendarEventCompletion).count() == len(expected) + 1


def test_day_note_and_visibility_persist_independently_and_round_trip_in_backup(account):
    db, _, client = account
    day = datetime(2026, 10, 27)
    note = 'Day note with <markup> & a second line\nRemember this.'
    before_hash = build_data_tree(db)['hash']
    response = client.post('/calendar/day/2026-10-27/note', data={'note': note}, follow_redirects=False)
    assert response.headers['location'] == '/classic/calendar?month=2026-10#day-2026-10-27'
    state = db.query(CalendarDay).filter_by(day=day).one()
    assert state.note == note and not state.hide_completed
    assert build_data_tree(db)['hash'] != before_hash
    assert build_data_tree(db)['children']['calendar_days']['data'][0]['note'] == note
    client.post('/calendar/day/2026-10-27/toggle-completed', follow_redirects=False)
    assert state.hide_completed
    client.post('/calendar/day/2026-10-28/note', data={'note': 'Tomorrow note'}, follow_redirects=False)
    assert db.query(CalendarDay).count() == 2
    assert db.query(CalendarDay).filter_by(day=day + timedelta(days=1)).one().hide_completed is False
    exported = client.get('/api/export').json()
    assert len(exported['data']['calendar_days']) == 2
    assert import_data_from_json(db, copy.deepcopy(exported))['errors'] == 0
    assert client.get('/api/export').json()['data']['calendar_days'] == exported['data']['calendar_days']
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as restored:
            assert import_data_from_json(restored, copy.deepcopy(exported))['errors'] == 0
            record = restored.query(CalendarDay).filter_by(day=day).one()
            assert record.note == note and record.hide_completed
    finally:
        engine.dispose()
    client.post('/calendar/day/2026-10-27/note', data={'note': '  '}, follow_redirects=False)
    assert state.note is None and state.hide_completed


@pytest.mark.parametrize('action', ['note', 'complete', 'toggle-completed'])
def test_day_actions_reject_invalid_dates_without_writing(account, action):
    db, _, client = account
    response = client.post(f'/calendar/day/2026-02-30/{action}', data={'note': 'No save'})
    assert response.status_code == 400
    assert db.query(CalendarDay).count() == db.query(CalendarEventCompletion).count() == 0


def test_calendar_day_table_is_created_for_existing_databases():
    engine = create_engine('sqlite://')
    try:
        run_migrations(engine)
        assert 'calendar_days' in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_day_toolbar_menu_details_notes_counts_and_completion(account, page):
    db, _, client = account
    day = datetime(2026, 10, 27)
    db.add(UserSettings(id=1, calendar_traditional_view=True))
    daily = CalendarEvent(title='Daily appointment', start_at=day - timedelta(days=1), rrule='FREQ=DAILY')
    single = CalendarEvent(title='A full event title ' * 10, note='Event note <b>as plain text</b>', start_at=day)
    db.add_all([daily, single])
    db.flush()
    db.add(CalendarEventCompletion(event=daily, occurrence_at=day))
    db.commit()
    serve_calendar(page, client)
    page.set_viewport_size({'width': 390, 'height': 350})
    page.goto('http://calendar.test/classic/calendar?month=2026-10')
    cell = page.locator('.cal-day[data-date="2026-10-27"]')
    more = cell.get_by_role('button', name='More actions for 2026-10-27')
    assert cell.locator('.cal-day-count').inner_text() == '2 events · 1 remaining'
    more.click()
    menu = page.locator('#calendar-day-menu')
    assert menu.is_visible()
    assert menu.get_by_role('button', name='Add event', exact=True).is_visible()
    box = menu.bounding_box()
    assert box['x'] >= 0 and box['x'] + box['width'] <= 390
    assert box['y'] >= 0 and box['y'] + box['height'] <= 844
    assert not menu.get_by_role('button', name='Move', exact=False).count()
    assert not menu.get_by_role('button', name='Copy', exact=False).count()
    assert not menu.get_by_role('button', name='Select', exact=False).count()
    menu.get_by_role('button', name='Add event', exact=True).click()
    assert page.locator('#create-event-form input[name="start_at"]').input_value() == '2026-10-27'
    more.click()
    menu.get_by_role('button', name='Open day', exact=True).click()
    dialog = page.get_by_role('dialog', name='2026-10-27')
    assert dialog.is_visible()
    assert dialog.locator('.cal-event-title').count() == 2
    assert dialog.locator('.cal-day-event-note').inner_text() == 'Event note <b>as plain text</b>'
    assert not dialog.locator('.cal-day-event-note b').count()
    assert dialog.locator('.cal-event-title').last.evaluate('e => e.scrollWidth <= e.clientWidth + 1')
    dialog.get_by_label('Day note', exact=True).fill('Saved day note <script>literal</script>')
    dialog.get_by_role('button', name='Save day note', exact=True).click()
    page.wait_for_load_state()
    page.goto('http://calendar.test/classic/calendar?month=2026-10#day-2026-10-27')
    assert dialog.is_visible()
    assert dialog.get_by_label('Day note', exact=True).input_value() == 'Saved day note <script>literal</script>'
    dialog.get_by_role('button', name='Close day', exact=True).click()
    more.click()
    menu.get_by_role('button', name='Hide completed', exact=True).click()
    page.wait_for_load_state()
    assert not cell.locator('.cal-event-completed').is_visible()
    assert cell.locator('.cal-event:visible').count() == 1
    page.goto('http://calendar.test/classic/calendar?month=2026-10')
    assert cell.locator('.cal-event:visible').count() == 1
    more.click()
    menu.get_by_role('button', name='Mark all complete', exact=True).click()
    page.wait_for_load_state()
    assert cell.locator('.cal-day-count').inner_text().startswith('2 events · 0 remaining')
    assert cell.locator('.cal-event:visible').count() == 0
    more.click()
    assert menu.get_by_role('button', name='Mark all complete').is_disabled()
    menu.get_by_role('button', name='Show completed', exact=True).click()
    page.wait_for_load_state()
    assert cell.locator('.cal-event:visible').count() == 2
    cell.locator('.cal-day-count').click()
    dialog.locator('input[type="checkbox"]').last.uncheck()
    page.wait_for_function("document.querySelector('#calendar-day-dialog-count').textContent === '2 events · 1 remaining'")
    assert cell.locator('.cal-event-completed').count() == 1
    assert len(db.query(CalendarEventCompletion).filter_by(occurrence_at=day).all()) == 1


@pytest.mark.parametrize('close_method', ['button', 'escape', 'add_event'])
def test_closing_day_clears_day_fragment_so_refresh_keeps_it_closed(account, page, close_method):
    db, _, client = account
    db.add(UserSettings(id=1, calendar_traditional_view=True))
    db.commit()
    serve_calendar(page, client)
    calendar_url = 'http://calendar.test/classic/calendar?month=2026-10&offset=2'
    page.goto(calendar_url + '#day-2026-10-27')
    dialog = page.get_by_role('dialog', name='2026-10-27')
    assert dialog.is_visible()
    history_length = page.evaluate('history.length')
    if close_method == 'button':
        dialog.get_by_role('button', name='Close day', exact=True).click()
    elif close_method == 'escape':
        page.keyboard.press('Escape')
    else:
        dialog.get_by_role('button', name='+ Add event', exact=True).click()
    page.wait_for_function('window.location.hash === ""')
    assert page.url == calendar_url
    assert page.evaluate('history.length') == history_length
    assert not dialog.is_visible()
    page.reload()
    assert not dialog.is_visible()


def test_day_tools_track_separate_occurrences_of_the_same_event(account, page):
    db, _, client = account
    day = datetime(2026, 10, 27)
    db.add(UserSettings(id=1, calendar_traditional_view=True))
    event = CalendarEvent(title='Hourly event', start_at=day, rrule='FREQ=HOURLY;COUNT=2')
    db.add(event)
    db.commit()
    client.post('/calendar/day/2026-10-27/complete', follow_redirects=False)
    assert {row.occurrence_at for row in db.query(CalendarEventCompletion)} == {day, day + timedelta(hours=1)}
    serve_calendar(page, client)
    page.goto('http://calendar.test/classic/calendar?month=2026-10')
    cell = page.locator('.cal-day[data-date="2026-10-27"]')
    cell.locator('.cal-day-count').click()
    dialog = page.get_by_role('dialog', name='2026-10-27')
    dialog.locator('input[type="checkbox"]').last.uncheck()
    page.wait_for_function("document.querySelector('#calendar-day-dialog-count').textContent === '2 events · 1 remaining'")
    assert cell.locator('input[type="checkbox"]').first.is_checked()
    assert not cell.locator('input[type="checkbox"]').last.is_checked()
    assert {row.occurrence_at for row in db.query(CalendarEventCompletion)} == {day}


def test_creating_event_preserves_displayed_month(account):
    db, _, client = account
    db.add(UserSettings(id=1, calendar_traditional_view=True))
    db.commit()
    page = client.get('/classic/calendar?month=2026-11')
    assert 'name="view_month" value="2026-11"' in page.text
    response = client.post('/calendar/create', data={
        'title': 'November event', 'start_at': '2026-11-15', 'view_month': '2026-11',
    }, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'] == '/classic/calendar?month=2026-11'
    assert db.query(CalendarEvent).filter_by(title='November event').one().start_at == datetime(2026, 11, 15)


def test_editing_event_preserves_displayed_month(account):
    db, _, client = account
    db.add(UserSettings(id=1, calendar_traditional_view=True))
    event = CalendarEvent(title='Recurring event', start_at=datetime(2026, 1, 1), rrule='FREQ=MONTHLY')
    db.add(event)
    db.commit()
    page = client.get('/classic/calendar?month=2026-11')
    edit_url = f'/calendar/event/{event.id}/edit?view_month=2026-11'
    assert edit_url in page.text
    edit_page = client.get(edit_url)
    assert 'name="view_month" value="2026-11"' in edit_page.text
    response = client.post(f'/calendar/event/{event.id}/update', data={
        'title': 'Updated event', 'start_at': '2026-01-01', 'rrule': 'FREQ=MONTHLY',
        'view_month': '2026-11',
    }, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'] == '/classic/calendar?month=2026-11'
    assert event.title == 'Updated event'

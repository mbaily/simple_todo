import copy
from datetime import datetime

import pytest
from sqlalchemy import create_engine, text

from database import run_migrations
from import_logic import import_data_from_json
from models import CalendarEvent, CalendarEventCompletion, UserSettings
from test_billing_accounts import account
from test_calendar_grid import serve_calendar


@pytest.mark.parametrize('start_time,duration,expected_all_day', [
    ('09:15', '', False), ('09:15', '90', False), ('00:00', '', False), ('', '', True),
])
def test_time_and_optional_duration_survive_edit_export_and_restore(account, start_time, duration, expected_all_day):
    db, _, client = account
    form = dict(title='Appointment', start_at='2026-10-28', start_time=start_time,
                duration_minutes=duration, view_week='2026-10-26')
    response = client.post('/calendar/create', data=form, follow_redirects=False)
    assert response.status_code == 303 and response.headers['location'] == '/classic/calendar?week=2026-10-26'
    event = db.query(CalendarEvent).one()
    assert event.start_at == datetime.fromisoformat('2026-10-28T' + (start_time or '00:00'))
    assert event.all_day == expected_all_day
    assert event.duration_minutes == (int(duration) if duration else None)
    edit = client.get(f'/calendar/event/{event.id}/edit?view_week=2026-10-26')
    assert f'name="start_time" step="60" value="{start_time}"' in edit.text
    assert 'name="view_week" value="2026-10-26"' in edit.text
    exported = client.get('/api/export').json()
    assert import_data_from_json(db, copy.deepcopy(exported))['errors'] == 0
    assert client.get('/api/export').json()['data'] == exported['data']
    form.update(start_time='', duration_minutes='')
    assert client.post(f'/calendar/event/{event.id}/update', data=form).status_code == 200
    assert event.start_at == datetime(2026, 10, 28) and event.all_day and event.duration_minutes is None


@pytest.mark.parametrize('extra', [dict(start_time='25:00'), dict(start_time='9:00'),
    dict(duration_minutes='-1'), dict(duration_minutes='0'), dict(duration_minutes='1.5'),
    dict(duration_minutes='10081')])
def test_invalid_time_or_duration_does_not_save(account, extra):
    db, _, client = account
    form = dict(title='Invalid', start_at='2026-10-28', start_time='09:00')
    form.update(extra)
    assert client.post('/calendar/create', data=form).status_code == 400
    assert db.query(CalendarEvent).count() == 0


def test_week_spans_year_boundary_recurs_at_time_and_includes_overnight_events(account):
    db, _, client = account
    db.add(UserSettings(id=1, calendar_weekly_view=True))
    db.add_all([
        CalendarEvent(title='All day', start_at=datetime(2026, 12, 28)),
        CalendarEvent(title='Midnight appointment', start_at=datetime(2026, 12, 28), all_day=False),
        CalendarEvent(title='Morning', start_at=datetime(2026, 12, 20, 9, 15), all_day=False,
                      duration_minutes=45, rrule='FREQ=DAILY'),
        CalendarEvent(title='Overnight', start_at=datetime(2026, 12, 27, 23, 30), all_day=False, duration_minutes=120),
        CalendarEvent(title='Sunday late', start_at=datetime(2027, 1, 3, 23, 59), all_day=False),
        CalendarEvent(title='Next Monday', start_at=datetime(2027, 1, 4), all_day=False),
    ])
    db.commit()
    response = client.get('/classic/calendar?week=2027-01-01')
    assert response.status_code == 200
    assert response.context['current_week_str'] == '2026-12-28'
    days = response.context['week_days']
    assert [row['event'].title for row in days[0]['all_day']] == ['All day']
    assert {row['event'].title for row in days[0]['timed']} == {'Morning', 'Midnight appointment', 'Overnight'}
    assert next(row for row in days[0]['timed'] if row['event'].title == 'Overnight')['continues_before']
    mornings = [row for day in days for row in day['timed'] if row['event'].title == 'Morning']
    assert len(mornings) == 7
    assert all(row['occurrence_at'].hour == 9 and row['occurrence_at'].minute == 15 for row in mornings)
    assert all(row['end_minute'] - row['start_minute'] == 45 for row in mornings)
    assert 'Next Monday' not in response.text and 'Sunday late' in response.text
    assert client.get('/classic/calendar?week=broken').status_code == 400


def test_week_selection_filter_and_sort_persist_and_keep_navigation(account):
    db, _, client = account
    response = client.post('/calendar/set-view', data={'view': 'week'})
    assert response.status_code == 200 and response.context['weekly_view']
    assert client.get('/classic/calendar').context['weekly_view']
    for path, form in [('/calendar/set-filter', {'calendar_event_filter': 'recurring'}),
                       ('/calendar/set-sort', {'calendar_sort_order': 'title_asc'})]:
        form['week'] = '2026-12-28'
        response = client.post(path, data=form, follow_redirects=False)
        assert response.headers['location'] == '/classic/calendar?week=2026-12-28'
    exported = client.get('/api/export').json()['data']['user_settings'][0]
    assert exported['calendar_weekly_view'] and exported['calendar_event_filter'] == 'recurring'
    assert client.post('/calendar/set-view', data={'view': 'bogus'}).status_code == 400
    assert client.post('/calendar/set-view', data={'view': 'month'}).context['traditional_view']
    assert not db.get(UserSettings, 1).calendar_weekly_view


def test_old_calendar_events_migrate_as_all_day_with_no_duration():
    engine = create_engine('sqlite://')
    try:
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE calendar_events (id INTEGER PRIMARY KEY, start_at DATETIME)'))
            connection.execute(text("INSERT INTO calendar_events VALUES (1, '2026-10-28 00:00:00')"))
        run_migrations(engine)
        run_migrations(engine)
        with engine.connect() as connection:
            assert connection.execute(text('SELECT all_day, duration_minutes FROM calendar_events')).one() == (1, None)
    finally:
        engine.dispose()


def test_week_browser_slots_overlaps_completion_and_all_day_row(account, page):
    db, _, client = account
    db.add(UserSettings(id=1, calendar_weekly_view=True))
    db.add_all([
        CalendarEvent(title='All-day reminder', start_at=datetime(2026, 10, 26)),
        CalendarEvent(title='Long appointment', start_at=datetime(2026, 10, 26, 9), all_day=False, duration_minutes=120),
        CalendarEvent(title='Overlapping appointment', start_at=datetime(2026, 10, 26, 9, 30), all_day=False, duration_minutes=60),
        CalendarEvent(title='No duration', start_at=datetime(2026, 10, 26, 11, 15), all_day=False),
    ])
    db.commit()
    serve_calendar(page, client)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    for width in (390, 1280):
        page.set_viewport_size({'width': width, 'height': 900})
        page.goto('http://calendar.test/classic/calendar?week=2026-10-26')
        assert page.locator('.cal-week-heading').count() == 8
        assert page.locator('.cal-week-slot').count() == 7 * 24
        assert page.locator('.cal-week-time-labels > div').all_text_contents() == [f'{hour:02d}:00' for hour in range(24)]
        assert page.locator('.cal-week-all-day').get_by_text('All-day reminder').count() == 1
        first = page.locator('.cal-week-event-timed').filter(has_text='Long appointment').bounding_box()
        second = page.locator('.cal-week-event-timed').filter(has_text='Overlapping appointment').bounding_box()
        assert first['x'] + first['width'] <= second['x'] + 1
        assert abs(first['height'] / second['height'] - 2) < .05
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        # The complete day uses page scrolling, with horizontal scrolling on phones.
        layout = page.locator('.cal-week-scroll').evaluate('''el => {
            el.scrollTop = 700;
            return { height: el.clientHeight, contentHeight: el.scrollHeight,
                     scrollTop: el.scrollTop, horizontalOverflow: el.scrollWidth > el.clientWidth };
        }''')
        assert layout['height'] == layout['contentHeight']
        assert layout['scrollTop'] == 0
        assert layout['horizontalOverflow'] == (width == 390)
        footer = page.locator('.cal-week-grid').evaluate('''grid => {
            const line = getComputedStyle(grid, '::after');
            const style = getComputedStyle(grid);
            return { border: line.borderTopWidth, borderStyle: line.borderTopStyle,
                     lineTop: grid.getBoundingClientRect().bottom - parseFloat(style.paddingBottom) - parseFloat(line.borderTopWidth),
                     slotBottom: grid.querySelector('.cal-week-slot[data-time="23:00"]').getBoundingClientRect().bottom };
        }''')
        assert footer['border'] == '1px' and footer['borderStyle'] == 'solid'
        assert abs(footer['lineTop'] - footer['slotBottom']) < 1
        page.locator('.cal-week-slot[data-date="2026-10-26"][data-time="23:00"]').scroll_into_view_if_needed()
        assert page.evaluate('window.scrollY') > 0
    checkbox = page.locator('.cal-week-event').filter(has_text='Overlapping appointment').get_by_role('checkbox')
    checkbox.check()
    page.wait_for_function("document.querySelector('.cal-week-event-completed') !== null")
    assert db.query(CalendarEventCompletion).one().occurrence_at == datetime(2026, 10, 26, 9, 30)
    page.locator('.cal-week-slot[data-date="2026-10-27"][data-time="10:00"]').click()
    form = page.locator('#create-event-form')
    assert form.locator('[name="start_at"]').input_value() == '2026-10-27'
    assert form.locator('[name="start_time"]').input_value() == '10:00'
    assert form.locator('[name="duration_minutes"]').input_value() == ''
    form.locator('[name="title"]').fill('Created from slot')
    form.locator('[data-rrule-field="freq"]').select_option('DAILY')
    form.locator('[data-rrule-field="until"]').fill('2026-10-29')
    form.get_by_role('button', name='Create', exact=True).click()
    page.wait_for_load_state()
    created = db.query(CalendarEvent).filter_by(title='Created from slot').one()
    assert created.start_at == datetime(2026, 10, 27, 10) and created.duration_minutes is None
    assert not errors
    page.goto('http://calendar.test/classic/calendar?week=2026-10-26')
    assert page.locator('.cal-week-event').filter(has_text='Created from slot').count() == 3
    page.evaluate('async () => { await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame); }')
    page.screenshot(path='/tmp/simple-todo-calendar-week.png')

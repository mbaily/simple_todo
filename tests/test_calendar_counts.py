from datetime import datetime

from sqlalchemy import create_engine, text

from database import run_migrations
from import_logic import import_data_from_json
from models import CalendarEvent, UserSettings
from test_billing_accounts import account
from test_calendar_grid import serve_calendar


def test_count_visibility_is_saved_and_preserves_navigation_and_other_settings(account):
    db, _, client = account
    db.add(UserSettings(id=1, calendar_traditional_view=True, calendar_word_wrap=True))
    db.commit()
    assert db.get(UserSettings, 1).calendar_show_counts is True
    response = client.post('/calendar/toggle-counts', data={'month': '2027-04'}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'] == '/classic/calendar?month=2027-04'
    assert db.get(UserSettings, 1).calendar_show_counts is False
    assert db.get(UserSettings, 1).calendar_word_wrap is True
    response = client.get('/classic/calendar?month=2027-04')
    assert 'aria-label="Show daily event counts" aria-pressed="false"' in response.text
    exported_settings = client.get('/api/export').json()['data']['user_settings']
    assert exported_settings[0]['calendar_show_counts'] is False
    client.post('/calendar/toggle-counts', data={'offset': -2}, follow_redirects=False)
    assert db.get(UserSettings, 1).calendar_show_counts is True
    assert import_data_from_json(db, {'data': {'user_settings': exported_settings}})['errors'] == 0
    assert db.get(UserSettings, 1).calendar_show_counts is False
    response = client.post('/calendar/toggle-counts', data={'offset': -2}, follow_redirects=False)
    assert response.headers['location'] == '/classic/calendar?offset=-2'


def test_existing_settings_migrate_with_counts_visible():
    engine = create_engine('sqlite://')
    try:
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE user_settings (id INTEGER PRIMARY KEY)'))
            connection.execute(text('INSERT INTO user_settings (id) VALUES (1)'))
        run_migrations(engine)
        with engine.connect() as connection:
            assert connection.execute(text('SELECT calendar_show_counts FROM user_settings')).scalar_one() == 1
    finally:
        engine.dispose()


def test_counts_toggle_hides_summaries_and_keeps_columns_and_day_actions(account, page):
    db, _, client = account
    db.add(UserSettings(id=1, calendar_traditional_view=True))
    db.add(CalendarEvent(title='A longer event title ' * 5, start_at=datetime(2026, 10, 27)))
    db.commit()
    serve_calendar(page, client)
    for width in (320, 390, 1280):
        page.set_viewport_size({'width': width, 'height': 844})
        page.goto('http://calendar.test/classic/calendar?month=2026-10')
        toggle = page.get_by_role('button', name='Show daily event counts', exact=True)
        assert toggle.get_attribute('aria-pressed') == 'true'
        assert page.locator('.cal-day-count:visible').count() == 31
        columns = page.locator('.cal-grid').evaluate('grid => getComputedStyle(grid).gridTemplateColumns')
        toggle.click()
        page.wait_for_load_state()
        assert toggle.get_attribute('aria-pressed') == 'false'
        assert page.locator('.cal-day-count:visible').count() == 0
        assert page.locator('.cal-grid').evaluate('grid => getComputedStyle(grid).gridTemplateColumns') == columns
        assert page.locator('.cal-day-number').count() == 31
        page.goto('http://calendar.test/classic/calendar?month=2026-10')
        assert toggle.get_attribute('aria-pressed') == 'false'
        assert page.locator('.cal-day-count:visible').count() == 0
        page.get_by_role('button', name='Open day 2026-10-27', exact=True).click()
        dialog = page.get_by_role('dialog', name='2026-10-27')
        assert dialog.locator('#calendar-day-dialog-count').inner_text() == '1 event · 1 remaining'
        dialog.get_by_role('button', name='Close day', exact=True).click()
        toggle.click()
        page.wait_for_load_state()
        assert page.locator('.cal-day-count:visible').count() == 31

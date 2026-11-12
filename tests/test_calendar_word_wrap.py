from sqlalchemy import create_engine, text

from database import run_migrations
from models import UserSettings
from test_billing_accounts import account


def test_word_wrap_is_saved_exported_and_preserves_navigation(account):
    db, _, client = account
    client.get('/classic/calendar')
    assert db.get(UserSettings, 1).calendar_word_wrap is False
    response = client.post('/calendar/toggle-word-wrap', data={'month': '2027-04'}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'] == '/classic/calendar?month=2027-04'
    assert db.get(UserSettings, 1).calendar_word_wrap is True
    assert client.get('/api/export').json()['data']['user_settings'][0]['calendar_word_wrap'] is True
    response = client.post('/calendar/toggle-view', follow_redirects=True)
    assert 'cal-grid-wrap' in response.text
    assert 'aria-label="Wrap event titles" aria-pressed="true"' in response.text
    response = client.post('/calendar/toggle-word-wrap', data={'offset': -2}, follow_redirects=False)
    assert response.headers['location'] == '/classic/calendar?offset=-2'
    assert db.get(UserSettings, 1).calendar_word_wrap is False


def test_existing_settings_migrate_with_word_wrap_disabled():
    engine = create_engine('sqlite://')
    try:
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE user_settings (id INTEGER PRIMARY KEY)'))
            connection.execute(text('INSERT INTO user_settings (id) VALUES (1)'))
        run_migrations(engine)
        with engine.connect() as connection:
            assert connection.execute(text('SELECT calendar_word_wrap FROM user_settings')).scalar_one() == 0
    finally:
        engine.dispose()

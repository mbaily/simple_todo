from datetime import datetime, timedelta
from fastapi.testclient import TestClient


def test_calendar_non_monthly_view_displays_day_abbreviation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SIMPLE_TODO_DB_PATH", "calendar_day_abbr.db")

    import database
    database._engines.clear()

    import main
    from models import CalendarEvent, UserSettings
    from helpers import format_configurable_date

    now = datetime.now()

    with TestClient(main.app) as client:
        session = database.get_session_maker("default")()
        try:
            # Ensure non-monthly (list) view
            settings = session.get(UserSettings, 1)
            if not settings:
                settings = UserSettings(id=1, calendar_traditional_view=False)
                session.add(settings)
            else:
                settings.calendar_traditional_view = False

            # Create events on consecutive days
            event1_date = now
            event2_date = now + timedelta(days=1)
            event3_date = now + timedelta(days=2)

            session.add_all([
                CalendarEvent(title="Alpha Event", start_at=event1_date),
                CalendarEvent(title="Beta Event", start_at=event2_date),
                CalendarEvent(title="Gamma Event", start_at=event3_date),
            ])
            session.commit()
        finally:
            session.close()

        response = client.get("/classic/calendar")
        assert response.status_code == 200

        day1_abbr = event1_date.strftime("%a")
        day2_abbr = event2_date.strftime("%a")
        day3_abbr = event3_date.strftime("%a")

        date1_str = format_configurable_date(event1_date)
        date2_str = format_configurable_date(event2_date)
        date3_str = format_configurable_date(event3_date)

        # Verify each event displays its 3-letter day abbreviation before the date
        assert f"{day1_abbr} {date1_str}" in response.text
        assert f"{day2_abbr} {date2_str}" in response.text
        assert f"{day3_abbr} {date3_str}" in response.text

        # Verify full span structure
        assert f'<span class="dates calendar-occurrence-date">{day1_abbr} {date1_str}</span>' in response.text
        assert f'<span class="dates calendar-occurrence-date">{day2_abbr} {date2_str}</span>' in response.text
        assert f'<span class="dates calendar-occurrence-date">{day3_abbr} {date3_str}</span>' in response.text

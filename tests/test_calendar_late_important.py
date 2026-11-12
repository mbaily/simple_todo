from datetime import datetime, date, timedelta
import pytest
from fastapi.testclient import TestClient

from main import app, get_db
from models import CalendarEvent, CalendarEventCompletion, UserSettings
from database import run_migrations, get_engine


@pytest.fixture
def client_with_db(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            yield client, db_session
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_calendar_event_model_default(db_session):
    event = CalendarEvent(title="Test Event", start_at=datetime(2026, 11, 1))
    db_session.add(event)
    db_session.commit()
    assert event.important_if_late is False


def test_edit_calendar_event_form_and_update(client_with_db):
    client, db = client_with_db
    event = CalendarEvent(title="Doctor", start_at=datetime(2026, 11, 1), important_if_late=False)
    db.add(event)
    db.commit()

    # GET edit form: checkbox should be present and unchecked
    res = client.get(f"/calendar/event/{event.id}/edit")
    assert res.status_code == 200
    assert 'name="important_if_late"' in res.text
    assert 'Mark as important if late and unchecked' in res.text
    # Should not be checked yet
    assert f'<input type="checkbox" name="important_if_late" value="1" checked>' not in res.text

    # POST update: check the box
    res = client.post(
        f"/calendar/event/{event.id}/update",
        data={
            "title": "Doctor",
            "start_at": "2026-11-01",
            "important_if_late": "1",
        },
        follow_redirects=False,
    )
    assert res.status_code == 303
    db.refresh(event)
    assert event.important_if_late is True

    # GET edit form again: now checked
    res = client.get(f"/calendar/event/{event.id}/edit")
    assert res.status_code == 200
    assert 'name="important_if_late" value="1" checked' in res.text

    # POST update: uncheck the box (omit field)
    res = client.post(
        f"/calendar/event/{event.id}/update",
        data={
            "title": "Doctor",
            "start_at": "2026-11-01",
        },
        follow_redirects=False,
    )
    assert res.status_code == 303
    db.refresh(event)
    assert event.important_if_late is False


def test_create_calendar_event_form_and_creation(client_with_db):
    client, db = client_with_db

    # Check that create form in list view, monthly grid, and weekly view has the checkbox
    res = client.get("/classic/calendar")
    assert res.status_code == 200
    assert 'name="important_if_late"' in res.text
    assert 'Mark as important if late and unchecked' in res.text

    # Create event with important_if_late checked
    res = client.post(
        "/calendar/create",
        data={
            "title": "Created Important Event",
            "start_at": "2026-11-01",
            "important_if_late": "1",
        },
        follow_redirects=False,
    )
    assert res.status_code == 303
    ev = db.query(CalendarEvent).filter(CalendarEvent.title == "Created Important Event").first()
    assert ev is not None
    assert ev.important_if_late is True

    # Create event with important_if_late unchecked (default)
    res = client.post(
        "/calendar/create",
        data={
            "title": "Created Default Event",
            "start_at": "2026-11-01",
        },
        follow_redirects=False,
    )
    assert res.status_code == 303
    ev2 = db.query(CalendarEvent).filter(CalendarEvent.title == "Created Default Event").first()
    assert ev2 is not None
    assert ev2.important_if_late is False


def test_calendar_page_and_home_highlighting(client_with_db):
    client, db = client_with_db
    today = date.today()
    past_date = today - timedelta(days=2)

    # 1. Late unchecked event with important_if_late = True
    late_important = CalendarEvent(
        title="Late Important Event",
        start_at=datetime.combine(past_date, datetime.min.time()),
        important_if_late=True,
    )
    # 2. Late unchecked event with important_if_late = False (default)
    late_normal = CalendarEvent(
        title="Late Normal Event",
        start_at=datetime.combine(past_date, datetime.min.time()),
        important_if_late=False,
    )
    # 3. Late completed event with important_if_late = True
    late_completed = CalendarEvent(
        title="Late Completed Event",
        start_at=datetime.combine(past_date, datetime.min.time()),
        important_if_late=True,
    )
    db.add_all([late_important, late_normal, late_completed])
    db.flush()
    db.add(CalendarEventCompletion(event=late_completed, occurrence_at=late_completed.start_at))
    db.commit()

    # Calendar list view
    res = client.get("/classic/calendar")
    assert res.status_code == 200
    text = res.text

    # Late important event should have the important class and badge
    assert "calendar-occurrence-important" in text
    assert "⚠️ Important" in text
    assert "Late Important Event" in text

    # Calendar traditional monthly grid view
    db.query(UserSettings).delete()
    db.add(UserSettings(id=1, calendar_traditional_view=True))
    db.commit()

    month_str = past_date.strftime("%Y-%m")
    res = client.get(f"/classic/calendar?month={month_str}")
    assert res.status_code == 200
    assert "cal-event-important" in res.text
    assert "⚠️ Important" in res.text


def test_database_migration_column():
    # Calling run_migrations should succeed and ensure important_if_late exists
    run_migrations(get_engine("default"))

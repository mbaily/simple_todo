from datetime import datetime, date, timedelta
import pytest
from fastapi.testclient import TestClient

from main import app, get_db
from models import CalendarEvent, CalendarEventCompletion, Hashtag, Note


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


def test_duplicate_calendar_event_endpoint_all_day(client_with_db):
    client, db = client_with_db

    tag = Hashtag(name="important_tag")
    db.add(tag)
    db.flush()

    orig = CalendarEvent(
        title="Dentist Visit",
        note="Bring insurance card",
        start_at=datetime(2026, 11, 1),
        all_day=True,
        rrule="FREQ=MONTHLY",  # Recurring original
        color="#ff0000",
        important_if_late=True,
    )
    orig.hashtags.append(tag)
    db.add(orig)
    db.flush()

    # Add child note and completion to original
    db.add(Note(calendar_event_id=orig.id, text="Subnote 1"))
    db.add(CalendarEventCompletion(event=orig, occurrence_at=orig.start_at))
    db.commit()

    # Duplicate 30 days into future (to 2026-12-01)
    target = "2026-12-01"
    res = client.post(
        f"/calendar/event/{orig.id}/duplicate",
        data={
            "target_date": target,
            "return_to": "edit",
        },
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert f"/calendar/event/{orig.id}/edit?" in res.headers["location"]
    assert "duplicated_id=" in res.headers["location"]

    # Verify duplicate in DB
    dup = db.query(CalendarEvent).filter(CalendarEvent.title == "Dentist Visit", CalendarEvent.id != orig.id).first()
    assert dup is not None
    assert dup.start_at.strftime("%Y-%m-%d") == "2026-12-01"
    assert dup.all_day is True
    assert dup.note == "Bring insurance card"
    assert dup.color == "#ff0000"
    assert dup.important_if_late is True
    # RRULE should be stripped (Option A: standalone one-time copy)
    assert dup.rrule is None
    # Hashtags copied
    assert [h.name for h in dup.hashtags] == ["important_tag"]
    # Child notes NOT copied
    assert len(dup.notes) == 0
    # Completions NOT copied
    assert len(dup.completions) == 0

    # Follow redirect to edit page: success banner should be present
    edit_page_res = client.get(res.headers["location"])
    assert edit_page_res.status_code == 200
    assert "Event duplicated successfully for 2026-12-01!" in edit_page_res.text
    assert f"/calendar/event/{dup.id}/edit" in edit_page_res.text


def test_duplicate_calendar_event_timed(client_with_db):
    client, db = client_with_db

    orig = CalendarEvent(
        title="Team Standup",
        note="Room 3B",
        start_at=datetime(2026, 11, 1, 14, 30),
        all_day=False,
        duration_minutes=45,
    )
    db.add(orig)
    db.commit()

    # Duplicate to calendar view
    target = "2026-11-20"
    res = client.post(
        f"/calendar/event/{orig.id}/duplicate",
        data={
            "target_date": target,
            "return_to": "calendar",
        },
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert "/classic/calendar?" in res.headers["location"]
    assert "duplicated_id=" in res.headers["location"]

    dup = db.query(CalendarEvent).filter(CalendarEvent.title == "Team Standup", CalendarEvent.id != orig.id).first()
    assert dup is not None
    # Should retain the exact same time (14:30)
    assert dup.start_at == datetime(2026, 11, 20, 14, 30)
    assert dup.all_day is False
    assert dup.duration_minutes == 45

    # Follow redirect to calendar: success banner should be present
    cal_res = client.get(res.headers["location"])
    assert cal_res.status_code == 200
    assert "Duplicated “Team Standup” to 2026-11-20!" in cal_res.text
    assert f"/calendar/event/{dup.id}/edit" in cal_res.text


def test_edit_page_and_calendar_page_contain_duplicate_ui(client_with_db):
    client, db = client_with_db

    event = CalendarEvent(title="Test Event", start_at=datetime.now())
    db.add(event)
    db.commit()

    # Edit event page contains duplicate section
    res = client.get(f"/calendar/event/{event.id}/edit")
    assert res.status_code == 200
    assert "Duplicate Event in the Future" in res.text
    assert f"/calendar/event/{event.id}/duplicate" in res.text
    assert "duplicate-days" in res.text
    assert "duplicate-target-date" in res.text

    # Calendar list view contains Duplicate button and form in operations
    res2 = client.get("/classic/calendar")
    assert res2.status_code == 200
    assert "showDuplicateForm()" in res2.text
    assert "id=\"duplicate-form\"" in res2.text


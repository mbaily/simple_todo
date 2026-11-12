from datetime import datetime
from models import CalendarEvent, Note, UserSettings
from test_billing_accounts import account, day


def test_edit_calendar_event_occurrence_notes(account):
    db, _, client = account
    event = CalendarEvent(
        title="Daily Standup",
        start_at=day("2026-11-01"),
        rrule="FREQ=DAILY",
    )
    db.add(event)
    db.commit()

    # 1. Access edit page with occurrence_at
    res = client.get(f"/calendar/event/{event.id}/edit?occurrence_at=2026-11-15")
    assert res.status_code == 200
    assert "This occurrence only (2026-11-15)" in res.text
    assert 'value="2026-11-15"' in res.text
    assert 'id="edit-note-occurrence-only"' in res.text
    assert 'checked' in res.text

    # 2. Add an occurrence-scoped note
    create_res = client.post(
        "/note/create",
        data={
            "calendar_event_id": event.id,
            "name": "Meeting updates",
            "text": "Discussed Q4 roadmap today",
            "occurrence_at": "2026-11-15",
            "return_to": f"/calendar/event/{event.id}/edit?occurrence_at=2026-11-15",
        },
        follow_redirects=False,
    )
    assert create_res.status_code == 303
    assert create_res.headers["location"] == f"/calendar/event/{event.id}/edit?occurrence_at=2026-11-15"

    # Verify note in DB
    note = db.query(Note).filter(Note.calendar_event_id == event.id).first()
    assert note is not None
    assert note.name == "Meeting updates"
    assert note.text == "Discussed Q4 roadmap today"
    assert note.occurrence_at == datetime(2026, 11, 15)

    # 3. View the edit page again and verify note displays with occurrence date
    res2 = client.get(f"/calendar/event/{event.id}/edit?occurrence_at=2026-11-15")
    assert res2.status_code == 200
    assert "Meeting updates:" in res2.text
    assert "Discussed Q4 roadmap today" in res2.text
    assert "(occurrence: 2026-11-15)" in res2.text

    # 4. Add a series-wide note (occurrence_at not checked / not provided)
    create_series_res = client.post(
        "/note/create",
        data={
            "calendar_event_id": event.id,
            "text": "General series info",
            "occurrence_at": "",
            "return_to": f"/calendar/event/{event.id}/edit",
        },
        follow_redirects=False,
    )
    assert create_series_res.status_code == 303
    assert create_series_res.headers["location"] == f"/calendar/event/{event.id}/edit"

    series_note = db.query(Note).filter(Note.text == "General series info").first()
    assert series_note is not None
    assert series_note.occurrence_at is None

    # 5. Delete note with return_to
    del_res = client.post(
        f"/note/{note.id}/delete",
        data={"return_to": f"/calendar/event/{event.id}/edit?occurrence_at=2026-11-15"},
        follow_redirects=False,
    )
    assert del_res.status_code == 303
    assert del_res.headers["location"] == f"/calendar/event/{event.id}/edit?occurrence_at=2026-11-15"
    assert db.get(Note, note.id) is None


def test_calendar_links_contain_occurrence_at(account):
    db, _, client = account
    settings = db.query(UserSettings).first()
    if not settings:
        settings = UserSettings(id=1)
        db.add(settings)
    settings.calendar_traditional_view = True
    settings.calendar_weekly_view = False
    db.commit()

    event = CalendarEvent(
        title="Weekly Sync",
        start_at=day("2026-11-02"),
        rrule="FREQ=WEEKLY;BYDAY=MO",
        note="Standup note",
    )
    db.add(event)
    db.commit()

    # 1. Monthly grid view
    month_res = client.get("/classic/calendar?month=2026-11")
    assert month_res.status_code == 200
    assert f"/calendar/event/{event.id}/edit?view_month=2026-11&amp;occurrence_at=2026-11-02" in month_res.text or \
           f"/calendar/event/{event.id}/edit?view_month=2026-11&occurrence_at=2026-11-02" in month_res.text

    # 2. Weekly view
    settings.calendar_weekly_view = True
    db.commit()
    week_res = client.get("/classic/calendar?week=2026-11-02")
    assert week_res.status_code == 200
    assert f"/calendar/event/{event.id}/edit?view_week=2026-11-02&amp;occurrence_at=2026-11-02" in week_res.text or \
           f"/calendar/event/{event.id}/edit?view_week=2026-11-02&occurrence_at=2026-11-02" in week_res.text

    # 3. List view
    settings.calendar_traditional_view = False
    settings.calendar_weekly_view = False
    db.commit()

    today_str = datetime.now().strftime("%Y-%m-%d")
    daily_event = CalendarEvent(
        title="Daily Standup Today",
        start_at=datetime.now(),
        rrule="FREQ=DAILY",
        note="Standup note",
    )
    db.add(daily_event)
    db.commit()

    list_res = client.get("/classic/calendar")
    assert list_res.status_code == 200
    assert f'data-occurrence="{today_str}"' in list_res.text
    assert f"/calendar/event/{daily_event.id}/edit?occurrence_at={today_str}" in list_res.text

    # 4. Index view (today/upcoming occurrences)
    index_res = client.get("/classic")
    assert index_res.status_code == 200
    assert f"/calendar/event/{daily_event.id}/edit?occurrence_at={today_str}" in index_res.text

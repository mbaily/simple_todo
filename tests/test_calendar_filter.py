from fastapi.testclient import TestClient


def test_calendar_recurrence_filter_persists_across_view_modes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SIMPLE_TODO_DB_PATH", "calendar_filter.db")

    import database
    database._engines.clear()

    import main
    from models import CalendarEvent

    with TestClient(main.app) as client:
        session = database.get_session_maker("default")()
        try:
            session.add_all([
                CalendarEvent(title="One-time event", start_at=main.datetime.now()),
                CalendarEvent(
                    title="Recurring event",
                    start_at=main.datetime.now(),
                    rrule="FREQ=WEEKLY",
                ),
            ])
            session.commit()
        finally:
            session.close()

        response = client.post(
            "/calendar/set-filter",
            data={"calendar_event_filter": "non_recurring"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert "One-time event" in response.text
        assert "Recurring event" not in response.text

        response = client.post("/calendar/toggle-view", follow_redirects=True)
        assert response.status_code == 200
        assert "One-time event" in response.text
        assert "Recurring event" not in response.text

        session = database.get_session_maker("default")()
        try:
            assert session.get(main.UserSettings, 1).calendar_event_filter == "non_recurring"
        finally:
            session.close()


def test_event_group_can_be_assigned_filtered_and_deleted(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SIMPLE_TODO_DB_PATH", "event_groups.db")

    import database
    database._engines.clear()

    import main
    from models import CalendarEvent, EventGroup, UserSettings

    with TestClient(main.app) as client:
        response = client.post(
            "/calendar/groups/create",
            data={"name": "Appointments"},
            follow_redirects=False,
        )
        assert response.status_code == 303

        session = database.get_session_maker("default")()
        try:
            group = session.query(EventGroup).filter_by(name="Appointments").one()
            group_id = group.id
        finally:
            session.close()

        for title, selected_group in [("Doctor appointment", str(group_id)), ("Shopping trip", "")]:
            response = client.post(
                "/calendar/create",
                data={
                    "title": title,
                    "start_at": main.datetime.now().date().isoformat(),
                    "event_group_id": selected_group,
                },
                follow_redirects=False,
            )
            assert response.status_code == 303

        response = client.get("/calendar/event/1/edit")
        assert response.status_code == 200
        assert 'value="%s" selected' % group_id in response.text

        response = client.post(
            "/calendar/set-filter",
            data={"calendar_event_filter": f"group:{group_id}"},
            follow_redirects=True,
        )
        assert "Doctor appointment" in response.text
        assert "Shopping trip" not in response.text

        create_form = response.text.split('id="create-event-form"', 1)[1].split('</form>', 1)[0]
        assert 'name="event_group_id"' in create_form
        assert '<option value="">None</option>' in create_form
        assert f'<option value="{group_id}">Appointments</option>' in create_form

        response = client.post("/calendar/toggle-view", follow_redirects=True)
        assert "Doctor appointment" in response.text
        assert "Shopping trip" not in response.text

        create_form = response.text.split('id="create-event-form"', 1)[1].split('</form>', 1)[0]
        assert 'name="event_group_id"' in create_form
        assert '<option value="">None</option>' in create_form
        assert f'<option value="{group_id}">Appointments</option>' in create_form

        response = client.post(
            f"/calendar/groups/{group_id}/delete",
            follow_redirects=False,
        )
        assert response.status_code == 303

        session = database.get_session_maker("default")()
        try:
            event = session.query(CalendarEvent).filter_by(title="Doctor appointment").one()
            assert event.event_group_id is None
            assert session.get(UserSettings, 1).calendar_event_filter == "all"
        finally:
            session.close()


def test_calendar_create_rejects_invalid_group_without_saving(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SIMPLE_TODO_DB_PATH", "invalid_event_group.db")

    import database
    database._engines.clear()

    import main
    from models import CalendarEvent, Hashtag, JournalEntry

    with TestClient(main.app) as client:
        for group_id, error in [("invalid", "Invalid event group"), ("999999", "Event group not found")]:
            response = client.post(
                "/calendar/create",
                data={
                    "title": "Must not save",
                    "start_at": "2026-10-21",
                    "hashtags": "#must-not-save",
                    "event_group_id": group_id,
                },
                follow_redirects=False,
            )
            assert response.status_code == 400
            assert response.json()["detail"] == error

            session = database.get_session_maker("default")()
            try:
                assert session.query(CalendarEvent).count() == 0
                assert session.query(Hashtag).count() == 0
                assert session.query(JournalEntry).count() == 0
            finally:
                session.close()

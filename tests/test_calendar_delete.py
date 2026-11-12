from datetime import datetime, timedelta

import pytest

from models import CalendarEvent, CalendarEventCompletion, EventGroup, Hashtag, JournalEntry, Note, UserSettings
from test_billing_accounts import account
from test_calendar_grid import serve_calendar


@pytest.mark.parametrize('navigation,expected', [
    ({}, '/classic/calendar'),
    ({'view_week': '2026-10-26'}, '/classic/calendar?week=2026-10-26'),
    ({'view_month': '2026-11'}, '/classic/calendar?month=2026-11'),
])
def test_delete_event_removes_series_notes_and_completions_and_preserves_other_records(account, navigation, expected):
    db, _, client = account
    date = datetime(2026, 10, 26, 9)
    group = EventGroup(name='Appointments')
    tag = Hashtag(name='shared')
    event = CalendarEvent(title='Delete series', start_at=date, rrule='FREQ=WEEKLY',
                          event_group=group, hashtags=[tag])
    other = CalendarEvent(title='Keep event', start_at=date, event_group=group, hashtags=[tag])
    db.add_all([event, other])
    db.flush()
    event_id, other_id = event.id, other.id
    db.add_all([
        Note(calendar_event=event, text='Series note'),
        Note(calendar_event=event, text='Occurrence note', occurrence_at=date),
        CalendarEventCompletion(event=event, occurrence_at=date),
        CalendarEventCompletion(event=event, occurrence_at=date + timedelta(weeks=1)),
        Note(calendar_event=other, text='Keep note'),
        CalendarEventCompletion(event=other, occurrence_at=date),
    ])
    db.commit()
    response = client.post(f'/calendar/event/{event_id}/delete', data=navigation, follow_redirects=False)
    assert response.status_code == 303 and response.headers['location'] == expected
    assert db.get(CalendarEvent, event_id) is None
    assert db.get(CalendarEvent, other_id) is not None
    assert db.query(Note).one().text == 'Keep note'
    assert db.query(CalendarEventCompletion).one().event_id == other_id
    assert db.query(Hashtag).one().id == tag.id
    assert db.query(EventGroup).one().id == group.id
    assert db.query(JournalEntry).filter_by(action='Deleted Calendar Event', entity_id=event_id).count() == 1
    assert client.post(f'/calendar/event/{event_id}/delete').status_code == 404
    assert db.query(CalendarEvent).count() == 1


@pytest.mark.parametrize('recurring', [False, True])
def test_edit_delete_confirmation_can_cancel_and_confirm_without_saving_changes(account, page, recurring):
    db, _, client = account
    event = CalendarEvent(title='An event with "quotes" & <markup>', start_at=datetime(2026, 10, 26),
                          rrule='FREQ=WEEKLY' if recurring else None)
    db.add_all([event, UserSettings(id=1, calendar_weekly_view=True)])
    db.commit()
    event_id = event.id
    serve_calendar(page, client)
    errors, messages, delete_requests = [], [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('request', lambda request: delete_requests.append(request) if request.url.endswith('/delete') else None)
    page.goto(f'http://calendar.test/calendar/event/{event_id}/edit?view_week=2026-10-26')
    button = page.get_by_role('button', name='Delete Event', exact=True)
    # Invalid unsaved edits must not prevent deleting the existing event.
    page.locator('input[name="title"]').fill('')

    def dismiss(dialog):
        assert dialog.type == 'confirm'
        messages.append(dialog.message)
        dialog.dismiss()

    page.once('dialog', dismiss)
    button.click()
    assert event.title in messages[0]
    assert ('entire recurring series' in messages[0]) == recurring
    assert 'notes and completion history' in messages[0]
    assert not delete_requests
    assert db.get(CalendarEvent, event_id) is not None
    page.once('dialog', lambda dialog: dialog.accept())
    button.click()
    page.wait_for_load_state()
    assert page.locator('h2').first.inner_text() == 'Calendar'
    assert len(delete_requests) == 1 and delete_requests[0].method == 'POST'
    assert db.get(CalendarEvent, event_id) is None
    assert not errors

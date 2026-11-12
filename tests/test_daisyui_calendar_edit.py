"""Calendar event editor, notes, duplicate and event groups in the daisyUI frontend."""
from datetime import date, datetime, timedelta

import pytest
from playwright.sync_api import expect

from daisyui_test_calendar_edit import build_rule, rule_values
from models import CalendarEvent, CalendarEventCompletion, EventGroup, Hashtag, JournalEntry, Link, Note, UserSettings
from test_daisyui_test import HX, prototype, prototype_server


def form(**changes):
    data = {'title': 'Team sync', 'start_at': '2027-03-01', 'start_time': '09:30', 'duration_minutes': '45',
            'note': 'Agenda first', 'hashtags': '#work', 'repeat': 'WEEKLY', 'interval': '2', 'byday': ['MO', 'WE'],
            'until': '2027-06-30', 'custom_rule': '', 'event_group_id': '', 'use_color': 'true', 'color': '#7fb3ff',
            'important_if_late': 'true'}
    data.update(changes)
    return {key: value for key, value in data.items() if value is not None}


def test_rule_builder_matches_original_and_preserves_custom_rules():
    values = dict(repeat='WEEKLY', interval='2', byday={'WE', 'MO'}, until='2027-06-30', custom_rule='')
    assert build_rule(values, date(2027, 3, 1)) == 'FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE;UNTIL=20270630T235959'
    assert build_rule(dict(values, byday=set(), interval='1', until=''), date(2027, 3, 3)) == 'FREQ=WEEKLY;BYDAY=WE'
    assert build_rule(dict(values, repeat=''), None) == ''
    assert rule_values('FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE;UNTIL=20270630T235959') == dict(
        repeat='WEEKLY', interval='2', byday={'MO', 'WE'}, until='2027-06-30', custom_rule='')
    for rule in ('FREQ=MONTHLY;BYMONTHDAY=1', 'FREQ=WEEKLY;COUNT=4', 'FREQ=MONTHLY;BYDAY=1MO', 'FREQ=HOURLY'):
        assert rule_values(rule) == dict(repeat='custom', interval='1', byday=set(), until='', custom_rule=rule)
    for bad in (dict(values, interval='0'), dict(values, until='2027-02-01'), dict(values, until='soon')):
        try:
            build_rule(bad, date(2027, 3, 1))
        except ValueError:
            continue
        raise AssertionError(bad)


def test_create_matches_original_and_returns_to_calendar(prototype):
    db, client, ids = prototype
    group = EventGroup(name='Work')
    db.add(group)
    db.commit()
    page = client.get('/app/calendar/event/new?date=2027-03-01&month=2027-03')
    assert page.status_code == 200 and page.context['values']['start_at'] == '2027-03-01'
    response = client.post('/app/calendar/events?month=2027-03', headers=HX, data=form(event_group_id=str(group.id)))
    assert response.status_code == 200 and 'Saved Team sync.' in response.text
    assert response.headers['HX-Push-Url'] == '/app/calendar?month=2027-03'
    daisy = db.query(CalendarEvent).filter_by(title='Team sync').one()
    original_form = {'title': 'Original sync', 'start_at': '2027-03-01', 'start_time': '09:30', 'duration_minutes': '45',
                     'rrule': 'FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE;UNTIL=20270630T235959', 'note': 'Agenda first',
                     'hashtags': '#work', 'event_group_id': str(group.id), 'important_if_late': '1'}
    assert client.post('/calendar/create', data=original_form, follow_redirects=False).status_code == 303
    original = db.query(CalendarEvent).filter_by(title='Original sync').one()
    fields = ('start_at', 'all_day', 'duration_minutes', 'rrule', 'note', 'event_group_id', 'important_if_late')
    assert {f: getattr(daisy, f) for f in fields} == {f: getattr(original, f) for f in fields}
    assert [t.name for t in daisy.hashtags] == ['work'] and daisy.color == '#7fb3ff' and original.color is None
    assert db.query(JournalEntry).filter_by(action='Created Calendar Event', entity_id=daisy.id).count() == 1


def test_update_validation_and_custom_rule_round_trip(prototype):
    db, client, ids = prototype
    event = CalendarEvent(title='Rent', start_at=datetime(2027, 1, 1), all_day=True, rrule='FREQ=MONTHLY;BYMONTHDAY=1')
    db.add(event)
    db.commit()
    url = f'/app/calendar/event/{event.id}/edit'
    page = client.get(url + '?occurrence_at=2027-02-01')
    assert page.context['values']['repeat'] == 'custom' and page.context['values']['custom_rule'] == 'FREQ=MONTHLY;BYMONTHDAY=1'
    assert page.context['occurrence_at'] == '2027-02-01'
    keep = dict(title='Rent', start_at='2027-01-01', start_time='', duration_minutes='', repeat='custom',
                custom_rule='FREQ=MONTHLY;BYMONTHDAY=1', use_color=None, important_if_late=None, byday=None, until='')
    assert client.post(url, headers=HX, data=form(**keep, note='Pay by transfer')).status_code == 200
    db.expire_all()
    assert event.rrule == 'FREQ=MONTHLY;BYMONTHDAY=1' and event.note == 'Pay by transfer' and event.color is None
    assert event.all_day and event.duration_minutes is None
    for changes, text in [({'title': ' '}, 'Enter a title'), ({'start_at': '2027-02-30'}, 'valid date'),
                          ({'start_time': '25:00'}, 'start time'), ({'custom_rule': 'FREQ=SOMETIMES'}, ''),
                          ({'use_color': 'true', 'color': 'blue'}, 'colour'), ({'duration_minutes': '0'}, 'Duration')]:
        bad = client.post(url, headers=HX, data=form(**{**keep, **changes}))
        assert bad.status_code == 400 and text in bad.text and bad.headers['HX-Retarget'] == '#page-feedback'
    plain = client.post(url, data=form(**{**keep, 'title': ''}))
    assert plain.status_code == 400 and '<!DOCTYPE html>' in plain.text and 'FREQ=MONTHLY;BYMONTHDAY=1' in plain.text
    db.expire_all()
    assert event.title == 'Rent'
    assert client.get('/app/calendar/event/999/edit').status_code == 404


def test_notes_duplicate_end_repeat_and_delete(prototype):
    db, client, ids = prototype
    start = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0) - timedelta(days=10)
    event = CalendarEvent(title='Standup', start_at=start, all_day=False, rrule='FREQ=DAILY')
    db.add(event)
    db.commit()
    base = f'/app/calendar/event/{event.id}'
    response = client.post(base + '/notes', headers=HX, data={'name': 'Prep', 'text': 'See https://example.com <b>x</b>'})
    assert response.status_code == 200 and 'Note added.' in response.text
    assert '<a href="https://example.com"' in response.text and '&lt;b&gt;x&lt;/b&gt;' in response.text
    occurrence = (start + timedelta(days=2)).strftime('%Y-%m-%d')
    client.post(base + '/notes', headers=HX, data={'text': 'Only then', 'occurrence_only': 'true', 'occurrence_at': occurrence})
    notes = db.query(Note).filter_by(calendar_event_id=event.id).order_by(Note.id).all()
    assert [n.occurrence_at for n in notes] == [None, datetime.strptime(occurrence, '%Y-%m-%d')]
    assert client.post(base + '/notes', headers=HX, data={'text': ' '}).status_code == 400
    assert client.post(base + '/notes', headers=HX, data={'text': 'x', 'occurrence_only': 'true'}).status_code == 400
    other = CalendarEvent(title='Other', start_at=start, all_day=True)
    db.add(other)
    db.commit()
    assert client.post(f'/app/calendar/event/{other.id}/notes/{notes[0].id}/delete', headers=HX).status_code == 404
    assert 'Note deleted.' in client.post(f'{base}/notes/{notes[0].id}/delete', headers=HX).text

    copy = client.post(base + '/duplicate', headers=HX, data={'target_date': '2030-05-06'})
    assert copy.status_code == 200 and 'Copied to 6 May 2030.' in copy.text and 'Edit the copy' in copy.text
    duplicate = db.query(CalendarEvent).filter(CalendarEvent.start_at == datetime(2030, 5, 6, 8)).one()
    assert duplicate.rrule is None and duplicate.title == 'Standup'
    assert client.post(base + '/duplicate', headers=HX, data={'target_date': 'later'}).status_code == 400

    ended = client.post(base + '/end-recurrence', headers=HX)
    assert ended.status_code == 200 and 'Ended the repeat' in ended.text
    db.expire_all()
    yesterday = date.today() - timedelta(days=1)
    assert event.rrule == f'FREQ=DAILY;UNTIL={yesterday:%Y%m%d}T235959'
    assert client.post(f'/app/calendar/event/{duplicate.id}/end-recurrence', headers=HX).status_code == 400

    event_id = event.id
    deleted = client.post(base + '/delete?week=2027-03-01', headers=HX)
    assert deleted.status_code == 200 and 'Deleted Standup.' in deleted.text
    assert deleted.headers['HX-Push-Url'] == '/app/calendar?week=2027-03-01'
    db.expire_all()
    assert db.query(CalendarEvent).filter_by(id=event_id).count() == 0


def recurring_event_with_notes(db, all_day=False):
    event = CalendarEvent(title='Weekly review', note='Main preparation note', start_at=datetime(2027, 3, 1, 0 if all_day else 9, 0 if all_day else 30),
                          all_day=all_day, duration_minutes=None if all_day else 45, rrule='FREQ=WEEKLY;COUNT=4',
                          color='#7fb3ff', important_if_late=True,
                          event_group=EventGroup(name='Work'), hashtags=[Hashtag(name='work')])
    db.add(event)
    db.flush()
    db.add_all([
        Note(calendar_event_id=event.id, name='General', text='For every review'),
        Note(calendar_event_id=event.id, text='Other global note'),
        Note(calendar_event_id=event.id, occurrence_at=datetime(2027, 3, 8), name='Agenda', text='Review the launch',
             links=[Link(url='https://example.com/agenda', title='Agenda link')]),
        Note(calendar_event_id=event.id, occurrence_at=datetime(2027, 3, 8), text='Bring two reports'),
        Note(calendar_event_id=event.id, occurrence_at=datetime(2027, 3, 15), text='Another occurrence only'),
        CalendarEventCompletion(event_id=event.id, occurrence_at=datetime(2027, 3, 8, 0 if all_day else 9, 0 if all_day else 30)),
    ])
    db.commit()
    return event


@pytest.mark.parametrize('all_day', [False, True])
def test_copy_occurrence_includes_only_applicable_notes(prototype, all_day):
    db, client, _ = prototype
    event = recurring_event_with_notes(db, all_day)
    originals = {note.id: (note.name, note.text, note.occurrence_at) for note in event.notes}
    source_links = [link.id for note in event.notes for link in note.links]
    url = f'/app/calendar/event/{event.id}/duplicate?occurrence_at=2027-03-08&month=2027-03'
    response = client.post(url, headers=HX, data={'target_date': '2030-05-06', 'copy_mode': 'occurrence', 'source_occurrence': '2027-03-08'})
    assert response.status_code == 200 and 'including its notes' in response.text
    copied_id = response.context['copied_event'].id
    db.expire_all()
    copied = db.get(CalendarEvent, copied_id)
    assert copied.start_at == datetime(2030, 5, 6, 0 if all_day else 9, 0 if all_day else 30)
    for attr in ['title', 'note', 'all_day', 'duration_minutes', 'color', 'event_group_id', 'important_if_late']:
        assert getattr(copied, attr) == getattr(event, attr)
    assert copied.rrule is None and not copied.completions
    assert [tag.name for tag in copied.hashtags] == ['work']
    assert {note.text for note in copied.notes} == {'For every review', 'Other global note', 'Review the launch', 'Bring two reports'}
    assert all(note.occurrence_at is None and note.list_id is None and note.todo_id is None for note in copied.notes)
    assert not (set(originals) & {note.id for note in copied.notes})
    linked_note = next(note for note in copied.notes if note.text == 'Review the launch')
    assert linked_note.name == 'Agenda' and [(link.url, link.title) for link in linked_note.links] == [('https://example.com/agenda', 'Agenda link')]
    assert linked_note.links[0].id not in source_links
    assert {note.id: (note.name, note.text, note.occurrence_at) for note in event.notes} == originals
    assert event.rrule == 'FREQ=WEEKLY;COUNT=4' and len(event.completions) == 1
    assert db.query(JournalEntry).filter_by(action='Duplicated Calendar Event', entity_id=copied_id).count() == 1
    linked_note.text = 'Changed only on the copy'
    db.commit()
    assert next(note for note in event.notes if note.name == 'Agenda').text == 'Review the launch'


def test_basic_copy_keeps_existing_behavior_and_occurrence_copy_requires_real_occurrence(prototype):
    db, client, _ = prototype
    event = recurring_event_with_notes(db)
    url = f'/app/calendar/event/{event.id}/duplicate?occurrence_at=2027-03-08'
    ordinary = client.post(url, headers=HX, data={'target_date': '2030-05-06', 'copy_mode': 'details', 'source_occurrence': '2027-03-08'})
    assert ordinary.status_code == 200
    copy = db.get(CalendarEvent, ordinary.context['copied_event'].id)
    assert copy.note == event.note and not copy.notes
    before = db.query(CalendarEvent).count(), db.query(Note).count(), db.query(JournalEntry).count()
    for changes in [{'copy_mode': 'other'}, {'source_occurrence': ''}, {'source_occurrence': 'later'},
                    {'source_occurrence': '2027-03-09'}, {'source_occurrence': '2027-02-22'},
                    {'source_occurrence': '2027-03-29'}, {'target_date': 'later'}]:
        response = client.post(url, headers=HX, data={**{'target_date': '2030-05-06', 'copy_mode': 'occurrence', 'source_occurrence': '2027-03-08'}, **changes})
        assert response.status_code == 400
        assert before == (db.query(CalendarEvent).count(), db.query(Note).count(), db.query(JournalEntry).count())
    assert client.post(f'/app/calendar/event/{copy.id}/duplicate', headers=HX,
                       data={'target_date': '2030-05-06', 'copy_mode': 'occurrence', 'source_occurrence': '2030-05-06'}).status_code == 400
    assert client.post('/app/calendar/event/99999/duplicate', headers=HX, data={'target_date': '2030-05-06'}).status_code == 404


@pytest.mark.parametrize('width', [1200, 380])
def test_browser_copy_this_occurrence(prototype_server, page, width):
    origin, (db, _, _) = prototype_server
    event = recurring_event_with_notes(db)
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(origin + f'/app/calendar/event/{event.id}/edit?occurrence_at=2027-03-08&month=2027-03')
    expect(page.get_by_role('button', name='Copy event details', exact=True)).to_be_visible()
    page.locator('#copy-date').fill('2030-05-06')
    page.get_by_role('button', name='Copy this occurrence', exact=True).click()
    expect(page.locator('.copy-notice')).to_be_visible()
    expect(page.locator('#page-feedback')).to_contain_text('including its notes')
    page.get_by_role('link', name='Edit the copy', exact=True).click()
    expect(page.locator('#event-repeat')).to_have_value('')
    expect(page.locator('#event-note')).to_have_value('Main preparation note')
    expect(page.locator('.event-note-row')).to_have_count(4)
    expect(page.locator('.event-note-row').filter(has_text='Another occurrence only')).to_have_count(0)
    expect(page.get_by_role('button', name='Copy this occurrence', exact=True)).to_have_count(0)


def test_event_groups(prototype):
    db, client, ids = prototype
    page = client.get('/app/calendar/groups')
    assert page.status_code == 200 and 'No event groups yet' in page.text
    response = client.post('/app/calendar/groups?view=event-groups', headers=HX, data={'name': ' Work '})
    assert response.status_code == 200 and 'Created group Work.' in response.text
    assert client.post('/app/calendar/groups?view=event-groups', headers=HX, data={'name': 'Work'}).status_code == 409
    assert client.post('/app/calendar/groups?view=event-groups', headers=HX, data={'name': ''}).status_code == 400
    group = db.query(EventGroup).one()
    event = CalendarEvent(title='Grouped', start_at=datetime(2027, 1, 1), all_day=True, event_group_id=group.id)
    db.add(event)
    settings = db.query(UserSettings).first() or UserSettings()
    db.add(settings)
    settings.calendar_event_filter = f'group:{group.id}'
    db.commit()
    response = client.post(f'/app/calendar/groups/{group.id}/delete?view=event-groups', headers=HX)
    assert 'Deleted group Work. Its events were kept.' in response.text
    db.expire_all()
    assert db.get(CalendarEvent, event.id).event_group_id is None and db.query(UserSettings).first().calendar_event_filter == 'all'


def test_browser_event_editor(prototype_server, page):
    origin, (db, _, ids) = prototype_server
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(origin + '/app/calendar')
    page.get_by_role('link', name='New event').click()
    page.get_by_role('textbox', name='Title').fill('Gym')
    page.get_by_role('textbox', name='Start time').fill('07:15')
    expect(page.get_by_role('group', name='Weekdays')).to_be_hidden()
    page.get_by_role('combobox', name='Repeat').select_option('WEEKLY')
    expect(page.get_by_role('group', name='Weekdays')).to_be_visible()
    page.get_by_role('button', name='Create event').click()
    expect(page.locator('#page-feedback')).to_contain_text('Saved Gym.')
    expect(page).to_have_url(origin + '/app/calendar')
    page.get_by_role('link', name='Gym').first.click()
    expect(page.get_by_role('combobox', name='Repeat')).to_have_value('WEEKLY')
    page.get_by_role('textbox', name='Note text').fill('Bring towel')
    page.get_by_role('textbox', name='Title').fill('Gym session')  # unsaved draft survives the note re-render
    page.get_by_role('button', name='Add note').click()
    expect(page.locator('#page-feedback')).to_contain_text('Note added.')
    expect(page.get_by_text('Bring towel')).to_be_visible()
    expect(page.get_by_role('textbox', name='Title')).to_have_value('Gym session')
    page.get_by_role('button', name='Save changes').click()
    expect(page.locator('#page-feedback')).to_contain_text('Saved Gym session.')
    page.get_by_role('link', name='Gym session').first.click()
    page.once('dialog', lambda dialog: dialog.accept())
    page.get_by_role('button', name='Delete', exact=True).click()
    expect(page.locator('#page-feedback')).to_contain_text('Deleted Gym session.')
    assert not errors

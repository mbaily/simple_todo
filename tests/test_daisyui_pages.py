"""Archived lists, collation lists and the journal in the daisyUI frontend."""
from datetime import datetime, timedelta, timezone

from playwright.sync_api import expect

from models import JournalEntry, List, Todo
from test_daisyui_test import HX, prototype, prototype_server


def test_archived_page_and_bulk_actions(prototype):
    db, client, ids = prototype
    archived = db.get(List, ids['other'])
    archived.archived = True
    db.commit()
    full = client.get('/app/archived')
    fragment = client.get('/app/archived', headers=HX)
    original = client.get('/archived')
    assert full.status_code == fragment.status_code == 200 and '<!DOCTYPE html>' not in fragment.text
    assert [item.id for item in full.context['lists']] == [item.id for item in original.context['lists']] == [ids['other']]
    assert 'Weekend projects' in full.text and 'A place to start' not in full.text
    response = client.post('/app/bulk?sort=user&view=archived', headers=HX,
                           data={'action': 'unarchive', 'items': [f'list:{ids["other"]}']})
    assert response.status_code == 200 and 'Selection unarchived.' in response.text and 'No archived lists' in response.text
    redirect = client.post('/app/bulk?sort=user&view=archived', data={'action': 'archive', 'items': [f'list:{ids["other"]}']},
                           follow_redirects=False)
    assert redirect.headers['location'] == '/app/archived'
    db.expire_all()
    assert db.get(List, ids['other']).archived


def test_collations_page_counts(prototype):
    db, client, ids = prototype
    collation = db.get(List, ids['other'])
    collation.is_collation = True
    collation.collation_color = '#ff8800'
    collation.collated_todos.append(db.get(Todo, ids['todo']))
    db.commit()
    page = client.get('/app/collations')
    original = client.get('/collations')
    assert page.status_code == 200
    assert [item.id for item in page.context['collation_lists']] == [item.id for item in original.context['collation_lists']] == [ids['other']]
    assert '--collation-color: #ff8800' in page.text
    assert page.context['open_collected'] == {ids['other']: 1} and '1 collected open' in page.text


def test_journal_filters_paging_and_links(prototype):
    db, client, ids = prototype
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add_all([JournalEntry(action=f'Updated List {index}', details='Renamed', entity_type='list', entity_id=ids['list'],
                             created_at=now - timedelta(minutes=index)) for index in range(105)])
    db.add_all([JournalEntry(action='Deleted Todo', details='Gone', entity_type='todo', entity_id=999, created_at=now - timedelta(days=3)),
                JournalEntry(action='Created Calendar Event', details='Dentist', entity_type='calendar_event', entity_id=7, created_at=now - timedelta(days=4)),
                JournalEntry(action='Completed Calendar Day', details='3 occurrences', created_at=now - timedelta(days=5))])
    db.commit()
    first = client.get('/app/journal')
    assert first.status_code == 200 and first.context['total'] == 108 and first.context['pages'] == 2
    assert first.context['older_url'] == '/app/journal?page=2' and first.context['newer_url'] is None
    assert f'href="/app/list/{ids["list"]}"' in first.text
    second = client.get('/app/journal?page=2')
    assert sum(len(day['entries']) for day in second.context['days']) == 8
    assert 'Deleted' in second.text and '/calendar/event/7/edit' in second.text
    filtered = client.get('/app/journal?kind=calendar_event', headers=HX)
    assert filtered.context['total'] == 1 and 'Dentist' in filtered.text and '<!DOCTYPE html>' not in filtered.text
    assert client.get('/app/journal?kind=other').context['total'] == 1
    assert client.get('/app/journal?q=dentist').context['total'] == 1
    assert client.get('/app/journal?q=nothing-here').context['days'] == []
    assert client.get('/app/journal?kind=spaceship').status_code == 400
    assert client.get('/app/journal?page=0').status_code == 400


def test_browser_more_menu_pages(prototype_server, page):
    origin, (db, _, ids) = prototype_server
    db.add(JournalEntry(action='Created List', details='Hello journal', entity_type='list', entity_id=ids['list']))
    db.commit()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(origin + '/app')
    page.locator('summary[aria-label="More navigation"]').click()
    page.get_by_role('link', name='Journal').click()
    expect(page.get_by_role('heading', name='Journal')).to_be_visible()
    expect(page.get_by_text('Hello journal')).to_be_visible()
    page.get_by_role('combobox', name='Entry type').select_option('calendar_event')
    expect(page).to_have_url(origin + '/app/journal?kind=calendar_event&q=')
    expect(page.get_by_text('No entries match the filter.')).to_be_visible()
    page.locator('summary[aria-label="More navigation"]').click()
    page.get_by_role('link', name='Archived').click()
    expect(page.get_by_role('heading', name='Archived', exact=True)).to_be_visible()
    assert not errors

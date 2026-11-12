"""Search and hashtag result pages in the daisyUI frontend."""
from datetime import datetime

from playwright.sync_api import expect

from models import CalendarEvent, Hashtag, JournalEntry, List, Todo
from test_daisyui_test import HX, prototype, prototype_server


def add_results_data(db, ids):
    tag = db.get(Hashtag, ids['tag'])
    tagged_list = db.get(List, ids['other'])
    tagged_list.hashtags.append(tag)
    done = Todo(title='Quiet finished chore', list_id=ids['list'], completed=True, hashtags=[tag])
    inherited = Todo(title='Weekend shelf', list_id=ids['other'])
    db.add_all([done, inherited, CalendarEvent(title='Quiet dinner', start_at=datetime(2030, 1, 2, 19), all_day=False, hashtags=[tag])])
    tag.show_completed = False
    db.commit()
    return {**ids, 'done': done.id, 'inherited': inherited.id}


def test_search_documents_fragments_and_parity(prototype):
    db, client, ids = prototype
    ids = add_results_data(db, ids)
    full = client.get('/app/search?q=quiet')
    fragment = client.get('/app/search?q=quiet', headers=HX)
    restored = client.get('/app/search?q=quiet', headers={**HX, 'HX-History-Restore-Request': 'true'})
    original = client.get('/search?q=quiet')
    assert full.status_code == fragment.status_code == restored.status_code == original.status_code == 200
    assert '<!DOCTYPE html>' in full.text and '<!DOCTYPE html>' in restored.text and '<!DOCTYPE html>' not in fragment.text
    assert full.headers['cache-control'] == 'no-store' and fragment.headers['vary'] == 'HX-Request, HX-History-Restore-Request'
    assert {t.id for t in full.context['todos']} == {ids['todo'], ids['done']}
    assert [e.title for e in full.context['events']] == [e.title for e in original.context['events']] == ['Quiet dinner']
    assert [item.id for item in full.context['lists']] == [item.id for item in original.context['lists']]
    assert 'aria-current="page"' in full.text and '3 results' in full.text
    assert full.context['query'] == '?sort=user&q=quiet&view=search'
    empty = client.get('/app/search')
    assert empty.status_code == 200 and 'Search names, task notes' in empty.text
    assert 'Nothing matches' in client.get('/app/search?q=zzzz-none').text


def test_hashtag_results_and_options_match_original(prototype):
    db, client, ids = prototype
    ids = add_results_data(db, ids)
    url = f'/app/hashtag/{ids["tag"]}'
    page = client.get(url)
    assert page.status_code == 200 and page.context['page_title'].startswith('#personal')
    assert [t.id for t in page.context['todos']] == [ids['todo']]
    assert [item.id for item in page.context['lists']] == [ids['other']]
    query = page.context['query']
    assert query == f'?sort=user&view=hashtag&hashtag_id={ids["tag"]}'

    response = client.post(f'{url}/options/include_list_items{query}', headers=HX, data={'enabled': 'true'})
    assert response.status_code == 200 and 'Weekend shelf' in response.text
    response = client.post(f'{url}/options/show_completed{query}', headers=HX, data={'enabled': 'true'})
    assert 'Quiet finished chore' in response.text
    # A retried request keeps the requested state instead of toggling it.
    client.post(f'{url}/options/show_completed{query}', headers=HX, data={'enabled': 'true'})
    db.expire_all()
    tag = db.get(Hashtag, ids['tag'])
    assert tag.show_completed and tag.include_list_items
    assert db.query(JournalEntry).filter_by(action='Toggled Hashtag Show Completed').count() == 1
    original = client.get(f'/hashtag/{ids["tag"]}')
    assert {t.id for t in client.get(url).context['todos']} == {
        t.id for group in original.context['todo_tree']['lists'] for t in group['todos']}

    bad = client.post(f'{url}/options/unknown{query}', headers=HX, data={'enabled': 'true'})
    assert bad.status_code == 400 and bad.headers['HX-Retarget'] == '#page-feedback'
    assert client.get('/app/hashtag/999999').status_code == 404
    redirect = client.post(f'{url}/options/show_completed{query}', data={}, follow_redirects=False)
    assert redirect.status_code == 303 and redirect.headers['location'] == url + '?sort=user'


def test_mutations_from_results_rerender_results(prototype):
    db, client, ids = prototype
    query = '?sort=user&q=quiet&view=search'
    response = client.post(f'/app/complete/todo/{ids["todo"]}{query}', headers=HX, data={'completed': 'true'})
    assert response.status_code == 200 and 'id="search-form"' in response.text and 'Completion updated.' in response.text
    response = client.post(f'/app/bulk{query}', headers=HX, data={'action': 'unpin', 'items': [f'todo:{ids["todo"]}']})
    assert response.status_code == 200 and 'id="search-form"' in response.text
    redirect = client.post(f'/app/complete/todo/{ids["todo"]}{query}', data={}, follow_redirects=False)
    assert redirect.headers['location'] == '/app/search?sort=user&q=quiet'
    db.expire_all()
    task = db.get(Todo, ids['todo'])
    assert not task.completed and not task.pinned


def test_editor_returns_to_results(prototype):
    db, client, ids = prototype
    path = f'/app/todo/{ids["todo"]}/edit?sort=user&q=quiet&view=search&back=results'
    page = client.get(path)
    assert page.status_code == 200 and page.context['return_url'] == '/app/search?sort=user&q=quiet'
    assert client.get(path.replace('view=search', 'view=nope')).status_code == 400
    response = client.post(path, headers=HX, data={'title': 'Book a quiet morning', 'note': '', 'hashtags': '#personal', 'alarm_mode': 'none'})
    assert response.status_code == 200 and response.headers['HX-Push-Url'] == '/app/search?sort=user&q=quiet'
    assert 'id="search-form"' in response.text and 'Book a quiet morning' in response.text
    tag_path = f'/app/list/{ids["list"]}/edit?sort=user&view=hashtag&hashtag_id={ids["tag"]}&back=results'
    response = client.post(tag_path, headers=HX, data={'name': 'Renamed', 'hashtags': ''})
    assert response.headers['HX-Push-Url'] == f'/app/hashtag/{ids["tag"]}?sort=user'
    response = client.post(tag_path.replace('/edit?', '/edit/delete?'), follow_redirects=False)
    assert response.status_code == 303 and response.headers['location'] == f'/app/hashtag/{ids["tag"]}?sort=user'


def test_browser_search_complete_edit_and_hashtag(prototype_server, page):
    origin, _ = prototype_server
    page.goto(origin + '/app')
    page.locator('.toolbar-navigation').get_by_role('link', name='Search').click()
    search = page.get_by_role('searchbox', name='Search text')
    expect(search).to_be_focused()
    search.fill('quiet')
    search.press('Enter')
    expect(page).to_have_url(origin + '/app/search?sort=user&q=quiet')
    expect(page.locator('#results-summary')).to_contain_text('1 result')
    expect(search).to_be_focused()
    checkbox = page.get_by_role('checkbox', name='Mark Book a quiet afternoon complete', exact=True)
    checkbox.check()
    expect(page.locator('#page-feedback')).to_contain_text('Completion updated.')
    expect(page.get_by_role('checkbox', name='Mark Book a quiet afternoon incomplete', exact=True)).to_be_checked()
    page.locator('summary[aria-label="Actions for Book a quiet afternoon"]').click()
    page.get_by_role('link', name='Edit task').click()
    page.get_by_role('textbox', name='Task title').fill('Book a quiet evening')
    page.get_by_role('button', name='Save changes').click()
    expect(page).to_have_url(origin + '/app/search?sort=user&q=quiet')
    expect(page.get_by_text('Book a quiet evening', exact=True)).to_be_visible()
    page.get_by_role('link', name='#personal').first.click()
    expect(page.get_by_role('heading', name='#personal')).to_be_visible()
    expect(page.get_by_text('Book a quiet evening', exact=True)).to_be_visible()
    page.get_by_role('checkbox', name='Show completed items').uncheck()
    expect(page.get_by_text('Nothing is tagged #personal that is still open.')).to_be_visible()
    page.go_back()
    expect(page.get_by_role('heading', name='Search')).to_be_visible()

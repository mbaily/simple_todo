"""Move to list in the daisyUI frontend."""
from playwright.sync_api import expect

from models import Folder, JournalEntry, List, Note, Todo, UserSettings
from test_daisyui_test import HX, prototype, prototype_server


def subtree(db, ids):
    parent = db.get(Todo, ids['todo'])
    child = Todo(title='Pack a thermos', list_id=parent.list_id, parent_id=parent.id)
    db.add(child)
    db.flush()
    note = Note(text='Bring the blue cup', list_id=parent.list_id, todo_id=child.id)
    loose = Note(text='Loose list note', list_id=parent.list_id)
    db.add_all([note, loose])
    db.commit()
    return {**ids, 'child': child.id, 'note': note.id, 'loose': loose.id}


def test_move_keeps_subtree_and_returns_to_the_originating_page(prototype):
    db, client, ids = prototype
    ids = subtree(db, ids)
    targets = client.get('/app/move/targets')
    assert targets.status_code == 200 and 'Weekend projects' in targets.text and 'name="target_list_id"' in targets.text
    query = f'?sort=user&view_list_id={ids["list"]}'
    response = client.post('/app/move', headers=HX, data={
        'items': [f'todo:{ids["todo"]}', f'note:{ids["loose"]}'], 'target_list_id': ids['other'], 'return_query': query})
    assert response.status_code == 200 and 'Moved 2 tasks and 2 notes to Weekend projects.' in response.text
    assert response.headers['HX-Trigger'] == 'daisy:selection-cleared'
    # The list page it came from is re-rendered, now without the moved items.
    assert 'data-scope="list:%d"' % ids['list'] in response.text and 'Book a quiet afternoon' not in response.text
    db.expire_all()
    assert {db.get(Todo, i).list_id for i in (ids['todo'], ids['child'])} == {ids['other']}
    assert db.get(Todo, ids['child']).parent_id == ids['todo']
    assert {db.get(Note, i).list_id for i in (ids['note'], ids['loose'])} == {ids['other']}
    assert db.query(JournalEntry).filter_by(action='Moved Todo (Tagged)').count() == 2


def test_move_validation(prototype):
    db, client, ids = prototype
    data = {'items': [f'todo:{ids["todo"]}'], 'target_list_id': ids['other'], 'return_query': '?sort=user'}
    for changes, status in [({'items': [f'list:{ids["list"]}']}, 400), ({'items': []}, 400), ({'target_list_id': 0}, 400),
                            ({'target_list_id': 999}, 404), ({'items': ['todo:999']}, 404)]:
        response = client.post('/app/move', headers=HX, data={**data, **changes})
        assert response.status_code == status and response.headers['HX-Retarget'] == '#page-feedback'
    archived = db.get(List, ids['other'])
    archived.archived = True
    db.commit()
    assert client.post('/app/move', headers=HX, data=data).status_code == 404
    # Unknown return parameters are dropped rather than passed to the page renderer.
    archived.archived = False
    db.commit()
    response = client.post('/app/move', headers=HX, data={**data, 'return_query': '?sort=user&evil=1'})
    assert response.status_code == 200 and 'Moved 1 task to Weekend projects.' in response.text
    redirect = client.post('/app/move', data={**data, 'items': [f'todo:{ids["old"]}']}, follow_redirects=False)
    assert redirect.status_code == 303 and redirect.headers['location'] == f'/app/list/{ids["other"]}'


def test_browser_move_to_list(prototype_server, page):
    origin, (db, _, ids) = prototype_server
    ids = subtree(db, ids)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(origin + f'/app/list/{ids["list"]}')
    page.get_by_role('button', name='Select', exact=True).click()
    move = page.get_by_role('button', name='Move to list…')
    expect(move).to_be_disabled()
    page.get_by_role('checkbox', name='Select Book a quiet afternoon').check()
    expect(move).to_be_enabled()
    move.click()
    dialog = page.locator('#move-dialog')
    expect(dialog.locator('#move-title')).to_contain_text('Move “Book a quiet afternoon” to')
    dialog.get_by_role('searchbox', name='Find a list').fill('weekend')
    expect(dialog.get_by_role('option', name='A place to start')).to_be_hidden()
    # Enter chooses the highlighted match rather than the first (hidden) button.
    expect(dialog.locator('.jump-item.is-active')).to_have_text('Weekend projects')
    page.keyboard.press('Enter')
    expect(page.locator('#page-feedback')).to_contain_text('Moved 2 tasks and 1 note to Weekend projects.')
    expect(dialog).not_to_be_visible()
    expect(page.get_by_text('Book a quiet afternoon')).to_be_hidden()
    page.goto(origin + f'/app/list/{ids["other"]}')
    expect(page.get_by_text('Pack a thermos')).to_be_visible()
    assert not errors


def test_targets_put_favourites_and_recent_destinations_first(prototype):
    db, client, ids = prototype
    child = Folder(name='Garden', parent_id=ids['folder'])
    other = Folder(name='Admin')
    db.add_all([child, other, List(name='Zebra crossing'), List(name='Put away', archived=True)])
    db.commit()
    client.get('/app/list/%d' % ids['other'])
    client.post('/app/favourite', headers=HX, data={'key': 'list:%d' % db.query(List).filter_by(name='Zebra crossing').one().id, 'starred': 'true'})
    lists = client.get('/app/move/targets', headers=HX).text
    assert lists.index('Zebra crossing') < lists.index('Weekend projects') < lists.index('A place to start')
    assert 'Put away' not in lists and 'name="target_folder_id"' not in lists

    folders = client.get('/app/move/targets', params={'kind': 'folder'}, headers=HX).text
    assert 'name="target_folder_id" value="0"' in folders and folders.index('Top level') < folders.index('Admin') < folders.index('Garden')
    assert 'data-name="projects garden"' in folders
    # A folder being moved, and everything below it, is not offered as its own destination.
    moving = client.get('/app/move/targets', params={'kind': 'folder', 'items': ['folder:%d' % ids['folder'], 'list:%d' % ids['list']]}, headers=HX).text
    assert 'Admin' in moving and 'Garden' not in moving and '>Projects<' not in moving
    # Moving somewhere makes it a recent destination.
    client.post('/app/move', headers=HX, data={'items': ['list:%d' % ids['list']], 'target_folder_id': child.id, 'return_query': '?sort=user'})
    folders = client.get('/app/move/targets', params={'kind': 'folder'}, headers=HX).text
    assert folders.index('Top level') < folders.index('Garden') < folders.index('Admin')


def test_move_lists_and_folders_to_a_folder_from_any_page(prototype):
    db, client, ids = prototype
    other = Folder(name='Admin')
    db.add(other)
    db.commit()
    items = ['list:%d' % ids['list'], 'folder:%d' % other.id]
    response = client.post('/app/move', headers=HX, data={'items': items, 'target_folder_id': ids['folder'], 'return_query': '?sort=created&folder_id=0'})
    assert response.status_code == 200 and 'Moved 2 items to Projects.' in response.text
    assert response.headers['HX-Trigger'] == 'daisy:selection-cleared'
    # The overview it came from (unfiled lists, same sort) is re-rendered without the moved list.
    assert response.context['folder_param'] == 0 and response.context['sort'] == 'created'
    assert ids['list'] not in [item.id for item in response.context['lists']]
    db.expire_all()
    assert db.get(List, ids['list']).folder_id == ids['folder'] and db.get(Folder, other.id).parent_id == ids['folder']
    assert db.query(JournalEntry).filter(JournalEntry.action.in_(['Moved List', 'Moved Folder'])).count() == 2
    assert db.query(UserSettings).one().metadata_json['shortcuts']['recent'][0] == 'folder:%d' % ids['folder']
    # From the list page and the Folders page, each returns to itself; 0 is the top level.
    back = client.post('/app/move', headers=HX, data={'items': items[:1], 'target_folder_id': 0, 'return_query': '?sort=user&view_list_id=%d' % ids['list']})
    assert 'Moved 1 item to the top level.' in back.text and 'data-scope="list:%d"' % ids['list'] in back.text
    tree = client.post('/app/move', headers=HX, data={'items': items[:1], 'target_folder_id': other.id, 'return_query': '?view=folders'})
    assert 'Moved 1 item to Projects &gt; Admin.' in tree.text and 'Folder tree' in tree.text
    for data, status in [({'items': ['folder:%d' % ids['folder']], 'target_folder_id': other.id}, 400),
                         ({'items': ['todo:%d' % ids['todo']], 'target_folder_id': 0}, 400),
                         ({'items': items, 'target_folder_id': 9999}, 404), ({'items': [], 'target_folder_id': 0}, 400)]:
        bad = client.post('/app/move', headers=HX, data={**data, 'return_query': '?sort=user'})
        assert bad.status_code == status and bad.headers['HX-Retarget'] == '#page-feedback'
    plain = client.post('/app/move', data={'items': items[:1], 'target_folder_id': ids['folder']}, follow_redirects=False)
    assert plain.status_code == 303 and plain.headers['location'] == '/app?folder_id=%d' % ids['folder']


def test_browser_move_lists_to_a_folder_from_overview_and_list_page(prototype_server, page):
    origin, (db, _, ids) = prototype_server
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(origin + '/app?folder_id=0')
    dialog = page.locator('#move-dialog')
    # One row, from its menu.
    row = page.locator('.lists-section .item-row', has=page.get_by_role('link', name='Weekend projects', exact=True))
    row.locator('summary').click()
    row.get_by_role('button', name='Move to folder…').click()
    expect(dialog.locator('#move-title')).to_contain_text('Move “Weekend projects” to')
    expect(dialog.get_by_role('searchbox', name='Find a folder')).to_be_focused()
    page.keyboard.press('ArrowDown')
    expect(dialog.locator('.jump-item.is-active')).to_contain_text('Projects')
    page.keyboard.press('Enter')
    expect(page.locator('#page-feedback')).to_contain_text('Moved 1 item to Projects.')
    expect(dialog).not_to_be_visible()
    expect(page.locator('.lists-section').get_by_role('link', name='Weekend projects', exact=True)).to_have_count(0)
    # Several lists, from the selection toolbar.
    page.get_by_role('button', name='Select', exact=True).click()
    move = page.get_by_role('button', name='Move to folder…')
    expect(move).to_be_disabled()
    page.locator('.lists-section').get_by_role('checkbox', name='Select A place to start').check()
    move.click()
    dialog.get_by_role('searchbox', name='Find a folder').fill('nothing like this')
    expect(dialog.get_by_text('No folders match.')).to_be_visible()
    dialog.get_by_role('searchbox', name='Find a folder').fill('proj')
    dialog.get_by_role('option', name='Projects').click()
    expect(page.locator('#page-feedback')).to_contain_text('Moved 1 item to Projects.')
    # From the list's own page, which stays open.
    page.goto(origin + '/app/list/%d' % ids['list'])
    page.get_by_text('View & list options').click()
    page.get_by_role('button', name='Move list to folder…').click()
    expect(dialog.locator('#move-title')).to_contain_text('Move “A place to start” to')
    dialog.get_by_role('option', name='Top level').click()
    expect(page.locator('#page-feedback')).to_contain_text('Moved 1 item to the top level.')
    expect(page.get_by_role('heading', level=1)).to_have_text('A place to start')
    assert not errors
    db.expire_all()
    assert db.get(List, ids['list']).folder_id is None and db.get(List, ids['other']).folder_id == ids['folder']

"""Folder management in the daisyUI frontend."""
from playwright.sync_api import expect

from models import Folder, JournalEntry, List, UserSettings
from test_daisyui_test import HX, prototype, prototype_server

Q = '?view=folders'


def tree(db, ids):
    parent = db.get(Folder, ids['folder'])
    child = Folder(name='Garden', parent_id=parent.id, sort_order=0)
    other = Folder(name='Admin', sort_order=10)
    db.add_all([child, other])
    db.flush()
    db.get(List, ids['other']).folder_id = child.id
    parent.sort_order = 0
    db.commit()
    return {**ids, 'child': child.id, 'admin': other.id}


def test_folders_documents_and_tree(prototype):
    db, client, ids = prototype
    ids = tree(db, ids)
    full = client.get('/app/folders')
    fragment = client.get('/app/folders', headers=HX)
    restored = client.get('/app/folders', headers={**HX, 'HX-History-Restore-Request': 'true'})
    assert full.status_code == fragment.status_code == restored.status_code == 200
    assert '<!DOCTYPE html>' in full.text and '<!DOCTYPE html>' in restored.text and '<!DOCTYPE html>' not in fragment.text
    assert full.headers['cache-control'] == 'no-store'
    assert [f.id for f in full.context['root_folders']] == [ids['folder'], ids['admin']]
    assert full.context['folder_paths'][ids['child']] == f'{ids["child"]} {ids["folder"]}'
    assert [item.id for item in full.context['lists_by_folder'][None]] == [ids['list']]
    assert 'Projects &gt; Garden' in full.text and 'aria-current="page"' in full.text


def test_create_rename_and_validation(prototype):
    db, client, ids = prototype
    response = client.post('/app/folders' + Q, headers=HX, data={'name': '  Errands  ', 'parent_id': ids['folder']})
    assert response.status_code == 200 and 'Created folder Errands.' in response.text
    created = db.query(Folder).filter_by(name='Errands').one()
    assert created.parent_id == ids['folder']
    assert db.query(JournalEntry).filter_by(action='Created Folder', entity_id=created.id).count() == 1
    for data, status in [({'name': '   '}, 400), ({'name': 'x' * 256}, 400), ({'name': 'Lost', 'parent_id': 9999}, 404)]:
        bad = client.post('/app/folders' + Q, headers=HX, data=data)
        assert bad.status_code == status and bad.headers['HX-Retarget'] == '#page-feedback'
    response = client.post(f'/app/folders/{created.id}/rename{Q}', headers=HX, data={'name': 'Chores'})
    assert 'Folder renamed.' in response.text
    db.expire_all()
    assert created.name == 'Chores'
    assert client.post(f'/app/folders/9999/rename{Q}', headers=HX, data={'name': 'x'}).status_code == 404
    redirect = client.post('/app/folders' + Q, data={'name': 'Plain'}, follow_redirects=False)
    assert redirect.status_code == 303 and redirect.headers['location'] == '/app/folders'
    error = client.post('/app/folders' + Q, data={'name': ''})
    assert error.status_code == 400 and 'Enter a folder name.' in error.text and '<!DOCTYPE html>' in error.text


def test_move_order_default_collapse_and_delete(prototype):
    db, client, ids = prototype
    ids = tree(db, ids)
    move = lambda items, target: client.post('/app/folders/move-items' + Q, headers=HX, data={'items': items, 'target_id': target})
    cycle = move([f'folder:{ids["folder"]}'], ids['child'])
    assert cycle.status_code == 400 and 'subfolders' in cycle.text
    assert move([f'folder:{ids["folder"]}'], ids['folder']).status_code == 400
    moved = move([f'folder:{ids["child"]}'], 0)
    assert moved.status_code == 200 and 'Moved 1 item to the top level.' in moved.text
    db.expire_all()
    assert db.get(Folder, ids['child']).parent_id is None

    client.post(f'/app/folders/{ids["admin"]}/order/up{Q}', headers=HX)
    db.expire_all()
    roots = db.query(Folder).filter(Folder.parent_id == None).order_by(Folder.sort_order, Folder.name).all()
    assert [f.id for f in roots] == [ids['child'], ids['admin'], ids['folder']]
    assert client.post(f'/app/folders/{ids["admin"]}/order/sideways{Q}', headers=HX).status_code == 400

    client.post(f'/app/folders/{ids["folder"]}/default{Q}', headers=HX, data={'enabled': 'true'})
    db.expire_all()
    assert db.query(UserSettings).one().active_folder_id == ids['folder']
    client.post(f'/app/folders/{ids["admin"]}/default{Q}', headers=HX, data={})
    db.expire_all()
    assert db.query(UserSettings).one().active_folder_id == ids['folder']
    client.post(f'/app/folders/{ids["folder"]}/default{Q}', headers=HX, data={})
    db.expire_all()
    assert db.query(UserSettings).one().active_folder_id is None

    assert client.post(f'/app/folders/{ids["folder"]}/collapse', data={'collapsed': 'true'}).status_code == 204
    db.expire_all()
    assert db.get(Folder, ids['folder']).is_collapsed
    assert 'aria-label="Expand Projects"' in client.get('/app/folders').text

    response = client.post(f'/app/folders/{ids["child"]}/delete{Q}', headers=HX)
    assert 'Deleted folder Garden.' in response.text
    db.expire_all()
    assert db.get(Folder, ids['child']) is None and db.get(List, ids['other']).folder_id is None


def test_move_items_mixed_selection_and_archive(prototype):
    db, client, ids = prototype
    ids = tree(db, ids)
    items = [f'list:{ids["list"]}', f'list:{ids["other"]}', f'folder:{ids["admin"]}']
    response = client.post('/app/folders/move-items' + Q, headers=HX, data={'items': items, 'target_id': ids['child']})
    assert response.status_code == 200 and 'Moved 3 items to Projects &gt; Garden.' in response.text
    assert response.headers['HX-Trigger'] == 'daisy:selection-cleared'
    db.expire_all()
    assert {db.get(List, i).folder_id for i in (ids['list'], ids['other'])} == {ids['child']}
    assert db.get(Folder, ids['admin']).parent_id == ids['child']
    assert db.query(JournalEntry).filter(JournalEntry.action.in_(['Moved List', 'Moved Folder'])).count() == 3
    for data, status in [({'items': [f'todo:{ids["todo"]}'], 'target_id': 0}, 400), ({'items': items}, 400),
                         ({'items': [], 'target_id': 0}, 400), ({'items': ['list:9999'], 'target_id': 0}, 404),
                         ({'items': ['folder:9999'], 'target_id': 0}, 404), ({'items': items, 'target_id': 9999}, 404),
                         ({'items': [f'folder:{ids["folder"]}'], 'target_id': ids['admin']}, 400)]:
        assert client.post('/app/folders/move-items' + Q, headers=HX, data=data).status_code == status
    response = client.post('/app/bulk' + Q, headers=HX, data={'action': 'archive', 'items': [f'list:{ids["other"]}']})
    assert response.status_code == 200 and 'Folder tree' in response.text
    db.expire_all()
    assert db.get(List, ids['other']).archived


def test_browser_folder_workflows(prototype_server, page):
    origin, (db, _, ids) = prototype_server
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(origin + '/app')
    page.locator('.toolbar-navigation').get_by_role('link', name='Folders', exact=True).click()
    expect(page).to_have_url(origin + '/app/folders')
    page.get_by_text('New folder').click()
    # Click into the field first: WebKit drops text typed during the panel's opening animation.
    page.get_by_role('textbox', name='Folder name').click()
    page.get_by_role('textbox', name='Folder name').fill('Errands')
    page.get_by_role('button', name='Add folder').click()
    expect(page.locator('#page-feedback')).to_contain_text('Created folder Errands.')

    # Move one list from its row, choosing the destination in the picker.
    dialog = page.locator('#move-dialog')
    page.get_by_role('button', name='Move A place to start').click()
    expect(dialog.locator('#move-title')).to_contain_text('Move “A place to start” to')
    dialog.get_by_role('searchbox', name='Find a folder').fill('err')
    page.keyboard.press('Enter')
    expect(page.locator('#page-feedback')).to_contain_text('Moved 1 item to Errands.')
    expect(dialog).not_to_be_visible()
    errands = page.locator('.folder-node', has=page.get_by_role('link', name='Errands', exact=True))
    expect(errands.get_by_role('link', name='A place to start')).to_be_visible()

    # A folder is not offered as its own destination; Escape cancels choosing.
    page.locator('summary[aria-label="Actions for Errands"]').click()
    page.get_by_role('button', name='Move folder…').click()
    expect(dialog.get_by_role('option', name='Projects')).to_be_visible()
    expect(dialog.get_by_role('option', name='Errands')).to_have_count(0)
    page.keyboard.press('Escape')
    expect(dialog).not_to_be_visible()

    # Select several items, then pick the target.
    page.get_by_role('button', name='Select', exact=True).click()
    page.get_by_role('checkbox', name='Select Weekend projects').check()
    page.get_by_role('checkbox', name='Select folder Errands').check()
    page.get_by_role('button', name='Move…').click()
    expect(dialog.locator('#move-title')).to_contain_text('1 list and 1 folder')
    dialog.get_by_role('option', name='Projects').click()
    expect(page.locator('#page-feedback')).to_contain_text('Moved 2 items to Projects.')
    page.get_by_role('button', name='Move Weekend projects').click()
    dialog.get_by_role('option', name='Top level').click()
    expect(page.locator('#page-feedback')).to_contain_text('Moved 1 item to the top level.')
    errands = page.locator('.folder-node', has=page.get_by_role('link', name='Errands', exact=True))

    errands.get_by_role('button', name='Collapse Errands').click()
    expect(errands.get_by_role('link', name='A place to start')).to_be_hidden()
    page.reload()
    expect(page.get_by_role('button', name='Expand Errands')).to_be_visible()

    page.locator('summary[aria-label="Actions for Errands"]').click()
    page.get_by_role('textbox', name='Rename').fill('Chores')
    page.get_by_role('textbox', name='Rename').press('Enter')
    expect(page.get_by_role('link', name='Chores', exact=True)).to_be_visible()

    page.locator('summary[aria-label="Actions for Chores"]').click()
    page.once('dialog', lambda dialog: dialog.accept())
    page.get_by_role('button', name='Delete folder').click()
    expect(page.locator('#page-feedback')).to_contain_text('Deleted folder Chores.')
    expect(page.locator('section', has=page.get_by_role('heading', name='Unfiled lists')).get_by_role('link', name='A place to start')).to_be_visible()
    assert not errors


def test_overview_browses_folders_with_breadcrumbs_and_actions(prototype):
    db, client, ids = prototype
    ids = tree(db, ids)
    root = client.get('/app')
    assert [f.id for f in root.context['subfolders']] == [ids['folder'], ids['admin']] and root.context['current_folder'] is None
    assert 'New folder at the top level' in root.text and f'data-item-key="folder:{ids["folder"]}"' in root.text
    assert '0 lists · 1 subfolder' in root.text
    inner = client.get(f'/app?folder_id={ids["child"]}&sort=created')
    assert inner.context['current_folder'].id == ids['child'] and inner.context['subfolders'] == []
    crumbs = inner.text.split('class="breadcrumbs"')[1].split('</ul>')[0]
    assert f'folder_id={ids["folder"]}' in crumbs and '>Projects<' in crumbs and '<li aria-current="page">Garden</li>' in crumbs
    assert 'aria-label="Actions for Garden"' in inner.text
    # Every breadcrumb starts with the trail icon, which nothing else uses.
    assert crumbs.count('class="trail-icon"') == 1 and inner.text.count('class="trail-icon"') == 1
    assert root.text.count('class="trail-icon"') == 1
    # Folder actions used from the overview show the same folder and sort again.
    query = f'?sort=created&folder_id={ids["folder"]}'
    renamed = client.post(f'/app/folders/{ids["child"]}/rename{query}', headers=HX, data={'name': 'Allotment'})
    assert 'Folder renamed.' in renamed.text and renamed.context['current_folder'].id == ids['folder']
    assert renamed.context['sort'] == 'created' and '>Allotment<' in renamed.text and 'Folder tree' not in renamed.text
    added = client.post('/app/folders' + query, headers=HX, data={'name': 'Shed', 'parent_id': ids['folder']})
    assert [f.name for f in added.context['subfolders']] == ['Allotment', 'Shed']
    deleted = client.post(f'/app/folders/{ids["child"]}/delete{query}', headers=HX)
    assert 'Deleted folder Allotment.' in deleted.text and [f.name for f in deleted.context['subfolders']] == ['Shed']
    bad = client.post(f'/app/folders/9999/rename{query}', headers=HX, data={'name': 'x'})
    assert bad.status_code == 404 and bad.headers['HX-Retarget'] == '#page-feedback'
    redirect = client.post('/app/folders' + query, data={'name': 'Plain', 'parent_id': ids['folder']}, follow_redirects=False)
    assert redirect.status_code == 303 and redirect.headers['location'] == f'/app?sort=created&folder_id={ids["folder"]}'
    # The list page shows where the list lives, each level a link.
    db.get(List, ids['list']).folder_id = db.query(Folder).filter_by(name='Shed').one().id
    db.commit()
    path = client.get(f'/app/list/{ids["list"]}').text.split('class="folder-path"')[1].split('</span><span>Created')[0]
    assert path.index('>Projects<') < path.index('>Shed<') and f'folder_id={ids["folder"]}' in path
    assert 'class="trail-icon"' in path and client.get('/app/calendar').text.count('class="trail-icon"') == 0
    assert '>Top level<' in client.get(f'/app/list/{ids["other"]}').text.split('class="folder-path"')[1].split('</span><span>Created')[0]


def test_browser_drill_into_folders_from_the_overview(prototype_server, page):
    origin, (db, _, ids) = prototype_server
    ids = tree(db, ids)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(origin + '/app')
    rows = page.locator('.subfolder-group')
    rows.get_by_role('link', name='Projects', exact=True).click()
    expect(page).to_have_url(origin + f'/app?sort=user&folder_id={ids["folder"]}')
    rows.get_by_role('link', name='Garden', exact=True).click()
    expect(page.get_by_role('heading', level=1)).to_contain_text('Garden')
    expect(page.locator('.lists-section').get_by_role('link', name='Weekend projects', exact=True)).to_be_visible()
    # Add a subfolder here, then go back up through the breadcrumb.
    page.locator('summary[aria-label="Actions for Garden"]').click()
    page.get_by_role('textbox', name='Add subfolder').fill('Seeds')
    page.get_by_role('textbox', name='Add subfolder').press('Enter')
    expect(page.locator('#page-feedback')).to_contain_text('Created folder Seeds.')
    expect(rows.get_by_role('link', name='Seeds', exact=True)).to_be_visible()
    page.locator('.breadcrumbs').get_by_role('link', name='Projects', exact=True).click()
    expect(page.get_by_role('heading', level=1)).to_contain_text('Projects')
    # Move the subfolder elsewhere from its row; it leaves this folder.
    page.locator('summary[aria-label="Actions for Garden"]').click()
    page.get_by_role('button', name='Move folder…').click()
    dialog = page.locator('#move-dialog')
    expect(dialog.get_by_role('option', name='Seeds')).to_have_count(0)
    dialog.get_by_role('option', name='Admin').click()
    expect(page.locator('#page-feedback')).to_contain_text('Moved 1 item to Admin.')
    expect(rows).to_have_count(0)
    # A list page links back to its folder.
    page.goto(origin + f'/app/list/{ids["other"]}')
    page.locator('.folder-path').get_by_role('link', name='Garden', exact=True).click()
    expect(page.get_by_role('heading', level=1)).to_contain_text('Garden')
    page.set_viewport_size({'width': 360, 'height': 700})
    page.locator('summary[aria-label="Actions for Garden"]').click()
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    assert not errors

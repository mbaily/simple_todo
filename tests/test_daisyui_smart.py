"""Smart folder index, results and editor in the daisyUI frontend."""
from datetime import datetime, timedelta

from playwright.sync_api import expect

from models import Folder, Hashtag, JournalEntry, List, SmartFolder, Todo
from test_daisyui_test import HX, prototype, prototype_server


def smart_data(db, ids):
    tag = db.get(Hashtag, ids['tag'])
    skip = Hashtag(name='skip')
    db.add(skip)
    db.flush()
    parent = db.get(Todo, ids['todo'])
    child = Todo(title='Pack a thermos', list_id=ids['list'], parent_id=parent.id)
    other_child = Todo(title='Skip me', list_id=ids['list'], parent_id=parent.id, hashtags=[skip])
    filed = db.get(List, ids['other'])
    filed.folder_id = ids['folder']
    weekend = Todo(title='Weekend tagged task', list_id=ids['other'], hashtags=[tag])
    db.add_all([child, other_child, weekend])
    db.commit()
    return {**ids, 'skip': skip.id, 'child': child.id, 'other_child': other_child.id, 'weekend': weekend.id}


def daisy_form(ids, **changes):
    form = {'name': 'Personal focus', 'show_lists': 'true', 'show_todos': 'true', 'section_order': 'TLE',
            'group_todos_by_list': 'true', 'nest_subtodos': 'true', 'uncompleted_only': 'true',
            f'hashtag_mode_{ids["tag"]}': 'include', f'subtodos_{ids["tag"]}': 'true',
            f'hashtag_mode_{ids["skip"]}': 'exclude',
            'list_sort_field': 'created_at', 'list_sort_dir': 'asc', 'todo_sort_field': 'modified_at', 'todo_sort_dir': 'desc'}
    form.update(changes)
    return {key: value for key, value in form.items() if value is not None}


def test_create_matches_original_and_returns_results(prototype):
    db, client, ids = prototype
    ids = smart_data(db, ids)
    response = client.post('/app/smart-folders?view=smart-index', headers=HX, data=daisy_form(ids))
    assert response.status_code == 200 and 'Smart folder saved.' in response.text
    daisy = db.query(SmartFolder).filter_by(name='Personal focus').one()
    assert response.headers['HX-Push-Url'] == f'/app/smart-folder/{daisy.id}'
    original_form = {'name': 'Original focus', 'show_lists': 'on', 'show_todos': 'on', 'section_order': 'TLE',
                     'group_todos_by_list': 'on', 'nest_subtodos': 'on', 'uncompleted_only': 'on',
                     'hashtag_ids': [str(ids['tag'])], f'include_subtodos_{ids["tag"]}': 'on',
                     'exclude_hashtag_ids': [str(ids['skip'])],
                     'list_sort_field': 'created_at', 'list_sort_dir': 'asc', 'todo_sort_field': 'modified_at', 'todo_sort_dir': 'desc',
                     'dokuwiki_date_field': 'created_at'}
    assert client.post('/smart-folder/create', data=original_form, follow_redirects=False).status_code == 303
    original = db.query(SmartFolder).filter_by(name='Original focus').one()
    columns = [c.name for c in SmartFolder.__table__.columns if c.name not in {'id', 'name', 'created_at', 'modified_at', 'sort_order'}]
    assert {c: getattr(daisy, c) for c in columns} == {c: getattr(original, c) for c in columns}
    rules = lambda f: ([(a.hashtag_id, a.include_subtodos, a.include_list_items) for a in f.include_hashtags_assoc],
                       [(a.hashtag_id, a.include_subtodos) for a in f.exclude_hashtags_assoc])
    assert rules(daisy) == rules(original)
    assert db.query(JournalEntry).filter_by(action='Created Smart Folder', entity_id=daisy.id).count() == 1

    page = client.get(f'/app/smart-folder/{daisy.id}')
    reference = client.get(f'/smart-folder/{original.id}')
    assert page.status_code == reference.status_code == 200
    assert [t.id for t in page.context['todos']] == [t.id for t in reference.context['todos']]
    assert [item.id for item in page.context['lists']] == [item.id for item in reference.context['lists']]
    assert page.context['sections'] == ['T', 'L']
    rows = [(todo.id, depth) for group in page.context['groups'] for todo, depth in group['rows']]
    # The excluded child is filtered by the query, but nesting shows children of matching parents.
    assert (ids['todo'], 0) in rows and (ids['child'], 1) in rows and (ids['weekend'], 0) in rows
    assert [group['path'] for group in page.context['groups'] if group['list'].id == ids['other']] == ['Projects']
    assert 'Pack a thermos' in page.text and 'not #skip' in page.text


def test_editor_round_trip_validation_and_folder_rules(prototype):
    db, client, ids = prototype
    ids = smart_data(db, ids)
    client.post('/app/smart-folders?view=smart-index', headers=HX, data=daisy_form(ids))
    folder = db.query(SmartFolder).one()
    edit = client.get(f'/app/smart-folder/{folder.id}/edit')
    assert edit.status_code == 200
    values = edit.context['values']
    assert values['hashtags'] == {ids['tag']: {'mode': 'include', 'subtodos': True, 'list_items': False},
                                  ids['skip']: {'mode': 'exclude', 'subtodos': False, 'list_items': False}}
    assert values['section_order'] == 'TLE' and values['list_sort_dir'] == 'asc'

    url = f'/app/smart-folder/{folder.id}/edit?view=smart-index'
    for changes, text in [({'name': ' '}, 'Enter a name'), ({'modified_after': '2027-02-30'}, 'valid date'),
                          ({'modified_after': '2027-03-02', 'modified_before': '2027-03-01'}, 'on or before'),
                          ({'show_lists': None, 'show_todos': None}, 'Show lists, tasks')]:
        bad = client.post(url, headers=HX, data=daisy_form(ids, **changes))
        assert bad.status_code == 400 and text in bad.text and bad.headers['HX-Retarget'] == '#page-feedback'
    assert client.post(url, headers=HX, data=daisy_form(ids, section_order='XYZ')).status_code == 400
    plain = client.post(url, data=daisy_form(ids, name=''))
    assert plain.status_code == 400 and '<!DOCTYPE html>' in plain.text and 'value="2027' not in plain.text

    saved = client.post(url, headers=HX, data=daisy_form(
        ids, **{f'hashtag_mode_{ids["skip"]}': None, f'folder_mode_{ids["folder"]}': 'include', 'folder_mode_root': 'exclude',
                'list_ids': [str(ids['other'])], 'modified_after': '2020-01-01', 'modified_before': '2030-01-01'}))
    assert saved.status_code == 200
    db.expire_all()
    assert [f.id for f in folder.include_folders] == [ids['folder']] and folder.exclude_root_folder and not folder.include_root_folder
    assert [item.id for item in folder.lists] == [ids['other']] and not folder.exclude_hashtags_assoc
    assert folder.modified_before == datetime(2030, 1, 2)
    assert client.get(f'/app/smart-folder/{folder.id}/edit').context['values']['modified_before'] == '2030-01-01'
    assert db.query(JournalEntry).filter_by(action='Updated Smart Folder', entity_id=folder.id).count() == 1
    assert client.get('/app/smart-folder/9999').status_code == 404
    assert client.get('/app/smart-folder/9999/edit').status_code == 404


def test_index_order_delete_and_mutations_return_to_results(prototype):
    db, client, ids = prototype
    ids = smart_data(db, ids)
    for name in ('First', 'Second'):
        client.post('/app/smart-folders?view=smart-index', headers=HX, data=daisy_form(ids, name=name))
    first, second = (db.query(SmartFolder).filter_by(name=name).one() for name in ('First', 'Second'))
    index = client.get('/app/smart-folders')
    assert index.status_code == 200 and 'aria-current="page"' in index.text
    names = lambda: [f.name for f in client.get('/app/smart-folders').context['smart_folders']]
    assert names() == ['Second', 'First']  # newest first until reordered, as in the original
    client.post(f'/app/smart-folder/{first.id}/order/up?view=smart-index', headers=HX)
    assert names() == ['First', 'Second']
    client.post(f'/app/smart-folder/{first.id}/order/down?view=smart-index', headers=HX)
    assert names() == ['Second', 'First']
    assert client.post(f'/app/smart-folder/{first.id}/order/sideways?view=smart-index', headers=HX).status_code == 400

    query = f'?sort=user&view=smart&smart_folder_id={first.id}'
    done = client.post(f'/app/complete/todo/{ids["weekend"]}{query}', headers=HX, data={'completed': 'true'})
    assert done.status_code == 200 and 'Completion updated.' in done.text and 'Edit rules' in done.text
    assert 'Weekend tagged task' not in done.text  # open items only
    edit = client.post(f'/app/todo/{ids["todo"]}/edit{query}&back=results', headers=HX,
                       data={'title': 'Renamed afternoon', 'note': '', 'hashtags': '#personal', 'alarm_mode': 'none'})
    assert edit.headers['HX-Push-Url'] == f'/app/smart-folder/{first.id}?sort=user' and 'Renamed afternoon' in edit.text

    deleted = client.post(f'/app/smart-folder/{first.id}/delete?view=smart-index', headers=HX)
    assert deleted.status_code == 200 and 'Deleted smart folder First.' in deleted.text
    assert deleted.headers['HX-Push-Url'] == '/app/smart-folders'
    db.expire_all()
    assert db.query(SmartFolder).count() == 1
    assert db.query(JournalEntry).filter_by(action='Deleted Smart Folder').count() == 1


def test_browser_smart_folder_workflow(prototype_server, page):
    origin, (db, _, ids) = prototype_server
    ids = smart_data(db, ids)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(origin + '/app')
    page.locator('.toolbar-navigation').get_by_role('link', name='Smart Folders').click()
    expect(page).to_have_url(origin + '/app/smart-folders')
    page.get_by_role('link', name='New smart folder').first.click()
    page.get_by_role('textbox', name='Name').fill('Personal')
    page.locator('summary', has_text='rules').first.click()
    page.get_by_role('combobox', name='Rule for #personal').select_option('include')
    page.get_by_role('button', name='Create smart folder').click()
    expect(page.get_by_role('heading', name='Personal', exact=True)).to_be_visible()
    expect(page.get_by_text('Weekend tagged task')).to_be_visible()
    page.get_by_role('checkbox', name='Mark Weekend tagged task complete', exact=True).check()
    expect(page.locator('#page-feedback')).to_contain_text('Completion updated.')
    page.get_by_role('link', name='Edit rules').first.click()
    page.get_by_role('checkbox', name='Open items only').check()
    page.get_by_role('button', name='Save changes').click()
    expect(page.locator('#page-feedback')).to_contain_text('Smart folder saved.')
    expect(page.get_by_text('Weekend tagged task')).to_be_hidden()
    page.get_by_role('link', name='Edit rules').first.click()
    page.once('dialog', lambda dialog: dialog.accept())
    page.get_by_role('button', name='Delete').click()
    expect(page).to_have_url(origin + '/app/smart-folders')
    expect(page.get_by_text('No smart folders yet.')).to_be_visible()
    assert not errors

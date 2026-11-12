"""Smart folder index, results and editor in the daisyUI frontend."""
from datetime import datetime, timedelta

from playwright.sync_api import expect

from models import CalendarEvent, Folder, Hashtag, JournalEntry, List, Note, SmartFolder, Todo
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
            'group_todos_by_list': 'true', 'subtasks': 'nest', 'uncompleted_only': 'true',
            'include': [f'tag:{ids["tag"]}'], 'subtodos': 'true',
            'exclude': [f'tag:{ids["skip"]}'], 'scope': 'all', 'scope_shown': '1', 'show_events': 'true',
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
    columns = [c.name for c in SmartFolder.__table__.columns if c.name not in {'id', 'name', 'created_at', 'modified_at', 'sort_order', 'include_subtodos'}]
    assert {c: getattr(daisy, c) for c in columns} == {c: getattr(original, c) for c in columns}
    rules = lambda f: ([(a.hashtag_id, a.include_subtodos, a.include_list_items) for a in f.include_hashtags_assoc],
                       [a.hashtag_id for a in f.exclude_hashtags_assoc])
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
    assert 'Pack a thermos' in page.text and 'not skip' in page.text


def test_editor_round_trip_validation_and_folder_rules(prototype):
    db, client, ids = prototype
    ids = smart_data(db, ids)
    client.post('/app/smart-folders?view=smart-index', headers=HX, data=daisy_form(ids))
    folder = db.query(SmartFolder).one()
    edit = client.get(f'/app/smart-folder/{folder.id}/edit')
    assert edit.status_code == 200
    values = edit.context['values']
    assert values['include'] == [f'tag:{ids["tag"]}']
    assert values['exclude'] == [f'tag:{ids["skip"]}'] and values['subtodos']
    assert values['section_order'] == 'TLE' and values['list_sort_dir'] == 'asc'

    url = f'/app/smart-folder/{folder.id}/edit?view=smart-index'
    for changes, text in [({'name': ' '}, 'Enter a name'), ({'modified_after': '2027-02-30'}, 'valid date'),
                          ({'modified_after': '2027-03-02', 'modified_before': '2027-03-01'}, 'on or before'),
                          ({'show_lists': None, 'show_todos': None, 'show_events': None}, 'Show lists, tasks')]:
        bad = client.post(url, headers=HX, data=daisy_form(ids, **changes))
        assert bad.status_code == 400 and text in bad.text and bad.headers['HX-Retarget'] == '#daisy-workspace'
    assert client.post(url, headers=HX, data=daisy_form(ids, section_order='XYZ')).status_code == 400
    plain = client.post(url, data=daisy_form(ids, name=''))
    assert plain.status_code == 400 and '<!DOCTYPE html>' in plain.text and 'value="2027' not in plain.text

    saved = client.post(url, headers=HX, data=daisy_form(
        ids, exclude=None, scope='only', places=[f'folder:{ids["folder"]}'],
        lists=[str(ids['other'])], modified_after='2020-01-01', modified_before='2030-01-01'))
    assert saved.status_code == 200
    db.expire_all()
    assert [f.id for f in folder.include_folders] == [ids['folder']] and not folder.exclude_root_folder and not folder.include_root_folder
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
    page.get_by_role('combobox', name='Add a tag or hashtag', exact=True).select_option(f'tag:{ids["tag"]}')
    expect(page.locator('#smart-preview')).to_contain_text('Weekend tagged task')
    expect(page.get_by_role('textbox', name='Name', exact=True)).to_have_value('Personal')
    page.get_by_role('button', name='Create smart folder').click()
    expect(page.get_by_role('heading', name='Personal', exact=True)).to_be_visible()
    expect(page.get_by_text('Weekend tagged task')).to_be_visible()
    page.get_by_role('checkbox', name='Mark Weekend tagged task complete', exact=True).check()
    expect(page.locator('#page-feedback')).to_contain_text('Completion updated.')
    page.get_by_role('link', name='Edit rules').first.click()
    page.get_by_role('checkbox', name='Still open (not completed)').check()
    expect(page.locator('#smart-preview')).not_to_contain_text('Weekend tagged task')
    page.get_by_role('button', name='Save changes').click()
    expect(page.locator('#page-feedback')).to_contain_text('Smart folder saved.')
    expect(page.get_by_text('Weekend tagged task')).to_be_hidden()
    page.get_by_role('link', name='Edit rules').first.click()
    page.once('dialog', lambda dialog: dialog.accept())
    page.get_by_role('button', name='Delete').click()
    expect(page).to_have_url(origin + '/app/smart-folders')
    expect(page.get_by_text('No smart folders yet.', exact=False)).to_be_visible()
    assert not errors


def test_tags_and_text_hashtags_match_any_all_and_exclude_item_notes(prototype):
    db, client, ids = prototype
    tag = db.get(Hashtag, ids['tag'])
    item, task = db.get(List, ids['list']), db.get(Todo, ids['todo'])
    item.name += ' #HOME'
    item.hashtags = [tag]
    task.title += ' #home'
    other_task = db.get(Todo, ids['old'])
    other_task.note = '#home'
    unrelated_event = db.query(CalendarEvent).first()
    event = CalendarEvent(title='Tagged event #home', hashtags=[tag], start_at=datetime(2027, 1, 1))
    db.add_all([event, Note(list_id=ids['other'], text='List note #home'),
                Note(calendar_event=unrelated_event, text='Unrelated #blocked')])
    db.commit()
    form = dict(name='Both kinds', show_lists='true', show_todos='true', show_events='true',
                include=[f'tag:{tag.id}', 'hash:HOME'], match='all', exclude=['hash:blocked'])
    response = client.post('/app/smart-folders', headers=HX, data=form)
    assert [row.id for row in response.context['lists']] == [item.id]
    assert [row.id for row in response.context['todos']] == [task.id]
    assert [row.id for row in response.context['events']] == [event.id]
    saved = db.query(SmartFolder).one()
    assert saved.include_text_hashtags == ['home']
    response = client.post(f'/app/smart-folder/{saved.id}/edit', headers=HX, data={**form, 'match': 'any'})
    assert {row.id for row in response.context['lists']} == {ids['list'], ids['other']}
    assert {row.id for row in response.context['todos']} == {ids['todo'], ids['old']}
    # A note on a task excludes that task, without excluding its list or other tasks.
    db.add(Note(todo_id=task.id, list_id=item.id, text='Task note #blocked'))
    db.commit()
    response = client.get(f'/app/smart-folder/{saved.id}')
    assert [row.id for row in response.context['todos']] == [other_task.id]
    assert {row.id for row in response.context['lists']} == {ids['list'], ids['other']}


def test_note_hashtags_attach_to_their_own_item_and_calendar_dates_apply(prototype):
    db, client, ids = prototype
    task = db.get(Todo, ids['todo'])
    event = db.query(CalendarEvent).one()
    event.modified_at = datetime(2026, 1, 1)
    db.add_all([Note(list_id=ids['other'], text='List #notes'),
                Note(list_id=ids['list'], todo_id=task.id, text='Task #notes'),
                Note(calendar_event_id=event.id, text='Event #notes')])
    db.commit()
    form = dict(name='Notes', include='hash:notes', show_lists='true', show_todos='true', show_events='true')
    response = client.post('/app/smart-folders', headers=HX, data=form)
    assert [row.id for row in response.context['lists']] == [ids['other']]
    assert [row.id for row in response.context['todos']] == [task.id]
    assert [row.id for row in response.context['events']] == [event.id]
    folder = db.query(SmartFolder).one()
    response = client.post(f'/app/smart-folder/{folder.id}/draft', headers=HX,
                           data={**form, 'modified_after': '2026-02-01'})
    assert response.context['matches']['events'] == 0
    db.expire_all()
    assert folder.modified_after is None


def test_drafts_and_validation_do_not_save_and_additions_can_be_removed(prototype):
    db, client, ids = prototype
    form = dict(name='Future work', show_todos='true', new_include='#Future, #école_2')
    draft = client.post('/app/smart-folders/draft', headers=HX, data=form)
    assert draft.status_code == 200 and draft.context['values']['include'] == ['hash:future', 'hash:école_2']
    assert db.query(SmartFolder).count() == 0
    assert db.query(JournalEntry).count() == 0
    created = client.post('/app/smart-folders', data=form, follow_redirects=False)
    assert created.status_code == 303
    folder = db.query(SmartFolder).one()
    original_modified = folder.modified_at
    url = f'/app/smart-folder/{folder.id}'
    for _ in range(2):
        draft = client.post(url + '/draft', headers=HX, data=dict(
            name='Unsaved name', show_lists='true', add_include=f'tag:{ids["tag"]}',
            scope='only', places=f'folder:{ids["folder"]}'))
        assert draft.context['values']['include'] == [f'tag:{ids["tag"]}']
        db.expire_all()
        assert folder.name == 'Future work' and folder.modified_at == original_modified
        assert folder.include_text_hashtags == ['future', 'école_2'] and not folder.include_hashtags_assoc
        assert not folder.include_folders
    assert db.query(JournalEntry).count() == 1
    bad = client.post(url + '/edit', headers=HX, data={**form, 'modified_after': 'not-a-date'})
    assert bad.status_code == 400 and bad.context['matches'] is None
    db.expire_all()
    assert folder.modified_at == original_modified
    cleared = client.post(url + '/edit', headers=HX, data=dict(name=folder.name, show_todos='true'))
    assert cleared.status_code == 200
    db.expire_all()
    assert folder.include_text_hashtags == [] and folder.exclude_text_hashtags == []
    assert client.post(url + '/edit', headers=HX, data={**form, 'new_include': '#123'}).status_code == 400


def test_folder_scope_root_subfolders_and_legacy_combined_exclusions(prototype):
    db, client, ids = prototype
    parent = db.get(Folder, ids['folder'])
    child = Folder(name='Child', parent=parent)
    filed = List(name='Direct', folder=parent)
    nested = List(name='Nested', folder=child)
    db.get(List, ids['other']).folder = parent
    legacy = SmartFolder(name='Legacy', include_folders=[parent], exclude_folders=[child],
                         include_root_folder=True, exclude_root_folder=True, include_subfolders=True)
    db.add_all([child, filed, nested, legacy])
    db.commit()
    url = f'/app/smart-folder/{legacy.id}'
    edit = client.get(url + '/edit')
    values = edit.context['values']
    assert values['scope'] == 'only'
    assert set(values['extra_exclude_places']) == {'root', f'folder:{child.id}'}
    form = dict(name='Legacy renamed', show_lists='true', scope='only',
                places=values['places'], extra_exclude_places=values['extra_exclude_places'],
                scope_shown='1', subfolders='true')
    response = client.post(url + '/edit', headers=HX, data=form)
    assert {row.id for row in response.context['lists']} == {ids['other'], filed.id}
    db.expire_all()
    assert legacy.exclude_root_folder and legacy.exclude_folders == [child]
    response = client.post(url + '/edit', headers=HX, data={**form, 'extra_exclude_places': []})
    assert {row.id for row in response.context['lists']} == {ids['list'], ids['other'], filed.id, nested.id}
    response = client.post(url + '/edit', headers=HX, data={**form, 'scope': 'except', 'places': [f'folder:{parent.id}']})
    assert [row.id for row in response.context['lists']] == [ids['list']]


def test_task_inheritance_is_recursive_and_strict_nesting_keeps_matching_children(prototype):
    db, client, ids = prototype
    ids = smart_data(db, ids)
    root = db.get(Todo, ids['todo'])
    root.note = '#home'
    grandchild = Todo(title='Deep task', list_id=root.list_id, parent_id=ids['child'])
    root.list.name += ' #work'
    db.add(grandchild)
    db.commit()
    form = dict(name='Nested', show_todos='true', include=['hash:home', f'tag:{ids["tag"]}'],
                match='all', subtodos='true', subtasks='nest_matching')
    response = client.post('/app/smart-folders', headers=HX, data=form)
    rows = [(todo.id, depth) for group in response.context['groups'] for todo, depth in group['rows']]
    assert (root.id, 0) in rows and (ids['child'], 1) in rows and (grandchild.id, 2) in rows
    folder = db.query(SmartFolder).one()
    response = client.post(f'/app/smart-folder/{folder.id}/edit', headers=HX,
                           data={**form, 'include': ['hash:work'], 'list_items': 'true', 'subtasks': 'flat'})
    assert {row.id for row in response.context['todos']} == {root.id, ids['child'], ids['other_child'], grandchild.id}
    response = client.post(f'/app/smart-folder/{folder.id}/edit', headers=HX,
                           data={**form, 'exclude': ['hash:home']})
    assert [row.id for row in response.context['todos']] == [ids['weekend']]  # Every descendant is excluded.


def test_smart_folder_schema_upgrade_and_export_import_sync(prototype):
    import copy
    from sqlalchemy import create_engine, text
    from database import run_migrations
    from import_logic import import_data_from_json
    from serializers import serialize_smart_folder
    from sync_apply import apply_smart_folder

    engine = create_engine('sqlite://')
    try:
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE smart_folders (id INTEGER PRIMARY KEY, name VARCHAR(255))'))
            connection.execute(text("INSERT INTO smart_folders (id, name) VALUES (1, 'Old rules')"))
        run_migrations(engine)
        run_migrations(engine)
        with engine.connect() as connection:
            row = connection.execute(text('SELECT name, include_text_hashtags, exclude_text_hashtags, show_events, match_all FROM smart_folders')).one()
            assert tuple(row) == ('Old rules', '[]', '[]', 1, 0)
    finally:
        engine.dispose()

    db, client, ids = prototype
    form = dict(name='Portable', show_todos='true', include=['hash:home', f'tag:{ids["tag"]}'],
                exclude=['hash:waiting'], match='all', subtodos='true', list_items='true',
                scope='only', places=f'folder:{ids["folder"]}', subfolders='true', scope_shown='1')
    client.post('/app/smart-folders', headers=HX, data=form)
    folder = db.query(SmartFolder).one()
    original = serialize_smart_folder(folder)
    assert import_data_from_json(db, {'data': {'smart_folders': [copy.deepcopy(original)]}})['errors'] == 0
    db.expire_all()
    assert serialize_smart_folder(folder) == original
    apply_smart_folder(db, {**original, 'include_text_hashtags': ['#HOME', 'work'],
                           'include_folders': [], 'exclude_folders': [dict(id=ids['folder'])],
                           'lists': [ids['list']]}, {})
    db.commit()
    assert folder.include_text_hashtags == ['home', 'work'] and folder.match_all and not folder.show_events
    assert not folder.include_folders and [f.id for f in folder.exclude_folders] == [ids['folder']]
    assert [item.id for item in folder.lists] == [ids['list']]
    apply_smart_folder(db, dict(id=folder.id, include_text_hashtags=[], lists=[]), {})
    db.commit()
    assert folder.include_text_hashtags == [] and not folder.lists
    assert [f.id for f in folder.exclude_folders] == [ids['folder']]


def test_browser_hashtag_preview_scope_and_mobile_layout(prototype_server, page):
    origin, (db, _, ids) = prototype_server
    db.get(Todo, ids['todo']).note = 'For the house #home'
    db.commit()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(origin + '/app/smart-folders/new')
    # Rule previews work before a name has been entered, and never create a folder.
    page.get_by_role('combobox', name='Add a tag or hashtag', exact=True).select_option('hash:home')
    expect(page.locator('input[name="include"][value="hash:home"]')).to_be_checked()
    expect(page.locator('#smart-preview')).to_contain_text('1 task')
    assert db.query(SmartFolder).count() == 0
    typed = page.get_by_role('textbox', name='Include another hashtag')
    typed.fill('#future')
    typed.press('Enter')
    expect(page.locator('input[name="include"][value="hash:future"]')).to_be_checked()
    expect(typed).to_have_value('')
    page.get_by_role('combobox', name='How many of the chosen tags and hashtags an item needs').select_option('all')
    expect(page.locator('#smart-preview')).to_contain_text('Nothing matches')
    page.get_by_title('Remove #future', exact=True).click()
    expect(page.locator('#smart-preview')).to_contain_text('1 task')
    expect(page.locator('input[name="include"][value="hash:future"]')).to_have_count(0)
    page.get_by_role('radio', name='Only these folders', exact=True).check()
    page.get_by_role('combobox', name='Add a folder', exact=True).select_option('root')
    expect(page.locator('input[name="places"][value="root"]')).to_be_checked()
    page.get_by_role('textbox', name='Name', exact=True).fill('House jobs')
    page.get_by_text('Layout and sorting', exact=False).click()
    page.get_by_role('checkbox', name='Group tasks under their list').uncheck()
    expect(page.locator('details[data-persist="smart-layout"]')).to_have_attribute('open', '')
    expect(page.get_by_role('textbox', name='Name', exact=True)).to_have_value('House jobs')
    page.screenshot(path='/tmp/smart-editor-desktop.png', full_page=True)
    for width in (390, 320):
        page.set_viewport_size({'width': width, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.screenshot(path='/tmp/smart-editor-mobile.png', full_page=True)
    # Saving immediately after typing also includes the new value.
    page.get_by_role('textbox', name='Exclude another hashtag').fill('#waiting')
    page.get_by_role('button', name='Create smart folder').click()
    expect(page.get_by_role('heading', name='House jobs', exact=True)).to_be_visible()
    db.expire_all()
    folder = db.query(SmartFolder).one()
    assert folder.include_text_hashtags == ['home'] and folder.exclude_text_hashtags == ['waiting']
    assert folder.include_root_folder and not folder.group_todos_by_list
    assert not errors

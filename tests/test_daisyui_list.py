"""List detail parity and stateful HTMX workflows in the daisyUI frontend."""
from datetime import datetime, timedelta
from html.parser import HTMLParser

import pytest
from playwright.sync_api import expect
from fastapi.testclient import TestClient

from models import List, Todo, Note, ListDatetimeHeader, JournalEntry, IngredientEvent, DokuWikiConfirmedItem
from test_daisyui_test import prototype, prototype_server, HX
from test_reverse_proxy import PrefixStrippingProxy
import main


@pytest.fixture
def detail(prototype):
    db, client, ids = prototype
    root = db.get(Todo, ids['todo'])
    child = Todo(title='A child task', list=root.list, parent=root)
    grandchild = Todo(title='A grandchild task', list=root.list, parent=child)
    source = List(name='Another source', folder_id=ids['folder'])
    external = Todo(title='Collected source task', list=source)
    external_child = Todo(title='Collected child task', list=source, parent=external)
    collection = List(name='Collected view', is_collation=True, collation_color='#77bb88', collated_todos=[external])
    db.add_all([child, grandchild, source, external, external_child, collection])
    db.flush()
    note = Note(name='List note', text='Keep these details', list_id=ids['list'])
    child_note = Note(name='Child note', text='Attached to a child', list_id=ids['list'], todo=child)
    external_note = Note(text='Collected task note', list_id=source.id, todo=external_child)
    divider = ListDatetimeHeader(list_id=ids['list'], created_at=datetime.now() - timedelta(hours=2))
    db.add_all([note, child_note, external_note, divider])
    db.commit()
    ids.update(child=child.id, grandchild=grandchild.id, note=note.id, child_note=child_note.id,
               divider=divider.id, external=external.id, external_child=external_child.id,
               external_note=external_note.id, source=source.id, collection=collection.id)
    return db, client, ids


def path(ids, suffix=''):
    return f'/app/list/{ids["list"]}' + suffix


def test_shared_list_query_preserves_hierarchy_notes_dividers_and_navigation(detail):
    db, client, ids = detail
    original = client.get(f'/list/{ids["list"]}?sort=created&folder_id=0')
    full = client.get(path(ids) + '?sort=created&folder_id=0')
    fragment = client.get(path(ids), headers=HX)
    restored = client.get(path(ids), headers={**HX, 'HX-History-Restore-Request': 'true'})
    assert full.status_code == fragment.status_code == restored.status_code == 200
    assert '<!DOCTYPE html>' in full.text and '<!DOCTYPE html>' not in fragment.text
    assert '<!DOCTYPE html>' in restored.text
    for key in ('prev_list_id', 'next_list_id', 'first_list_id', 'active_list_index', 'total_active_lists', 'total_todo_count'):
        assert full.context[key] == original.context[key]
    assert [(item['type'], item.get('obj').id if item.get('obj') else None) for item in full.context['items']] == [(item['type'], item.get('obj').id if item.get('obj') else None) for item in original.context['items']]
    for text in ('A child task', 'A grandchild task', 'Keep these details', 'Attached to a child'):
        assert text in full.text
    assert 'data-item-key="datetime_header:' in full.text
    assert f'/app/list/{ids["list"]}' in client.get('/app').text
    assert client.get('/app/list/999999').status_code == 404


def test_add_tasks_subtasks_and_notes_return_list_fragments(detail):
    db, client, ids = detail
    for title, parent in [('New root task', ''), ('New subtask', str(ids['child']))]:
        response = client.post(path(ids, '/todos') + '?folder_id=0&sort=created', headers=HX,
                               data={'title': title, 'parent_id': parent, 'hashtags': '#personal #next'})
        assert response.status_code == 200
        assert response.context['list'].id == ids['list'] and response.context['sort'] == 'created'
        created = db.query(Todo).filter_by(title=title).one()
        assert created.parent_id == (int(parent) if parent else None)
        assert {tag.name for tag in created.hashtags} == {'personal', 'next'}
    response = client.post(path(ids, '/notes'), headers=HX,
                           data={'name': 'New task note', 'text': '<script>safe text</script>', 'todo_id': ids['child']})
    assert response.status_code == 200 and '<script>safe text</script>' not in response.text
    note = db.query(Note).filter_by(name='New task note').one()
    assert note.list_id == ids['list'] and note.todo_id == ids['child']
    assert client.post(path(ids, '/todos'), headers=HX, data={'title': '   '}).status_code == 400
    assert client.post(path(ids, '/notes'), headers=HX, data={'text': ''}).status_code == 400
    assert client.post(path(ids, '/todos'), headers=HX, data={'title': 'Wrong parent', 'parent_id': ids['external']}).status_code == 400
    assert not db.query(Todo).filter_by(title='Wrong parent').count()


def test_completion_options_and_form_redirects_stay_in_new_frontend(detail):
    db, client, ids = detail
    response = client.post(f'/app/complete/todo/{ids["todo"]}?view_list_id={ids["list"]}', headers=HX, data={'completed': 'true'})
    assert response.status_code == 200 and response.context['list'].id == ids['list']
    for enabled in ('false', 'true'):
        response = client.post(path(ids, '/options/show_completed'), headers=HX, data={'enabled': enabled})
        assert response.status_code == 200
        assert response.context['list'].show_completed == (enabled == 'true')
        assert ('data-task-title="Book a quiet afternoon"' in response.text) == (enabled == 'true')
    response = client.post(path(ids, '/todos') + '?sort=created&folder_id=0', data={'title': 'Plain form'}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'] == path(ids) + '?sort=created&folder_id=0'
    assert client.post(path(ids, '/options/unknown'), headers=HX, data={'enabled': 'true'}).status_code == 400
    assert client.post(path(ids, '/options/is_recipe'), headers=HX, data={'enabled': 'true'}).status_code == 400


def test_completed_highlights_and_deferred_filters_follow_original_semantics(detail):
    db, client, ids = detail
    item = db.get(List, ids['list'])
    item.show_completed = False
    item.show_delayed = False
    db.get(Todo, ids['todo']).completed = True
    db.get(Todo, ids['child']).completed = True
    db.add(Todo(title='A future task', list_id=ids['list'], delayed_until=datetime.now() + timedelta(days=10)))
    db.commit()
    response = client.get(path(ids))
    assert f'data-item-key="todo:{ids["todo"]}"' not in response.text
    assert 'A future task' not in response.text
    response = client.get(path(ids) + f'?highlight={ids["child"]}')
    assert f'data-item-key="todo:{ids["todo"]}"' in response.text
    assert f'data-item-key="todo:{ids["child"]}"' in response.text
    assert response.context['show_completed']
    response = client.post(path(ids, '/options/show_delayed'), headers=HX, data={'enabled': 'true'})
    assert response.status_code == 200 and 'A future task' in response.text


def test_collated_subtasks_and_notes_belong_to_the_source_list(detail):
    db, client, ids = detail
    collection = f'/app/list/{ids["collection"]}'
    response = client.get(collection)
    for title in ('Collected source task', 'Collected child task', 'Collected task note'):
        assert title in response.text
    assert client.post(collection + '/todos', headers=HX, data={'title': 'Child of collected task', 'parent_id': ids['external']}).status_code == 200
    task = db.query(Todo).filter_by(title='Child of collected task').one()
    assert task.list_id == ids['source'] and task.parent_id == ids['external']
    assert client.post(collection + '/notes', headers=HX, data={'text': 'New collected note', 'todo_id': ids['external_child']}).status_code == 200
    note = db.query(Note).filter_by(text='New collected note').one()
    assert note.list_id == ids['source'] and note.todo_id == ids['external_child']
    for enabled in ('true', 'true', 'false'):
        response = client.post(path(ids, f'/collation/{ids["collection"]}/{ids["todo"]}'), headers=HX, data={'enabled': enabled})
        assert response.status_code == 200
        db.expire_all()
        assert (db.get(List, ids['collection']) in db.get(Todo, ids['todo']).collation_lists) == (enabled == 'true')


def test_manual_order_category_recipe_and_ingredient_actions(detail):
    db, client, ids = detail
    assert client.post(path(ids, '/options/manual_sorting'), headers=HX, data={'enabled': 'true'}).status_code == 200
    assert client.post(path(ids, '/options/is_recipe'), headers=HX, data={'enabled': 'true'}).status_code == 200
    for action in ('bought', 'ran_out'):
        response = client.post(path(ids, '/selected/' + action), headers=HX, data={'items': f'todo:{ids["todo"]}'})
        assert response.status_code == 200
    assert db.query(IngredientEvent).filter_by(todo_id=ids['todo']).count() == 2
    assert client.post(path(ids, '/ingredients/reset'), headers=HX).status_code == 200
    assert db.query(IngredientEvent).filter_by(event_type='reset').count() == 3
    response = client.post(path(ids, '/reorder'), headers=HX,
                           data={'items': f'todo:{ids["todo"]}', 'target_id': ids['child']})
    assert response.status_code == 200
    db.expire_all()
    assert db.get(Todo, ids['todo']).sort_order > db.get(Todo, ids['child']).sort_order
    assert client.post(path(ids, '/category'), headers=HX, data={'category': 'Desktop work'}).status_code == 200
    db.expire_all()
    assert db.get(List, ids['list']).category == 'Desktop work'


def test_reorder_preserves_the_requested_relative_order_of_multiple_tasks(detail):
    db, client, ids = detail
    client.post(path(ids, '/options/manual_sorting'), headers=HX, data={'enabled': 'true'})
    for title in ('Move second', 'Move first'):
        client.post(path(ids, '/todos'), headers=HX, data={'title': title})
    second = db.query(Todo).filter_by(title='Move second').one()
    first = db.query(Todo).filter_by(title='Move first').one()
    response = client.post(path(ids, '/reorder'), headers=HX,
                           data={'items': [f'todo:{first.id}', f'todo:{second.id}'], 'target_id': ids['todo']})
    assert response.status_code == 200
    db.expire_all()
    assert db.get(Todo, ids['todo']).sort_order < first.sort_order < second.sort_order


@pytest.mark.parametrize('prefix', ['', '/simple_todo', '/apps/simple_todo'])
def test_detail_urls_and_form_mutations_respect_the_public_prefix(detail, monkeypatch, prefix):
    db, _, ids = detail
    monkeypatch.setattr(main.app, 'root_path', prefix)
    client = TestClient(PrefixStrippingProxy(main.app, prefix))
    response = client.get(prefix + path(ids))
    assert response.status_code == 200

    class URLs(HTMLParser):
        def handle_starttag(self, tag, attrs):
            for key, value in attrs:
                if key in {'href', 'src', 'action', 'hx-post', 'hx-get'} and value and value.startswith('/'):
                    assert not prefix or value.startswith(prefix + '/'), (key, value)

    URLs().feed(response.text)
    response = client.post(prefix + path(ids, '/todos'), headers=HX, data={'title': 'Created under prefix'})
    assert response.status_code == 200
    assert f'action="{prefix}/app/list/' in response.text


def test_selected_task_metadata_and_validation(detail):
    db, client, ids = detail
    selection = {'items': f'todo:{ids["todo"]}'}
    response = client.post(path(ids, '/selected/defer'), headers=HX, data={**selection, 'delayed_until': '2027-04-05'})
    assert response.status_code == 200
    db.expire_all()
    assert db.get(Todo, ids['todo']).delayed_until.date().isoformat() == '2027-04-05'
    assert 'deferred' in {tag.name for tag in db.get(Todo, ids['todo']).hashtags}
    assert client.post(path(ids, '/selected/hashtags'), headers=HX, data={**selection, 'hashtags': '#desktop'}).status_code == 200
    db.expire_all()
    assert {tag.name for tag in db.get(Todo, ids['todo']).hashtags} == {'desktop', 'deferred'}
    assert client.post(path(ids, '/selected/defer'), headers=HX, data=selection).status_code == 200
    for mode in ('range', 'tolerance', 'none'):
        response = client.post(path(ids, '/selected/alarm'), headers=HX,
                               data={**selection, 'alarm_mode': mode, 'alarm_start_range': '2027-04-05T09:00',
                                     'alarm_end_range': '2027-04-05T10:00', 'alarm_target_time': '2027-04-05T09:00', 'alarm_minutes': '15'})
        assert response.status_code == 200
    db.expire_all()
    assert db.get(Todo, ids['todo']).alarm_start is None
    response = client.post(path(ids, '/selected/alarm'), headers=HX,
                           data={**selection, 'alarm_mode': 'range', 'alarm_start_range': '2027-04-05T11:00', 'alarm_end_range': '2027-04-05T10:00'})
    assert response.status_code == 400
    assert client.post(path(ids, '/selected/hashtags'), headers=HX, data={'items': [f'todo:{ids["todo"]}', f'todo:{ids["child"]}']}).status_code == 400
    assert client.post(path(ids, '/selected/hashtags'), headers=HX, data={'items': f'todo:{ids["external"]}'}).status_code == 400


def test_notes_headers_and_mixed_deletion_are_scoped_before_any_mutation(detail):
    db, client, ids = detail
    url = f'/app/bulk?view_list_id={ids["list"]}'
    for values in ([f'note:{ids["external_note"]}'], [f'list:{ids["list"]}'], [f'todo:{ids["todo"]}', f'note:{ids["external_note"]}']):
        assert client.post(url, headers=HX, data={'action': 'delete', 'items': values}).status_code == 400
    assert db.get(Todo, ids['todo']) is not None
    assert client.post(url, headers=HX, data={'action': 'pin', 'items': [f'todo:{ids["todo"]}', f'note:{ids["note"]}']}).status_code == 400
    values = [f'note:{ids["child_note"]}', f'datetime_header:{ids["divider"]}', f'todo:{ids["child"]}']
    response = client.post(url, headers=HX, data={'action': 'delete', 'items': values})
    assert response.status_code == 200
    db.expire_all()
    assert db.get(Note, ids['child_note']) is None
    assert db.get(Todo, ids['child']) is None and db.get(Todo, ids['grandchild']) is None
    assert db.get(ListDatetimeHeader, ids['divider']) is None
    assert db.get(Todo, ids['external']) is not None
    assert client.post(f'/app/complete/todo/{ids["external"]}?view_list_id={ids["list"]}', headers=HX, data={'completed': 'true'}).status_code == 400


def test_dividers_and_wiki_confirmation_reuse_existing_services(detail):
    db, client, ids = detail
    assert client.post(path(ids, '/dividers'), headers=HX).status_code == 200
    assert db.query(ListDatetimeHeader).filter_by(list_id=ids['list']).count() == 2
    selection = {'items': f'todo:{ids["todo"]}'}
    assert client.post(path(ids, '/selected/wiki-enable'), headers=HX, data=selection).status_code == 200
    db.expire_all()
    assert db.get(Todo, ids['todo']).dokuwiki_page_enabled
    assert client.post(path(ids, '/selected/wiki-disable'), headers=HX, data=selection).status_code == 200
    for _ in range(5):
        assert client.post(path(ids, '/wiki/confirm'), headers=HX, data={'todo_ids': ids['todo']}).status_code == 200
    assert db.query(DokuWikiConfirmedItem).filter_by(item_type='todo', item_id=ids['todo']).count() == 1
    assert client.post(path(ids, '/wiki/unconfirm'), headers=HX, data={'todo_ids': ids['todo']}).status_code == 200
    db.expire_all()
    assert db.query(DokuWikiConfirmedItem).filter_by(item_type='todo', item_id=ids['todo']).count() == 0


def test_browser_list_workflow_preserves_drafts_selection_and_inline_actions(detail, prototype_server, page):
    origin, _ = prototype_server
    _, _, ids = detail
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(origin + '/app')
    page.get_by_role('link', name='A place to start', exact=True).last.click()
    page.wait_for_url(f'**/app/list/{ids["list"]}?*')
    page.evaluate('window.retainedListDocument = true')
    # The title field is always there; "+" reveals the tags and the Add button.
    expect(page.get_by_role('button', name='New task', exact=True)).to_have_count(0)
    expect(page.get_by_role('button', name='Add task', exact=True)).to_be_hidden()
    page.get_by_role('button', name='Tags and Add button for the new task').click()
    page.get_by_role('textbox', name='Task title', exact=True).fill('Created on desktop')
    page.get_by_role('button', name='Add task', exact=True).click()
    expect(page.locator('.task-row').filter(has_text='Created on desktop')).to_have_count(1)
    expect(page.get_by_role('textbox', name='Task title', exact=True)).to_have_value('')
    expect(page.get_by_role('textbox', name='Task title', exact=True)).to_be_focused()
    page.get_by_role('textbox', name='Task title', exact=True).fill('Keep this draft')
    page.get_by_role('checkbox', name='Mark Book a quiet afternoon complete', exact=True).check()
    expect(page.get_by_role('checkbox', name='Mark Book a quiet afternoon incomplete', exact=True)).to_be_checked()
    expect(page.get_by_role('textbox', name='Task title', exact=True)).to_have_value('Keep this draft')
    page.get_by_role('button', name='Select', exact=True).click()
    page.locator(f'.selection-checkbox[value="todo:{ids["child"]}"]').check()
    page.get_by_role('button', name='Subtask', exact=True).click()
    page.get_by_role('textbox', name='Subtask title', exact=True).fill('Added inside selection')
    page.get_by_role('button', name='Add subtask', exact=True).click()
    expect(page.locator('.task-row').filter(has_text='Added inside selection')).to_have_count(1)
    expect(page.locator('#selection-count')).to_have_text('1 selected')
    page.get_by_role('button', name='Task note', exact=True).click()
    page.get_by_role('textbox', name='Task note text', exact=True).fill('Written in the new frontend')
    page.get_by_role('button', name='Add task note', exact=True).click()
    expect(page.locator('.note-row').filter(has_text='Written in the new frontend')).to_be_visible()
    page.get_by_role('button', name='Tags', exact=True).click()
    page.locator('#edit-task-tags').fill('#desktop')
    page.get_by_role('button', name='Apply tags', exact=True).click()
    expect(page.locator(f'#todo-row-{ids["child"]} .tag')).to_have_text('#desktop')
    page.get_by_role('button', name='Defer', exact=True).click()
    page.get_by_label('Deferred until', exact=True).fill('2027-04-05')
    page.get_by_role('button', name='Apply deferral', exact=True).click()
    expect(page.locator(f'#todo-row-{ids["child"]}')).to_contain_text('Deferred')
    page.get_by_role('button', name='Defer', exact=True).click()
    page.get_by_role('button', name='Clear deferral', exact=True).click()
    expect(page.locator(f'#todo-row-{ids["child"]}')).not_to_contain_text('Deferred')
    assert page.evaluate('window.retainedListDocument')
    assert errors == []


@pytest.mark.parametrize('prototype_server', ['/apps/simple_todo'], indirect=True)
def test_browser_list_mobile_proxy_and_options(detail, prototype_server, page):
    origin, _ = prototype_server
    _, _, ids = detail
    page.set_viewport_size({'width': 390, 'height': 844})
    page.goto(origin + path(ids) + '?folder_id=0&sort=created')
    page.get_by_text('View & list options', exact=False).first.click()
    page.get_by_role('checkbox', name='Manual ordering', exact=True).check()
    expect(page.get_by_role('checkbox', name='Manual ordering', exact=True)).to_be_checked()
    page.keyboard.press('Escape')
    page.get_by_role('button', name='Select', exact=True).click()
    page.locator(f'.selection-checkbox[value="note:{ids["note"]}"]').check()
    expect(page.get_by_role('button', name='Pin', exact=True)).to_be_disabled()
    expect(page.get_by_role('button', name='Edit', exact=True)).to_be_enabled()
    page.get_by_role('button', name='Delete', exact=True).click()
    expect(page.get_by_role('dialog')).to_be_visible()
    page.get_by_role('button', name='Cancel', exact=True).click()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert page.locator('html').get_attribute('data-theme') == 'dark'


def test_browser_wiki_group_opens_link_and_updates_confirmation(detail, prototype_server, page, monkeypatch):
    import helpers
    # Integration is opt-in; exercise it with a fictional site configuration.
    monkeypatch.setattr(main, 'ENABLE_DOKUWIKI_LINKS', True)
    monkeypatch.setattr(helpers, 'ENABLE_DOKUWIKI_LINKS', True)
    monkeypatch.setattr(helpers, 'DOKUWIKI_LINK_FSTRING',
                        'https://wiki.example.test/dokuwiki/doku.php?id={year}:{month_short}#{wday_occurrence}')
    origin, _ = prototype_server
    page.context.route('**/dokuwiki/**', lambda route: route.fulfill(status=200, body='Wiki preview'))
    page.goto(origin + path(detail[2]))
    link = page.locator('a[data-wiki-group]').first
    assert link.count() == 1
    with page.expect_response(lambda response: '/wiki/confirm?' in response.url and response.request.method == 'POST') as changed:
        with page.expect_popup() as popup:
            link.click()
    assert changed.value.status == 200
    popup.value.close()
    expect(page.locator('a.wiki-confirmed').first).to_be_visible()


def test_browser_new_task_title_is_always_shown_and_plus_reveals_the_rest(prototype_server, page):
    origin, (_, _, ids) = prototype_server
    for width in (1200, 360):
        page.set_viewport_size({'width': width, 'height': 700})
        page.goto(origin + f'/app/list/{ids["list"]}')
        title = page.get_by_role('textbox', name='Task title')
        toggle = page.get_by_role('button', name='Tags and Add button for the new task')
        add = page.get_by_role('button', name='Add task')
        expect(page.locator('#panel-task').get_by_text('New task', exact=True)).to_be_visible()
        expect(title).to_be_visible()
        expect(add).to_be_hidden()
        expect(toggle).to_have_attribute('aria-expanded', 'false')
        # Enter adds the task without opening anything.
        title.fill(f'Quick {width}')
        title.press('Enter')
        expect(page.locator('.task-row').filter(has_text=f'Quick {width}')).to_have_count(1)
        expect(title).to_have_value('')
        expect(title).to_be_focused()
        # The full form stays open after adding, until it is closed again.
        toggle.click()
        expect(page.get_by_role('textbox', name='Task hashtags').first).to_be_focused()
        box, line = title.bounding_box(), page.locator('.task-capture-line').bounding_box()
        assert add.bounding_box()['y'] >= box['y'] + box['height'] and box['width'] > line['width'] * 0.55
        title.fill(f'Tagged {width}')
        page.get_by_role('textbox', name='Task hashtags').first.fill('#fresh')
        add.click()
        expect(page.locator('.task-row').filter(has_text=f'Tagged {width}')).to_contain_text('#fresh')
        expect(add).to_be_visible()
        toggle.click()
        expect(add).to_be_hidden()
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')

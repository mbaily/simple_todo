"""Editor validation, context preservation, links, and desktop HTMX workflows."""
from datetime import datetime
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect

import main
from models import List, Todo, Note, Link, JournalEntry
from test_daisyui_list import detail, prototype, prototype_server
from test_daisyui_test import HX
from test_reverse_proxy import PrefixStrippingProxy


def editor(kind, identifier, suffix=''):
    return f'/app/{kind}/{identifier}/edit' + suffix


def task_form(**changes):
    return {'title': 'Updated task', 'note': '', 'hashtags': '', 'alarm_mode': 'none', **changes}


def test_editor_documents_fragments_and_return_context(detail):
    db, client, ids = detail
    for kind in ('list', 'todo', 'note'):
        path = editor(kind, ids[kind]) + f'?view_list_id={ids["list"]}&sort=created&folder_id=0'
        full = client.get(path)
        fragment = client.get(path, headers=HX)
        restored = client.get(path, headers={**HX, 'HX-History-Restore-Request': 'true'})
        assert full.status_code == fragment.status_code == restored.status_code == 200
        assert '<!DOCTYPE html>' in full.text and '<!DOCTYPE html>' in restored.text
        assert '<!DOCTYPE html>' not in fragment.text
        assert 'data-theme="dark"' in full.text
        assert full.context['return_url'] == f'/app/list/{ids["list"]}?sort=created&folder_id=0'
        assert full.headers['cache-control'] == 'no-store'
    assert client.get(editor('todo', 999999)).status_code == 404
    assert client.get(editor('unknown', ids['todo'])).status_code == 404
    assert client.get(editor('todo', ids['external']) + f'?view_list_id={ids["list"]}').status_code == 400


def test_list_editor_preserves_other_settings_and_can_clear_metadata(detail):
    db, client, ids = detail
    item = db.get(List, ids['list'])
    item.category = 'Keep category'
    item.manual_sorting = True
    db.commit()
    url = editor('list', item.id) + '?back=home&sort=created&folder_id=0'
    response = client.post(url, headers=HX, data={'name': 'Renamed list', 'hashtags': '#desktop',
                                                'is_delayed': 'true', 'delayed_until': '2027-04-05'})
    assert response.status_code == 200 and response.headers['HX-Push-Url'] == '/app?sort=created&folder_id=0'
    db.expire_all()
    assert item.name == 'Renamed list' and item.category == 'Keep category' and item.manual_sorting
    assert item.delayed_until.date().isoformat() == '2027-04-05'
    assert {tag.name for tag in item.hashtags} == {'desktop', 'deferred'}
    response = client.post(url, data={'name': 'Renamed list', 'hashtags': ''}, follow_redirects=False)
    assert response.status_code == 303 and response.headers['location'] == '/app?sort=created&folder_id=0'
    db.expire_all()
    assert item.delayed_until is None and not item.hashtags
    assert db.query(JournalEntry).filter_by(action='Updated List', entity_id=item.id).count() == 2


def test_task_editor_updates_and_explicitly_clears_all_supported_fields(detail):
    db, client, ids = detail
    path = editor('todo', ids['todo']) + f'?view_list_id={ids["list"]}&folder_id=0'
    payload = dict(title='A fully edited task', note='A useful note\nwith a second line', hashtags='#desktop',
                   use_color='true', color='#72c8a0', is_delayed='true', delayed_until='2027-04-05',
                   alarm_mode='range', alarm_start_range='2027-04-05T09:00', alarm_end_range='2027-04-05T10:00')
    response = client.post(path, headers=HX, data=payload)
    assert response.status_code == 200
    assert response.headers['HX-Push-Url'] == f'/app/list/{ids["list"]}?sort=user&folder_id=0'
    db.expire_all()
    task = db.get(Todo, ids['todo'])
    assert task.title == payload['title'] and task.note == payload['note'] and task.color == payload['color']
    assert task.alarm_start == datetime(2027, 4, 5, 9) and task.alarm_end == datetime(2027, 4, 5, 10)
    assert task.delayed_until == datetime(2027, 4, 5)
    full = client.get(path)
    assert full.context['values']['alarm_mode'] == 'range'
    assert full.context['values']['alarm_start_range'] == '2027-04-05T09:00'
    response = client.post(path, headers=HX, data={'title': task.title, 'note': '', 'hashtags': '', 'alarm_mode': 'none'})
    assert response.status_code == 200
    db.expire_all()
    assert task.note == '' and task.color is None and task.delayed_until is None
    assert task.alarm_start is None and task.alarm_end is None and not task.hashtags


def test_renaming_a_task_preserves_existing_metadata_and_structure(detail):
    db, client, ids = detail
    task = db.get(Todo, ids['child'])
    task.note = 'Keep this note'
    task.color = 'red'
    task.delayed_until = datetime(2027, 4, 5)
    task.alarm_start, task.alarm_end = datetime(2027, 4, 5, 9), datetime(2027, 4, 5, 10)
    db.commit()
    url = editor('todo', task.id)
    values = client.get(url).context['values']
    payload = {key: 'true' if value is True else value for key, value in values.items() if value is not False}
    response = client.post(url, headers=HX, data={**payload, 'title': 'Rename only'})
    assert response.status_code == 200
    db.expire_all()
    assert task.title == 'Rename only' and task.note == 'Keep this note' and task.color == 'red'
    assert task.delayed_until == datetime(2027, 4, 5)
    assert task.alarm_start == datetime(2027, 4, 5, 9) and task.alarm_end == datetime(2027, 4, 5, 10)
    assert task.parent_id == ids['todo'] and task.list_id == ids['list']


def test_tolerance_alarm_is_supported_and_saves_as_a_range(detail):
    db, client, ids = detail
    response = client.post(editor('todo', ids['todo']), headers=HX,
                           data=task_form(alarm_mode='tolerance', alarm_target_time='2027-04-05T09:00', alarm_minutes='15'))
    assert response.status_code == 200
    db.expire_all()
    task = db.get(Todo, ids['todo'])
    assert task.alarm_start == datetime(2027, 4, 5, 8, 45)
    assert task.alarm_end == datetime(2027, 4, 5, 9, 15)


def test_editing_collected_task_returns_to_collection_without_reparenting(detail):
    db, client, ids = detail
    path = editor('todo', ids['external']) + f'?view_list_id={ids["collection"]}&sort=created'
    response = client.post(path, headers=HX, data=task_form())
    assert response.status_code == 200
    assert response.context['list'].id == ids['collection']
    assert response.headers['HX-Push-Url'] == f'/app/list/{ids["collection"]}?sort=created'
    db.expire_all()
    assert db.get(Todo, ids['external']).list_id == ids['source']
    assert db.get(Todo, ids['external']) in db.get(List, ids['collection']).collated_todos


def test_note_editor_retains_attachment_and_handles_plain_text_urls(detail):
    db, client, ids = detail
    url = editor('note', ids['child_note']) + f'?view_list_id={ids["list"]}'
    response = client.post(url, headers=HX, data={'name': 'Updated note', 'text': 'Read https://example.test/help'})
    assert response.status_code == 200
    db.expire_all()
    note = db.get(Note, ids['child_note'])
    assert note.todo_id == ids['child'] and note.list_id == ids['list']
    assert note.name == 'Updated note' and '<a href=' in note.text
    assert client.get(url).context['values']['text'] == 'Read https://example.test/help'
    assert client.post(url, headers=HX, data={'name': '', 'text': 'Read https://example.test/help'}).status_code == 200
    db.expire_all()
    assert note.name is None and note.text.count('<a href=') == 1


def test_note_urls_render_as_links_without_exposing_stored_html(detail):
    from daisyui_test_edit import render_note_text
    db, client, ids = detail
    response = client.post(editor('note', ids['note']), headers=HX,
                           data={'name': '', 'text': 'Read https://example.test/help and <script>alert(1)</script>'})
    assert response.status_code == 200
    assert '<a href="https://example.test/help" target="_blank" rel="noopener noreferrer">https://example.test/help</a>' in response.text
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in response.text
    assert '<script>alert(1)</script>' not in response.text
    class Links(HTMLParser):
        def handle_starttag(self, tag, attrs):
            assert tag == 'a'
            assert dict(attrs) == {'href': 'https://example.test/"onclick=bad',
                                   'target': '_blank', 'rel': 'noopener noreferrer'}

    Links().feed(str(render_note_text('https://example.test/"onclick=bad')))


@pytest.mark.parametrize('changes', [
    {'title': ' '}, {'alarm_mode': 'invalid'},
    {'is_delayed': 'true', 'delayed_until': ''},
    {'use_color': 'true', 'color': 'not-a-colour'},
    {'alarm_mode': 'range', 'alarm_start_range': '2027-04-05T11:00', 'alarm_end_range': '2027-04-05T10:00'},
    {'alarm_mode': 'tolerance', 'alarm_target_time': '2027-04-05T10:00', 'alarm_minutes': '-1'},
])
def test_invalid_task_edits_do_not_partially_save(detail, changes):
    db, client, ids = detail
    item = db.get(Todo, ids['todo'])
    before = (item.title, item.note, item.color, item.modified_at, item.alarm_start, item.delayed_until)
    payload = {'title': 'Must not save', 'note': 'Must not save this either', 'hashtags': '#bad', 'alarm_mode': 'none'}
    response = client.post(editor('todo', item.id), headers=HX, data={**payload, **changes})
    assert response.status_code == 400 and response.headers['HX-Retarget'] == '#page-feedback'
    db.expire_all()
    assert (item.title, item.note, item.color, item.modified_at, item.alarm_start, item.delayed_until) == before
    assert not db.query(JournalEntry).filter_by(action='Updated Todo', entity_id=item.id).count()


def test_incomplete_forms_are_rejected_and_native_errors_preserve_input(detail):
    db, client, ids = detail
    response = client.post(editor('todo', ids['todo']), headers=HX, data={'title': 'Incomplete'})
    assert response.status_code == 400
    db.expire_all()
    assert db.get(Todo, ids['todo']).title == 'Book a quiet afternoon'
    response = client.post(editor('note', ids['note']), data={'name': 'Keep my entered name', 'text': '   '})
    assert response.status_code == 400
    assert response.context['values']['name'] == 'Keep my entered name'
    assert response.context['values']['text'] == '   '


@pytest.mark.parametrize('kind', ['list', 'todo', 'note'])
def test_link_management_is_scoped_and_does_not_modify_content(detail, kind):
    db, client, ids = detail
    path = editor(kind, ids[kind])
    original = client.get(path).context['values']
    response = client.post(path + '/links', headers=HX, data={'url': 'https://example.test/resource', 'title': 'Reference'})
    assert response.status_code == 200 and response.context['values'] == original
    link = db.query(Link).filter_by(url='https://example.test/resource').one()
    link_id = link.id
    assert getattr(link, kind + '_id') == ids[kind]
    assert sum(getattr(link, field) is not None for field in ('list_id', 'todo_id', 'note_id')) == 1
    wrong_kind = 'list' if kind != 'list' else 'todo'
    wrong = editor(wrong_kind, ids[wrong_kind], f'/links/{link.id}/delete')
    assert client.post(wrong, headers=HX).status_code == 404
    assert client.post(path + '/links', headers=HX, data={'url': 'javascript:alert(1)'}).status_code == 400
    assert client.post(path + f'/links/{link.id}/delete', headers=HX).status_code == 200
    db.expire_all()
    assert db.get(Link, link_id) is None
    response = client.post(path + '/links', data={'url': 'https://example.test/another'}, follow_redirects=False)
    assert response.status_code == 303 and response.headers['location'].startswith(path + '?')


@pytest.mark.parametrize('kind', ['list', 'todo', 'note'])
def test_editor_delete_returns_to_a_valid_new_frontend_page(detail, kind):
    db, client, ids = detail
    path = editor(kind, ids[kind], '/delete') + f'?view_list_id={ids["list"]}'
    response = client.post(path, headers=HX)
    assert response.status_code == 200
    assert response.headers['HX-Push-Url'] == ('/app?sort=user' if kind == 'list' else f'/app/list/{ids["list"]}?sort=user')
    db.expire_all()
    assert db.get({'list': List, 'todo': Todo, 'note': Note}[kind], ids[kind]) is None
    if kind in ('list', 'todo'):
        assert db.get(Todo, ids['child']) is None and db.get(Todo, ids['grandchild']) is None
        assert db.get(Note, ids['child_note']) is None


@pytest.mark.parametrize('prefix', ['', '/simple_todo', '/apps/simple_todo'])
def test_editor_urls_forms_and_return_navigation_respect_proxy_prefix(detail, monkeypatch, prefix):
    db, _, ids = detail
    monkeypatch.setattr(main.app, 'root_path', prefix)
    client = TestClient(PrefixStrippingProxy(main.app, prefix))

    class URLs(HTMLParser):
        def handle_starttag(self, tag, attrs):
            for key, value in attrs:
                if key in {'href', 'src', 'action', 'hx-post'} and value and value.startswith('/'):
                    assert not prefix or value.startswith(prefix + '/'), (key, value)

    for kind in ('list', 'todo', 'note'):
        response = client.get(prefix + editor(kind, ids[kind]))
        assert response.status_code == 200
        URLs().feed(response.text)
    response = client.post(prefix + editor('todo', ids['todo']) + '?back=home&folder_id=0', headers=HX, data=task_form())
    assert response.status_code == 200
    assert response.headers['HX-Push-Url'] == prefix + '/app?sort=user&folder_id=0'


def test_browser_task_editor_preserves_unsaved_fields_when_managing_links(detail, prototype_server, page):
    origin, _ = prototype_server
    _, _, ids = detail
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(origin + '/app')
    page.get_by_role('button', name='Select', exact=True).click()
    page.locator(f'.selection-checkbox[value="todo:{ids["todo"]}"]').first.check()
    page.get_by_role('button', name='Edit', exact=True).click()
    page.wait_for_url(f'**/app/todo/{ids["todo"]}/edit?*')
    page.evaluate('window.editorDocument = true')
    page.get_by_role('textbox', name='Task title', exact=True).fill('Saved through the new editor')
    page.get_by_role('textbox', name='Note', exact=True).fill('My unsaved note')
    page.get_by_role('checkbox', name='Custom colour', exact=True).check()
    page.get_by_role('textbox', name='Task colour', exact=True).fill('#55cc99')
    page.locator('.editor-links > summary').click()
    page.get_by_role('textbox', name='Link URL', exact=True).fill('https://example.test/desktop')
    page.get_by_role('textbox', name='Link title', exact=True).fill('Desktop reference')
    page.get_by_role('button', name='Add link', exact=True).click()
    expect(page.get_by_role('link', name='Desktop reference', exact=True)).to_be_visible()
    expect(page.get_by_role('textbox', name='Task title', exact=True)).to_have_value('Saved through the new editor')
    expect(page.get_by_role('textbox', name='Note', exact=True)).to_have_value('My unsaved note')
    expect(page.get_by_role('textbox', name='Task colour', exact=True)).to_have_value('#55cc99')
    expect(page.locator('[data-editor-colour-picker]')).to_have_value('#55cc99')
    page.get_by_role('button', name='Remove Desktop reference', exact=True).click()
    expect(page.get_by_role('link', name='Desktop reference', exact=True)).to_have_count(0)
    expect(page.get_by_role('textbox', name='Task title', exact=True)).to_have_value('Saved through the new editor')
    page.get_by_role('button', name='Save changes', exact=True).click()
    page.wait_for_url('**/app?sort=user')
    expect(page.locator('.item-title').filter(has_text='Saved through the new editor')).to_be_visible()
    assert page.evaluate('window.editorDocument')
    assert errors == []


def test_browser_validation_preserves_drafts_then_returns_to_list(detail, prototype_server, page):
    origin, _ = prototype_server
    _, _, ids = detail
    page.goto(origin + editor('todo', ids['todo']) + f'?view_list_id={ids["list"]}')
    page.get_by_role('textbox', name='Task title', exact=True).fill('Preserve this title')
    page.locator('#editor-alarm-mode').select_option('range')
    page.locator('[name=alarm_start_range]').fill('2027-04-05T11:00')
    page.locator('[name=alarm_end_range]').fill('2027-04-05T10:00')
    page.get_by_role('button', name='Save changes', exact=True).click()
    expect(page.get_by_role('alert')).to_contain_text('valid alarm range')
    expect(page.get_by_role('textbox', name='Task title', exact=True)).to_have_value('Preserve this title')
    page.locator('#editor-alarm-mode').select_option('none')
    expect(page.locator('[name=alarm_start_range]')).to_be_disabled()
    page.get_by_role('button', name='Save changes', exact=True).click()
    page.wait_for_url(f'**/app/list/{ids["list"]}?sort=user')
    expect(page.locator('.task-row').filter(has_text='Preserve this title')).to_be_visible()


@pytest.mark.parametrize('prototype_server', ['/apps/simple_todo'], indirect=True)
def test_browser_note_delete_can_cancel_and_confirm_with_invalid_unsaved_form(detail, prototype_server, page):
    origin, _ = prototype_server
    _, _, ids = detail
    page.set_viewport_size({'width': 390, 'height': 844})
    page.goto(origin + editor('note', ids['note']) + f'?view_list_id={ids["list"]}')
    page.get_by_role('textbox', name='Note text', exact=True).fill('')
    page.get_by_role('button', name='Delete note', exact=True).click()
    expect(page.get_by_role('dialog')).to_be_visible()
    page.get_by_role('button', name='Cancel', exact=True).last.click()
    expect(page.get_by_role('textbox', name='Note text', exact=True)).to_have_value('')
    page.get_by_role('button', name='Delete note', exact=True).click()
    page.get_by_role('button', name='Delete items', exact=True).click()
    page.wait_for_url(f'**/app/list/{ids["list"]}?sort=user')
    assert page.locator(f'[data-item-key="note:{ids["note"]}"]').count() == 0
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')

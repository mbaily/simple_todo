"""Reorder lists page in the daisyUI frontend."""
from playwright.sync_api import expect

from models import Folder, JournalEntry, List
from test_daisyui_test import HX, prototype, prototype_server


def setup_lists(db, ids):
    for index, name in enumerate(['Alpha', 'Bravo', 'Charlie']):
        db.add(List(name=name, folder_id=ids['folder'], sort_order=index))
    db.commit()


def order(client, folder_id):
    return [item.name for item in client.get(f'/app/lists/reorder?folder_id={folder_id}').context['lists']]


def test_reorder_scope_moves_and_overview_order(prototype):
    db, client, ids = prototype
    setup_lists(db, ids)
    page = client.get(f'/app/lists/reorder?folder_id={ids["folder"]}')
    original = client.get(f'/lists/reorder?folder_id={ids["folder"]}')
    assert page.status_code == 200 and [l.id for l in page.context['lists']] == [l.id for l in original.context['lists']]
    assert order(client, 0) == ['A place to start', 'Weekend projects']
    url = f'/app/lists/reorder/move?folder_id={ids["folder"]}&view=reorder'
    charlie = db.query(List).filter_by(name='Charlie').one()
    response = client.post(url, headers=HX, data={'list_id': charlie.id, 'to': 'top'})
    assert response.status_code == 200 and 'Moved Charlie to the top.' in response.text
    assert order(client, ids['folder']) == ['Charlie', 'Alpha', 'Bravo']
    client.post(url, headers=HX, data={'list_id': charlie.id, 'to': 'down'})
    assert order(client, ids['folder']) == ['Alpha', 'Charlie', 'Bravo']
    client.post(url, headers=HX, data={'list_id': charlie.id, 'to': 'bottom'})
    client.post(url, headers=HX, data={'list_id': charlie.id, 'to': 'down'})  # already last: no change
    assert order(client, ids['folder']) == ['Alpha', 'Bravo', 'Charlie']
    assert db.query(JournalEntry).filter_by(action='Reordered Lists').count() == 3
    overview = client.get(f'/app?sort=user&folder_id={ids["folder"]}')
    assert [l.name for l in overview.context['lists']] == ['Alpha', 'Bravo', 'Charlie']
    outside = db.query(List).filter_by(name='Weekend projects').one()
    assert client.post(url, headers=HX, data={'list_id': outside.id, 'to': 'up'}).status_code == 404
    assert client.post(url, headers=HX, data={'list_id': charlie.id, 'to': 'sideways'}).status_code == 400
    assert client.get('/app/lists/reorder?folder_id=999').status_code == 404
    redirect = client.post(url, data={'list_id': charlie.id, 'to': 'up'}, follow_redirects=False)
    assert redirect.headers['location'] == f'/app/lists/reorder?folder_id={ids["folder"]}'


def test_browser_reorder_keeps_keyboard_focus(prototype_server, page):
    origin, (db, _, ids) = prototype_server
    setup_lists(db, ids)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(origin + '/app/folders')
    page.get_by_role('link', name='Reorder lists').click()
    page.get_by_role('combobox', name='Folder to reorder').select_option(label='Projects')
    expect(page.locator('.reorder-row').first).to_contain_text('Alpha')
    page.get_by_role('button', name='Move Charlie up').click()
    expect(page.locator('#page-feedback')).to_contain_text('Moved Charlie up.')
    expect(page.get_by_role('button', name='Move Charlie up')).to_be_focused()
    page.keyboard.press('Enter')
    expect(page.locator('.reorder-row').first).to_contain_text('Charlie')
    expect(page.get_by_role('button', name='Move Charlie down')).to_be_focused()
    assert not errors

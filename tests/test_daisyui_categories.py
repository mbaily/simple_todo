"""Open tasks and categories pages in the daisyUI frontend."""
from playwright.sync_api import expect

from models import Category, JournalEntry, List, Todo
from test_daisyui_test import HX, prototype, prototype_server


def test_open_tasks_match_original_and_completion_returns_here(prototype):
    db, client, ids = prototype
    db.add(Todo(title='Already done', list_id=ids['list'], completed=True))
    archived = List(name='Old archive', archived=True)
    db.add(archived)
    db.flush()
    db.add(Todo(title='Hidden in archive', list_id=archived.id))
    db.commit()
    page = client.get('/app/uncompleted')
    original = client.get('/uncompleted')
    assert page.status_code == original.status_code == 200
    ours = {todo.id for group in page.context['groups'] for todo in group['todos']}
    theirs = {todo.id for group in original.context['todo_tree']['lists'] for todo in group['todos']}
    for node in original.context['todo_tree']['folder_nodes']:
        theirs |= {todo.id for group in node['lists'] for todo in group['todos']}
    assert ours == theirs == {ids['todo'], ids['old']}
    assert 'Already done' not in page.text and 'Hidden in archive' not in page.text
    response = client.post(f'/app/complete/todo/{ids["todo"]}?sort=user&view=uncompleted', headers=HX, data={'completed': 'true'})
    assert response.status_code == 200 and 'Open tasks' in response.text and 'Book a quiet afternoon' not in response.text


def test_categories_create_rename_delete_and_list_only_names(prototype):
    db, client, ids = prototype
    db.get(List, ids['list']).category = 'Loose'  # used by a list, not saved in the Category table
    db.commit()
    page = client.get('/app/categories')
    rows = {row['name']: row for row in page.context['categories']}
    assert rows['Personal']['list_count'] == 1 and rows['Loose'] == dict(name='Loose', list_count=1, saved=False)
    url = '/app/categories/{}?view=categories'
    response = client.post(url.format('create'), headers=HX, data={'name': ' Errands '})
    assert response.status_code == 200 and 'Created category Errands.' in response.text
    assert client.post(url.format('create'), headers=HX, data={'name': 'Errands'}).status_code == 409
    assert client.post(url.format('create'), headers=HX, data={'name': ' '}).status_code == 400
    assert client.post(url.format('rename'), headers=HX, data={'old_name': 'Loose', 'new_name': 'Errands'}).status_code == 409
    renamed = client.post(url.format('rename'), headers=HX, data={'old_name': 'Loose', 'new_name': 'Home'})
    assert 'Renamed Loose to Home.' in renamed.text
    db.expire_all()
    assert db.get(List, ids['list']).category == 'Home'
    assert client.post(url.format('rename'), headers=HX, data={'old_name': 'Nope', 'new_name': 'X'}).status_code == 404
    deleted = client.post(url.format('delete'), headers=HX, data={'name': 'Personal'})
    assert 'Deleted category Personal. Its lists are kept without a category.' in deleted.text
    db.expire_all()
    assert db.get(List, ids['other']).category is None
    assert db.query(Category).filter_by(name='Errands').count() == 1
    assert db.query(JournalEntry).filter_by(action='Category Renamed').count() == 1


def test_browser_more_menu_open_tasks_and_categories(prototype_server, page):
    origin, (db, _, ids) = prototype_server
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(origin + '/app')
    page.locator('summary[aria-label="More navigation"]').click()
    page.get_by_role('link', name='Open tasks').click()
    expect(page.get_by_role('heading', name='Open tasks')).to_be_visible()
    page.get_by_role('checkbox', name='Mark Book a quiet afternoon complete', exact=True).check()
    expect(page.locator('#page-feedback')).to_contain_text('Completion updated.')
    expect(page.get_by_text('Book a quiet afternoon')).to_be_hidden()
    page.locator('summary[aria-label="More navigation"]').click()
    page.get_by_role('link', name='Categories').click()
    page.get_by_role('textbox', name='Category name').fill('Errands')
    page.get_by_role('button', name='Add category').click()
    expect(page.locator('#page-feedback')).to_contain_text('Created category Errands.')
    expect(page.get_by_role('textbox', name='Category name')).to_have_value('')
    assert not errors

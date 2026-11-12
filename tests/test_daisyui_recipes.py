"""Recipe pages in the daisyUI frontend: shopping list, ingredients, pantry and history."""
from datetime import datetime, timedelta

from playwright.sync_api import expect

from models import IngredientEvent, IngredientGroup, JournalEntry, List, Todo
from test_daisyui_test import HX, prototype, prototype_server


def recipes(db, ids):
    soup = db.get(List, ids['list'])
    soup.is_recipe = True
    soup.name = 'Soup'
    curry = List(name='Curry', is_recipe=True)
    db.add(curry)
    db.flush()
    now = datetime.utcnow()
    onion = Todo(title='Onion', list_id=soup.id)
    stock = Todo(title='Stock', list_id=soup.id)
    salt = Todo(title='Salt', list_id=soup.id)
    curry_onion = Todo(title='onions', list_id=curry.id)
    db.add_all([onion, stock, salt, curry_onion])
    db.flush()
    db.add_all([IngredientEvent(todo_id=onion.id, event_type='bought', event_date=now - timedelta(days=20)),
                IngredientEvent(todo_id=onion.id, event_type='ran_out', event_date=now - timedelta(days=2)),
                IngredientEvent(todo_id=salt.id, event_type='bought', event_date=now - timedelta(days=1))])
    db.commit()
    return {**ids, 'soup': soup.id, 'curry': curry.id, 'onion': onion.id, 'stock': stock.id, 'salt': salt.id, 'curry_onion': curry_onion.id}


def do(client, operation, back, **data):
    return client.post(f'/app/recipes/do/{operation}', headers=HX, data={'back': back, **data})


def test_pages_match_original_data(prototype):
    db, client, ids = prototype
    ids = recipes(db, ids)
    shopping = client.get(f'/app/list/{ids["soup"]}/shopping')
    original = client.get(f'/list/{ids["soup"]}/shopping_list')
    assert shopping.status_code == original.status_code == 200
    ours = {row['todo'].id: row['status'] for group in shopping.context['groups'].values() for row in group}
    theirs = {row['todo'].id: row['status'] for row in original.context['shopping_items']}
    assert ours == theirs and ours[ids['onion']] == 'ran_out' and ours[ids['stock']] == 'new' and ours[ids['salt']] == 'in_stock'
    assert 'Ran out' in shopping.text and 'bought 20 days ago' in shopping.text and 'usually lasts 18 days' in shopping.text
    report = client.get(f'/app/list/{ids["soup"]}/ingredients')
    assert [r['todo'].id for r in report.context['ingredients']] == [r['todo'].id for r in client.get(f'/list/{ids["soup"]}/ingredient_report').context['ingredients']]
    pantry = client.get('/app/pantry')
    assert pantry.status_code == 200 and [len(s['todos']) for s in pantry.context['suggestions']] == [2]
    history = client.get(f'/app/ingredient/{ids["onion"]}/history')
    assert history.status_code == 200 and len(history.context['events']) == 2
    assert client.get(f'/app/list/{ids["other"]}/shopping').status_code == 400
    assert client.get('/app/list/999/shopping').status_code == 404


def test_events_bulk_reset_and_history(prototype):
    db, client, ids = prototype
    ids = recipes(db, ids)
    back = f'shopping:{ids["soup"]}'
    response = do(client, 'event', back, todo_id=ids['onion'], event_type='bought')
    assert response.status_code == 200 and 'Marked Onion as bought.' in response.text
    assert db.query(JournalEntry).filter_by(action='Ingredient Bought', entity_id=ids['onion']).count() == 1
    assert do(client, 'event', back, todo_id=ids['onion'], event_type='eaten').status_code == 400
    assert do(client, 'event', back, todo_id=ids['old'], event_type='bought').status_code == 404  # not in a recipe list
    bulk = do(client, 'bulk-bought', back, list_id=ids['soup'], todo_ids=[ids['stock'], ids['salt']])
    assert 'Marked 2 items as bought.' in bulk.text
    reset = do(client, 'reset', f'ingredients:{ids["soup"]}', list_id=ids['soup'])
    assert reset.status_code == 200 and 'Marked every open ingredient as just bought.' in reset.text
    history_back = f'history:{ids["salt"]}'
    dated = do(client, 'event', history_back, todo_id=ids['salt'], event_type='ran_out', event_date='2026-01-05', note='Party')
    assert dated.status_code == 200 and 'Party' in dated.text
    event = db.query(IngredientEvent).filter_by(todo_id=ids['salt'], note='Party').one()
    assert event.event_date == datetime(2026, 1, 5, 12)
    assert do(client, 'event', history_back, todo_id=ids['salt'], event_type='bought', event_date='2026-02-30').status_code == 400
    assert do(client, 'event-delete', history_back, todo_id=ids['onion'], event_id=event.id).status_code == 404
    assert 'Event deleted.' in do(client, 'event-delete', history_back, todo_id=ids['salt'], event_id=event.id).text
    assert do(client, 'event', 'elsewhere', todo_id=ids['salt'], event_type='bought').status_code == 400
    redirect = client.post('/app/recipes/do/event', data={'back': back, 'todo_id': ids['salt'], 'event_type': 'bought'},
                           follow_redirects=False)
    assert redirect.headers['location'] == f'/app/list/{ids["soup"]}/shopping'


def test_pantry_groups(prototype):
    db, client, ids = prototype
    ids = recipes(db, ids)
    created = do(client, 'group-create', 'pantry', canonical_name='Onions', todo_ids=[ids['onion'], ids['curry_onion']])
    assert created.status_code == 200 and 'Grouped 2 ingredients as Onions.' in created.text
    onions = db.query(IngredientGroup).one()
    assert do(client, 'group-create', 'pantry', canonical_name=' ', todo_ids=[ids['stock']]).status_code == 400
    assert 'Renamed the group to Brown onions.' in do(client, 'group-rename', 'pantry', group_id=onions.id, canonical_name='Brown onions').text
    assert 'Marked Brown onions as bought.' in do(client, 'group-bought', 'pantry', group_id=onions.id).text
    # The original records one event per group (on its first member); group statistics combine members.
    assert db.query(JournalEntry).filter_by(action='Bought Ingredient (Grouped)', entity_id=onions.id).count() == 1
    # A grouped ingredient's buttons on the shopping list act on the whole group.
    do(client, 'event', f'shopping:{ids["soup"]}', todo_id=ids['onion'], event_type='ran_out', scope='group')
    assert db.query(JournalEntry).filter_by(action='Ran Out (Grouped)', entity_id=onions.id).count() == 1
    assert 'Added Stock to Brown onions.' in do(client, 'group-link', 'pantry', todo_id=ids['stock'], group_id=onions.id).text
    assert 'Removed Stock from its group.' in do(client, 'group-unlink', 'pantry', todo_id=ids['stock']).text
    assert do(client, 'group-bought', 'pantry', group_id=999).status_code == 404
    assert do(client, 'teleport', 'pantry').status_code == 404


def test_browser_shopping_and_pantry(prototype_server, page):
    origin, (db, client, ids) = prototype_server
    ids = recipes(db, ids)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(origin + f'/app/list/{ids["soup"]}')
    page.get_by_role('link', name='Shopping list').first.click()
    expect(page.get_by_role('heading', name='Shopping list')).to_be_visible()
    page.get_by_role('button', name='Bought: Onion').click()
    expect(page.locator('#page-feedback')).to_contain_text('Marked Onion as bought.')
    expect(page.locator('.recipe-in_stock')).to_contain_text('Onion')
    page.get_by_role('link', name='Pantry').click()
    page.get_by_role('button', name='Group 2').click()
    expect(page.locator('#page-feedback')).to_contain_text('Grouped 2 ingredients as Onion.')
    expect(page.get_by_role('heading', name='Shared ingredients 1')).to_be_visible()
    assert not errors

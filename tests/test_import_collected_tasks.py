"""Collected task links must restore completely or roll back the whole import."""
import copy
import json
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from import_logic import import_data_from_json
from models import List, Todo, collation_todos
from test_billing_accounts import account


@pytest.fixture
def collected(account):
    db, bill, client = account
    source = List(name='Source list')
    target = List(name='Collected tasks', is_collation=True)
    task = Todo(title='Collected task', list=source)
    db.add_all([source, target, task])
    db.flush()
    db.execute(collation_todos.insert().values(collation_list_id=target.id, todo_id=task.id, sort_order=3))
    db.commit()
    client.get('/customization')
    return db, client, target, task


@pytest.mark.parametrize('invalid', ['missing_both', 'missing_list', 'missing_task',
                                   'unknown_list', 'unknown_task', 'unknown_both'])
def test_invalid_collected_task_link_rolls_back_records_and_earlier_link_updates(collected, invalid):
    db, client, target, task = collected
    link = dict(collation_list_id=target.id, todo_id=task.id)
    if invalid == 'missing_both':
        link = {}
    elif invalid == 'missing_list':
        link.pop('collation_list_id')
    elif invalid == 'missing_task':
        link.pop('todo_id')
    else:
        if invalid in ('unknown_list', 'unknown_both'):
            link['collation_list_id'] = 99999
        if invalid in ('unknown_task', 'unknown_both'):
            link['todo_id'] = 99999
    before = client.get('/api/export').json()['data']
    payload = {'data': {'folders': [dict(id=99, name='Must not commit')],
                        'lists': [dict(id=target.id, name='Must not rename')],
                        'collation_todos': [dict(collation_list_id=target.id, todo_id=task.id, sort_order=9), link]}}
    with pytest.raises(ValueError, match='collation_todos.*No changes were saved'):
        import_data_from_json(db, copy.deepcopy(payload))
    assert client.get('/api/export').json()['data'] == before
    response = client.post('/api/import', files={'file': ('invalid-link.json', json.dumps(payload), 'application/json')},
                           follow_redirects=False)
    query = parse_qs(urlparse(response.headers['location']).query)
    assert response.status_code == 303 and 'import_success' not in query
    assert 'collation_todos' in query['import_error'][0] and 'No changes were saved' in query['import_error'][0]
    assert client.get('/api/export').json()['data'] == before


def test_valid_collected_task_links_allow_same_upload_references_and_sort_corrections(collected):
    db, client, target, task = collected
    payload = {'data': {'lists': [dict(id=99, name='New collection', is_collation=True)],
                        'todos': [dict(id=99, title='New task', list_id=task.list_id)],
                        'collation_todos': [dict(collation_list_id=target.id, todo_id=task.id, sort_order=8),
                                            dict(collation_list_id=99, todo_id=99, sort_order=2)]}}
    assert import_data_from_json(db, payload) == dict(created=3, updated=1, errors=0)
    links = [dict(row._mapping) for row in db.execute(select(collation_todos)).all()]
    assert links == [dict(collation_list_id=target.id, todo_id=task.id, sort_order=8),
                     dict(collation_list_id=99, todo_id=99, sort_order=2)]
    exported = client.get('/api/export').json()
    for _ in range(2):
        assert import_data_from_json(db, copy.deepcopy(exported))['errors'] == 0
        assert [dict(row._mapping) for row in db.execute(select(collation_todos)).all()] == links

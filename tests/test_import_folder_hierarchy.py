"""Folder imports keep a complete, acyclic tree and roll back invalid uploads."""
import copy
import json
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from import_logic import import_data_from_json
from models import Base, Folder, List
from test_billing_accounts import account


@pytest.fixture
def tree(account):
    db, bill, client = account
    root = Folder(name='Root')
    other = Folder(name='Other root')
    db.add_all([root, other])
    db.flush()
    child = Folder(name='Child', parent_id=root.id)
    db.add(child)
    db.flush()
    grandchild = Folder(name='Grandchild', parent_id=child.id)
    item = List(name='List in child folder', folder=child)
    db.add_all([grandchild, item])
    db.commit()
    assert client.get('/folders').status_code == 200
    return db, client, root, child, grandchild, other, item


@pytest.mark.parametrize('cycle', ['self', 'ancestor', 'multiple_updates', 'new_self', 'new_chain', 'generated_id'])
def test_circular_folder_import_rolls_back_all_fields_and_keeps_lists_visible(tree, cycle):
    db, client, root, child, grandchild, other, item = tree
    folders = [dict(id=other.id, name='Must not rename'), dict(id=99, name='Must not create')]
    if cycle == 'self':
        folders.append(dict(id=root.id, parent_id=root.id))
    elif cycle == 'ancestor':
        folders.append(dict(id=root.id, parent_id=grandchild.id))
    elif cycle == 'multiple_updates':
        folders.extend([dict(id=other.id, parent_id=grandchild.id), dict(id=root.id, parent_id=other.id)])
    elif cycle == 'new_self':
        folders[1]['parent_id'] = 99
    elif cycle == 'new_chain':
        folders.extend([dict(id=100, name='New second folder'), dict(id=101, name='New third folder'),
                        dict(id=99, parent_id=100), dict(id=100, parent_id=101), dict(id=101, parent_id=99)])
    else:
        folders.extend([dict(name='Generated folder', parent_id=root.id), dict(id=root.id, parent_id=100)])
    payload = {'data': {'folders': folders, 'bills': [dict(id=1, name='Must not rename bill')]}}
    before = client.get('/api/export').json()['data']
    with pytest.raises(ValueError, match='folders #.*inside itself.*No changes were saved'):
        import_data_from_json(db, copy.deepcopy(payload))
    assert client.get('/api/export').json()['data'] == before
    response = client.post('/api/import', files={'file': ('invalid-tree.json', json.dumps(payload), 'application/json')},
                           follow_redirects=False)
    query = parse_qs(urlparse(response.headers['location']).query)
    assert response.status_code == 303 and 'import_success' not in query
    assert 'folders #' in query['import_error'][0] and 'No changes were saved' in query['import_error'][0]
    assert client.get('/api/export').json()['data'] == before
    db.expire_all()
    page = client.get('/folders')
    assert page.status_code == 200 and f'href="/list/{item.id}?folder_id={child.id}"' in page.text


def test_omitted_parent_preserves_nesting_and_explicit_null_moves_folder_to_root(tree):
    db, client, root, child, grandchild, other, item = tree
    assert import_data_from_json(db, {'data': {'folders': [dict(id=child.id, name='Renamed child')]}})['updated'] == 1
    assert child.parent_id == root.id
    assert import_data_from_json(db, {'data': {'folders': [dict(id=child.id, parent_id=None)]}})['updated'] == 1
    assert child.parent_id is None and grandchild.parent_id == child.id


def test_valid_parent_can_follow_child_in_same_upload_and_export_restores_into_empty_database(tree):
    db, client, root, child, grandchild, other, item = tree
    payload = {'data': {'folders': [dict(id=child.id, parent_id=100),
                                    dict(id=99, name='New child', parent_id=100),
                                    dict(id=100, name='New parent')]}}
    assert import_data_from_json(db, payload) == dict(created=2, updated=1, errors=0)
    assert child.parent_id == db.get(Folder, 99).parent_id == 100
    db.expire_all()
    assert f'href="/list/{item.id}?folder_id={child.id}"' in client.get('/folders').text
    exported = client.get('/api/export').json()
    for _ in range(2):
        assert import_data_from_json(db, copy.deepcopy(exported))['errors'] == 0
        assert client.get('/api/export').json()['data'] == exported['data']
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as restored:
            assert import_data_from_json(restored, copy.deepcopy(exported))['errors'] == 0
            assert restored.get(Folder, child.id).parent_id == restored.get(Folder, 99).parent_id == 100
            assert restored.get(Folder, grandchild.id).parent_id == child.id
            assert restored.get(List, item.id).folder_id == child.id
    finally:
        engine.dispose()


def test_missing_parent_is_rejected_even_when_sqlite_foreign_keys_are_disabled():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            db.add(Folder(id=1, name='Original'))
            db.commit()
            with pytest.raises(ValueError, match='folders #99.*Parent folder #999.*No changes were saved'):
                import_data_from_json(db, {'data': {'folders': [dict(id=1, name='Must not rename'),
                                                               dict(id=99, name='Orphan', parent_id=999)]}})
            assert db.get(Folder, 1).name == 'Original' and db.get(Folder, 99) is None
    finally:
        engine.dispose()


def test_valid_import_can_repair_a_preexisting_folder_cycle(tree):
    db, client, root, child, grandchild, other, item = tree
    root.parent_id = child.id
    db.commit()
    assert import_data_from_json(db, {'data': {'folders': [dict(id=root.id, parent_id=None)]}})['updated'] == 1
    db.expire_all()
    assert f'href="/list/{item.id}?folder_id={child.id}"' in client.get('/folders').text

"""Partial imports preserve relationships unless their replacement is supplied."""
import copy

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from import_logic import import_data_from_json
from models import (Base, List, Todo, CalendarEvent, Hashtag, Folder, NamedSearch,
                    SmartFolder, SmartFolderIncludeHashtag, SmartFolderExcludeHashtag)
from test_billing_accounts import account, day


RELATIONSHIPS = {
    'lists': ('hashtags',),
    'todos': ('hashtags',),
    'calendar_events': ('hashtags',),
    'named_searches': ('hashtags', 'excluded_hashtags'),
    'smart_folders': ('include_hashtags', 'exclude_hashtags', 'lists', 'include_folders', 'exclude_folders'),
}


@pytest.fixture
def related(account):
    db, bill, client = account
    included, excluded = Hashtag(name='Included'), Hashtag(name='Excluded')
    first, second = Folder(name='Included folder'), Folder(name='Excluded folder')
    item = List(name='Original list', hashtags=[included])
    task = Todo(title='Original task', list=item, hashtags=[included])
    calendar = CalendarEvent(title='Original event', start_at=day('2026-10-01'), hashtags=[included])
    search = NamedSearch(name='Original search', hashtags=[included], excluded_hashtags=[excluded])
    smart = SmartFolder(name='Original smart folder', lists=[item], include_folders=[first], exclude_folders=[second])
    smart.include_hashtags_assoc = [SmartFolderIncludeHashtag(hashtag=included, include_subtodos=True, include_list_items=True)]
    smart.exclude_hashtags_assoc = [SmartFolderExcludeHashtag(hashtag=excluded, include_subtodos=True)]
    db.add_all([included, excluded, first, second, item, task, calendar, search, smart])
    db.commit()
    return db, client, dict(lists=item, todos=task, calendar_events=calendar, named_searches=search, smart_folders=smart)


def relation_snapshot(client):
    data = client.get('/api/export').json()['data']
    return {collection: {row['id']: {field: [{key: value for key, value in item.items() if key != 'name'}
                                           for item in row[field]] for field in fields} for row in data[collection]}
            for collection, fields in RELATIONSHIPS.items()}


@pytest.mark.parametrize('collection', RELATIONSHIPS)
def test_name_only_import_preserves_tags_selections_and_filter_flags(related, collection):
    db, client, records = related
    before = relation_snapshot(client)
    record = records[collection]
    key = 'title' if collection in ('todos', 'calendar_events') else 'name'
    result = import_data_from_json(db, {'data': {collection: [dict(id=record.id, **{key: 'Renamed'})]}})
    db.expire_all()
    assert result == dict(created=0, updated=1, errors=0)
    assert getattr(record, key) == 'Renamed'
    assert relation_snapshot(client) == before


@pytest.mark.parametrize('collection,field', [(collection, field) for collection, fields in RELATIONSHIPS.items() for field in fields])
@pytest.mark.parametrize('replacement', [None, []])
def test_explicit_empty_clears_only_selected_relationship_and_null_preserves_it(related, collection, field, replacement):
    db, client, records = related
    before = relation_snapshot(client)
    record = records[collection]
    result = import_data_from_json(db, {'data': {collection: [dict(id=record.id, **{field: replacement})]}})
    db.expire_all()
    expected = copy.deepcopy(before)
    if replacement == []:
        expected[collection][record.id][field] = []
    assert result['errors'] == 0
    assert relation_snapshot(client) == expected


@pytest.mark.parametrize('collection', RELATIONSHIPS)
def test_new_records_with_omitted_relationships_default_to_empty(related, collection):
    db, client, records = related
    data = dict(id=99)
    if collection == 'todos':
        data.update(title='New task', list_id=records['lists'].id)
    elif collection == 'calendar_events':
        data.update(title='New event', start_at='2026-10-01')
    else:
        data['name'] = 'New record'
    assert import_data_from_json(db, {'data': {collection: [data]}})['created'] == 1
    db.expire_all()
    assert all(value == [] for value in relation_snapshot(client)[collection][99].values())


def test_explicit_nonempty_tag_replacement_keeps_other_records_and_filters(related):
    db, client, records = related
    before = relation_snapshot(client)
    excluded = db.query(Hashtag).filter_by(name='Excluded').one()
    data = {'lists': [dict(id=records['lists'].id, hashtags=[dict(id=excluded.id)])],
            'smart_folders': [dict(id=records['smart_folders'].id,
                                  include_hashtags=[dict(id=excluded.id, include_subtodos=False, include_list_items=True)])]}
    assert import_data_from_json(db, {'data': data})['errors'] == 0
    db.expire_all()
    expected = copy.deepcopy(before)
    expected['lists'][records['lists'].id]['hashtags'] = [dict(id=excluded.id)]
    expected['smart_folders'][records['smart_folders'].id]['include_hashtags'] = [
        dict(id=excluded.id, include_subtodos=False, include_list_items=True)]
    assert relation_snapshot(client) == expected


def test_repeated_full_export_restore_preserves_all_relationships_and_filter_flags(related):
    db, client, records = related
    before = relation_snapshot(client)
    payload = client.get('/api/export').json()
    for _ in range(2):
        assert import_data_from_json(db, copy.deepcopy(payload))['errors'] == 0
        db.expire_all()
        assert relation_snapshot(client) == before
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as restored:
            assert import_data_from_json(restored, copy.deepcopy(payload))['errors'] == 0
            import main
            original = main.app.dependency_overrides[main.get_db]
            main.app.dependency_overrides[main.get_db] = lambda: restored
            try:
                assert relation_snapshot(client) == before
            finally:
                main.app.dependency_overrides[main.get_db] = original
    finally:
        engine.dispose()


def test_failed_import_rolls_back_explicit_relationship_clearing(related):
    db, client, records = related
    before = relation_snapshot(client)
    data = {'lists': [dict(id=records['lists'].id, hashtags=[])],
            'smart_folders': [dict(id=records['smart_folders'].id, include_folders=[])],
            'expenses': [dict(id=99, amount=5, date='2026-10-01', unsupported_field=True)]}
    with pytest.raises(ValueError, match='expenses #99.*No changes were saved'):
        import_data_from_json(db, {'data': data})
    assert relation_snapshot(client) == before

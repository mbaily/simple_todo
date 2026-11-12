"""Exports must restore delayed tasks and alarms as dates rather than strings."""
import copy

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from import_logic import import_data_from_json
from models import Base, List, Todo
from test_billing_accounts import account, day


DATE_FIELDS = [('lists', List, 'delayed_until'), ('todos', Todo, 'delayed_until'),
               ('todos', Todo, 'alarm_start'), ('todos', Todo, 'alarm_end')]


@pytest.mark.parametrize('collection,model,field', DATE_FIELDS)
def test_import_restores_delays_and_alarms_for_new_and_existing_records(account, collection, model, field):
    db, bill, client = account
    parent = List(name='Source list')
    db.add(parent)
    db.commit()
    values = dict(id=99, **{field: '2026-09-20T15:30:00'})
    if model is Todo:
        values.update(title='Imported task', list_id=parent.id)
    else:
        values['name'] = 'Imported list'
    assert import_data_from_json(db, {'data': {collection: [values]}})['created'] == 1
    item = db.get(model, 99)
    assert getattr(item, field) == day('2026-09-20T15:30:00')
    assert import_data_from_json(db, {'data': {collection: [dict(id=99, **{field: '2026-10-02T16:00:00'})]}})['updated'] == 1
    db.refresh(item)
    assert getattr(item, field) == day('2026-10-02T16:00:00')


@pytest.mark.parametrize('collection,model,field', DATE_FIELDS)
def test_invalid_delay_or_alarm_rolls_back_other_imported_records(account, collection, model, field):
    db, bill, client = account
    parent = List(name='Source list')
    task = Todo(title='Original task', list=parent)
    db.add_all([parent, task])
    db.commit()
    before = client.get('/api/export').json()['data']
    record = parent if model is List else task
    with pytest.raises(ValueError, match='No changes were saved'):
        import_data_from_json(db, {'data': {'folders': [dict(id=99, name='Must not persist')],
                                          collection: [dict(id=record.id, **{field: 'not-a-date'})]}})
    assert client.get('/api/export').json()['data'] == before


def test_full_export_restores_all_delay_and_alarm_fields_to_existing_and_empty_database(account):
    db, bill, client = account
    parent = List(name='Delayed list', delayed_until=day('2026-10-10'))
    task = Todo(title='Delayed task with alarm', list=parent, delayed_until=day('2026-10-09'),
                alarm_start=day('2026-10-09T10:00:00'), alarm_end=day('2026-10-09T10:30:00'))
    db.add_all([parent, task])
    db.commit()
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
            assert restored.get(List, parent.id).delayed_until == parent.delayed_until
            restored_task = restored.get(Todo, task.id)
            for field in ('delayed_until', 'alarm_start', 'alarm_end'):
                assert getattr(restored_task, field) == getattr(task, field)
    finally:
        engine.dispose()

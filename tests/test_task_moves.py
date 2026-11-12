"""Task moves must preserve subtrees and their attached notes."""
import pytest

from models import List, Todo, Note, CalendarEvent
from test_billing_accounts import account, day


def tree(db):
    source, target = List(name='Source'), List(name='Target')
    root = Todo(title='Parent', list=source)
    child = Todo(title='Child', list=source, parent=root)
    grandchild = Todo(title='Grandchild', list=source, parent=child)
    notes = [Note(text=f'Attached to {task.title}', list=source, todo=task)
             for task in (root, child, grandchild)]
    db.add_all([source, target, root, child, grandchild, *notes])
    db.commit()
    return source, target, (root, child, grandchild), notes


@pytest.mark.parametrize('selection', ['parent', 'child', 'parent_child', 'child_parent', 'duplicates'])
def test_task_move_keeps_all_descendant_notes_and_survives_old_list_deletion(account, selection):
    db, _, client = account
    source, target, tasks, notes = tree(db)
    root, child, grandchild = tasks
    choices = {'parent': [root.id], 'child': [child.id], 'parent_child': [root.id, child.id],
               'child_parent': [child.id, root.id], 'duplicates': [child.id, root.id, child.id]}
    moving = tasks[1:] if selection == 'child' else tasks
    ids, note_ids = [task.id for task in moving], [note.id for note in notes if note.todo_id in {t.id for t in moving}]
    source_id, target_id = source.id, target.id
    response = client.post('/api/tagged/move-items', json=dict(todo_ids=choices[selection], note_ids=[note_ids[0]], target_list_id=target_id))
    assert response.status_code == 200 and response.json() == dict(success=True, moved_todos=len(moving), moved_notes=len(note_ids))
    db.expire_all()
    assert all(db.get(Todo, identifier).list_id == target_id for identifier in ids)
    assert db.get(Todo, moving[0].id).parent_id is None
    assert db.get(Todo, grandchild.id).parent_id == child.id
    if selection != 'child':
        assert db.get(Todo, child.id).parent_id == root.id
    for identifier in note_ids:
        note = db.get(Note, identifier)
        assert note.list_id == target_id and note.todo_id in ids
        edited = client.post(f'/note/{identifier}/edit', data={'text': 'Preserved note'}, follow_redirects=False)
        assert edited.headers['location'] == f'/list/{target_id}'
    deleted = client.post(f'/list/{source_id}/delete', follow_redirects=False)
    assert deleted.status_code == 303
    db.expire_all()
    assert all(db.get(Todo, identifier) is not None for identifier in ids)
    assert all(db.get(Note, identifier) is not None for identifier in note_ids)
    assert client.get(f'/list/{target_id}').status_code == 200


def test_move_selected_notes_without_task_detaches_them_and_does_not_move_unselected_notes(account):
    db, _, client = account
    source, target, tasks, notes = tree(db)
    event = CalendarEvent(title='Event', start_at=day('2026-10-08'))
    event_note = Note(text='Calendar note', calendar_event=event, occurrence_at=day('2026-10-08'))
    db.add_all([event, event_note]); db.commit()
    response = client.post('/api/tagged/move-items', json=dict(todo_ids=[], note_ids=[notes[0].id, event_note.id, notes[0].id], target_list_id=str(target.id)))
    assert response.status_code == 200 and response.json()['moved_notes'] == 2
    db.expire_all()
    for note in (notes[0], event_note):
        assert note.list_id == target.id and note.todo_id is None and note.calendar_event_id is None and note.occurrence_at is None
    assert notes[1].list_id == source.id and notes[1].todo_id == tasks[1].id


def test_task_only_note_is_assigned_to_target_list_and_keeps_attachment(account):
    db, _, client = account
    source, target, tasks, notes = tree(db)
    extra = Note(text='Task-only note', todo=tasks[2])
    db.add(extra); db.commit()
    note_id = extra.id
    response = client.post('/api/tagged/move-items', json=dict(todo_ids=[tasks[0].id], target_list_id=target.id))
    assert response.status_code == 200 and response.json()['moved_notes'] == 4
    db.expire_all()
    assert db.get(Note, note_id).list_id == target.id and db.get(Note, note_id).todo_id == tasks[2].id
    assert client.post(f'/list/{source.id}/delete', follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(Note, note_id) is not None


@pytest.mark.parametrize('changes,status', [({'target_list_id': 'bad'}, 400), ({'target_list_id': True}, 400),
    ({'target_list_id': 999}, 404), ({'todo_ids': [999]}, 404), ({'note_ids': [999]}, 404),
    ({'todo_ids': '1'}, 400), ({'note_ids': [True]}, 400), ({'todo_ids': [1.5]}, 400)])
def test_invalid_move_does_not_partially_move_tasks_or_notes(account, changes, status):
    db, _, client = account
    source, target, tasks, notes = tree(db)
    before = client.get('/api/export').json()['data']
    payload = dict(todo_ids=[tasks[0].id], note_ids=[notes[0].id], target_list_id=target.id)
    payload.update(changes)
    response = client.post('/api/tagged/move-items', json=payload)
    assert response.status_code == status
    assert client.get('/api/export').json()['data'] == before


def test_detaching_a_subtask_never_deletes_it_but_deleting_a_parent_still_deletes_subtasks(account):
    db, _, client = account
    source, target, (root, child, grandchild), notes = tree(db)
    # Detaching must not be treated as orphan deletion (this was intermittent, depending
    # on which parents SQLAlchemy still tracked in memory).
    assert 'delete-orphan' not in Todo.children.property.cascade
    child.parent = None
    db.commit()
    db.expire_all()
    assert db.get(Todo, child.id) is not None and db.get(Todo, child.id).parent_id is None
    assert db.get(Todo, grandchild.id).parent_id == child.id
    child_id, grandchild_id = child.id, grandchild.id
    db.delete(db.get(Todo, child_id))
    db.commit()
    db.expire_all()
    assert db.query(Todo).filter(Todo.id.in_([child_id, grandchild_id])).count() == 0

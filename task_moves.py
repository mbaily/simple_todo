"""Move complete task trees and attached notes in the caller's transaction."""
from models import List, Todo, Note, utcnow
from helpers import create_journal_entry


class MoveError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _identifier(value):
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str) and value.isdecimal() and int(value) > 0:
        return int(value)
    raise MoveError('Choose valid task, note and target list IDs.')


def _selection(value):
    if not isinstance(value, list):
        raise MoveError('Task and note selections must be lists of IDs.')
    return {_identifier(identifier) for identifier in value}


def move_tagged_records(db, data):
    if not isinstance(data, dict):
        raise MoveError('Choose tasks or notes and a target list.')
    target_id = _identifier(data.get('target_list_id'))
    todo_ids, note_ids = _selection(data.get('todo_ids', [])), _selection(data.get('note_ids', []))
    target = db.get(List, target_id)
    if target is None:
        raise MoveError('Target list not found.', 404)
    selected = db.query(Todo).filter(Todo.id.in_(todo_ids)).all()
    notes = {note.id: note for note in db.query(Note).filter(Note.id.in_(note_ids)).all()}
    if {task.id for task in selected} != todo_ids or set(notes) != note_ids:
        raise MoveError('A selected task or note no longer exists. Reload and select the items again.', 404)

    # Gather every descendant before changing relationships. Selection order and
    # overlapping selections must not detach children from a selected parent.
    moving = {}
    pending = list(selected)
    while pending:
        task = pending.pop()
        if task.id in moving:
            continue
        moving[task.id] = task
        pending.extend(task.children)
    for task in moving.values():
        ancestors = set()
        current = task
        while current is not None and current.id in moving:
            if current.id in ancestors:
                raise MoveError('These tasks contain a parent cycle. Correct their hierarchy before moving them.')
            ancestors.add(current.id)
            current = current.parent
    notes.update({note.id: note for note in db.query(Note).filter(Note.todo_id.in_(moving)).all()})
    now = utcnow()
    original_lists = {task.list.id: task.list for task in moving.values() if task.list}
    original_lists.update({note.list.id: note.list for note in notes.values() if note.list})
    for task in moving.values():
        if task.parent_id not in moving:
            task.parent = None
        task.list = target
        task.modified_at = now
        create_journal_entry(db, 'Moved Todo (Tagged)', f"Moved todo '{task.title}' to list '{target.name}'", 'todo', task.id)
    for note in notes.values():
        if note.todo_id not in moving:
            note.todo = None
            note.calendar_event = None
            note.occurrence_at = None
        note.list = target
        note.modified_at = now
        create_journal_entry(db, 'Moved Note (Tagged)', f"Moved note to list '{target.name}'", 'note', note.id)
    for original in original_lists.values():
        original.modified_at = now
    target.modified_at = now
    return dict(success=True, moved_todos=len(moving), moved_notes=len(notes))

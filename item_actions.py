"""Shared item mutations used by JSON endpoints and the daisyUI HTML routes."""
from fastapi.responses import JSONResponse
from models import Todo, List, CalendarEvent, ListDatetimeHeader, SmartFolder, Note, JournalEntry, utcnow


def create_journal_entry(db, action, details=None, entity_type=None, entity_id=None):
    db.add(JournalEntry(action=action, details=details, entity_type=entity_type, entity_id=entity_id))


def delete_items(db, item_type, ids):
    if not ids:
        return JSONResponse({"error": "No items specified"}, status_code=400)

    if item_type == 'todo':
        for todo_id in ids:
            todo = db.query(Todo).filter(Todo.id == int(todo_id)).first()
            if todo:
                if todo.list:
                    todo.list.modified_at = utcnow()
                create_journal_entry(db, "Deleted Todo (Bulk)", f"Deleted todo '{todo.title}'", "todo", todo.id)
                db.delete(todo)
    elif item_type == 'list':
        for list_id in ids:
            todo_list = db.query(List).filter(List.id == int(list_id)).first()
            if todo_list:
                create_journal_entry(db, "Deleted List (Bulk)", f"Deleted list '{todo_list.name}'", "list", todo_list.id)
                db.delete(todo_list)
    elif item_type == 'calendar_event':
        for event_id in ids:
            ev = db.query(CalendarEvent).filter(CalendarEvent.id == int(event_id)).first()
            if ev:
                create_journal_entry(db, "Deleted Calendar Event (Bulk)", f"Deleted event '{ev.title}'", "calendar_event", ev.id)
                db.delete(ev)
    elif item_type == 'datetime_header':
        for header_id in ids:
            hdr = db.query(ListDatetimeHeader).filter(ListDatetimeHeader.id == int(header_id)).first()
            if hdr:
                # Update list modified time as the list contents changed.
                lst = db.query(List).filter(List.id == hdr.list_id).first()
                if lst:
                    lst.modified_at = utcnow()
                create_journal_entry(db, "Deleted Datetime Header (Bulk)", f"Deleted datetime header in list {hdr.list_id}", "list_datetime_header", hdr.id)
                db.delete(hdr)
    elif item_type == 'smart-folder' or item_type == 'smart_folder':
        for folder_id in ids:
            folder = db.query(SmartFolder).filter(SmartFolder.id == int(folder_id)).first()
            if folder:
                create_journal_entry(db, "Deleted Smart Folder (Bulk)", f"Deleted smart folder '{folder.name}'", "smart_folder", folder.id)
                db.delete(folder)
    elif item_type == 'note':
        for note_id in ids:
            note = db.query(Note).filter(Note.id == int(note_id)).first()
            if note:
                if note.list:
                    note.list.modified_at = utcnow()
                db.delete(note)
    else:
        return JSONResponse({"error": "Invalid type"}, status_code=400)

    db.commit()
    return JSONResponse({"success": True, "deleted": len(ids)})



def pin_items(db, item_type, ids, pin=True):
    if not ids:
        return JSONResponse({"error": "No items specified"}, status_code=400)

    now = utcnow()

    if item_type == 'todo':
        for todo_id in ids:
            todo = db.query(Todo).filter(Todo.id == int(todo_id)).first()
            if todo:
                todo.pinned = pin
                todo.pinned_at = now if pin else None
                todo.modified_at = now
                if todo.list:
                    todo.list.modified_at = now
                create_journal_entry(db, f"{'Pinned' if pin else 'Unpinned'} Todo (Bulk)", f"{'Pinned' if pin else 'Unpinned'} todo '{todo.title}'", "todo", todo.id)
    elif item_type == 'list':
        for list_id in ids:
            todo_list = db.query(List).filter(List.id == int(list_id)).first()
            if todo_list:
                todo_list.pinned = pin
                todo_list.pinned_at = now if pin else None
                create_journal_entry(db, f"{'Pinned' if pin else 'Unpinned'} List (Bulk)", f"{'Pinned' if pin else 'Unpinned'} list '{todo_list.name}'", "list", todo_list.id)
                todo_list.modified_at = now
    else:
        return JSONResponse({"error": "Invalid type"}, status_code=400)

    db.commit()
    return JSONResponse({"success": True, "updated": len(ids)})



def archive_items(db, item_type, ids, archived=True):
    if not ids:
        return JSONResponse({"error": "No items specified"}, status_code=400)
    if item_type != 'list':
        return JSONResponse({"error": "Only lists can be archived"}, status_code=400)

    now = utcnow()
    updated = 0
    for list_id in ids:
        todo_list = db.query(List).filter(List.id == int(list_id)).first()
        if todo_list:
            todo_list.archived = archived
            todo_list.modified_at = now
            create_journal_entry(db, f"{'Archived' if archived else 'Unarchived'} List (Bulk)", f"{'Archived' if archived else 'Unarchived'} list '{todo_list.name}'", "list", todo_list.id)
            updated += 1

    db.commit()
    return JSONResponse({"success": True, "updated": updated, "archived": archived})

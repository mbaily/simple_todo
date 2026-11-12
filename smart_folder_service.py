"""Which lists, tasks and calendar events a smart folder shows.

A smart folder combines:
- what to include: tags attached to items and hashtags written in their text; any one is
  enough, or (match_all) every one is required. No included tag or hashtag means everything.
- what to leave out: items carrying any excluded tag or hashtag.
- where to look: every folder, only some folders, or everywhere except some folders, and
  optionally only certain lists.
- which items: open only, a modified-date range, archived lists or not.
"""
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import and_, column, or_, select, table
from models import CalendarEvent, Folder, List, Note, Todo, Hashtag, SmartFolder
from hashtag_index import ensure_index

_index = table("hashtag_index", column("tag"), column("entity_type"), column("entity_id"))


def _indexed(entity_type, name):
    """Ids of the rows of one table whose text contains the hashtag."""
    return select(_index.c.entity_id).where(_index.c.entity_type == entity_type, _index.c.tag == name)


def _names(values):
    return [str(name).lstrip("#").lower() for name in (values or []) if str(name).strip("#")]


def _list_has_hashtag(name):
    """The hashtag is in the list's name or in one of the list's own notes."""
    noted = select(Note.list_id).where(Note.list_id != None, Note.todo_id == None, Note.id.in_(_indexed("notes", name)))
    return or_(List.id.in_(_indexed("lists", name)), List.id.in_(noted))


def _todo_has_hashtag(todo, name):
    """The hashtag is in the task's title or note, or in a note attached to the task."""
    noted = select(Note.todo_id).where(Note.todo_id != None, Note.id.in_(_indexed("notes", name)))
    return or_(todo.id.in_(_indexed("todos", name)), todo.id.in_(noted))


def _event_has_hashtag(name):
    noted = select(Note.calendar_event_id).where(Note.calendar_event_id != None, Note.id.in_(_indexed("notes", name)))
    return or_(CalendarEvent.id.in_(_indexed("calendar_events", name)), CalendarEvent.id.in_(noted))


def _combine(folder, conditions):
    """Included tags and hashtags: any one, or all of them."""
    return (and_ if folder.match_all else or_)(*conditions)


def _with_subtasks(condition):
    """A task carrying a rule, or any of its descendants, at every nesting depth."""
    matched = select(Todo.id).where(condition).cte(recursive=True)
    matched = matched.union(select(Todo.id).join(matched, Todo.parent_id == matched.c.id))
    return Todo.id.in_(select(matched.c.id))


def _folder_scope(db, folder):
    """Condition on List.folder_id for the folders a smart folder looks in, or None for everywhere."""
    def expand(ids):
        ids = set(ids)
        if not folder.include_subfolders or not ids:
            return ids
        children = {}
        for folder_id, parent_id in db.query(Folder.id, Folder.parent_id).all():
            children.setdefault(parent_id, []).append(folder_id)
        found, pending = set(), list(ids)
        while pending:
            current = pending.pop()
            if current not in found:
                found.add(current)
                pending.extend(children.get(current, []))
        return found

    conditions = []
    only = expand(f.id for f in folder.include_folders)
    if only or folder.include_root_folder:
        places = []
        if only:
            places.append(List.folder_id.in_(only))
        if folder.include_root_folder:
            places.append(List.folder_id == None)
        conditions.append(or_(*places))
    without = expand(f.id for f in folder.exclude_folders)
    if without:
        conditions.append(or_(List.folder_id == None, ~List.folder_id.in_(without)))
    if folder.exclude_root_folder:
        conditions.append(List.folder_id != None)
    return and_(*conditions) if conditions else None


class SmartFolderService:
    @staticmethod
    def get_lists(db: Session, folder: SmartFolder):
        """Get lists matching smart folder criteria."""
        if not folder.show_lists:
            return []
        ensure_index(db)
        list_ids = [l.id for l in folder.lists]

        ql = db.query(List)
        if not folder.include_archived_lists:
            ql = ql.filter(List.archived == False)
        if list_ids:
            ql = ql.filter(List.id.in_(list_ids))
        include = [List.hashtags.any(Hashtag.id == a.hashtag_id) for a in folder.include_hashtags_assoc]
        include += [_list_has_hashtag(name) for name in _names(folder.include_text_hashtags)]
        if include:
            ql = ql.filter(_combine(folder, include))
        exclude = [List.hashtags.any(Hashtag.id == a.hashtag_id) for a in folder.exclude_hashtags_assoc]
        exclude += [_list_has_hashtag(name) for name in _names(folder.exclude_text_hashtags)]
        if exclude:
            ql = ql.filter(~or_(*exclude))
        scope = _folder_scope(db, folder)
        if scope is not None:
            ql = ql.filter(scope)

        if folder.uncompleted_only:
            ql = ql.filter(List.completed == False)
        if folder.modified_after:
            ql = ql.filter(List.modified_at >= folder.modified_after)
        if folder.modified_before:
            ql = ql.filter(List.modified_at < folder.modified_before)
            
        # Sorting configuration
        list_sort_field = (folder.list_sort_field or 'modified_at').strip()
        if list_sort_field not in ('modified_at', 'created_at'):
            list_sort_field = 'modified_at'
        list_sort_desc = bool(folder.list_sort_desc) if folder.list_sort_desc is not None else True
        
        list_sort_col = getattr(List, list_sort_field)
        return ql.distinct().order_by(list_sort_col.desc() if list_sort_desc else list_sort_col.asc()).all()

    @staticmethod
    def get_todos(db: Session, folder: SmartFolder, *, matching_ids=None):
        """Get todos matching smart folder criteria.
        
        Returns:
            Tuple (matching_todos, todo_groups)
        When supplied, matching_ids is filled before nested children are removed from
        the top-level rows, so strict nesting can still recognize those children.
        """
        matching_todos = []
        todo_groups = []

        if not folder.show_todos:
            return matching_todos, todo_groups
        ensure_index(db)
        list_ids = [l.id for l in folder.lists]
        qt = db.query(Todo).join(List, Todo.list)
        if not folder.include_archived_lists:
            qt = qt.filter(List.archived == False)
        if list_ids:
            qt = qt.filter(Todo.list_id.in_(list_ids))
        scope = _folder_scope(db, folder)
        if scope is not None:
            qt = qt.filter(scope)
        if folder.root_todos_only:
            qt = qt.filter(Todo.parent_id == None)

        def tagged(assoc, list_items):
            direct = Todo.hashtags.any(Hashtag.id == assoc.hashtag_id)
            has = [_with_subtasks(direct) if folder.include_subtodos or assoc.include_subtodos else direct]
            if list_items:
                has.append(List.hashtags.any(Hashtag.id == assoc.hashtag_id))
            return or_(*has)

        def written(name, list_items):
            direct = _todo_has_hashtag(Todo, name)
            has = [_with_subtasks(direct) if folder.include_subtodos else direct]
            if list_items:
                has.append(_list_has_hashtag(name))
            return or_(*has)

        include = [tagged(a, folder.include_list_items or a.include_list_items) for a in folder.include_hashtags_assoc]
        include += [written(name, folder.include_list_items) for name in _names(folder.include_text_hashtags)]
        if include:
            qt = qt.filter(_combine(folder, include))
        exclude = [tagged(a, False) for a in folder.exclude_hashtags_assoc]
        exclude += [written(name, False) for name in _names(folder.exclude_text_hashtags)]
        if exclude:
            qt = qt.filter(~or_(*exclude))

        if folder.uncompleted_only:
            qt = qt.filter(Todo.completed == False)
        if folder.modified_after:
            qt = qt.filter(Todo.modified_at >= folder.modified_after)
        if folder.modified_before:
            qt = qt.filter(Todo.modified_at < folder.modified_before)

        # Sorting
        todo_sort_field = (folder.todo_sort_field or 'modified_at').strip()
        if todo_sort_field not in ('modified_at', 'created_at'):
            todo_sort_field = 'modified_at'
        todo_sort_desc = bool(folder.todo_sort_desc) if folder.todo_sort_desc is not None else True

        todo_sort_col = getattr(Todo, todo_sort_field)
        matching_todos = qt.distinct().order_by(todo_sort_col.desc() if todo_sort_desc else todo_sort_col.asc()).all()
        if matching_ids is not None:
            matching_ids.update(todo.id for todo in matching_todos)

        # Nesting Logic
        if folder.nest_subtodos:
            matching_ids = {t.id for t in matching_todos}
            matching_todos = [t for t in matching_todos if t.parent_id is None or t.parent_id not in matching_ids]

        # Grouping Logic
        todo_tree = None
        if folder.group_todos_by_list:
            # Sort matching todos first by list sort logic to ensure consistent input?
            # organize_todos_into_tree sorts groups by list name and Modified At Desc for todos.
            # If the smart folder has specific sorting requirements for *List Names*, organize_todos_into_tree uses alphabetical.
            # However, Smart Folders supports `list_sort_field`.
            # organize_todos_into_tree currently hardcodes list sort by name. 
            # We might want to enhance organize_todos_into_tree to accept sort key, but for now let's just use it.
            # Users who group by list usually expect alphabetical or folder structure. 
            
            from grouping_service import organize_todos_into_tree
            
            # Pass user's todo sort preference to the tree organizer
            # We want recursive date sorting based on these fields.
            todo_tree = organize_todos_into_tree(
                db, 
                matching_todos,
                sort_by_todo_date=True,
                sort_field=todo_sort_field,
                sort_desc=todo_sort_desc
            )

        return matching_todos, todo_tree

    @staticmethod
    def get_calendar_events(db: Session, folder: SmartFolder):
        """Get calendar events matching smart folder criteria.

        Events belong to no list or folder, so only the tag, hashtag and date rules apply.
        """
        from models import CalendarEventCompletion

        if folder.show_events is not None and not folder.show_events:
            return []
        ensure_index(db)
        qe = db.query(CalendarEvent)
        include = [CalendarEvent.hashtags.any(Hashtag.id == a.hashtag_id) for a in folder.include_hashtags_assoc]
        include += [_event_has_hashtag(name) for name in _names(folder.include_text_hashtags)]
        if include:
            qe = qe.filter(_combine(folder, include))
        exclude = [CalendarEvent.hashtags.any(Hashtag.id == a.hashtag_id) for a in folder.exclude_hashtags_assoc]
        exclude += [_event_has_hashtag(name) for name in _names(folder.exclude_text_hashtags)]
        if exclude:
            qe = qe.filter(~or_(*exclude))

        if folder.modified_after:
            qe = qe.filter(CalendarEvent.modified_at >= folder.modified_after)
        if folder.modified_before:
            qe = qe.filter(CalendarEvent.modified_at < folder.modified_before)

        events_raw = qe.distinct().all()
        
        # We need to process these to find the relevant "display" occurrence
        # similar to hashtags/search, because smart folders often imply "active" items.
        # However, smart folders might be used for archival or history too.
        # If the user asks for "uncompleted only", we definitely should do next-occ logic.
        # If they don't, we probably still want to show the next occurrence rather than 
        # a 3-year old recurrence start date.
        
        # Let's apply the same logic: show next occurrence on or after today.
        
        from datetime import datetime
        # We need _midnight and helpers... better to import them or duplicate simple logic?
        # Since this service is imported by main, let's keep it self-contained if possible,
        # or import the helper.
        from helpers import get_next_uncompleted_occurrence, _midnight
        from models import CalendarEventCompletion
        
        today = _midnight(datetime.now())
        
        # We need completion data
        completion_rows = db.query(CalendarEventCompletion).all()
        completed_set = {(c.event_id, c.occurrence_at) for c in completion_rows}
        
        processed_events = []
        for ev in events_raw:
             next_occ = get_next_uncompleted_occurrence(ev, completed_set, today)
             
             if folder.uncompleted_only:
                 if not next_occ:
                     continue
                 ev.display_start_at = next_occ
                 processed_events.append(ev)
             else:
                 # Standard view: show next if available, else original (or skip if we want strict future view?)
                 # For consistency with other views, lets show next or start.
                 ev.display_start_at = next_occ if next_occ else ev.start_at
                 processed_events.append(ev)
        
        # Return sorted by display date
        processed_events.sort(key=lambda x: x.display_start_at)
        return processed_events

from datetime import datetime, timedelta
from sqlalchemy.orm import Session, aliased
from sqlalchemy import or_
from models import List, Todo, Hashtag, SmartFolder

class SmartFolderService:
    @staticmethod
    def get_lists(db: Session, folder: SmartFolder):
        """Get lists matching smart folder criteria."""
        include_ids = [a.hashtag_id for a in folder.include_hashtags_assoc]
        exclude_ids = [a.hashtag_id for a in folder.exclude_hashtags_assoc]
        list_ids = [l.id for l in folder.lists]
        include_folder_ids = [f.id for f in folder.include_folders]
        exclude_folder_ids = [f.id for f in folder.exclude_folders]

        if not folder.show_lists:
            return []

        ql = db.query(List)
        if not folder.include_archived_lists:
            ql = ql.filter(List.archived == False)
        if list_ids:
            ql = ql.filter(List.id.in_(list_ids))
        if include_ids:
            ql = ql.join(List.hashtags).filter(Hashtag.id.in_(include_ids))
        if exclude_ids:
            ql = ql.filter(~List.hashtags.any(Hashtag.id.in_(exclude_ids)))
        if include_folder_ids:
            ql = ql.filter(List.folder_id.in_(include_folder_ids))
        elif folder.include_root_folder:
            # Include only items in root (no folder)
            ql = ql.filter(List.folder_id == None)
        if exclude_folder_ids:
            ql = ql.filter(or_(List.folder_id == None, ~List.folder_id.in_(exclude_folder_ids)))
        if folder.exclude_root_folder:
            # Exclude items in root (no folder)
            ql = ql.filter(List.folder_id != None)

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
    def get_todos(db: Session, folder: SmartFolder):
        """Get todos matching smart folder criteria.
        
        Returns:
            Tuple (matching_todos, todo_groups)
        """
        include_assocs = folder.include_hashtags_assoc
        exclude_assocs = folder.exclude_hashtags_assoc
        list_ids = [l.id for l in folder.lists]
        include_folder_ids = [f.id for f in folder.include_folders]
        exclude_folder_ids = [f.id for f in folder.exclude_folders]

        matching_todos = []
        todo_groups = []

        if not folder.show_todos:
            return matching_todos, todo_groups

        qt = db.query(Todo)
        if not folder.include_archived_lists:
            qt = qt.join(Todo.list).filter(List.archived == False)
        if list_ids:
            qt = qt.filter(Todo.list_id.in_(list_ids))
        if include_folder_ids:
             if not folder.include_archived_lists:
                 # Already joined above
                 qt = qt.filter(List.folder_id.in_(include_folder_ids))
             else:
                 qt = qt.join(List, Todo.list).filter(List.folder_id.in_(include_folder_ids))
        elif folder.include_root_folder:
             # Include only items in root (no folder)
             if not folder.include_archived_lists:
                 qt = qt.filter(List.folder_id == None)
             else:
                 qt = qt.join(List, Todo.list).filter(List.folder_id == None)
        if exclude_folder_ids:
             if folder.include_archived_lists and not include_folder_ids and not folder.include_root_folder:
                 qt = qt.join(List, Todo.list)
             
             qt = qt.filter(or_(List.folder_id == None, ~List.folder_id.in_(exclude_folder_ids)))
        if folder.exclude_root_folder:
             # Exclude items in root (no folder)
             if folder.include_archived_lists and not include_folder_ids and not folder.include_root_folder and not exclude_folder_ids:
                 qt = qt.join(List, Todo.list)
             qt = qt.filter(List.folder_id != None)
        if folder.root_todos_only:
            qt = qt.filter(Todo.parent_id == None)
        
        # --- INCLUDE LOGIC ---
        if include_assocs:
            direct_include_ids = [a.hashtag_id for a in include_assocs if not a.include_subtodos]
            recursive_include_ids = [a.hashtag_id for a in include_assocs if a.include_subtodos]
            cascade_include_ids = [a.hashtag_id for a in include_assocs if a.include_list_items]
            
            conditions = []
            all_include_ids = direct_include_ids + recursive_include_ids
            if all_include_ids:
                conditions.append(Todo.hashtags.any(Hashtag.id.in_(all_include_ids)))
            
            if recursive_include_ids:
                ParentTodo = aliased(Todo)
                qt = qt.outerjoin(ParentTodo, Todo.parent)
                conditions.append(ParentTodo.hashtags.any(Hashtag.id.in_(recursive_include_ids)))

            if cascade_include_ids:
                conditions.append(Todo.list.has(List.hashtags.any(Hashtag.id.in_(cascade_include_ids))))
            
            if conditions:
                qt = qt.filter(or_(*conditions))
        
        # --- EXCLUDE LOGIC ---
        if exclude_assocs:
            direct_exclude_ids = [a.hashtag_id for a in exclude_assocs if not a.include_subtodos]
            recursive_exclude_ids = [a.hashtag_id for a in exclude_assocs if a.include_subtodos]
            
            if direct_exclude_ids:
                qt = qt.filter(~Todo.hashtags.any(Hashtag.id.in_(direct_exclude_ids)))
            
            if recursive_exclude_ids:
                ParentTodoEx = aliased(Todo)
                qt = qt.outerjoin(ParentTodoEx, Todo.parent)
                qt = qt.filter(
                    ~or_(
                        Todo.hashtags.any(Hashtag.id.in_(recursive_exclude_ids)),
                        ParentTodoEx.hashtags.any(Hashtag.id.in_(recursive_exclude_ids))
                    )
                )
        
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
        """Get calendar events matching smart folder criteria."""
        include_assocs = folder.include_hashtags_assoc
        exclude_assocs = folder.exclude_hashtags_assoc
        
        # We only really filter calendar events by hashtags or date range (if applicable).
        # List/Folder filters don't apply to calendar events.
        
        # If hashtags are required but none provided, and no other criteria applies?
        # Smart folders are usually inclusive OR for includes.
        
        from models import CalendarEvent, CalendarEventCompletion

        # Base query
        qe = db.query(CalendarEvent)

        # Hash tag logic (similar to todos but simpler - no recurrence/parent logic for now)
        if include_assocs:
             # Gather all include IDs (ignoring subtodo flag for events)
             include_ids = [a.hashtag_id for a in include_assocs]
             if include_ids:
                 qe = qe.join(CalendarEvent.hashtags).filter(Hashtag.id.in_(include_ids))
        
        if exclude_assocs:
             exclude_ids = [a.hashtag_id for a in exclude_assocs]
             if exclude_ids:
                 qe = qe.filter(~CalendarEvent.hashtags.any(Hashtag.id.in_(exclude_ids)))
        
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

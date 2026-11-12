from typing import Dict, List, Any, Optional
from sqlalchemy.orm import Session
from datetime import datetime
from calendar_service import parse_calendar_duration
from sqlalchemy import text # For manual ID insertion/lookup if needed

from models import (
    List as ListModel,
    Todo,
    Folder,
    Hashtag,
    SmartFolder,
    CalendarEvent,
    NamedSearch,
    ListDatetimeHeader,
    CalendarEventCompletion,
    DokuWikiConfirmedItem,
    collation_todos,
    Note,
    Link,
    SmartFolderIncludeHashtag,
    SmartFolderExcludeHashtag,
    JournalEntry,
)

def create_journal_entry_sync(db: Session, action: str, details: str = None, entity_type: str = None, entity_id: int = None):
    """Creates a journal entry for a sync interaction."""
    try:
        entry = JournalEntry(
            action=action,
            details=details,
            entity_type=entity_type,
            entity_id=entity_id
        )
        db.add(entry)
    except Exception:
        pass # Don't fail sync due to journaling

def parse_iso(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        # datetime.fromisoformat handles "YYYY-MM-DDTHH:MM:SS"
        # It handles timezone offsets if present in Python 3.7+
        return datetime.fromisoformat(dt_str)
    except (ValueError, TypeError):
        return None

def _resolve_id(client_id: Any, id_map: Dict[str, int]) -> Optional[int]:
    """
    Resolve a client ID to a server ID.
    If client_id is None or 0, return None.
    If client_id is negative (temp ID), look up in id_map.
    If client_id is positive, it's already a server ID.
    """
    if client_id is None:
        return None
    
    try:
        cid_int = int(client_id)
    except (ValueError, TypeError):
        # Could be string temp ID?
        if str(client_id) in id_map:
            return id_map[str(client_id)]
        return None # Invalid ID

    if cid_int > 0:
        return cid_int
    
    # Negative ID -> check map
    # We convert to string because JSON keys are strings
    cid_str = str(cid_int)
    return id_map.get(cid_str)

def apply_modifications(db: Session, modifications: Dict[str, Any]) -> Dict[str, int]:
    """
    Apply modifications to the database.
    modifications: dict of "lists": [...], "todos": [...] etc.
    Returns: id_map { "temp_id": real_id }
    """
    id_map = {}
    
    # 1. Hashtags
    # Hashtags are usually looked up by name or ID.
    # If key is provided, update. If new, create.
    if 'hashtags' in modifications:
        for item in modifications['hashtags']:
            apply_hashtag(db, item, id_map)
            
    # 2. Folders (Self-referential, requires care with parent_id)
    # We might need two passes for folders to handle parenting of new folders to new folders.
    # Simple approach: Create all first (orphan if parent missing), then update parents.
    if 'folders' in modifications:
        apply_folders(db, modifications['folders'], id_map)

    # 3. Lists
    if 'lists' in modifications:
        for item in modifications['lists']:
            apply_list(db, item, id_map)

    # 4. Todos (Self-referential parent_id)
    if 'todos' in modifications:
        apply_todos(db, modifications['todos'], id_map)

    # 5. Calendar Events
    if 'calendar_events' in modifications:
        for item in modifications['calendar_events']:
            apply_calendar_event(db, item, id_map)

    # 6. Smart Folders
    if 'smart_folders' in modifications:
        for item in modifications['smart_folders']:
            apply_smart_folder(db, item, id_map)

    # 7. Named Searches
    if 'named_searches' in modifications:
        for item in modifications['named_searches']:
            apply_named_search(db, item, id_map)

    # 8. List Headers
    if 'headers' in modifications:
        for item in modifications['headers']:
            apply_header(db, item, id_map)
            
    # 9. Calendar Completions
    if 'calendar_completions' in modifications:
        for item in modifications['calendar_completions']:
            apply_calendar_completion(db, item, id_map)

    # 10. Collation Associations
    if 'collation_associations' in modifications:
        for item in modifications['collation_associations']:
            apply_collation_association(db, item, id_map)

    # 11. DokuWiki Confirmed Items
    if 'confirmed_items' in modifications:
        for item in modifications['confirmed_items']:
            apply_confirmed_item(db, item, id_map)
            
    # 12. Deletions (Last to avoid reference issues? Or first? Or depends?)
    # Usually safe to delete last, or after relationships are handled.
    # But if we delete a list, its todos might be deleted via cascade.
    if 'deletions' in modifications:
        apply_deletions(db, modifications['deletions'], id_map)

    db.commit()
    return id_map

def apply_hashtag(db: Session, data: Dict[str, Any], id_map: Dict[str, int]):
    client_id = data.get('id')
    name = data.get('name')
    if not name: return # Name required

    # Try to find by ID if positive
    obj = None
    if client_id and isinstance(client_id, int) and client_id > 0:
        obj = db.query(Hashtag).filter(Hashtag.id == client_id).first()
    
    # If not found by ID, try name (hashtags are unique by name)
    if not obj:
        obj = db.query(Hashtag).filter(Hashtag.name == name).first()
    
    if not obj:
        obj = Hashtag(name=name)
        db.add(obj)
        db.flush() # Get ID
        if client_id and (isinstance(client_id, int) and client_id <= 0):
            id_map[str(client_id)] = obj.id
    
    # Update fields
    if 'show_completed' in data:
        obj.show_completed = data['show_completed']
    
    # Commit changes implicitly done by flush, formal commit at end

def apply_folders(db: Session, items: List[Dict[str, Any]], id_map: Dict[str, int]):
    # Two passes: Create/Identify, then Link Parents
    
    # Pass 1: Ensure existence
    # Pass 1: Ensure existence
    for data in items:
        client_id = data.get('id')
        obj = None
        is_new = False
        
        if client_id and isinstance(client_id, int) and client_id > 0:
            obj = db.query(Folder).filter(Folder.id == client_id).first()
            if not obj: continue
        else:
            obj = Folder(name=data.get('name', 'New Folder'))
            db.add(obj)
            db.flush()
            is_new = True
            if client_id and (isinstance(client_id, int) and client_id <= 0):
                id_map[str(client_id)] = obj.id
        
        if is_new:
            create_journal_entry_sync(db, "Created Folder (Sync)", f"Name: {obj.name}", "folder", obj.id)
        else:
            create_journal_entry_sync(db, "Updated Folder (Sync)", f"Name: {obj.name}", "folder", obj.id)
        
        # Update basic fields
        if 'name' in data: obj.name = data['name']
        if 'is_collapsed' in data: obj.is_collapsed = data['is_collapsed']
        if 'sort_order' in data: obj.sort_order = data['sort_order']

    # Pass 2: Parents
    for data in items:
        client_id = data.get('id')
        server_id = _resolve_id(client_id, id_map)
        if not server_id: continue # Should exist now
        
        obj = db.query(Folder).filter(Folder.id == server_id).first()
        if not obj: continue

        # Parent ID
        if 'parent_id' in data:
            pid = data.get('parent_id')
            resolved_pid = _resolve_id(pid, id_map)
            
            # Prevent self-parenting and cycles
            if resolved_pid != obj.id:
                if not resolved_pid or not causes_cycle(db, obj.id, resolved_pid):
                    obj.parent_id = resolved_pid

    db.flush()

def apply_list(db: Session, data: Dict[str, Any], id_map: Dict[str, int]):
    client_id = data.get('id')
    obj = None
    is_new = False

    if client_id and isinstance(client_id, int) and client_id > 0:
        obj = db.query(ListModel).filter(ListModel.id == client_id).first()
        if not obj: return
    else:
        obj = ListModel(name=data.get('name', 'New List'))
        db.add(obj)
        db.flush()
        is_new = True
        if client_id and (isinstance(client_id, int) and client_id <= 0):
            id_map[str(client_id)] = obj.id
            create_journal_entry_sync(db, "Created List (Sync)", f"Name: {obj.name}", "list", obj.id)
        else:
            # If we found it, it's an update (or a recreate with positive ID, but strict sync usually prevents that)
            if client_id: create_journal_entry_sync(db, "Updated List (Sync)", f"Name: {obj.name}", "list", obj.id)
    
    # Update fields
    for field in ['name', 'completed', 'pinned', 'is_collation', 'collation_color', 
                  'archived', 'show_completed', 'sort_ascending', 
                  'partition_by_datetime_headers', 'intersperse_collated_todos',
                  'manual_sorting', 'show_delayed', 'sort_order']:
        if field in data:
            setattr(obj, field, data[field])
            
    if 'pinned_at' in data: obj.pinned_at = parse_iso(data['pinned_at'])
    if 'created_at' in data: obj.created_at = parse_iso(data['created_at'])
    if 'modified_at' in data: obj.modified_at = parse_iso(data['modified_at'])
    if 'delayed_until' in data: obj.delayed_until = parse_iso(data['delayed_until'])
    
    if 'folder_id' in data:
        obj.folder_id = _resolve_id(data['folder_id'], id_map)

    # Hashtags
    if 'hashtag_ids' in data:
        new_ids = []
        for h_id in data['hashtag_ids']:
            rid = _resolve_id(h_id, id_map)
            if rid: new_ids.append(rid)
        
        if new_ids:
            tags = db.query(Hashtag).filter(Hashtag.id.in_(new_ids)).all()
            obj.hashtags = tags
            
    # Links
    if 'links' in data:
        update_links(db, obj, data['links'])

    db.flush()

def apply_todos(db: Session, items: List[Dict[str, Any]], id_map: Dict[str, int]):
    # Pass 1: Create/Update (ignoring parent_id initially to avoid cycle issues)
    for data in items:
        client_id = data.get('id')
        obj = None
        is_new = False

        if client_id and isinstance(client_id, int) and client_id > 0:
            obj = db.query(Todo).filter(Todo.id == client_id).first()
            if not obj: continue
        else:
            # List ID is required for creation
            list_id = _resolve_id(data.get('list_id'), id_map)
            
            # Fallback to Inbox/First List if list_id missing
            if not list_id:
                first_list = db.query(ListModel).filter(ListModel.archived == False).order_by(ListModel.id).first()
                if first_list:
                    list_id = first_list.id
            
            if not list_id:
                # Still no list? Cannot create todo.
                continue
                
            obj = Todo(title=data.get('title', 'New Todo'), list_id=list_id)
            db.add(obj)
            db.flush()
            is_new = True
            if client_id and (isinstance(client_id, int) and client_id <= 0):
                id_map[str(client_id)] = obj.id

        if is_new:
             create_journal_entry_sync(db, "Created Todo (Sync)", f"Title: {obj.title}", "todo", obj.id)
        else:
             create_journal_entry_sync(db, "Updated Todo (Sync)", f"Title: {obj.title}", "todo", obj.id)

        # Update fields
        for field in ['title', 'note', 'color', 'completed', 'pinned', 'sort_order']:
            if field in data:
                setattr(obj, field, data[field])
        
        if 'pinned_at' in data: obj.pinned_at = parse_iso(data['pinned_at'])
        if 'created_at' in data: obj.created_at = parse_iso(data['created_at'])
        if 'modified_at' in data: obj.modified_at = parse_iso(data['modified_at'])
        if 'delayed_until' in data: obj.delayed_until = parse_iso(data['delayed_until'])
        if 'alarm_start' in data: obj.alarm_start = parse_iso(data['alarm_start'])
        if 'alarm_end' in data: obj.alarm_end = parse_iso(data['alarm_end'])
        
        if 'list_id' in data:
            lid = _resolve_id(data['list_id'], id_map)
            if lid: obj.list_id = lid

        # Hashtags
        if 'hashtag_ids' in data:
            new_ids = []
            for h_id in data['hashtag_ids']:
                rid = _resolve_id(h_id, id_map)
                if rid: new_ids.append(rid)
            if new_ids:
                tags = db.query(Hashtag).filter(Hashtag.id.in_(new_ids)).all()
                obj.hashtags = tags

    # Links
    if 'links' in data:
        update_links(db, obj, data['links'])

    # Pass 2: Parent IDs
    for data in items:
        client_id = data.get('id')
        server_id = _resolve_id(client_id, id_map)
        if not server_id: continue
        
        obj = db.query(Todo).filter(Todo.id == server_id).first()
        if not obj: continue
        
        if 'parent_id' in data:
            pid = data.get('parent_id')
            resolved_pid = _resolve_id(pid, id_map)
            # Prevent self-parenting and cycles
            if resolved_pid != obj.id:
                if not resolved_pid or not causes_todo_cycle(db, obj.id, resolved_pid):
                    obj.parent_id = resolved_pid
                
    db.flush()

def apply_calendar_event(db: Session, data: Dict[str, Any], id_map: Dict[str, int]):
    if 'all_day' in data and not isinstance(data['all_day'], bool):
        raise ValueError('All-day must be true or false.')
    if 'duration_minutes' in data:
        data['duration_minutes'] = parse_calendar_duration(data['duration_minutes'])
    client_id = data.get('id')
    obj = None
    is_new = False

    if client_id and isinstance(client_id, int) and client_id > 0:
        obj = db.query(CalendarEvent).filter(CalendarEvent.id == client_id).first()
        if not obj: return
    else:
        start_at = parse_iso(data.get('start_at'))
        if not start_at: start_at = datetime.now() # Fallback
        
        obj = CalendarEvent(title=data.get('title', 'Event'), start_at=start_at)
        db.add(obj)
        db.flush()
        is_new = True
        if client_id and (isinstance(client_id, int) and client_id <= 0):
            id_map[str(client_id)] = obj.id

    if 'title' in data: obj.title = data['title']
    if 'note' in data: obj.note = data['note']
    if 'rrule' in data: obj.rrule = data['rrule']
    if 'all_day' in data: obj.all_day = data['all_day']
    if 'duration_minutes' in data: obj.duration_minutes = data['duration_minutes']
    if 'important_if_late' in data: obj.important_if_late = bool(data['important_if_late'])
    if 'start_at' in data: 
        ts = parse_iso(data['start_at'])
        if ts: obj.start_at = ts

    # Hashtags
    if 'hashtag_ids' in data:
        new_ids = []
        for h_id in data['hashtag_ids']:
            rid = _resolve_id(h_id, id_map)
            if rid: new_ids.append(rid)
        if new_ids:
            tags = db.query(Hashtag).filter(Hashtag.id.in_(new_ids)).all()
            obj.hashtags = tags
    
    db.flush()

def apply_smart_folder(db: Session, data: Dict[str, Any], id_map: Dict[str, int]):
    client_id = data.get('id')
    obj = None
    if client_id and isinstance(client_id, int) and client_id > 0:
        obj = db.query(SmartFolder).filter(SmartFolder.id == client_id).first()
        if not obj: return
    else:
        obj = SmartFolder(name=data.get('name', 'Smart Folder'))
        db.add(obj)
        db.flush()
        if client_id and (isinstance(client_id, int) and client_id <= 0):
            id_map[str(client_id)] = obj.id

    simple_fields = [
        'name', 'show_lists', 'show_todos', 'group_todos_by_list', 
        'root_todos_only', 'nest_subtodos', 'uncompleted_only', 
        'include_archived_lists', 'list_sort_field', 'list_sort_desc', 
        'todo_sort_field', 'todo_sort_desc', 'dokuwiki_links_enabled', 
        'dokuwiki_date_field', 'include_root_folder', 'exclude_root_folder',
        'strict_nesting', 'sort_order'
    ]
    for field in simple_fields:
        if field in data:
            setattr(obj, field, data[field])

    if 'modified_after' in data: obj.modified_after = parse_iso(data['modified_after'])
    if 'modified_before' in data: obj.modified_before = parse_iso(data['modified_before'])
    
    # Relationships like include_hashtags are complex (association model).
    # For bulk sync, maybe simpler to clear and re-add if provided?
    # Or just ignore for now as it's complex. 
    # Let's implement basic List inclusion.
    if 'lists' in data: # Expected list of dicts with IDs?
        # Serializer: "lists": [{"id": l.id, "name": l.name} ...]
        # Or incoming might be just IDs?
        # Let's assume input might match output structure, i.e. list of dicts.
        target_ids = []
        for l_item in data['lists']:
             if isinstance(l_item, dict):
                 lid = _resolve_id(l_item.get('id'), id_map)
             else:
                 lid = _resolve_id(l_item, id_map)
             if lid: target_ids.append(lid)
        
        if target_ids:
             lists = db.query(ListModel).filter(ListModel.id.in_(target_ids)).all()
             obj.lists = lists

    if 'include_hashtags' in data:
        update_smart_folder_hashtags(db, obj, data['include_hashtags'], True, id_map)
        
    if 'exclude_hashtags' in data:
        update_smart_folder_hashtags(db, obj, data['exclude_hashtags'], False, id_map)

    db.flush()

def apply_named_search(db: Session, data: Dict[str, Any], id_map: Dict[str, int]):
    client_id = data.get('id')
    obj = None
    if client_id and isinstance(client_id, int) and client_id > 0:
        obj = db.query(NamedSearch).filter(NamedSearch.id == client_id).first()
        if not obj: return
    else:
        obj = NamedSearch(name=data.get('name', 'Search'))
        db.add(obj)
        db.flush()
        if client_id and (isinstance(client_id, int) and client_id <= 0):
            id_map[str(client_id)] = obj.id

    if 'name' in data: obj.name = data['name']
    if 'uncompleted_only' in data: obj.uncompleted_only = data['uncompleted_only']

    # Hashtags
    if 'hashtag_ids' in data:
        new_ids = []
        for h_id in data['hashtag_ids']:
            rid = _resolve_id(h_id, id_map)
            if rid: new_ids.append(rid)
        if new_ids:
            tags = db.query(Hashtag).filter(Hashtag.id.in_(new_ids)).all()
            obj.hashtags = tags
            
    db.flush()

def apply_header(db: Session, data: Dict[str, Any], id_map: Dict[str, int]):
    client_id = data.get('id')
    obj = None
    if client_id and isinstance(client_id, int) and client_id > 0:
        obj = db.query(ListDatetimeHeader).filter(ListDatetimeHeader.id == client_id).first()
        if not obj: return
    else:
        list_id = _resolve_id(data.get('list_id'), id_map)
        if not list_id: return
        obj = ListDatetimeHeader(list_id=list_id)
        db.add(obj)
        db.flush()
        if client_id and (isinstance(client_id, int) and client_id <= 0):
            id_map[str(client_id)] = obj.id
            
    if 'created_at' in data: obj.created_at = parse_iso(data['created_at'])
    if 'list_id' in data:
        lid = _resolve_id(data['list_id'], id_map)
        if lid: obj.list_id = lid
    db.flush()

def apply_calendar_completion(db: Session, data: Dict[str, Any], id_map: Dict[str, int]):
    # Identified by (event_id, occurrence_at) purely? Or ID?
    # Merkle sync doesn't export ID. So we match by composite.
    event_id = _resolve_id(data.get('event_id'), id_map)
    if not event_id: return
    
    occ = parse_iso(data.get('occurrence_at'))
    if not occ: return
    
    # Check existence
    exists = db.query(CalendarEventCompletion).filter(
        CalendarEventCompletion.event_id == event_id,
        CalendarEventCompletion.occurrence_at == occ
    ).first()
    
    if not exists:
        obj = CalendarEventCompletion(event_id=event_id, occurrence_at=occ)
        db.add(obj)
        db.flush()

def apply_collation_association(db: Session, data: Dict[str, Any], id_map: Dict[str, int]):
    lid = _resolve_id(data.get('collation_list_id'), id_map)
    tid = _resolve_id(data.get('todo_id'), id_map)
    
    if not lid or not tid: return
    
    # Use raw SQL or model manipulation?
    # Models have relationships: Todo.collation_lists, List.collated_todos
    # But for a specific pair, it's easier to verify existence.
    
    todo = db.query(Todo).filter(Todo.id == tid).first()
    lst = db.query(ListModel).filter(ListModel.id == lid).first()
    
    if todo and lst:
        if lst not in todo.collation_lists:
            todo.collation_lists.append(lst)
            db.flush()

def apply_confirmed_item(db: Session, data: Dict[str, Any], id_map: Dict[str, int]):
    client_id = data.get('id')
    obj = None
    if client_id and isinstance(client_id, int) and client_id > 0:
        obj = db.query(DokuWikiConfirmedItem).filter(DokuWikiConfirmedItem.id == client_id).first()
        if not obj: return
    else:
        itype = data.get('item_type')
        if not itype: return
        iid = _resolve_id(data.get('item_id'), id_map) # Might be list or todo ID
        if not iid: return
        
        # Dedupe
        exists = db.query(DokuWikiConfirmedItem).filter(
            DokuWikiConfirmedItem.item_type == itype,
            DokuWikiConfirmedItem.item_id == iid
        ).first()
        if exists:
            obj = exists # update existing
        else:
            obj = DokuWikiConfirmedItem(item_type=itype, item_id=iid)
            db.add(obj)
            db.flush()
            if client_id and (isinstance(client_id, int) and client_id <= 0):
                id_map[str(client_id)] = obj.id

    if 'confirmed_at' in data: obj.confirmed_at = parse_iso(data['confirmed_at'])
    if 'click_burst' in data: obj.click_burst = data['click_burst']
    if 'last_click' in data: obj.last_click = parse_iso(data['last_click'])
    db.flush()

def causes_cycle(db: Session, node_id: int, proposed_parent_id: int) -> bool:
    """
    Check if moving node_id to proposed_parent_id would cause a cycle.
    Traverse up from proposed_parent_id. If we hit node_id, it's a cycle.
    """
    if node_id == proposed_parent_id:
        return True
        
    current_id = proposed_parent_id
    # Safety limit to prevent infinite loops if cycle already exists
    steps = 0
    while current_id and steps < 100:
        if current_id == node_id:
            return True
        
        parent = db.query(Folder.parent_id).filter(Folder.id == current_id).scalar()
        current_id = parent
        steps += 1
        
    return False

def causes_todo_cycle(db: Session, node_id: int, proposed_parent_id: int) -> bool:
    """
    Check if moving todo node_id to proposed_parent_id would cause a cycle.
    """
    if node_id == proposed_parent_id:
        # Trivial cycle
        return True
        
    current_id = proposed_parent_id
    steps = 0
    while current_id and steps < 100:
        if current_id == node_id:
            return True
        
        # Move up tree
        val = db.query(Todo.parent_id).filter(Todo.id == current_id).scalar()
        current_id = val
        steps += 1
        
    return False

def update_links(db: Session, parent_obj: Any, links_data: List[Dict]):
    # map current links by url
    current_links = {l.url: l for l in parent_obj.links}
    
    seen_urls = set()
    for item in links_data:
        url = item.get('url')
        if not url: continue
        title = item.get('title')
        seen_urls.add(url)
        
        if url in current_links:
            # Update title if changed
            if current_links[url].title != title:
                current_links[url].title = title
        else:
            # Create new
            new_link = Link(url=url, title=title)
            parent_obj.links.append(new_link)
            
    # Delete missing
    for url, link in current_links.items():
        if url not in seen_urls:
            db.delete(link)

def update_smart_folder_hashtags(db: Session, sf: SmartFolder, data_list: List[Dict], is_include: bool, id_map: Dict[str, int]):
    # is_include=True -> include_hashtags_assoc
    # is_include=False -> exclude_hashtags_assoc
    
    # Current assocs
    if is_include:
        current_assocs = {a.hashtag_id: a for a in sf.include_hashtags_assoc}
    else:
        current_assocs = {a.hashtag_id: a for a in sf.exclude_hashtags_assoc}
        
    seen_ids = set()
    
    for item in data_list:
        # Resolve hashtag ID:
        resolved_hid = _resolve_id(item.get('id'), id_map) 
        
        # Fallback: look up by name if enabled?
        # Note: hashtags should have been synced first.
        if not resolved_hid and item.get('name'):
             h = db.query(Hashtag).filter(Hashtag.name == item.get('name')).first()
             if h: resolved_hid = h.id
             
        if not resolved_hid: continue
        
        seen_ids.add(resolved_hid)
        
        include_sub = item.get('include_subtodos', False)
        
        if resolved_hid in current_assocs:
            # update
            assoc = current_assocs[resolved_hid]
            if assoc.include_subtodos != include_sub:
                assoc.include_subtodos = include_sub
        else:
            # create
            if is_include:
                assoc = SmartFolderIncludeHashtag(smart_folder_id=sf.id, hashtag_id=resolved_hid, include_subtodos=include_sub)
            else:
                assoc = SmartFolderExcludeHashtag(smart_folder_id=sf.id, hashtag_id=resolved_hid, include_subtodos=include_sub)
            db.add(assoc)
            
    # Delete missing
    for hid, assoc in current_assocs.items():
        if hid not in seen_ids:
            db.delete(assoc)

def apply_deletions(db: Session, deletions: Dict[str, List[Any]], id_map: Dict[str, int]):
    """
    Apply deletions. deletions is dict like {"todos": [id1, id2], ...}
    """
    # Order matters? Child first? Or Cascade?
    # SQLAlchemy cascades should handle it, but being explicit is safer for some models.
    
    # Todos
    if 'todos' in deletions:
        for raw_id in deletions['todos']:
            tid = _resolve_id(raw_id, id_map)
            if tid:
                obj = db.query(Todo).filter(Todo.id == tid).first()
                if obj:
                    db.delete(obj)
                    create_journal_entry_sync(db, "Deleted Todo (Sync)", f"Deleted via sync", "todo", tid)

    # Lists
    if 'lists' in deletions:
        for raw_id in deletions['lists']:
            lid = _resolve_id(raw_id, id_map)
            if lid:
                obj = db.query(ListModel).filter(ListModel.id == lid).first()
                if obj:
                    db.delete(obj)
                    create_journal_entry_sync(db, "Deleted List (Sync)", f"Deleted via sync", "list", lid)

    # Folders
    if 'folders' in deletions:
        for raw_id in deletions['folders']:
            fid = _resolve_id(raw_id, id_map)
            if fid:
                obj = db.query(Folder).filter(Folder.id == fid).first()
                if obj:
                    db.delete(obj)
                    create_journal_entry_sync(db, "Deleted Folder (Sync)", f"Deleted via sync", "folder", fid)
                    
    # Calendar Events
    if 'calendar_events' in deletions:
        for raw_id in deletions['calendar_events']:
            cid = _resolve_id(raw_id, id_map)
            if cid:
                obj = db.query(CalendarEvent).filter(CalendarEvent.id == cid).first()
                if obj:
                    db.delete(obj)
                    
    # Smart Folders
    if 'smart_folders' in deletions:
        for raw_id in deletions['smart_folders']:
            sid = _resolve_id(raw_id, id_map)
            if sid:
                obj = db.query(SmartFolder).filter(SmartFolder.id == sid).first()
                if obj:
                    db.delete(obj)

    # Named Searches
    if 'named_searches' in deletions:
        for raw_id in deletions['named_searches']:
            nid = _resolve_id(raw_id, id_map)
            if nid:
                obj = db.query(NamedSearch).filter(NamedSearch.id == nid).first()
                if obj:
                    db.delete(obj)
    
    db.flush()

    # Headers
    if 'headers' in deletions:
        for raw_id in deletions['headers']:
            hid = _resolve_id(raw_id, id_map)
            if hid:
                db.query(ListDatetimeHeader).filter(ListDatetimeHeader.id == hid).delete()

    # Confirmed Items
    if 'confirmed_items' in deletions:
        for raw_id in deletions['confirmed_items']:
            cid = _resolve_id(raw_id, id_map)
            if cid:
                db.query(DokuWikiConfirmedItem).filter(DokuWikiConfirmedItem.id == cid).delete()

    # Calendar Completions (Composite Key)
    if 'calendar_completions' in deletions:
        for item in deletions['calendar_completions']:
            if isinstance(item, dict):
                eid = _resolve_id(item.get('event_id'), id_map)
                occ = parse_iso(item.get('occurrence_at'))
                if eid and occ:
                    db.query(CalendarEventCompletion).filter(
                        CalendarEventCompletion.event_id == eid,
                        CalendarEventCompletion.occurrence_at == occ
                    ).delete()

    # Collation Associations (Composite Key)
    if 'collation_associations' in deletions:
        for item in deletions['collation_associations']:
            if isinstance(item, dict):
                lid = _resolve_id(item.get('collation_list_id'), id_map)
                tid = _resolve_id(item.get('todo_id'), id_map)
                if lid and tid:
                    # Delete from association table
                    # Using delete() with synchronization might be needed if objects in session
                    stmt = collation_todos.delete().where(
                        (collation_todos.c.collation_list_id == lid) &
                        (collation_todos.c.todo_id == tid)
                    )
                    db.execute(stmt)

    db.flush()


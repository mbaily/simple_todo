"""
Utility helper functions for the Simple Todo application.

These functions are pure utilities with no FastAPI route dependencies.
"""
import re
import html
import random
from calendar_service import normalize_calendar_rule, calendar_occurrences
from datetime import datetime
from datetime import date as date_type
from typing import Optional

from sqlalchemy.orm import Session

from models import (
    Hashtag,
    CalendarEvent,
    _as_utc_aware,
    ListDatetimeHeader,
    DokuWikiConfirmedItem,
    List,
    Todo,
    Note,
    format_local_datetime,
    utcnow,
    UserSettings,
    JournalEntry,
)
from sqlalchemy import or_
from datetime import timedelta

# Import config values with safe fallbacks
try:
    from config import ENABLE_DOKUWIKI_LINKS, DOKUWIKI_LINK_FSTRING, DOKUWIKI_BURST_CONFIRMATION
    from config import CALENDAR_MAX_OCCURRENCES
    from config import DOKUWIKI_ITEM_LINK_FSTRING
except ImportError:
    ENABLE_DOKUWIKI_LINKS = False
    DOKUWIKI_LINK_FSTRING = ""
    DOKUWIKI_BURST_CONFIRMATION = False
    CALENDAR_MAX_OCCURRENCES = 100
    DOKUWIKI_ITEM_LINK_FSTRING = ""

def get_wday_occurrence(dt: datetime) -> str:
    """
    Calculate the weekday occurrence string (e.g., 'fri', 'fri1', 'fri3').
    fri = 1st Friday
    fri1 = 2nd Friday
    ...
    """
    # Weekday name (mon, tue, wed...)
    wday_name = dt.strftime("%a").lower()
    
    # Calculate occurrence index (0-based)
    # day 1-7 -> index 0
    # day 8-14 -> index 1
    occurrence_index = (dt.day - 1) // 7
    
    if occurrence_index == 0:
        return wday_name
    else:
        return f"{wday_name}{occurrence_index}"


def generate_dokuwiki_link(dt: datetime | None) -> str | None:
    """Generate a DokuWiki link based on a datetime."""
    if not dt or not ENABLE_DOKUWIKI_LINKS:
        return None
    
    # Ensure we have a local datetime for correct day/month calculation
    # (Assuming dt is naive UTC as per models.py convention, convert to local)
    local_dt = _as_utc_aware(dt).astimezone()
    
    year = local_dt.year
    month_short = local_dt.strftime("%b").lower()  # jan, feb
    month_long = local_dt.strftime("%B").lower()  # january, february
    wday_occurrence = get_wday_occurrence(local_dt)
    
    try:
        return DOKUWIKI_LINK_FSTRING.format(
            year=year,
            month_short=month_short,
            month_long=month_long,
            wday_occurrence=wday_occurrence
        )
    except Exception as e:
        print(f"Error generating DokuWiki link: {e}")
        return None


def generate_dokuwiki_item_link(item_type: str, item_id: int) -> str | None:
    """Generate a DokuWiki page link for a specific item (todo or list).
    
    Uses DOKUWIKI_ITEM_LINK_FSTRING with {item_type} and {item_id} placeholders.
    """
    if not ENABLE_DOKUWIKI_LINKS or not DOKUWIKI_ITEM_LINK_FSTRING:
        return None
    
    try:
        return DOKUWIKI_ITEM_LINK_FSTRING.format(
            item_type=item_type,
            item_id=item_id
        )
    except Exception as e:
        print(f"Error generating DokuWiki item link: {e}")
        return None

def format_configurable_datetime(dt: datetime | None) -> str:
    """Format a datetime according to the configured DATETIME_FORMAT.
    
    Uses config.DATETIME_FORMAT, falling back to %Y-%m-%d %H:%M if not set.
    """
    if dt is None:
        return ""
        
    try:
        from config import DATETIME_FORMAT
    except ImportError:
        DATETIME_FORMAT = "%Y-%m-%d %H:%M"
        
    try:
        return dt.strftime(DATETIME_FORMAT)
    except Exception:
        return dt.strftime("%Y-%m-%d %H:%M")


def format_configurable_date(dt: datetime | None) -> str:
    """Format a date according to the configured DATE_FORMAT.
    
    Uses config.DATE_FORMAT, falling back to %Y-%m-%d if not set.
    """
    if dt is None:
        return ""
        
    try:
        from config import DATE_FORMAT
    except ImportError:
        DATE_FORMAT = "%Y-%m-%d"
        
    try:
        return dt.strftime(DATE_FORMAT)
    except Exception:
        return dt.strftime("%Y-%m-%d")


def random_hex_color() -> str:
    """Generate a random hex color code."""
    return "#" + "".join(random.choice("0123456789abcdef") for _ in range(6))


def _normalize_rrule(rrule_text: str) -> str:
    """Accept either 'RRULE:FREQ=...' or 'FREQ=...' and return the latter."""
    return normalize_calendar_rule(rrule_text)


def _event_occurrences(event: CalendarEvent, start: datetime, end: datetime) -> list[datetime]:
    """Generate occurrences between [start, end] inclusive.

    Stores datetimes as naive UTC-ish values in this app, so this function treats
    all datetimes as naive.
    """
    return calendar_occurrences(event, start, end, CALENDAR_MAX_OCCURRENCES)


def _midnight(dt: datetime | date_type | str | None) -> datetime | None:
    """Return the datetime at midnight (00:00:00) with naive timezone (tzinfo=None). Supports datetime, date, and ISO string."""
    if dt is None:
        return None
    if isinstance(dt, str):
        s = dt.strip()
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s)
        except Exception:
            return None
    if isinstance(dt, datetime):
        return dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    if isinstance(dt, date_type):
        return datetime(dt.year, dt.month, dt.day)
    return None


def _parse_date_or_datetime(value: str | datetime | date_type | None) -> datetime:
    """Parse either YYYY-MM-DD or an ISO datetime and normalize to midnight."""
    if value is None:
        raise ValueError("empty")
    if isinstance(value, datetime):
        return _midnight(value)
    if isinstance(value, date_type):
        return datetime(value.year, value.month, value.day)
    s = str(value).strip()
    if not s:
        raise ValueError("empty")
    # Date-only
    if "T" not in s and len(s) == 10:
        try:
            d = date_type.fromisoformat(s)
            return datetime(d.year, d.month, d.day)
        except ValueError:
            pass
    # datetime-local or ISO datetime
    return _midnight(datetime.fromisoformat(s))


def _parse_date_yyyy_mm_dd(s: str | None) -> datetime | None:
    """Parse an HTML date input (YYYY-MM-DD) into a naive datetime at 00:00."""
    if not s:
        return None
    s = str(s).strip()
    if not s:
        return None
    try:
        d = datetime.strptime(s, "%Y-%m-%d")
        # d is a datetime at midnight, naive.
        return d
    except Exception:
        return None


def get_next_uncompleted_occurrence(
    event: CalendarEvent, 
    completed_keys: set[tuple[int, datetime]], 
    from_date: datetime | None = None
) -> datetime | None:
    """Find the next uncompleted occurrence of an event on or after from_date.
    
    Args:
        event: The CalendarEvent object.
        completed_keys: A set of (event_id, occurrence_at) tuples that are completed.
        from_date: Finding start date. Defaults to midnight (now).
        
    Returns:
        The occurrence datetime if found, or None if no future uncompleted occurrences exist.
    """
    if from_date is None:
        from_date = _midnight(datetime.now())
    else:
        from_date = _midnight(from_date)
        
    # Optimistic approach: check occurrences for separate windows?
    # Actually, _event_occurrences limits to CALENDAR_MAX_OCCURRENCES in a range.
    # For a recurring event with NO end date, we just need to search forward.
    # We'll check a window of, say, 1 year or 100 occurrences.
    # If the event has truly ended, rrule.between will handle it.
    
    # Define a reasonable search window
    search_end = from_date + timedelta(days=365*2) # 2 years lookahead seems plenty
    
    # _event_occurrences uses rrule logic
    occs = _event_occurrences(event, from_date, search_end)
    
    for occ in occs:
        # Check completion
        # Note: occ is usually a datetime.
        # Ensure exact match on stored completion key (which is stored as naive midnight usually)
        if (event.id, occ) not in completed_keys:
            return occ
            
    return None


def parse_hashtags(text: str) -> list[str]:
    """Extract hashtags from user-provided hashtag text.

    Accepts tags with or without a leading '#'. Examples:
    - "#work #home" -> ["work", "home"]
    - "work home" -> ["work", "home"]
    - "work, home; #urgent" -> ["work", "home", "urgent"]

    Only word characters are kept (equivalent to regex \\w+).
    """
    if not text:
        return []

    parts = re.split(r"[\s,;]+", text.strip())
    tags: list[str] = []
    seen: set[str] = set()

    for part in parts:
        if not part:
            continue

        candidate = part.lstrip('#')
        match = re.match(r"^(\w+)", candidate)
        if not match:
            continue

        tag = match.group(1)
        if tag not in seen:
            tags.append(tag)
            seen.add(tag)

    return tags


def get_or_create_hashtag(db: Session, name: str) -> Hashtag:
    """Get existing hashtag or create new one."""
    hashtag = db.query(Hashtag).filter(Hashtag.name == name).first()
    if not hashtag:
        hashtag = Hashtag(name=name)
        db.add(hashtag)
        db.flush()
    return hashtag


def linkify_text(text: str) -> str:
    """Convert URLs in text to clickable links.
    
    Note: We don't escape the full text here because Jinja2 templates
    handle escaping. We only need to ensure URLs are safe in href attributes.
    """
    url_pattern = re.compile(r'(https?://\S+)')
    return url_pattern.sub(r'<a href="\1" target="_blank">\1</a>', text)


# =============================================================================
# HOME PAGE HELPER FUNCTIONS
# =============================================================================

def is_old_pinned(item, threshold: datetime) -> bool:
    """Check if a pinned item is older than the threshold.
    
    Args:
        item: A Todo or List object with an optional pinned_at attribute
        threshold: datetime threshold - items pinned before this are "old"
    
    Returns:
        True if the item was pinned before threshold or has no pinned_at
    """
    pinned_at = getattr(item, "pinned_at", None)
    # If we don't have a pinned_at (e.g., legacy pinned items), treat as old.
    return pinned_at is None or pinned_at < threshold


def separate_pinned_items(items: list, threshold: datetime) -> tuple[list, list]:
    """Separate pinned items into old and recent lists.
    
    Args:
        items: List of pinned Todo or List objects
        threshold: datetime threshold for old/recent separation
    
    Returns:
        Tuple of (old_items, recent_items)
    """
    old = [item for item in items if is_old_pinned(item, threshold)]
    recent = [item for item in items if not is_old_pinned(item, threshold)]
    # Sort recent by pinned_at desc (fallback to modified_at)
    recent.sort(key=lambda x: (getattr(x, 'pinned_at', None) or getattr(x, 'modified_at', None) or datetime.min), reverse=True)
    return old, recent


def get_home_calendar_occurrences(db: Session, events: list, around_days: int) -> list[dict]:
    """Get calendar occurrences for the home page.
    
    Args:
        db: Database session
        events: List of CalendarEvent objects
        around_days: Number of days before/after today to include
    
    Returns:
        List of dicts with 'event', 'occurrence_at', and 'completed' keys
    """
    from models import CalendarEventCompletion
    
    now = _midnight(datetime.now())
    from datetime import timedelta
    start = now - timedelta(days=around_days)
    end = now + timedelta(days=around_days + 1) - timedelta(microseconds=1)
    
    completion_rows = db.query(CalendarEventCompletion).all()
    completed_set = {(c.event_id, c.occurrence_at) for c in completion_rows}
    
    today = now.date()
    occurrences: list[dict] = []
    for ev in events:
        for occ_at in _event_occurrences(ev, start, end):
            is_completed = (ev.id, occ_at) in completed_set
            is_late = (occ_at.date() < today) and not is_completed
            is_important = is_late and bool(ev.important_if_late)
            occurrences.append({
                "event": ev,
                "occurrence_at": occ_at,
                "completed": is_completed,
                "is_late": is_late,
                "is_important": is_important,
            })
    occurrences.sort(key=lambda x: x["occurrence_at"])
    return occurrences


def get_active_alarms(db: Session) -> list:
    """Get todos with currently active alarms.
    
    Returns todos where the current time is within the alarm window.
    """
    from models import Todo
    
    now = datetime.now()
    alarm_todos = db.query(Todo).filter(
        Todo.completed == False, 
        Todo.alarm_start != None,
        Todo.alarm_end != None
    ).all()
    
    active = []
    for t in alarm_todos:
        if t.alarm_start <= now <= t.alarm_end:
            active.append(t)
    return active


def get_resuming_items(db: Session) -> tuple[list, list]:
    """Get delayed items that are now resuming (delayed_until <= now).
    
    Returns:
        Tuple of (resuming_todos, resuming_lists)
    """
    from models import Todo, List
    
    now = datetime.now()
    
    resuming_todos = (
        db.query(Todo)
        .filter(Todo.delayed_until != None, Todo.delayed_until <= now, Todo.completed == False)
        .order_by(Todo.delayed_until.asc())
        .all()
    )
    
    resuming_lists = (
        db.query(List)
        .filter(List.delayed_until != None, List.delayed_until <= now, List.completed == False, List.archived == False)
        .order_by(List.delayed_until.asc())
        .all()
    )
    
    return resuming_todos, resuming_lists


def get_uncompleted_counts(db: Session, include_collected: bool = False) -> dict[int, int]:
    """Get uncompleted todo counts per list.
    
    Args:
        db: Database session
        include_collected: If True, include collected todos in collation list counts
    
    Returns:
        Dict mapping list_id to uncompleted count
    """
    from sqlalchemy import func
    from models import Todo, List, collation_todos
    
    counts: dict[int, int] = dict(
        db.query(Todo.list_id, func.count(Todo.id))
        .filter(Todo.completed == False)
        .group_by(Todo.list_id)
        .all()
    )
    
    if include_collected:
        collected_counts = dict(
            db.query(collation_todos.c.collation_list_id, func.count(func.distinct(collation_todos.c.todo_id)))
            .join(List, List.id == collation_todos.c.collation_list_id)
            .join(Todo, Todo.id == collation_todos.c.todo_id)
            .filter(List.is_collation == True, Todo.completed == False)
            .group_by(collation_todos.c.collation_list_id)
            .all()
        )
        for list_id, n in collected_counts.items():
            counts[int(list_id)] = int(counts.get(int(list_id), 0)) + int(n)
    
    return counts


# =============================================================================
# LIST NAVIGATION HELPERS
# =============================================================================

def get_list_navigation(db: Session, list_id: int, settings, folder_id, sort: str = "user") -> dict:
    """Calculate prev/next/first list IDs for navigation.
    
    Args:
        db: Database session
        list_id: Current list ID
        settings: UserSettings object
        folder_id: Optional folder ID from query param
        sort: Sort order ('created' or 'modified')
    
    Returns:
        Dict with keys: first_id, prev_id, next_id, current_index, total_count
    """
    from models import List, Hashtag
    
    # Determine effective folder for filtering/navigation context
    effective_folder_id = None
    if folder_id is not None:
        effective_folder_id = folder_id if folder_id > 0 else None
    elif settings.active_folder_id:
        effective_folder_id = settings.active_folder_id

    # Build query matching home page ordering
    query = db.query(List).filter(List.archived == False)

    # Folder Filter
    if effective_folder_id:
        query = query.filter(List.folder_id == effective_folder_id)
    else:
        query = query.filter(List.folder_id == None)

    # Only include uncategorized lists in navigation
    query = query.filter(or_(List.category == None, List.category == ""))

    # Hashtag Filters (Only if using global settings context, NOT explicit folder view)
    if folder_id is None:
        inc_tags = parse_hashtags(settings.filter_inc_hashtags)
        if inc_tags:
            query = query.join(List.hashtags).filter(Hashtag.name.in_(inc_tags))

        exc_tags = parse_hashtags(settings.filter_exc_hashtags)
        if exc_tags:
            query = query.filter(~List.hashtags.any(Hashtag.name.in_(exc_tags)))

    if sort == "modified":
        nav_lists = query.order_by(List.modified_at.desc()).all()
    elif sort == "created":
        nav_lists = query.order_by(List.created_at.desc()).all()
    else:
        nav_lists = query.order_by(List.sort_order.asc(), List.created_at.desc()).all()

    nav_ids = [l.id for l in nav_lists]
    result = {
        "first_id": nav_ids[0] if nav_ids else None,
        "prev_id": None,
        "next_id": None,
        "current_index": None,
        "total_count": len(nav_ids)
    }
    
    try:
        idx = nav_ids.index(list_id)
        result["current_index"] = idx
        if idx > 0:
            result["prev_id"] = nav_ids[idx - 1]
        if idx < len(nav_ids) - 1:
            result["next_id"] = nav_ids[idx + 1]
    except ValueError:
        # Current list might be archived or filtered out
        pass
    
    return result


# =============================================================================
# SMART FOLDER HELPERS
# =============================================================================

def validate_sort_field(field: str | None, allowed: tuple = ('modified_at', 'created_at'), default: str = 'modified_at') -> str:
    """Validate and return a sort field name.
    
    Args:
        field: The field name to validate
        allowed: Tuple of allowed field names
        default: Default field if invalid
    
    Returns:
        Validated field name
    """
    field = (field or default).strip()
    return field if field in allowed else default


def apply_dokuwiki_links(items: list, todos: list, date_attr: str, confirmed_set: set, item_type: str = 'list'):
    """Apply DokuWiki links to a list of items.
    
    Args:
        items: List of List or Todo objects
        todos: Optional list of todos (for recursive application)
        date_attr: Attribute name for date ('created_at' or 'modified_at')
        confirmed_set: Set of confirmed item IDs
        item_type: 'list' or 'todo'
    """
    for item in items:
        dt = getattr(item, date_attr, None)
        item.dokuwiki_link = generate_dokuwiki_link(dt)
        item.dokuwiki_confirmed = (item.id in confirmed_set)


def apply_dokuwiki_links_recursive(todo, date_attr: str, confirmed_set: set):
    """Recursively apply DokuWiki links to a todo and its children.
    
    Args:
        todo: Todo object
        date_attr: Attribute name for date
        confirmed_set: Set of confirmed todo IDs
    """
    dt = getattr(todo, date_attr, None)
    todo.dokuwiki_link = generate_dokuwiki_link(dt)
    todo.dokuwiki_confirmed = (todo.id in confirmed_set)
    for child in todo.children:
        apply_dokuwiki_links_recursive(child, date_attr, confirmed_set)


def get_confirmed_items(db: Session) -> tuple[set, set]:
    """Get sets of confirmed list and todo IDs.
    
    Returns:
        Tuple of (confirmed_list_ids, confirmed_todo_ids)
    """
    from models import DokuWikiConfirmedItem
    
    confirmed_lists = set()
    confirmed_todos = set()
    
    all_confirmed = db.query(DokuWikiConfirmedItem).all()
    for item in all_confirmed:
        if item.item_type == 'list':
            confirmed_lists.add(item.item_id)
        elif item.item_type == 'todo':
            confirmed_todos.add(item.item_id)
    
    return confirmed_lists, confirmed_todos


def parse_smart_folder_form(form_data) -> dict:
    """Parse common smart folder form fields.
    
    Args:
        form_data: FastAPI FormData object
        
    Returns:
        Dict of cleaned values suitable for SmartFolder model
    """
    from datetime import datetime
    
    modified_after_str = (form_data.get('modified_after') or '').strip()
    modified_before_str = (form_data.get('modified_before') or '').strip()
    
    modified_after = datetime.strptime(modified_after_str, '%Y-%m-%d') if modified_after_str else None
    modified_before = datetime.strptime(modified_before_str, '%Y-%m-%d') + timedelta(days=1) if modified_before_str else None

    # Sort fields
    list_sort_field = validate_sort_field(form_data.get('list_sort_field'))
    todo_sort_field = validate_sort_field(form_data.get('todo_sort_field'))
    list_sort_dir = (form_data.get('list_sort_dir') or 'desc').strip().lower()
    todo_sort_dir = (form_data.get('todo_sort_dir') or 'desc').strip().lower()

    return {
        "name": (form_data.get('name') or '').strip(),
        "show_lists": str(form_data.get('show_lists') or '').strip().lower() in ("1", "true", "yes", "on"),
        "show_todos": str(form_data.get('show_todos') or '').strip().lower() in ("1", "true", "yes", "on"),
        "group_todos_by_list": str(form_data.get('group_todos_by_list') or '').strip().lower() in ("1", "true", "yes", "on"),
        "root_todos_only": str(form_data.get('root_todos_only') or '').strip().lower() in ("1", "true", "yes", "on"),
        "nest_subtodos": str(form_data.get('nest_subtodos') or '').strip().lower() in ("1", "true", "yes", "on"),
        "strict_nesting": str(form_data.get('strict_nesting') or '').strip().lower() in ("1", "true", "yes", "on"),
        "uncompleted_only": str(form_data.get('uncompleted_only') or '').strip().lower() in ("1", "true", "yes", "on"),
        "include_archived_lists": str(form_data.get('include_archived_lists') or '').strip().lower() in ("1", "true", "yes", "on"),
        "dokuwiki_links_enabled": str(form_data.get('dokuwiki_links_enabled') or '').strip().lower() in ("1", "true", "yes", "on"),
        "dokuwiki_date_field": (form_data.get('dokuwiki_date_field') or 'modified_at').strip(),
        "modified_after": modified_after,
        "modified_before": modified_before,
        "list_sort_field": list_sort_field,
        "todo_sort_field": todo_sort_field,
        "list_sort_desc": list_sort_dir != 'asc',
        "todo_sort_desc": todo_sort_dir != 'asc',
        "section_order": (form_data.get('section_order') or 'LET').strip().upper(),
    }


def update_smart_folder_hashtags(db: Session, folder, form_data, include_ids: list[int], exclude_ids: list[int]):
    """Update smart folder hashtag associations.
    
    Args:
        db: Database session
        folder: SmartFolder object
        form_data: FormData (for reading include_subtodos variants)
        include_ids: List of hashtag IDs to include
        exclude_ids: List of hashtag IDs to exclude
    """
    from models import SmartFolderIncludeHashtag, SmartFolderExcludeHashtag
    
    # Clear existing associations
    folder.include_hashtags_assoc = []
    folder.exclude_hashtags_assoc = []
    db.flush()

    for hid in sorted(include_ids):
        inc_sub = str(form_data.get(f'include_subtodos_{hid}') or '').strip().lower() in ("1", "true", "yes", "on")
        inc_list_items = str(form_data.get(f'include_list_items_{hid}') or '').strip().lower() in ("1", "true", "yes", "on")
        assoc = SmartFolderIncludeHashtag(
            smart_folder_id=folder.id, 
            hashtag_id=hid, 
            include_subtodos=inc_sub,
            include_list_items=inc_list_items
        )
        db.add(assoc)

    for hid in sorted(exclude_ids):
        exc_sub = str(form_data.get(f'exclude_subtodos_{hid}') or '').strip().lower() in ("1", "true", "yes", "on")
        assoc = SmartFolderExcludeHashtag(smart_folder_id=folder.id, hashtag_id=hid, include_subtodos=exc_sub)
        db.add(assoc)


def update_smart_folder_collections(db: Session, folder, list_ids: list[int], include_folder_ids: list[int], exclude_folder_ids: list[int]):
    """Update smart folder list/folder collections.
    
    Args:
        db: Database session
        folder: SmartFolder object
        list_ids: List of List IDs to include
        include_folder_ids: List of Folder IDs to include
        exclude_folder_ids: List of Folder IDs to exclude
    """
    from models import List, Folder
    
    folder.lists = []
    folder.include_folders = []
    folder.exclude_folders = []
    
    for lid in sorted(list_ids):
        lst = db.query(List).filter(List.id == lid).first()
        if lst:
            folder.lists.append(lst)
            
    for fid in sorted(include_folder_ids):
        f = db.query(Folder).filter(Folder.id == fid).first()
        if f:
            folder.include_folders.append(f)
            
    for fid in sorted(exclude_folder_ids):
        f = db.query(Folder).filter(Folder.id == fid).first()
        if f:
            folder.exclude_folders.append(f)


def get_list_items_with_dividers(
    db: Session,
    todos: list[Todo],
    notes: list[Note],
    datetime_headers: list[ListDatetimeHeader],
    todo_list: List,
    reverse_sort: bool
) -> list[dict]:
    """
    Process list items (todos, notes) and datetime headers to create
    a flat list of items for the view_list template, handling
    explicit and synthetic dividers.
    """
    items: list[dict] = []


    
    if todo_list.partition_by_datetime_headers and not todo_list.manual_sorting:
        combined = []
        for t in todos:
            combined.append({"type": "todo", "obj": t, "created_at": t.created_at})
        for h in datetime_headers:
            combined.append({"type": "divider", "obj": h, "created_at": h.created_at})
        for n in notes:
            combined.append({"type": "note", "obj": n, "created_at": n.created_at})
        
        def sort_key(item):
            dt = item["created_at"] or datetime.min
            # Tie-breaker: We always want Headers to be processed BEFORE Todos if timestamps are equal.
            # This ensures the Header is seen as the "start" of the section containing the Todo.
            is_header = (item["type"] == "divider")
            if reverse_sort:
                # Descending (Newest First): Header needs to be "larger" to come first.
                rank = 1 if is_header else 0
            else:
                # Ascending (Oldest First): Header needs to be "smaller" to come first.
                rank = 0 if is_header else 1
            return (dt, rank)

        combined.sort(key=sort_key, reverse=reverse_sort)
        
        current_section_items = []
        current_section_divider = None 
        
        # Pre-fetch confirmed items for this list's todos
        confirmed_todo_ids = set()
        all_todo_ids = [t.id for t in todos]
        if all_todo_ids:
            confirmed_rows = db.query(DokuWikiConfirmedItem.item_id).filter(
                DokuWikiConfirmedItem.item_type == 'todo',
                DokuWikiConfirmedItem.item_id.in_(all_todo_ids)
            ).all()
            confirmed_todo_ids = {r[0] for r in confirmed_rows}

        for item in combined:
            if item["type"] in ("todo", "note"):
                current_section_items.append(item)
            elif item["type"] == "divider":
                if current_section_items:
                    oldest_dt = min((it["created_at"] or datetime.min) for it in current_section_items)
                    heading = "Beginning " + format_local_datetime(oldest_dt)
                    dw_link = generate_dokuwiki_link(oldest_dt)
                    
                    section_todos = [it["obj"] for it in current_section_items if it["type"] == "todo"]
                    is_confirmed = any(t.id in confirmed_todo_ids for t in section_todos)
                    todo_ids = [t.id for t in section_todos]
                    
                    if current_section_divider:
                        items.append({
                            "type": "datetime_header",
                            "obj": current_section_divider,
                            "heading": heading,
                            "created_at": current_section_divider.created_at,
                            "dokuwiki_link": dw_link,
                            "dokuwiki_confirmed": is_confirmed,
                            "todo_ids": todo_ids
                        })
                    else:
                        items.append({
                            "type": "synthetic_datetime_header",
                            "heading": heading,
                            "created_at": oldest_dt,
                            "dokuwiki_link": dw_link,
                            "dokuwiki_confirmed": is_confirmed,
                            "todo_ids": todo_ids
                        })
                    
                    items.extend(current_section_items)
                    
                current_section_items = []
                current_section_divider = item["obj"]
                
        if current_section_items:
            oldest_dt = min((it["created_at"] or datetime.min) for it in current_section_items)
            heading = "Beginning " + format_local_datetime(oldest_dt)
            dw_link = generate_dokuwiki_link(oldest_dt)
            
            section_todos = [it["obj"] for it in current_section_items if it["type"] == "todo"]
            is_confirmed = any(t.id in confirmed_todo_ids for t in section_todos)
            todo_ids = [t.id for t in section_todos]
            
            if current_section_divider:
                 items.append({
                    "type": "datetime_header",
                    "obj": current_section_divider,
                    "heading": heading,
                    "created_at": current_section_divider.created_at,
                    "dokuwiki_link": dw_link,
                    "dokuwiki_confirmed": is_confirmed,
                    "todo_ids": todo_ids
                })
            else:
                items.append({
                    "type": "synthetic_datetime_header",
                    "heading": heading,
                    "created_at": oldest_dt,
                    "dokuwiki_link": dw_link,
                    "dokuwiki_confirmed": is_confirmed,
                    "todo_ids": todo_ids
                })
                
            items.extend(current_section_items)
    else:
        # Old behavior: Headers are just items in the list.
        for t in todos:
            items.append({"type": "todo", "created_at": t.created_at, "obj": t})
        for n in notes:
            items.append({"type": "note", "created_at": n.created_at, "obj": n})
        for h in datetime_headers:
            items.append({
                "type": "datetime_header", 
                "created_at": h.created_at, 
                "obj": h, 
                "heading": h.created_at_heading,
                "dokuwiki_link": generate_dokuwiki_link(h.created_at)
            })

        if (todos or notes) and datetime_headers:
            oldest_todo_dt = min((t.created_at for t in todos if t.created_at), default=None)
            oldest_note_dt = min((n.created_at for n in notes if n.created_at), default=None)
            
            dts = [d for d in [oldest_todo_dt, oldest_note_dt] if d]
            oldest_item_dt = min(dts, default=None)

            oldest_header_dt = min((h.created_at for h in datetime_headers if h.created_at), default=None)
            if oldest_item_dt and oldest_header_dt and oldest_item_dt < oldest_header_dt:
                synthetic_dt = oldest_item_dt - timedelta(minutes=1)
                items.append(
                    {
                        "type": "synthetic_datetime_header",
                        "created_at": synthetic_dt,
                        "heading": format_local_datetime(synthetic_dt),
                        "dokuwiki_link": generate_dokuwiki_link(synthetic_dt)
                    }
                )
        if not todo_list.manual_sorting:
            items.sort(key=lambda x: (x["created_at"] or datetime.min), reverse=reverse_sort)

    return items


def process_dokuwiki_click(db: Session, item_type: str, item_id: Optional[int], todo_ids: list[int] = None) -> dict:
    """
    Process a click on a DokuWiki link, handling burst logic and header group confirmation.
    
    Returns:
        Dict with keys: 'success' (bool), 'confirmed' (bool), 'burst' (int), 'error' (str, optional)
    """
    if not item_type:
        return {"error": "Missing type"}
        
    todo_ids = todo_ids or []

    # Helper to handle burst logic for confirmed items
    def handle_burst(itype, iid):
        # Find or create the confirmed item record
        record = db.query(DokuWikiConfirmedItem).filter(
            DokuWikiConfirmedItem.item_type == itype,
            DokuWikiConfirmedItem.item_id == iid
        ).first()
        
        if not record:
            record = DokuWikiConfirmedItem(
                item_type=itype,
                item_id=iid,
                click_burst=0,
                confirmed_at=None # Not confirmed yet
            )
            db.add(record)
            db.flush()
            
        BURST_WINDOW = timedelta(minutes=10)
        MIN_SEPARATION = timedelta(seconds=10)
        BURST_THRESHOLD = 2
        
        now = utcnow()
        
        if not record.confirmed_at:
            if not DOKUWIKI_BURST_CONFIRMATION:
                # Immediate confirmation
                record.confirmed_at = now
                record.click_burst = 1
            else:
                # Burst logic
                if record.last_click:
                    last_click = record.last_click
                    time_since = now - last_click
                    
                    if time_since < BURST_WINDOW:
                        if time_since > MIN_SEPARATION:
                            record.click_burst = (record.click_burst or 0) + 1
                    else:
                        record.click_burst = 1
                else:
                    record.click_burst = 1
                    
                if (record.click_burst or 0) >= BURST_THRESHOLD:
                    record.confirmed_at = now
                
        record.last_click = now
        return record

    if item_type == 'header_group':
        if not todo_ids:
             return {"error": "No todos in group"}
        
        anchor_id = todo_ids[0]
        record = handle_burst('todo', anchor_id)
        
        if record.confirmed_at:
            # Confirm all other todos in the group
            for tid in todo_ids:
                if tid == anchor_id: continue
                
                other_record = db.query(DokuWikiConfirmedItem).filter(
                    DokuWikiConfirmedItem.item_type == 'todo',
                    DokuWikiConfirmedItem.item_id == tid
                ).first()
                
                if not other_record:
                    other_record = DokuWikiConfirmedItem(
                        item_type='todo',
                        item_id=tid,
                        click_burst=0,
                        confirmed_at=record.confirmed_at,
                        last_click=record.last_click
                    )
                    db.add(other_record)
                elif not other_record.confirmed_at:
                    other_record.confirmed_at = record.confirmed_at
        
        db.commit()
        return {
            "success": True, 
            "confirmed": bool(record.confirmed_at),
            "burst": record.click_burst
        }

    elif item_type in ('todo', 'list'):
        if item_id is None:
             return {"error": "Missing id"}
        record = handle_burst(item_type, int(item_id))
        db.commit()
        return {
            "success": True, 
            "confirmed": bool(record.confirmed_at),
            "burst": record.click_burst
        }
        
    elif item_type in ('datetime_header', 'synthetic_datetime_header'):
        return {"error": "Use header_group type"}
    
    return {"error": "Invalid type"}


def get_user_settings(db: Session) -> UserSettings:
    """Get the persistent user settings singleton, creating if needed."""
    settings = db.query(UserSettings).filter(UserSettings.id == 1).first()
    if not settings:
        settings = UserSettings(id=1, show_completed=True, show_delayed=True)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def create_journal_entry(db: Session, action: str, details: str = None, entity_type: str = None, entity_id: int = None):
    """Creates a journal entry for a write interaction."""
    entry = JournalEntry(
        action=action,
        details=details,
        entity_type=entity_type,
        entity_id=entity_id
    )
    db.add(entry)

import json
from typing import Optional
from datetime import datetime, timezone, date
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Table, Float, CheckConstraint, Index, UniqueConstraint, JSON, event
from sqlalchemy.orm import relationship, declarative_base, backref

Base = declarative_base()


@event.listens_for(Base, "init", propagate=True)
def _init_metadata_json(target, args, kwargs):
    if hasattr(target, "metadata_json") and kwargs.get("metadata_json") is None:
        kwargs["metadata_json"] = {}


def utcnow() -> datetime:
    """Return a naive datetime representing current UTC time.

    Python 3.13 deprecates datetime.utcnow(); this keeps the previous storage
    behavior (naive UTC) while avoiding the deprecated API.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_utc_aware(dt: datetime | date) -> datetime:
    """Treat a naive datetime or date from the DB as UTC and return an aware datetime."""
    if not isinstance(dt, datetime) and isinstance(dt, date):
        dt = datetime(dt.year, dt.month, dt.day)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def format_calendar_date(dt: datetime | date | None) -> str:
    """Format a calendar date (naive date or midnight datetime) without timezone shift.
    
    Uses config.DATE_FORMAT.
    """
    if not dt:
        return ''
    try:
        from config import DATE_FORMAT
    except ImportError:
        DATE_FORMAT = "%Y-%m-%d"
        
    try:
        return dt.strftime(DATE_FORMAT)
    except Exception:
        return dt.strftime("%Y-%m-%d")


def format_local_date(dt: datetime | None) -> str:
    """Format a stored UTC timestamp as a local date string.
    
    Uses config.DATE_FORMAT.
    """
    if not dt:
        return ''
    local = _as_utc_aware(dt).astimezone()
    
    try:
        from config import DATE_FORMAT
    except ImportError:
        DATE_FORMAT = "%Y-%m-%d"
        
    try:
        return local.strftime(DATE_FORMAT)
    except Exception:
        return local.strftime("%Y-%m-%d")


def format_local_datetime(dt: datetime | None) -> str:
    """Format a stored UTC timestamp as a local date+time string.
    
    Uses config.DATETIME_FORMAT.
    """
    if not dt:
        return ''
    local = _as_utc_aware(dt).astimezone()
    
    try:
        from config import DATETIME_FORMAT
    except ImportError:
        DATETIME_FORMAT = "%Y-%m-%d %H:%M"

    try:
        return local.strftime(DATETIME_FORMAT)
    except Exception:
        return local.strftime("%Y-%m-%d %H:%M")

# Association tables for many-to-many relationships
list_hashtags = Table(
    'list_hashtags',
    Base.metadata,
    Column('list_id', Integer, ForeignKey('lists.id'), primary_key=True),
    Column('hashtag_id', Integer, ForeignKey('hashtags.id'), primary_key=True)
)

todo_hashtags = Table(
    'todo_hashtags',
    Base.metadata,
    Column('todo_id', Integer, ForeignKey('todos.id'), primary_key=True),
    Column('hashtag_id', Integer, ForeignKey('hashtags.id'), primary_key=True)
)

calendar_event_hashtags = Table(
    'calendar_event_hashtags',
    Base.metadata,
    Column('calendar_event_id', Integer, ForeignKey('calendar_events.id'), primary_key=True),
    Column('hashtag_id', Integer, ForeignKey('hashtags.id'), primary_key=True)
)

named_search_hashtags = Table(
    'named_search_hashtags',
    Base.metadata,
    Column('named_search_id', Integer, ForeignKey('named_searches.id'), primary_key=True),
    Column('hashtag_id', Integer, ForeignKey('hashtags.id'), primary_key=True)
)

# Hashtags that a named search should EXCLUDE from results.
named_search_excluded_hashtags = Table(
    'named_search_excluded_hashtags',
    Base.metadata,
    Column('named_search_id', Integer, ForeignKey('named_searches.id'), primary_key=True),
    Column('hashtag_id', Integer, ForeignKey('hashtags.id'), primary_key=True)
)


# Smart folders: saved queries / views.
class SmartFolderIncludeHashtag(Base):
    __tablename__ = 'smart_folder_include_hashtags'
    smart_folder_id = Column(Integer, ForeignKey('smart_folders.id'), primary_key=True)
    hashtag_id = Column(Integer, ForeignKey('hashtags.id'), primary_key=True)
    include_subtodos = Column(Boolean, default=False)
    include_list_items = Column(Boolean, default=False)
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")

    smart_folder = relationship("SmartFolder", back_populates="include_hashtags_assoc")
    hashtag = relationship("Hashtag")

class SmartFolderExcludeHashtag(Base):
    __tablename__ = 'smart_folder_exclude_hashtags'
    smart_folder_id = Column(Integer, ForeignKey('smart_folders.id'), primary_key=True)
    hashtag_id = Column(Integer, ForeignKey('hashtags.id'), primary_key=True)
    include_subtodos = Column(Boolean, default=False)
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")

    smart_folder = relationship("SmartFolder", back_populates="exclude_hashtags_assoc")
    hashtag = relationship("Hashtag")

smart_folder_lists = Table(
    'smart_folder_lists',
    Base.metadata,
    Column('smart_folder_id', Integer, ForeignKey('smart_folders.id'), primary_key=True),
    Column('list_id', Integer, ForeignKey('lists.id'), primary_key=True)
)


smart_folder_include_folders = Table(
    'smart_folder_include_folders',
    Base.metadata,
    Column('smart_folder_id', Integer, ForeignKey('smart_folders.id'), primary_key=True),
    Column('folder_id', Integer, ForeignKey('folders.id'), primary_key=True)
)

smart_folder_exclude_folders = Table(
    'smart_folder_exclude_folders',
    Base.metadata,
    Column('smart_folder_id', Integer, ForeignKey('smart_folders.id'), primary_key=True),
    Column('folder_id', Integer, ForeignKey('folders.id'), primary_key=True)
)


# Many-to-many relationship: which todos are "collated" into which collation lists.
# sort_order allows manual positioning of collated todos within the collation list.
collation_todos = Table(
    'collation_todos',
    Base.metadata,
    Column('collation_list_id', Integer, ForeignKey('lists.id'), primary_key=True),
    Column('todo_id', Integer, ForeignKey('todos.id'), primary_key=True),
    Column('sort_order', Integer, default=0)
)


class List(Base):
    __tablename__ = 'lists'

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    completed = Column(Boolean, default=False)
    pinned = Column(Boolean, default=False)
    pinned_at = Column(DateTime, nullable=True)
    is_collation = Column(Boolean, default=False)
    collation_color = Column(String(7), nullable=True)  # e.g. "#aabbcc"
    archived = Column(Boolean, default=False)
    show_completed = Column(Boolean, default=True)
    sort_ascending = Column(Boolean, default=False)
    partition_by_datetime_headers = Column(Boolean, default=True)
    intersperse_collated_todos = Column(Boolean, default=False)
    manual_sorting = Column(Boolean, default=False)
    is_recipe = Column(Boolean, default=False)  # Recipe mode for ingredient tracking
    show_delayed = Column(Boolean, default=True)
    show_dokuwiki_item_links = Column(Boolean, default=False)  # Toggle visibility of per-item DokuWiki links
    dokuwiki_page_enabled = Column(Boolean, default=False)  # Whether this list has a DokuWiki page link
    category = Column(String(255), nullable=True)  # Optional category for grouping lists
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=utcnow)
    modified_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    delayed_until = Column(DateTime, nullable=True)
    folder_id = Column(Integer, ForeignKey('folders.id'), nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")

    # DokuWiki click tracking
    # dokuwiki_confirmed = Column(Boolean, default=False)
    # dokuwiki_click_burst = Column(Integer, default=0)
    # dokuwiki_last_click = Column(DateTime, nullable=True)

    folder = relationship("Folder", back_populates="lists")
    todos = relationship("Todo", back_populates="list", cascade="all, delete-orphan")
    notes = relationship("Note", back_populates="list", cascade="all, delete-orphan")
    links = relationship("Link", back_populates="list", cascade="all, delete-orphan")
    datetime_headers = relationship(
        "ListDatetimeHeader",
        back_populates="list",
        cascade="all, delete-orphan",
    )
    hashtags = relationship("Hashtag", secondary=list_hashtags, back_populates="lists")

    smart_folders = relationship(
        "SmartFolder",
        secondary=smart_folder_lists,
        back_populates="lists",
    )

    # Todos from other (non-collation) lists that are collected into this collation list.
    collated_todos = relationship("Todo", secondary=collation_todos, back_populates="collation_lists")

    @property
    def created_at_formatted(self):
        return format_local_date(self.created_at)

    @property
    def modified_at_formatted(self):
        return format_local_date(self.modified_at)

    @property
    def full_path_name(self) -> str:
        if self.folder:
            return f"{self.folder.path_name} > {self.name}"
        return self.name


class Todo(Base):
    __tablename__ = 'todos'

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    note = Column(Text, nullable=True)
    color = Column(String(7), nullable=True)  # e.g. "#ff0000"
    completed = Column(Boolean, default=False)
    pinned = Column(Boolean, default=False)
    pinned_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    modified_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    delayed_until = Column(DateTime, nullable=True)
    list_id = Column(Integer, ForeignKey('lists.id'), nullable=False)
    parent_id = Column(Integer, ForeignKey('todos.id'), nullable=True)
    sort_order = Column(Integer, default=0)

    alarm_start = Column(DateTime, nullable=True)
    alarm_end = Column(DateTime, nullable=True)
    dokuwiki_page_enabled = Column(Boolean, default=False)  # Whether this todo has a DokuWiki page link

    # Ingredient grouping for recipe mode - links todos across recipes as the same ingredient
    ingredient_group_id = Column(Integer, ForeignKey('ingredient_groups.id'), nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")

    # DokuWiki click tracking (for the Todo itself)
    # dokuwiki_confirmed = Column(Boolean, default=False)
    # dokuwiki_click_burst = Column(Integer, default=0)
    # dokuwiki_last_click = Column(DateTime, nullable=True)

    # DokuWiki click tracking (for the Header this todo belongs to)
    # dokuwiki_header_confirmed = Column(Boolean, default=False)
    # dokuwiki_header_click_burst = Column(Integer, default=0)
    # dokuwiki_header_last_click = Column(DateTime, nullable=True)

    list = relationship("List", back_populates="todos")
    links = relationship("Link", back_populates="todo", cascade="all, delete-orphan")
    hashtags = relationship("Hashtag", secondary=todo_hashtags, back_populates="todos")
    collation_lists = relationship("List", secondary=collation_todos, back_populates="collated_todos")
    parent = relationship("Todo", back_populates="children", remote_side=[id])
    # Deleting a task deletes its subtasks, but detaching a subtask (moving it out
    # from under its parent) must never delete it, so there is no delete-orphan.
    children = relationship("Todo", back_populates="parent", cascade="all")
    notes = relationship("Note", back_populates="todo", cascade="all, delete-orphan")
    ingredient_group = relationship("IngredientGroup", back_populates="todos")

    @property
    def created_at_formatted(self):
        return format_local_date(self.created_at)

    @property
    def modified_at_formatted(self):
        return format_local_date(self.modified_at)

    @property
    def created_at_formatted_full(self):
        return format_local_datetime(self.created_at)

    @property
    def modified_at_formatted_full(self):
        return format_local_datetime(self.modified_at)

    @property
    def has_note(self):
        return bool(self.note and self.note.strip())


class ListDatetimeHeader(Base):
    """A lightweight heading item that can be inserted into a list.

    It has a creation datetime only (no modified_at) and is used purely for
    visual grouping on the list view.
    """

    __tablename__ = 'list_datetime_headers'

    id = Column(Integer, primary_key=True, index=True)
    list_id = Column(Integer, ForeignKey('lists.id'), nullable=False)
    created_at = Column(DateTime, default=utcnow)
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")

    # DokuWiki click tracking
    # dokuwiki_confirmed = Column(Boolean, default=False)
    # dokuwiki_click_burst = Column(Integer, default=0)
    # dokuwiki_last_click = Column(DateTime, nullable=True)

    list = relationship("List", back_populates="datetime_headers")

    @property
    def created_at_heading(self) -> str:
        # Display with date + time in server local timezone.
        return format_local_datetime(self.created_at)


class Hashtag(Base):
    __tablename__ = 'hashtags'

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    show_completed = Column(Boolean, default=True)
    include_list_items = Column(Boolean, default=False)
    hide_completed_list_items = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")

    lists = relationship("List", secondary=list_hashtags, back_populates="hashtags")
    todos = relationship("Todo", secondary=todo_hashtags, back_populates="hashtags")
    calendar_events = relationship("CalendarEvent", secondary=calendar_event_hashtags, back_populates="hashtags")
    named_searches = relationship("NamedSearch", secondary=named_search_hashtags, back_populates="hashtags")
    excluded_from_named_searches = relationship(
        "NamedSearch",
        secondary=named_search_excluded_hashtags,
        back_populates="excluded_hashtags",
    )

    smart_folders = relationship(
        "SmartFolder",
        secondary="smart_folder_include_hashtags",
        viewonly=True,
    )
    excluded_from_smart_folders = relationship(
        "SmartFolder",
        secondary="smart_folder_exclude_hashtags",
        viewonly=True,
    )


class NamedSearch(Base):
    __tablename__ = 'named_searches'

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    uncompleted_only = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")

    hashtags = relationship("Hashtag", secondary=named_search_hashtags, back_populates="named_searches")
    excluded_hashtags = relationship(
        "Hashtag",
        secondary=named_search_excluded_hashtags,
        back_populates="excluded_from_named_searches",
    )


class SmartFolder(Base):
    """A saved, user-defined view/filter for lists and todos."""

    __tablename__ = 'smart_folders'

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)

    # What to show
    show_lists = Column(Boolean, default=True)
    show_todos = Column(Boolean, default=True)
    group_todos_by_list = Column(Boolean, default=True)

    # When enabled, show only root todos and render subtodos nested underneath.
    root_todos_only = Column(Boolean, default=False)
    nest_subtodos = Column(Boolean, default=False)

    # Common filters
    uncompleted_only = Column(Boolean, default=False)
    include_archived_lists = Column(Boolean, default=False)
    
    # Hashtag propagation
    # filter_include_subtodos = Column(Boolean, default=False) # Deprecated in favor of per-hashtag setting
    # filter_exclude_subtodos = Column(Boolean, default=False) # Deprecated in favor of per-hashtag setting

    # Sorting
    # Fields are stored as column names ("modified_at" or "created_at").
    list_sort_field = Column(String(32), default="modified_at")
    list_sort_desc = Column(Boolean, default=True)
    todo_sort_field = Column(String(32), default="modified_at")
    todo_sort_desc = Column(Boolean, default=True)

    # Date bounds (naive UTC-ish, consistent with the rest of the app)
    modified_after = Column(DateTime, nullable=True)
    modified_before = Column(DateTime, nullable=True)

    # Root folder filter (lists with folder_id = None)
    include_root_folder = Column(Boolean, default=False)
    exclude_root_folder = Column(Boolean, default=False)
    strict_nesting = Column(Boolean, default=False)

    section_order = Column(String(8), default="LET")
    sort_order = Column(Integer, default=0)

    # DokuWiki integration
    dokuwiki_links_enabled = Column(Boolean, default=False)
    dokuwiki_date_field = Column(String(32), default="created_at")  # "created_at" or "modified_at"

    created_at = Column(DateTime, default=utcnow)
    modified_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")

    include_hashtags_assoc = relationship(
        "SmartFolderIncludeHashtag",
        back_populates="smart_folder",
        cascade="all, delete-orphan",
    )
    exclude_hashtags_assoc = relationship(
        "SmartFolderExcludeHashtag",
        back_populates="smart_folder",
        cascade="all, delete-orphan",
    )
    lists = relationship(
        "List",
        secondary=smart_folder_lists,
        back_populates="smart_folders",
    )

    include_folders = relationship(
        "Folder",
        secondary=smart_folder_include_folders,
        backref="included_in_smart_folders",
    )
    exclude_folders = relationship(
        "Folder",
        secondary=smart_folder_exclude_folders,
        backref="excluded_from_smart_folders",
    )


class CalendarEvent(Base):
    __tablename__ = 'calendar_events'

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    note = Column(Text, nullable=True)
    start_at = Column(DateTime, nullable=False)
    all_day = Column(Boolean, nullable=False, default=True, server_default="1")
    duration_minutes = Column(Integer, nullable=True)
    # Store iCalendar-style RRULE string (e.g. "FREQ=WEEKLY;BYDAY=MO,WE")
    rrule = Column(Text, nullable=True)
    color = Column(String(7), nullable=True)  # e.g. "#ff0000"
    event_group_id = Column(Integer, ForeignKey('event_groups.id'), nullable=True)
    important_if_late = Column(Boolean, nullable=False, default=False, server_default="0")
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")

    created_at = Column(DateTime, default=utcnow)
    modified_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    completions = relationship(
        "CalendarEventCompletion",
        back_populates="event",
        cascade="all, delete-orphan",
    )
    notes = relationship("Note", back_populates="calendar_event", cascade="all, delete-orphan")

    hashtags = relationship("Hashtag", secondary=calendar_event_hashtags, back_populates="calendar_events")
    event_group = relationship("EventGroup", back_populates="events")

    @property
    def created_at_formatted(self):
        return format_local_date(self.created_at)

    @property
    def modified_at_formatted(self):
        return format_local_date(self.modified_at)


class CalendarEventCompletion(Base):
    __tablename__ = 'calendar_event_completions'

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey('calendar_events.id'), nullable=False)
    occurrence_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=utcnow)
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")

    event = relationship("CalendarEvent", back_populates="completions")


class CalendarDay(Base):
    """Notes and display preferences attached to one calendar date."""
    __tablename__ = 'calendar_days'

    id = Column(Integer, primary_key=True)
    day = Column(DateTime, nullable=False, unique=True)
    note = Column(Text, nullable=True)
    hide_completed = Column(Boolean, default=False, nullable=False)
    modified_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")


class EventGroup(Base):
    """A user-defined group used to organize calendar events."""
    __tablename__ = 'event_groups'

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, nullable=False)
    created_at = Column(DateTime, default=utcnow)
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")

    events = relationship("CalendarEvent", back_populates="event_group")


class Link(Base):
    __tablename__ = 'links'

    id = Column(Integer, primary_key=True, index=True)
    url = Column(String(2048), nullable=False)
    title = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    
    list_id = Column(Integer, ForeignKey('lists.id'), nullable=True)
    todo_id = Column(Integer, ForeignKey('todos.id'), nullable=True)
    note_id = Column(Integer, ForeignKey('notes.id'), nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")
    
    list = relationship("List", back_populates="links")
    todo = relationship("Todo", back_populates="links")
    note = relationship("Note", back_populates="links")


class JournalEntry(Base):
    __tablename__ = 'journal_entries'

    id = Column(Integer, primary_key=True, index=True)
    action = Column(String(255), nullable=False)  # e.g., "Created Todo", "Updated List"
    details = Column(Text, nullable=True) # Description of what happened
    entity_type = Column(String(50), nullable=True) # e.g., "todo", "list"
    entity_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")

    @property
    def created_at_formatted(self):
        return format_local_datetime(self.created_at)


class DokuWikiConfirmedItem(Base):
    """Tracks which items (todos or lists) have been confirmed as having active DokuWiki links."""
    __tablename__ = 'dokuwiki_confirmed_items'

    id = Column(Integer, primary_key=True, index=True)
    item_type = Column(String(32), nullable=False) # 'todo' or 'list'
    item_id = Column(Integer, nullable=False)
    confirmed_at = Column(DateTime, default=utcnow)
    
    # Burst tracking (stored here instead of on the item itself)
    click_burst = Column(Integer, default=0)
    last_click = Column(DateTime, nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")


class Folder(Base):
    __tablename__ = 'folders'

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    parent_id = Column(Integer, ForeignKey('folders.id'), nullable=True)
    is_collapsed = Column(Boolean, default=False)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=utcnow)
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")
    
    parent = relationship("Folder", remote_side=[id], backref=backref("children", order_by="[Folder.sort_order, Folder.name]"))
    lists = relationship("List", back_populates="folder")

    @property
    def path_name(self) -> str:
        ancestors = []
        current = self
        while current:
            ancestors.append(current.name)
            current = current.parent
        return " > ".join(reversed(ancestors))


class Note(Base):
    __tablename__ = 'notes'

    id = Column(Integer, primary_key=True, index=True)
    text = Column(Text, nullable=False)
    name = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    modified_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    list_id = Column(Integer, ForeignKey('lists.id'), nullable=True)
    todo_id = Column(Integer, ForeignKey('todos.id'), nullable=True)
    calendar_event_id = Column(Integer, ForeignKey('calendar_events.id'), nullable=True)
    occurrence_at = Column(DateTime, nullable=True)  # If set, note only shows for this specific occurrence
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")

    list = relationship("List", back_populates="notes")
    todo = relationship("Todo", back_populates="notes")
    calendar_event = relationship("CalendarEvent", back_populates="notes")
    links = relationship("Link", back_populates="note", cascade="all, delete-orphan")

    @property
    def created_at_formatted(self):
        return format_local_datetime(self.created_at)

    @property
    def modified_at_formatted(self):
        return format_local_datetime(self.modified_at)


class UserSettings(Base):
    """Per-user configuration/filters for the main views."""
    __tablename__ = 'user_settings'

    id = Column(Integer, primary_key=True, index=True)
    # Comma-separated or space-separated list of hashtags to require (OR logic or AND logic?) 
    # Usually inclusive filters are OR (show if it has any of these).
    filter_inc_hashtags = Column(Text, nullable=True)
    # Exclude if it has any of these.
    filter_exc_hashtags = Column(Text, nullable=True)
    
    hide_empty_lists = Column(Boolean, default=False)
    # If True, show completed; if False, hide them (overrides list defaults? or applies to index?)
    # Let's say this applies to the global index view of lists, or forces list views.
    # PROMPT: "filters always apply yo view_list.html and index.html only"
    show_completed = Column(Boolean, default=True)
    show_delayed = Column(Boolean, default=True)
    
    # Folder navigation
    active_folder_id = Column(Integer, ForeignKey('folders.id'), nullable=True)

    # Calendar view mode: if True, show traditional monthly grid view
    calendar_traditional_view = Column(Boolean, default=False)
    calendar_weekly_view = Column(Boolean, default=False)
    # Calendar recurrence filter: all, non_recurring, or recurring.
    calendar_event_filter = Column(String(20), default="all")
    calendar_sort_order = Column(String(20), default="created_asc")
    calendar_word_wrap = Column(Boolean, default=False)
    calendar_show_counts = Column(Boolean, default=True)
    
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")


class IngredientEvent(Base):
    """Tracks ingredient events for recipe mode lists.
    
    Events:
    - 'bought': Ingredient was purchased
    - 'ran_out': Ingredient ran out
    - 'reset': All ingredient history was reset (bulk action)
    """
    __tablename__ = 'ingredient_events'

    id = Column(Integer, primary_key=True, index=True)
    todo_id = Column(Integer, ForeignKey('todos.id'), nullable=False)
    event_type = Column(String(32), nullable=False)  # 'bought', 'ran_out', 'reset'
    quantity = Column(Integer, default=1)  # How much was bought/used
    event_date = Column(DateTime, default=utcnow)
    note = Column(Text, nullable=True)  # Optional note about this event
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")

    todo = relationship("Todo", backref="ingredient_events")

    @property
    def event_date_formatted(self):
        return format_local_date(self.event_date)


class IngredientGroup(Base):
    """Groups todos across different recipe lists as the same ingredient.
    
    This allows unified tracking - when you buy "Milk", it counts for all
    recipes that use milk. Each recipe can still specify its own quantity
    requirement.
    """
    __tablename__ = 'ingredient_groups'

    id = Column(Integer, primary_key=True, index=True)
    canonical_name = Column(String(255), nullable=False)  # The display name for this ingredient
    created_at = Column(DateTime, default=utcnow)
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")

    # Relationship to todos that are part of this group
    todos = relationship("Todo", back_populates="ingredient_group")

    @property
    def created_at_formatted(self):
        return format_local_date(self.created_at)


class Category(Base):
    """Standalone category storage for list categorization."""
    __tablename__ = 'categories'

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, nullable=False)
    created_at = Column(DateTime, default=utcnow)
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")


class Bill(Base):
    """A recurring bill / subscription to track upcoming payments."""
    __tablename__ = 'bills'

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    payee = Column(String(255), nullable=True)
    note = Column(Text, nullable=True)

    # Amount
    expected_amount = Column(Float, nullable=True)
    currency = Column(String(8), default="AUD")
    amount_strategy = Column(String(20), default="fixed", nullable=False)  # fixed, rolling_avg, last_payment
    ledger_enabled = Column(Boolean, default=False, nullable=False)

    # Due date tracking (no RRULE — just the next due date that advances on payment)
    next_due_date = Column(DateTime, nullable=True)
    anchor_day = Column(Integer, nullable=True)         # Day of month (1-31) to anchor recurrence and prevent drift
    is_due_date_confirmed = Column(Boolean, default=False)  # True if confirmed from actual invoice/statement
    frequency = Column(String(20), default="monthly")  # weekly, fortnightly, monthly, bimonthly, quarterly, yearly, custom
    frequency_days = Column(Integer, nullable=True)     # Only used when frequency == "custom"

    # Flags
    is_auto_pay = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)

    # Categorization
    category = Column(String(100), nullable=True)
    color = Column(String(20), nullable=True)

    created_at = Column(DateTime, default=utcnow)
    modified_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")

    payments = relationship("BillPayment", back_populates="bill",
                            cascade="all, delete-orphan",
                            order_by="[BillPayment.due_date.desc(), BillPayment.id.desc()]")
    invoices = relationship("BillInvoice", back_populates="bill", cascade="all, delete-orphan",
                            order_by="BillInvoice.id")
    adjustments = relationship('BillAdjustment', back_populates='bill', cascade='all, delete-orphan')
    statements = relationship('BillStatement', back_populates='bill', cascade='all, delete-orphan')

    @property
    def credit_cents(self):
        return (sum(p.unallocated_cents for p in self.payments)
                + sum(a.available_cents for a in self.adjustments if a.is_credit))

    @property
    def outstanding_cents(self):
        return (sum(i.outstanding_cents for i in self.invoices)
                + sum(a.outstanding_cents for a in self.adjustments if not a.is_credit))

    @property
    def ordered_invoices(self):
        return sorted(self.invoices, key=lambda i: (
            _to_date(i.invoice_date) or _to_date(i.due_date) or _to_date(i.created_at) or date.max,
            i.id or 0,
        ))

    @property
    def open_invoice(self):
        outstanding = [i for i in self.ordered_invoices if i.outstanding_cents > 0]
        return min(outstanding, key=lambda i: _to_date(i.due_date) or date.max, default=None)

    @property
    def created_at_formatted(self):
        return format_local_date(self.created_at)

    @property
    def modified_at_formatted(self):
        return format_local_date(self.modified_at)

    @property
    def next_due_date_formatted(self):
        return format_calendar_date(self.next_due_date)

    @property
    def next_due_date_iso(self) -> str:
        """Return next_due_date formatted as YYYY-MM-DD for HTML date inputs."""
        if not self.next_due_date:
            return ""
        if hasattr(self.next_due_date, "strftime"):
            return self.next_due_date.strftime("%Y-%m-%d")
        d = _to_date(self.next_due_date)
        return d.strftime("%Y-%m-%d") if d else ""

    def days_until_due(self, now: datetime | date | None = None) -> Optional[int]:
        """Return number of days until next due date (positive = future, negative = past, 0 = today)."""
        d_next = _to_date(self.next_due_date)
        if not d_next:
            return None
        d_now = _to_date(now) or date.today()
        return (d_next - d_now).days

    @property
    def frequency_label(self) -> str:
        if self.frequency == "bimonthly":
            return "every 2 months"
        if self.frequency == "custom":
            if self.frequency_days and self.frequency_days == 1:
                return "daily"
            elif self.frequency_days:
                return f"every {self.frequency_days} days"
            return "custom"
        return self.frequency or "monthly"

    @property
    def currency_symbol(self) -> str:
        curr = (self.currency or "AUD").strip().upper() or "AUD"
        symbols = {
            "AUD": "$",
            "USD": "$",
            "CAD": "$",
            "NZD": "$",
            "GBP": "£",
            "EUR": "€",
            "JPY": "¥",
        }
        return symbols.get(curr, "$")

    @property
    def estimated_amount(self) -> Optional[float]:
        """
        Calculates the best estimate for the next bill payment amount based on amount_strategy
        and payment history.
        - 'fixed': returns expected_amount.
        - 'last_payment': returns the most recent valid payment's amount_paid.
        - 'rolling_avg': calculates a recency-weighted moving average (WMA) of up to 4 recent
          positive payments, filtering extreme outliers when >= 4 payments exist.
          If no payment history exists, falls back to expected_amount.
        """
        strategy = (self.amount_strategy or "fixed").strip().lower()
        if strategy == "fixed" and self.expected_amount is not None:
            return self.expected_amount

        if not self.payments and not self.invoices:
            return self.expected_amount

        def _payment_sort_key(p):
            d = _to_date(p.due_date) or date.min
            return (d, p.id or 0)

        sorted_payments = sorted(self.payments, key=_payment_sort_key, reverse=True)
        charges = [i.amount_cents / 100 for i in reversed(self.ordered_invoices)
                   if not i.is_legacy and i.amount_cents > 0]
        valid_amounts = charges or [p.amount_paid for p in sorted_payments if p.amount_paid is not None and p.amount_paid > 0]

        if not valid_amounts:
            return self.expected_amount

        if strategy == "last_payment":
            return round(float(valid_amounts[0]), 2)

        k = len(valid_amounts)
        if k == 1:
            return round(float(valid_amounts[0]), 2)
        elif k == 2:
            return round((2.0 * valid_amounts[0] + 1.0 * valid_amounts[1]) / 3.0, 2)
        elif k == 3:
            return round((3.0 * valid_amounts[0] + 2.0 * valid_amounts[1] + 1.0 * valid_amounts[2]) / 6.0, 2)
        else:
            recent = valid_amounts[:4]
            sorted_recent = sorted(recent)
            med = (sorted_recent[1] + sorted_recent[2]) / 2.0
            filtered = [x for x in recent if (med == 0 or (0.25 * med <= x <= 3.0 * med))]
            if not filtered:
                filtered = recent
            m = len(filtered)
            weights = list(range(m, 0, -1))
            weighted_sum = sum(w * x for w, x in zip(weights, filtered))
            total_w = sum(weights)
            return round(weighted_sum / float(total_w), 2)

    @property
    def effective_amount(self) -> Optional[float]:
        """Return the amount to display or prefill for the current bill."""
        if self.ledger_enabled and self.outstanding_cents:
            return self.outstanding_cents / 100
        if self.is_due_date_confirmed and self.expected_amount is not None:
            return self.expected_amount
        est = self.estimated_amount
        return est if est is not None else self.expected_amount

    @property
    def budget_amount(self) -> Optional[float]:
        """Recurring charge estimate, independent of account debt or partial cash."""
        return self.estimated_amount if self.ledger_enabled else self.effective_amount

    @property
    def is_estimated(self) -> bool:
        """Return True if this bill uses a dynamic estimate derived from payment history."""
        if (self.ledger_enabled and self.outstanding_cents) or self.is_due_date_confirmed:
            return False
        strat = (self.amount_strategy or "fixed").strip().lower()
        if strat in ("rolling_avg", "last_payment"):
            return (any(not i.is_legacy and i.amount_cents > 0 for i in self.invoices)
                    or any(p.amount_paid is not None and p.amount_paid > 0 for p in (self.payments or [])))
        return False

    @property
    def last_payment(self):
        """Return the most recent payment if any, sorted by due_date desc and id desc."""
        if not self.payments:
            return None
        def _payment_sort_key(p):
            d = _to_date(p.due_date) or date.min
            return (d, p.id or 0)
        return sorted(self.payments, key=_payment_sort_key, reverse=True)[0]

    def is_covered_early(self, now: datetime | date | None = None) -> bool:
        """
        Returns True if:
        1. The most recent payment covered a due date that is today or in the future (paid early), OR
        2. The payment was made very recently (0 to 3 days ago) and the next due date is in the future.
        Bills whose next_due_date is today or in the past are due now and never considered covered early.
        """
        if self.ledger_enabled and self.outstanding_cents:
            return False
        d_next = _to_date(self.next_due_date)
        d_now = _to_date(now) or date.today()
        if not d_next or d_next <= d_now:
            return False
        lp = self.last_payment
        if not lp:
            return False
        d_due = _to_date(lp.due_date)
        d_paid = _to_date(lp.paid_date)
        if not d_due or not d_paid:
            return False
        # If payment covered a due date that hasn't passed yet
        if d_due >= d_now:
            return True
        # Or paid within the last 3 days and next cycle is in future
        days_since_paid = (d_now - d_paid).days
        if 0 <= days_since_paid <= 3 and d_next > d_now:
            return True
        return False


def _to_date(val: datetime | date | str | None) -> Optional[date]:
    """Safely convert datetime, date, or ISO string to datetime.date."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return None
        try:
            return date.fromisoformat(val[:10])
        except Exception:
            return None
    return None


class BillDueDateSubmission(Base):
    """Used date-only confirmation forms survive later schedule changes."""
    __tablename__ = 'bill_due_date_submissions'
    id = Column(Integer, primary_key=True)
    bill_id = Column(Integer, nullable=False, index=True)
    request_token = Column(String(64), nullable=False, unique=True, index=True)
    request_fingerprint = Column(String(64), nullable=False)
    is_deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=utcnow)
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")


class BillRecordSubmission(Base):
    """Used invoice, adjustment and statement forms outlive their records."""
    __tablename__ = 'bill_record_submissions'
    __table_args__ = (
        UniqueConstraint('record_type', 'request_token'),
        CheckConstraint("record_type IN ('invoice', 'adjustment', 'statement')"),
    )
    id = Column(Integer, primary_key=True)
    # No foreign keys: record/account deletion must not free a used form.
    bill_id = Column(Integer, nullable=False, index=True)
    record_type = Column(String(20), nullable=False)
    record_id = Column(Integer, nullable=False, index=True)
    request_token = Column(String(64), nullable=False)
    request_fingerprint = Column(String(64), nullable=True)
    is_deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=utcnow)
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")


class BillPaymentSubmission(Base):
    """Used payment forms survive removal of the associated financial records."""
    __tablename__ = 'bill_payment_submissions'
    id = Column(Integer, primary_key=True)
    # No foreign keys: deletion of a receipt or account must not free its form token.
    bill_id = Column(Integer, nullable=False, index=True)
    payment_id = Column(Integer, nullable=True)
    request_token = Column(String(64), nullable=False, unique=True, index=True)
    request_fingerprint = Column(String(64), nullable=True)
    is_deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=utcnow)
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")


class BillPayment(Base):
    """Records an individual payment made against a Bill."""
    __tablename__ = 'bill_payments'

    id = Column(Integer, primary_key=True, index=True)
    bill_id = Column(Integer, ForeignKey('bills.id'), nullable=False, index=True)

    due_date = Column(DateTime, nullable=False)       # What the due date was at time of payment
    paid_date = Column(DateTime, nullable=False, index=True)      # When it was actually paid
    amount_paid = Column(Float, nullable=True)        # Actual amount (may differ from expected)
    amount_cents = Column(Integer, nullable=True)
    invoice_id = Column(Integer, ForeignKey('bill_invoices.id'), nullable=True)
    payment_method = Column(String(100), nullable=True)
    request_token = Column(String(64), nullable=True, unique=True, index=True)
    request_fingerprint = Column(String(64), nullable=True)
    note = Column(Text, nullable=True)
    is_late_overridden = Column(Boolean, default=False)  # User manually marked as not late

    created_at = Column(DateTime, default=utcnow)
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")

    bill = relationship("Bill", back_populates="payments")
    invoice = relationship("BillInvoice", foreign_keys=[invoice_id])
    allocations = relationship("BillAllocation", back_populates="payment", cascade="all, delete-orphan")
    adjustment_allocations = relationship('BillAdjustmentAllocation', back_populates='payment',
                                          foreign_keys='BillAdjustmentAllocation.payment_id', cascade='all, delete-orphan')

    @property
    def unallocated_cents(self):
        if self.amount_cents is None:
            return 0
        return max(0, self.amount_cents - sum(a.amount_cents for a in self.allocations)
                   - sum(a.amount_cents for a in self.adjustment_allocations))

    @property
    def currency_symbol(self) -> str:
        return self.bill.currency_symbol if self.bill else "$"

    @property
    def effective_due_date(self):
        if self.invoice_id and self.invoice:
            return self.invoice.due_date
        if self.amount_cents is not None:
            return None
        return self.due_date

    @property
    def due_date_formatted(self):
        return format_calendar_date(self.effective_due_date)

    @property
    def paid_date_formatted(self):
        return format_calendar_date(self.paid_date)

    @property
    def due_date_iso(self) -> str:
        """Return due_date formatted as YYYY-MM-DD for HTML date inputs."""
        due = self.effective_due_date
        if not due:
            return ""
        if hasattr(due, "strftime"):
            return due.strftime("%Y-%m-%d")
        d = _to_date(due)
        return d.strftime("%Y-%m-%d") if d else ""

    @property
    def paid_date_iso(self) -> str:
        """Return paid_date formatted as YYYY-MM-DD for HTML date inputs."""
        if not self.paid_date:
            return ""
        if hasattr(self.paid_date, "strftime"):
            return self.paid_date.strftime("%Y-%m-%d")
        d = _to_date(self.paid_date)
        return d.strftime("%Y-%m-%d") if d else ""

    @property
    def days_difference(self) -> int:
        """
        Days between due date and paid date (due_date - paid_date).
        Positive = paid early. 0 = paid on due date. Negative = paid late.
        """
        d_due = _to_date(self.effective_due_date)
        d_paid = _to_date(self.paid_date)
        if not d_due or not d_paid:
            return 0
        return (d_due - d_paid).days

    @property
    def timing_label(self) -> str:
        if not self.effective_due_date or not self.paid_date:
            return ""
        diff = self.days_difference
        if diff > 1:
            return f"{diff} days early"
        elif diff == 1:
            return "1 day early"
        elif diff == 0:
            return "On time"
        elif diff == -1:
            return "1 day late"
        else:
            return f"{abs(diff)} days late"

    @property
    def is_late(self) -> bool:
        """True if paid after due date and override not enabled."""
        if self.is_late_overridden:
            return False
        return self.days_difference < 0

    @property
    def notes_list(self):
        """Return payment notes as a list of dicts. Migrates plain-text and string lists on the fly."""
        if not self.note:
            return []
        try:
            data = json.loads(self.note)
            if isinstance(data, list):
                result = []
                for item in data:
                    if isinstance(item, dict):
                        result.append(item)
                    else:
                        result.append({"text": str(item), "created_at": None})
                return result
            elif isinstance(data, dict):
                return [data]
            return [{"text": str(data), "created_at": None}]
        except (json.JSONDecodeError, ValueError):
            return [{"text": self.note, "created_at": None}]

    @property
    def display_note(self) -> str:
        """Return human-readable summary of notes for display without exposing raw JSON."""
        notes = self.notes_list
        if not notes:
            return ""
        return ", ".join(n["text"] for n in notes if isinstance(n, dict) and n.get("text"))


class BillInvoice(Base):
    """A provider charge, distinct from cash received and any existing account credit."""
    __tablename__ = 'bill_invoices'
    __table_args__ = (CheckConstraint('amount_cents >= 0'),
                      Index('ix_bill_invoices_number', 'bill_id', 'invoice_number_key', unique=True))
    id = Column(Integer, primary_key=True)
    bill_id = Column(Integer, ForeignKey('bills.id'), nullable=False, index=True)
    due_date = Column(DateTime, nullable=True)
    invoice_date = Column(DateTime, nullable=True)
    period_start = Column(DateTime, nullable=True)
    period_end = Column(DateTime, nullable=True)
    statement_balance_cents = Column(Integer, nullable=True)
    request_token = Column(String(64), nullable=True, unique=True, index=True)
    request_fingerprint = Column(String(64), nullable=True)
    invoice_number = Column(String(255), nullable=True)
    invoice_number_key = Column(String(255), nullable=True)
    amount_cents = Column(Integer, nullable=False)
    note = Column(Text, nullable=True)
    is_legacy = Column(Boolean, default=False, nullable=False)
    settled_at = Column(DateTime, nullable=True)
    schedule_advanced = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=utcnow)
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")
    bill = relationship("Bill", back_populates="invoices")
    allocations = relationship("BillAllocation", back_populates="invoice", cascade="all, delete-orphan")
    adjustment_allocations = relationship('BillAdjustmentAllocation', back_populates='invoice',
                                          foreign_keys='BillAdjustmentAllocation.invoice_id', cascade='all, delete-orphan')

    @property
    def outstanding_cents(self):
        return max(0, self.amount_cents - sum(a.amount_cents for a in self.allocations)
                   - sum(a.amount_cents for a in self.adjustment_allocations))

    @property
    def credit_applied_cents(self):
        return (sum(a.amount_cents for a in self.allocations if a.is_credit)
                + sum(a.amount_cents for a in self.adjustment_allocations))

    @property
    def due_date_iso(self):
        return self.due_date.strftime('%Y-%m-%d') if self.due_date else ''


class BillAllocation(Base):
    __tablename__ = 'bill_allocations'
    __table_args__ = (CheckConstraint('amount_cents > 0'),)
    id = Column(Integer, primary_key=True)
    invoice_id = Column(Integer, ForeignKey('bill_invoices.id'), nullable=False, index=True)
    payment_id = Column(Integer, ForeignKey('bill_payments.id'), nullable=False, index=True)
    amount_cents = Column(Integer, nullable=False)
    is_credit = Column(Boolean, default=False, nullable=False)
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")
    invoice = relationship("BillInvoice", back_populates="allocations")
    payment = relationship("BillPayment", back_populates="allocations")


class BillAdjustment(Base):
    __tablename__ = 'bill_adjustments'
    __table_args__ = (CheckConstraint('amount_cents > 0'),
                      CheckConstraint("kind IN ('opening_credit', 'opening_debt', 'supplier_credit', 'supplier_debit', 'refund')"))
    CREDIT_KINDS = frozenset({'opening_credit', 'supplier_credit'})
    LABELS = {'opening_credit': 'Opening credit', 'opening_debt': 'Opening amount owing',
              'supplier_credit': 'Supplier credit', 'supplier_debit': 'Supplier debit', 'refund': 'Cash refund received'}
    id = Column(Integer, primary_key=True)
    bill_id = Column(Integer, ForeignKey('bills.id'), nullable=False, index=True)
    kind = Column(String(30), nullable=False)
    amount_cents = Column(Integer, nullable=False)
    effective_date = Column(DateTime, nullable=False)
    note = Column(Text, nullable=True)
    request_token = Column(String(64), nullable=True, unique=True, index=True)
    request_fingerprint = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")
    bill = relationship('Bill', back_populates='adjustments')
    credit_allocations = relationship('BillAdjustmentAllocation', back_populates='credit_adjustment',
                                      foreign_keys='BillAdjustmentAllocation.credit_adjustment_id', cascade='all, delete-orphan')
    debit_allocations = relationship('BillAdjustmentAllocation', back_populates='debit_adjustment',
                                     foreign_keys='BillAdjustmentAllocation.debit_adjustment_id', cascade='all, delete-orphan')

    @property
    def is_credit(self):
        return self.kind in self.CREDIT_KINDS

    @property
    def kind_label(self):
        return self.LABELS.get(self.kind, self.kind)

    @property
    def balance_delta_cents(self):
        return -self.amount_cents if self.is_credit else self.amount_cents

    @property
    def available_cents(self):
        return max(0, self.amount_cents - sum(a.amount_cents for a in self.credit_allocations)) if self.is_credit else 0

    @property
    def outstanding_cents(self):
        return max(0, self.amount_cents - sum(a.amount_cents for a in self.debit_allocations)) if not self.is_credit else 0


class BillAdjustmentAllocation(Base):
    __tablename__ = 'bill_adjustment_allocations'
    __table_args__ = (
        CheckConstraint('amount_cents > 0'),
        CheckConstraint('(invoice_id IS NOT NULL AND debit_adjustment_id IS NULL) OR (invoice_id IS NULL AND debit_adjustment_id IS NOT NULL)'),
        CheckConstraint('(payment_id IS NOT NULL AND credit_adjustment_id IS NULL) OR (payment_id IS NULL AND credit_adjustment_id IS NOT NULL)'),
    )
    id = Column(Integer, primary_key=True)
    invoice_id = Column(Integer, ForeignKey('bill_invoices.id'), nullable=True, index=True)
    debit_adjustment_id = Column(Integer, ForeignKey('bill_adjustments.id'), nullable=True, index=True)
    payment_id = Column(Integer, ForeignKey('bill_payments.id'), nullable=True, index=True)
    credit_adjustment_id = Column(Integer, ForeignKey('bill_adjustments.id'), nullable=True, index=True)
    amount_cents = Column(Integer, nullable=False)
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")
    invoice = relationship('BillInvoice', back_populates='adjustment_allocations', foreign_keys=[invoice_id])
    payment = relationship('BillPayment', back_populates='adjustment_allocations', foreign_keys=[payment_id])
    credit_adjustment = relationship('BillAdjustment', back_populates='credit_allocations', foreign_keys=[credit_adjustment_id])
    debit_adjustment = relationship('BillAdjustment', back_populates='debit_allocations', foreign_keys=[debit_adjustment_id])


class BillStatement(Base):
    __tablename__ = 'bill_statements'
    id = Column(Integer, primary_key=True)
    bill_id = Column(Integer, ForeignKey('bills.id'), nullable=False, index=True)
    as_of_date = Column(DateTime, nullable=False)
    balance_cents = Column(Integer, nullable=False)
    note = Column(Text, nullable=True)
    request_token = Column(String(64), nullable=True, unique=True, index=True)
    request_fingerprint = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")
    bill = relationship('Bill', back_populates='statements')


class Expense(Base):
    __tablename__ = "expenses"

    id = Column(Integer, primary_key=True, index=True)
    amount = Column(Float, nullable=False)
    description = Column(String(255), nullable=True)
    category = Column(String(100), nullable=True)
    date = Column(DateTime, nullable=False, default=utcnow)
    payment_method = Column(String(50), nullable=True)
    currency = Column(String(8), default="AUD")
    note = Column(Text, nullable=True)

    created_at = Column(DateTime, default=utcnow)
    modified_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    metadata_json = Column(JSON, nullable=False, default=dict, server_default="{}")

    @property
    def currency_symbol(self) -> str:
        curr = (self.currency or "AUD").strip().upper() or "AUD"
        symbols = {
            "AUD": "$",
            "USD": "$",
            "CAD": "$",
            "NZD": "$",
            "GBP": "£",
            "EUR": "€",
            "JPY": "¥",
        }
        return symbols.get(curr, "$")

    @property
    def date_formatted(self) -> str:
        return format_calendar_date(self.date)

    @property
    def date_iso(self) -> str:
        if not self.date:
            return ""
        if hasattr(self.date, "strftime"):
            return self.date.strftime("%Y-%m-%d")
        d = _to_date(self.date)
        return d.strftime("%Y-%m-%d") if d else ""

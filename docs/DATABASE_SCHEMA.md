# Simple Todo Database Schema

This document defines all data types stored in the SQLite database. It serves as a reference for reproducing the data layer from scratch.

All timestamps are stored as **naive UTC datetimes**.

---

## Core Entities

### `lists`
Todo lists that contain todos.

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | INTEGER | No | Auto | Primary key |
| `name` | VARCHAR(255) | No | - | List name |
| `completed` | BOOLEAN | Yes | `False` | Whether list is completed |
| `archived` | BOOLEAN | Yes | `0` | Soft delete/archive flag |
| `pinned` | BOOLEAN | Yes | `0` | Whether pinned to top |
| `pinned_at` | DATETIME | Yes | - | When pinned |
| `is_collation` | BOOLEAN | Yes | `0` | If true, this is a "collation list" that collects todos from other lists |
| `collation_color` | VARCHAR(7) | Yes | - | Hex color for collation UI (e.g., `#aabbcc`) |
| `show_completed` | BOOLEAN | Yes | `1` | Display completed todos in this list |
| `show_delayed` | BOOLEAN | Yes | `1` | Display delayed todos in this list |
| `sort_ascending` | BOOLEAN | Yes | `0` | Sort order for todos |
| `partition_by_datetime_headers` | BOOLEAN | Yes | `1` | Group todos by datetime headers |
| `intersperse_collated_todos` | BOOLEAN | Yes | `0` | Mix collated todos with native todos |
| `manual_sorting` | BOOLEAN | Yes | `0` | Enable drag-and-drop reordering |
| `delayed_until` | DATETIME | Yes | - | Hide list until this time |
| `folder_id` | INTEGER | Yes | - | FK → `folders.id` |
| `created_at` | DATETIME | Yes | `utcnow()` | Creation timestamp |
| `modified_at` | DATETIME | Yes | `utcnow()` | Last modification |

**Relationships:**
- Has many `todos`
- Has many `notes`
- Has many `links`
- Has many `list_datetime_headers`
- Belongs to optional `folder`
- Many-to-many with `hashtags` via `list_hashtags`
- Many-to-many with `smart_folders` via `smart_folder_lists`
- Many-to-many with `todos` via `collation_todos` (for collation lists)

---

### `todos`
Individual todo items within a list.

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | INTEGER | No | Auto | Primary key |
| `title` | VARCHAR(255) | No | - | Todo text |
| `note` | TEXT | Yes | - | Extended note/description |
| `color` | VARCHAR(7) | Yes | - | Hex color (e.g., `#ff0000`) |
| `completed` | BOOLEAN | Yes | `False` | Completion status |
| `pinned` | BOOLEAN | Yes | `0` | Whether pinned to top |
| `pinned_at` | DATETIME | Yes | - | When pinned |
| `list_id` | INTEGER | No | - | FK → `lists.id` |
| `parent_id` | INTEGER | Yes | - | FK → `todos.id` (for subtodos) |
| `sort_order` | INTEGER | Yes | `0` | Manual sort position |
| `delayed_until` | DATETIME | Yes | - | Hide todo until this time |
| `alarm_start` | DATETIME | Yes | - | Alarm window start |
| `alarm_end` | DATETIME | Yes | - | Alarm window end |
| `created_at` | DATETIME | Yes | `utcnow()` | Creation timestamp |
| `modified_at` | DATETIME | Yes | `utcnow()` | Last modification |

**Relationships:**
- Belongs to `list`
- Has many `children` (subtodos, self-referential)
- Has optional `parent` (self-referential)
- Has many `notes`
- Has many `links`
- Many-to-many with `hashtags` via `todo_hashtags`
- Many-to-many with `lists` (collation lists) via `collation_todos`

---

### `hashtags`
Tags for categorizing lists, todos, and calendar events.

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | INTEGER | No | Auto | Primary key |
| `name` | VARCHAR(100) | No | - | Unique hashtag name |
| `show_completed` | BOOLEAN | Yes | `1` | Show completed items in hashtag view |
| `created_at` | DATETIME | Yes | `utcnow()` | Creation timestamp |

**Relationships:**
- Many-to-many with `lists` via `list_hashtags`
- Many-to-many with `todos` via `todo_hashtags`
- Many-to-many with `calendar_events` via `calendar_event_hashtags`
- Many-to-many with `smart_folders` via `smart_folder_include_hashtags` and `smart_folder_exclude_hashtags`

---

### `folders`
Organizational folders that contain lists.

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | INTEGER | No | Auto | Primary key |
| `name` | VARCHAR(255) | No | - | Folder name |
| `parent_id` | INTEGER | Yes | - | FK → `folders.id` (for nesting) |
| `is_collapsed` | BOOLEAN | Yes | `0` | UI collapse state |
| `sort_order` | INTEGER | Yes | `0` | Display order |
| `created_at` | DATETIME | Yes | `utcnow()` | Creation timestamp |

**Relationships:**
- Has many `lists`
- Has many `children` (subfolders, self-referential)
- Has optional `parent` (self-referential)

---

### `notes`
Extended notes attached to lists, todos, or calendar events.

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | INTEGER | No | Auto | Primary key |
| `text` | TEXT | No | - | Note content |
| `name` | VARCHAR(255) | Yes | - | Optional note title |
| `list_id` | INTEGER | Yes | - | FK → `lists.id` |
| `todo_id` | INTEGER | Yes | - | FK → `todos.id` |
| `calendar_event_id` | INTEGER | Yes | - | FK → `calendar_events.id` |
| `occurrence_at` | DATETIME | Yes | - | For recurring events: specific occurrence |
| `created_at` | DATETIME | Yes | `utcnow()` | Creation timestamp |
| `modified_at` | DATETIME | Yes | `utcnow()` | Last modification |

**Note:** Exactly one of `list_id`, `todo_id`, or `calendar_event_id` should be set.

---

### `links`
URL bookmarks attached to lists or todos.

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | INTEGER | No | Auto | Primary key |
| `url` | VARCHAR(2048) | No | - | URL |
| `title` | VARCHAR(255) | Yes | - | Display title |
| `list_id` | INTEGER | Yes | - | FK → `lists.id` |
| `todo_id` | INTEGER | Yes | - | FK → `todos.id` |
| `created_at` | DATETIME | Yes | `utcnow()` | Creation timestamp |

---

## Calendar

### `calendar_events`
Recurring or one-time calendar events.

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | INTEGER | No | Auto | Primary key |
| `title` | VARCHAR(255) | No | - | Event title |
| `note` | TEXT | Yes | - | Event description |
| `start_at` | DATETIME | No | - | Local calendar date and optional wall-clock start time; no timezone conversion |
| `all_day` | BOOLEAN | No | True | Date-only event; existing events migrate as all-day |
| `duration_minutes` | INTEGER | Yes | - | Optional duration for timed events, from 1 to 10080 minutes |
| `rrule` | TEXT | Yes | - | iCalendar RRULE string (e.g., `FREQ=WEEKLY;BYDAY=MO,WE`) |
| `created_at` | DATETIME | Yes | `utcnow()` | Creation timestamp |
| `modified_at` | DATETIME | Yes | `utcnow()` | Last modification |

**Relationships:**
- Has many `calendar_event_completions`
- Has many `notes`
- Many-to-many with `hashtags` via `calendar_event_hashtags`

---

### `calendar_event_completions`
Tracks completion of individual occurrences of recurring events.

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | INTEGER | No | Auto | Primary key |
| `event_id` | INTEGER | No | - | FK → `calendar_events.id` |
| `occurrence_at` | DATETIME | No | - | Which occurrence was completed |
| `created_at` | DATETIME | Yes | `utcnow()` | When it was marked complete |

---

## Saved Queries

### `smart_folders`
User-defined filtered views combining lists and todos.

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | INTEGER | No | Auto | Primary key |
| `name` | VARCHAR(255) | No | - | Folder name |
| `show_lists` | BOOLEAN | Yes | `True` | Include lists in results |
| `show_todos` | BOOLEAN | Yes | `True` | Include todos in results |
| `group_todos_by_list` | BOOLEAN | Yes | `True` | Group todos under their lists |
| `root_todos_only` | BOOLEAN | Yes | `0` | Only show top-level todos |
| `nest_subtodos` | BOOLEAN | Yes | `0` | Render subtodos hierarchically |
| `strict_nesting` | BOOLEAN | Yes | `0` | Strict parent-child display |
| `uncompleted_only` | BOOLEAN | Yes | `False` | Filter to uncompleted items |
| `include_archived_lists` | BOOLEAN | Yes | `False` | Include archived lists |
| `include_root_folder` | BOOLEAN | Yes | `0` | Include lists with no folder |
| `exclude_root_folder` | BOOLEAN | Yes | `0` | Exclude lists with no folder |
| `list_sort_field` | VARCHAR(32) | Yes | `modified_at` | Sort lists by this field |
| `list_sort_desc` | BOOLEAN | Yes | `1` | Lists descending order |
| `todo_sort_field` | VARCHAR(32) | Yes | `modified_at` | Sort todos by this field |
| `todo_sort_desc` | BOOLEAN | Yes | `1` | Todos descending order |
| `modified_after` | DATETIME | Yes | - | Date filter lower bound |
| `modified_before` | DATETIME | Yes | - | Date filter upper bound |
| `sort_order` | INTEGER | Yes | `0` | Display order in UI |
| `dokuwiki_links_enabled` | BOOLEAN | Yes | `0` | Enable DokuWiki integration |
| `dokuwiki_date_field` | VARCHAR(32) | Yes | `created_at` | Date field for DokuWiki |
| `created_at` | DATETIME | Yes | `utcnow()` | Creation timestamp |
| `modified_at` | DATETIME | Yes | `utcnow()` | Last modification |

**Relationships:**
- Many-to-many with `lists` via `smart_folder_lists`
- Many-to-many with `hashtags` via `smart_folder_include_hashtags` (with `include_subtodos` flag)
- Many-to-many with `hashtags` via `smart_folder_exclude_hashtags` (with `include_subtodos` flag)
- Many-to-many with `folders` via `smart_folder_include_folders`
- Many-to-many with `folders` via `smart_folder_exclude_folders`

---

## Supporting Tables

### `list_datetime_headers`
Visual datetime separators inserted into lists.

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | INTEGER | No | Auto | Primary key |
| `list_id` | INTEGER | No | - | FK → `lists.id` |
| `created_at` | DATETIME | Yes | `utcnow()` | Header timestamp (displayed) |

---

### `user_settings`
Per-user configuration and global filters.

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | INTEGER | No | Auto | Primary key |
| `filter_inc_hashtags` | TEXT | Yes | - | Comma-separated include hashtags |
| `filter_exc_hashtags` | TEXT | Yes | - | Comma-separated exclude hashtags |
| `hide_empty_lists` | BOOLEAN | Yes | `0` | Hide lists with no visible todos |
| `show_completed` | BOOLEAN | Yes | `1` | Global completed visibility |
| `show_delayed` | BOOLEAN | Yes | `1` | Global delayed visibility |
| `active_folder_id` | INTEGER | Yes | - | FK → `folders.id` (current folder context) |
| `updated_at` | DATETIME | Yes | `utcnow()` | Last update |

---

### `journal_entries`
Audit log of user actions.

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | INTEGER | No | Auto | Primary key |
| `action` | VARCHAR(255) | No | - | Action name (e.g., "Created Todo") |
| `details` | TEXT | Yes | - | Action description |
| `entity_type` | VARCHAR(50) | Yes | - | Entity type (e.g., "todo", "list") |
| `entity_id` | INTEGER | Yes | - | ID of affected entity |
| `created_at` | DATETIME | Yes | `utcnow()` | When action occurred |

---

### `dokuwiki_confirmed_items`
Tracks DokuWiki link confirmation state (for external wiki integration).

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | INTEGER | No | Auto | Primary key |
| `item_type` | VARCHAR(32) | No | - | Entity type ("todo" or "list") |
| `item_id` | INTEGER | No | - | ID of the item |
| `confirmed_at` | DATETIME | Yes | `utcnow()` | When confirmed |
| `click_burst` | INTEGER | Yes | `0` | Click tracking counter |
| `last_click` | DATETIME | Yes | - | Last click timestamp |

---

## Association Tables (Many-to-Many)

| Table | Columns | Description |
|-------|---------|-------------|
| `list_hashtags` | `list_id`, `hashtag_id` | Lists ↔ Hashtags |
| `todo_hashtags` | `todo_id`, `hashtag_id` | Todos ↔ Hashtags |
| `calendar_event_hashtags` | `calendar_event_id`, `hashtag_id` | Calendar Events ↔ Hashtags |
| `smart_folder_include_hashtags` | `smart_folder_id`, `hashtag_id`, `include_subtodos` | Smart Folders ↔ Hashtags (include) |
| `smart_folder_exclude_hashtags` | `smart_folder_id`, `hashtag_id`, `include_subtodos` | Smart Folders ↔ Hashtags (exclude) |
| `smart_folder_lists` | `smart_folder_id`, `list_id` | Smart Folders ↔ Lists |
| `smart_folder_include_folders` | `smart_folder_id`, `folder_id` | Smart Folders ↔ Folders (include) |
| `smart_folder_exclude_folders` | `smart_folder_id`, `folder_id` | Smart Folders ↔ Folders (exclude) |
| `collation_todos` | `collation_list_id`, `todo_id` | Collation Lists ↔ Todos |

---

## Implementation Notes

### Entity history and activity

Each user database has `entity_versions` and `entity_activity` tables. SQLite
triggers cover all mapped application tables, including settings, billing,
existing journal entries, and association rows. An entity is identified by its
table name (`entity_type`) and a JSON object containing its primary-key columns
(`entity_key`); composite keys identify association rows. Primary keys reused
after deletion share the same history key. Changing a primary key records a
delete followed by a create.

`entity_versions` contains `id`, `entity_type`, `entity_key`, `operation`,
`recorded_at`, and `snapshot`. Snapshots are JSON objects containing all SQLite
column values; BLOBs are represented as `{"$sqlite_blob_hex":"..."}`. Finite REAL
values are JSON numbers with enough decimal precision to recover the stored
floating-point value exactly. Older snapshots written with rounded values cannot
recover the lost precision. A delete snapshot contains the last values before
deletion. Installation records one `baseline` snapshot for each existing row,
but cannot recover older changes.
Subsequent creates, actual value changes, and deletes produce snapshots and
matching entries in `entity_activity` in the same transaction. Rolled-back
changes are not recorded. The activity table contains `id`, `entity_type`,
`entity_key`, `operation`, and `recorded_at`; it is not thinned and does not
identify the actor or record reads.

Timestamps and day boundaries are UTC. All snapshots newer than one calendar
year are retained; for older snapshots, automatic maintenance keeps the latest
per entity per UTC day indefinitely, breaking timestamp ties by `id`. The
activity log remains complete even when snapshots are thinned. Maintenance
runs when a database is opened and periodically while the server runs, including
previously initialized user databases not open in that worker. Cleanup scans
small ID pages, commits each deletion batch, and briefly yields between batches
so waiting writers can proceed. It uses the retention index to find each day's
latest old snapshot. A stopped server does not run maintenance. Records and
relationship rows are separate snapshots; history is not a replacement for a
consistent database backup.

The triggers also cover direct SQLite writes, but external clients using
`INSERT OR REPLACE` must set `PRAGMA recursive_triggers=ON` to record the
implicit delete; application connections enable it automatically. History
starts when each existing user database is first opened after upgrading. The
history tables themselves are not versioned. As with the underlying database,
history contains potentially sensitive data and must be included in backup and
access-control policies.

1. **SQLAlchemy ORM**: The app uses SQLAlchemy declarative models in `models.py`
2. **Migrations**: Column additions are handled via lightweight migrations in `database.py` (checking for missing columns and adding them)
3. **Multi-user**: Database files are per-user: `{username}_todo_app.db`
4. **Timestamps**: All stored as naive UTC; converted to local timezone in display formatters
5. **Soft deletes**: Lists use `archived` flag; no hard deletes
6. **Hierarchical data**: Both `todos` and `folders` support self-referential parent/child relationships

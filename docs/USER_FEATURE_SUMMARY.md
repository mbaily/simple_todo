# Simple Todo App — User-Facing Feature Summary (Architecture-Agnostic)

This document captures the app behavior from a user perspective so the product can be rebuilt with a different architecture (e.g., Yjs/CRDT sync, full PWA, alternative backend/frontend stack) while preserving UX and feature semantics.

## 1) Core objects users work with

- **List**: container for todos. Can be completed, archived, pinned, delayed, categorized, placed in folder, and optionally set as collation/recipe/manual-sort list.
- **Todo**: task inside a list (supports subtodos). Can be completed, pinned, delayed, alarmed, colorized, tagged, linked, and optionally grouped as ingredient.
- **List datetime divider/header**: timestamp marker in a list used for visual grouping.
- **Note**: freeform text note tied to list/todo/calendar occurrence.
- **Hashtag**: tag attached to lists/todos/calendar events; supports behavior toggles.
- **Named search**: saved include/exclude hashtag query (with optional uncompleted-only).
- **Smart folder**: advanced saved view with filters/sorting across lists/todos/events.
- **Folder**: hierarchical container for lists (tree structure, collapsible).
- **Category**: simple label assigned to lists.
- **Calendar event**: dated event, optionally recurring, with completion tracked per occurrence.
- **Offline note**: local-only note stored in browser (not synced to server).
- **Ingredient group/event**: recipe-mode inventory tracking across list todos.
- **Journal entry**: activity log of user actions.

## 2) Global UX patterns

- Two checkbox roles:
  - **Selection checkbox** for bulk actions.
  - **Completion checkbox** for done/undone state.
- Selection UX:
  - “N selected” counter.
  - “Created/Modified” metadata shown for single selection.
- Bulk operations exist across multiple pages (delete, pin, archive, edit, etc.).
- Dates/times are consistently displayed in configured format.
- Multi-language UI support (en/es/fr/de/ja/pl).
- Optional auth flow (login/logout session cookie) when auth is enabled.

## 3) Main navigation destinations

Home, Search, Calendar, Hashtags, Smart Folders, Folders, Archived, Collations, Customization; plus contextual routes like Uncompleted, Journal, Pantry, Shopping List, Ingredient Report, Offline Notes.

## 4) Home page behavior

- Shows active lists (typically filtered by active folder/unfiled context).
- Supports quick add of new list.
- Supports Daily Plan quick-create (date-offset shortcut) that creates manual-order lists.
- Supports config-gated compact quick-add forms on Home/List with optional hashtag multi-select.
- Shows pinned lists/todos, split into recent vs older pinned.
- Shows optional calendar preview window around today.
- Shows active alarms and resuming items (items whose deferral has ended).
- Groups lists by category + uncategorized.
- Supports global sort toggle (created vs modified).
- Has link area for Offline Notes and Journal.
- Bulk actions on selected lists/todos:
  - list: pin/unpin/archive/unarchive/edit/delete
  - todo: pin/unpin/edit/delete

## 5) List view behavior

- Header: list title, hashtags, timestamps, list navigation (first/prev/next/index).
- Add forms: todo, note, datetime header/divider.
- Display options/toggles:
  - show/hide completed
  - show/hide delayed
  - mark list as collation
  - manual ordering mode
  - recipe mode (when allowed)
  - show DokuWiki item links / page links (if enabled)
- Todo row capabilities:
  - complete/incomplete
  - pin/unpin
  - edit
  - hashtags
  - note indicator
  - alarm indicator
  - optional DokuWiki link
  - collation dot toggles (include in collation lists)
- Supports subtodos (parent/child nesting).
- Subtodo nesting is recursive and effectively unbounded (no explicit maximum depth is enforced by the app logic).
- Notes can appear inline in list flow (list-level and todo-attached) and are selectable/editable like other items.
- Supports list-level category assignment.
- Supports defer and alarm actions inline for selected item(s).
- Supports inline hashtag edits for selected todos.
- Supports quick subnote creation for a selected todo.
- Supports bulk enable/disable of per-todo DokuWiki pages (when DokuWiki item-link mode is enabled).
- Supports manual reorder of selected todos.
- Supports recipe-mode bulk actions (e.g., ingredient-state operations) when recipe mode is enabled in lists that also use manual ordering mode.
- Supports deleting selected datetime headers.
- Can render a synthetic (presentational) oldest divider when a list uses headers and has older pre-header items.
- Collation-list behavior:
  - can show collected todos from other lists
  - can intersperse collected todos with native ones (toggle)
  - collected item inclusion toggled by colored dots

## 6) Edit pages and item metadata

### Edit Todo
- Title, note, color picker, hashtags
- Defer-until date
- Alarm mode:
  - none
  - explicit range
  - target time ± minutes
- Link management (add/remove URLs)
- Created/modified timestamps

### Edit List
- Name, hashtags
- Defer-until date
- Link management
- Created/modified timestamps

### Edit Calendar Event
- Title, date, recurrence rule inputs, note, hashtags, color
- Created/modified timestamps

### Edit Note
- Multi-line note editing
- Optional note name/title
- Link management (add/remove URLs)
- Created/modified timestamps

## 7) Archived and Uncompleted views

- **Archived**: archived lists with select + unarchive/edit/delete actions.
- **Uncompleted**: grouped tree of uncompleted todos by list; can include collation-collected uncompleted todos (config-dependent).

## 8) Search experience

- Query matches lists, todos, todo notes, hashtags, and calendar events.
- Results grouped by object type.
- Event results include quick open/edit paths back into Calendar.
- Todo results preserve context (list/parent), note indicator, pin marker.

## 9) Hashtags and named searches

### Hashtags page
- Shows all hashtags as clickable pills.
- Mode-based interactions:
  - normal: open hashtag detail
  - delete mode: delete tag
  - customize mode: edit hashtag behavior toggles

### Hashtag customization flags
- include list items (cascade list-tagged todos into hashtag view)
- hide completed list items (affects that cascade)
- show/hide completed items toggle in hashtag detail
- hashtag detail views may also include matching calendar events

### Named searches
- Saved include/exclude hashtag queries.
- “All items” mode (ignore include set, keep excludes).
- Exclude wins if same hashtag is both included and excluded.
- Optional uncompleted-only filtering.
- Result views can include matching calendar events alongside lists/todos.
- In the current UI, the Named Searches section is configuration-gated and may be hidden.

## 10) Smart folders (advanced saved views)

Creation/edit options include:

- What to show: lists, todos, group todos by list
- Todo structure: root-only, nest subtodos, strict nesting
- Completion/archive filters
- Date range filters (modified after/before)
- Independent sort fields/directions for lists and todos
- List-scope filters (specific lists)
- Hashtag include/exclude filters with per-hashtag options:
  - include/exclude subtodos propagation
  - include list items (for include side)
- Folder filters:
  - include/exclude specific folders
  - include/exclude unfiled (root) lists
- Section order control (ordering of Lists/Events/Todos blocks)
- Folder include/exclude filters (including unfiled/root scope)
- Optional DokuWiki link behavior + date-field basis

Smart folder results page:
- Renders configurable sections (lists/events/todos)
- Supports grouping/nesting behavior
- Has selection + bulk operations
- Supports DokuWiki delete mode for links

## 11) Calendar behavior

- Timeline view over rolling date window with pagination by offset.
- Supports recurring events (RRULE style behavior).
- Recurrence future-occurrence collapse heuristic to reduce clutter.
- Completion tracked per occurrence date (not only per event).
- Add/edit/delete events, select-all, bulk delete.
- Event notes, hashtags, color support.
- Supports event subnotes, including occurrence-scoped notes for specific dates.

## 12) Folders and categories

### Folders
- Hierarchical tree of folders with collapse/expand.
- Create/move/rename/delete/reorder folders.
- Move lists between folders and root.
- Set active folder context for home/list filtering.
- Tagged-item move workflows allow bulk relocating tagged lists/items.
- Tagging can be initiated from list views, then completed from the Folders page.
- Tagged todo moves preserve subtodo trees; tagged note moves are handled during cross-list moves.

### Categories
- Create/rename/delete categories.
- Assign category to lists.
- Home view groups lists by category.

## 13) Recipe/Pantry subsystem

Recipe mode is list-level and unlocks inventory workflows:

- **Shopping list (per recipe list)**: statuses like ran out / likely low / new / in stock, per-item quantity, per-item ran-out/bought actions, and bulk mark-selected-bought.
- **Ingredient report**: stats (days since bought, cycle, likely run-out) plus reset-all-ingredients action.
- **Pantry (global)**:
  - aggregates ingredients across all recipe lists
  - supports grouping similar ingredients into canonical groups
  - suggests likely duplicates for linking
  - supports linking/unlinking items, renaming groups, and buy/ran-out events at item and group level
- **Ingredient history**: timeline of buy/ran-out/reset events.

## 14) Journal and traceability

- Journal page shows recent actions (create/update/delete/toggle/move/etc.).
- Entries include timestamp, action text, details, and deep links when entity still exists.

## 15) Customization and data portability

- Global include/exclude hashtag filters applied to home/list views.
- Export full app state as JSON.
- Import JSON with upsert behavior (update existing IDs, create new records).

## 16) Offline and sync behavior (important for PWA/Yjs rebuild)

- Offline notes:
  - local browser storage (IndexedDB)
  - create/edit/delete local-only notes
  - explicitly not server-synced
- Offline-capable client behavior:
  - local cache of data/templates/assets
  - SPA-style internal routing when offline
  - periodic/background sync attempts when online
- Data sync endpoints support full/incremental tree-diff style synchronization and server-applied modifications.

## 17) Configuration Variables (`config.py`)

Primary configuration constants and their effects:

- `PINNED_OLD_DAYS`: threshold (days) before pinned items are shown in an "older pinned" group.
- `CALENDAR_LOOKAHEAD_DAYS`: future window for Calendar occurrences.
- `CALENDAR_MAX_OCCURRENCES`: cap for generated recurrence occurrences.
- `HOME_CALENDAR_AROUND_DAYS`: ±day window for Home calendar preview.
- `SHOW_CALENDAR_ABOVE_LISTS`: controls Home calendar placement (above vs below list sections).
- `RRULE_COLLAPSE_FUTURE`, `RRULE_HIDE_FUTURE_OCCURRENCES_LIMIT`: reduce recurring-event clutter by limiting visible future occurrences.
- `SHOW_COLLATION_COLLECTED_IN_UNCOMPLETED`: include/exclude collation-collected todos in Uncompleted view.
- `COLLATION_UNCOMPLETED_COUNT_INCLUDES_COLLECTED`: include/exclude collected todos in collation uncompleted counters.
- `ENABLE_DOKUWIKI_LINKS`: global enable/disable for DokuWiki integration UI.
- `DOKUWIKI_LINK_FSTRING`: template for date-based DokuWiki links.
- `DOKUWIKI_BURST_CONFIRMATION`: click-confirmation behavior for DokuWiki confirmation tracking.
- `DOKUWIKI_ITEM_LINK_FSTRING`: template for per-item DokuWiki page links.
- `LOAD_DEFAULT_SMART_FOLDERS`: auto-load default smart folders from `default_smart_folders.json`.
- `DISABLE_OFFLINE_CACHE`: disable client-side IndexedDB/template/asset offline cache behavior.
- `SHOW_NAMED_SEARCHES`: show/hide Named Searches section on the Hashtags page.
- `SIMPLE_TODO_MODE`, `SYNC_SERVER_URL`, `CLIENT_ID`: sync/client mode settings.
- `DATETIME_FORMAT`, `DATE_FORMAT`: display formats used across templates.
- `LOCALE`, `SUPPORTED_LOCALES`: active/default language and available locales.
- `SHOW_QUICK_ADD_FORMS`: show/hide compact quick-add forms on Home and List pages.
- `SESSION_EXPIRY_DAYS`: login session cookie lifetime when auth is enabled.

## 18) Rebuild-critical behavioral rules (acceptance criteria)

1. Selection and completion semantics remain distinct.
2. Bulk operations preserve current scope/page context.
3. Subtodo hierarchy, collation collection, and manual ordering behave predictably.
4. Deferral and alarm features remain first-class and editable from both detail and list contexts.
5. Hashtag include/exclude precedence remains stable (exclude wins).
6. Smart folder filtering order and section rendering are deterministic.
7. Calendar completion is occurrence-based.
8. Offline notes remain local-only unless intentionally redesigned.
9. Export/import round-trip preserves IDs/relationships where possible.
10. Journal remains a user-visible audit trail of major actions.

---

This summary is intentionally implementation-agnostic and can be used as a product contract for a rewrite (e.g., CRDT/Yjs + PWA) while preserving user-observed behavior.
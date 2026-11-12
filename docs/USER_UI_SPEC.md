# Simple Todo — User Interface Specification (User Perspective)

This document describes the application *as experienced by a user* through its web UI. It is intentionally implementation- and framework-agnostic.

## 1) Overall concepts

### 1.1 Items you interact with

- **Lists**: containers for todos.
- **Todos**: tasks inside a list. Todos can have:
  - Completion state (done / not done)
  - Optional note
  - Optional hashtags
  - Optional pin state
  - Optional parent/child relationship (subtodos)
- **Datetime dividers**: a timestamped divider line inside a list used to visually separate “older” items below from “newer” items above.
- **Hashtags**: tags that can be attached to lists and todos.
- **Named searches**: saved searches composed of selected hashtags, optionally “uncompleted only”.
- **Smart folders**: saved views that can show lists and/or todos using filters (hashtags, list scope, completion, and modified date range) and sorting.
- **Calendar events**: dated items shown in a chronological list, optionally recurring.
- **Offline notes**: local notes stored on your device that are available even without an internet connection.

### 1.2 Timestamps & formatting (display)

- Dates/datetimes are shown using the app's **configured date format settings**.
- Default formats are typically ISO-like (e.g. `YYYY-MM-DD` and `YYYY-MM-DD HH:MM`) unless reconfigured.
- Times are displayed in **the server’s local time zone**.

### 1.3 Two kinds of checkboxes

Many rows include two different checkbox styles:

1. **Selection checkbox** (round): used for bulk operations (pin, delete, edit, archive, etc.).
2. **Completion checkbox** (standard square): toggles completion/done state.

### 1.4 Selection UX

- When you tick selection checkboxes, an **“N selected”** counter updates.
- If *exactly one* item is selected, a small “Created / Modified” line appears for that item.
- Bulk operations generally require selecting one or more items first.

### 1.5 Confirmation dialogs

- Bulk deletion actions prompt a confirmation (e.g., “Delete N item(s)?”).

### 1.6 Authentication (optional)

- The app can be protected using htpasswd-backed authentication.
- For normal web UI usage, users are sent to a **Login page** and authenticated via a session cookie (with Logout support).
- HTTP Basic Auth headers are still supported for API/sync-style clients.
- Some “browser probe” icon routes and static assets do not trigger repeated auth prompts.

### 1.7 Todo Colors

Todos can have a custom color assigned to them.

- The color is applied to the todo title text throughout the application (Lists, Smart Folders, Search Results, Hashtag Views, etc.).
- The color can be set when editing a todo.

## 2) Global navigation

The home page provides links to major sections (typically at the top):

- **Search**
- **Calendar**
- **Hashtags**
- **Smart folders**
- **Folders**
- **Archived**
- **Collations**
- **Customization**
- **Logout** (shown when session auth is enabled)

Most subpages include a “Back to Lists” or similar link.

## 3) Home (Lists overview)

### 3.1 Purpose

The home page shows:

- Active (non-archived) lists
- Pinned lists and pinned todos
- An **Active Alarms** section for todos currently within alarm windows (when present)
- A **Resuming Items** section for items whose deferral has ended (when present)
- A read-only calendar preview (optional)
- Quick creation of new lists
- Bulk operations over lists and todos
- Links to **Offline Notes** and **Journal** (below the lists)

### 3.2 Create New List

A collapsible section allows you to create a list:

- **Name** (required)
- **Hashtags** (optional)
  - You can type tags with or without `#`
  - You can also add from an “existing hashtags” dropdown (if there are any)

When quick-add forms are enabled by configuration, Home also shows an always-visible compact create-list form with optional multi-select existing hashtags.

Submitting creates the list and returns you to the home view.

### 3.2A Daily Plan Quick Create

Home also includes a quick-create shortcut for **Daily Plan** lists:

- A plus icon + day-offset selector (0..N days) creates a list named for that target date.
- Daily Plan quick-created lists are created with **manual ordering enabled**.
- If you are currently in a folder context, the new daily plan list is created in that folder.

### 3.3 List display

Each list row includes:

- Selection checkbox (round)
- Completion checkbox (toggles list completed)
- Optional pin indicator 📌
- Optional collation indicator (colored dot)
- List name (link to list page)
- An **uncompleted todo count** shown immediately after the list name
- Hashtag pills (links to hashtag pages)

Lists are ordered by **created time** by default (newest first), with an option to switch to modified-time sorting.

**Sorting**: A control at the bottom of the list allows toggling the sort order between **Modified** and **Created**.

Notes:

- The uncompleted count is the number of todos in that list that are not completed.
- For **collation lists**, this count may (depending on configuration) include uncompleted todos that are *collected into* the collation list.

### 3.4 Pinned section

If there are pinned items, a **Pinned** section appears near the top.

- Pinned lists and pinned todos appear here.
- Older pinned items may be grouped into a separate section (e.g., “Pinned (older than N days)”).

Pinned todo rows include:

- Selection checkbox
- Completion checkbox
- 📌 pin marker
- Todo title
- Optional **note icon** 📝 (if the todo has a note). Clicking the icon opens that todo’s edit page immediately.
- Hashtag pills
- “(in ListName)” context link

Pinned list rows also show the uncompleted todo count after the list name.

### 3.5 Calendar preview (home page)

The home page may show a **Calendar** preview section (placement can vary based on configuration).

- It shows calendar event occurrences within a window of **± N days** around “today”.
- Each row shows:
  - a **read-only (disabled)** checkbox indicating completion
  - the occurrence date
  - event title
    - Non-recurring events are displayed in a distinct color (e.g., teal) to differentiate them from recurring series.
  - “(recurring)” label if applicable
  - note preview if present
- It also includes a link to open the full Calendar page.

### 3.6 Active Alarms (home page)

When there are alarmed todos currently in an active alarm window, an **Active Alarms** section appears on the home page.

- Each row links to the list containing the todo.
- Rows include alarm timing context and may show note/hashtag context similarly to other todo rows.

### 3.6A Resuming Items (home page)

When there are lists/todos whose deferral period has ended, a **Resuming** section appears.

- Resuming lists and todos are shown with quick links back to their list context.
- This section helps surface items that have become actionable again.

### 3.7 Offline Notes and Journal Links

Below the lists (and before the sort controls), there are links to:

- **Offline Notes** (local notes that persist without an internet connection)
- **Journal** (recent activity log)

### 3.8 Home page Operations

When there are items present, Operations appear.

Selection controls:

- **Select All** / **Select None** (applies to selectable items on the page)

Todo operations:

- **Pin** / **Unpin**
- **Edit** (requires exactly one todo selected)
- **Delete**

List operations:

- **Pin** / **Unpin**
- **Ar.** (archive)
- **Unar.** (unarchive)
- **Edit** (requires exactly one list selected)
- **Delete**

## 4) List page (View a list)

### 4.1 Header

The list page shows:

- List name
- List hashtags (as clickable pills)
- Created / Modified dates

The header also includes navigation links across active lists:

- **Back to Lists**
- **1st**: goes to the first list in the same order as the home page (only unarchived + uncompleted lists)
- **Prev** / **Next**: move through unarchived + uncompleted lists in the same order as shown on the home page
- An indicator `X / Y` showing the current position among unarchived + uncompleted lists.
  - `Y` counts only lists that are unarchived and uncompleted.
  - If the current list is archived or completed, the indicator may show `? / Y`.

### 4.2 Add Todo

A collapsible section allows adding a new todo:

- Title (required)
- Hashtags (optional)
  - You can type tags with or without `#`
  - You can also add from an “existing hashtags” dropdown

When quick-add forms are enabled by configuration, List view also shows an always-visible compact add-todo form with optional hashtag multi-select.

### 4.2A Add Note

A collapsible section allows adding a list-level note:

- Optional name/title
- Multi-line text body

Notes can later be selected and edited/deleted via operations.

### 4.3 Add datetime divider

A collapsible section allows creating a **datetime divider** (header) for the list.

- When created, it appears as a timestamp label with a horizontal line extending across the screen.
- Divider semantics in a newest-first list:
  - Todos created **after** the divider appear **above** it.
  - Todos created **before** the divider appear **below** it.
- **DokuWiki Links**: If enabled in configuration, datetime dividers (and synthetic dividers) may display a DokuWiki icon.
  - Clicking the icon opens a corresponding page in your DokuWiki instance (e.g., a daily journal page).
  - The icon color indicates status:
    - **Grey/Faded**: Not yet confirmed (clicked).
    - **Green**: Confirmed (clicked recently).
  - Clicking the icon "confirms" the item (and potentially other items in the same group) as having been processed/journaled.

Dividers can be selected and removed via Operations.

**Automatic (synthetic) dividers**:
If a list has at least one user-created divider, but contains todos older than the oldest divider, the system may automatically display a "synthetic" divider at the bottom to visually group the oldest items. This divider is presentational only and cannot be deleted directly.

### 4.4 Show completed todos

A checkbox toggles whether completed todos are shown in the list.

- When off, completed todos are hidden.
- Subtodos follow the same rule: hidden if completed when “show completed” is off.

### 4.5 Collation toggle

A checkbox toggles whether this list is a **collation list**.

- Collation lists can “collect” todos from other lists.
- Collation lists have a colored dot indicator.

### 4.6 Todo display (including subtodos)

Todos are shown according to the list’s current sort mode (newest-first by default, optionally oldest-first, or manual order) and may be nested as subtodos.

Each todo row includes:

- Selection checkbox (round)
- Completion checkbox (toggles completion)
- Optional 📌 if pinned
- Title text
- Optional note icon 📝 (if it has a note). Clicking the icon goes directly to edit.
- Hashtag pills

Subtodos:

- A todo may have child items (subtodos).
- Subtodos are displayed indented under their parent.

Notes in list flow:

- List-level notes and todo-attached notes can appear inline in the list flow.
- Notes are selectable rows and can be edited/deleted via standard operations.

### 4.7 Collation dots (collecting todos)

If there are existing collation lists (and the current list is not itself a collation list), each todo row can show one dot per collation list.

- Clicking a dot toggles whether that todo is included in that collation list.
- The dot is filled when the todo is included.
- After toggling, the page refreshes to reflect the change.

### 4.8 Collected Todos (when viewing a collation list)

If the current list is a collation list, a **Collected Todos** section appears.

- Shows todos collected from other lists.
- Each collected todo can be removed from this collation list via the colored dot button.

Collected todos are selectable, and the list Operations section supports acting on selected collected todos.

### 4.9 Additional List Controls

At the bottom/top controls area of the list view, additional controls may appear:

- **Sort Order**: A toggle to switch the list sort direction between **Newest First (Desc)** and **Oldest First (Asc)**.
- **Dividers Toggle**: A toggle to enable or disable the display of datetime dividers for this list.
- **Show Delayed**: A toggle to include/exclude delayed items in the current list view.
- **Manual Ordering**: A toggle that enables manual reordering mode for todos.
- **Recipe Mode** (when available): enables recipe/ingredient workflows for the list.
- **Collation Interleave** (for collation lists): toggles whether collected todos are interspersed with native todos.
- **Category assignment**: a list-level category selector (when categories exist).
- **DokuWiki Link Delete Mode**: A toggle button (trash icon) that, when active, allows you to delete DokuWiki links from datetime headers.

### 4.10 List page Operations

Selection controls:

- **Select All** / **Select None**
- Selected item count
- Single-selection “Created/Modified” display (for one selected item)
- **Tag** selected items for later move actions in the Folders page workflow

Todo operations:

- **Pin** / **Unpin** (for selected todos)
- **Edit** (requires exactly one todo selected)
- **Delete**
- **Defer** (inline date form for a selected todo; exactly one todo required)
- **Set Alarm** (inline alarm mode/date-time form for a selected todo; exactly one todo required)
- **Edit Hashtags** (inline update for a selected todo; exactly one todo required)
- **+ Subnote** (requires selecting exactly one todo; opens a note-create form linked to that todo)
- **Enable/Disable DokuWiki Page** (for selected todos, when DokuWiki item-link mode is enabled for the list)

Recipe-mode list actions (via More Actions menu):

- Open Shopping List
- Open Ingredient Report
- Reset all ingredient statuses
- Bulk mark selected ingredients as bought/ran out

Subtodo operations:

- **+ Subtodo** (only shown on non-collation lists)
  - Requires selecting **exactly one** todo.
  - Reveals a small form to add a subtodo under the selected parent.

Divider operations:

- **Delete Headers** deletes selected datetime dividers.
  - This button is only shown if the list currently has at least one datetime divider.

## 5) Edit Todo

Accessible by:

- Selecting a todo and clicking **Edit**
- Clicking the note icon 📝 next to a todo

Edit form fields:

- Title
- Note (multi-line)
- **Color**: A color picker to choose the text color for the todo title.
- Hashtags (text field; tags can be entered with or without `#`)
- Optional dropdown to insert existing hashtags
- **Deferral** (defer-until date)
- **Alarm settings** (none, explicit range, or target time ± minutes)
- Link management (add/remove links)

Displays created/modified dates at the bottom.

## 6) Edit List

Accessible by selecting a list and clicking **Edit**.

Edit form fields:

- Name
- Hashtags (text field)
- Optional dropdown to insert existing hashtags
- **Deferral** (defer-until date)
- Link management (add/remove links)

Displays created/modified dates at the bottom.

## 6.1) Edit Note

Accessible via note edit routes; notes are commonly created from list/todo/calendar note entry points and then edited on the Edit Note page.

Edit form fields:

- Note body (multi-line)
- Optional note name/title
- Link management (add/remove links)

Displays created/modified dates.

## 7) Archived Lists

Shows lists that have been archived.

Navigation links: **Back to Lists** | **Hashtags** | **Uncompleted**

For each archived list:

- Selection checkbox
- Completion checkbox
- List link
- Hashtag pills

Operations:

- **N selected** counter
- **Select All** / **Select None**
- If exactly one list is selected, a “Created / Modified” line is shown
- **Edit** (requires exactly one list selected)
- **Unarchive**
- **Delete**

## 8) Uncompleted view

Shows only **uncompleted** lists/todos, grouped by list.

- Lists are shown as headings with completion toggles.
- Under each list, uncompleted todos (and their uncompleted subtodos) are shown.
- Todo rows include the note icon 📝 if a note exists.

Optional collation behavior:

- Depending on configuration, collation lists may also appear here when they have **uncompleted collected todos**.
- When shown, a small `Collected` label may appear under the collation list heading, followed by the collected uncompleted todos.

Operations (for lists and todos):

- Pin/Unpin todos
- Edit/Delete lists and todos

## 9) Search

A general search page.

- Input field: search across lists, todos, todo notes, and hashtags.
- Results split into:
  - Matching lists
  - Matching events
  - Matching todos

Event results show:

- Event title/date context
- Quick links to edit the event or open Calendar

Todo results show:

- 📌 if pinned
- 📝 if a note exists (click opens todo edit)
- Hashtag pills
- Context: which list (and parent, if it’s a subtodo)

## 10) Smart folders

Smart folders are saved “views” that return matching **lists**, **todos**, or both.

You can get to them from the home page navigation link **Smart folders**.

### 10.1 Smart Folders (overview)

This page shows:

- Existing smart folders (each is a link)
- A compact summary for each folder (what it shows, filters, counts)
- Selection checkboxes for bulk operations

Operations:

- **Select All** / **Select None**
- **Edit** (requires exactly one folder selected)
- **Delete** (bulk delete supported)

### 10.2 Create / Edit Smart Folder

You can create a new smart folder or edit an existing one. The form provides the following options:

#### What it shows

- **Todos** (enabled by default)
- **Lists**
- **Group todos by list** (enabled by default)

Todo structure options:

- **Root todos only**
- **Nest subtodos**
  - When enabled, subtodos are shown nested under their parents.
  - If a todo's parent is also in the results, the child is only shown nested (not at the top level).

#### Filters

- **Uncompleted only**
- **Include archived lists**
- **Modified after** (date)
- **Modified before** (date)

Notes:

- The modified date range applies to items’ *modified time*.
- If you set both “modified after” and “modified before”, the smart folder shows items modified within that window.

#### Sorting

You can choose sort fields and direction separately for lists and todos:

- Lists sort by: **Modified** or **Created**, ascending or descending
- Todos sort by: **Modified** or **Created**, ascending or descending

When grouping todos by list:

- The *group order* (which list appears first) follows the list sort.

#### Limit to specific lists (optional)

You can restrict the smart folder to only include selected lists:

- A checklist of lists is shown, each with its hashtags.
- If you don’t select any lists, the folder applies to **all lists** (subject to other filters).

#### Hashtag filters

You can filter by hashtags in two ways:

- **Include hashtags**: select hashtags that items *should have*
- **Exclude hashtags (NOT)**: select hashtags that items *must not have*

For each hashtag, you can also check **"w/ subtodos"**:
- **Include w/ subtodos**: Includes items with the hashtag, AND includes subtodos of parents that have the hashtag.
- **Exclude w/ subtodos**: Excludes items with the hashtag, AND excludes subtodos of parents that have the hashtag.

For include-hashtag entries, an additional option may appear:
- **inc. list items**: Include todos from lists that match the hashtag rule.

There is also an **All items** option:

- If “All items” is checked, the include-hashtag selection is ignored.

If the same hashtag is both included and excluded:

- **NOT wins** (the item is excluded).

Additional Smart Folder options available in create/edit:

- **Strict nesting** (only matching subtodos are shown in nested output)
- **Section order** control for results sections (Lists / Events / Todos)
- **Folder filters** (include/exclude specific folders and include/exclude unfiled/root)

### 10.3 Viewing a smart folder

Clicking a smart folder opens its results page. Near the top it summarizes the folder configuration:

- Whether it’s showing lists, todos, or both
- Whether it’s uncompleted-only
- Whether it includes archived lists
- Modified-after / modified-before constraints (when set)
- Which lists it is limited to (or “Lists: All”)
- Included hashtags (with "(w/ subs)" indicator if applicable)
- Excluded hashtags shown as `NOT #tag` (with "(w/ subs)" indicator if applicable)

#### DokuWiki Link Delete Mode

A toggle button (trash icon) is available at the top right. When active, it allows you to delete DokuWiki links from items in the smart folder results.

#### Lists section (optional)

If the folder is configured to show lists, you’ll see:

- Select checkbox (round)
- Completion checkbox (square)
- List name (link to the list)
- Hashtag pills
- **DokuWiki Link**: If present, a DokuWiki icon links to the corresponding page.

#### Events section (optional)

If section order includes Events, matching calendar events can be shown in smart folder results.

- Event rows include date/title context
- Rows provide quick links to edit/open the calendar event

#### Todos section (optional)

If the folder is configured to show todos:

- Each todo row includes selection + completion checkboxes
- 📌 appears if the todo is pinned
- 📝 note icon appears if a note exists (clicking goes to edit)
- Hashtag pills appear on the row
- **DokuWiki Link**: If present, a DokuWiki icon links to the corresponding page.
- **Context**:
  - If **Group todos by list** is ON: No context suffix is shown (since the list header provides context).
  - If **Group todos by list** is OFF: A folder icon 📂 links to the containing list.

Grouping / nesting behavior:

- If **Group todos by list** is on, todos appear under a heading for each list.
- If **Nest subtodos** is on, subtodos appear indented under their parent.

#### Smart folder Operations

If there are any results, an Operations section appears:

- **N selected** counter
- **Select All** / **Select None**
- If exactly one item is selected, a “Created / Modified” line is shown

Bulk actions:

- Todos: **Pin**, **Unpin**, **Edit** (requires exactly one), **Delete**
- Lists: **Edit** (requires exactly one), **Delete**

## 11) Hashtags

Two main areas:

### 11.1 Named Searches

Displays existing named searches (when enabled by configuration).

For each named search:

- Name (click to run it)
- Optional label: “(uncompleted only)”
- List of included hashtags (or "All items")
- List of excluded hashtags (NOT)
- Delete button

### 11.2 Create Named Search

Available when Named Searches are enabled in configuration.

Form fields:

- Search name
- Checkbox: “Uncompleted items only”
- **All items** checkbox (ignores include-hashtag selection)
- Checklist of hashtags to **include**
- Checklist of hashtags to **exclude** (NOT)

Submitting creates the named search.

### 11.3 Hashtag list

Displays all hashtags:

- Each hashtag is a pill link
- Deletion is done via a page-level **Select to delete** mode (click a hashtag in delete mode to remove it)

### 11.4 Hashtag customization mode

The hashtags page provides a “Select to customize” mode for per-hashtag behavior flags.

Per-hashtag options include:

- **inc. list items**: include todos from lists tagged with the hashtag
- **exclude completed lists' todos**: when list-item inclusion is on, hide todos coming from completed lists

## 12) Hashtag detail view

Shows lists, todos, and matching calendar events for a specific hashtag.

Controls:

- Checkbox: “Show completed items” (toggles whether completed items are included)

### 12.1 Lists section

- Selectable and completable list rows
- List name (link)
- Hashtag pills

### 12.2 Todos section

- Selectable and completable todo rows
- 📌 pin marker if pinned
- 📝 icon if note exists (click opens edit)
- Hashtag pills
- Context: list (and parent, if any)

### 12.3 Operations

- **N selected** counter
- **Select All** / **Select None**
- If exactly one item is selected, a “Created / Modified” line is shown

Bulk actions:

- Todos: **Pin**, **Unpin**, **Edit** (requires exactly one), **Delete**
- Lists: **Edit** (requires exactly one), **Delete**

## 13) Named search results

Shows results for a saved search (when Named Searches are enabled in configuration).

- Displays the hashtags included in the named search (or "All items")
- Displays excluded hashtags (NOT)
- May be filtered to “uncompleted only”

Sections:

- Matching Lists
- Matching Events
- Matching Todos

Operations:

- Pin/unpin/edit/delete for todos
- Edit/delete for lists
- Matching events are shown as result rows with quick navigation/edit links

## 14) Calendar

Offers List, Month and Week views and remembers the selected view. The list shows occurrences within a date window. The week runs Monday–Sunday, with previous/next week navigation and a Today link.

The weekly view places all-day events in a separate row above a 24-hour grid with one-hour slots. Select a slot to prefill a new event’s date and time. Timed events display their exact start time; an optional duration determines their height. Events without a duration use a one-hour block without storing an assumed duration. Overlapping events are allowed and appear side by side. Events with a duration that crosses midnight continue on the following day. The full day and all-day row expand naturally so the page handles vertical scrolling. Narrow screens can scroll the calendar horizontally, with time labels staying visible.
- Displays the current date range at the top (e.g., "Showing YYYY-MM-DD → YYYY-MM-DD").

### 14.1 Add Event

A collapsible “Add Event” form with:

- Title
- Date
- Optional start time (blank means all-day, including for existing events)
- Optional duration in whole minutes, from 1 to 10080 (timed events only)
- Optional note (single line text)
- Optional recurrence settings:
  - Frequency (None/Daily/Weekly/Monthly/Yearly)
  - Interval
  - Weekly day selection (for weekly frequency)

The recurrence UI builds a recurrence rule automatically.

### 14.2 Occurrences list

Each occurrence row includes:

- Selection checkbox (for bulk actions on the underlying event)
- Completion checkbox (marks that specific occurrence completed)
- Occurrence date
- Title
  - Non-recurring events are displayed in a distinct color (e.g., teal) to differentiate them from recurring series.
- Labels:
  - “(recurring)” if it’s from a recurrence
  - Note preview if present

### 14.3 Calendar Operations

- **N selected** counter
- **Select All** / **Select None** (events)
- If exactly one event is selected, a “Created / Modified” line is shown
- **Edit** (requires selecting exactly one event)
- **Delete**
- **+ Subnote** (requires selecting exactly one event)
  - Creates a note attached to the selected event.
  - Can optionally be scoped to the selected occurrence date only.

## 15) Edit Calendar Event

Fields:

- Title
- Date
- Optional start time and duration (same behavior as create)
- Recurrence settings (same UI as create)
- Note (multi-line)
- Hashtags
- Color

Shows created/modified dates.

**Delete Event** opens a confirmation dialog before deleting the event, its notes and completion history. For recurring events, the dialog explicitly confirms deletion of the entire series. Cancelling keeps the event; confirming returns to the calendar and preserves the displayed week or month.

## 16) Collations

A simple informational page listing all collation lists.

- Each collation list shows its colored dot and a link to open it.

## 17) Offline Notes

A feature to take notes that are stored locally on your device and persist even without an internet connection.

- **Access**: Click "Offline Notes" on the home page (visible when offline or via direct link).
- **Storage**: Notes are stored in your browser's IndexedDB. They are **NOT** synced to the server.
- **Functionality**:
  - Create new notes.
  - Edit existing notes.
  - Delete notes.
  - Notes have a title and content.
  - Notes show their last modified timestamp.

## 17A) Additional implemented sections

### Customization

- Global include/exclude hashtag filters that affect Home/List views.
- Data export (JSON) and import (upsert behavior).

### Folders

- Tree view with create/move/rename/delete/reorder operations.
- Active folder context for Home filtering.
- Tagged-item move workflows.

### Categories

- Create/rename/delete categories and assign categories to lists.

### Journal

- Recent activity log with action/details/time and context links.

### Pantry / Shopping / Ingredient Report

- Recipe-mode lists expose shopping list + ingredient report pages.
- Shopping list supports per-item and bulk “mark bought” actions, per-item “ran out”, and quantity inputs.
- Ingredient report includes status-oriented ingredient insights and a “reset all ingredients” action.
- Pantry aggregates ingredients across recipe lists, including grouping/suggestions/history.
- Pantry supports creating/renaming ingredient groups, linking/unlinking items, and group-level bought/ran-out actions.

---

## 18) (Requested / Proposed) Node workspace

This section describes a requested feature that does not exist in the current UI.

### 17.1 Purpose

The node workspace is a visual area where you can organize your work as **nodes** that can be moved and nested. It is meant to support flexible organization beyond strict “todo inside list”.

Typical uses:

- Create “projects” or “areas” as container nodes, then drag related list/todo/note nodes into them
- Keep reference material as note nodes next to actionable todos
- Restructure work quickly by dragging nodes instead of editing multiple forms

### 17.2 Node types

All nodes share:

- A title
- Created / modified timestamps
- Optional hashtags
- A selection checkbox (for bulk actions)

Node types:

- **Container node**
  - Organizational only
  - Can contain other nodes (including other containers)
  - Does not have a completion checkbox
- **Note node**
  - A freeform note body (multi-line)
  - Does not have a completion checkbox
- **List node** (existing list)
  - Represents today’s list
  - May still have a completion checkbox (same semantics as lists today)
- **Todo node** (existing todo)
  - Represents today’s todo
  - Has a completion checkbox
  - May have a note
  - May have subtodos

### 17.3 Creating nodes

From within the node workspace you can create:

- A new **container node** by providing a title
- A new **note node** by providing a title and (optionally) a note body
- A new **list node** (same fields as creating a list)
- A new **todo node** (same fields as creating a todo)

New nodes are placed into the currently focused container (if any), otherwise they appear at the top level of the workspace.

### 17.4 Viewing and editing nodes

Selecting a node shows its details and available actions.

Common actions:

- Rename
- Edit hashtags
- View created/modified timestamps

Type-specific actions:

- Todo nodes: toggle completion, edit note, manage subtodos
- List nodes: open the list view (if the list-page UI remains available)
- Note nodes: edit note body
- Container nodes: expand/collapse contained nodes

### 17.5 Moving nodes (drag and drop)

You can reorganize by dragging nodes:

- Drag a node onto a **container node** to move it inside that container.
- Drag a node out of a container to move it to the container’s parent (or to the workspace root).
- Drag within a container to reorder nodes.

When a drag would create an invalid structure, the UI prevents the drop (for example, dropping a container node into itself).

If the UI supports multi-select dragging:

- Dragging one selected node can move all selected nodes together.

### 17.6 Nesting and relationships

Container nesting:

- Containers can contain other containers.
- Collapsing a container hides its children.

Todo nesting:

- Todo nodes may still have subtodos (as they do today).
- Subtodo nesting is distinct from container nesting: a todo can be inside a container, and also have subtodos under it.

### 17.7 Searching and saved views

Smart folders and search continue to work across node types:

- Smart folders can match list nodes and todo nodes as they do today.
- In a node-aware version, smart folders may also be able to include note nodes and container nodes.

### 17.8 Node workspace Operations

Operations mirror the existing selection-based approach:

- **N selected** counter
- **Select All** / **Select None**
- Single-selection “Created / Modified” display

Bulk actions depend on node type:

- Todo nodes: pin/unpin, edit, delete, toggle completion
- List nodes: edit, delete
- Note nodes: edit, delete
- Container nodes: rename, delete (with confirmation if it contains children)

---

*End of user-facing specification.*

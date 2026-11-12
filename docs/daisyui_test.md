# daisyui_test prototype

Run the application normally and open `/app` (under the normal app prefix
when hosted in a subdirectory). `/`, `/calendar`, and `/bills` redirect to the
corresponding new frontend pages, preserving query parameters. The original pages
remain at `/classic`, `/classic/calendar`, and `/classic/bills`.
Both frontends use the same database, authentication, settings, and journal;
actions modify the real application data.

- `daisyui_test.py`: isolated HTML/HTMX router factory, registered in `main.py`.
- `daisyui_test_list.py`: list detail rendering and HTML actions, using the existing
  mutation functions through the router factory.
- `daisyui_test_edit.py`: list, task, and note editors with HTML save/delete and
  attached-link actions, reusing the existing mutation functions.
- `main.get_home_context` and `main.get_list_context`: queries shared by both
  frontend renderers.
- `item_actions.py`: existing bulk mutation logic shared by JSON and HTML routes.
- `templates/daisyui_test/`: full page, workspace fragment, row macros, and feedback.
- `static/daisyui_test/`: layout CSS, local interaction state, and vendored daisyUI
  5.7.50 CSS with its MIT license. HTMX and URL handling reuse existing local assets.

The stylesheet comes from `https://cdn.jsdelivr.net/npm/daisyui@5.7.50/daisyui.css`.
There is no Node build step or runtime CDN dependency. Layout CSS uses ordinary CSS
alongside daisyUI component classes. The default theme is dark with pure black
page, panel, menu, and form backgrounds; mint accents mark interactive states.

The page preserves list creation and hashtags, daily plans (offsets 0–9), folder
scope, category grouping, user/created/modified sort, reorder navigation, alarms,
resuming items, recent/older pins, configured calendar previews and occurrence
notes, completion, and existing bulk actions. “Mark for moving” uses the existing
browser-local tagged-items storage consumed by Folders. Calendar completion remains
read-only here. List links open `/daisyui_test/list/{id}` in the same frontend.
List detail preserves nested tasks and notes, date dividers and wiki confirmations,
completion/deferred visibility, collation membership, manual ordering, category,
recipe controls, and the selection workflow. Task creation, subtasks, task/list
notes, deferral, alarms, hashtags, and bulk actions update through HTML fragments.
Selections are scoped to the current list, including its collected
records; subtasks and notes added to collected tasks belong to the source list.

List, task, and note editing stays in the prototype at
`/daisyui_test/{list|todo|note}/{id}/edit`. Compact forms support names, task notes,
hashtags, custom colours, deferral, alarm ranges or tolerances, attached links,
and confirmed deletion. Saves return to the originating overview or list with
its sort and folder context; collected tasks keep their source membership.
Validation errors preserve input, and adding/removing links retains unsaved form
drafts across HTMX swaps. Stored note URLs render as clickable links with other
content escaped. Search, calendar, folders, hashtags, shopping/ingredient reports,
and other navigation destinations still use the existing UI.

HTML mutations return a refreshed workspace so duplicated pinned/list rows and
remaining-task counts stay synchronized. A small script retains drafts, selected
item IDs, and expanded sections across swaps. Folder changes clear selection.
Sort/folder navigation updates browser history. Forms also support ordinary POST
redirects; JavaScript is needed for selection and action panels. Navigation uses a compact
top toolbar with an overflow menu. Rows keep metadata and tags inline when space
allows, with tighter spacing on desktop and comfortable controls on touch screens.

The prototype interface is currently English, with existing translated labels used
where available. Dates use the application's configured formats.

Verification: `python3 -m pytest tests/test_daisyui_test.py tests/test_daisyui_list.py tests/test_daisyui_edit.py tests/test_list_user_ordering.py tests/test_reverse_proxy.py tests/test_task_moves.py`.
The prototype tests use an isolated in-memory database and launch their own local
server for Playwright; they do not require a running development server.

Smart folders at `/app/smart-folders` use the same editor for creation and editing.
Choose item types, add removable tags or text hashtags to include/exclude, then
choose folder scope and optional date/completion filters. Included rules match
any or all of the chosen tags and hashtags; exclusions always win. An empty
include selection matches everything allowed by the other filters. Text hashtags
are case insensitive and include notes attached to the matching item. A task's
notes do not make its list match. Unused hashtags can be typed into the editor.
Folder scope applies to lists and tasks; calendar events use the tag, hashtag,
completion and date filters. Layout and sorting are in a separate expandable panel.

New folders can start from an editable preset: open tasks with `#work`, all open
tasks, or lists with `#work`. Presets only fill the editor and preview; they do
not create records until Create is pressed. Change the hashtag chips or any other
rule before saving. The task preview explains each sample's matching sources:
its own text or attached tag, a parent/ancestor task, or its list. These labels
evaluate the same SQL expressions used by the matching query. With no included
tag/hashtag rules, samples explicitly say there is no tag or hashtag filter.

The tag/hashtag, folder, folder-exception, and list pickers filter their choices
as you type, without sending draft requests until an item is selected. Search is
case insensitive; folder paths and lists can be searched using multiple words
or slash-separated paths. List choices show their folder path to distinguish
identical names. Arrow keys move through choices, Enter adds the highlighted
choice, Escape closes the menu, and Tab moves to the next field. Selected chips
are omitted from the choices and become available again when removed. The same
forms retain native select controls when JavaScript is disabled.

The editor posts rule changes to a draft endpoint and shows matching item counts
and sample names. Drafts are rolled back, including association changes, and do
not create journal entries. Only Create/Save commits the rules. Schema upgrades
add hashtag arrays, match mode, event visibility, and inheritance/folder options
without deleting existing smart folders or their tag associations. Inheritance is
stored only in `smart_folders.include_subtodos` and `include_list_items`, shared by
both editors, matching, defaults, export/import, and sync. Subtask inheritance
applies to included and excluded rules; list inheritance applies to includes.
Mixed include/exclude folder scopes are retained as exceptions.

On upgrade, enabled legacy per-tag flags are combined with ANY into the common
folder switches. The old association columns are then removed transactionally,
and their history triggers rebuilt; IDs, tag selections, metadata, and historical
snapshots are retained. This conversion can broaden a formerly mixed per-tag
configuration to use the same setting for every rule. It runs once, so a switch
turned off cannot be re-enabled by a later startup. Older import/sync/form inputs
are normalized at the boundary; explicit folder values (including false) take
precedence, and partial JSON updates preserve omitted settings. New exports put
inheritance flags only on the folder. A downgrade to code expecting the removed
columns requires restoring the pre-upgrade database backup.
The new fields are included in export/import and sync. Verify with
`python3 -m pytest tests/test_smart_folder_settings.py tests/test_daisyui_smart.py tests/test_daisyui_texthashtags.py tests/test_import_relationships.py tests/test_entity_history.py`.

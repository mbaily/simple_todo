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

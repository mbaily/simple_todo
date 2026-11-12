# HTMX 4 assessment — 2026-11-11

> **Status (later on 2026-11-11):** the native migration below has been applied to
> master with HTMX 4.0.0 pinned at `static/daisyui_test/htmx-4.0.0.min.js`. The
> original Folders and Reorder pages still load the shared 1.9.12 asset. The extra
> browser checks now live in `tests/test_daisyui_htmx4.py`, and the browser fixture
> reads `PLAYWRIGHT_BROWSER` (`chromium`, `firefox` or `webkit`). The sections below
> describe the assessment as it was run.

HTMX 4 is feasible for the daisyUI prototype with a small, explicit migration.
Do not deploy an unmodified 4.0.1 while supporting older Chromium browsers:
the published build fails to parse responses on our Chromium 143 test browser.
Version 4.0.0 avoids that particular regression and passes the prototype's browser
workflows after migration. It does not include the other fixes released in 4.0.1.

The deployed application still uses 1.9.12. All runtime experiments are isolated
in `/tmp/simple-todo-htmx4-assessment`, on local branch
`assessment/htmx-4.0.1`, based on commit `3057cc2`. The worktree currently selects
4.0.0; both unmodified upstream assets are retained for comparison. Nothing from
the experiment has been deployed or pushed.

## Evidence

| Experiment | Result |
| --- | --- |
| Replace 1.9.12 with unmodified 4.0.1, Chromium 143.0.7499.4 | First browser workflow fails: POST saves, HTML parsing throws, page does not refresh. |
| Add the official 2.x compatibility extension, Firefox 144.0.2 | Still fails: existing handlers access `event.detail.xhr`, which no longer exists. |
| Native app migration, unmodified 4.0.1, Firefox 144.0.2 | All 10 existing prototype browser tests pass. |
| Same migration, unmodified 4.0.0, Chromium 143.0.7499.4 | All 13 browser tests pass: the 10 existing workflows plus HTTP 503 handling, network failure rollback, and pending-submit/duplicate-request protection. |

The wider 4.0.0 run finished with 94 passing tests and one failing task-move
case, `test_task_move_keeps_all_descendant_notes_and_survives_old_list_deletion[child]`.
That same failure reproduces on the unchanged main checkout with HTMX 1.9.12;
the task-move suite also passes on an isolated rerun of the experiment. Treat it
as a separate, intermittent backend issue, not evidence that the upgrade is fully
regression-free. No production data was used for these tests.

The 4.0.1 parser failure matches the open
[upstream issue #4182](https://github.com/bigskysoftware/htmx/issues/4182).
4.0.1 passes a second argument to `Document.parseHTMLUnsafe`, which older Chromium
rejects before the fallback can run. 4.0.0 uses the one-argument call. Our local
test confirms the failure on Chromium 143; the issue reports Chrome 124–145.

## Required app changes

The official checker reports 28 findings in the current prototype: 13 attribute
renames, nine inheritance findings, and six event renames. Additional manual work
is needed for event payloads and response headers. The native trial changes:

- Load a versioned HTMX asset only in `daisyui_test/index.html`, preserving the
  shared 1.9.12 asset used by the original Folders and Reorder pages.
- Rename `hx-disabled-elt` to `hx-disable`; make workspace indicators/request
  synchronization and toolbar target/swap inheritance explicit with `:inherited`.
- Move lifecycle handlers to the new event names and Fetch context
  (`event.detail.ctx`), including cancellation with `event.preventDefault()`.
- Preserve inline validation, draft restoration, selection clearing, and expired
  session navigation. Explicitly prevent unexpected error responses from replacing
  the workspace, since v4 swaps error responses by default.
- Reset local state before history restoration and initialize after the returned
  DOM is swapped. Existing server rendering already handles full history restores.
- Replace the prototype's `HX-Trigger-After-Swap` response header with `HX-Trigger`.

No database migration, domain mutation rewrite, framework change, or daisyUI
change is needed. The official compatibility extension alone is insufficient.
See the [migration documentation](https://four.htmx.org/docs/whats-new-in-htmx-4)
and [event context reference](https://four.htmx.org/docs/htmx-events-guide).

## Recommendation and limits

If moving to v4 now, pin 4.0.0 for the prototype and retain the original UI's
separate asset. Otherwise, wait for a 4.0.x release that fixes the parser regression
and rerun the comparison. 4.0.1 contains other history, request synchronization,
trigger, and extension fixes, so 4.0.0 should not be described as bug-free.
See the [4.0.1 release notes](https://github.com/bigskysoftware/htmx/releases/tag/v4.0.1).

The assessment covers our current workflows, not every browser or rapid-navigation
race. WebKit and newer Chromium versions have not been run against the migrated
app. Built-in morphing and new extensions have not been adopted or evaluated.
The trial preserves existing outerHTML swaps to keep the migration bounded.

To reproduce the wider 4.0.0 run in the worktree:

```sh
python3 -m pytest tests/test_daisyui_test.py tests/test_daisyui_list.py tests/test_daisyui_edit.py tests/test_list_user_ordering.py tests/test_reverse_proxy.py tests/test_task_moves.py tests/test_htmx4_assessment.py -q
```

The worktree's browser fixture accepts `ASSESSMENT_BROWSER=firefox` or `webkit`.
Raw results are saved in `/tmp/htmx4-bare-tests.txt`,
`/tmp/htmx4-compat-tests.txt`, `/tmp/htmx4-native-firefox-tests.txt`, and
`/tmp/htmx400-native-chromium-tests.txt`. The 4.0.1 npm asset was verified against
the SHA-384 checksum in the official installation documentation.

# Moving tasks and calendar recurrence

Moving tagged tasks carries their complete subtrees and all attached notes to the target list. A child remains attached when its parent is included, regardless of selection order. Tasks and notes selected more than once are counted once. The success counts include automatically moved descendants and notes.

Attached notes keep their task reference and receive the target list reference, so editing a note returns to the correct list and deleting the original list cannot delete the moved note. A note moved on its own becomes a list-level note. The original and target lists receive updated modification times. Invalid or missing selections reject the request without partially moving records.

Calendar create and edit forms validate recurrence rules before changing records. JSON imports validate the final recurrence and start date after merging all partial changes in the upload; an invalid rule rolls back the entire import. A partial update that omits recurrence preserves it. Valid exports can be restored repeatedly without changing rules or occurrence completions.

Invalid rules already in the database do not break Home or Calendar. Calendar lists the affected events with links to edit their recurrence. Reading an invalid event does not change or remove its stored rule, notes or completions. Occurrence generation stops at the configured occurrence limit and the requested date range.

Regression tests:

```sh
.venv/bin/python -m pytest tests/test_task_moves.py tests/test_calendar_recurrence.py tests/test_calendar_filter.py tests/test_calendar_view_day_abbr.py -q
```

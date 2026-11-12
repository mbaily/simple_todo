# Calendar events API

`GET /api/calendar/events` returns calendar occurrences as JSON for an inclusive
date range. Both `start_date` and `end_date` are required in `YYYY-MM-DD` form.
For example:

```sh
curl -H "Authorization: Bearer $CALENDAR_API_KEY" \
  'https://example.com/simple_todo/api/calendar/events?start_date=2026-11-01&end_date=2026-11-07'
```

The range may contain at most 366 calendar days. A single day is specified by
using the same date for both parameters. Results are ordered by occurrence
start time, then event ID. An occurrence is included when its start is within
the range; an event that starts earlier and overlaps the range is not included.
Recurring events have one entry per occurrence, including completed occurrences.

```json
{
  "start_date": "2026-11-01",
  "end_date": "2026-11-07",
  "occurrences": [
    {
      "event": {
        "id": 1,
        "title": "Appointment",
        "start_at": "2026-11-01T09:00:00",
        "all_day": false,
        "duration_minutes": 60,
        "rrule": null,
        "note": null,
        "color": null,
        "event_group_id": null,
        "important_if_late": false,
        "created_at": "2026-10-01T00:00:00",
        "modified_at": "2026-10-01T00:00:00",
        "hashtag_ids": [],
        "hashtags": []
      },
      "occurrence_at": "2026-11-01T09:00:00",
      "completed": false,
      "is_late": false,
      "is_important": false
    }
  ]
}
```

`event.start_at` is the event's original start, while `occurrence_at` is the
start of this particular occurrence. `event.important_if_late` indicates whether
the event is configured to become important when overdue and unchecked.
Each occurrence also reports `is_late` (true when uncompleted and at least one
calendar day past the occurrence date) and `is_important` (true when `is_late`
and `event.important_if_late` is enabled). Calendar start and occurrence times are
timezone-naive wall-clock values; the app does not attach a timezone or convert
them to UTC. The event also includes its note and hashtags, so treat responses
as sensitive. Responses use `Cache-Control: no-store`.

Invalid, missing or reversed dates, ranges over 366 days, and ranges producing
more than 10,000 occurrences return HTTP 422 rather than partial results. A
stored invalid recurrence or unavailable recurrence support returns HTTP 500
rather than silently omitting data.

## Authentication

With `SIMPLE_TODO_BASIC_AUTH=1`, a signed login session cookie or HTTP Basic
credentials can be used, but Basic credentials grant access to the rest of the
application. For server-to-server use, configure a separate calendar-only
bearer key. Generate a random key and its SHA-256 digest on a trusted machine:

```sh
python3 -c 'import secrets, hashlib; key = secrets.token_urlsafe(32); print("Key:", key); print("SHA-256:", hashlib.sha256(key.encode()).hexdigest())'
```

Store the key in the calling server's protected configuration, not browser
code. Set the username whose database is to be accessed and the digest in the
Simple Todo service environment, then restart Simple Todo:

```sh
SIMPLE_TODO_CALENDAR_API_USER=your-simple-todo-username
SIMPLE_TODO_CALENDAR_API_KEY_SHA256=the-printed-sha256-digest
```

Only one calendar key and user are supported. Generate a distinct key from the
bills key. The calendar key authorizes only `GET /api/calendar/events`; it does
not authorize other routes. Replace the digest and restart to rotate it, or
remove both settings and restart to revoke it. Use HTTPS. If global
authentication is disabled, routes are public regardless of key configuration.

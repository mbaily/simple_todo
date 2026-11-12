# Bills dashboard API

For calendar occurrences, see the separate [calendar events API](calendar-api.md).

`GET /api/bills` returns the same dashboard groups as `/bills`: `overdue`,
`due_today`, `upcoming`, `covered_early` (paid for the current cycle),
`future_estimates`, and `unscheduled`. Each bill includes its displayed
`effective_amount`, estimate flag, due date, and, for tracked accounts, credit
and outstanding balances in cents. `monthly_estimates` contains a separate
recurring budget per currency. `recent_payments` covers the same rolling
30-day window as the page; it is not a list of all payments in the current cycle.
`as_of` is the server's local calendar date. Dates are ISO 8601 calendar dates.

Inactive bills are omitted unless `?show_inactive=1` is supplied. The response
always includes `inactive_count`. Responses use `Cache-Control: no-store`.

Authentication follows the rest of the app: when `SIMPLE_TODO_BASIC_AUTH=1`,
use a signed login session cookie or HTTP Basic credentials from the htpasswd
file. Unauthenticated requests return JSON with HTTP 401. For example:

```sh
curl --user 'username' 'https://example.com/simple_todo/api/bills'
```

Curl prompts for the password. Use HTTPS, a strong unique account password and
restricted access to the credentials. Authentication is optional globally; if
it is disabled, this endpoint is unauthenticated too. Basic credentials grant the
same account access as the rest of the application, not read-only API access.
For a server-to-server integration, configure a dedicated bearer key. Generate a
random key and its SHA-256 digest on a trusted machine:

```sh
python3 -c 'import secrets, hashlib; key = secrets.token_urlsafe(32); print("Key:", key); print("SHA-256:", hashlib.sha256(key.encode()).hexdigest())'
```

Save the key in the calling server's protected configuration. Add the digest and
the Simple Todo username whose database should be accessed to the Simple Todo
service environment (for example, its external `.env`):

```sh
SIMPLE_TODO_BILLS_API_USER=your-simple-todo-username
SIMPLE_TODO_BILLS_API_KEY_SHA256=the-printed-sha256-digest
```

Restart Simple Todo after changing the service environment. Send the key as
`Authorization: Bearer <key>` from the lists server, never from browser code.
The key only authorizes `GET /api/bills`; it does not authorize other API routes.
Replace the digest and restart to rotate the key, or remove both settings and
restart to revoke it. Only one key and user are supported at a time. Keys should
be generated randomly as above, not chosen as passwords. If global authentication
is disabled, routes remain public regardless of this key configuration.

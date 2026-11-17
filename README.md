# Simple Todo

A self-hosted todo and household organiser built with FastAPI, SQLite and htmx.

- **Lists and todos** with subtasks, notes, pinning, delays, hashtags and categories
- **Folders** and **smart folders** (saved filtered views across lists, todos and events)
- **Search** backed by an SQLite FTS5 trigram index
- **Calendar** with recurring events, per-occurrence completion and notes, in agenda, week and month views
- **Bills** with invoices, payments, credits and estimated amounts and due dates from history
- **Recipes and pantry** tracking, expenses and a journal of activity
- Optional logins, with a separate SQLite database per user
- Interface in English, Spanish, French, German, Japanese and Polish

## Quick start

Requires Python 3.11 or newer.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
PYTHON_BIN=.venv/bin/python ./run_dev.sh
```

Open http://127.0.0.1:3102/. The default interface is at `/app`; the original
interface remains at `/classic`.

To keep databases and settings outside the source tree, set
`SIMPLE_TODO_DATA_DIR` first. See [docs/STORAGE.md](docs/STORAGE.md).

## Tests

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m pytest tests
```

## Deploying

[deploying.txt](deploying.txt) covers running the app as a systemd service
behind Caddy on Debian. [Caddyfile.example](Caddyfile.example) is a starting
point for the reverse proxy.

## License

[MIT](LICENSE). Bundled third-party files keep their own licenses: Alpine.js
(MIT, [static/LICENSE.alpine](static/LICENSE.alpine)), htmx (Zero-Clause BSD,
[static/daisyui_test/LICENSE.htmx](static/daisyui_test/LICENSE.htmx)) and
daisyUI (MIT, noted in its CSS file).

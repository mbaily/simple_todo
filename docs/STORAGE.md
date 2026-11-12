# External user data and settings

Set `SIMPLE_TODO_DATA_DIR` to a directory outside the application checkout or
deployment. It may be an absolute path (recommended) or a path relative to the
working directory. `~` is expanded. Missing directories are created with mode
0700; the service account must be able to write there.

```sh
export SIMPLE_TODO_DATA_DIR=/var/lib/simple_todo
python main.py
```

This mode keeps server state in the selected directory:

| File | Purpose |
| --- | --- |
| `alice_todo_app.db`, `bob_todo_app.db`, etc. | Separate SQLite databases containing each user's records, preferences, sync state and journal |
| `todo_app.db` | The anonymous/default user's database |
| SQLite sidecar files | Created beside the corresponding database |
| `.htpasswd` | Login accounts and password hashes |
| `session-secret` | Automatically generated private signing secret, stable across restarts/workers |
| `.env` | Optional environment configuration, loaded by systemd or `run_dev.sh` |
| `settings.json` | Optional site-wide overrides of the constants in `config.py` |
| `default_smart_folders.json` | Editable smart-folder defaults, seeded once from the bundled defaults |

Ordinary usernames keep their familiar filenames. Characters such as `/` and
`%` are percent-encoded instead of removed, so different usernames cannot collapse
onto the same filename. Punctuation previously stripped by legacy naming may
therefore need a filename change when migrating. `default` is the reserved name
used by the application for its anonymous/default identity.

`SIMPLE_TODO_DB_PATH` is **ignored when `SIMPLE_TODO_DATA_DIR` is set**, including
an old override left in an existing environment file. Every named user gets a
separate database. There is no fallback to databases or credentials in the app
directory. `SIMPLE_TODO_HTPASSWD_PATH`, if supplied, is resolved under the selected
data directory; absolute paths are accepted only inside it. State-file symlinks
that escape the selected directory are rejected.

An explicit `SIMPLE_TODO_SESSION_SECRET` still takes precedence over the generated
secret. Keep it in the external `.env` (or a service environment) when preserving
existing login cookies. No session database is needed: cookies are signed and
per-user preferences are stored in each user's database.

For site-specific settings, create `settings.json`, for example:

```json
{
  "SHOW_QUICK_ADD_FORMS": true,
  "SESSION_EXPIRY_DAYS": 30,
  "DATE_FORMAT": "%d/%m/%Y"
}
```

Keys must name existing uppercase settings from `config.py`, with matching JSON
types. Invalid settings stop startup. The app's `config.py` supplies built-in
defaults; site overrides belong in this JSON file. Changes to settings and the
environment require a restart. Existing smart-folder defaults are never replaced
on startup; per-user modifications to folders remain in the corresponding DB.

Direct Python and uvicorn launches consume **exported environment variables**;
they do not automatically load `.env`. `run_dev.sh` reads the external `.env`
when `SIMPLE_TODO_DATA_DIR` is exported before launch, and does not read the source
`.env` in that mode. Without the directory variable, legacy working-directory
storage and the old single-file DB override remain supported. Absolute legacy
DB overrides now resolve correctly.

## Deployment and migration

Generate a deployment configured for external storage with:

```sh
sudo ./deploy.py --no-restart /opt/simple_todo /var/lib/simple_todo
```

Both directory arguments are optional. The target defaults to `/opt/simple_todo`
and data defaults to `/var/lib/simple_todo`. The optional second
argument can also be supplied using `SIMPLE_TODO_DATA_DIR` in the deploy script's
environment; an explicit argument takes precedence. It seeds/merges environment
settings into the selected data directory, preserves existing keys there, and generates a service with that
directory, its `.env`, and `UMask=0077`. It excludes source databases, credentials,
session-secret and site overrides from code synchronization. It does not move
existing deployment data, copy `.htpasswd`, or install the service. Routine
deployments restart the installed service after deployment succeeds; use
`--no-restart` (as above) while preparing a first installation or migrating data.

For an existing deployment, migrate deliberately before enabling the new layout:

1. Stop the service and take an application-consistent backup of its existing
   databases, credentials and environment. Do not copy a live SQLite main file
   without its outstanding journal/WAL state.
2. Create the external directory owned by the service user, typically `www-data`,
   with mode 0700. Copy each existing user's database to its corresponding new
   filename. With the common names `alice` and `bob`, the filenames stay unchanged.
   The old `todo_app.db` belongs to the anonymous/default identity. If an old
   `SIMPLE_TODO_DB_PATH` made everybody share one DB, establish ownership before
   copying: assigning that DB to `alice_todo_app.db` makes it available to `alice` only.
   The app cannot infer how to split a formerly shared user's data safely.
3. Move `.htpasswd` and the deployment `.env` into the external directory, retaining
   the existing session-signing secret. Remove `SIMPLE_TODO_DB_PATH`, keep
   `SIMPLE_TODO_DATA_DIR` in the service/bootstrap environment, and update any
   absolute password-file override to the new location. Transfer custom constants
   from the old `config.py` into `settings.json`, and copy customized smart-folder
   defaults if needed. Protect credentials and database files with mode 0600.
4. Generate/install the updated service, or set these fields on the existing unit:

   ```ini
   Environment=SIMPLE_TODO_DATA_DIR=/var/lib/simple_todo
   EnvironmentFile=-/var/lib/simple_todo/.env
   Environment=PYTHONDONTWRITEBYTECODE=1
   UMask=0077
   ```

   Retain the existing application `WorkingDirectory`/`ExecStart`. Update any
   locally added writable-path restrictions for the new state directory. Reload
   systemd, start the app, and verify both accounts and their distinct data.
5. After checking the migration and retaining a backup elsewhere, remove old user
   databases, sidecars, backups, credentials and environment files from the code
   deployment. Enabling the setting alone does not delete old files. Future
   runtime state is created only in the external directory.

Back up the whole external directory. Maintenance/reconciliation scripts take
explicit database arguments; pass the external user's database filename so their
backups remain alongside that database. Exports downloaded to a browser are held
by that browser. Browser localStorage/IndexedDB, including unsynchronized offline
edits, remains in the browser profile and is independent of server storage.

## Upgrading site-specific defaults

New installations use `/var/lib/simple_todo` for external data and leave DokuWiki integration disabled. Set `ENABLE_DOKUWIKI_LINKS`, `DOKUWIKI_LINK_FSTRING` and `DOKUWIKI_ITEM_LINK_FSTRING` in the private data directory’s `settings.json` to enable it for your own wiki. For example:

```json
{
  "ENABLE_DOKUWIKI_LINKS": true,
  "DOKUWIKI_LINK_FSTRING": "https://wiki.example.com/doku.php?id=journal:{year}:{month_short}:todo#{wday_occurrence}",
  "DOKUWIKI_ITEM_LINK_FSTRING": "https://wiki.example.com/doku.php?id=items:{item_type}_{item_id}"
}
```

For an existing installation, `deploy.py` retains the data root recorded in its generated `simple_todo.service` unless a directory is explicitly supplied. Before replacing an installed `config.py`, deployment preserves customized DokuWiki values in private `settings.json`, keeping any overrides already present. This migration reads constant values without executing the installed configuration.

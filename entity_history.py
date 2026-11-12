"""SQLite snapshots and change activity for application-owned records."""
import logging
import os
from pathlib import Path
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta
from sqlalchemy import text

from models import Base
from storage import data_directory, data_path, external_data_enabled

logger = logging.getLogger(__name__)

_TIMESTAMP = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"

def _retention_cutoff(now=None):
    now = now or datetime.now(timezone.utc)
    return (now - relativedelta(years=1)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _identifier(value):
    return '"' + value.replace('"', '""') + '"'


def _literal(value):
    return "'" + value.replace("'", "''") + "'"


def _json_row(columns, prefix):
    pairs = []
    for column in columns:
        value = f"{prefix}.{_identifier(column)}"
        # Preserve SQLite's dynamic BLOB values without making json_object fail.
        # Inject finite REALs as JSON numbers with enough digits to round-trip;
        # json_object's default floating-point conversion loses precision.
        pairs.extend((_literal(column), f"CASE WHEN typeof({value}) = 'blob' "
                      f"THEN json_object('$sqlite_blob_hex', hex({value})) "
                      f"WHEN typeof({value}) = 'real' AND abs({value}) < 9e999 "
                      f"THEN json(printf('%!.26g', {value})) ELSE {value} END"))
    return f"json_object({', '.join(pairs)})"


def _record_sql(table_name, columns, keys, prefix, operation):
    key = _json_row(keys, prefix)
    snapshot = _json_row(columns, prefix)
    name = _literal(table_name)
    return f"""
        INSERT INTO entity_versions (entity_type, entity_key, operation, recorded_at, snapshot)
        VALUES ({name}, {key}, '{operation}', {_TIMESTAMP}, {snapshot});
        INSERT INTO entity_activity (entity_type, entity_key, operation, recorded_at)
        VALUES ({name}, {key}, '{operation}', {_TIMESTAMP});
    """


def install_entity_history(engine):
    """Install triggers and seed each table once, atomically with concurrent writers."""
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            connection.exec_driver_sql("""
                CREATE TABLE IF NOT EXISTS entity_versions (
                    id INTEGER PRIMARY KEY,
                    entity_type TEXT NOT NULL,
                    entity_key TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    snapshot TEXT NOT NULL
                )
            """)
            connection.exec_driver_sql("""
                CREATE INDEX IF NOT EXISTS ix_entity_versions_retention
                ON entity_versions (entity_type, entity_key, recorded_at, id)
            """)
            connection.exec_driver_sql("""
                CREATE TABLE IF NOT EXISTS entity_activity (
                    id INTEGER PRIMARY KEY,
                    entity_type TEXT NOT NULL,
                    entity_key TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                )
            """)
            connection.exec_driver_sql("""
                CREATE INDEX IF NOT EXISTS ix_entity_activity_entity
                ON entity_activity (entity_type, entity_key, recorded_at, id)
            """)
            connection.exec_driver_sql("""
                CREATE TABLE IF NOT EXISTS entity_history_tables (
                    entity_type TEXT PRIMARY KEY
                )
            """)
            for table in Base.metadata.sorted_tables:
                name = table.name
                quoted = _identifier(name)
                info = connection.exec_driver_sql(f"PRAGMA table_info({quoted})").all()
                if not info:
                    raise RuntimeError(f"History cannot find entity table {name}")
                columns = [row[1] for row in info]
                keys = [row[1] for row in sorted(info, key=lambda row: row[5]) if row[5]]
                if not keys:
                    raise RuntimeError(f"History requires a primary key on {name}")
                # json_object has a limit of 127 arguments in older SQLite versions.
                if len(columns) > 63:
                    raise RuntimeError(f"History cannot snapshot {name}: too many columns")
                changed = " OR ".join(
                    f"OLD.{_identifier(column)} IS NOT NEW.{_identifier(column)}"
                    for column in columns
                )
                key_changed = " OR ".join(
                    f"OLD.{_identifier(column)} IS NOT NEW.{_identifier(column)}"
                    for column in keys
                )
                triggers = {
                    "insert": ("INSERT", "", _record_sql(name, columns, keys, "NEW", "create")),
                    "delete": ("DELETE", "", _record_sql(name, columns, keys, "OLD", "delete")),
                    "update": ("UPDATE", f"({changed}) AND NOT ({key_changed})",
                               _record_sql(name, columns, keys, "NEW", "update")),
                    "rekey": ("UPDATE", key_changed,
                              _record_sql(name, columns, keys, "OLD", "delete") +
                              _record_sql(name, columns, keys, "NEW", "create")),
                }
                for suffix, (event, condition, body) in triggers.items():
                    trigger = _identifier(f"entity_history_{name}_{suffix}")
                    connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger}")
                    when = f"WHEN {condition}" if condition else ""
                    connection.exec_driver_sql(
                        f"CREATE TRIGGER {trigger} AFTER {event} ON {quoted} "
                        f"{when} BEGIN {body} END"
                    )
                registered = connection.execute(
                    text("SELECT 1 FROM entity_history_tables WHERE entity_type = :name"),
                    {"name": name},
                ).first()
                if not registered:
                    connection.exec_driver_sql(
                        "INSERT INTO entity_versions "
                        "(entity_type, entity_key, operation, recorded_at, snapshot) "
                        f"SELECT {_literal(name)}, {_json_row(keys, 'source')}, "
                        f"'baseline', {_TIMESTAMP}, {_json_row(columns, 'source')} "
                        f"FROM {quoted} AS source"
                    )
                    connection.execute(
                        text("INSERT INTO entity_history_tables (entity_type) VALUES (:name)"),
                        {"name": name},
                    )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


_THIN_BATCH_SIZE = 500
_OBSOLETE_VERSION_SQL = """
    CASE WHEN recorded_at < ? THEN (recorded_at, id) < (
        SELECT newer.recorded_at, newer.id
        FROM entity_versions AS newer
        WHERE newer.entity_type = entity_versions.entity_type
          AND newer.entity_key = entity_versions.entity_key
          AND newer.recorded_at >= substr(entity_versions.recorded_at, 1, 10)
          AND newer.recorded_at < min(?, date(entity_versions.recorded_at, '+1 day'))
        ORDER BY newer.recorded_at DESC, newer.id DESC
        LIMIT 1
    ) ELSE 0 END
"""
_THIN_PAGE_SQL = f"""
    SELECT id, {_OBSOLETE_VERSION_SQL} FROM entity_versions
    WHERE id > ? AND id <= ? ORDER BY id LIMIT ?
"""
_THIN_BATCH_SQL = f"""
    DELETE FROM entity_versions
    WHERE id > ? AND id <= ? AND {_OBSOLETE_VERSION_SQL}
"""


def _thin_connection(connection):
    """Page by primary key so both reads and write transactions stay bounded."""
    cutoff = _retention_cutoff()
    try:
        # Finish this pass even if application writes keep adding snapshots.
        cursor = connection.execute("SELECT coalesce(max(id), 0) FROM entity_versions")
        maximum_id = cursor.fetchone()[0]
        cursor.close()
        connection.commit()
        after_id = 0
        while after_id < maximum_id:
            cursor = connection.execute(
                _THIN_PAGE_SQL,
                (cutoff, cutoff, after_id, maximum_id, _THIN_BATCH_SIZE),
            )
            rows = cursor.fetchall()
            cursor.close()
            # End the read transaction before acquiring a write lock, including
            # on connections configured to start transactions for SELECTs.
            connection.commit()
            if not rows:
                break
            through_id = rows[-1][0]
            if any(row[1] for row in rows):
                # The existing retention index finds each day's newest old row
                # directly. Never sort or scan the whole archive under a lock.
                cursor = connection.execute(
                    _THIN_BATCH_SQL, (after_id, through_id, cutoff, cutoff)
                )
                cursor.close()
                connection.commit()
                # SQLite does not queue waiting writers fairly. Give them a
                # chance to acquire the lock before starting another batch.
                time.sleep(0.01)
            after_id = through_id
    except BaseException:
        connection.rollback()
        raise


def thin_entity_history(engine):
    """Keep the last snapshot for each entity and UTC day older than one year."""
    with closing(engine.raw_connection()) as connection:
        _thin_connection(connection)


def _history_paths():
    if not external_data_enabled() and os.environ.get("SIMPLE_TODO_DB_PATH"):
        yield Path(os.environ["SIMPLE_TODO_DB_PATH"]).expanduser().resolve()
        return
    root = data_directory()
    if not root.exists():
        return
    names = {path.name for path in root.glob("*_todo_app.db")}
    names.add("todo_app.db")
    for name in sorted(names):
        try:
            path = data_path(name)
        except ValueError:
            logger.exception("Cannot access entity history database %s", name)
            continue
        if path.is_file():
            yield path


def maintain_entity_histories():
    """Thin initialized databases, including databases not opened by this worker."""
    for path in _history_paths():
        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=30)) as connection:
                cursor = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='entity_versions'"
                )
                exists = cursor.fetchone()
                cursor.close()
                connection.commit()
                if exists:
                    _thin_connection(connection)
        except sqlite3.Error:
            logger.exception("Could not thin entity history in %s", path)

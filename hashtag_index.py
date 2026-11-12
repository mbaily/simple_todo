"""Hashtags written in text (``#word``), indexed in SQLite.

Hashtags are separate from tags: a tag is attached to an item in its Tags
field, a hashtag is simply found in the item's text. Nothing is stored on the
items themselves; ``hashtag_index`` is derived data that can be rebuilt from
the text at any time, so it is neither exported nor synced.

Triggers keep the index current for every write path (ORM, raw SQL, sync and
imports). SQL cannot pick hashtags out of text, so the triggers only remove
the entries of deleted rows and queue rows whose text may contain a hashtag in
``hashtag_dirty``; the queue is worked off in Python before each commit.
"""
import re

from sqlalchemy import event
from sqlalchemy.orm import Session

# source table -> text columns searched for hashtags
SOURCES = {
    "folders": ("name",),
    "lists": ("name",),
    "todos": ("title", "note"),
    "notes": ("name", "text"),
    "calendar_events": ("title", "note"),
}
MAX_LENGTH = 100
# A hashtag starts a word and begins with a letter: "#3" and "page#section" are not hashtags,
# and neither is the fragment of a URL or an HTML character reference.
_HASHTAG = re.compile(r"(?<![\w#&/])#([^\W\d_]\w*)")
# Three or six hex digits with a number among them read as a colour ("#ff0000"), not a hashtag.
_COLOUR = re.compile(r"(?=.*\d)(?:[0-9a-f]{3}|[0-9a-f]{6})\Z", re.IGNORECASE)
_installed = set()


def extract_hashtags(*texts):
    """Hashtags in the given texts, as written and without the #, in order of first appearance.

    Upper and lower case spellings are the same hashtag; the first spelling is returned.
    """
    found = {}
    for value in texts:
        if not value or "#" not in value:
            continue
        for name in _HASHTAG.findall(value):
            if len(name) <= MAX_LENGTH and not _COLOUR.match(name):
                found.setdefault(name.lower(), name)
    return list(found.values())


def _has_hash(prefix, columns):
    return "instr(" + " || ".join(f"coalesce({prefix}.{column}, '')" for column in columns) + ", '#')"


def _queue_all(connection):
    for name, columns in SOURCES.items():
        connection.exec_driver_sql(
            f"INSERT OR IGNORE INTO hashtag_dirty (entity_type, entity_id) "
            f"SELECT '{name}', id FROM {name} AS item WHERE {_has_hash('item', columns)}"
        )


def refresh(connection):
    """Re-read the queued rows and replace their entries in the index."""
    queued = connection.exec_driver_sql("SELECT entity_type, entity_id FROM hashtag_dirty").fetchall()
    for entity_type, entity_id in queued:
        connection.exec_driver_sql(
            "DELETE FROM hashtag_index WHERE entity_type = ? AND entity_id = ?", (entity_type, entity_id))
        columns = SOURCES.get(entity_type)
        row = columns and connection.exec_driver_sql(
            f"SELECT {', '.join(columns)} FROM {entity_type} WHERE id = ?", (entity_id,)).first()
        for name in extract_hashtags(*row) if row else ():
            connection.exec_driver_sql(
                "INSERT OR IGNORE INTO hashtag_index (tag, entity_type, entity_id) VALUES (?, ?, ?)",
                (name.lower(), entity_type, entity_id))
    if queued:
        connection.exec_driver_sql("DELETE FROM hashtag_dirty")
    return len(queued)


def install_hashtag_index(engine):
    """Create the index, its queue and the triggers, then index the existing text.

    Everything is re-read at each start, so text changed while the triggers were
    missing (or under older rules) cannot leave the index stale.
    """
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            connection.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS hashtag_index (tag TEXT NOT NULL, entity_type TEXT NOT NULL, "
                "entity_id INTEGER NOT NULL, PRIMARY KEY (tag, entity_type, entity_id)) WITHOUT ROWID")
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS hashtag_index_entity ON hashtag_index (entity_type, entity_id)")
            connection.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS hashtag_dirty (entity_type TEXT NOT NULL, entity_id INTEGER NOT NULL, "
                "PRIMARY KEY (entity_type, entity_id)) WITHOUT ROWID")
            for name, columns in SOURCES.items():
                queue = f"INSERT OR IGNORE INTO hashtag_dirty (entity_type, entity_id) VALUES ('{name}', NEW.id);"
                for trigger in ("insert", "update", "delete"):
                    connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS hashtag_{name}_{trigger}")
                connection.exec_driver_sql(
                    f"CREATE TRIGGER hashtag_{name}_insert AFTER INSERT ON {name} "
                    f"WHEN {_has_hash('NEW', columns)} BEGIN {queue} END")
                # Only text edits matter, and only when a # is involved before or after.
                connection.exec_driver_sql(
                    f"CREATE TRIGGER hashtag_{name}_update AFTER UPDATE OF id, {', '.join(columns)} ON {name} "
                    f"WHEN {_has_hash('NEW', columns)} OR {_has_hash('OLD', columns)} BEGIN {queue} "
                    f"INSERT OR IGNORE INTO hashtag_dirty (entity_type, entity_id) VALUES ('{name}', OLD.id); END")
                connection.exec_driver_sql(
                    f"CREATE TRIGGER hashtag_{name}_delete AFTER DELETE ON {name} BEGIN "
                    f"DELETE FROM hashtag_index WHERE entity_type = '{name}' AND entity_id = OLD.id; "
                    f"DELETE FROM hashtag_dirty WHERE entity_type = '{name}' AND entity_id = OLD.id; END")
            connection.exec_driver_sql("DELETE FROM hashtag_index")
            _queue_all(connection)
            refresh(connection)
            connection.exec_driver_sql("COMMIT")
        except Exception:
            connection.exec_driver_sql("ROLLBACK")
            raise
    _installed.add(engine)


@event.listens_for(Session, "before_commit")
def _refresh_before_commit(session):
    """Work off the queue inside the transaction that changed the text."""
    if session.bind in _installed:
        session.flush()
        refresh(session.connection())


def ensure_index(db):
    """Index a database opened outside database.get_engine (tests) on first use.

    Call this before the session writes anything: installing opens its own transaction.
    """
    if db.bind not in _installed:
        install_hashtag_index(db.bind)


def all_hashtags(db):
    """[(hashtag, {entity type: count})], most used first, then by name."""
    counts = {}
    for tag, entity_type, total in db.connection().exec_driver_sql(
            "SELECT tag, entity_type, count(*) FROM hashtag_index GROUP BY tag, entity_type"):
        counts.setdefault(tag, {})[entity_type] = total
    return sorted(counts.items(), key=lambda item: (-sum(item[1].values()), item[0]))


def hashtag_entities(db, tag):
    """{entity type: [ids]} of the rows whose text contains the hashtag."""
    found = {name: [] for name in SOURCES}
    for entity_type, entity_id in db.connection().exec_driver_sql(
            "SELECT entity_type, entity_id FROM hashtag_index WHERE tag = ?", (tag.lower(),)):
        found.setdefault(entity_type, []).append(entity_id)
    return found

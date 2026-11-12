"""SQLite FTS5 search index for lists, todos and calendar events.

Each indexed table gets an external-content FTS5 table kept current by
triggers, so every write path (ORM, raw SQL, sync and imports) stays indexed.
The trigram tokenizer keeps "match anywhere inside a word" behaviour and folds
case for non-ASCII text, which SQLite's LIKE does not. Trigrams need at least
three characters, so shorter terms (and databases whose SQLite lacks FTS5)
fall back to LIKE.
"""
import logging
import re

from sqlalchemy import and_, column, literal_column, or_, select, table, text
from sqlalchemy.exc import DBAPIError

logger = logging.getLogger(__name__)

# source table -> indexed text columns
INDEXED = {
    "lists": ("name",),
    "todos": ("title", "note"),
    "calendar_events": ("title", "note"),
}
MIN_INDEXED_TERM = 3
MAX_TERMS = 12


def _fts_name(table_name):
    return f"{table_name}_fts"


def _available(connection):
    try:
        connection.exec_driver_sql("CREATE VIRTUAL TABLE temp.fts_probe USING fts5(x, tokenize='trigram')")
        connection.exec_driver_sql("DROP TABLE temp.fts_probe")
        return True
    except DBAPIError:
        return False


def install_search_index(engine):
    """Create the FTS tables and triggers, indexing existing rows the first time."""
    with engine.connect() as connection:
        if not _available(connection):
            logger.warning("SQLite lacks FTS5 with the trigram tokenizer; search will use LIKE.")
            return False
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            for name, columns in INDEXED.items():
                fts = _fts_name(name)
                exists = connection.exec_driver_sql(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (fts,)
                ).first()
                cols = ", ".join(columns)
                new = ", ".join(f"NEW.{c}" for c in columns)
                old = ", ".join(f"OLD.{c}" for c in columns)
                remove = f"INSERT INTO {fts}({fts}, rowid, {cols}) VALUES ('delete', OLD.id, {old});"
                add = f"INSERT INTO {fts}(rowid, {cols}) VALUES (NEW.id, {new});"
                connection.exec_driver_sql(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS {fts} USING fts5("
                    f"{cols}, content='{name}', content_rowid='id', tokenize='trigram')"
                )
                connection.exec_driver_sql(
                    f"CREATE TRIGGER IF NOT EXISTS {fts}_insert AFTER INSERT ON {name} BEGIN {add} END"
                )
                connection.exec_driver_sql(
                    f"CREATE TRIGGER IF NOT EXISTS {fts}_delete AFTER DELETE ON {name} BEGIN {remove} END"
                )
                # Only text edits touch the index; ticking a checkbox does not.
                connection.exec_driver_sql(
                    f"CREATE TRIGGER IF NOT EXISTS {fts}_update AFTER UPDATE OF id, {cols} ON {name} "
                    f"BEGIN {remove} {add} END"
                )
                if not exists:
                    connection.exec_driver_sql(f"INSERT INTO {fts}({fts}) VALUES ('rebuild')")
            connection.exec_driver_sql("COMMIT")
        except Exception:
            connection.exec_driver_sql("ROLLBACK")
            raise
    return True


def search_terms(q):
    """Split a query into terms; double-quoted text stays together as a phrase."""
    terms = [phrase or word for phrase, word in re.findall(r'"([^"]+)"|(\S+)', q or "")]
    terms = [term.strip('"').strip() for term in terms]
    return [term for term in terms if term][:MAX_TERMS]


def index_installed(db):
    return db.execute(
        text("SELECT count(*) FROM sqlite_master WHERE type='table' AND name LIKE '%\\_fts' ESCAPE '\\'")
    ).scalar() >= len(INDEXED)


def text_match(model, term, indexed):
    """Condition: ``term`` occurs in one of ``model``'s indexed text columns."""
    name = model.__tablename__
    if indexed and len(term) >= MIN_INDEXED_TERM:
        fts = table(_fts_name(name), column("rowid"))
        phrase = '"' + term.replace('"', '""') + '"'
        return model.id.in_(select(fts.c.rowid).where(literal_column(_fts_name(name)).op("MATCH")(phrase)))
    pattern = f"%{term}%"
    return or_(*(getattr(model, c).ilike(pattern) for c in INDEXED[name]))


def all_terms(q, condition):
    """Require every term of ``q`` to satisfy ``condition(term)``."""
    return and_(*(condition(term) for term in search_terms(q)))

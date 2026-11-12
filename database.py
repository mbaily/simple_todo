from sqlalchemy import create_engine, text, inspect, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.engine import URL
from models import Base
import os
from pathlib import Path
from urllib.parse import quote
from storage import data_directory, data_path, external_data_enabled
from entity_history import install_entity_history, thin_entity_history

_engines = {}

def get_db_path(user: str) -> str:
    if external_data_enabled():
        # Encoding, rather than deleting characters, keeps different users
        # separate (e.g. "a.b", "ab", "a/b" and "a%b").
        safe_user = quote(user, safe="-_")
        filename = "todo_app.db" if not user or user == "default" else f"{safe_user}_todo_app.db"
        path = data_path(filename)
    elif os.environ.get("SIMPLE_TODO_DB_PATH"):
        # Legacy single-file mode, retained for existing installs and tests.
        # An external data root always takes precedence and isolates users.
        path = Path(os.environ["SIMPLE_TODO_DB_PATH"]).expanduser().resolve()
    else:
        safe_user = "".join(c for c in user if c.isalnum() or c in ('-', '_'))
        filename = "todo_app.db" if not safe_user or safe_user == "default" else f"{safe_user}_todo_app.db"
        path = Path(filename).resolve()
    return str(URL.create("sqlite", database=str(path)))

def get_engine(user: str):
    db_url = get_db_path(user)
    if db_url not in _engines:
        if external_data_enabled():
            data_directory().mkdir(mode=0o700, parents=True, exist_ok=True)
        engine = create_engine(
            db_url, connect_args={"check_same_thread": False}
        )
        @event.listens_for(engine, "connect")
        def enable_recursive_triggers(dbapi_connection, connection_record):
            dbapi_connection.execute("PRAGMA recursive_triggers = ON")

        # Initialize DB if new
        Base.metadata.create_all(bind=engine)
        run_migrations(engine)
        install_entity_history(engine)
        thin_entity_history(engine)
        _engines[db_url] = engine

    return _engines[db_url]


# Define migrations as (table_name, column_name, column_definition)
MIGRATIONS = [
    ("lists", "show_completed", "BOOLEAN DEFAULT 1"),
    ("lists", "archived", "BOOLEAN DEFAULT 0"),
    ("lists", "pinned", "BOOLEAN DEFAULT 0"),
    ("lists", "pinned_at", "DATETIME"),
    ("lists", "is_collation", "BOOLEAN DEFAULT 0"),
    ("lists", "collation_color", "VARCHAR(7)"),
    ("lists", "sort_ascending", "BOOLEAN DEFAULT 0"),
    ("lists", "partition_by_datetime_headers", "BOOLEAN DEFAULT 1"),
    ("lists", "intersperse_collated_todos", "BOOLEAN DEFAULT 0"),
    ("todos", "pinned", "BOOLEAN DEFAULT 0"),
    ("todos", "pinned_at", "DATETIME"),
    ("hashtags", "show_completed", "BOOLEAN DEFAULT 1"),
    ("todos", "parent_id", "INTEGER REFERENCES todos(id)"),

    # Smart folders (added later; columns may be missing in existing DBs)
    ("smart_folders", "show_lists", "BOOLEAN DEFAULT 1"),
    ("smart_folders", "show_todos", "BOOLEAN DEFAULT 1"),
    ("smart_folders", "group_todos_by_list", "BOOLEAN DEFAULT 1"),
    ("smart_folders", "uncompleted_only", "BOOLEAN DEFAULT 0"),
    ("smart_folders", "include_archived_lists", "BOOLEAN DEFAULT 0"),
    ("smart_folders", "modified_after", "DATETIME"),
    ("smart_folders", "modified_before", "DATETIME"),
    ("smart_folders", "created_at", "DATETIME"),
    ("smart_folders", "modified_at", "DATETIME"),

    # Smart folder sorting + nesting options
    ("smart_folders", "root_todos_only", "BOOLEAN DEFAULT 0"),
    ("smart_folders", "nest_subtodos", "BOOLEAN DEFAULT 0"),
    ("smart_folders", "list_sort_field", "VARCHAR(32) DEFAULT 'modified_at'"),
    ("smart_folders", "list_sort_desc", "BOOLEAN DEFAULT 1"),
    ("smart_folders", "todo_sort_field", "VARCHAR(32) DEFAULT 'modified_at'"),
    ("smart_folders", "todo_sort_desc", "BOOLEAN DEFAULT 1"),
    
    # New columns (2026-01-02)
    ("todos", "color", "VARCHAR(7)"),
    # ("smart_folders", "filter_include_subtodos", "BOOLEAN DEFAULT 0"), # Removed in favor of per-hashtag
    # ("smart_folders", "filter_exclude_subtodos", "BOOLEAN DEFAULT 0"), # Removed in favor of per-hashtag
    
    # Per-hashtag subtodo flags (2026-01-02 update)
    ("smart_folder_include_hashtags", "include_subtodos", "BOOLEAN DEFAULT 0"),
    ("smart_folder_exclude_hashtags", "include_subtodos", "BOOLEAN DEFAULT 0"),

    # Delayed items (2026-01-04)
    ("lists", "delayed_until", "DATETIME"),
    ("todos", "delayed_until", "DATETIME"),
    ("lists", "show_delayed", "BOOLEAN DEFAULT 1"),

    # Bill payment late override (2026-03-25)
    ("bill_payments", "is_late_overridden", "BOOLEAN DEFAULT 0"),

    # Bill anchor day and confirmed due date (2026-09-24)
    ("bills", "anchor_day", "INTEGER"),
    ("bills", "is_due_date_confirmed", "BOOLEAN DEFAULT 0"),
    ("bills", "amount_strategy", "VARCHAR(20) DEFAULT 'fixed'"),
    ("bills", "ledger_enabled", "BOOLEAN NOT NULL DEFAULT 0"),
    ("bill_payments", "amount_cents", "INTEGER"),
    ("bill_payments", "invoice_id", "INTEGER REFERENCES bill_invoices(id)"),
    ("bill_payments", "payment_method", "VARCHAR(100)"),
    ("bill_payments", "request_token", "VARCHAR(64)"),
    ("bill_payments", "request_fingerprint", "VARCHAR(64)"),
    ("bill_invoices", "invoice_date", "DATETIME"),
    ("bill_invoices", "period_start", "DATETIME"),
    ("bill_invoices", "period_end", "DATETIME"),
    ("bill_invoices", "statement_balance_cents", "INTEGER"),
    ("bill_invoices", "request_token", "VARCHAR(64)"),
    ("bill_invoices", "request_fingerprint", "VARCHAR(64)"),
    ("bill_invoices", "invoice_number", "VARCHAR(255)"),
    ("bill_invoices", "invoice_number_key", "VARCHAR(255)"),
    ("calendar_events", "important_if_late", "BOOLEAN DEFAULT 0"),
]


# Lightweight table migrations (for adding new tables without a full migration framework)
TABLE_MIGRATIONS = [
    (
        "list_datetime_headers",
        """
        CREATE TABLE IF NOT EXISTS list_datetime_headers (
            id INTEGER PRIMARY KEY,
            list_id INTEGER NOT NULL REFERENCES lists(id),
            created_at DATETIME
        )
        """,
    ),
    (
        "expenses",
        """
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY,
            amount FLOAT NOT NULL,
            description VARCHAR(255),
            category VARCHAR(100),
            date DATETIME NOT NULL,
            payment_method VARCHAR(50),
            currency VARCHAR(8) DEFAULT 'AUD',
            note TEXT,
            created_at DATETIME,
            modified_at DATETIME
        )
        """,
    ),
]


def run_migrations(engine):
    """Check and apply any missing columns"""
    inspector = inspect(engine)
    
    with engine.connect() as conn:
        for table_name, column_name, column_def in MIGRATIONS:
            # Check if table exists
            if table_name not in inspector.get_table_names():
                continue
            
            # Check if column exists
            columns = [col["name"] for col in inspector.get_columns(table_name)]
            if column_name not in columns:
                print(f"Migration: Adding column {column_name} to {table_name}")
                conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}"))
                conn.commit()

        if "bill_payments" in inspector.get_table_names():
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_bill_payments_request_token ON bill_payments (request_token)"))
            conn.commit()
        if "bill_invoices" in inspector.get_table_names():
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_bill_invoices_request_token ON bill_invoices (request_token)"))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_bill_invoices_number ON bill_invoices (bill_id, invoice_number_key)"))
            conn.commit()

        # Ensure any new tables exist (older DBs won't have them).
        existing_tables = set(inspector.get_table_names())
        for table_name, create_sql in TABLE_MIGRATIONS:
            if table_name in existing_tables:
                continue
            print(f"Migration: Creating table {table_name}")
            conn.execute(text(create_sql))
            conn.commit()
        
        # Special migration: notes.list_id needs to be nullable for calendar event notes
        if "notes" in existing_tables:
            columns = {col["name"]: col for col in inspector.get_columns("notes")}
            if "list_id" in columns and columns["list_id"].get("nullable") == False:
                print("Migration: Recreating notes table with nullable list_id")
                conn.execute(text("""
                    CREATE TABLE notes_new (
                        id INTEGER PRIMARY KEY,
                        text TEXT NOT NULL,
                        name VARCHAR(255),
                        created_at DATETIME,
                        modified_at DATETIME,
                        list_id INTEGER REFERENCES lists(id),
                        todo_id INTEGER REFERENCES todos(id),
                        calendar_event_id INTEGER REFERENCES calendar_events(id)
                    )
                """))
                conn.execute(text("""
                    INSERT INTO notes_new (id, text, name, created_at, modified_at, list_id, todo_id)
                    SELECT id, text, name, created_at, modified_at, list_id, todo_id FROM notes
                """))
                conn.execute(text("DROP TABLE notes"))
                conn.execute(text("ALTER TABLE notes_new RENAME TO notes"))
                conn.commit()

        # Special migration: bills.next_due_date needs to be nullable for unscheduled bills
        if "bills" in existing_tables:
            columns = {col["name"]: col for col in inspector.get_columns("bills")}
            if "next_due_date" in columns and columns["next_due_date"].get("nullable") == False:
                print("Migration: Recreating bills table with nullable next_due_date")
                conn.execute(text("PRAGMA foreign_keys = OFF;"))
                conn.execute(text("""
                    CREATE TABLE bills_new (
                        id INTEGER PRIMARY KEY,
                        name VARCHAR(255) NOT NULL,
                        payee VARCHAR(255),
                        note TEXT,
                        expected_amount FLOAT,
                        currency VARCHAR(8) DEFAULT 'AUD',
                        next_due_date DATETIME,
                        anchor_day INTEGER,
                        is_due_date_confirmed BOOLEAN DEFAULT 0,
                        frequency VARCHAR(20) DEFAULT 'monthly',
                        frequency_days INTEGER,
                        is_auto_pay BOOLEAN DEFAULT 0,
                        is_active BOOLEAN DEFAULT 1,
                        category VARCHAR(100),
                        color VARCHAR(20),
                        amount_strategy VARCHAR(20) DEFAULT 'fixed',
                        ledger_enabled BOOLEAN NOT NULL DEFAULT 0,
                        created_at DATETIME,
                        modified_at DATETIME
                    )
                """))
                conn.execute(text("""
                    INSERT INTO bills_new (id, name, payee, note, expected_amount, currency, next_due_date, anchor_day, is_due_date_confirmed, frequency, frequency_days, is_auto_pay, is_active, category, color, amount_strategy, ledger_enabled, created_at, modified_at)
                    SELECT id, name, payee, note, expected_amount, currency, next_due_date, anchor_day, is_due_date_confirmed, frequency, frequency_days, is_auto_pay, is_active, category, color, amount_strategy, ledger_enabled, created_at, modified_at FROM bills
                """))
                conn.execute(text("DROP TABLE bills"))
                conn.execute(text("ALTER TABLE bills_new RENAME TO bills"))
                conn.execute(text("PRAGMA foreign_keys = ON;"))
                conn.commit()

        # Re-run column migrations in case TABLE_MIGRATIONS or table recreations ran above
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
        for table_name, column_name, column_def in MIGRATIONS:
            if table_name not in table_names:
                continue
            columns = [col["name"] for col in inspector.get_columns(table_name)]
            if column_name not in columns:
                print(f"Migration: Adding column {column_name} to {table_name}")
                conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}"))
                conn.commit()


def init_db():
    """Create all tables and run migrations for default user (backward compatibility)"""
    get_engine("default")


def get_session_maker(user: str):
    engine = get_engine(user)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)

# DokuWiki links in smart folders
MIGRATIONS.append(("smart_folders", "dokuwiki_links_enabled", "BOOLEAN DEFAULT 0"))
MIGRATIONS.append(("smart_folders", "dokuwiki_date_field", "VARCHAR(32) DEFAULT 'created_at'"))

# DokuWiki click tracking migrations
for table in ["lists", "todos", "list_datetime_headers"]:
    MIGRATIONS.append((table, "dokuwiki_confirmed", "BOOLEAN DEFAULT 0"))
    MIGRATIONS.append((table, "dokuwiki_click_burst", "INTEGER DEFAULT 0"))
    MIGRATIONS.append((table, "dokuwiki_last_click", "DATETIME"))

# DokuWiki header tracking on todos
MIGRATIONS.append(("todos", "dokuwiki_header_confirmed", "BOOLEAN DEFAULT 0"))
MIGRATIONS.append(("todos", "dokuwiki_header_click_burst", "INTEGER DEFAULT 0"))
MIGRATIONS.append(("todos", "dokuwiki_header_last_click", "DATETIME"))

# DokuWiki confirmed items table
TABLE_MIGRATIONS.append((
    "dokuwiki_confirmed_items",
    """
    CREATE TABLE IF NOT EXISTS dokuwiki_confirmed_items (
        id INTEGER PRIMARY KEY,
        item_type VARCHAR(32) NOT NULL,
        item_id INTEGER NOT NULL,
        confirmed_at DATETIME,
        click_burst INTEGER DEFAULT 0,
        last_click DATETIME
    )
    """
))

TABLE_MIGRATIONS.append((
    "journal_entries",
    """
    CREATE TABLE IF NOT EXISTS journal_entries (
        id INTEGER PRIMARY KEY,
        action VARCHAR(255) NOT NULL,
        details TEXT,
        entity_type VARCHAR(50),
        entity_id INTEGER,
        created_at DATETIME
    )
    """
))

TABLE_MIGRATIONS.append((
    "links",
    """
    CREATE TABLE IF NOT EXISTS links (
        id INTEGER PRIMARY KEY,
        url VARCHAR(2048) NOT NULL,
        title VARCHAR(255),
        created_at DATETIME,
        list_id INTEGER REFERENCES lists(id),
        todo_id INTEGER REFERENCES todos(id)
    )
    """
))

TABLE_MIGRATIONS.append((
    "user_settings",
    """
    CREATE TABLE IF NOT EXISTS user_settings (
        id INTEGER PRIMARY KEY,
        filter_inc_hashtags TEXT,
        filter_exc_hashtags TEXT,
        hide_empty_lists BOOLEAN DEFAULT 0,
        show_completed BOOLEAN DEFAULT 1,
        show_delayed BOOLEAN DEFAULT 1,
        updated_at DATETIME
    )
    """
))

TABLE_MIGRATIONS.append((
    "folders",
    """
    CREATE TABLE IF NOT EXISTS folders (
        id INTEGER PRIMARY KEY,
        name VARCHAR(255) NOT NULL,
        parent_id INTEGER REFERENCES folders(id),
        created_at DATETIME
    )
    """
))

MIGRATIONS.append(("lists", "folder_id", "INTEGER REFERENCES folders(id)"))
MIGRATIONS.append(("user_settings", "active_folder_id", "INTEGER REFERENCES folders(id)"))
MIGRATIONS.append(("folders", "is_collapsed", "BOOLEAN DEFAULT 0"))
MIGRATIONS.append(("todos", "alarm_start", "DATETIME"))
MIGRATIONS.append(("todos", "alarm_end", "DATETIME"))
MIGRATIONS.append(("folders", "sort_order", "INTEGER DEFAULT 0"))

TABLE_MIGRATIONS.append((
    "notes",
    """
    CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY,
        text TEXT NOT NULL,
        name VARCHAR(255),
        created_at DATETIME,
        modified_at DATETIME,
        list_id INTEGER NOT NULL REFERENCES lists(id),
        todo_id INTEGER REFERENCES todos(id)
    )
    """
))

MIGRATIONS.append(("notes", "todo_id", "INTEGER REFERENCES todos(id)"))

# Root folder filter for smart folders (lists with no parent folder)
MIGRATIONS.append(("smart_folders", "include_root_folder", "BOOLEAN DEFAULT 0"))
MIGRATIONS.append(("smart_folders", "exclude_root_folder", "BOOLEAN DEFAULT 0"))

MIGRATIONS.append(("smart_folders", "sort_order", "INTEGER DEFAULT 0"))
MIGRATIONS.append(("smart_folders", "strict_nesting", "BOOLEAN DEFAULT 0"))

MIGRATIONS.append(("lists", "manual_sorting", "BOOLEAN DEFAULT 0"))
MIGRATIONS.append(("todos", "sort_order", "INTEGER DEFAULT 0"))

# Calendar event notes (2026-01-08)
MIGRATIONS.append(("notes", "calendar_event_id", "INTEGER REFERENCES calendar_events(id)"))
MIGRATIONS.append(("notes", "occurrence_at", "DATETIME"))

# Smart folder cascade (2026-01-11)
MIGRATIONS.append(("smart_folder_include_hashtags", "include_list_items", "BOOLEAN DEFAULT 0"))
MIGRATIONS.append(("hashtags", "include_list_items", "BOOLEAN DEFAULT 0"))
MIGRATIONS.append(("hashtags", "hide_completed_list_items", "BOOLEAN DEFAULT 0"))
MIGRATIONS.append(("smart_folders", "section_order", "VARCHAR(8) DEFAULT 'LET'"))

# Recipe mode for lists (2026-01-12)
MIGRATIONS.append(("lists", "is_recipe", "BOOLEAN DEFAULT 0"))

# Ingredient events table for recipe mode tracking
TABLE_MIGRATIONS.append((
    "ingredient_events",
    """
    CREATE TABLE IF NOT EXISTS ingredient_events (
        id INTEGER PRIMARY KEY,
        todo_id INTEGER NOT NULL REFERENCES todos(id),
        event_type VARCHAR(32) NOT NULL,
        quantity INTEGER DEFAULT 1,
        event_date DATETIME,
        note TEXT
    )
    """
))

# Ingredient groups for linking same ingredients across recipes (2026-01-12)
TABLE_MIGRATIONS.append((
    "ingredient_groups",
    """
    CREATE TABLE IF NOT EXISTS ingredient_groups (
        id INTEGER PRIMARY KEY,
        canonical_name VARCHAR(255) NOT NULL,
        created_at DATETIME
    )
    """
))

# Link todos to ingredient groups
MIGRATIONS.append(("todos", "ingredient_group_id", "INTEGER REFERENCES ingredient_groups(id)"))

# Links on notes (2026-01-13)
MIGRATIONS.append(("links", "note_id", "INTEGER REFERENCES notes(id)"))

# Collation todos sorting (2026-01-19)
# Adds sort_order to collation join table for manual positioning of collected todos
MIGRATIONS.append(("collation_todos", "sort_order", "INTEGER DEFAULT 0"))

# List categories (2026-01-27)
MIGRATIONS.append(("lists", "category", "VARCHAR(255)"))

# Standalone categories table (2026-01-27)
TABLE_MIGRATIONS.append((
    "categories",
    """
    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY,
        name VARCHAR(255) UNIQUE NOT NULL,
        created_at DATETIME
    )
    """
))

# Calendar event color (2026-02-20)
MIGRATIONS.append(("calendar_events", "color", "VARCHAR(7)"))

# Calendar event groups (2026-08-07)
TABLE_MIGRATIONS.append((
    "event_groups",
    """
    CREATE TABLE IF NOT EXISTS event_groups (
        id INTEGER PRIMARY KEY,
        name VARCHAR(255) UNIQUE NOT NULL,
        created_at DATETIME
    )
    """
))
MIGRATIONS.append(("calendar_events", "event_group_id", "INTEGER REFERENCES event_groups(id)"))

# Per-item DokuWiki page links (2026-02-21)
MIGRATIONS.append(("lists", "show_dokuwiki_item_links", "BOOLEAN DEFAULT 0"))
MIGRATIONS.append(("lists", "dokuwiki_page_enabled", "BOOLEAN DEFAULT 0"))
MIGRATIONS.append(("todos", "dokuwiki_page_enabled", "BOOLEAN DEFAULT 0"))

# Calendar traditional view setting (2026-03-11)
MIGRATIONS.append(("user_settings", "calendar_traditional_view", "BOOLEAN DEFAULT 0"))
MIGRATIONS.append(("user_settings", "calendar_weekly_view", "BOOLEAN DEFAULT 0"))
MIGRATIONS.append(("calendar_events", "all_day", "BOOLEAN NOT NULL DEFAULT 1"))
MIGRATIONS.append(("calendar_events", "duration_minutes", "INTEGER"))
MIGRATIONS.append(("user_settings", "calendar_event_filter", "VARCHAR(20) DEFAULT 'all'"))
MIGRATIONS.append(("user_settings", "calendar_sort_order", "VARCHAR(20) DEFAULT 'created_asc'"))
MIGRATIONS.append(("user_settings", "calendar_word_wrap", "BOOLEAN DEFAULT 0"))
MIGRATIONS.append(("user_settings", "calendar_show_counts", "BOOLEAN DEFAULT 1"))
TABLE_MIGRATIONS.append((
    "calendar_days",
    """CREATE TABLE IF NOT EXISTS calendar_days (
        id INTEGER PRIMARY KEY,
        day DATETIME NOT NULL UNIQUE,
        note TEXT,
        hide_completed BOOLEAN NOT NULL DEFAULT 0,
        modified_at DATETIME
    )""",
))

# User-defined ordering for lists
MIGRATIONS.append(("lists", "sort_order", "INTEGER DEFAULT 0"))

# JSON metadata column on all ORM entities
METADATA_JSON_TABLES = (
    "smart_folder_include_hashtags",
    "smart_folder_exclude_hashtags",
    "lists",
    "todos",
    "list_datetime_headers",
    "hashtags",
    "named_searches",
    "smart_folders",
    "calendar_events",
    "calendar_event_completions",
    "calendar_days",
    "event_groups",
    "links",
    "journal_entries",
    "dokuwiki_confirmed_items",
    "folders",
    "notes",
    "user_settings",
    "ingredient_events",
    "ingredient_groups",
    "categories",
    "bills",
    "bill_due_date_submissions",
    "bill_record_submissions",
    "bill_payment_submissions",
    "bill_payments",
    "bill_invoices",
    "bill_allocations",
    "bill_adjustments",
    "bill_adjustment_allocations",
    "bill_statements",
    "expenses",
)
for _table in METADATA_JSON_TABLES:
    MIGRATIONS.append((_table, "metadata_json", "JSON NOT NULL DEFAULT '{}'"))

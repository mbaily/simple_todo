import json
import math
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text

import database
import entity_history
from entity_history import _retention_cutoff, install_entity_history, maintain_entity_histories, thin_entity_history
from models import Base


@pytest.fixture
def history_db(tmp_path, monkeypatch):
    path = tmp_path / "history.db"
    monkeypatch.delenv("SIMPLE_TODO_DATA_DIR", raising=False)
    monkeypatch.setenv("SIMPLE_TODO_DB_PATH", str(path))
    monkeypatch.setattr(database, "_engines", {})
    yield path
    for engine in database._engines.values():
        engine.dispose()


def test_baseline_changes_rollback_and_reinitialization(history_db):
    old_engine = create_engine(f"sqlite:///{history_db}")
    Base.metadata.create_all(old_engine)
    with old_engine.begin() as conn:
        conn.execute(text("INSERT INTO lists (id, name) VALUES (10, 'before')"))
    old_engine.dispose()

    engine = database.get_engine("default")
    with engine.begin() as conn:
        conn.execute(text("UPDATE lists SET name='after' WHERE id=10"))
        conn.execute(text("UPDATE lists SET name='after' WHERE id=10"))
        conn.execute(text("INSERT INTO hashtags (id, name) VALUES (3, 'tag')"))
        conn.execute(text("INSERT INTO list_hashtags (list_id, hashtag_id) VALUES (10, 3)"))
        conn.execute(text("DELETE FROM list_hashtags WHERE list_id=10"))
        conn.execute(text("DELETE FROM lists WHERE id=10"))
    with engine.connect() as conn:
        conn.execute(text("INSERT INTO lists (id, name) VALUES (11, 'rolled back')"))
        conn.rollback()
    install_entity_history(engine)

    with engine.connect() as conn:
        versions = conn.execute(text(
            "SELECT operation, snapshot FROM entity_versions "
            "WHERE entity_type='lists' ORDER BY id"
        )).all()
        assert [row.operation for row in versions] == ["baseline", "update", "delete"]
        assert [json.loads(row.snapshot)["name"] for row in versions] == ["before", "after", "after"]
        activity = conn.execute(text(
            "SELECT operation FROM entity_activity WHERE entity_type='lists' ORDER BY id"
        )).scalars().all()
        assert activity == ["update", "delete"]
        relationship = conn.execute(text(
            "SELECT operation, entity_key FROM entity_versions "
            "WHERE entity_type='list_hashtags' ORDER BY id"
        )).all()
        assert [r.operation for r in relationship] == ["create", "delete"]
        assert json.loads(relationship[0].entity_key) == {"list_id": 10, "hashtag_id": 3}


def test_direct_sql_rekey_and_blob_values(history_db):
    engine = database.get_engine("default")
    with closing(sqlite3.connect(history_db)) as conn:
        conn.execute("INSERT INTO lists (id, name) VALUES (?, ?)", (1, sqlite3.Binary(b"\x00\xff")))
        conn.execute("UPDATE lists SET id=2 WHERE id=1")
        conn.commit()
        rows = conn.execute(
            "SELECT entity_key, operation, snapshot FROM entity_versions "
            "WHERE entity_type='lists' ORDER BY id"
        ).fetchall()
    assert [row[1] for row in rows] == ["create", "delete", "create"]
    assert [json.loads(row[0])["id"] for row in rows] == [1, 1, 2]
    assert json.loads(rows[0][2])["name"] == {"$sqlite_blob_hex": "00FF"}


@pytest.mark.parametrize("amount", [
    0.1,
    1.2345678901234567,
    12345678901234.56,
    -1.2345678901234567,
    5e-324,
    1.7976931348623157e308,
    float("inf"),
    -float("inf"),
])
def test_real_values_round_trip_in_baselines_and_direct_sql_changes(history_db, amount):
    old_engine = create_engine(f"sqlite:///{history_db}")
    try:
        Base.metadata.create_all(old_engine)
        with old_engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO expenses (id, amount, date) VALUES (1, ?, '2026-10-31')",
                (amount,),
            )
    finally:
        old_engine.dispose()

    database.get_engine("default")
    updated = math.nextafter(amount, 0.0)
    # A separate SQLite client must retain precision without Python callbacks.
    with closing(sqlite3.connect(history_db)) as conn:
        conn.execute("INSERT INTO expenses (id, amount, date) VALUES (2, ?, '2026-10-31')", (amount,))
        conn.execute("UPDATE expenses SET amount=? WHERE id=2", (updated,))
        conn.execute("DELETE FROM expenses WHERE id=2")
        conn.commit()
        rows = conn.execute(
            "SELECT operation, snapshot FROM entity_versions "
            "WHERE entity_type='expenses' ORDER BY id"
        ).fetchall()

    assert [row[0] for row in rows] == ["baseline", "create", "update", "delete"]
    assert [json.loads(row[1])["amount"].hex() for row in rows] == [
        value.hex() for value in (amount, amount, updated, updated)
    ]


def test_replace_records_implicit_delete_on_application_connections(history_db):
    engine = database.get_engine("default")
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO lists (id, name) VALUES (1, 'old')")
        conn.exec_driver_sql("INSERT OR REPLACE INTO lists (id, name) VALUES (1, 'new')")
        operations = conn.exec_driver_sql(
            "SELECT operation FROM entity_activity WHERE entity_type='lists' ORDER BY id"
        ).scalars().all()
    assert operations == ["create", "delete", "create"]


def test_thinning_keeps_last_old_version_per_utc_day_and_all_activity(history_db):
    engine = database.get_engine("default")
    with engine.begin() as conn:
        conn.exec_driver_sql("""
            INSERT INTO entity_versions (entity_type, entity_key, operation, recorded_at, snapshot)
            VALUES
              ('lists', '{"id":1}', 'create', strftime('%Y-%m-%dT01:00:00.000Z', 'now', '-2 years'), '{}'),
              ('lists', '{"id":1}', 'delete', strftime('%Y-%m-%dT02:00:00.000Z', 'now', '-2 years'), '{}'),
              ('lists', '{"id":2}', 'create', strftime('%Y-%m-%dT01:00:00.000Z', 'now', '-2 years'), '{}'),
              ('lists', '{"id":1}', 'create', strftime('%Y-%m-%dT01:00:00.000Z', 'now', '-2 years', '-1 day'), '{}'),
              ('lists', '{"id":1}', 'create', strftime('%Y-%m-%dT%H:%M:%fZ', ?, '+1 hour'), '{}'),
              ('lists', '{"id":1}', 'update', strftime('%Y-%m-%dT%H:%M:%fZ', ?, '+2 hours'), '{}')
        """, (_retention_cutoff(), _retention_cutoff()))
        conn.exec_driver_sql("""
            INSERT INTO entity_activity (entity_type, entity_key, operation, recorded_at)
            VALUES ('lists', '{"id":1}', 'create', '2000-01-01T00:00:00.000Z')
        """)
    thin_entity_history(engine)
    thin_entity_history(engine)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT id FROM entity_versions ORDER BY id")).scalars().all() == [2, 3, 4, 5, 6]
        assert conn.execute(text("SELECT count(*) FROM entity_activity")).scalar_one() == 1


def test_thinning_honors_year_cutoff_and_timestamp_ties(history_db):
    engine = database.get_engine("default")
    with engine.begin() as conn:
        conn.exec_driver_sql("""
            INSERT INTO entity_versions (entity_type, entity_key, operation, recorded_at, snapshot)
            VALUES
              ('lists', '{"id":1}', 'create', strftime('%Y-%m-%dT%H:%M:%fZ', ?, '-10 seconds'), '{}'),
              ('lists', '{"id":1}', 'update', strftime('%Y-%m-%dT%H:%M:%fZ', ?, '-10 seconds'), '{}'),
              ('lists', '{"id":2}', 'create', strftime('%Y-%m-%dT%H:%M:%fZ', ?, '+10 minutes'), '{}'),
              ('lists', '{"id":2}', 'update', strftime('%Y-%m-%dT%H:%M:%fZ', ?, '+10 minutes'), '{}')
        """, (_retention_cutoff(),) * 4)
    thin_entity_history(engine)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT id FROM entity_versions ORDER BY id").scalars().all() == [2, 3, 4]


@pytest.mark.parametrize("maintenance", [False, True], ids=["database-open", "background"])
def test_thinning_allows_other_writers_before_cleanup_finishes(history_db, monkeypatch, maintenance):
    engine = database.get_engine("default")
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO lists (id, name) VALUES (1, 'before')")
        conn.exec_driver_sql("""
            WITH RECURSIVE seq(n) AS (
                VALUES(1) UNION ALL SELECT n+1 FROM seq WHERE n<1201
            )
            INSERT INTO entity_versions (entity_type, entity_key, operation, recorded_at, snapshot)
            SELECT 'notes', '{"id":1}', 'update', '2000-01-01T12:00:00.000Z', '{}' FROM seq
        """)
        latest = conn.exec_driver_sql("SELECT max(id) FROM entity_versions WHERE entity_type='notes'").scalar_one()

    connect = sqlite3.connect
    writes_during_cleanup = []

    class MaintenanceConnection(sqlite3.Connection):
        committed_changes = 0

        def commit(self):
            changed = self.total_changes > self.committed_changes
            super().commit()
            self.committed_changes = self.total_changes
            if changed:
                with closing(connect(history_db, timeout=0.1)) as writer:
                    remaining = writer.execute(
                        "SELECT count(*) FROM entity_versions WHERE entity_type='notes'"
                    ).fetchone()[0]
                    if remaining > 1:
                        writer.execute("UPDATE lists SET name=? WHERE id=1", (f"during {remaining}",))
                        writer.commit()
                        writes_during_cleanup.append(remaining)

    def maintenance_connect(*args, **kwargs):
        return connect(*args, **kwargs, factory=MaintenanceConnection)

    if maintenance:
        monkeypatch.setattr(entity_history.sqlite3, "connect", maintenance_connect)
        maintain_entity_histories()
    else:
        tracked_engine = create_engine("sqlite://", creator=lambda: maintenance_connect(history_db))
        try:
            thin_entity_history(tracked_engine)
        finally:
            tracked_engine.dispose()

    assert writes_during_cleanup, "Normal writes must commit before the whole archive is thinned"
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT id FROM entity_versions WHERE entity_type='notes'").scalars().all() == [latest]
        assert conn.exec_driver_sql("SELECT count(*) FROM entity_activity WHERE operation='update'").scalar_one() == len(writes_during_cleanup)


def test_concurrent_thinning_preserves_daily_winners_across_batches(history_db, monkeypatch):
    cutoff = "2025-10-31T12:00:00.000Z"
    monkeypatch.setattr(entity_history, "_retention_cutoff", lambda: cutoff)
    engine = database.get_engine("default")
    expected = set()
    daily = {}
    with engine.begin() as conn:
        for number in range(1301):
            table = ("lists", "todos")[number % 2]
            key = json.dumps({"id": number % 7})
            stamp = f"2025-10-{29 + number % 3:02d}T{(number * 13) % 24:02d}:00:00.000Z"
            identifier = conn.exec_driver_sql(
                "INSERT INTO entity_versions (entity_type, entity_key, operation, recorded_at, snapshot) "
                "VALUES (?, ?, 'update', ?, '{}')",
                (table, key, stamp),
            ).lastrowid
            if stamp >= cutoff:
                expected.add(identifier)
            else:
                group = (table, key, stamp[:10])
                daily[group] = max(daily.get(group, ("", 0)), (stamp, identifier))
    expected.update(identifier for _, identifier in daily.values())
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: thin_entity_history(engine), range(2)))
    with engine.connect() as conn:
        assert set(conn.exec_driver_sql("SELECT id FROM entity_versions").scalars()) == expected


def test_already_thinned_history_can_be_scanned_without_a_write_transaction(history_db):
    engine = database.get_engine("default")
    with engine.begin() as conn:
        conn.exec_driver_sql("""
            WITH RECURSIVE seq(n) AS (
                VALUES(1) UNION ALL SELECT n+1 FROM seq WHERE n<1201
            )
            INSERT INTO entity_versions (entity_type, entity_key, operation, recorded_at, snapshot)
            SELECT 'notes', json_object('id', n), 'update', '2000-01-01T12:00:00.000Z', '{}' FROM seq
        """)
    read_engine = create_engine(
        "sqlite://", creator=lambda: sqlite3.connect(history_db.as_uri() + "?mode=ro", uri=True)
    )
    try:
        thin_entity_history(read_engine)
    finally:
        read_engine.dispose()
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT count(*) FROM entity_versions").scalar_one() == 1201


def test_leap_day_retention_cutoff():
    assert _retention_cutoff(datetime(2024, 2, 29, 12, 0, tzinfo=timezone.utc)) == "2023-02-28T12:00:00.000Z"


def test_maintenance_visits_unopened_user_databases(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMPLE_TODO_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SIMPLE_TODO_DB_PATH", raising=False)
    monkeypatch.setattr(database, "_engines", {})
    first = database.get_engine("alice")
    second = database.get_engine("bob")
    for engine in (first, second):
        with engine.begin() as conn:
            conn.exec_driver_sql("""
                INSERT INTO entity_versions (entity_type, entity_key, operation, recorded_at, snapshot)
                VALUES ('lists', '{"id":1}', 'create', '2000-01-01T01:00:00.000Z', '{}'),
                       ('lists', '{"id":1}', 'update', '2000-01-01T02:00:00.000Z', '{}')
            """)
    database._engines.clear()
    maintain_entity_histories()
    for engine in (first, second):
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM entity_versions")).scalar_one() == 1
        engine.dispose()


def test_concurrent_history_install_seeds_only_once(history_db):
    engine = database.get_engine("default")
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO lists (id, name) VALUES (1, 'list')"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: install_entity_history(engine), range(2)))
    with engine.connect() as conn:
        assert conn.execute(text(
            "SELECT count(*) FROM entity_versions WHERE entity_type='lists'"
        )).scalar_one() == 1


def test_list_creation_endpoint_records_entities_and_relationships(history_db, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from main import app, get_db

    monkeypatch.setenv("SIMPLE_TODO_BASIC_AUTH", "0")
    engine = database.get_engine("default")
    with Session(engine) as db:
        app.dependency_overrides[get_db] = lambda: db
        client = TestClient(app)
        try:
            response = client.post("/list/create", data={"name": "History list", "hashtags": "#history"})
            assert response.status_code == 200
            rows = db.execute(text(
                "SELECT entity_type, operation FROM entity_activity "
                "WHERE entity_type IN ('lists', 'hashtags', 'list_hashtags') ORDER BY id"
            )).all()
            assert rows == [("lists", "create"), ("hashtags", "create"), ("list_hashtags", "create")]
        finally:
            client.close()
            app.dependency_overrides.pop(get_db, None)

"""Journal entries for folder actions must be committed, not left in a closed session.

Uses a file database with a fresh connection per session: the shared in-memory
connection used elsewhere can make uncommitted rows look saved.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

import main
from models import Base, Folder, JournalEntry, List


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMPLE_TODO_BASIC_AUTH", "0")
    engine = create_engine(f"sqlite:///{tmp_path / 'journal.db'}", poolclass=NullPool)
    Base.metadata.create_all(engine)

    def get_db():
        with Session(engine) as session:
            yield session

    main.app.dependency_overrides[main.get_db] = get_db
    with Session(engine) as db:
        db.add_all([Folder(name="Home"), List(name="Errands")])
        db.commit()
    try:
        with TestClient(main.app) as test_client:
            yield test_client, engine
    finally:
        main.app.dependency_overrides.pop(main.get_db, None)
        engine.dispose()


def actions(engine):
    with Session(engine) as db:
        return [(row.action, row.entity_type, row.entity_id) for row in db.query(JournalEntry).order_by(JournalEntry.id)]


def test_original_folder_routes_commit_their_journal_entries(client):
    test_client, engine = client
    HX = {"HX-Request": "true"}
    assert test_client.post("/folders/create", data={"name": "Work"}, headers=HX).status_code == 200
    assert test_client.post("/folders/move", data={"source_type": "folder", "source_id": 2, "target_folder_id": 1}, headers=HX).status_code == 200
    assert test_client.post("/folders/move", data={"source_type": "list", "source_id": 1, "target_folder_id": 2}, headers=HX).status_code == 200
    assert test_client.post("/folders/move_list", data={"list_id": 1, "folder_id": 0}, headers=HX).status_code == 200
    assert test_client.post("/folders/rename", data={"folder_id": 2, "new_name": "Office"}, headers=HX).status_code == 200
    assert test_client.post("/folders/delete", data={"folder_id": 2}, headers=HX).status_code == 200
    assert actions(engine) == [
        ("Created Folder", "folder", 2), ("Moved Folder", "folder", 2), ("Moved List", "list", 1),
        ("Moved List", "list", 1), ("Renamed Folder", "folder", 2), ("Deleted Folder", "folder", 2),
    ]


def test_daisyui_folder_actions_are_journalled(client):
    test_client, engine = client
    HX = {"HX-Request": "true"}
    test_client.post("/app/folders?view=folders", data={"name": "Work"}, headers=HX)
    test_client.post("/app/folders/move-items?view=folders", data={"items": ["list:1"], "target_id": 2}, headers=HX)
    test_client.post("/app/folders/move-items?view=folders", data={"items": ["folder:2"], "target_id": 1}, headers=HX)
    assert [action for action, _, _ in actions(engine)] == ["Created Folder", "Moved List", "Moved Folder"]

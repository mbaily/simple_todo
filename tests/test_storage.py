"""External state must stay outside the app and isolated between users."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
import deploy
from sqlalchemy import text
from sqlalchemy.engine import make_url

import database
import storage


@pytest.fixture
def external_state(tmp_path, monkeypatch):
    root = tmp_path / "user data" / "state"
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    monkeypatch.chdir(deployment)
    monkeypatch.setenv("SIMPLE_TODO_DATA_DIR", str(root))
    monkeypatch.delenv("SIMPLE_TODO_SESSION_SECRET", raising=False)
    monkeypatch.delenv("SIMPLE_TODO_HTPASSWD_PATH", raising=False)
    monkeypatch.setattr(database, "_engines", {})
    yield root, deployment
    for engine in database._engines.values():
        engine.dispose()


def test_each_user_has_an_isolated_database_and_settings(external_state, monkeypatch):
    root, deployment = external_state
    # A legacy deployment's override must not make the new mode share data.
    monkeypatch.setenv("SIMPLE_TODO_DB_PATH", str(deployment / "shared.db"))
    users = ["alice", "bob", "a.b", "ab", "a/b", "a%b", "../alice", "default", "日本語"]
    paths = [Path(make_url(database.get_db_path(user)).database) for user in users]
    assert len(set(paths)) == len(users)
    assert all(path.parent == root for path in paths)
    for user in users:
        engine = database.get_engine(user)
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO user_settings (id, calendar_event_filter) VALUES (1, :value)"), {"value": user})
    for user in users:
        with database.get_engine(user).connect() as connection:
            assert connection.execute(text("SELECT calendar_event_filter FROM user_settings")).scalar_one() == user
    assert list(deployment.iterdir()) == []


def test_changing_root_does_not_reuse_an_old_engine(external_state, monkeypatch):
    root, _ = external_state
    first = database.get_engine("alice")
    monkeypatch.setenv("SIMPLE_TODO_DATA_DIR", str(root.parent / "other-state"))
    second = database.get_engine("alice")
    assert first is not second
    assert first.url.database != second.url.database


def test_legacy_absolute_database_override(tmp_path, monkeypatch):
    monkeypatch.delenv("SIMPLE_TODO_DATA_DIR", raising=False)
    target = tmp_path / "legacy.db"
    monkeypatch.setenv("SIMPLE_TODO_DB_PATH", str(target))
    assert make_url(database.get_db_path("alice")).database == str(target)


def test_secret_and_smart_folder_settings_are_external_and_preserved(external_state):
    root, deployment = external_state
    storage.initialize_storage()
    secret = storage.session_secret()
    assert len(secret) == 64
    assert (root / "session-secret").stat().st_mode & 0o777 == 0o600
    folders = root / "default_smart_folders.json"
    assert isinstance(json.loads(folders.read_text()), list)
    folders.write_text('[]\n')
    storage._persistent_secret.cache_clear()
    storage.initialize_storage()
    assert storage.session_secret() == secret
    assert folders.read_text() == '[]\n'
    assert list(deployment.iterdir()) == []


def test_simultaneous_workers_share_a_complete_session_secret(external_state):
    root, _ = external_state
    root.mkdir(parents=True)
    with ThreadPoolExecutor(max_workers=8) as workers:
        secrets = list(workers.map(lambda _: storage.session_secret(), range(16)))
    assert len(set(secrets)) == 1
    assert len(secrets[0]) == 64
    assert not list(root.glob(".simple-todo-*"))


def test_external_paths_cannot_escape_or_point_into_the_app(external_state, monkeypatch):
    root, deployment = external_state
    import auth
    assert auth._htpasswd_path() == str(root / ".htpasswd")
    monkeypatch.setenv("SIMPLE_TODO_HTPASSWD_PATH", str(deployment / ".htpasswd"))
    with pytest.raises(ValueError, match="inside SIMPLE_TODO_DATA_DIR"):
        auth._htpasswd_path()
    root.mkdir(parents=True)
    (root / "alice_todo_app.db").symlink_to(deployment / "alice.db")
    with pytest.raises(ValueError, match="inside SIMPLE_TODO_DATA_DIR"):
        database.get_db_path("alice")
    monkeypatch.setenv("SIMPLE_TODO_DATA_DIR", str(storage.APP_DIR / "data"))
    with pytest.raises(ValueError, match="outside the application"):
        storage.data_directory()


def run_python(code, cwd, root, app_dir=storage.APP_DIR):
    env = {key: value for key, value in os.environ.items() if not key.startswith("SIMPLE_TODO_")}
    env.update(SIMPLE_TODO_DATA_DIR=str(root), PYTHONPATH=str(app_dir), PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, "-B", "-c", code], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("settings", [{"UNKNOWN_SETTING": True}, {"SHOW_QUICK_ADD_FORMS": "yes"}, []])
def test_invalid_external_settings_stop_startup(tmp_path, settings):
    root = tmp_path / "state"
    root.mkdir()
    (root / "settings.json").write_text(json.dumps(settings))
    result = run_python("import main", tmp_path, root)
    assert result.returncode != 0
    assert "ValueError" in result.stderr


def test_real_startup_auth_user_settings_and_restart_leave_app_untouched(tmp_path):
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    for source in storage.APP_DIR.glob("*.py"):
        if not source.name.startswith("test_"):
            shutil.copy2(source, deployment / source.name)
    for directory in ("templates", "static", "locales"):
        shutil.copytree(storage.APP_DIR / directory, deployment / directory)
    shutil.copy2(storage.APP_DIR / "default_smart_folders.json", deployment)
    # Decoy legacy state must never be read or used in external mode.
    (deployment / ".htpasswd").write_text("alice:wrong-password\n")
    (deployment / ".env").write_text("SIMPLE_TODO_DB_PATH=shared.db\n")
    root = tmp_path / "user data"
    root.mkdir()
    (root / ".htpasswd").write_text("alice:alice-password\nbob:bob-password\n")
    (root / "settings.json").write_text(json.dumps({"SHOW_QUICK_ADD_FORMS": True, "LOAD_DEFAULT_SMART_FOLDERS": False}))
    before = {path.relative_to(deployment): path.read_bytes() for path in deployment.rglob("*") if path.is_file()}
    first = run_python('''
import os
os.environ["SIMPLE_TODO_BASIC_AUTH"] = "1"
os.environ["SIMPLE_TODO_DB_PATH"] = "shared.db"
import config, main
from fastapi.testclient import TestClient
from auth import _sign_session
assert config.SHOW_QUICK_ADD_FORMS is True
with TestClient(main.app) as client:
    for username in ("alice", "bob"):
        response = client.post("/login", data={"username": username, "password": username + "-password", "next": "/"}, follow_redirects=False)
        assert response.status_code == 302, response.text
        response = client.post("/list/create", data={"name": username + " private list"}, follow_redirects=False)
        assert response.status_code == 303, response.text
        response = client.post("/calendar/set-filter", data={"calendar_event_filter": "non_recurring" if username == "alice" else "recurring"}, follow_redirects=False)
        assert response.status_code == 303, response.text
        home = client.get("/classic")
        assert home.status_code == 200
        assert username + " private list" in home.text
        assert ("bob" if username == "alice" else "alice") + " private list" not in home.text
    from storage import data_path
    data_path("saved-cookie").write_text(_sign_session("alice"))
''', deployment, root, deployment)
    assert first.returncode == 0, first.stdout + first.stderr
    second = run_python('''
import os
os.environ["SIMPLE_TODO_BASIC_AUTH"] = "1"
import main
from fastapi.testclient import TestClient
from storage import data_path
from database import get_session_maker
from models import UserSettings
with TestClient(main.app) as client:
    client.cookies.set("simple_todo_session", data_path("saved-cookie").read_text())
    home = client.get("/classic", follow_redirects=False)
    assert home.status_code == 200, home.text
    assert "alice private list" in home.text and "bob private list" not in home.text
    for user, expected in (("alice", "non_recurring"), ("bob", "recurring")):
        with get_session_maker(user)() as session:
            assert session.get(UserSettings, 1).calendar_event_filter == expected
''', tmp_path, root, deployment)
    assert second.returncode == 0, second.stdout + second.stderr
    after = {path.relative_to(deployment): path.read_bytes() for path in deployment.rglob("*") if path.is_file()}
    assert after == before
    assert not (tmp_path / "todo_app.db").exists()
    assert {"alice_todo_app.db", "bob_todo_app.db", "session-secret"} <= {path.name for path in root.iterdir()}


def test_dev_launcher_uses_external_environment_and_credentials(tmp_path):
    deployment = tmp_path / "app"
    deployment.mkdir()
    for filename in ("run_dev.sh", "storage.py", "auth.py"):
        shutil.copy2(storage.APP_DIR / filename, deployment / filename)
    (deployment / ".env").write_text("exit 17 # must not source this file\n")
    root = tmp_path / "user data"
    root.mkdir()
    (root / ".env").write_text("SIMPLE_TODO_PORT=43123\nSIMPLE_TODO_BASIC_AUTH=yes\n")
    (root / ".htpasswd").write_text("alice:test-only-password\n")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    python = binaries / "python"
    python.write_text(f'''#!{sys.executable}
import json, os, sys
if sys.argv[1:3] == ["-m", "uvicorn"]:
    print(json.dumps({{"root": os.environ["SIMPLE_TODO_DATA_DIR"], "port": os.environ["SIMPLE_TODO_PORT"]}}))
else:
    os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
''')
    python.chmod(0o755)
    (binaries / "python3").symlink_to(python)
    env = {key: value for key, value in os.environ.items() if not key.startswith("SIMPLE_TODO_")}
    env.update(SIMPLE_TODO_DATA_DIR="user data", PATH=str(binaries) + os.pathsep + env["PATH"], PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(["bash", str(deployment / "run_dev.sh")], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {"root": str(root), "port": "43123"}


def test_deployment_copies_runtime_files_without_private_source_state(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "deployment"
    source.mkdir()
    target.mkdir()
    runtime = {"main.py", "storage.py", "requirements.txt", "default_smart_folders.json",
               "simple_todo.service", "templates/index.html", "static/app.js",
               "locales/en.json", "docs/STORAGE.md", "scripts/ensure_session_secret.py"}
    private = {".env", ".htpasswd", "settings.json", "session-secret", "alice_todo_app.db",
               "alice_todo_app.db-wal", "alice_todo_app.db.bak", "config.bak", ".swp",
               "latest_commit.txt", "test_grouping_bug.py", "tests/test_example.py",
               "deploy.py", "deploy.sh.old", "run_dev.sh"}
    for filename in runtime | private:
        path = source / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test fixture\n")
    deploy.sync_files(source, target)
    assert {str(path.relative_to(target)) for path in target.rglob("*") if path.is_file()} == runtime


def test_generated_systemd_unit_parses_external_environment_path(tmp_path):
    if shutil.which("systemd-analyze") is None:
        pytest.skip("systemd-analyze is not installed")
    unit = tmp_path / "simple-todo-storage-test.service"
    target = tmp_path / 'app path %n'
    python = target / '.venv/bin/python'
    python.parent.mkdir(parents=True)
    python.symlink_to('/bin/true')
    deployment = deploy.Deployment(tmp_path / 'source', target, tmp_path / 'user data %n', True)
    unit.write_text(deploy.render_service(deployment))
    result = subprocess.run(["systemd-analyze", "verify", str(unit)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "EnvironmentFile=" not in result.stderr, result.stderr

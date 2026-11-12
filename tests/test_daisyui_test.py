"""Prototype parity, HTML mutations, and real-browser HTMX interactions."""
from datetime import datetime, timedelta
from html.parser import HTMLParser
import socket
import threading
import time

import pytest
import uvicorn
from fastapi.testclient import TestClient
from playwright.sync_api import expect
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import main
from models import Base, CalendarEvent, Folder, Hashtag, JournalEntry, List, Note, Todo, UserSettings, utcnow
from test_reverse_proxy import PrefixStrippingProxy

HX = {"HX-Request": "true"}


@pytest.fixture
def prototype(monkeypatch):
    monkeypatch.setenv("SIMPLE_TODO_BASIC_AUTH", "0")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)

    def get_db():
        with Session(engine) as session:
            yield session

    main.app.dependency_overrides[main.get_db] = get_db
    with Session(engine) as db:
        tag = Hashtag(name="personal")
        folder = Folder(name="Projects")
        first = List(name="A place to start", sort_order=0, pinned=True, pinned_at=utcnow())
        second = List(name="Weekend projects", category="Personal", sort_order=1)
        db.add_all([tag, folder, first, second])
        db.flush()
        todo = Todo(title="Book a quiet afternoon", list_id=first.id, pinned=True, pinned_at=utcnow(), hashtags=[tag])
        old = Todo(title="An older idea", list_id=second.id, pinned=True, pinned_at=utcnow() - timedelta(days=40))
        db.add_all([todo, old, CalendarEvent(title="An upcoming appointment", start_at=datetime.now().replace(hour=12, minute=0), all_day=False)])
        db.commit()
        client = TestClient(main.app)
        try:
            yield db, client, {"list": first.id, "other": second.id, "todo": todo.id, "old": old.id, "folder": folder.id, "tag": tag.id}
        finally:
            client.close()
            main.app.dependency_overrides.pop(main.get_db, None)
    engine.dispose()


def test_document_fragments_and_home_parity(prototype):
    db, client, ids = prototype
    full = client.get("/app")
    fragment = client.get("/app", headers=HX)
    restored = client.get("/app", headers={**HX, "HX-History-Restore-Request": "true"})
    original = client.get("/classic")
    assert full.status_code == fragment.status_code == restored.status_code == original.status_code == 200
    assert "<!DOCTYPE html>" in full.text and "<!DOCTYPE html>" in restored.text
    assert "<!DOCTYPE html>" not in fragment.text
    assert fragment.headers["vary"] == "HX-Request, HX-History-Restore-Request"
    for key in ("lists", "pinned_todos_recent", "pinned_todos_old", "pinned_lists_recent", "home_calendar_occurrences"):
        if key == "home_calendar_occurrences":
            assert [(row["event"].id, row["occurrence_at"]) for row in full.context[key]] == [(row["event"].id, row["occurrence_at"]) for row in original.context[key]]
        else:
            assert [item.id for item in full.context[key]] == [item.id for item in original.context[key]]
    assert "An older idea" in full.text and "An upcoming appointment" in full.text
    assert "daisyui-5.7.50.css" in full.text and "https://cdn" not in full.text


def test_create_list_reuses_tags_and_explicit_unfiled_overrides_active_folder(prototype):
    db, client, ids = prototype
    db.add(UserSettings(active_folder_id=ids["folder"]))
    db.commit()
    response = client.post("/app/lists?folder_id=0&sort=created", headers=HX,
                           data={"name": "  <New & useful>  ", "hashtags": "#personal #fresh", "hashtag_ids": str(ids["tag"])})
    assert response.status_code == 200
    assert "&lt;New &amp; useful&gt;" in response.text
    db.expire_all()
    created = db.query(List).filter_by(name="<New & useful>").one()
    assert created.folder_id is None and not created.manual_sorting
    assert {tag.name for tag in created.hashtags} == {"personal", "fresh"}
    assert response.context["folder_param"] == 0 and response.context["sort"] == "created"
    assert db.query(JournalEntry).filter_by(entity_type="list", entity_id=created.id, action="Created List").count() == 1
    # The shared creation route also respects the explicit unfiled sentinel.
    result = client.post("/list/create", data={"name": "Original unfiled", "folder_id": "0"}, follow_redirects=False)
    assert result.status_code == 303
    assert db.query(List).filter_by(name="Original unfiled").one().folder_id is None


def test_daily_plan_uses_manual_sorting_and_folder_context(prototype):
    db, client, ids = prototype
    response = client.post(f'/app/daily-plan?folder_id={ids["folder"]}', headers=HX, data={"offset": "9"})
    assert response.status_code == 200
    day = datetime.now().date() + timedelta(days=9)
    created = db.query(List).filter_by(name=f"Daily Plan {day.day}/{day.month}/{day.year}").one()
    assert created.manual_sorting and created.folder_id == ids["folder"]
    count = db.query(List).count()
    assert client.post("/app/daily-plan", headers=HX, data={"offset": "10"}).status_code == 400
    assert db.query(List).count() == count


def test_completion_sets_state_and_updates_every_copy_and_remaining_count(prototype):
    db, client, ids = prototype
    path = f'/app/complete/todo/{ids["todo"]}'
    for _ in range(2):
        response = client.post(path, headers=HX, data={"completed": "true"})
        assert response.status_code == 200
        assert response.context["uncompleted_counts"].get(ids["list"], 0) == 0
    db.expire_all()
    assert db.get(Todo, ids["todo"]).completed
    assert db.query(JournalEntry).filter_by(action="Toggled Todo Completion").count() == 1
    assert client.post(path, headers=HX).status_code == 200
    db.expire_all()
    assert not db.get(Todo, ids["todo"]).completed
    assert client.post("/app/complete/todo/99999", headers=HX).status_code == 404


def test_bulk_operations_deduplicate_and_handle_mixed_types(prototype):
    db, client, ids = prototype
    selection = [f'list:{ids["list"]}', f'todo:{ids["todo"]}', f'list:{ids["list"]}']
    response = client.post("/app/bulk", headers=HX, data={"action": "unpin", "items": selection})
    assert response.status_code == 200
    db.expire_all()
    assert not db.get(List, ids["list"]).pinned and not db.get(Todo, ids["todo"]).pinned
    assert db.query(JournalEntry).filter(JournalEntry.action.like("Unpinned%" )).count() == 2
    assert response.headers["HX-Trigger"] == "daisy:selection-cleared"
    assert client.post("/app/bulk", headers=HX, data={"action": "pin", "items": selection}).status_code == 200
    assert client.post("/app/bulk", headers=HX, data={"action": "delete", "items": selection}).status_code == 200
    db.expire_all()
    assert db.get(List, ids["list"]) is None and db.get(Todo, ids["todo"]) is None
    assert db.get(List, ids["other"]) is not None


@pytest.mark.parametrize("action,items", [("archive", ["list:1", "todo:1"]), ("pin", ["list:1", "todo:999999"]), ("pin", ["bad:1"]), ("delete", ["list:not-an-id"]), ("delete", []), ("unknown", ["list:1"])])
def test_invalid_bulk_actions_preserve_data_and_return_inline_error(prototype, action, items):
    db, client, ids = prototype
    response = client.post("/app/bulk", headers=HX, data={"action": action, "items": items})
    assert response.status_code in (400, 404)
    assert response.headers["HX-Retarget"] == "#page-feedback"
    assert 'role="alert"' in response.text
    db.expire_all()
    assert db.query(List).count() == 2 and db.query(Todo).count() == 2
    assert db.get(List, ids["list"]).pinned and not db.get(List, ids["list"]).archived


def test_archive_unarchive_and_form_fallback(prototype):
    db, client, ids = prototype
    for action, archived in [("archive", True), ("unarchive", False)]:
        response = client.post("/app/bulk", headers=HX, data={"action": action, "items": f'list:{ids["list"]}'})
        assert response.status_code == 200
        db.expire_all()
        assert db.get(List, ids["list"]).archived == archived
    response = client.post("/app/lists?folder_id=0&sort=modified", data={"name": "Regular form"}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/app?sort=modified&folder_id=0"
    assert client.post("/app/lists", headers=HX, data={"name": "  "}).status_code == 400


def test_configured_sections_occurrence_notes_and_folder_visibility(prototype, monkeypatch):
    db, client, ids = prototype
    now = datetime.now()
    alarm = Todo(title="Alarm task", list_id=ids["list"], alarm_start=now - timedelta(hours=1), alarm_end=now + timedelta(hours=1))
    resumed = Todo(title="Ready again", list_id=ids["list"], delayed_until=now - timedelta(days=1))
    event = db.query(CalendarEvent).one()
    db.add_all([alarm, resumed, Note(calendar_event=event, text="Only for this occurrence", occurrence_at=event.start_at),
                List(name="Inside the folder", folder_id=ids["folder"])])
    db.commit()
    monkeypatch.setattr(main, 'SHOW_QUICK_ADD_FORMS', True)
    monkeypatch.setattr(main, 'SHOW_CALENDAR_ABOVE_LISTS', True)
    monkeypatch.setattr(main, 'SHOW_TODAY_CALENDAR_ABOVE_PINNED', True)
    response = client.get('/app')
    assert 'Alarm task' in response.text and 'Ready again' in response.text
    assert 'Only for this occurrence' in response.text
    assert response.text.index('id="calendar-heading"') < response.text.index('id="lists-heading"')
    assert 'class="collapse capture-collapse" open' in response.text
    folder_view = client.get(f'/app?folder_id={ids["folder"]}')
    assert 'Inside the folder' in folder_view.text
    assert 'Alarm task' not in folder_view.text and 'Ready again' not in folder_view.text
    assert 'An older idea' not in folder_view.text
    monkeypatch.setattr(main, 'SHOW_CALENDAR_ABOVE_LISTS', False)
    response = client.get('/app')
    assert response.text.index('id="lists-heading"') < response.text.index('id="calendar-heading"')


def test_existing_json_bulk_endpoints_keep_their_contract(prototype):
    db, client, ids = prototype
    for kind, identifier in [('list', ids['list']), ('todo', ids['todo'])]:
        for pin in (False, True):
            response = client.post('/api/bulk/pin', json={'type': kind, 'ids': [identifier], 'pin': pin})
            assert response.status_code == 200 and response.json() == {'success': True, 'updated': 1}
            db.expire_all()
            assert db.get(List if kind == 'list' else Todo, identifier).pinned == pin
    response = client.post('/api/bulk/archive', json={'type': 'list', 'ids': [ids['list']], 'archived': True})
    assert response.status_code == 200 and response.json() == {'success': True, 'updated': 1, 'archived': True}
    db.expire_all()
    assert db.get(List, ids['list']).archived
    response = client.post('/api/bulk/delete', json={'type': 'todo', 'ids': [ids['todo']]})
    assert response.status_code == 200 and response.json() == {'success': True, 'deleted': 1}
    db.expire_all()
    assert db.get(Todo, ids['todo']) is None


@pytest.mark.parametrize("prefix", ["", "/simple_todo", "/apps/simple_todo"])
def test_all_prototype_urls_and_htmx_actions_respect_proxy_prefix(prototype, monkeypatch, prefix):
    db, _, ids = prototype
    monkeypatch.setattr(main.app, "root_path", prefix)
    client = TestClient(PrefixStrippingProxy(main.app, prefix))
    response = client.get(prefix + "/app")
    assert response.status_code == 200

    class URLs(HTMLParser):
        def handle_starttag(self, tag, attrs):
            for key, value in attrs:
                if key in {"href", "src", "action", "hx-get", "hx-post"} and value and value.startswith("/"):
                    assert not prefix or value.startswith(prefix + "/"), (key, value)

    URLs().feed(response.text)
    response = client.post(prefix + "/app/lists?folder_id=0", headers=HX, data={"name": "Prefixed creation"})
    assert response.status_code == 200
    assert f'action="{prefix}/app/' in response.text


@pytest.fixture
def prototype_server(prototype, request, monkeypatch):
    prefix = getattr(request, 'param', '')
    monkeypatch.setattr(main.app, 'root_path', prefix)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(PrefixStrippingProxy(main.app, prefix), lifespan="off", log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(.01)
    assert server.started
    try:
        yield f"http://127.0.0.1:{port}{prefix}", prototype
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_browser_creation_completion_selection_and_deletion(prototype_server, page):
    origin, (_, _, ids) = prototype_server
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(origin + "/app")
    page.evaluate("window.prototypeDocument = 'retained'")
    page.get_by_role("button", name="New list", exact=True).click()
    page.get_by_role("textbox", name="List name").fill("Created through HTMX")
    page.get_by_role("button", name="Add list", exact=True).click()
    expect(page.get_by_role("link", name="Created through HTMX", exact=True)).to_be_visible()
    expect(page.get_by_role("textbox", name="List name")).to_have_value("")
    page.get_by_role("textbox", name="List name").fill("Keep my draft")
    page.get_by_role("checkbox", name="Mark Book a quiet afternoon complete", exact=True).check()
    expect(page.get_by_role("checkbox", name="Mark Book a quiet afternoon incomplete", exact=True)).to_be_checked()
    expect(page.get_by_role("textbox", name="List name")).to_have_value("Keep my draft")
    page.get_by_role("button", name="Select", exact=True).click()
    selection = page.locator(f'.selection-checkbox[value="list:{ids["list"]}"]')
    selection.first.check()
    expect(selection.last).to_be_checked()
    expect(page.locator("#selection-count")).to_have_text("1 selected")
    expect(page.locator("#selected-dates")).to_be_visible()
    # Lists move between folders on the Folders page; Move to list is for tasks and notes.
    expect(page.get_by_role("button", name="Move to list…", exact=True)).to_be_disabled()
    page.get_by_role("button", name="Unpin", exact=True).click()
    expect(page.locator("#selection-count")).to_have_text("0 selected")
    expect(page.get_by_role("textbox", name="List name")).to_have_value("Keep my draft")
    page.get_by_role("button", name="Clear", exact=True).click()
    created_row = page.locator(".item-row").filter(has=page.get_by_role("link", name="Created through HTMX", exact=True))
    created_row.locator(".selection-checkbox").check()
    page.get_by_role("button", name="Delete", exact=True).click()
    expect(page.get_by_role("dialog")).to_be_visible()
    page.get_by_role("button", name="Cancel", exact=True).click()
    expect(page.get_by_role("link", name="Created through HTMX", exact=True)).to_be_visible()
    page.get_by_role("button", name="Delete", exact=True).click()
    page.get_by_role("button", name="Delete items", exact=True).click()
    expect(page.get_by_role("link", name="Created through HTMX", exact=True)).to_have_count(0)
    assert page.evaluate("window.prototypeDocument") == "retained"
    assert errors == []


@pytest.mark.parametrize('prototype_server', ['/apps/simple_todo'], indirect=True)
def test_browser_mobile_layout_sort_history_and_errors(prototype_server, page):
    origin, (_, _, ids) = prototype_server
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(origin + "/app")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.get_by_label("More navigation", exact=True).click()
    expect(page.get_by_role("link", name="Quick expenses", exact=True)).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.get_by_role("link", name="Quick expenses", exact=True)).not_to_be_visible()
    page.get_by_label("Sort lists", exact=True).select_option("created")
    page.wait_for_url("**/app?sort=created")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.go_back()
    expect(page.get_by_label("Sort lists", exact=True)).to_have_value("user")
    page.get_by_role("button", name="New list", exact=True).click()
    page.get_by_role("textbox", name="List name").fill("   ")
    page.get_by_role("button", name="Add list", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Enter a name")
    expect(page.get_by_role("textbox", name="List name")).to_have_value("   ")


def test_browser_folder_changes_clear_selection_and_keep_collapsed_sections(prototype_server, page):
    origin, (_, _, ids) = prototype_server
    page.goto(origin + '/app')
    category = page.locator('details.category-panel')
    category.locator(':scope > summary').click()
    page.get_by_role('button', name='Select', exact=True).click()
    page.locator(f'.selection-checkbox[value="list:{ids["list"]}"]').first.check()
    page.get_by_label('Sort lists', exact=True).select_option('modified')
    expect(page.locator('#selection-count')).to_have_text('1 selected')
    expect(category).not_to_have_attribute('open', '')
    page.locator('.folder-dropdown summary').click()
    page.locator('.folder-dropdown').get_by_role('link', name='Projects', exact=True).click()
    page.wait_for_url(f'**/app?sort=modified&folder_id={ids["folder"]}')
    expect(page.locator('#daisy-bulk-form')).to_be_hidden()
    expect(page.locator('h1')).to_have_text('Projects.')
    assert page.locator('.selection-checkbox:checked').count() == 0


def test_browser_expired_session_navigates_to_login_without_swapping_login_into_page(prototype_server, page, monkeypatch, tmp_path):
    origin, (db, _, _) = prototype_server
    page.goto(origin + '/app?sort=created')
    credentials = tmp_path / 'htpasswd'
    credentials.write_text('prototype-user:{SHA}dGVzdA==\n')
    monkeypatch.setenv('SIMPLE_TODO_HTPASSWD_PATH', str(credentials))
    monkeypatch.setenv('SIMPLE_TODO_BASIC_AUTH', '1')
    page.get_by_role('button', name='New list', exact=True).click()
    page.get_by_role('textbox', name='List name').fill('Must require authentication')
    page.get_by_role('button', name='Add list', exact=True).click()
    page.wait_for_url('**/login?next=%2Fapp%3Fsort%3Dcreated')
    expect(page.locator('input[name="username"]')).to_be_visible()
    assert page.locator('#daisy-workspace').count() == 0
    assert db.query(List).filter_by(name='Must require authentication').count() == 0


def test_root_redirects_to_new_frontend_and_original_moves_to_classic(prototype):
    db, client, ids = prototype
    response = client.get("/?sort=created&folder_id=0", follow_redirects=False)
    assert response.status_code == 302 and response.headers["location"] == "/app?sort=created&folder_id=0"
    assert client.get("/", follow_redirects=False).headers["location"] == "/app"
    classic = client.get("/classic")
    assert classic.status_code == 200 and "A place to start" in classic.text
    assert 'href="/classic"' in client.get("/search").text  # original pages link home to the original frontend
    page = client.get("/app")
    assert 'href="/classic"' in page.text and "<small>daisyui_test</small>" not in page.text
    edit = client.post(f"/list/{ids['other']}/delete", follow_redirects=False)
    assert edit.status_code == 303 and edit.headers["location"] == "/classic"


def test_old_daisyui_test_urls_redirect_to_app(prototype):
    db, client, ids = prototype
    for old, new in [('/daisyui_test', '/app'), ('/daisyui_test?sort=created', '/app?sort=created'),
                     (f'/daisyui_test/list/{ids["list"]}?folder_id=0', f'/app/list/{ids["list"]}?folder_id=0')]:
        response = client.get(old, follow_redirects=False)
        assert response.status_code == 308 and response.headers['location'] == new
    # A form posted from a page opened before the move still saves.
    response = client.post('/daisyui_test/lists', headers=HX, data={'name': 'From an old tab'})
    assert response.status_code == 200 and 'List created.' in response.text
    assert client.get('/', follow_redirects=False).headers['location'] == '/app'


def test_browser_toolbar_is_fixed_and_content_starts_below_it(prototype_server, page):
    """A sticky toolbar flickered on iOS Safari reloads while scrolled; fixed does not."""
    origin, (db, _, ids) = prototype_server
    for index in range(40):
        db.add(Todo(title=f"Filler task {index}", list_id=ids["list"]))
    db.commit()
    for width in (1200, 360):
        page.set_viewport_size({"width": width, "height": 700})
        page.goto(origin + f"/app/list/{ids['list']}")
        bar = page.evaluate("getComputedStyle(document.querySelector('.topbar')).position")
        height = page.evaluate("document.querySelector('.topbar').getBoundingClientRect().height")
        # The shortcut strip is the first thing under the toolbar, then the page content.
        strip = page.evaluate("document.getElementById('shortcut-strip').getBoundingClientRect()")
        main = page.evaluate("document.getElementById('main-content').getBoundingClientRect().top")
        assert bar == "fixed" and abs(strip["top"] - height) < 1 and abs(main - strip["bottom"]) < 1
        page.evaluate("window.scrollTo(0, 400)")
        assert page.evaluate("document.querySelector('.topbar').getBoundingClientRect().top") == 0


def test_browser_completion_is_square_and_selection_is_round(prototype_server, page):
    origin, (_, _, ids) = prototype_server
    page.goto(origin + f"/app/list/{ids['list']}")
    page.get_by_role("button", name="Select", exact=True).click()
    radius = "el => parseFloat(getComputedStyle(el).borderTopLeftRadius) / el.getBoundingClientRect().width"
    assert page.locator(".completion-checkbox").first.evaluate(radius) < 0.3
    assert page.locator(".selection-checkbox").first.evaluate(radius) >= 0.5

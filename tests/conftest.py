"""
Pytest fixtures for Playwright browser tests.
"""
import os
import pytest
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page


@pytest.fixture(scope="session")
def playwright():
    """Share one Playwright event loop across browser engines and test modules."""
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(playwright):
    """Create a shared browser instance for all tests."""
    browser = getattr(playwright, os.environ.get('PLAYWRIGHT_BROWSER', 'chromium')).launch(headless=True)
    yield browser
    browser.close()


@pytest.fixture
def context(browser: Browser) -> BrowserContext:
    """Create a new browser context for each test."""
    context = browser.new_context()
    yield context
    context.close()


@pytest.fixture
def page(context: BrowserContext) -> Page:
    """Create a new page for each test."""
    page = context.new_page()
    yield page
    page.close()


@pytest.fixture(scope="session")
def test_user():
    return "testuser"


@pytest.fixture
def db_session(test_user):
    from database import get_session_maker, init_db
    import os
    
    # Use a shared test DB
    os.environ["SIMPLE_TODO_DB_PATH"] = "parity_test.db"
    
    SessionLocal = get_session_maker("default") # User doesn't matter if env var is set
    db = SessionLocal()
    
    # Clean previous data if any (or we rely on file deletion)
    # For now, just yield db. 
    # In a real test suite, we'd drop tables or use a transaction rollback.
    # Let's clean all data for the test user to be safe.
    from models import Base
    for table in reversed(Base.metadata.sorted_tables):
        try:
            db.execute(table.delete())
        except:
            pass
    db.commit()
    
    yield db
    db.close()


@pytest.fixture
def live_url(db_session, monkeypatch):
    """Serve the app in-process on a free port, using the db_session test database."""
    import socket
    import threading
    import time
    import uvicorn
    from sqlalchemy.orm import Session
    import main

    monkeypatch.setenv("SIMPLE_TODO_BASIC_AUTH", "0")
    engine = db_session.get_bind()

    def get_db():
        with Session(engine) as session:
            yield session

    main.app.dependency_overrides[main.get_db] = get_db
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(main.app, lifespan="off", log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "Test server did not start"
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        main.app.dependency_overrides.pop(main.get_db, None)

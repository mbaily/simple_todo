"""Exercise public URLs with the same prefix stripping used by Caddy."""
import base64
import hashlib
import socket
import threading
import time
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from main import app, get_db
from models import Base, List, Todo


class PrefixStrippingProxy:
    def __init__(self, app, prefix):
        self.app, self.prefix = app, prefix

    async def __call__(self, scope, receive, send):
        scope = dict(scope)
        if scope['type'] == 'http' and self.prefix:
            path = scope['path']
            if not path.startswith(self.prefix + '/'):
                await send({'type': 'http.response.start', 'status': 404, 'headers': []})
                await send({'type': 'http.response.body', 'body': b'Other app'})
                return
            scope['path'] = path[len(self.prefix):]
            scope['raw_path'] = scope['path'].encode()
        await self.app(scope, receive, send)


@pytest.fixture(params=['', '/simple_todo', '/apps/simple_todo'])
def proxy_app(request, monkeypatch, tmp_path):
    import database
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(database, '_engines', {})
    prefix = request.param
    monkeypatch.setattr(app, 'root_path', prefix)
    monkeypatch.setenv('SIMPLE_TODO_BASIC_AUTH', '1')
    password = '{SHA}' + base64.b64encode(hashlib.sha1(b'test-password').digest()).decode()
    htpasswd = tmp_path / 'htpasswd'
    htpasswd.write_text('proxy-user:' + password + '\n')
    monkeypatch.setenv('SIMPLE_TODO_HTPASSWD_PATH', str(htpasswd))
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        item = List(name='Proxy test list')
        db.add(item)
        db.flush()
        db.add(Todo(title='Proxy test todo', list_id=item.id))
        db.commit()

    def test_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = test_db
    try:
        yield PrefixStrippingProxy(app, prefix), prefix
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def test_proxy_login_api_cookies_and_redirects(proxy_app):
    upstream, prefix = proxy_app
    with TestClient(upstream, base_url='https://example.test') as client:
        # API errors must stay JSON; the login page and assets must remain public.
        response = client.get(prefix + '/api/sync')
        assert response.status_code == 401
        assert response.json() == {'error': 'Unauthorized'}
        assert client.get(prefix + '/static/urls.js').status_code == 200
        response = client.get(prefix + '/list/1?sort=created&folder_id=2', follow_redirects=False)
        assert response.status_code == 302
        target = urlsplit(response.headers['location'])
        assert target.path == prefix + '/login'
        assert parse_qs(target.query)['next'] == ['/list/1?sort=created&folder_id=2']
        response = client.get(response.headers['location'])
        assert response.status_code == 200
        assert f'action="{prefix}/login"' in response.text
        assert f'href="{prefix}/static/daisyui_test/style.css?v=' in response.text
        # The login page's stylesheet is public, so it loads before signing in.
        assert client.get(prefix + '/static/daisyui_test/daisyui-5.7.50.css').status_code == 200

        response = client.post(prefix + '/login', data={
            'username': 'proxy-user', 'password': 'test-password',
            'next': '/list/1?sort=created',
        }, follow_redirects=False)
        assert response.headers['location'] == prefix + '/list/1?sort=created'
        cookie = response.headers['set-cookie']
        assert f'Path={prefix or "/"};' in cookie
        assert 'Secure' in cookie and 'HttpOnly' in cookie
        assert client.get(prefix + '/api/sync').status_code == 200
        assert client.get(prefix + '/list/1').status_code == 200

        response = client.post(prefix + '/list/create', data={'name': 'Created via proxy'}, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers['location'].startswith(prefix + '/')
        assert client.get(response.headers['location']).status_code == 200
        response = client.get(prefix + '/logout', follow_redirects=False)
        assert response.headers['location'] == prefix + '/login'
        assert f'Path={prefix or "/"};' in response.headers['set-cookie']


def test_calendar_bearer_works_behind_prefix(proxy_app, monkeypatch):
    upstream, prefix = proxy_app
    monkeypatch.setenv('SIMPLE_TODO_CALENDAR_API_USER', 'proxy-user')
    monkeypatch.setenv('SIMPLE_TODO_CALENDAR_API_KEY_SHA256', hashlib.sha256(b'calendar-key').hexdigest())
    with TestClient(upstream, base_url='https://example.test') as client:
        response = client.get(prefix + '/api/calendar/events?start_date=2026-11-01&end_date=2026-11-01',
                              headers={'Authorization': 'Bearer calendar-key'})
        assert response.status_code == 200
        assert response.json()['occurrences'] == []
        assert client.get(prefix + '/api/export', headers={'Authorization': 'Bearer calendar-key'}).status_code == 401


@pytest.mark.parametrize('path,query', [
    ('calendar', ''),
    ('calendar', '?month=2026-11&day=2026-11-12&tag=a%2Bb&tag=two'),
    ('bills', ''),
    ('bills', '?show_inactive=1&filter=a%26b'),
])
def test_calendar_and_bills_links_open_new_frontend(proxy_app, path, query):
    upstream, prefix = proxy_app
    with TestClient(upstream, base_url='https://example.test') as client:
        client.post(prefix + '/login', data={'username': 'proxy-user', 'password': 'test-password'})
        response = client.get(prefix + '/' + path + query, follow_redirects=False)
        assert response.status_code == 302
        assert response.headers['location'] == prefix + '/app/' + path + query
        destination = client.get(response.headers['location'])
        assert destination.status_code == 200
        assert 'id="daisy-workspace"' in destination.text
        classic = client.get(prefix + '/classic/' + path)
        assert classic.status_code == 200
        assert 'id="daisy-workspace"' not in classic.text


def test_rendered_page_urls_use_the_public_prefix(proxy_app):
    upstream, prefix = proxy_app

    class PageURLs(HTMLParser):
        def handle_starttag(self, tag, attrs):
            for key, value in attrs:
                if key in {'href', 'src', 'action'} and value and value.startswith('/'):
                    assert value.startswith(prefix + '/'), (tag, key, value)

    with TestClient(upstream, base_url='https://example.test') as client:
        client.post(prefix + '/login', data={'username': 'proxy-user', 'password': 'test-password'})
        for path in ['/', '/list/1', '/folders', '/classic/bills', '/expenses', '/pantry',
                     '/classic/calendar', '/customization', '/journal']:
            response = client.get(prefix + path)
            assert response.status_code == 200, path
            PageURLs().feed(response.text)


def test_browser_requests_and_navigation_stay_under_prefix(proxy_app, browser):
    upstream, prefix = proxy_app
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(upstream, lifespan='off', log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    origin = f'http://127.0.0.1:{port}'
    context = browser.new_context()
    page = context.new_page()
    failures, errors = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('response', lambda response: failures.append(response.url) if response.status >= 400 else None)
    try:
        page.goto(origin + prefix + '/list/1')
        page.locator('input[name=username]').fill('proxy-user')
        page.locator('input[name=password]').fill('test-password')
        page.locator('button[type=submit]').click()
        page.wait_for_url(origin + prefix + '/list/1')
        page.wait_for_load_state('networkidle')
        with page.expect_response(lambda response: '/api/todo/1/toggle' in response.url) as changed:
            page.locator('input[type=checkbox][data-type=todo][data-id="1"]').last.check()
        assert changed.value.status == 200
        assert urlsplit(changed.value.url).path == prefix + '/api/todo/1/toggle'
        # Shared URL code resolves relative paths from nested pages and leaves external URLs alone.
        assert page.evaluate('SimpleTodo.url("api/sync")') == prefix + '/api/sync'
        assert page.evaluate('SimpleTodo.url("https://other.test/app")') == 'https://other.test/app'
        assert page.evaluate('(path) => SimpleTodo.url(path)', prefix + '/api/sync') == prefix + '/api/sync'
        if prefix:
            assert page.evaluate('SimpleTodo.routePath("/other_app/")') is None
            assert page.evaluate('(path) => SimpleTodo.url(path)', prefix + '?sort=created') == prefix + '?sort=created'
        page.goto(origin + prefix + '/folders')
        page.once('dialog', lambda dialog: dialog.accept('Proxy folder'))
        with page.expect_response(lambda response: response.request.method == 'POST' and '/folders/create' in response.url) as created:
            page.get_by_title('Create Unfiled Folder').click()
        assert urlsplit(created.value.url).path == prefix + '/folders/create'
        page.wait_for_load_state('networkidle')
        assert page.get_by_text('Proxy folder', exact=True).count() > 0
        page.goto(origin + prefix + '/')
        page.wait_for_load_state('networkidle')
        assert urlsplit(page.url).path == prefix + '/app'
        page.goto(origin + prefix + '/classic')
        page.wait_for_load_state('networkidle')
        page.locator(f'a[href="{prefix}/journal"]').click()
        page.wait_for_load_state('networkidle')
        assert urlsplit(page.url).path == prefix + '/journal'
        assert not failures, failures
        assert not errors, errors
    finally:
        context.close()
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()

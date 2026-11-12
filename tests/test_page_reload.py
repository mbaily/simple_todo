from datetime import datetime
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from main import templates
from models import CalendarEvent, Folder, List, SmartFolder, Todo, UserSettings
from test_billing_accounts import account


@pytest.fixture(scope='module', params=['chromium', 'webkit'])
def reload_browser(request, playwright):
    browser = getattr(playwright, request.param).launch(headless=True)
    yield browser
    browser.close()


@pytest.mark.parametrize('path,traditional_view', [
    ('/classic', False),
    ('/classic/calendar?month=2026-10', False),
    ('/classic/calendar?month=2026-10', True),
    ('/smart-folders', False),
    ('/smart-folder/1', False),
    ('/folders', False),
    ('/list/1', False),
], ids=['home', 'calendar-list', 'calendar-month', 'smart-folders', 'smart-results', 'folders', 'list'])
@pytest.mark.parametrize('width,height', [(1280, 720), (390, 500), (844, 390)],
                         ids=['laptop', 'portrait', 'landscape'])
def test_reload_waits_for_content_without_painting_toolbar_or_blank_background(
        account, reload_browser, monkeypatch, path, traditional_view, width, height):
    db, _, client = account
    monkeypatch.setitem(templates.env.globals, 'disable_offline_cache', True)
    db.add(UserSettings(id=1, calendar_traditional_view=traditional_view))
    db.add_all([
        CalendarEvent(title=f'Daily appointment {number}', start_at=datetime(2026, 10, 1),
                      rrule='FREQ=DAILY')
        for number in range(16)
    ])
    for number in range(32):
        db.add_all([
            Folder(name=f'Folder {number}'),
            SmartFolder(name=f'Smart folder {number}'),
            List(name=f'List {number}', todos=[Todo(title=f'Task {item}')
                                             for item in range(40 if number == 0 else 1)]),
        ])
    db.commit()

    with reload_browser.new_context(viewport={'width': width, 'height': height},
                                    color_scheme='dark' if width < 1000 else 'light') as context:
        page = context.new_page()
        page.add_init_script('''
            window.reloadPaints = [];
            function sample() {
                const toolbar = document.querySelector('.nav-icon');
                if (toolbar) reloadPaints.push({
                    visible: getComputedStyle(toolbar).visibility === 'visible',
                    y: scrollY,
                });
                requestAnimationFrame(sample);
            }
            requestAnimationFrame(sample);
        ''')
        pending_scripts = []

        def serve(route):
            request = route.request
            url = urlsplit(request.url)
            if url.path == '/pause-page.js':
                pending_scripts.append(route)
                return
            response = client.request(
                request.method, url.path + ('?' + url.query if url.query else ''),
                content=request.post_data_buffer,
                headers={'content-type': request.headers.get('content-type', '')},
                follow_redirects=True,
            )
            if request.is_navigation_request():
                assert response.status_code == 200
                # Pause after the toolbar, before enough content has arrived to
                # restore scrolling. This exposes both toolbar and blank paints.
                html = response.text.replace('</div>',
                                            '</div><script src="/pause-page.js"></script>', 1)
                route.fulfill(content_type='text/html', body=html)
            else:
                route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

        page.route('http://app.test/**', serve)
        toolbar = page.locator('.nav-icon').first
        page.goto('http://app.test' + path, wait_until='commit')
        page.wait_for_function("document.querySelector('script[src=\"/pause-page.js\"]') !== null", polling=50)
        pending_scripts.pop().fulfill(content_type='application/javascript', body='')
        page.wait_for_load_state()
        page.evaluate('''async () => {
            await new Promise(requestAnimationFrame);
            await new Promise(requestAnimationFrame);
            scrollTo(0, document.documentElement.scrollHeight);
            await new Promise(requestAnimationFrame);
        }''')
        previous_y = page.evaluate('scrollY')
        assert previous_y > 50, 'Fixture must scroll far enough to hide the toolbar'
        assert toolbar.bounding_box()['y'] < 0

        # WebKit's automation reload command resets scrolling; use the browser's
        # own reload so this exercises the scroll restoration users experience.
        with page.expect_navigation(wait_until='commit'):
            page.evaluate('location.reload()')
        page.wait_for_function("document.querySelector('script[src=\"/pause-page.js\"]') !== null", polling=50)
        # Leave time for an unwanted paint during this deliberately slow reload.
        page.wait_for_timeout(150)
        assert page.evaluate('scrollY') == 0
        assert page.evaluate('getComputedStyle(document.body).visibility') == 'visible'
        assert page.evaluate('performance.getEntriesByType("paint")') == []
        assert page.evaluate('reloadPaints') == []
        pending_scripts.pop().fulfill(content_type='application/javascript', body='')
        page.wait_for_load_state()
        # Chromium can restore up to 2px short on the classic list page at the landscape
        # phone size (restoration lands just before the final layout); that is not visible.
        page.wait_for_function('previous => Math.abs(scrollY - previous) <= 2', arg=previous_y)
        expect(toolbar).to_be_visible()
        assert toolbar.bounding_box()['y'] < 0
        page.evaluate('async () => { await new Promise(requestAnimationFrame); }')
        paints = page.evaluate('reloadPaints')
        assert paints
        assert all(frame['visible'] and abs(frame['y'] - previous_y) <= 2 for frame in paints)


def test_pages_render_without_javascript(account, reload_browser):
    db, _, client = account
    db.add_all(Folder(name=f'Folder {number}') for number in range(32))
    db.commit()
    with reload_browser.new_context(java_script_enabled=False) as context:
        page = context.new_page()

        def serve(route):
            response = client.get(urlsplit(route.request.url).path)
            route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

        page.route('http://app.test/**', serve)
        page.goto('http://app.test/folders')
        expect(page.get_by_role('heading', name='Manage Folders')).to_be_visible()
        expect(page.get_by_text('Folder 31', exact=True)).to_be_visible()
        with page.expect_navigation():
            page.evaluate('location.reload()')
        expect(page.get_by_role('heading', name='Manage Folders')).to_be_visible()

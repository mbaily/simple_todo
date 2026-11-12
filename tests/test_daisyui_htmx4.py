from playwright.sync_api import expect
from test_daisyui_test import prototype, prototype_server


def test_unhandled_http_error_keeps_workspace_and_draft(prototype_server, page):
    origin, _ = prototype_server
    page.goto(origin + '/app')
    page.get_by_role('button', name='New list', exact=True).click()
    page.get_by_role('textbox', name='List name').fill('Do not lose this draft')
    page.route('**/app/lists?*', lambda route: route.fulfill(status=503, content_type='text/html', body='<p>Service unavailable</p>'))
    page.get_by_role('button', name='Add list', exact=True).click()
    expect(page.locator('#page-feedback')).to_contain_text('HTTP 503')
    expect(page.locator('#daisy-workspace')).to_be_visible()
    expect(page.get_by_role('textbox', name='List name')).to_have_value('Do not lose this draft')
    expect(page.get_by_role('button', name='Add list', exact=True)).to_be_enabled()


def test_network_error_rolls_back_completion(prototype_server, page):
    origin, _ = prototype_server
    page.goto(origin + '/app')
    page.route('**/app/complete/todo/*', lambda route: route.abort())
    checkbox = page.get_by_role('checkbox', name='Mark Book a quiet afternoon complete', exact=True)
    # click(), not check(): the aborted request can roll the box back before check() verifies it.
    checkbox.click()
    expect(page.locator('#page-feedback')).to_contain_text('could not be completed')
    expect(checkbox).not_to_be_checked()
    expect(checkbox).to_be_enabled()


def test_pending_submit_disables_button_shows_indicator_and_drops_duplicate(prototype_server, page):
    origin, _ = prototype_server
    page.add_init_script('''
        const original = window.fetch;
        window.fetch = async (...args) => {
            if (String(args[0]).includes('/app/lists')) {
                window.pendingCount = (window.pendingCount || 0) + 1;
                await new Promise(resolve => window.releaseRequest = resolve);
            }
            return original(...args);
        };
    ''')
    page.goto(origin + '/app')
    page.get_by_role('button', name='New list', exact=True).click()
    page.get_by_role('textbox', name='List name').fill('Only one list')
    page.get_by_role('button', name='Add list', exact=True).click()
    expect(page.get_by_role('button', name='Add list', exact=True)).to_be_disabled()
    expect(page.locator('#workspace-loading')).to_have_class(__import__('re').compile('htmx-request'))
    page.locator('#create-list-form').evaluate('(form) => form.requestSubmit()')
    assert page.evaluate('window.pendingCount') == 1
    page.evaluate('window.releaseRequest()')
    expect(page.get_by_role('link', name='Only one list', exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='Add list', exact=True)).to_be_enabled()
    expect(page.locator('#workspace-loading')).not_to_have_class(__import__('re').compile('htmx-request'))


def test_updates_morph_in_place_keeping_scroll_and_elements(prototype_server, page):
    origin, (db, _, ids) = prototype_server
    from models import Todo
    db.add_all([Todo(title=f'Long task {n:02d}', list_id=ids['list']) for n in range(60)])
    db.commit()
    page.set_viewport_size({'width': 1000, 'height': 600})
    page.goto(origin + f'/app/list/{ids["list"]}')
    target = page.get_by_role('checkbox', name='Mark Long task 00 complete', exact=True)
    target.scroll_into_view_if_needed()
    page.evaluate('document.querySelector("#daisy-workspace").dataset.marker = "kept"')
    page.evaluate('[...document.querySelectorAll(".task-row")].forEach(row => row.morphMarker = row.id)')
    assert page.evaluate('scrollY') > 300
    before = target.bounding_box()['y']
    target.check()
    expect(page.locator('#page-feedback')).to_contain_text('Completion updated.')
    # The workspace and its rows are updated in place rather than replaced.
    assert page.evaluate('[...document.querySelectorAll(".task-row")].filter(row => row.morphMarker === row.id).length') >= 59
    # The ticked task stays where it was on screen (the browser compensates for the feedback above it).
    done = page.get_by_role('checkbox', name='Mark Long task 00 incomplete', exact=True)
    expect(done).to_be_checked()
    assert abs(done.bounding_box()['y'] - before) < 5


def test_links_navigate_by_morphing_without_reloading(prototype_server, page):
    origin, (db, _, ids) = prototype_server
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(origin + '/app')
    page.evaluate('window.notReloaded = true')
    page.locator('.toolbar-navigation').get_by_role('link', name='Calendar').click()
    expect(page).to_have_url(origin + '/app/calendar')
    expect(page.get_by_role('heading', name='Calendar', exact=True)).to_be_visible()
    assert page.evaluate('window.notReloaded') is True
    assert page.title() == 'Calendar · Simple Todo'
    expect(page.locator('.toolbar-navigation').get_by_role('link', name='Calendar')).to_have_attribute('aria-current', 'page')
    expect(page.locator('.toolbar-navigation').get_by_role('link', name='Overview')).not_to_have_attribute('aria-current', 'page')
    # Workspace links and the selection toolbar's Edit also stay in the page.
    page.go_back()
    expect(page.get_by_role('heading', name='Overview.')).to_be_visible()
    page.get_by_role('link', name='A place to start').first.click()
    expect(page.get_by_role('heading', name='A place to start')).to_be_visible()
    assert page.evaluate('window.notReloaded') is True
    page.get_by_role('button', name='Select', exact=True).click()
    page.get_by_role('checkbox', name='Select Book a quiet afternoon').check()
    page.locator('#daisy-bulk-form').get_by_role('button', name='Edit').click()
    expect(page.get_by_role('heading', name='Edit task')).to_be_visible()
    assert page.evaluate('window.notReloaded') is True
    # Links to the original frontend still load normally.
    page.locator('summary[aria-label="More navigation"]').click()
    page.get_by_role('link', name='Original frontend').click()
    expect(page).to_have_url(origin + '/classic')
    assert page.evaluate('window.notReloaded') is None
    assert not errors

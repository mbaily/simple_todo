"""Calendar layouts, preferences, occurrence completion and day tools in the daisyUI frontend."""
from datetime import datetime, timedelta

import re

import pytest
from playwright.sync_api import expect

from models import CalendarDay, CalendarEvent, CalendarEventCompletion, EventGroup, JournalEntry, UserSettings
from test_daisyui_test import HX, prototype, prototype_server

Q = '?view=calendar'


def weekly(db):
    start = datetime.now().replace(hour=9, minute=30, second=0, microsecond=0) - timedelta(days=14)
    event = CalendarEvent(title='Weekly review', start_at=start, all_day=False, rrule='FREQ=WEEKLY')
    group = EventGroup(name='Work')
    db.add_all([event, group])
    db.commit()
    return event, group


def layout(client, value):
    return client.post('/app/calendar/view' + Q, headers=HX, data={'layout': value})


def test_layouts_match_original_and_persist(prototype):
    db, client, ids = prototype
    event, _ = weekly(db)
    today = datetime.now()
    for value, key in [('list', 'occurrences'), ('month', 'weeks'), ('week', 'week_days')]:
        response = layout(client, value)
        assert response.status_code == 200 and response.headers['HX-Push-Url'] == '/app/calendar'
        db.expire_all()
        settings = db.query(UserSettings).one()
        assert (settings.calendar_traditional_view, settings.calendar_weekly_view) == (value == 'month', value == 'week')
        full = client.get('/app/calendar')
        fragment = client.get('/app/calendar', headers=HX)
        original = client.get('/classic/calendar')
        assert full.status_code == fragment.status_code == original.status_code == 200
        assert '<!DOCTYPE html>' in full.text and '<!DOCTYPE html>' not in fragment.text
        assert full.context['view'] == value and 'aria-current="page"' in full.text
        flatten = {
            'occurrences': lambda c: [(r['event'].id, r['occurrence_at'], r['completed']) for r in c['occurrences']],
            'weeks': lambda c: [(d['day'], [(r['event'].id, r['occurrence_at']) for r in d['events']]) for w in c['weeks'] for d in w],
            'week_days': lambda c: [(d['date'], [(r['event'].id, r['occurrence_at']) for r in d['all_day'] + d['timed']]) for d in c['week_days']],
        }[key]
        assert flatten(full.context) == flatten(original.context)
        assert 'Weekly review' in full.text and 'An upcoming appointment' in full.text
    assert client.post('/app/calendar/view' + Q, headers=HX, data={'layout': 'year'}).status_code == 400
    assert client.get('/app/calendar?week=2027-13-01').status_code == 400
    assert client.get('/app/calendar?month=bad').status_code == 400
    assert client.get('/app/calendar?offset=9999').status_code == 400


def test_navigation_filter_sort_and_display(prototype):
    db, client, ids = prototype
    _, group = weekly(db)
    layout(client, 'month')
    page = client.get('/app/calendar?month=2027-02')
    assert page.context['period_title'] == 'February 2027'
    assert page.context['prev_url'].endswith('month=2027-01') and page.context['next_url'].endswith('month=2027-03')
    assert page.context['query'] == '?month=2027-02&view=calendar'

    query = '?month=2027-02&view=calendar'
    response = client.post('/app/calendar/filter' + query, headers=HX, data={'calendar_event_filter': 'recurring'})
    assert response.status_code == 200 and 'February 2027' in response.text
    assert client.post('/app/calendar/filter' + query, headers=HX, data={'calendar_event_filter': f'group:{group.id}'}).status_code == 200
    assert client.post('/app/calendar/filter' + query, headers=HX, data={'calendar_event_filter': 'group:999'}).status_code == 400
    assert client.post('/app/calendar/sort' + query, headers=HX, data={'calendar_sort_order': 'title_desc'}).status_code == 200
    assert client.post('/app/calendar/sort' + query, headers=HX, data={'calendar_sort_order': 'random'}).status_code == 400
    client.post('/app/calendar/display/calendar_show_counts' + query, headers=HX, data={'enabled': 'true'})
    assert client.post('/app/calendar/display/theme' + query, headers=HX, data={'enabled': 'true'}).status_code == 400
    db.expire_all()
    settings = db.query(UserSettings).one()
    assert settings.calendar_event_filter == f'group:{group.id}' and settings.calendar_sort_order == 'title_desc'
    assert settings.calendar_show_counts
    redirect = client.post('/app/calendar/sort' + query, data={'calendar_sort_order': 'title_asc'}, follow_redirects=False)
    assert redirect.status_code == 303 and redirect.headers['location'] == '/app/calendar?month=2027-02'


def test_occurrence_completion_is_idempotent_and_validated(prototype):
    db, client, ids = prototype
    event, _ = weekly(db)
    occurrence = event.start_at + timedelta(days=7)
    url = f'/app/calendar/occurrence/{event.id}' + Q
    for _ in range(2):
        response = client.post(url, headers=HX, data={'occurrence_at': occurrence.isoformat(), 'completed': 'true'})
        assert response.status_code == 200 and 'Occurrence completed.' in response.text
    assert db.query(CalendarEventCompletion).filter_by(event_id=event.id, occurrence_at=occurrence).count() == 1
    assert db.query(JournalEntry).filter_by(action='Toggled Calendar Occurrence').count() == 1
    assert 'Occurrence reopened.' in client.post(url, headers=HX, data={'occurrence_at': occurrence.isoformat()}).text
    assert db.query(CalendarEventCompletion).count() == 0
    off_schedule = client.post(url, headers=HX, data={'occurrence_at': (occurrence + timedelta(hours=1)).isoformat(), 'completed': 'true'})
    assert off_schedule.status_code == 400 and off_schedule.headers['HX-Retarget'] == '#page-feedback'
    assert client.post(url, headers=HX, data={'occurrence_at': 'soon', 'completed': 'true'}).status_code == 400
    assert client.post('/app/calendar/occurrence/999' + Q, headers=HX, data={'occurrence_at': occurrence.isoformat()}).status_code == 404


def test_day_tools(prototype):
    db, client, ids = prototype
    event, _ = weekly(db)
    layout(client, 'month')
    day = event.start_at.strftime('%Y-%m-%d')
    month = event.start_at.strftime('%Y-%m')
    query = f'?month={month}&day={day}&view=calendar'
    page = client.get(f'/app/calendar?month={month}&day={day}')
    assert page.context['selected']['date'] == day and 'Complete day' in page.text
    assert client.get(f'/app/calendar?month={month}&day=2001-01-01').context['selected'] is None

    response = client.post(f'/app/calendar/day/{day}/note{query}', headers=HX, data={'note': '  Bring slides  '})
    assert response.status_code == 200 and 'Day note saved.' in response.text and 'Bring slides' in response.text
    assert client.post(f'/app/calendar/day/{day}/note{query}', headers=HX, data={'note': 'x' * 2001}).status_code == 400
    client.post(f'/app/calendar/day/{day}/hide-completed{query}', headers=HX, data={'enabled': 'true'})
    response = client.post(f'/app/calendar/day/{day}/complete{query}', headers=HX)
    assert 'Completed every event on this day.' in response.text
    db.expire_all()
    state = db.query(CalendarDay).one()
    assert state.note == 'Bring slides' and state.hide_completed
    assert db.query(CalendarEventCompletion).filter_by(event_id=event.id, occurrence_at=event.start_at).count() == 1
    assert client.post(f'/app/calendar/day/2027-02-30/complete{query}', headers=HX).status_code == 400


def test_browser_calendar_workflow(prototype_server, page):
    origin, (db, _, ids) = prototype_server
    event, _ = weekly(db)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(origin + '/app')
    page.locator('.toolbar-navigation').get_by_role('link', name='Calendar').click()
    expect(page).to_have_url(origin + '/app/calendar')
    today_label = datetime.now().strftime('%-d %b')
    checkbox = page.get_by_role('checkbox', name=f'Mark An upcoming appointment on {today_label} done')
    checkbox.check()
    expect(page.locator('#page-feedback')).to_contain_text('Occurrence completed.')
    expect(page.get_by_role('checkbox', name=f'Mark An upcoming appointment on {today_label} not done')).to_be_checked()

    page.get_by_role('button', name='Month').click()
    expect(page.get_by_role('button', name='Month')).to_have_attribute('aria-pressed', 'true')
    page.locator('.month-cell.is-today .month-day').click()
    expect(page.get_by_role('heading', name=datetime.now().strftime('%A %-d %B %Y'))).to_be_visible()
    page.get_by_role('textbox', name='Day note').fill('Quiet day')
    page.get_by_role('button', name='Save note').click()
    expect(page.locator('#day-dialog #page-feedback')).to_contain_text('Day note saved.')
    # The day opens as a real modal: Escape closes it and Back reopens it.
    assert page.locator('#day-dialog').evaluate("dialog => dialog.matches(':modal')")
    page.keyboard.press('Escape')
    expect(page.locator('#day-dialog')).to_have_count(0)
    page.get_by_role('link', name='Next month').click()
    expect(page.locator('#calendar-period')).not_to_contain_text(datetime.now().strftime('%B'))
    page.go_back()
    expect(page.locator('#calendar-period')).to_contain_text(datetime.now().strftime('%B'))
    page.go_back()
    expect(page.get_by_role('textbox', name='Day note')).to_have_value('Quiet day')
    assert page.locator('#day-dialog').evaluate("dialog => dialog.matches(':modal')")
    page.get_by_role('link', name='Close day').first.click()
    expect(page.locator('#day-dialog')).to_have_count(0)

    page.get_by_role('button', name='Week').click()
    expect(page.locator('.week-day.is-today')).to_contain_text('Weekly review' if datetime.now().weekday() == event.start_at.weekday() else datetime.now().strftime('%-d %b'))
    page.get_by_role('link', name='Next week').click()
    expect(page.locator('.week-day')).to_have_count(7)
    expect(page.locator('.week-day', has_text='Weekly review')).to_have_count(1)
    assert not errors


@pytest.mark.parametrize('calendar_layout', ['list', 'month', 'week'])
@pytest.mark.parametrize('width', [360, 1000])
def test_browser_jump_to_today_scrolls_and_only_loads_when_needed(prototype_server, page, calendar_layout, width):
    origin, (db, client, _) = prototype_server
    today = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    db.add_all([
        CalendarEvent(title=f'Appointment {day} {number}', start_at=today + timedelta(days=day), all_day=True)
        for day in range(-7, 36) for number in range(6)
    ])
    db.commit()
    layout(client, calendar_layout)
    page.set_viewport_size({'width': width, 'height': 320})
    page.goto(origin + '/app/calendar')
    page.evaluate('window.calendarDocument = true')
    requests = []
    page.on('request', lambda request: requests.append(request.url) if request.url.split('?')[0] == origin + '/app/calendar' else None)
    jump = page.get_by_role('link', name='Jump to today', exact=True)
    expect(page.get_by_role('link', name='Today', exact=True)).to_have_text('Today')
    assert jump.inner_text() == ''
    assert jump.locator('svg').count() == 1
    assert 'btn' not in jump.get_attribute('class').split()
    assert page.locator('#calendar-period').evaluate('(period) => period.nextElementSibling.matches(".calendar-today-link")')
    period_box, icon_box = page.locator('#calendar-period').bounding_box(), jump.bounding_box()
    assert icon_box['x'] >= period_box['x'] + period_box['width']
    assert abs(icon_box['y'] + icon_box['height'] / 2 - period_box['y'] - period_box['height'] / 2) < 2

    def assert_today_scrolled():
        page.wait_for_function('''() => {
            const root = document.getElementById('daisy-workspace');
            if (root.dataset.calendarContainsToday !== 'true') return false;
            const target = root.querySelector(`[data-calendar-date="${root.dataset.calendarToday}"]`);
            return Math.abs(target.getBoundingClientRect().top
                - document.querySelector('.topbar').getBoundingClientRect().bottom - 8) < 2;
        }''')

    jump.click()
    assert_today_scrolled()
    assert requests == []
    assert page.evaluate('window.calendarDocument') is True

    next_label = 'Next ' + {'list': 'period', 'month': 'month', 'week': 'week'}[calendar_layout]
    page.get_by_role('link', name=next_label).click()
    expect(page.locator('#daisy-workspace')).to_have_attribute('data-calendar-contains-today', 'false')
    requests.clear()
    jump.click()
    assert_today_scrolled()
    assert requests == [origin + '/app/calendar']
    assert page.evaluate('window.calendarDocument') is True
    expect(page.get_by_role('button', name={'list': 'Agenda', 'month': 'Month', 'week': 'Week'}[calendar_layout], exact=True)).to_have_attribute('aria-pressed', 'true')


def test_browser_jump_to_today_with_no_agenda_events_today(prototype_server, page):
    origin, (db, client, _) = prototype_server
    today = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    db.query(CalendarEvent).delete()
    db.add_all([CalendarEvent(title=f'Day {day}', start_at=today + timedelta(days=day), all_day=True)
                for day in [-6, -5, -4, -3, -2, -1, 1, 2, 3, 4, 5, 6]])
    db.commit()
    layout(client, 'list')
    page.set_viewport_size({'width': 360, 'height': 320})
    page.goto(origin + '/app/calendar')
    requests = []
    page.on('request', lambda request: requests.append(request.url))
    page.get_by_role('link', name='Jump to today').click()
    tomorrow = (today + timedelta(days=1)).strftime('%Y-%m-%d')
    target = page.locator(f'[data-calendar-date="{tomorrow}"]')
    assert abs(target.bounding_box()['y'] - page.locator('.topbar').bounding_box()['height'] - 8) < 2
    assert requests == []


@pytest.mark.parametrize('calendar_layout', ['list', 'month', 'week'])
def test_browser_jump_to_today_aligns_today_even_on_short_pages(prototype_server, page, calendar_layout):
    origin, (_, client, _) = prototype_server
    layout(client, calendar_layout)
    page.set_viewport_size({'width': 1200, 'height': 1100})
    page.goto(origin + '/app')
    page.locator('.toolbar-navigation').get_by_role('link', name='Calendar').click()
    page.get_by_role('link', name='Jump to today', exact=True).click()
    page.wait_for_function('''() => {
        const root = document.getElementById('daisy-workspace');
        const today = root.querySelector(`[data-calendar-date="${root.dataset.calendarToday}"]`);
        return Math.abs(today.getBoundingClientRect().top
            - document.querySelector('.topbar').getBoundingClientRect().bottom - 8) < 2;
    }''')


def test_quick_add_event_from_day_panel(prototype):
    db, client, ids = prototype
    layout(client, 'month')
    query = '?month=2027-03&day=2027-03-04&view=calendar'
    response = client.post('/app/calendar/day/2027-03-04/event' + query, headers=HX, data={'title': ' Dentist ', 'start_time': '14:30'})
    assert response.status_code == 200 and 'Added Dentist at 14:30.' in response.text and 'Thursday 4 March 2027' in response.text
    client.post('/app/calendar/day/2027-03-04/event' + query, headers=HX, data={'title': 'Bin night', 'start_time': ''})
    db.expire_all()
    dentist = db.query(CalendarEvent).filter_by(title='Dentist').one()
    bins = db.query(CalendarEvent).filter_by(title='Bin night').one()
    assert dentist.start_at == datetime(2027, 3, 4, 14, 30) and not dentist.all_day and dentist.rrule is None
    assert bins.start_at == datetime(2027, 3, 4) and bins.all_day
    assert db.query(JournalEntry).filter_by(action='Created Calendar Event').count() == 2
    for data, status in [({'title': ' '}, 400), ({'title': 'X', 'start_time': '25:00'}, 400), ({'title': 'x' * 501}, 400)]:
        bad = client.post('/app/calendar/day/2027-03-04/event' + query, headers=HX, data=data)
        assert bad.status_code == status and bad.headers['HX-Retarget'] == '#page-feedback'
    assert client.post('/app/calendar/day/2027-02-30/event' + query, headers=HX, data={'title': 'X'}).status_code == 400


def test_browser_quick_add_event(prototype_server, page):
    origin, (db, client, ids) = prototype_server
    layout(client, 'month')
    page.goto(origin + '/app/calendar')
    page.locator('.month-cell.is-today .month-day').click()
    page.get_by_role('textbox', name='New event title').fill('Call the plumber')
    page.get_by_role('textbox', name='New event title').press('Enter')
    expect(page.locator('#page-feedback')).to_contain_text('Added Call the plumber.')
    expect(page.locator('.day-panel')).to_contain_text('Call the plumber')
    expect(page.locator('.month-cell.is-today')).to_contain_text('Call the plumber')
    expect(page.get_by_role('textbox', name='New event title')).to_have_value('')


def test_browser_agenda_rows_wrap_as_one_line_on_phones(prototype_server, page):
    origin, (db, client, ids) = prototype_server
    group = EventGroup(name='Household')
    db.add(group)
    db.flush()
    start = datetime.now().replace(hour=19, minute=0, second=0, microsecond=0)
    db.add(CalendarEvent(title='Take the green waste bin out to the kerb', start_at=start, all_day=False,
                         rrule='FREQ=WEEKLY', event_group_id=group.id))
    db.commit()
    layout(client, 'list')
    page.set_viewport_size({'width': 360, 'height': 740})
    page.goto(origin + '/app/calendar')
    row = page.locator('.calendar-row', has_text='green waste bin').first
    # Details flow after the title instead of being squeezed into narrow columns.
    for text in ('Recurring', 'Household'):
        box = row.locator('.item-context', has_text=text).bounding_box()
        line = row.evaluate('row => parseFloat(getComputedStyle(row).fontSize)')
        assert box['height'] < line * 1.8, f'{text} wrapped onto several lines'
    assert page.evaluate('document.documentElement.scrollWidth - innerWidth') == 0


def test_browser_month_grid_completion_checkboxes(prototype_server, page):
    origin, (db, client, ids) = prototype_server
    layout(client, 'month')
    page.set_viewport_size({'width': 1200, 'height': 800})
    page.goto(origin + '/app/calendar')
    today_label = datetime.now().strftime('%-d %b')
    grid = page.locator('.month-cell.is-today')
    grid.get_by_role('checkbox', name=f'Mark An upcoming appointment on {today_label} done').check()
    expect(page.locator('#page-feedback')).to_contain_text('Occurrence completed.')
    done = grid.get_by_role('checkbox', name=f'Mark An upcoming appointment on {today_label} not done')
    expect(done).to_be_checked()
    expect(grid.locator('li.is-completed')).to_have_count(1)
    done.uncheck()
    expect(grid.get_by_role('checkbox', name=f'Mark An upcoming appointment on {today_label} done')).not_to_be_checked()
    # Phones keep the coloured dots and tick events off in the day panel instead.
    page.set_viewport_size({'width': 360, 'height': 740})
    expect(grid.locator('.completion-checkbox')).to_be_hidden()


def test_day_menu_in_month_and_week(prototype):
    db, client, ids = prototype
    today = datetime.now()
    day = today.strftime('%Y-%m-%d')
    layout(client, 'month')
    page = client.get('/app/calendar')
    assert f'aria-label="Actions for {day}"' in page.text
    assert f'/app/calendar/event/new?date={day}&amp;month={today:%Y-%m}' in page.text
    assert f'/app/calendar?month={today:%Y-%m}&amp;day={day}' in page.text

    layout(client, 'week')
    week = (today - timedelta(days=today.weekday())).strftime('%Y-%m-%d')
    query = f'?week={week}&view=calendar'
    page = client.get('/app/calendar')
    assert page.text.count('class="dropdown dropdown-end row-dropdown day-menu"') == 7
    assert f'/app/calendar/event/new?date={day}&amp;week={week}' in page.text
    assert f'/app/calendar?week={week}&amp;day={day}' in page.text and 'id="day-dialog"' not in page.text
    opened = client.get(f'/app/calendar?week={week}&day={day}')
    assert opened.context['selected']['date'] == day and 'id="day-dialog"' in opened.text
    assert [row['event'].title for row in opened.context['selected']['events']] == ['An upcoming appointment']
    assert client.get(f'/app/calendar?week={week}&day=2001-01-01').context['selected'] is None
    shown = next(d for d in page.context['week_days'] if d['date'].date() == today.date())
    assert (shown['remaining'], shown['note'], shown['hide_completed']) == (1, '', False)

    response = client.post(f'/app/calendar/day/{day}/note{query}', headers=HX, data={'note': 'Pack lunch'})
    assert 'Day note saved.' in response.text and '<p class="week-day-note">Pack lunch</p>' in response.text
    response = client.post(f'/app/calendar/day/{day}/complete{query}', headers=HX)
    assert 'Completed every event on this day.' in response.text and 'Complete day' not in response.text
    assert 'An upcoming appointment' in response.text
    response = client.post(f'/app/calendar/day/{day}/hide-completed{query}', headers=HX, data={'enabled': 'true'})
    assert 'An upcoming appointment' not in response.text and '1 done, hidden' in response.text
    response = client.post(f'/app/calendar/day/{day}/hide-completed{query}', headers=HX, data={})
    assert 'An upcoming appointment' in response.text and 'done, hidden' not in response.text


@pytest.mark.parametrize('calendar_layout', ['month', 'week'])
def test_browser_day_menu_stays_on_screen_and_saves(prototype_server, page, calendar_layout):
    origin, (db, client, ids) = prototype_server
    layout(client, calendar_layout)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.set_viewport_size({'width': 1200, 'height': 800})
    page.goto(origin + '/app/calendar')
    # Every menu, including the first column's and the last row's, opens inside the viewport.
    menus = page.locator('details.day-menu')
    for index in {0, menus.count() - 1}:
        menus.nth(index).locator('summary').click()
        content = menus.nth(index).locator('.dropdown-content')
        expect(content).to_have_css('position', 'fixed')
        box = content.bounding_box()
        assert box['x'] >= 0 and box['x'] + box['width'] <= 1200 and box['y'] >= 0 and box['y'] + box['height'] <= 800
        page.keyboard.press('Escape')
    today = page.locator('.month-cell.is-today, .week-day.is-today')
    today.locator('.day-menu summary').click()
    today.get_by_role('textbox').fill('From the menu')
    today.get_by_role('button', name='Save note').click()
    expect(page.locator('#page-feedback')).to_contain_text('Day note saved.')
    expect(today.locator('.day-menu')).not_to_have_attribute('open', '')
    today.locator('.day-menu summary').click()
    page.once('dialog', lambda dialog: dialog.accept())
    today.get_by_role('button', name='Complete day').click()
    expect(page.locator('#page-feedback')).to_contain_text('Completed every event on this day.')
    today.locator('.day-menu summary').click()
    today.get_by_role('checkbox', name='Hide done').check()
    expect(today.get_by_role('link', name='An upcoming appointment')).to_have_count(0)
    db.expire_all()
    state = db.query(CalendarDay).one()
    assert state.note == 'From the menu' and state.hide_completed and not errors


def test_browser_open_day_from_week_menu(prototype_server, page):
    origin, (db, client, ids) = prototype_server
    layout(client, 'week')
    page.goto(origin + '/app/calendar')
    today = page.locator('.week-day.is-today')
    today.locator('.day-menu summary').click()
    today.get_by_role('link', name='Open day').click()
    dialog = page.locator('#day-dialog')
    expect(dialog.get_by_role('heading', name=datetime.now().strftime('%A %-d %B %Y'))).to_be_visible()
    expect(dialog.get_by_role('link', name='An upcoming appointment', exact=True)).to_be_visible()
    dialog.get_by_role('checkbox', name='Mark An upcoming appointment').check()
    expect(dialog.locator('#page-feedback')).to_contain_text('Occurrence completed.')
    box = dialog.locator('.modal-box').bounding_box()
    page.mouse.click(box['x'] / 2, box['y'] + 10)
    expect(dialog).to_have_count(0)
    expect(page).to_have_url(origin + '/app/calendar?week=' + (datetime.now() - timedelta(days=datetime.now().weekday())).strftime('%Y-%m-%d'))


def test_browser_day_dialog_has_no_sideways_scroll_on_phones(prototype_server, page):
    origin, (db, client, ids) = prototype_server
    layout(client, 'month')
    today = datetime.now()
    for width in (320, 390):
        page.set_viewport_size({'width': width, 'height': 700})
        page.goto(origin + f'/app/calendar?month={today:%Y-%m}&day={today:%Y-%m-%d}')
        box = page.locator('#day-dialog .modal-box')
        expect(box).to_be_visible()
        assert box.evaluate('box => box.scrollWidth <= box.clientWidth')
        assert box.evaluate("box => getComputedStyle(box).overflowX") == 'hidden'


def noted_events(db):
    """Main, series-wide and multiple occurrence notes, including another date."""
    from models import Note
    today = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)
    own = CalendarEvent(title='Dentist', start_at=today, all_day=False, note='Bring the  referral\nletter ' + 'x' * 120)
    attached = CalendarEvent(title='Standup', start_at=today + timedelta(hours=1), all_day=False,
                             rrule='FREQ=DAILY', note='Regular agenda')
    db.add_all([own, attached])
    db.flush()
    db.add_all([Note(text='Ask about the release\r\n\r\nConfirm timing', name='Agenda', calendar_event_id=attached.id, occurrence_at=today),
                Note(text='Bring the report', calendar_event_id=attached.id, occurrence_at=today),
                Note(text='Shared meeting link', calendar_event_id=attached.id),
                Note(text='Not for today', calendar_event_id=attached.id, occurrence_at=today + timedelta(days=1))])
    db.commit()
    return own, attached


@pytest.mark.parametrize('value', ['list', 'month', 'week'])
def test_note_icon_previews_and_links(prototype, value):
    db, client, ids = prototype
    own, attached = noted_events(db)
    day = datetime.now().strftime('%Y-%m-%d')
    layout(client, value)
    page = client.get('/app/calendar')
    preview = 'Bring the  referral\nletter ' + 'x' * 72 + '…'
    assert len(preview) == 100 and f'title="{preview}"' in page.text
    assert 'title="Agenda: Ask about the release\n\nConfirm timing"' in page.text
    assert f'/app/calendar/event/{own.id}/edit?occurrence_at={day}' in page.text and '#event-note"' in page.text
    icons = re.findall(r'<a class="event-note-icon"[^>]*>', page.text)
    today_icons = [tag for tag in icons if f'/event/{attached.id}/edit?occurrence_at={day}' in tag]
    assert len(today_icons) == 4  # Main field, series-wide attachment, two occurrence notes.
    assert any('#event-note"' in tag and 'title="Regular agenda"' in tag for tag in today_icons)
    edit = client.get(f'/app/calendar/event/{attached.id}/edit?occurrence_at={day}')
    for note in attached.notes:
        matching = [tag for tag in icons if f'#event-note-{note.id}"' in tag]
        if note.occurrence_at:
            assert all(f'occurrence_at={note.occurrence_at:%Y-%m-%d}' in tag for tag in matching)
        if note.text == 'Not for today':
            assert all(tag not in today_icons for tag in matching)
        else:
            assert any(tag in today_icons for tag in matching)
        assert f'id="event-note-{note.id}" tabindex="-1"' in edit.text
    assert 'aria-label="Note for An upcoming appointment' not in page.text
    assert page.text.count('aria-label="Note for Dentist') == 1


def test_note_icons_skip_blank_and_other_occurrences(prototype):
    from models import Note
    db, client, _ = prototype
    event = db.query(CalendarEvent).one()
    event.note = ' \n '
    db.add_all([Note(text=' ', calendar_event_id=event.id),
                Note(text='Another occurrence', calendar_event_id=event.id,
                     occurrence_at=event.start_at + timedelta(days=1))])
    db.commit()
    layout(client, 'month')
    assert 'class="event-note-icon"' not in client.get('/app/calendar').text


def test_browser_note_icon_opens_the_note_field(prototype_server, page):
    origin, (db, client, ids) = prototype_server
    own, attached = noted_events(db)
    layout(client, 'month')
    page.set_viewport_size({'width': 1200, 'height': 800})
    page.goto(origin + '/app/calendar')
    today = page.locator('.month-cell.is-today')
    icon = today.get_by_role('link', name='Note for Dentist')
    assert icon.get_attribute('title') == 'Bring the  referral\nletter ' + 'x' * 72 + '…'
    assert today.get_by_role('link', name='Note for Standup: Agenda:').get_attribute('title') == 'Agenda: Ask about the release\n\nConfirm timing'
    icon.click()
    expect(page).to_have_url(re.compile(rf'/app/calendar/event/{own.id}/edit\?.*#event-note$'))
    expect(page.locator('#event-note')).to_be_focused()
    expect(page.locator('#event-note')).to_be_in_viewport()
    # Phones show dots: the icon is hidden, the dot gets a halo, and the day dialog has the icon.
    page.set_viewport_size({'width': 380, 'height': 800})
    page.goto(origin + '/app/calendar')
    today = page.locator('.month-cell.is-today')
    expect(today.locator('.event-note-icon').first).to_be_hidden()
    assert today.locator('.has-note > .calendar-title').first.evaluate("dot => getComputedStyle(dot).outlineStyle") == 'solid'
    expect(page.locator('.month-notes-legend')).to_be_visible()
    today.locator('.month-day').click()
    dialog = page.locator('#day-dialog')
    expect(dialog.get_by_role('link', name='Note for Dentist')).to_be_visible()
    expect(dialog.get_by_role('link', name='Note for Standup')).to_have_count(4)
    occurrence_note = next(note for note in attached.notes if note.text == 'Bring the report')
    dialog.get_by_role('link', name='Note for Standup: Bring the report', exact=True).click()
    expect(page).to_have_url(re.compile(rf'#event-note-{occurrence_note.id}$'))
    target = page.locator(f'#event-note-{occurrence_note.id}')
    expect(target).to_be_focused()
    expect(target).to_be_in_viewport()

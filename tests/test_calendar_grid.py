import calendar
from datetime import datetime
from urllib.parse import urlsplit

import pytest

from models import CalendarEvent, UserSettings
from test_billing_accounts import account


def serve_calendar(page, client):
    def serve(route):
        request = route.request
        url = urlsplit(request.url)
        response = client.request(
            request.method, url.path + ('?' + url.query if url.query else ''),
            content=request.post_data_buffer,
            headers={'content-type': request.headers.get('content-type', '')},
            follow_redirects=True,
        )
        route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

    page.route('http://calendar.test/**', serve)


@pytest.mark.parametrize('has_events', [True, False])
def test_month_grid_shares_space_without_hiding_days(account, page, has_events):
    db, _, client = account
    db.add(UserSettings(id=1, calendar_traditional_view=True))
    if has_events:
        db.add_all([
            CalendarEvent(title='An event with a very long title ' * 8, start_at=datetime(2026, 10, 1)),
            CalendarEvent(title='UnbrokenTitle' * 30, start_at=datetime(2026, 10, 6)),
        ])
    db.commit()

    serve_calendar(page, client)
    for width in (320, 390, 800, 1280):
        page.set_viewport_size({'width': width, 'height': 900})
        page.goto('http://calendar.test/classic/calendar?month=2026-10')
        assert page.locator('.cal-day-number').all_text_contents() == [str(day) for day in range(1, 32)]
        assert page.locator('.cal-day').count() == len(calendar.monthcalendar(2026, 10)) * 7
        layout = page.locator('.cal-grid').evaluate('''grid => {
            const bounds = grid.getBoundingClientRect();
            const cells = [...grid.querySelectorAll('.cal-day')].map(cell => cell.getBoundingClientRect());
            return {
                widths: cells.map(cell => cell.width),
                clipped: cells.some(cell => cell.left < bounds.left || cell.right > bounds.right),
                overflows: grid.scrollWidth > grid.clientWidth,
            };
        }''')
        assert not layout['clipped'], (width, layout)
        assert not layout['overflows'], (width, layout)
        if has_events and width > 600:
            # Tuesday and Thursday need more title space than the empty Monday column.
            assert layout['widths'][1] > layout['widths'][0], (width, layout)
            assert layout['widths'][3] > layout['widths'][0], (width, layout)
        else:
            assert max(layout['widths']) - min(layout['widths']) < 1, (width, layout)


def test_word_wrap_changes_height_and_preserves_column_widths(account, page):
    db, _, client = account
    db.add(UserSettings(id=1, calendar_traditional_view=True))
    db.add_all([
        CalendarEvent(title='A full event title that needs wrapping to remain readable ' * 10,
                      start_at=datetime(2026, 10, 1)),
        CalendarEvent(title='AnUnbrokenEventTitle' * 10, start_at=datetime(2026, 10, 6)),
        CalendarEvent(title='A shorter event', start_at=datetime(2026, 10, 7)),
    ])
    db.commit()
    serve_calendar(page, client)

    def layout():
        return page.locator('.cal-grid').evaluate('''grid => ({
            columns: [...grid.querySelectorAll('.cal-grid-header')].map(cell => cell.getBoundingClientRect().width),
            height: grid.getBoundingClientRect().height,
            titles: [...grid.querySelectorAll('.cal-event-title')].map(title => ({
                height: title.getBoundingClientRect().height,
                overflow: title.scrollWidth > title.clientWidth + 1,
            })),
            overflows: grid.scrollWidth > grid.clientWidth,
        })''')

    for width in (320, 390, 800, 1280):
        page.set_viewport_size({'width': width, 'height': min(width - 1, 900)})
        page.goto('http://calendar.test/classic/calendar?month=2026-10')
        toggle = page.get_by_role('button', name='Wrap event titles', exact=True)
        assert toggle.get_attribute('aria-pressed') == 'false'
        before = layout()
        toggle.click()
        page.wait_for_load_state()
        assert toggle.get_attribute('aria-pressed') == 'true'
        wrapped = layout()
        assert wrapped['columns'] == before['columns'], (width, before, wrapped)
        assert wrapped['height'] > before['height'], (width, before, wrapped)
        assert wrapped['titles'][0]['height'] > before['titles'][0]['height']
        assert all(not title['overflow'] for title in wrapped['titles'])
        assert not wrapped['overflows']
        page.goto('http://calendar.test/classic/calendar?month=2026-10')
        assert toggle.get_attribute('aria-pressed') == 'true'
        assert layout()['columns'] == before['columns']
        toggle.click()
        page.wait_for_load_state()
        assert toggle.get_attribute('aria-pressed') == 'false'
        assert layout() == before

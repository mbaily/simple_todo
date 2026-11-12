from datetime import datetime

import pytest

from models import CalendarEvent, UserSettings
from test_billing_accounts import account
from test_calendar_grid import serve_calendar
from test_page_reload import reload_browser


@pytest.mark.parametrize('word_wrap', [False, True], ids=['single-line', 'wrapped'])
@pytest.mark.parametrize('width,height', [(800, 600), (844, 390), (390, 844), (1280, 720)],
                         ids=['narrow-laptop', 'landscape', 'portrait', 'wide-laptop'])
def test_day_controls_and_grid_geometry_are_correct_from_the_first_frame(
        account, reload_browser, word_wrap, width, height):
    db, _, client = account
    db.add(UserSettings(id=1, calendar_traditional_view=True, calendar_word_wrap=word_wrap))
    db.add(CalendarEvent(title='A long appointment title that needs more column space ' * 4,
                         start_at=datetime(2026, 10, 1), rrule='FREQ=WEEKLY'))
    db.add_all(CalendarEvent(title='Task', start_at=datetime(2026, 10, 5), rrule='FREQ=WEEKLY')
               for _ in range(6))
    db.commit()

    with reload_browser.new_context(viewport={'width': width, 'height': height}) as context:
        page = context.new_page()
        serve_calendar(page, client)
        page.add_init_script('''
            window.calendarToolbarFrames = [];
            function sample() {
                const grid = document.querySelector('.cal-grid');
                if (grid) {
                    const gridBounds = grid.getBoundingClientRect();
                    const portrait = matchMedia('(max-width: 600px) and (orientation: portrait)').matches;
                    calendarToolbarFrames.push({
                        height: gridBounds.height,
                        cells: [...grid.querySelectorAll('.cal-day[data-date]')].map(cell => {
                            const bounds = cell.getBoundingClientRect();
                            const toolbar = cell.querySelector('.cal-day-toolbar').getBoundingClientRect();
                            return {
                                compact: portrait || toolbar.width < 85,
                                addVisible: cell.querySelector('.cal-day-add').getClientRects().length > 0,
                                openVisible: cell.querySelector('.cal-day-open').getClientRects().length > 0,
                                moreWidth: cell.querySelector('.cal-day-more').getBoundingClientRect().width,
                                geometry: [bounds.left - gridBounds.left, bounds.top - gridBounds.top,
                                           bounds.width, bounds.height, toolbar.height],
                            };
                        }),
                    });
                }
                if (calendarToolbarFrames.length < 6) requestAnimationFrame(sample);
            }
            requestAnimationFrame(sample);
        ''')

        for phase in ('initial load', 'reload'):
            if phase == 'initial load':
                page.goto('http://calendar.test/classic/calendar?month=2026-10')
            else:
                page.evaluate('scrollTo(0, document.documentElement.scrollHeight)')
                with page.expect_navigation():
                    page.evaluate('location.reload()')
            page.wait_for_function('calendarToolbarFrames.length === 6')
            frames = page.evaluate('calendarToolbarFrames')
            final = frames[-1]
            for frame in frames:
                assert frame['height'] == final['height'], (phase, frame, final)
                for cell, settled in zip(frame['cells'], final['cells']):
                    assert cell['addVisible'] == cell['openVisible'] == (not cell['compact']), (phase, cell)
                    assert cell['moreWidth'] == (12 if cell['compact'] else 18), (phase, cell)
                    assert cell['geometry'] == settled['geometry'], (phase, cell, settled)
            if width < 1000:
                assert any(cell['compact'] for cell in final['cells'])
            if width > 600:
                assert any(not cell['compact'] for cell in final['cells'])

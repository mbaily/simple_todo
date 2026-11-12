from datetime import datetime, timedelta

import pytest

from models import CalendarDay, CalendarEvent, CalendarEventCompletion, UserSettings
from test_billing_accounts import account
from test_calendar_grid import serve_calendar


@pytest.mark.parametrize('show_counts', [True, False])
def test_portrait_uses_counts_or_dots_and_landscape_restores_titles(account, page, show_counts):
    db, _, client = account
    day = datetime(2026, 10, 27)
    title = 'A long appointment title that should be readable in day details ' * 8
    db.add(UserSettings(id=1, calendar_traditional_view=True, calendar_word_wrap=True,
                        calendar_show_counts=show_counts))
    long_event = CalendarEvent(title=title, start_at=day)
    completed = CalendarEvent(title='Already complete', start_at=day)
    tomorrow = CalendarEvent(title='Finish this task', start_at=day + timedelta(days=1))
    db.add_all([long_event, completed, tomorrow, CalendarDay(day=day + timedelta(days=1), hide_completed=True)])
    db.flush()
    db.add(CalendarEventCompletion(event=completed, occurrence_at=day))
    db.commit()
    serve_calendar(page, client)
    url = 'http://calendar.test/classic/calendar?month=2026-10'

    for width in (320, 390, 430):
        page.set_viewport_size({'width': width, 'height': 844})
        page.goto(url)
        grid = page.locator('.cal-grid')
        cell = grid.locator('.cal-day[data-date="2026-10-27"]')
        assert grid.locator('.cal-day-number:visible').count() == 31
        assert grid.locator('.cal-event:visible').count() == 0
        assert grid.bounding_box()['height'] < 400
        assert grid.evaluate('e => e.scrollWidth === e.clientWidth')
        if show_counts:
            assert cell.locator('.cal-day-count').inner_text() == '2'
            assert grid.locator('.cal-day-count:visible').count() == 31
            assert grid.locator('.cal-day-marker:visible').count() == 0
        else:
            assert grid.locator('.cal-day-count:visible').count() == 0
            assert grid.locator('.cal-day-marker:visible').count() == 2
            assert grid.locator('.cal-day[data-date="2026-10-29"] .cal-day-marker').is_hidden()
        cell.get_by_role('button', name='Open day 2026-10-27', exact=True).click()
        dialog = page.get_by_role('dialog', name='2026-10-27')
        assert dialog.is_visible()
        assert dialog.locator('.cal-event-title').first.inner_text() == title.strip()
        assert dialog.locator('.cal-event-title').first.evaluate('e => e.clientWidth > 200 && e.scrollWidth <= e.clientWidth + 1')
        dialog.get_by_role('button', name='Close day', exact=True).click()

    # Rotate without reloading: show the same event rows with the saved wrapping setting.
    page.set_viewport_size({'width': 844, 'height': 390})
    assert grid.locator('.cal-event:visible').count() == 3
    assert grid.locator('.cal-day-count-short:visible').count() == 0
    assert grid.locator('.cal-day-marker:visible').count() == 0
    assert grid.locator('.cal-event-title').first.evaluate('e => getComputedStyle(e).whiteSpace') == 'normal'

    # Completing the last visible event updates both the count badge and dot fallback.
    page.set_viewport_size({'width': 390, 'height': 844})
    page.get_by_role('button', name='Open day 2026-10-28', exact=True).click()
    dialog = page.get_by_role('dialog', name='2026-10-28')
    dialog.locator('input[type="checkbox"]').check()
    page.wait_for_function("document.querySelector('#calendar-day-dialog-count').textContent === '1 event · 0 remaining'")
    dialog.get_by_role('button', name='Close day', exact=True).click()
    tomorrow_cell = grid.locator('.cal-day[data-date="2026-10-28"]')
    assert tomorrow_cell.locator('.cal-day-marker').is_hidden()
    assert tomorrow_cell.locator('.cal-day-count').get_attribute('title') == '1 event · 0 remaining'
    assert tomorrow_cell.locator('.cal-day-count-short').text_content() == '1'

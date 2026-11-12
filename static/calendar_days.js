function calendarDayCell(day) {
    return document.querySelector(`.cal-day[data-date="${day}"]`);
}

function closeCalendarDayMenu() {
    const menu = document.getElementById('calendar-day-menu');
    if (menu.matches(':popover-open')) menu.hidePopover();
}

function calendarDayCounts(cell) {
    const events = [...cell.querySelectorAll(':scope > .cal-event')];
    const remaining = events.filter(row => !row.classList.contains('cal-event-completed')).length;
    return { total: events.length, remaining, text: `${events.length} event${events.length === 1 ? '' : 's'} · ${remaining} remaining` };
}

function addCalendarDayEvent(day) {
    closeCalendarDayMenu();
    document.getElementById('calendar-day-dialog').close();
    const form = document.getElementById('create-event-form');
    form.style.display = 'block';
    form.elements.start_at.value = day;
    form.dispatchEvent(new Event('change', { bubbles: true }));
    form.scrollIntoView({ block: 'nearest' });
    form.elements.title.focus();
}

function showCalendarDayMenu(button) {
    const cell = button.closest('.cal-day');
    const day = cell.dataset.date;
    const menu = document.getElementById('calendar-day-menu');
    closeCalendarDayMenu();
    menu.dataset.day = day;
    document.getElementById('calendar-day-menu-date').textContent = day;
    document.getElementById('calendar-day-complete-form').action = SimpleTodo.url(`/calendar/day/${day}/complete`);
    document.querySelector('#calendar-day-complete-form button').disabled = calendarDayCounts(cell).remaining === 0;
    document.getElementById('calendar-day-visibility-form').action = SimpleTodo.url(`/calendar/day/${day}/toggle-completed`);
    document.querySelector('#calendar-day-visibility-form button').textContent = cell.dataset.hideCompleted === 'true' ? 'Show completed' : 'Hide completed';
    document.getElementById('calendar-day-note-button').textContent = cell.dataset.note ? 'Edit day note' : 'Add day note';
    menu.showPopover();
    const anchor = button.getBoundingClientRect();
    const size = menu.getBoundingClientRect();
    menu.style.left = `${Math.max(8, Math.min(anchor.left, window.innerWidth - size.width - 8))}px`;
    menu.style.top = `${Math.max(8, Math.min(anchor.bottom + 3, window.innerHeight - size.height - 8))}px`;
    menu.querySelector('button').focus();
}

function openCalendarDay(day, focusNote = false) {
    closeCalendarDayMenu();
    const cell = calendarDayCell(day);
    if (!cell) return;
    const dialog = document.getElementById('calendar-day-dialog');
    dialog.dataset.day = day;
    document.getElementById('calendar-day-title').textContent = day;
    document.getElementById('calendar-day-dialog-count').textContent = calendarDayCounts(cell).text;
    const content = document.getElementById('calendar-day-events');
    content.replaceChildren();
    for (const row of cell.querySelectorAll(':scope > .cal-event')) {
        content.append(row.cloneNode(true));
        if (row.dataset.note) {
            const note = document.createElement('p');
            note.className = 'cal-day-event-note';
            note.textContent = row.dataset.note;
            content.append(note);
        }
    }
    if (!content.children.length) {
        const empty = document.createElement('p');
        empty.textContent = 'No events for this day in the current filter.';
        content.append(empty);
    }
    document.getElementById('calendar-day-note-form').action = SimpleTodo.url(`/calendar/day/${day}/note`);
    document.getElementById('calendar-day-note').value = cell.dataset.note;
    if (!dialog.open) dialog.showModal();
    if (focusNote) document.getElementById('calendar-day-note').focus();
}

async function toggleCalendarDayOccurrence(checkbox) {
    const row = checkbox.closest('.cal-event');
    const eventId = row.dataset.eventId;
    const day = row.dataset.day;
    const occurrence = row.dataset.occurrence;
    checkbox.disabled = true;
    try {
        const response = await SimpleTodo.fetch(`/api/calendar/event/${eventId}/occurrence/toggle`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ occurrence_at: occurrence })
        });
        const data = await response.json();
        if (!response.ok || data.error) throw new Error(data.error || 'Could not update event');
        for (const event of document.querySelectorAll(`.cal-event[data-event-id="${eventId}"][data-occurrence="${occurrence}"]`)) {
            event.classList.toggle('cal-event-completed', data.completed);
            event.querySelector('input[type="checkbox"]').checked = data.completed;
        }
        const cell = calendarDayCell(day);
        const counts = calendarDayCounts(cell);
        const badge = cell.querySelector('.cal-day-count');
        badge.querySelector('.cal-day-count-full').textContent = counts.text + (cell.dataset.note ? ' · 📝' : '');
        badge.querySelector('.cal-day-count-short').textContent = counts.total;
        badge.title = counts.text;
        badge.setAttribute('aria-label', `Open day ${day}: ${counts.text}`);
        const marker = cell.querySelector('.cal-day-marker');
        marker.dataset.count = cell.dataset.hideCompleted === 'true' ? counts.remaining : counts.total;
        marker.title = counts.text;
        marker.setAttribute('aria-label', `Open day ${day}: ${counts.text}`);
        document.getElementById('calendar-day-dialog-count').textContent = counts.text;
    } catch (error) {
        checkbox.checked = !checkbox.checked;
        alert(error.message);
    } finally {
        checkbox.disabled = false;
    }
}

window.addEventListener('resize', closeCalendarDayMenu);
document.getElementById('calendar-day-dialog').addEventListener('close', () => {
    if (/^#day-\d{4}-\d{2}-\d{2}$/.test(window.location.hash)) {
        window.history.replaceState(window.history.state, '', window.location.pathname + window.location.search);
    }
});
if (/^#day-\d{4}-\d{2}-\d{2}$/.test(window.location.hash)) {
    openCalendarDay(window.location.hash.slice(5));
}

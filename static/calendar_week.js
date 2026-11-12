function addWeekEvent(button) {
    const form = document.getElementById('create-event-form');
    form.style.display = 'block';
    form.elements.start_at.value = button.dataset.date;
    form.elements.start_time.value = button.dataset.time || '';
    form.elements.duration_minutes.value = '';
    form.dispatchEvent(new Event('change', { bubbles: true }));
    form.scrollIntoView({ block: 'nearest' });
    form.elements.title.focus();
}

async function toggleWeekOccurrence(checkbox) {
    const row = checkbox.closest('.cal-week-event');
    const matches = [...document.querySelectorAll('.cal-week-event')].filter(event =>
        event.dataset.eventId === row.dataset.eventId && event.dataset.occurrence === row.dataset.occurrence);
    for (const event of matches) event.querySelector('input').disabled = true;
    try {
        const response = await SimpleTodo.fetch(`/api/calendar/event/${row.dataset.eventId}/occurrence/toggle`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ occurrence_at: row.dataset.occurrence })
        });
        const data = await response.json();
        if (!response.ok || data.error) throw new Error(data.error || 'Could not update event');
        for (const event of matches) {
            event.classList.toggle('cal-week-event-completed', data.completed);
            event.querySelector('input').checked = data.completed;
        }
    } catch (error) {
        checkbox.checked = !checkbox.checked;
        alert(error.message);
    } finally {
        for (const event of matches) event.querySelector('input').disabled = false;
    }
}

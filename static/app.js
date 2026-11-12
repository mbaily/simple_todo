function toggleItem(checkbox) {
    const type = checkbox.dataset.type;
    const id = checkbox.dataset.id;
    const url = `/api/${type}/${id}/toggle`;

    SimpleTodo.fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert('Error: ' + data.error);
                checkbox.checked = !checkbox.checked;
            }
        })
        .catch(error => {
            console.error('Error:', error);
            checkbox.checked = !checkbox.checked;
        });
}

function toggleShowCompleted(checkbox, listId) {
    const url = `/api/list/${listId}/toggle-show-completed`;

    SimpleTodo.fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert('Error: ' + data.error);
                checkbox.checked = !checkbox.checked;
            } else {
                // Reload page to show/hide completed todos
                window.location.reload();
            }
        })
        .catch(error => {
            console.error('Error:', error);
            checkbox.checked = !checkbox.checked;
        });
}

function toggleShowDelayed(checkbox, listId) {
    const url = `/api/list/${listId}/toggle-show-delayed`;

    SimpleTodo.fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert('Error: ' + data.error);
                checkbox.checked = !checkbox.checked;
            } else {
                // Reload page to show/hide delayed todos
                window.location.reload();
            }
        })
        .catch(error => {
            console.error('Error:', error);
            checkbox.checked = !checkbox.checked;
        });
}

function toggleArchived(checkbox, listId) {
    const url = `/api/list/${listId}/toggle-archived`;

    SimpleTodo.fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert('Error: ' + data.error);
                checkbox.checked = !checkbox.checked;
            }
        })
        .catch(error => {
            console.error('Error:', error);
            checkbox.checked = !checkbox.checked;
        });
}

function addHashtagFromSelect(selectElement, inputId) {
    const selectedValue = selectElement.value;
    if (!selectedValue) return;

    const input = document.getElementById(inputId);
    const currentValue = input.value.trim();
    const hashtag = '#' + selectedValue;

    // Check if hashtag already exists in input
    if (!currentValue.includes(hashtag)) {
        if (currentValue) {
            input.value = currentValue + ' ' + hashtag;
        } else {
            input.value = hashtag;
        }
    }

    // Reset select to placeholder
    selectElement.selectedIndex = 0;
}

function togglePinned(button, todoId) {
    const url = `/api/todo/${todoId}/toggle-pinned`;

    SimpleTodo.fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert('Error: ' + data.error);
            } else {
                // Reload page to reorder todos
                window.location.reload();
            }
        })
        .catch(error => {
            console.error('Error:', error);
        });
}

function toggleHashtagShowCompleted(checkbox, hashtagId) {
    const url = `/api/hashtag/${hashtagId}/toggle-show-completed`;

    SimpleTodo.fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert('Error: ' + data.error);
                checkbox.checked = !checkbox.checked;
            } else {
                // Reload page to show/hide completed items
                window.location.reload();
            }
        })
        .catch(error => {
            console.error('Error:', error);
            checkbox.checked = !checkbox.checked;
        });
}

function toggleCollationListFlag(checkbox, listId) {
    const url = `/api/list/${listId}/toggle-collation`;

    SimpleTodo.fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert('Error: ' + data.error);
                checkbox.checked = !checkbox.checked;
            } else {
                window.location.reload();
            }
        })
        .catch(error => {
            console.error('Error:', error);
            checkbox.checked = !checkbox.checked;
        });
}

function toggleTodoInCollation(collationListId, todoId) {
    const url = `/api/collation/${collationListId}/todo/${todoId}/toggle`;

    SimpleTodo.fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert('Error: ' + data.error);
            } else {
                // Simple and reliable: reload so both sections update.
                window.location.reload();
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('Error updating collation');
        });
}

// ---------------------------------------------------------------------------
// Reorder Logic
// ---------------------------------------------------------------------------

function reorderTodos() {
    const ids = getSelectedIds('todo');
    if (ids.length < 2) {
        alert('Please select at least two todos to reorder');
        return;
    }

    // Logic: Move all selected items (except the last one) BELOW the last selected item.
    // Last selected item = Target.
    // Others = Sources.

    const targetId = ids[ids.length - 1];
    const sourceIds = ids.slice(0, -1);

    const formData = new FormData();
    sourceIds.forEach(id => formData.append('source_ids', id));
    formData.append('target_id', targetId);

    SimpleTodo.fetch('/todo/reorder', {
        method: 'POST',
        body: formData
    }).then(response => {
        if (response.ok) {
            window.location.reload();
        } else {
            response.json().then(data => alert('Error: ' + (data.detail || 'Unknown error')));
        }
    });
}

function toggleCalendarOccurrence(eventId, occurrenceAtIso, checkbox) {
    SimpleTodo.fetch(`/api/calendar/event/${eventId}/occurrence/toggle`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ occurrence_at: occurrenceAtIso })
    })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert('Error: ' + data.error);
                if (checkbox) checkbox.checked = !checkbox.checked;
            } else {
                // keep UI in sync in case of server-side toggle
                if (checkbox) checkbox.checked = !!data.completed;
            }
        })
        .catch(error => {
            console.error('Error:', error);
            if (checkbox) checkbox.checked = !checkbox.checked;
        });
}

function showAddSubtodo(todoId) {
    document.getElementById('subtodo-form-' + todoId).style.display = 'block';
}

function hideAddSubtodo(todoId) {
    document.getElementById('subtodo-form-' + todoId).style.display = 'none';
}

function showSubtodoForm() {
    const ids = getSelectedIds('todo');
    if (ids.length === 0) {
        alert('No todo selected');
        return;
    }
    if (ids.length > 1) {
        alert('Please select only one todo to add a subtodo to');
        return;
    }
    const parentId = ids[0];
    document.getElementById('subtodo-parent-id').value = parentId;

    // Use the selected todo's actual list_id (important for collation lists)
    const checkbox = document.querySelector('.select-item[data-type="todo"][data-id="' + parentId + '"]:checked');
    if (checkbox && checkbox.dataset.listId) {
        const listIdInput = document.querySelector('#subtodo-form input[name="list_id"]');
        if (listIdInput) listIdInput.value = checkbox.dataset.listId;
    }

    document.getElementById('subtodo-form').style.display = 'block';
    document.getElementById('subtodo-form').scrollIntoView({ behavior: 'smooth' });
}

function hideSubtodoForm() {
    document.getElementById('subtodo-form').style.display = 'none';
}

function showSubnoteForm() {
    const ids = getSelectedIds('todo');
    if (ids.length === 0) {
        alert('No todo selected');
        return;
    }
    if (ids.length > 1) {
        alert('Please select only one todo to add a note to');
        return;
    }
    const todoId = ids[0];
    const form = document.getElementById('subnote-form');
    if (form) {
        document.getElementById('subnote-todo-id').value = todoId;

        // Use the selected todo's actual list_id (important for collation lists)
        const checkbox = document.querySelector('.select-item[data-type="todo"][data-id="' + todoId + '"]:checked');
        if (checkbox && checkbox.dataset.listId) {
            const listIdInput = form.querySelector('input[name="list_id"]');
            if (listIdInput) listIdInput.value = checkbox.dataset.listId;
        }

        form.style.display = 'block';
        form.scrollIntoView({ behavior: 'smooth' });
    }
}

function hideSubnoteForm() {
    document.getElementById('subnote-form').style.display = 'none';
}

// Selection management
function getSelectedIds(type) {
    const selector = '.select-item[data-type="' + type + '"]:checked';
    const checkboxes = document.querySelectorAll(selector);
    const ids = Array.from(new Set(Array.from(checkboxes).map(cb => cb.dataset.id)));
    return ids;
}

function getSelectedCount(type) {
    if (type) {
        const checkboxes = document.querySelectorAll('.select-item[data-type="' + type + '"]:checked');
        const ids = new Set(Array.from(checkboxes).map(cb => cb.dataset.id));
        return ids.size;
    }
    const checked = document.querySelectorAll('.select-item:checked');
    const keys = new Set(Array.from(checked).map(cb => (cb.dataset.type || '') + ':' + (cb.dataset.id || '')));
    return keys.size;
}

function updateSelectionCount() {
    const countEl = document.getElementById('selection-count');
    const reorderContainer = document.getElementById('reorder-container');

    if (countEl) {
        const count = getSelectedCount();
        const suffix = countEl.getAttribute('data-text-selected') || ' selected';
        countEl.textContent = count + suffix;

        // Enable reorder button if exactly 2 todos selected
        const reorderBtnIcon = document.getElementById('reorder-btn-icon');
        if (reorderBtnIcon) {
            const todoIds = getSelectedIds('todo');
            if (todoIds.length >= 2) {
                reorderBtnIcon.disabled = false;
                reorderBtnIcon.style.opacity = '1';
            } else {
                reorderBtnIcon.disabled = true;
                reorderBtnIcon.style.opacity = '0.3';
            }
        }
    }

    // Update dates display
    const datesEl = document.getElementById('selected-dates');
    if (datesEl) {
        const checked = document.querySelectorAll('.select-item:checked');
        const byKey = new Map();
        Array.from(checked).forEach(cb => {
            const key = (cb.dataset.type || '') + ':' + (cb.dataset.id || '');
            if (!byKey.has(key)) byKey.set(key, cb);
        });
        if (byKey.size === 1) {
            const item = byKey.values().next().value;
            const created = item.dataset.created || '';
            const modified = item.dataset.modified || '';
            datesEl.textContent = 'Created: ' + created + ' | Modified: ' + modified;
            datesEl.style.display = 'block';
        } else {
            datesEl.style.display = 'none';
        }
    }
}

// Bulk operations
function doBulkDelete(type, ids) {
    return SimpleTodo.fetch('/api/bulk/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: type, ids: ids })
    })
        .then(response => response.json())
        .then(data => {
            if (data.error) throw data.error;
            return data;
        });
}

function bulkDelete(type) {
    // Legacy/Explicit mode
    if (type) {
        const ids = getSelectedIds(type);
        console.log('bulkDelete called with type:', type, 'ids:', ids);
        if (ids.length === 0) {
            alert('No items selected');
            return;
        }
        if (!confirm('Delete ' + ids.length + ' item(s)?')) return;

        doBulkDelete(type, ids)
            .then(() => window.location.reload())
            .catch(error => {
                console.error('Error:', error);
                alert('Error deleting items: ' + error);
            });
        return;
    }

    // Auto-detect mode (Todos + Notes + Datetime Headers + Lists)
    const todoIds = getSelectedIds('todo');
    const noteIds = getSelectedIds('note');
    const headerIds = getSelectedIds('datetime_header');
    const listIds = getSelectedIds('list');

    // Explicitly check for datetime headers if no other ID types found, 
    // or include them in the total count if mixed (though mixed selection is rare/weird for headers).

    const total = todoIds.length + noteIds.length + headerIds.length + listIds.length;

    if (total === 0) {
        alert('No items selected');
        return;
    }

    if (!confirm('Delete ' + total + ' item(s)?')) return;

    const promises = [];
    if (todoIds.length > 0) promises.push(doBulkDelete('todo', todoIds));
    if (noteIds.length > 0) promises.push(doBulkDelete('note', noteIds));
    if (headerIds.length > 0) promises.push(doBulkDelete('datetime_header', headerIds));
    if (listIds.length > 0) promises.push(doBulkDelete('list', listIds));

    Promise.all(promises)
        .then(() => window.location.reload())
        .catch(error => {
            console.error('Error:', error);
            alert('Error deleting items: ' + error);
        });
}

// Helper to get selected type and IDs if not provided
function getTargetSelection(type, allowedTypes = ['todo', 'list', 'note']) {
    if (type) {
        return { type: type, ids: getSelectedIds(type) };
    }
    for (const t of allowedTypes) {
        const ids = getSelectedIds(t);
        if (ids.length > 0) return { type: t, ids: ids };
    }
    return { type: null, ids: [] };
}

function bulkPin(type) {
    const sel = getTargetSelection(type, ['todo', 'list']);
    const finalType = sel.type;
    const ids = sel.ids;

    if (ids.length === 0) {
        alert('No items selected');
        return;
    }

    SimpleTodo.fetch('/api/bulk/pin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: finalType, ids: ids, pin: true })
    })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert('Error: ' + data.error);
            } else {
                window.location.reload();
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('Error pinning items');
        });
}

function bulkUnpin(type) {
    const sel = getTargetSelection(type, ['todo', 'list']);
    const finalType = sel.type;
    const ids = sel.ids;

    if (ids.length === 0) {
        alert('No items selected');
        return;
    }

    SimpleTodo.fetch('/api/bulk/pin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: finalType, ids: ids, pin: false })
    })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert('Error: ' + data.error);
            } else {
                window.location.reload();
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('Error unpinning items');
        });
}

function bulkArchive(type) {
    const sel = getTargetSelection(type, ['list']);
    const finalType = sel.type;
    const ids = sel.ids;

    if (ids.length === 0) {
        if (!type) { // Auto-detect failed because possibly todo selected?
            const todoIds = getSelectedIds('todo');
            if (todoIds.length > 0) {
                alert('Only lists can be archived');
                return;
            }
        }
        alert('No lists selected');
        return;
    }

    if (finalType !== 'list') {
        alert('Only lists can be archived');
        return;
    }

    SimpleTodo.fetch('/api/bulk/archive', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: finalType, ids: ids, archived: true })
    })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert('Error: ' + data.error);
            } else {
                window.location.reload();
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('Error archiving lists');
        });
}

function bulkUnarchive(type) {
    const sel = getTargetSelection(type, ['list']);
    const finalType = sel.type;
    const ids = sel.ids;

    if (ids.length === 0) {
        if (!type) {
            const todoIds = getSelectedIds('todo');
            if (todoIds.length > 0) {
                alert('Only lists can be unarchived');
                return;
            }
        }
        alert('No lists selected');
        return;
    }

    if (finalType !== 'list') {
        alert('Only lists can be unarchived');
        return;
    }

    SimpleTodo.fetch('/api/bulk/archive', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: finalType, ids: ids, archived: false })
    })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert('Error: ' + data.error);
            } else {
                window.location.reload();
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('Error unarchiving lists');
        });
}

function bulkEdit(type) {
    if (type === 'datetime_header') {
        alert('Datetime headers cannot be edited');
        return;
    }

    // Auto-detect type if not specific
    let ids = [];
    let targetType = type;

    if (!type) {
        const sel = getTargetSelection(null, ['todo', 'list', 'note']);
        targetType = sel.type;
        ids = sel.ids;
    } else {
        ids = getSelectedIds(type);
    }

    if (ids.length === 0) {
        alert('No items selected');
        return;
    }
    if (ids.length > 1) {
        alert('Please select only one item to edit');
        return;
    }

    if (!targetType) {
        alert('Unknown item type');
        return;
    }

    window.location.href = SimpleTodo.url('/' + targetType + '/' + ids[0] + '/edit');
}

function bulkEditCalendarEvent() {
    const ids = getSelectedIds('calendar_event');
    if (ids.length === 0) {
        alert('No events selected');
        return;
    }
    // Deduplicate (calendar selection appears once per event, but be safe)
    const unique = Array.from(new Set(ids));
    if (unique.length > 1) {
        alert('Please select only one event to edit');
        return;
    }
    const selectedCheckbox = document.querySelector('input.select-item[data-type="calendar_event"]:checked');
    let url = `/calendar/event/${unique[0]}/edit`;
    if (selectedCheckbox && selectedCheckbox.dataset.occurrence) {
        url += `?occurrence_at=${encodeURIComponent(selectedCheckbox.dataset.occurrence)}`;
    }
    window.location.href = SimpleTodo.url(url);
}

// ---------------------------------------------------------------------------
// More Actions: Defer and Set Alarm (single-todo operations)
// ---------------------------------------------------------------------------

function onMoreActionsChange(select) {
    const action = select.value;
    // Hide all inline forms first
    const deferForm = document.getElementById('inline-defer-form');
    const alarmForm = document.getElementById('inline-alarm-form');
    const hashtagsForm = document.getElementById('inline-hashtags-form');
    if (deferForm) deferForm.style.display = 'none';
    if (alarmForm) alarmForm.style.display = 'none';
    if (hashtagsForm) hashtagsForm.style.display = 'none';

    if (action === 'defer') {
        if (deferForm) {
            deferForm.style.display = 'block';
            deferForm.scrollIntoView({ behavior: 'smooth' });
        }
    } else if (action === 'set-alarm') {
        if (alarmForm) {
            alarmForm.style.display = 'block';
            alarmForm.scrollIntoView({ behavior: 'smooth' });
        }
    } else if (action === 'edit-hashtags') {
        if (hashtagsForm) {
            loadHashtagsForSelectedTodo();
            hashtagsForm.style.display = 'block';
            hashtagsForm.scrollIntoView({ behavior: 'smooth' });
        }
    } else if (action === 'toggle-recipe') {
        // Toggle recipe mode for the list
        toggleRecipeModeViaForm();
    } else if (action === 'reset-ingredients') {
        resetAllIngredientsAction();
    } else if (action === 'bulk-mark-bought') {
        bulkIngredientEvent('bought');
    } else if (action === 'bulk-mark-ran-out') {
        bulkIngredientEvent('ran_out');
    } else if (action === 'ingredient-report') {
        // Navigate to ingredient report
        if (typeof LIST_ID !== 'undefined') {
            window.location.href = SimpleTodo.url(`/list/${LIST_ID}/ingredient_report`);
        }
    } else if (action === 'shopping-list') {
        // Navigate to shopping list
        if (typeof LIST_ID !== 'undefined') {
            window.location.href = SimpleTodo.url(`/list/${LIST_ID}/shopping_list`);
        }
    } else if (action === 'enable-dokuwiki-page') {
        bulkToggleDokuWikiPage(true);
    } else if (action === 'disable-dokuwiki-page') {
        bulkToggleDokuWikiPage(false);
    }
    // Reset select
    select.selectedIndex = 0;
}

// ---------------------------------------------------------------------------
// DokuWiki Item Page Link Functions
// ---------------------------------------------------------------------------

function toggleShowDokuWikiItemLinks(checkbox, listId) {
    const url = `/api/list/${listId}/toggle-show-dokuwiki-item-links`;

    SimpleTodo.fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
    })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert('Error: ' + data.error);
                checkbox.checked = !checkbox.checked;
            } else {
                window.location.reload();
            }
        })
        .catch(error => {
            console.error('Error:', error);
            checkbox.checked = !checkbox.checked;
        });
}

function toggleListDokuWikiPage(listId) {
    const url = `/api/list/${listId}/toggle-dokuwiki-page`;

    SimpleTodo.fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
    })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert('Error: ' + data.error);
            } else {
                window.location.reload();
            }
        })
        .catch(error => {
            console.error('Error:', error);
        });
}

function bulkToggleDokuWikiPage(enable) {
    const ids = getSelectedIds('todo');
    if (ids.length === 0) {
        alert('No todos selected');
        return;
    }

    SimpleTodo.fetch('/api/bulk/toggle-dokuwiki-page', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: ids, enable: enable })
    })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert('Error: ' + data.error);
            } else {
                window.location.reload();
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('Error updating DokuWiki page links');
        });
}

// ---------------------------------------------------------------------------
// Recipe Mode Functions
// ---------------------------------------------------------------------------

function toggleRecipeMode(checkbox, listId) {
    SimpleTodo.fetch(`/list/${listId}/toggle_recipe_mode`, {
        method: 'POST'
    })
        .then(response => {
            if (response.ok) {
                window.location.reload();
            } else {
                alert('Failed to toggle recipe mode. Make sure manual sorting is enabled.');
                checkbox.checked = !checkbox.checked;
            }
        })
        .catch(error => {
            console.error('Error:', error);
            checkbox.checked = !checkbox.checked;
        });
}

function toggleRecipeModeViaForm() {
    if (typeof LIST_ID === 'undefined') {
        alert('Cannot determine list ID');
        return;
    }

    SimpleTodo.fetch(`/list/${LIST_ID}/toggle_recipe_mode`, {
        method: 'POST'
    })
        .then(response => {
            if (response.ok) {
                window.location.reload();
            } else {
                alert('Failed to toggle recipe mode. Make sure manual sorting is enabled.');
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('Error toggling recipe mode');
        });
}

function resetAllIngredientsAction() {
    if (typeof LIST_ID === 'undefined') {
        alert('Cannot determine list ID');
        return;
    }

    if (!confirm('This will mark all ingredients as freshly stocked. Continue?')) {
        return;
    }

    SimpleTodo.fetch(`/api/list/${LIST_ID}/reset_ingredients`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ quantity: 1 })
    })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert('Error: ' + data.error);
            } else {
                alert(`Reset ${data.reset_count} ingredients!`);
                window.location.reload();
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('Error resetting ingredients');
        });
}

function bulkIngredientEvent(eventType) {
    if (typeof LIST_ID === 'undefined') {
        alert('Cannot determine list ID');
        return;
    }

    const ids = getSelectedIds('todo');
    if (ids.length === 0) {
        alert('No items selected');
        return;
    }

    const quantity = prompt('Enter quantity (default: 1):', '1');
    if (quantity === null) return; // Cancelled

    const qty = parseInt(quantity, 10) || 1;

    SimpleTodo.fetch(`/api/list/${LIST_ID}/bulk_ingredient_event`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            todo_ids: ids.map(id => parseInt(id, 10)),
            event_type: eventType,
            quantity: qty
        })
    })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert('Error: ' + data.error);
            } else {
                alert(`Marked ${data.updated_count} items as '${eventType}'`);
                window.location.reload();
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('Error updating ingredients');
        });
}


function applyDefer() {
    const ids = getSelectedIds('todo');
    if (ids.length === 0) {
        alert('No todo selected');
        return;
    }
    if (ids.length > 1) {
        alert('Defer can only be applied to one todo at a time');
        return;
    }

    const todoId = ids[0];
    const dateInput = document.getElementById('defer-date-input');
    const delayedUntil = dateInput ? dateInput.value : '';

    if (!delayedUntil) {
        alert('Please select a date');
        return;
    }

    SimpleTodo.fetch(`/api/todo/${todoId}/defer`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ delayed_until: delayedUntil })
    })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert('Error: ' + data.error);
            } else {
                window.location.reload();
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('Error setting deferral');
        });
}

function cancelDefer() {
    const form = document.getElementById('inline-defer-form');
    if (form) form.style.display = 'none';
}

function toggleAlarmModeViewList() {
    const mode = document.querySelector('input[name="alarm_mode_viewlist"]:checked');
    const rangeDiv = document.getElementById('alarm-range-inputs-viewlist');
    const tolDiv = document.getElementById('alarm-tolerance-inputs-viewlist');

    if (rangeDiv) rangeDiv.style.display = 'none';
    if (tolDiv) tolDiv.style.display = 'none';

    if (mode && mode.value === 'range' && rangeDiv) {
        rangeDiv.style.display = 'block';
    } else if (mode && mode.value === 'tolerance' && tolDiv) {
        tolDiv.style.display = 'block';
    }
}

function applyAlarm() {
    const ids = getSelectedIds('todo');
    if (ids.length === 0) {
        alert('No todo selected');
        return;
    }
    if (ids.length > 1) {
        alert('Set Alarm can only be applied to one todo at a time');
        return;
    }

    const todoId = ids[0];
    const modeEl = document.querySelector('input[name="alarm_mode_viewlist"]:checked');
    const mode = modeEl ? modeEl.value : 'none';

    const payload = { alarm_mode: mode };

    if (mode === 'range') {
        const startEl = document.querySelector('input[name="alarm_start_range_viewlist"]');
        const endEl = document.querySelector('input[name="alarm_end_range_viewlist"]');
        payload.alarm_start_range = startEl ? startEl.value : '';
        payload.alarm_end_range = endEl ? endEl.value : '';
        if (!payload.alarm_start_range || !payload.alarm_end_range) {
            alert('Please set both start and end times');
            return;
        }
    } else if (mode === 'tolerance') {
        const targetEl = document.querySelector('input[name="alarm_target_time_viewlist"]');
        const minutesEl = document.querySelector('input[name="alarm_minutes_viewlist"]');
        payload.alarm_target_time = targetEl ? targetEl.value : '';
        payload.alarm_minutes = minutesEl ? parseInt(minutesEl.value, 10) : 0;
        if (!payload.alarm_target_time) {
            alert('Please set a target time');
            return;
        }
    }

    SimpleTodo.fetch(`/api/todo/${todoId}/set-alarm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert('Error: ' + data.error);
            } else {
                window.location.reload();
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('Error setting alarm');
        });
}

function cancelAlarm() {
    const form = document.getElementById('inline-alarm-form');
    if (form) form.style.display = 'none';
}

function loadHashtagsForSelectedTodo() {
    const ids = getSelectedIds('todo');
    if (ids.length !== 1) return;

    const todoId = ids[0];
    // Find the todo row and extract current hashtags from the pills
    const todoRow = document.getElementById('todo-row-' + todoId);
    if (!todoRow) return;

    const pills = todoRow.querySelectorAll('a.pill');
    let hashtagText = '';
    pills.forEach(pill => {
        hashtagText += pill.textContent.trim() + ' ';
    });

    const input = document.getElementById('hashtags-edit-viewlist');
    if (input) {
        input.value = hashtagText.trim();
    }
}

function applyHashtags() {
    const ids = getSelectedIds('todo');
    if (ids.length === 0) {
        alert('No todo selected');
        return;
    }
    if (ids.length > 1) {
        alert('Edit Hashtags can only be applied to one todo at a time');
        return;
    }

    const todoId = ids[0];
    const input = document.getElementById('hashtags-edit-viewlist');
    const hashtags = input ? input.value : '';

    SimpleTodo.fetch(`/api/todo/${todoId}/update-hashtags`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hashtags: hashtags })
    })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert('Error: ' + data.error);
            } else {
                window.location.reload();
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('Error updating hashtags');
        });
}

function cancelHashtags() {
    const form = document.getElementById('inline-hashtags-form');
    if (form) form.style.display = 'none';
}

// ---------------------------------------------------------------------------
// RRULE UI helpers (simple builder for common recurrence patterns)
// ---------------------------------------------------------------------------

function parseRRuleString(rrule) {
    const out = { freq: '', interval: 1, byday: [], until: '' };
    if (!rrule) return out;
    const s = rrule.trim().replace(/^RRULE:/i, '');
    const parts = s.split(';').map(p => p.trim()).filter(Boolean);
    for (const p of parts) {
        const [k, v] = p.split('=');
        if (!k || v === undefined) continue;
        const key = k.toUpperCase();
        const val = v.toUpperCase();
        if (key === 'FREQ') out.freq = val;
        if (key === 'INTERVAL') {
            const n = parseInt(val, 10);
            if (!isNaN(n) && n >= 1) out.interval = n;
        }
        if (key === 'BYDAY') {
            out.byday = val.split(',').map(x => x.trim()).filter(Boolean);
        }
        if (key === 'UNTIL') {
            // UNTIL can be YYYYMMDD or YYYYMMDDTHHMMSSZ - extract date part
            const m = val.match(/^(\d{4})(\d{2})(\d{2})/);
            if (m) {
                out.until = m[1] + '-' + m[2] + '-' + m[3];
            }
        }
    }
    return out;
}

function weekdayToByDay(dateStr) {
    // dateStr: YYYY-MM-DD
    const d = new Date(dateStr + 'T00:00:00');
    const day = d.getDay();
    // JS: 0=Sun..6=Sat
    return ['SU', 'MO', 'TU', 'WE', 'TH', 'FR', 'SA'][day];
}

function buildRRuleFromForm(form) {
    if (!form) return '';
    const freqEl = form.querySelector('select[data-rrule-field="freq"]');
    const intervalEl = form.querySelector('input[data-rrule-field="interval"]');
    const weeklyBox = form.querySelector('[data-rrule-weekly]');
    const startEl = form.querySelector('input[name="start_at"]');
    const untilEl = form.querySelector('input[data-rrule-field="until"]');

    const freq = (freqEl ? freqEl.value : '').toUpperCase();
    if (!freq) return '';

    let interval = 1;
    if (intervalEl) {
        const n = parseInt(intervalEl.value, 10);
        if (!isNaN(n) && n >= 1) interval = n;
    }

    const parts = [`FREQ=${freq}`];
    if (interval !== 1) parts.push(`INTERVAL=${interval}`);

    if (freq === 'WEEKLY') {
        let days = Array.from(form.querySelectorAll('input[data-rrule-byday]:checked')).map(cb => cb.value);
        if (days.length === 0 && startEl && startEl.value) {
            days = [weekdayToByDay(startEl.value)];
        }
        if (days.length) parts.push(`BYDAY=${days.join(',')}`);
    }

    // UNTIL end date
    if (untilEl && untilEl.value) {
        const untilDate = untilEl.value.replace(/-/g, '');
        parts.push(`UNTIL=${untilDate}T235959`);
    }

    return parts.join(';');
}

function updateRRulePreview(form) {
    if (!form) return;
    const freqEl = form.querySelector('select[data-rrule-field="freq"]');
    const weeklyBox = form.querySelector('[data-rrule-weekly]');
    const outputEl = form.querySelector('input[data-rrule-output]');
    const previewEl = form.querySelector('[data-rrule-preview]');
    const untilEl = form.querySelector('input[data-rrule-field="until"]');

    const freq = (freqEl ? freqEl.value : '').toUpperCase();
    if (weeklyBox) {
        weeklyBox.style.display = (freq === 'WEEKLY') ? 'block' : 'none';
    }

    // Show/hide UNTIL field container based on whether frequency is set
    const untilContainer = form.querySelector('[data-rrule-until-container]');
    if (untilContainer) {
        untilContainer.style.display = freq ? 'block' : 'none';
    }

    const rrule = buildRRuleFromForm(form);
    if (outputEl) outputEl.value = rrule;
    if (previewEl) {
        let preview = rrule ? `RRULE: ${rrule}` : 'Not recurring';
        if (untilEl && untilEl.value && freq) {
            preview += ` (ends ${untilEl.value})`;
        }
        previewEl.textContent = preview;
    }
}

function initRRuleUI(form) {
    if (!form) return;
    const outputEl = form.querySelector('input[data-rrule-output]');
    const freqEl = form.querySelector('select[data-rrule-field="freq"]');
    const intervalEl = form.querySelector('input[data-rrule-field="interval"]');
    if (!outputEl || !freqEl || !intervalEl) {
        return;
    }

    const parsed = parseRRuleString(outputEl.value);
    if (parsed.freq) freqEl.value = parsed.freq;
    intervalEl.value = parsed.interval || 1;

    // Set weekly BYDAY checkboxes
    const bydayEls = form.querySelectorAll('input[data-rrule-byday]');
    bydayEls.forEach(cb => {
        cb.checked = parsed.byday.includes(cb.value);
    });

    // Set UNTIL date if present
    const untilEl = form.querySelector('input[data-rrule-field="until"]');
    if (untilEl && parsed.until) {
        untilEl.value = parsed.until;
    }

    updateRRulePreview(form);
}

function prepareRRuleForSubmit(form) {
    // Ensure hidden RRULE field is up to date.
    updateRRulePreview(form);
    return true;
}

function selectAll(type) {
    const checkboxes = document.querySelectorAll('.select-item' + (type ? '[data-type="' + type + '"]' : ''));
    checkboxes.forEach(cb => cb.checked = true);
    updateSelectionCount();
}

function selectNone(type) {
    const checkboxes = document.querySelectorAll('.select-item' + (type ? '[data-type="' + type + '"]' : ''));
    checkboxes.forEach(cb => cb.checked = false);
    updateSelectionCount();
}

var dokuWikiDeleteMode = false;

function toggleDokuWikiDeleteMode() {
    dokuWikiDeleteMode = !dokuWikiDeleteMode;
    const btn = document.getElementById('dokuwiki-delete-toggle');
    if (btn) {
        btn.style.opacity = dokuWikiDeleteMode ? '1' : '0.5';
        btn.style.border = dokuWikiDeleteMode ? '2px solid red' : 'none';
    }

    // Update all DokuWiki links
    const links = document.querySelectorAll('.dokuwiki-link');
    links.forEach(link => {
        if (dokuWikiDeleteMode) {
            link.classList.add('delete-mode');
            // Disable href temporarily
            link.dataset.href = link.href;
            link.removeAttribute('href');
            link.style.cursor = 'pointer';
        } else {
            link.classList.remove('delete-mode');
            // Restore href
            if (link.dataset.href) {
                link.href = link.dataset.href;
            }
        }
    });
}

function trackDokuWikiClick(type, id, listId, todoIds) {
    if (dokuWikiDeleteMode) {
        untrackDokuWikiClick(type, id, listId, todoIds);
        return;
    }

    const payload = { type: type, id: id };
    if (listId) {
        payload.list_id = listId;
    }
    if (todoIds) {
        payload.todo_ids = todoIds;
    }

    SimpleTodo.fetch('/api/dokuwiki/click', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // If confirmed, or if it was a synthetic header (which now became real),
                // we should reload to reflect the state change (green icon or new checkbox).
                // Note: With burst=False (default), burst will be 1 but confirmed will be true.
                if (data.confirmed || type === 'synthetic_datetime_header') {
                    window.location.reload();
                }
            }
        })
        .catch(console.error);
}

function untrackDokuWikiClick(type, id, listId, todoIds) {
    const payload = { type: type, id: id };
    if (listId) {
        payload.list_id = listId;
    }
    if (todoIds) {
        payload.todo_ids = todoIds;
    }

    SimpleTodo.fetch('/api/dokuwiki/unconfirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                window.location.reload();
            }
        })
        .catch(console.error);
}

// =============================================================================
// TAGGING SYSTEM - Cross-page item selection for bulk operations
// =============================================================================

const TAGGED_STORAGE_KEY = SimpleTodo.storageKey('simple_todo_tagged_items');

function getTaggedItems() {
    try {
        const stored = localStorage.getItem(TAGGED_STORAGE_KEY);
        if (stored) {
            return JSON.parse(stored);
        }
    } catch (e) {
        console.error('Error reading tagged items:', e);
    }
    return { todos: [], lists: [], notes: [] };
}

function setTaggedItems(items) {
    try {
        localStorage.setItem(TAGGED_STORAGE_KEY, JSON.stringify(items));
    } catch (e) {
        console.error('Error saving tagged items:', e);
    }
}

function clearTaggedItems() {
    try {
        localStorage.removeItem(TAGGED_STORAGE_KEY);
    } catch (e) {
        console.error('Error clearing tagged items:', e);
    }
    updateTaggedDisplay();
}

function tagItem(type, id) {
    const items = getTaggedItems();
    const key = type + 's'; // todos, lists, notes
    if (!items[key]) items[key] = [];
    if (!items[key].includes(id)) {
        items[key].push(id);
        setTaggedItems(items);
    }
}

function untagItem(type, id) {
    const items = getTaggedItems();
    const key = type + 's';
    if (items[key]) {
        items[key] = items[key].filter(i => i !== id);
        setTaggedItems(items);
    }
}

function isItemTagged(type, id) {
    const items = getTaggedItems();
    const key = type + 's';
    return items[key] && items[key].includes(id);
}

function getTaggedCount() {
    const items = getTaggedItems();
    return (items.todos?.length || 0) + (items.lists?.length || 0) + (items.notes?.length || 0);
}

function tagSelectedItems() {
    // Get all selected checkboxes
    const checkboxes = document.querySelectorAll('.select-item:checked');
    let taggedCount = 0;

    checkboxes.forEach(cb => {
        const type = cb.dataset.type;
        const id = cb.dataset.id;
        if (type && id && ['todo', 'list', 'note'].includes(type)) {
            tagItem(type, id);
            taggedCount++;
        }
    });

    if (taggedCount > 0) {
        updateTaggedDisplay();
        // Visual feedback
        const btn = document.getElementById('tag-selected-btn');
        if (btn) {
            const originalText = btn.textContent;
            btn.textContent = `Tagged ${taggedCount}!`;
            setTimeout(() => {
                btn.textContent = originalText;
            }, 1500);
        }
    } else {
        alert('No items selected to tag. Please select items using checkboxes first.');
    }
}

function updateTaggedDisplay() {
    // Update any tagged count displays on the page
    const countEl = document.getElementById('tagged-count');
    if (countEl) {
        const items = getTaggedItems();
        const parts = [];
        if (items.todos?.length) parts.push(`${items.todos.length} todo${items.todos.length > 1 ? 's' : ''}`);
        if (items.lists?.length) parts.push(`${items.lists.length} list${items.lists.length > 1 ? 's' : ''}`);
        if (items.notes?.length) parts.push(`${items.notes.length} note${items.notes.length > 1 ? 's' : ''}`);

        if (parts.length > 0) {
            countEl.textContent = parts.join(', ');
            countEl.style.display = 'inline';
        } else {
            countEl.textContent = 'None';
            countEl.style.display = 'inline';
        }
    }

    // Update tagged items panel if exists
    const panel = document.getElementById('tagged-items-panel');
    if (panel) {
        const count = getTaggedCount();
        if (count > 0) {
            panel.style.display = 'block';
        } else {
            panel.style.display = 'none';
        }
    }
}

function moveTaggedLists(targetFolderId) {
    const items = getTaggedItems();
    if (!items.lists || items.lists.length === 0) {
        alert('No lists are tagged for moving.');
        return;
    }

    SimpleTodo.fetch('/api/tagged/move-lists', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            list_ids: items.lists,
            target_folder_id: targetFolderId
        })
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // Clear tagged lists after successful move
                items.lists = [];
                setTaggedItems(items);
                alert(`Moved ${data.moved_count} list(s) successfully.`);
                window.location.reload();
            } else {
                alert('Error: ' + (data.error || 'Unknown error'));
            }
        })
        .catch(error => {
            console.error('Error moving lists:', error);
            alert('Error moving lists: ' + error);
        });
}

function moveTaggedTodosNotes(targetListId) {
    const items = getTaggedItems();
    const todoCount = items.todos?.length || 0;
    const noteCount = items.notes?.length || 0;

    if (todoCount === 0 && noteCount === 0) {
        alert('No todos or notes are tagged for moving.');
        return;
    }

    SimpleTodo.fetch('/api/tagged/move-items', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            todo_ids: items.todos || [],
            note_ids: items.notes || [],
            target_list_id: targetListId
        })
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // Clear tagged todos/notes after successful move
                items.todos = [];
                items.notes = [];
                setTaggedItems(items);
                alert(`Moved ${data.moved_todos} todo(s) and ${data.moved_notes} note(s) successfully.`);
                window.location.reload();
            } else {
                alert('Error: ' + (data.error || 'Unknown error'));
            }
        })
        .catch(error => {
            console.error('Error moving items:', error);
            alert('Error moving items: ' + error);
        });
}

// Initialize tagged display on page load
document.addEventListener('DOMContentLoaded', function () {
    updateTaggedDisplay();
});

/* Local UI state only. Persistent mutations and rendering go through HTMX. */
(() => {
    'use strict';
    const state = { selecting: false, selected: new Set(), details: new Map(), scope: null, draft: {}, targets: new Map(), focus: null, focusId: null, filter: '', reorderFocus: null, taskOptions: null };
    let toastTimer;
    const workspace = () => document.getElementById('daisy-workspace');
    const checkboxes = () => [...document.querySelectorAll('.selection-checkbox')];

    function toast(message) {
        const container = document.getElementById('toast');
        container.replaceChildren();
        const alert = document.createElement('div');
        alert.className = 'alert';
        alert.textContent = message;
        container.append(alert);
        container.hidden = false;
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => { container.hidden = true; }, 4500);
    }

    function navigate(url) {
        // Go through the same boosted (morphing) navigation as an ordinary link.
        const link = document.createElement('a');
        link.href = url;
        link.hidden = true;
        document.body.append(link);
        if (window.htmx) htmx.process(link);
        link.click();
    }

    function scrollCalendarToday() {
        const root = workspace();
        if (root?.dataset.calendarContainsToday !== 'true') return;
        const today = root.dataset.calendarToday;
        const days = [...root.querySelectorAll('[data-calendar-date]')];
        // Agenda omits empty days: use today's position before the next event day.
        const target = days.find(day => day.dataset.calendarDate >= today) || days.at(-1) || root;
        root.style.paddingBottom = '';
        const toolbarBottom = document.querySelector('.topbar')?.getBoundingClientRect().bottom || 0;
        const top = window.scrollY + target.getBoundingClientRect().top - toolbarBottom - 8;
        // A short month/week may fit entirely on screen. Give the document enough
        // room below today to place its heading below the fixed toolbar anyway.
        const contentBottom = window.scrollY + root.getBoundingClientRect().bottom;
        const missingSpace = Math.max(0, top) + window.innerHeight - contentBottom;
        if (missingSpace > 0) root.style.paddingBottom = `${Math.ceil(missingSpace)}px`;
        window.scrollTo(0, Math.max(0, top));
        // The month grid can also scroll sideways on narrow screens.
        const grid = target.closest('.month-scroll');
        if (grid) {
            const cell = target.getBoundingClientRect(), bounds = grid.getBoundingClientRect();
            if (cell.left < bounds.left) grid.scrollLeft += cell.left - bounds.left;
            else if (cell.right > bounds.right) grid.scrollLeft += cell.right - bounds.right;
        }
    }

    function updateSelection() {
        const root = workspace();
        if (!root || !document.getElementById('daisy-bulk-form')) return;
        root.classList.toggle('selection-mode', state.selecting);
        const available = new Set(checkboxes().map(input => input.value));
        state.selected = new Set([...state.selected].filter(key => available.has(key)));
        checkboxes().forEach(input => {
            input.checked = state.selected.has(input.value);
            input.closest('.item-row').classList.toggle('is-selected', input.checked);
        });
        const count = state.selected.size;
        document.getElementById('daisy-bulk-form').hidden = !state.selecting;
        document.getElementById('selection-count').textContent = `${count} selected`;
        document.querySelectorAll('.select-mode-button').forEach(button => {
            button.setAttribute('aria-pressed', String(state.selecting));
            button.querySelector('span').textContent = state.selecting ? 'Done' : 'Select';
        });
        document.querySelectorAll('[data-requires-selection]').forEach(button => { button.disabled = !count; });
        document.querySelectorAll('[data-lists-only]').forEach(button => {
            button.disabled = !count || [...state.selected].some(key => !key.startsWith('list:'));
            button.title = button.disabled ? 'Select lists only for this action.' : '';
        });
        const keys = [...state.selected];
        const tasksOnly = count > 0 && keys.every(key => key.startsWith('todo:'));
        document.querySelectorAll('[data-single-item]').forEach(button => { button.disabled = count !== 1 || keys[0]?.startsWith('datetime_header:'); });
        document.querySelectorAll('[data-single-todo]').forEach(button => { button.disabled = count !== 1 || !tasksOnly; });
        document.querySelectorAll('[data-todos-only]').forEach(button => { button.disabled = !tasksOnly; });
        document.querySelectorAll('[data-pin-items]').forEach(button => { button.disabled = !count || keys.some(key => !/^(todo|list):/.test(key)); });
        document.querySelectorAll('[data-movable]').forEach(button => { button.disabled = !count || keys.some(key => !/^(todo|note):/.test(key)); });
        document.querySelectorAll('[data-native-todos]').forEach(button => {
            button.disabled = !tasksOnly || checkboxes().some(input => state.selected.has(input.value) && input.dataset.listId !== root.dataset.listId);
        });
        const dates = document.getElementById('selected-dates');
        dates.hidden = count !== 1;
        if (count === 1) {
            const input = checkboxes().find(box => state.selected.has(box.value));
            dates.textContent = `Created: ${input.dataset.created}` + (input.dataset.modified ? ` · Modified: ${input.dataset.modified}` : '');
        }
    }

    function snapshot() {
        const root = workspace();
        if (!root) return;
        root.querySelectorAll('details[data-persist], #create-list-details').forEach(details => {
            state.details.set(details.dataset.persist || details.id, details.open);
        });
        state.draft = {};
        root.querySelectorAll('form[data-draft]').forEach(form => {
            const fields = {};
            [...form.elements].filter(input => input.name && !['submit', 'button'].includes(input.type)).forEach(input => {
                if (['checkbox', 'radio'].includes(input.type)) {
                    if (!fields[input.name]) fields[input.name] = { checked: [] };
                    if (input.checked) fields[input.name].checked.push(input.value);
                } else fields[input.name] = { value: input.value };
            });
            state.draft[form.id] = fields;
        });
        const active = document.activeElement;
        state.focusId = active?.id || null;
        const row = active?.closest('.item-row');
        state.focus = row && active.matches('.completion-checkbox') ? row.dataset.itemKey : null;
    }

    function initialize() {
        const root = workspace();
        if (!root) return;
        const scope = root.dataset.scope || root.dataset.folder;
        const firstLoad = state.scope === null;
        const enteringScope = state.scope !== scope;
        if (state.scope !== null && state.scope !== scope) {
            state.selected.clear();
            state.selecting = false;
            state.details.clear();
            state.draft = {};
            state.targets.clear();
            state.focus = null;
            state.focusId = null;
            state.filter = '';
            state.taskOptions = null;
        }
        state.scope = scope;
        root.querySelectorAll('details[data-persist], #create-list-details').forEach(details => {
            const key = details.dataset.persist || details.id;
            if (state.details.has(key)) details.open = state.details.get(key);
        });
        root.querySelectorAll('form[data-draft]').forEach(form => {
            const fields = state.draft[form.id];
            if (!fields) return;
            [...form.elements].forEach(input => {
                const saved = fields[input.name];
                if (!saved) return;
                if (saved.checked) input.checked = saved.checked.includes(input.value);
                else input.value = saved.value;
            });
        });
        openDayDialog();
        state.targets.forEach((title, id) => {
            const caption = document.querySelector(`#${id} [data-target-title]`);
            if (caption) caption.textContent = title;
        });
        if (root.dataset.pageTitle) {
            // Match the server-rendered <title>: "Page · Simple Todo".
            const page = root.dataset.pageTitle, app = document.body.dataset.appTitle;
            document.title = !app || page === app || page.endsWith(' · ' + app) ? page : `${page} · ${app}`;
        }
        if (firstLoad) root.querySelector('.highlighted-task')?.scrollIntoView({ block: 'center' });
        const filter = root.querySelector('[data-row-filter]');
        if (filter) {
            if (state.filter && !filter.value) filter.value = state.filter;
            filterRows(filter);
        }
        // New task: keep the tags and Add button shown or hidden as the user left them.
        const options = document.getElementById('task-create-options');
        if (options) {
            const open = state.taskOptions ?? options.dataset.defaultOpen === 'true';
            options.hidden = !open;
            root.querySelector('.task-options-toggle')?.setAttribute('aria-expanded', String(open));
        }
        initializeEditor(root);
        if (root.dataset.editor && (firstLoad || enteringScope)) document.getElementById('editor-title')?.focus({ preventScroll: true });
        // Arriving from a calendar note icon: focus the main field or the specific attached note.
        if (firstLoad && /^#event-note(?:-\d+)?$/.test(location.hash)) document.getElementById(location.hash.slice(1))?.focus();
        const search = root.dataset.results === 'search' && (firstLoad || enteringScope) ? document.getElementById('search-q') : null;
        // Keep refining a search from the keyboard; leave linked results unfocused.
        if (search && (!firstLoad || !search.value)) {
            search.focus({ preventScroll: true });
            search.setSelectionRange(search.value.length, search.value.length);
        }
        updateSelection();
        if (state.focusId) document.getElementById(state.focusId)?.focus({ preventScroll: true });
        if (state.reorderFocus) {
            // Keep focus on the moved list's button so it can be moved again from the keyboard.
            const [id, to] = state.reorderFocus.split(':');
            const buttons = [...root.querySelectorAll(`[data-reorder-key^="${id}:"]`)];
            (buttons.find(button => button.dataset.reorderKey.endsWith(':' + to) && !button.disabled)
                || buttons.find(button => !button.disabled))?.focus({ preventScroll: true });
            buttons[0]?.closest('.reorder-row')?.scrollIntoView({ block: 'nearest' });
            state.reorderFocus = null;
        }
        if (state.focus) {
            const input = [...root.querySelectorAll('.item-row')].find(row => row.dataset.itemKey === state.focus)?.querySelector('.completion-checkbox');
            input?.focus({ preventScroll: true });
        }
    }

    function initializeEditor(root, changed = null) {
        if (!root?.dataset.editor) return;
        const form = document.getElementById('editor-form');
        const deferred = form.elements.is_delayed;
        if (deferred) {
            const input = form.elements.delayed_until;
            input.disabled = !deferred.checked;
            input.required = deferred.checked;
            if (changed?.name === 'is_delayed' && deferred.checked && !input.value) {
                const tomorrow = new Date();
                tomorrow.setDate(tomorrow.getDate() + 1);
                input.value = [tomorrow.getFullYear(), String(tomorrow.getMonth() + 1).padStart(2, '0'), String(tomorrow.getDate()).padStart(2, '0')].join('-');
            }
        }
        const colour = form.elements.use_color;
        if (colour) {
            const input = form.elements.color;
            const picker = form.querySelector('[data-editor-colour-picker]');
            input.disabled = picker.disabled = !colour.checked;
            input.required = colour.checked;
            if (/^#[0-9a-f]{3}([0-9a-f]{3})?$/i.test(input.value)) {
                picker.value = input.value.length === 4 ? '#' + [...input.value.slice(1)].map(character => character + character).join('') : input.value;
            }
            if (changed?.name === 'use_color' && colour.checked && !input.value) input.value = picker.value;
        }
        form.querySelectorAll('[data-editor-alarm]').forEach(group => {
            const active = form.elements.alarm_mode.value === group.dataset.editorAlarm;
            group.hidden = !active;
            group.querySelectorAll('input').forEach(input => {
                input.disabled = !active;
                input.required = active;
            });
        });
    }

    function newList() {
        document.getElementById('create-list-details').open = true;
        document.getElementById('new-list-name').focus();
        document.getElementById('create-list-details').scrollIntoView({ behavior: 'auto', block: 'nearest' });
    }

    function showPanel(name, key) {
        const panel = document.getElementById('panel-' + name);
        if (!panel) return;
        if (['subtask', 'task-note', 'defer', 'alarm', 'hashtags'].includes(name)) {
            key = key || (state.selected.size === 1 ? [...state.selected][0] : null);
            const row = [...document.querySelectorAll('.task-row')].find(item => item.dataset.itemKey === key);
            if (!row || !key.startsWith('todo:')) { showError('Select exactly one task.'); return; }
            const form = panel.querySelector('form');
            const caption = panel.querySelector('[data-target-title]');
            caption.textContent = row.dataset.taskTitle;
            state.targets.set(panel.id, caption.textContent);
            form.querySelectorAll('[name="parent_id"], [name="todo_id"]').forEach(input => { input.value = key.split(':')[1]; });
            form.querySelectorAll('input[name="items"]').forEach(input => { input.value = key; });
            if (name === 'hashtags') form.elements.hashtags.value = row.dataset.hashtags ? '#' + row.dataset.hashtags : '';
            if (name === 'defer') form.elements.delayed_until.value = row.dataset.deferred;
            if (name === 'alarm') {
                form.elements.alarm_mode.value = row.dataset.alarmStart ? 'range' : 'none';
                form.elements.alarm_start_range.value = row.dataset.alarmStart;
                form.elements.alarm_end_range.value = row.dataset.alarmEnd;
            }
        }
        panel.open = true;
        panel.querySelector('input:not([type=hidden]), textarea, select')?.focus();
        panel.scrollIntoView({ block: 'nearest' });
    }

    // "Move to…": tasks and notes go to a list; lists and folders go to a folder.
    async function openMoveDialog(kind, only) {
        const dialog = document.getElementById('move-dialog');
        const form = document.getElementById('move-form');
        const pattern = kind === 'folder' ? /^(list|folder):/ : /^(todo|note):/;
        const keys = (only || [...state.selected]).filter(key => pattern.test(key));
        if (!dialog || !keys.length || document.querySelector('dialog[open]')) return;
        form.querySelector('[data-move-items]').replaceChildren(...keys.map(key =>
            Object.assign(document.createElement('input'), { type: 'hidden', name: 'items', value: key })));
        // The server re-renders the page the selection came from, using its own query.
        const bulk = document.getElementById('daisy-bulk-form');
        // getAttribute: the form's buttons are named "action", which shadows form.action.
        form.elements.return_query.value = bulk ? new URL(bulk.getAttribute('action'), location.href).search : location.search;
        const count = (prefix, one, many) => {
            const total = keys.filter(key => key.startsWith(prefix + ':')).length;
            return total ? `${total} ${total === 1 ? one : many}` : '';
        };
        let summary = [count('todo', 'task', 'tasks'), count('note', 'note', 'notes'), count('list', 'list', 'lists'), count('folder', 'folder', 'folders')].filter(Boolean).join(' and ');
        if (keys.length === 1) {
            const row = [...document.querySelectorAll('.item-row')].find(item => item.dataset.itemKey === keys[0]);
            const title = row?.querySelector('.item-title')?.textContent.trim() || (workspace().dataset.scope === keys[0] ? workspace().dataset.pageTitle : '');
            if (title) summary = `“${title}”`;
        }
        dialog.querySelector('[data-move-summary]').textContent = summary;
        dialog.querySelector('[data-move-hint]').textContent = kind === 'folder'
            ? 'Lists keep their tasks, and folders keep everything inside them.' : 'Subtasks and attached notes move with their tasks.';
        const box = form.querySelector('[data-move-targets]');
        box.innerHTML = '<span class="loading loading-spinner loading-sm" aria-label="Loading destinations"></span>';
        dialog.showModal();
        try {
            const query = new URLSearchParams([['kind', kind], ...keys.map(key => ['items', key])]);
            const response = await fetch(SimpleTodo.url('/app/move/targets') + '?' + query, { headers: { 'HX-Request': 'true' } });
            if (!response.ok) throw new Error(String(response.status));
            box.innerHTML = await response.text();
            const filter = box.querySelector('[data-pick-filter]');
            filter?.focus();
            if (filter) filterPick(filter);
        } catch {
            box.textContent = 'The destinations could not be loaded. Check your connection and try again.';
        }
    }

    // Search-select lists (jump box and "Move to…"): filter as you type, arrow keys to choose.
    function pickItems(box) { return [...box.querySelectorAll('.jump-item')]; }
    function setPickActive(box, item) {
        pickItems(box).forEach(option => {
            option.classList.toggle('is-active', option === item);
            option.setAttribute('aria-selected', String(option === item));
        });
        item?.scrollIntoView({ block: 'nearest' });
    }
    function filterPick(input) {
        const box = input.closest('dialog');
        // "work/proj" and "work proj" both match "Work > Projects".
        const tokens = input.value.toLowerCase().split(/[\s/>]+/).filter(Boolean);
        pickItems(box).forEach(option => {
            if (option.dataset.jumpSearch !== undefined) {
                option.hidden = !tokens.length;
                option.href = option.dataset.jumpSearch + '?q=' + encodeURIComponent(input.value.trim());
                option.querySelector('[data-jump-term]').textContent = input.value.trim();
            } else option.hidden = !tokens.every(token => option.dataset.name.includes(token));
        });
        const visible = pickItems(box).filter(option => !option.hidden);
        const empty = box.querySelector('[data-pick-empty]');
        if (empty) empty.hidden = visible.length > 0 || !pickItems(box).length;
        setPickActive(box, visible[0]);
    }
    async function openJump() {
        const dialog = document.getElementById('jump-dialog');
        if (!dialog || document.querySelector('dialog[open]')) return;
        const input = document.getElementById('jump-input'), box = document.getElementById('jump-results');
        input.value = '';
        dialog.showModal();
        input.focus();
        try {
            const response = await fetch(SimpleTodo.url('/app/jump'), { headers: { 'HX-Request': 'true' } });
            if (!response.ok) throw new Error(String(response.status));
            box.innerHTML = await response.text();
            if (window.htmx) htmx.process(box);
            filterPick(input);
        } catch {
            box.textContent = 'The places could not be loaded. Check your connection and try again.';
        }
    }
    // The strip sits outside the workspace, so refresh it when the workspace moves to another place.
    async function refreshStrip(at) {
        state.stripAt = at;
        try {
            const response = await fetch(SimpleTodo.url('/app/shortcuts') + '?at=' + encodeURIComponent(at), { headers: { 'HX-Request': 'true' } });
            const strip = document.getElementById('shortcut-strip');
            if (!response.ok || !strip || state.stripAt !== at) return;
            strip.outerHTML = await response.text();
            if (window.htmx) htmx.process(document.getElementById('shortcut-strip'));
        } catch { /* The strip is a convenience; the next page load refreshes it. */ }
    }

    function showError(message) {
        const container = document.getElementById('page-feedback');
        const alert = document.createElement('div');
        alert.className = 'alert alert-error feedback';
        alert.setAttribute('role', 'alert');
        alert.textContent = message;
        container.replaceChildren(alert);
    }

    function filterRows(input) {
        const term = input.value.trim().replace(/^#/, '').toLowerCase();
        const rows = [...workspace().querySelectorAll(input.dataset.rowFilter)];
        rows.forEach(row => { row.hidden = Boolean(term) && !row.dataset.name.includes(term); });
        const empty = document.getElementById('hashtag-filter-empty');
        if (empty) empty.hidden = !rows.length || rows.some(row => !row.hidden);
    }
    document.addEventListener('input', event => {
        if (event.target.matches?.('[data-colour-picker]')) {
            const form = event.target.form;
            form.querySelector('[data-colour-toggle]').checked = true;
            form.querySelector('[data-colour-value]').value = event.target.value;
        }
        if (event.target.matches?.('[data-pick-filter]')) { filterPick(event.target); return; }
        if (!event.target.matches?.('[data-row-filter]')) return;
        state.filter = event.target.value;
        filterRows(event.target);
    });
    document.addEventListener('change', event => {
        if (event.target.matches('[data-add-tag]') && event.target.value) {
            const input = event.target.closest('form').querySelector('input[name="hashtags"]');
            const tag = '#' + event.target.value;
            if (!input.value.split(/\s+/).includes(tag)) input.value = (input.value.trim() + ' ' + tag).trim();
            event.target.value = '';
        }
        if (event.target.matches('[data-colour-toggle]')) {
            const form = event.target.form;
            form.querySelector('[data-colour-value]').value = event.target.checked ? form.querySelector('[data-colour-picker]').value : '';
        }
        if (event.target.matches('[data-invoice-target]')) {
            // Correcting an invoice starts from its recorded values.
            const form = event.target.form, option = event.target.selectedOptions[0];
            form.elements.invoice_number.value = option.dataset.number;
            form.elements.invoice_date.value = option.dataset.issued;
            form.elements.actual_due_date.value = option.dataset.due;
            form.elements.actual_amount.value = option.dataset.amount;
            form.elements.credit_applied.value = '';
            form.elements.amount_type.value = 'gross';
            form.elements.amount_type.dispatchEvent(new Event('change', { bubbles: true }));
        }
        if (event.target.matches('[data-amount-type]')) {
            const form = event.target.form, net = event.target.value === 'net';
            form.elements.invoice_date.required = net;
            form.elements.credit_applied.disabled = !net;
            form.querySelector('.invoice-credit').hidden = !net;
        }
        if (event.target.matches('[data-editor-control]')) initializeEditor(workspace(), event.target);
        if (event.target.matches('[data-editor-colour-picker]')) {
            const form = event.target.closest('form');
            form.elements.color.value = event.target.value;
        }
        if (event.target.matches('[data-editor-colour]') && /^#[0-9a-f]{3}([0-9a-f]{3})?$/i.test(event.target.value)) {
            let value = event.target.value;
            if (value.length === 4) value = '#' + [...value.slice(1)].map(character => character + character).join('');
            event.target.closest('form').querySelector('[data-editor-colour-picker]').value = value;
        }
        if (!event.target.matches('.selection-checkbox')) return;
        const input = event.target;
        if (input.checked) state.selected.add(input.value);
        else state.selected.delete(input.value);
        updateSelection();
    });

    document.addEventListener('click', event => {
        const reorder = event.target.closest('[data-reorder-key]');
        if (reorder) state.reorderFocus = reorder.dataset.reorderKey;
        const wikiLink = event.target.closest('a[data-wiki-group]');
        if (wikiLink) htmx.trigger(wikiLink, 'wiki-open');
        const clearButton = event.target.closest('[data-clear-field]');
        if (clearButton) clearButton.form.elements[clearButton.dataset.clearField].value = '';
        const button = event.target.closest('[data-command]');
        if (!button || button.disabled) return;
        const command = button.dataset.command;
        if (command === 'calendar-today') {
            event.preventDefault();
            if (workspace()?.dataset.calendarContainsToday === 'true') scrollCalendarToday();
            else htmx.trigger(button, 'calendar-today');
        }
        if (command === 'new-list') newList();
        if (command === 'jump') openJump();
        if (command === 'toggle-task-options') {
            const options = document.getElementById('task-create-options');
            state.taskOptions = options.hidden;
            options.hidden = !state.taskOptions;
            button.setAttribute('aria-expanded', String(state.taskOptions));
            if (state.taskOptions) options.querySelector('input')?.focus();
        }
        if (command === 'show-panel') {
            button.closest('.row-dropdown')?.removeAttribute('open');
            showPanel(button.dataset.panel, button.dataset.key);
        }
        if (command === 'close-panel') button.closest('details').open = false;
        if (command === 'bill-panel') {
            const panel = document.getElementById(button.dataset.panel);
            if (!panel) return;
            panel.hidden = !panel.hidden;
            button.setAttribute('aria-expanded', String(!panel.hidden));
            if (!panel.hidden) panel.querySelector('input:not([type=hidden]), select')?.focus({ preventScroll: true });
        }
        if (command === 'move-to-folder') openMoveDialog('folder');
        if (command === 'move-item') {
            // One row's own Move button: move just that item, whatever is selected.
            button.closest('details.dropdown')?.removeAttribute('open');
            openMoveDialog(/^(list|folder):/.test(button.dataset.key) ? 'folder' : 'list', [button.dataset.key]);
        }
        if (command === 'toggle-folder') {
            const children = document.getElementById(button.getAttribute('aria-controls'));
            if (!children) return;
            const collapsed = !children.hidden;
            children.hidden = collapsed;
            button.setAttribute('aria-expanded', String(!collapsed));
            button.setAttribute('aria-label', button.getAttribute('aria-label').replace(/^(Expand|Collapse)/, collapsed ? 'Expand' : 'Collapse'));
            // Collapse state is a preference; keep the change locally if saving fails.
            fetch(button.dataset.collapseUrl, { method: 'POST', body: new URLSearchParams({ collapsed: String(collapsed) }) }).catch(() => {});
        }
        if (command === 'toggle-selection') {
            state.selecting = !state.selecting;
            if (!state.selecting) state.selected.clear();
            updateSelection();
        }
        if (command === 'select-item') {
            state.selecting = true;
            state.selected.add(button.dataset.key);
            button.closest('details').open = false;
            updateSelection();
        }
        if (command === 'select-all') {
            checkboxes().forEach(input => state.selected.add(input.value));
            updateSelection();
        }
        if (command === 'clear-selection') { state.selected.clear(); updateSelection(); }
        if (command === 'edit-selected' && state.selected.size === 1) {
            const [kind, id] = [...state.selected][0].split(':');
            navigate(SimpleTodo.url(`/app/${kind}/${id}/edit`) + (workspace().dataset.editQuery || ''));
        }
        if (command === 'move-to-list') openMoveDialog('list');
        if (command === 'delete-edit-item') {
            document.getElementById('delete-title').textContent = `Delete this ${workspace().dataset.editorLabel}?`;
            document.getElementById('delete-description').textContent = workspace().dataset.editor === 'note' ? 'This note will be permanently deleted.' : 'This item and its nested tasks and notes will be permanently deleted.';
            document.getElementById('delete-selection-summary').textContent = workspace().dataset.editorTitle;
            document.getElementById('delete-dialog').showModal();
        }
        if (command === 'delete-selected' && state.selected.size) {
            document.getElementById('delete-title').textContent = 'Delete selected items?';
            document.getElementById('delete-description').textContent = 'The selected items will be permanently deleted. Deleting a list or parent task also deletes its nested tasks and notes.';
            document.getElementById('delete-selection-summary').textContent = `${state.selected.size} selected item(s)`;
            document.getElementById('delete-dialog').showModal();
        }
        if (command === 'confirm-delete') {
            const editorForm = document.getElementById('editor-delete-form');
            if (editorForm) {
                document.getElementById('delete-dialog').close();
                htmx.trigger(editorForm, 'submit');
                return;
            }
            const form = document.getElementById('daisy-bulk-form');
            let action = form.querySelector('input[name="action"]');
            if (!action) { action = document.createElement('input'); action.type = 'hidden'; action.name = 'action'; form.append(action); }
            action.value = 'delete';
            document.getElementById('delete-dialog').close();
            htmx.trigger(form, 'submit');
            action.remove();
        }
        if (command === 'dismiss-feedback') document.getElementById('page-feedback').replaceChildren();
    });

    // The server renders an open day as a plain overlay; make it a true modal
    // (focus stays inside, Escape closes) once scripts are running.
    function openDayDialog() {
        const dialog = document.getElementById('day-dialog');
        if (!dialog || dialog.matches(':modal')) return;
        dialog.removeAttribute('open');
        dialog.showModal();
    }
    document.addEventListener('cancel', event => {
        if (event.target.id !== 'day-dialog') return;
        event.preventDefault();
        event.target.querySelector('[data-close-day]').click();
    }, true);

    // Day menus sit in narrow calendar columns (and a scrolling month grid), so
    // pin the open one to the viewport beside its button instead of the column.
    function placeDayMenu(details) {
        const menu = details.querySelector('.dropdown-content');
        const button = details.querySelector('summary').getBoundingClientRect();
        Object.assign(menu.style, { position: 'fixed', margin: '0', inset: 'auto', left: '0px', top: '0px' });
        const width = menu.offsetWidth, height = menu.offsetHeight, edge = 8;
        const left = Math.max(edge, Math.min(button.right - width, window.innerWidth - width - edge));
        const below = button.bottom + 4;
        const top = below + height <= window.innerHeight - edge ? below : Math.max(edge, button.top - height - 4);
        menu.style.left = left + 'px';
        menu.style.top = top + 'px';
    }
    document.addEventListener('toggle', event => {
        if (event.target.matches?.('details.day-menu') && event.target.open) placeDayMenu(event.target);
    }, true);
    ['scroll', 'resize'].forEach(name => window.addEventListener(name, () => {
        document.querySelectorAll('details.day-menu[open]').forEach(placeDayMenu);
    }, { passive: true, capture: true }));

    document.addEventListener('click', event => {
        document.querySelectorAll('.dropdown[open]').forEach(details => {
            if (!details.contains(event.target)) details.open = false;
        });
        if (event.target.closest('#jump-results .jump-item')) document.getElementById('jump-dialog').close();
        if (event.target.closest('a[href="#older-pins"]')) {
            document.getElementById('older-pins').open = true;
        }
    });

    document.addEventListener('keydown', event => {
        if (event.key !== 'Escape') return;
        document.querySelectorAll('.dropdown[open]').forEach(details => {
            details.open = false;
            if (details.contains(document.activeElement)) details.querySelector('summary').focus();
        });
    });

    document.addEventListener('keydown', event => {
        const dialog = document.querySelector('#jump-dialog[open], #move-dialog[open]');
        if (dialog) {
            const visible = pickItems(dialog).filter(option => !option.hidden);
            const index = visible.findIndex(option => option.classList.contains('is-active'));
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                if (visible.length) setPickActive(dialog, visible[(index + (event.key === 'ArrowDown' ? 1 : -1) + visible.length) % visible.length]);
            }
            // Enter in the filter chooses the highlighted row, not the first button in the form.
            if (event.key === 'Enter' && event.target.matches?.('[data-pick-filter]')) {
                event.preventDefault();
                visible[Math.max(index, 0)]?.click();
            }
            return;
        }
        const typing = event.target.closest?.('input, textarea, select, [contenteditable]');
        const shortcut = (event.key.toLowerCase() === 'k' && (event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey)
            || (event.key === '/' && !typing && !event.ctrlKey && !event.metaKey && !event.altKey);
        if (!shortcut) return;
        event.preventDefault();
        openJump();
    });

    document.body.addEventListener('htmx:before:request', () => snapshot());
    document.body.addEventListener('htmx:before:response', event => {
        const response = event.detail.ctx.response.raw;
        if (response.url && new URL(response.url).pathname === SimpleTodo.url('/login')) {
            event.preventDefault();
            const next = SimpleTodo.routePath() + window.location.search;
            window.location.assign(SimpleTodo.url('/login') + '?next=' + encodeURIComponent(next));
        }
    });
    let anchor = null;
    document.body.addEventListener('htmx:before:swap', event => {
        const ctx = event.detail.ctx;
        const source = ctx.sourceElement;
        // Remember where the control the user acted on sits on screen. Safari has no
        // CSS scroll anchoring, so content added above it (feedback) would push it down.
        anchor = ctx.target !== document.body && source?.isConnected
            ? { element: source, top: source.getBoundingClientRect().top } : null;
        if (ctx.response?.status >= 400) {
            const input = source?.querySelector?.('.completion-checkbox');
            if (input) input.checked = !input.checked;
            if (![400, 404, 422].includes(ctx.response.status) || !ctx.response.headers.get('HX-Retarget')) event.preventDefault();
        } else {
            const form = source?.matches?.('form[data-draft]') ? source : source?.closest?.('form[data-draft]');
            if (form) {
                delete state.draft[form.id];
                // Morphing keeps live input values (especially the focused one), so clear a
                // successfully submitted form before the server's fresh copy is merged in.
                form.reset();
                state.focus = null;
                state.focusId = form.id === 'task-create-form' ? 'task-title' : form.id === 'create-list-form' ? 'new-list-name' : form.id === 'expense-form' ? 'exp-amount' : form.id === 'editor-link-form' ? 'editor-link-url' : null;
                const panel = form.closest('.detail-editor');
                if (panel && panel.id !== 'panel-task') state.details.set(panel.dataset.persist, false);
            }
        }
    });
    document.body.addEventListener('htmx:after:swap', event => {
        const target = event.detail.ctx.target;
        if (target === document.body || target?.id === 'daisy-workspace' || target === '#daisy-workspace') {
            initialize();
            const at = workspace()?.dataset.pageUrl || '';
            if (target === document.body) state.stripAt = at;
            else if (at !== state.stripAt) refreshStrip(at);
        }
        if (anchor?.element.isConnected) {
            const shift = anchor.element.getBoundingClientRect().top - anchor.top;
            if (Math.abs(shift) >= 1) window.scrollBy(0, shift);
        }
        anchor = null;
    });
    document.body.addEventListener('htmx:after:request', event => {
        const ctx = event.detail.ctx;
        if (ctx?.sourceElement?.matches?.('[data-command="calendar-today"]') && ctx.response?.status < 400) {
            requestAnimationFrame(scrollCalendarToday);
        }
        // Close the move picker once its request finishes so the result is visible.
        if (event.detail.ctx?.sourceElement?.closest?.('#move-form')) document.getElementById('move-dialog')?.close();
    });
    document.body.addEventListener('daisy:selection-cleared', () => {
        state.selected.clear();
        updateSelection();
    });
    document.addEventListener('htmx:before:history:restore', () => {
        state.selected.clear(); state.selecting = false; state.details.clear(); state.draft = {}; state.targets.clear(); state.focus = null; state.focusId = null;
    });
    document.body.addEventListener('htmx:response:error', event => {
        const response = event.detail.ctx.response;
        if ([400, 404, 422].includes(response.status) && response.headers.get('HX-Retarget')) return;
        showError(`The update could not be saved (HTTP ${response.status}). Try again.`);
    });
    document.body.addEventListener('htmx:error', event => {
        const ctx = event.detail.ctx;
        if (!ctx?.response) {
            const input = ctx?.sourceElement?.querySelector?.('.completion-checkbox');
            if (input) input.checked = !input.checked;
        }
        showError('The update could not be completed. Check your connection and try again.');
    });
    initialize();
    state.stripAt = workspace()?.dataset.pageUrl || '';
})();

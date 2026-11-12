
// ---------------------------------------------------------------------------
// Dynamic Folder Filters for Smart Folders
// ---------------------------------------------------------------------------

function addFolderFilter() {
    const container = document.getElementById('folder-filters-container');
    const template = document.getElementById('folder-filter-template');

    // Check limit (optional, but good for UI sanity)
    if (container.children.length >= 20) {
        alert("Maximum number of folder filters reached.");
        return;
    }

    const clone = template.content.cloneNode(true);
    const wrapper = clone.querySelector('div');

    // Assign unique ID for potential future use or just robust DOM
    const uniqueId = Date.now() + Math.random().toString(36).substr(2, 9);
    wrapper.dataset.filterId = uniqueId;

    container.appendChild(clone);
}

function removeFolderFilter(btn) {
    const wrapper = btn.closest('.folder-filter-row');
    wrapper.remove();
}

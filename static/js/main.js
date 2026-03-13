// Sidebar toggle
const sidebarToggle = document.getElementById('sidebarToggle');
const body = document.body;

if (sidebarToggle) {
  sidebarToggle.addEventListener('click', () => {
    body.classList.toggle('sidebar-collapsed');
    // Mobile: toggle 'open' on sidebar
    const sidebar = document.getElementById('sidebar');
    if (window.innerWidth <= 768 && sidebar) {
      sidebar.classList.toggle('open');
    }
  });
}

// Auto-dismiss alerts after 5s
document.querySelectorAll('.alert.alert-success, .alert.alert-info').forEach(el => {
  setTimeout(() => {
    const alert = bootstrap.Alert.getOrCreateInstance(el);
    if (alert) alert.close();
  }, 5000);
});

// Initialize EasyMDE on all textareas with data-markdown
document.querySelectorAll('textarea[data-markdown]').forEach(el => {
  new EasyMDE({
    element: el,
    spellChecker: false,
    autofocus: false,
    toolbar: [
      'bold', 'italic', 'strikethrough', '|',
      'heading-1', 'heading-2', 'heading-3', '|',
      'code', 'quote', '|',
      'unordered-list', 'ordered-list', '|',
      'link', 'image', 'table', 'horizontal-rule', '|',
      'preview', 'side-by-side', 'fullscreen', '|',
      'guide',
    ],
    previewRender: (text) => {
      // Use marked if available, else basic HTML
      return '<em>Vorschau wird geladen…</em>';
    },
    status: ['lines', 'words', 'cursor'],
    minHeight: '300px',
  });
});

// Confirm dialogs for delete buttons
document.querySelectorAll('[data-confirm]').forEach(btn => {
  btn.addEventListener('click', (e) => {
    if (!confirm(btn.dataset.confirm || 'Wirklich löschen?')) {
      e.preventDefault();
    }
  });
});

// Ticket filter: submit on change
document.querySelectorAll('.auto-submit').forEach(el => {
  el.addEventListener('change', () => el.closest('form').submit());
});

// Asset tag auto-generate
const nameInput = document.getElementById('asset-name');
const tagInput = document.getElementById('asset-tag');
if (nameInput && tagInput) {
  nameInput.addEventListener('input', () => {
    if (!tagInput.value) {
      const slug = nameInput.value.trim().substring(0, 6).toUpperCase().replace(/\s+/g, '-');
      tagInput.placeholder = 'z.B. IT-' + slug;
    }
  });
}

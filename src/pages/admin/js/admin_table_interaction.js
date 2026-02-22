() => {
  if (typeof window === 'undefined') return [];

  const ensureRoot = () => {
    if (typeof window.gradioApp === 'function') {
      try { const app = window.gradioApp(); if (app) return app; } catch {}
    }
    const el = document.querySelector('gradio-app');
    return el ? (el.shadowRoot || el) : document;
  };

  const q  = (root, sel) => root.querySelector(sel);
  const qa = (root, sel) => Array.from(root.querySelectorAll(sel));
  let currentEditingRow = null;

  const setTextboxValue = (el, val) => {
    el.value = String(val);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  };

  const hideCellMenus = (root) => {
    const dfRoot = q(root, '#admin-editor-df');
    if (!dfRoot) return;
    const candidateSelector = 'button, [role="button"], [aria-haspopup]';
    const dotLabels = ['⋮', '...', '•••', '⋯', '…'];
    qa(dfRoot, candidateSelector).forEach((btn) => {
      if (btn.dataset.adminMenuHidden === '1') return;
      const aria = (btn.getAttribute('aria-label') || '').toLowerCase();
      const title = (btn.getAttribute('title') || '').toLowerCase();
      const text = (btn.textContent || '').trim();
      const className = (btn.className || '').toLowerCase();
      const looksLikeMenu =
        (btn.classList && btn.classList.contains('cell-menu-button')) ||
        aria.includes('menu') ||
        aria.includes('options') ||
        title.includes('menu') ||
        title.includes('options') ||
        className.includes('menu') ||
        className.includes('options') ||
        dotLabels.includes(text);
      if (looksLikeMenu) {
        btn.style.display = 'none';
        btn.dataset.adminMenuHidden = '1';
      }
    });
  };

  const getSelectEl   = () => q(root, '#admin-selected-index textarea, #admin-selected-index input');
  const getLoadBtn    = () => q(root, '#admin-load-trigger');
  const getDeleteBtn  = () => q(root, '#admin-delete-trigger');
  const getDeleteDlg  = () => q(root, '.admin-delete-dialog');
  const getInlineBox  = () => q(root, '#admin-inline-payload textarea, #admin-inline-payload input');
  const getInlineSave = () => q(root, '#admin-inline-save-trigger');

  const setSelectedIndex = (value) => {
    const el = getSelectEl();
    if (!el) return false;
    setTextboxValue(el, value);
    return true;
  };

  const triggerLoadForIndex = (value) => {
    const loadBtn = getLoadBtn();
    if (!loadBtn) return false;
    if (!setSelectedIndex(value)) return false;
    loadBtn.click();
    return true;
  };

  const hideDeleteModal = () => {
    const deleteDlg = getDeleteDlg();
    if (!deleteDlg) return;
    deleteDlg.classList.remove('modal-visible');
    deleteDlg.style.pointerEvents = 'none';
  };

  const showDeleteModal = () => {
    const deleteDlg = getDeleteDlg();
    if (deleteDlg) {
      deleteDlg.classList.add('modal-visible');
      deleteDlg.style.pointerEvents = 'auto';
    } else {
      const deleteBtn = getDeleteBtn();
      if (deleteBtn && window.confirm('Are you sure you want to delete this row?')) {
        deleteBtn.click();
      }
    }
  };

  const clearInlineEdit = (rowEl, revertToOriginal = true) => {
    if (!rowEl) return;
    rowEl.classList.remove('row-editing');
    const editBtn = rowEl.querySelector('.edit-btn');
    if (editBtn) {
      const original = editBtn.dataset.originalLabel || '✏️';
      editBtn.textContent = original;
      editBtn.classList.remove('save-mode');
      editBtn.disabled = false;
      delete editBtn.dataset.mode;
    }
    qa(rowEl, 'td[data-column]').forEach((cell) => {
      if (cell.dataset.inlineEditing !== '1') return;
      let textValue = cell.dataset.inlineOriginal || '';
      if (!revertToOriginal) {
        const input = cell.querySelector('input.inline-editor-input');
        if (input) textValue = input.value;
      }
      cell.innerHTML = '';
      cell.textContent = textValue;
      delete cell.dataset.inlineEditing;
      delete cell.dataset.inlineOriginal;
    });
    if (currentEditingRow === rowEl) {
      currentEditingRow = null;
    }
  };

  const getRowValues = (rowEl) => {
    const data = {};
    qa(rowEl, 'td[data-column]').forEach((cell) => {
      const column = cell.dataset.column;
      if (!column) return;
      const input = cell.querySelector('input.inline-editor-input');
      data[column] = input ? input.value : (cell.textContent || '');
    });
    return data;
  };

  const submitInlineEdit = (rowEl) => {
    if (!rowEl) return;
    const idx = rowEl.dataset.row ?? '';
    if (idx === '') return;
    const payloadEl = getInlineBox();
    const saveBtn = getInlineSave();
    if (!payloadEl || !saveBtn) return;
    const values = getRowValues(rowEl);
    if (!setSelectedIndex(idx)) return;
    try {
      setTextboxValue(payloadEl, JSON.stringify({ index: idx, values }));
    } catch (err) {
      console.error('Unable to serialize inline row', err);
      return;
    }
    saveBtn.click();
    currentEditingRow = null;
  };

  const beginInlineEdit = (rowEl, btn) => {
    if (!rowEl || !btn) return;
    currentEditingRow = rowEl;
    rowEl.classList.add('row-editing');
    qa(rowEl, 'td[data-column]').forEach((cell) => {
      if (cell.dataset.inlineEditing === '1') return;
      const currentText = cell.textContent || '';
      cell.dataset.inlineOriginal = currentText;
      cell.dataset.inlineEditing = '1';
      cell.innerHTML = '';
      const input = document.createElement('input');
      input.type = 'text';
      input.value = currentText;
      input.className = 'inline-editor-input';
      input.addEventListener('click', (event) => event.stopPropagation());
      input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' && !event.shiftKey) {
          event.preventDefault();
          submitInlineEdit(rowEl);
        } else if (event.key === 'Escape') {
          event.preventDefault();
          clearInlineEdit(rowEl, true);
        }
      });
      cell.appendChild(input);
    });
    btn.dataset.originalLabel = btn.dataset.originalLabel || btn.textContent || '✏️';
    btn.textContent = 'Save';
    btn.classList.add('save-mode');
    btn.disabled = false;
    const firstInput = rowEl.querySelector('td[data-column] input.inline-editor-input');
    if (firstInput) firstInput.focus();
  };

  const handleRowClick = (ev) => {
    const rowEl = ev.target.closest('.admin-table-wrapper tbody tr[data-row]');
    if (!rowEl) return;
    if (rowEl.classList.contains('row-editing')) return;
    if (ev.target.closest('.trash-btn') || ev.target.closest('.edit-btn')) return;
    if (ev.target.closest('input.inline-editor-input')) return;
    ev.preventDefault();
  };

  const handleRowDoubleClick = (ev) => {
    const rowEl = ev.target.closest('.admin-table-wrapper tbody tr[data-row]');
    if (!rowEl) return;
    if (rowEl.classList.contains('row-editing')) return;
    if (ev.target.closest('.trash-btn') || ev.target.closest('.edit-btn')) return;
    if (ev.target.closest('input.inline-editor-input')) return;
    ev.preventDefault();
    if (currentEditingRow && currentEditingRow !== rowEl) {
      clearInlineEdit(currentEditingRow, true);
    }
    const editBtn = rowEl.querySelector('.edit-btn[data-row]');
    if (editBtn) {
      beginInlineEdit(rowEl, editBtn);
    }
  };

  const handleTrashClick = (ev) => {
    const btn = ev.target.closest('.admin-table-wrapper .trash-btn[data-row]');
    if (!btn) return;
    ev.stopPropagation();
    ev.preventDefault();
    const rowEl = btn.closest('tr[data-row]');
    if (rowEl && rowEl === currentEditingRow) {
      clearInlineEdit(rowEl, true);
    }
    const i = btn.dataset.row ?? '';
    if (!setSelectedIndex(i)) return;
    showDeleteModal();
  };

  const handleEditClick = (ev) => {
    const btn = ev.target.closest('.admin-table-wrapper .edit-btn[data-row]');
    if (!btn) return;
    ev.stopPropagation();
    ev.preventDefault();
    const rowEl = btn.closest('tr[data-row]');
    if (!rowEl) return;
    if (rowEl.classList.contains('row-editing')) {
      btn.disabled = true;
      btn.textContent = 'Saving...';
      submitInlineEdit(rowEl);
    } else {
      if (currentEditingRow && currentEditingRow !== rowEl) {
        clearInlineEdit(currentEditingRow, true);
      }
      beginInlineEdit(rowEl, btn);
    }
  };

  const root = ensureRoot();
  if (!root) return [];

  if (!window.__adminDelegatedHandlersBound) {
    root.addEventListener('click', handleRowClick);
    root.addEventListener('dblclick', handleRowDoubleClick);
    root.addEventListener('click', handleTrashClick, true);
    root.addEventListener('click', handleEditClick, true);
    root.addEventListener('click', (ev) => {
      if (ev.target.closest('.admin-delete-dialog .modal-actions button')) {
        hideDeleteModal();
      }
    });
    window.__adminDelegatedHandlersBound = true;
  }

  hideCellMenus(root);

  const mo = new MutationObserver((muts) => {
    let shouldRebind = false;
    const needsBindSelector = '.admin-table-wrapper, .admin-table-wrapper .trash-btn, .admin-table-wrapper .edit-btn, .admin-table-wrapper tr[data-row]';
    for (const m of muts) {
      if (shouldRebind) break;
      const added = m.addedNodes ? Array.from(m.addedNodes) : [];
      if (!added.length) continue;
      if (added.some((n) => {
        if (n.nodeType !== 1) return false;
        const el = /** @type {Element} */ (n);
        return (el.matches && el.matches(needsBindSelector)) || (el.querySelector && el.querySelector(needsBindSelector));
      })) {
        shouldRebind = true;
      }
    }
    if (shouldRebind) {
      currentEditingRow = null;
      hideCellMenus(root);
    }
  });
  mo.observe(root, { childList: true, subtree: true, attributes: true });

  return [];
}
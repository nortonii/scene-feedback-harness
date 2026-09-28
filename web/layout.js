export function setupMinimalLayout({getState}) {
  const byId = (id) => document.getElementById(id);
  const dialogs = ['tasks-dialog', 'activity-dialog', 'references-dialog']
    .map(byId).filter(Boolean);
  const previousFocus = new WeakMap();
  const skipFocusRestore = new WeakSet();
  const seenAttention = new Set();
  const pendingAttention = new Set();
  let activeAttention = new Set();

  function syncDialogButtons(dialog) {
    for (const button of document.querySelectorAll('[data-open-dialog]')) {
      if (button.dataset.openDialog === dialog.id) {
        button.setAttribute('aria-expanded', String(dialog.open));
      }
    }
  }

  function closeDialog(dialog, {restoreFocus=true}={}) {
    if (!dialog?.open) return;
    if (!restoreFocus) skipFocusRestore.add(dialog);
    dialog.close();
    syncDialogButtons(dialog);
  }

  function openDialog(dialog, trigger) {
    if (!dialog || dialog.open) return;
    for (const other of dialogs) {
      if (other !== dialog && other.open) closeDialog(other, {restoreFocus:false});
    }
    previousFocus.set(dialog, trigger || document.activeElement);
    dialog.showModal();
    syncDialogButtons(dialog);
    closePopovers();
  }

  function closePopovers(except=null) {
    for (const details of document.querySelectorAll('details.popover, details#more-tools')) {
      if (details !== except) details.open = false;
    }
  }

  function annotationEditorOpen() {
    const editor = byId('text-editor');
    return editor && !editor.classList.contains('hidden');
  }

  function showPendingAttention() {
    for (const key of pendingAttention) {
      if (!activeAttention.has(key)) pendingAttention.delete(key);
    }
    const activity = byId('activity-dialog');
    if (activity?.open) { pendingAttention.clear(); return; }
    if (!pendingAttention.size || annotationEditorOpen()) return;
    if (document.querySelector('dialog[open]')) return;
    openDialog(activity);
    pendingAttention.clear();
  }

  function refresh() {
    const state = getState() || {};
    const bound = state.boundThreadId;
    const target = Array.isArray(state.targets)
      ? state.targets.find((item) => item?.thread_id === bound) : null;
    const rawName = target?.title?.trim();
    const name = rawName?.startsWith('未命名任务 · ')
      ? '未命名任务' : rawName || (bound ? '当前任务' : '任务');
    const taskTitle = byId('task-short-title');
    if (taskTitle && taskTitle.textContent !== name) taskTitle.textContent = name;
    const taskButton = byId('task-dialog-button');
    if (taskButton) {
      taskButton.disabled = state.deliveryMode !== 'external' || !bound;
      taskButton.title = [name, target?.model, target?.reasoning_effort].filter(Boolean).join(' · ');
      taskButton.setAttribute('aria-label', bound ? '任务：' + name : '任务');
    }

    const attention = new Set();
    for (const approval of state.approvals || []) {
      if (approval?.approval_id) attention.add('approval:' + approval.approval_id);
    }
    for (const [index, item] of (state.queue || []).entries()) {
      if (!item || !(['blocked_stale', 'delivery_uncertain'].includes(item.status) ||
          (item.status === 'failed' && !item.turn_id))) continue;
      attention.add('queue:' + (item.feedback_id || item.id || index) + ':' + item.status);
    }
    activeAttention = attention;
    for (const key of attention) {
      if (seenAttention.has(key)) continue;
      seenAttention.add(key);
      pendingAttention.add(key);
    }
    const count = byId('attention-count');
    if (count) {
      count.textContent = String(attention.size);
      count.classList.toggle('hidden', !attention.size);
    }
    const activityButton = byId('activity-dialog-button');
    if (activityButton) {
      const label = attention.size ? '执行记录 · ' + attention.size + ' 项需要处理' : '执行记录';
      activityButton.title = label;
      activityButton.setAttribute('aria-label', label);
      activityButton.classList.toggle('has-attention', !!attention.size);
    }

    const mode = state.mode;
    const more = byId('more-tools');
    const moreActive = mode === 'text' || mode === 'freehand';
    if (more) {
      more.classList.toggle('active', moreActive);
      more.querySelector('summary')?.classList.toggle('active', moreActive);
    }
    const moreLabel = byId('more-tools-label');
    if (moreLabel) moreLabel.textContent = mode === 'text' ? '字' : mode === 'freehand' ? '画笔' : '更多';
    showPendingAttention();
  }

  for (const dialog of dialogs) {
    dialog.addEventListener('click', (event) => {
      if (event.target !== dialog) return;
      const bounds = dialog.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right ||
          event.clientY < bounds.top || event.clientY > bounds.bottom) closeDialog(dialog);
    });
    dialog.addEventListener('close', () => {
      syncDialogButtons(dialog);
      const skipRestore = skipFocusRestore.delete(dialog);
      const target = previousFocus.get(dialog);
      previousFocus.delete(dialog);
      if (!skipRestore && target?.isConnected && !target.disabled) target.focus({preventScroll:true});
      showPendingAttention();
    });
  }

  document.addEventListener('click', (event) => {
    const opener = event.target.closest('[data-open-dialog]');
    if (opener && !opener.disabled) {
      openDialog(byId(opener.dataset.openDialog), opener);
      return;
    }
    const closer = event.target.closest('[data-close-dialog]');
    if (closer) {
      closeDialog(byId(closer.dataset.closeDialog));
      return;
    }
    const details = event.target.closest('details.popover, details#more-tools');
    if (details && event.target.closest('button, label.upload-button')) closePopovers();
    if (event.target.closest('.tool-button, .reference-insert, [data-selection-level], #browse-button')) refresh();
  });
  document.addEventListener('pointerdown', (event) => {
    closePopovers(event.target.closest('details.popover, details#more-tools'));
  });
  document.addEventListener('keyup', (event) => {
    if (event.key >= '1' && event.key <= '7') refresh();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closePopovers();
  });

  return {
    refresh,
    closeReferences() { closeDialog(byId('references-dialog'), {restoreFocus:false}); }
  };
}

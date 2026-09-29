export function setupChatDock({getState}) {
  const byId = (id) => document.getElementById(id);
  const dock = byId('chat-dock');
  const launcher = byId('chat-launcher');
  const history = byId('chat-history');
  const conversation = byId('conversation');
  const historyToggle = byId('chat-history-toggle');
  const latest = byId('chat-latest');
  const resizeHandle = byId('chat-resize-handle');
  let sessionId = null;
  let collapsed = false;
  let historyCollapsed = false;
  let followingLatest = true;
  let savedScrollTop = 0;
  let unread = 0;
  let dockHeight = null;
  let resizeDrag = null;
  let resizeFrame = 0;
  let lastHeightBounds = {min:1, max:window.innerHeight};

  const historyVisible = () => !!dock && !collapsed && !historyCollapsed;
  const atLatest = () => !conversation ||
    conversation.scrollHeight - conversation.scrollTop - conversation.clientHeight < 32;
  const storageKey = () => 'astra-visual-layout:' + sessionId;

  function persist() {
    if (!sessionId) return;
    try {
      localStorage.setItem(storageKey(), JSON.stringify({collapsed, historyCollapsed, dockHeight}));
    } catch { /* Layout controls remain usable when browser storage is unavailable. */ }
  }

  function updateCounts() {
    const count = conversation?.querySelectorAll('.conversation-item').length || 0;
    const messageCount = byId('chat-message-count');
    if (messageCount) {
      messageCount.textContent = (count ? count + ' 条' : '') + (unread ? ' · ' + unread + ' 新' : '');
      messageCount.classList.toggle('has-unread', !!unread);
    }
    const unreadCount = byId('chat-unread-count');
    if (unreadCount) {
      unreadCount.textContent = String(unread);
      unreadCount.classList.toggle('hidden', !unread);
    }
    if (launcher) {
      launcher.setAttribute('aria-label', '打开会话' + (unread ? '，' + unread + ' 条新消息' : ''));
      launcher.classList.toggle('has-unread', !!unread);
    }
    if (historyToggle) {
      const label = historyCollapsed ? '展开记录' : '收起记录';
      const text = historyToggle.querySelector('[data-chat-history-label]');
      if (text) text.textContent = label;
      historyToggle.setAttribute('aria-label', label + (unread ? '，' + unread + ' 条新消息' : ''));
      historyToggle.title = unread ? unread + ' 条新消息' : label;
      historyToggle.classList.toggle('has-unread', !!unread);
    }
    if (latest) {
      latest.classList.toggle('hidden', !historyVisible() || (followingLatest && !unread));
      latest.textContent = unread ? '查看新消息（' + unread + '）' : '回到最新';
    }
  }

  function scrollToLatest() {
    followingLatest = true;
    if (historyVisible() && conversation) {
      conversation.scrollTop = conversation.scrollHeight;
      savedScrollTop = conversation.scrollTop;
      unread = 0;
    }
    updateCounts();
  }

  function rememberScroll() {
    if (!historyVisible() || !conversation) return;
    savedScrollTop = conversation.scrollTop;
    followingLatest = atLatest();
  }

  function heightBounds() {
    if (!dock || collapsed) return lastHeightBounds;
    const rect = dock.getBoundingClientRect();
    const viewport = window.visualViewport;
    const top = viewport?.offsetTop || 0;
    const bottom = Math.min(rect.bottom, top + (viewport?.height || window.innerHeight));
    const cssMaximum = parseFloat(getComputedStyle(dock).maxHeight);
    const maximum = Math.max(1, Math.floor(Math.min(bottom - top - 12,
      Number.isFinite(cssMaximum) ? cssMaximum : Infinity)));
    // Everything outside history (header, input, actions and spacing) keeps its
    // natural size; leave enough history to read a message even at the minimum.
    const chrome = rect.height - (history?.getBoundingClientRect().height || 0);
    const minimumHistory = Math.min(64, Math.max(24, conversation?.scrollHeight || 0));
    const minimum = historyCollapsed ? lastHeightBounds.min : Math.ceil(chrome + minimumHistory);
    return lastHeightBounds = {min:Math.min(maximum, minimum), max:maximum};
  }

  function updateHeightAria(bounds) {
    if (!resizeHandle || !dock || collapsed) return;
    const height = historyCollapsed && dockHeight !== null
      ? Math.min(bounds.max, Math.max(bounds.min, dockHeight)) : dock.getBoundingClientRect().height;
    resizeHandle.setAttribute('aria-valuemin', String(Math.min(bounds.min, Math.round(height))));
    resizeHandle.setAttribute('aria-valuemax', String(bounds.max));
    resizeHandle.setAttribute('aria-valuenow', String(Math.round(height)));
    resizeHandle.setAttribute('aria-valuetext', Math.round(height) + ' 像素');
  }

  function restoreHistoryScroll() {
    if (!historyVisible() || !conversation) return;
    if (followingLatest) scrollToLatest();
    else {
      conversation.scrollTop = savedScrollTop;
      savedScrollTop = conversation.scrollTop;
    }
  }

  function applyHeight({restoreScroll=true}={}) {
    if (!dock) return;
    dock.classList.toggle('is-resized', dockHeight !== null);
    if (dockHeight === null) dock.style.removeProperty('--chat-height');
    if (collapsed) return;
    const bounds = heightBounds();
    if (dockHeight !== null) {
      // Viewport limits are temporary. Keep the user's preferred height so it
      // returns when a small window or the mobile keyboard opens up again.
      const value = Math.min(bounds.max, Math.max(bounds.min, dockHeight)) + 'px';
      if (dock.style.getPropertyValue('--chat-height') !== value) dock.style.setProperty('--chat-height', value);
    }
    if (restoreScroll) restoreHistoryScroll();
    updateHeightAria(bounds);
  }

  function scheduleHeightUpdate() {
    if (resizeFrame) return;
    resizeFrame = requestAnimationFrame(() => {
      resizeFrame = 0;
      applyHeight();
    });
  }

  function expandHistoryForResize() {
    rememberScroll();
    if (historyCollapsed) {
      historyCollapsed = false;
      applyLayout();
      persist();
    }
  }

  function setHeight(height) {
    rememberScroll();
    const bounds = heightBounds();
    dockHeight = Math.round(Math.min(bounds.max, Math.max(bounds.min, height)));
    applyHeight();
  }

  function finishResize(event) {
    if (!resizeDrag || (event?.pointerId !== undefined && event.pointerId !== resizeDrag.pointerId)) return;
    const pointerId = resizeDrag.pointerId;
    resizeDrag = null;
    dock?.classList.remove('resizing');
    if (resizeHandle?.hasPointerCapture(pointerId)) resizeHandle.releasePointerCapture(pointerId);
    persist();
  }

  function applyLayout() {
    dock?.classList.toggle('hidden', collapsed);
    launcher?.classList.toggle('hidden', !collapsed);
    launcher?.setAttribute('aria-expanded', String(!collapsed));
    byId('chat-collapse')?.setAttribute('aria-expanded', String(!collapsed));
    history?.classList.toggle('hidden', historyCollapsed);
    historyToggle?.setAttribute('aria-expanded', String(!historyCollapsed));
    dock?.classList.toggle('history-collapsed', historyCollapsed);
    applyHeight({restoreScroll:false});
    updateCounts();
    if (historyVisible()) {
      // Let the restored history acquire its height before restoring its position.
      requestAnimationFrame(() => {
        if (!historyVisible()) return;
        if (followingLatest) scrollToLatest();
        else if (conversation) conversation.scrollTop = savedScrollTop;
      });
    }
  }

  function open({focus=false}={}) {
    if (collapsed) {
      collapsed = false;
      applyLayout();
      persist();
    }
    if (focus) byId('feedback-note')?.focus({preventScroll:true});
  }

  function refresh() {
    const state = getState() || {};
    if (state.sessionId && state.sessionId !== sessionId) {
      finishResize();
      sessionId = state.sessionId;
      collapsed = false;
      historyCollapsed = false;
      followingLatest = true;
      savedScrollTop = 0;
      unread = 0;
      dockHeight = null;
      try {
        const stored = JSON.parse(localStorage.getItem(storageKey()) || 'null');
        collapsed = stored?.collapsed === true;
        historyCollapsed = stored?.historyCollapsed === true;
        if (Number.isFinite(stored?.dockHeight) && stored.dockHeight > 0) dockHeight = stored.dockHeight;
      } catch { /* Ignore an unavailable or invalid saved preference. */ }
      applyLayout();
    }
    const status = state.agent?.status || 'disconnected';
    const failed = !!state.networkError || state.sessionStatus === 'error' ||
      (state.sessionStatus !== 'connecting' && ['error', 'disconnected', 'delivery_uncertain'].includes(status));
    const label = state.sessionStatus === 'error' ? '连接失败'
      : state.networkError ? '连接中断，正在重试'
      : state.sessionStatus === 'connecting' ? '正在连接'
      : state.sessionStatus !== 'open' ? '会话已结束'
      : state.submitting ? '正在发送'
      : ({idle:'等待反馈', running:'Codex 正在处理', awaiting_approval:'等待审批',
        waiting:'反馈已排队', disconnected:'Codex 连接中断', delivery_uncertain:'送达待核实',
        error:'Codex 执行出错', waiting_for_mcp:'等待 MCP 读取', external_idle:'等待 MCP 读取'})[status] || '正在连接';
    const compactStatus = byId('chat-status');
    if (compactStatus) {
      compactStatus.textContent = label;
      compactStatus.title = state.networkError || state.agent?.error || byId('agent-status')?.textContent || label;
      compactStatus.classList.toggle('error', failed);
      compactStatus.classList.toggle('running', !failed && (state.submitting || status === 'running'));
      compactStatus.classList.toggle('queued', !failed && ['awaiting_approval', 'waiting'].includes(status));
    }
    launcher?.classList.toggle('has-attention', failed || status === 'awaiting_approval');
    if (launcher) launcher.title = label + (unread ? ' · ' + unread + ' 条新消息' : '');
    updateCounts();
  }

  resizeHandle?.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || event.isPrimary === false || resizeDrag || collapsed) return;
    event.preventDefault(); event.stopPropagation();
    expandHistoryForResize();
    resizeHandle.focus({preventScroll:true});
    resizeDrag = {pointerId:event.pointerId, y:event.clientY, height:dock.getBoundingClientRect().height};
    dock.classList.add('resizing');
    try { resizeHandle.setPointerCapture(event.pointerId); }
    catch { finishResize(); }
  });
  resizeHandle?.addEventListener('pointermove', (event) => {
    if (!resizeDrag || event.pointerId !== resizeDrag.pointerId) return;
    event.preventDefault(); event.stopPropagation();
    if (event.pointerType === 'mouse' && !event.buttons) { finishResize(event); return; }
    setHeight(resizeDrag.height + resizeDrag.y - event.clientY);
  });
  for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) {
    resizeHandle?.addEventListener(name, (event) => {
      if (!resizeDrag || event.pointerId !== resizeDrag.pointerId) return;
      event.preventDefault(); event.stopPropagation();
      finishResize(event);
    });
  }
  resizeHandle?.addEventListener('click', (event) => {
    event.preventDefault(); event.stopPropagation();
  });
  resizeHandle?.addEventListener('dblclick', (event) => {
    event.preventDefault(); event.stopPropagation();
    finishResize(); rememberScroll();
    dockHeight = null;
    applyHeight();
    persist();
  });
  resizeHandle?.addEventListener('keydown', (event) => {
    if (event.isComposing || !['ArrowUp', 'ArrowDown', 'Home', 'End', 'Escape'].includes(event.key)) return;
    if (event.key === 'Escape') { finishResize(); return; }
    event.preventDefault(); event.stopPropagation();
    expandHistoryForResize();
    const bounds = heightBounds();
    const step = event.shiftKey ? 64 : 24;
    const next = event.key === 'Home' ? bounds.min : event.key === 'End' ? bounds.max
      : dock.getBoundingClientRect().height + (event.key === 'ArrowUp' ? step : -step);
    setHeight(next);
    persist();
  });
  window.addEventListener('blur', () => finishResize());
  window.addEventListener('resize', scheduleHeightUpdate);
  window.visualViewport?.addEventListener('resize', scheduleHeightUpdate);
  window.visualViewport?.addEventListener('scroll', scheduleHeightUpdate);
  if (typeof ResizeObserver === 'function') {
    const observer = new ResizeObserver(scheduleHeightUpdate);
    for (const element of [dock, dock?.querySelector('.chat-dock-header'), dock?.querySelector('.feedback-heading'),
      dock?.querySelector('.composer'), byId('timeline-panel')]) {
      if (element) observer.observe(element);
    }
  }

  byId('chat-collapse')?.addEventListener('click', () => {
    finishResize();
    rememberScroll();
    collapsed = true;
    applyLayout();
    persist();
    launcher?.focus({preventScroll:true});
  });
  launcher?.addEventListener('click', () => open({focus:true}));
  historyToggle?.addEventListener('click', () => {
    finishResize();
    rememberScroll();
    historyCollapsed = !historyCollapsed;
    applyLayout();
    persist();
  });
  latest?.addEventListener('click', scrollToLatest);
  conversation?.addEventListener('scroll', () => {
    if (!historyVisible()) return;
    savedScrollTop = conversation.scrollTop;
    followingLatest = atLatest();
    if (followingLatest) unread = 0;
    updateCounts();
  });
  applyLayout();

  return {
    refresh,
    open,
    beforeConversationAppend() {
      rememberScroll();
      const anchor = historyVisible() && conversation
        ? [...conversation.children].find((item) => item.getBoundingClientRect().bottom > conversation.getBoundingClientRect().top)
        : null;
      return {followLatest:historyVisible() && followingLatest, anchor,
        anchorOffset:anchor ? anchor.getBoundingClientRect().top - conversation.getBoundingClientRect().top : 0};
    },
    conversationAppended(previous, {historical=false}={}) {
      if (previous?.followLatest) scrollToLatest();
      else {
        if (!historical) unread += 1;
        if (historyVisible() && previous?.anchor?.isConnected) {
          conversation.scrollTop += previous.anchor.getBoundingClientRect().top -
            conversation.getBoundingClientRect().top - previous.anchorOffset;
          savedScrollTop = conversation.scrollTop;
        }
        updateCounts();
      }
    }
  };
}

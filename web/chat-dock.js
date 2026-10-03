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
  let launcherStatus = '';
  const visibilityTransitions = new Map();
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

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
      launcher.setAttribute('aria-label', '打开会话' + (launcherStatus ? '，' + launcherStatus : '') +
        (unread ? '，' + unread + ' 条新消息' : ''));
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

  function launcherBounds() {
    // Measure the real button, including unread counts and mobile safe areas.
    // Restoring display in this same task avoids painting a second launcher.
    const hidden = launcher.classList.contains('hidden');
    launcher.classList.remove('hidden');
    const rect = launcher.getBoundingClientRect();
    launcher.classList.toggle('hidden', hidden);
    return rect;
  }

  function setVisible(element, visible, animate) {
    if (!element) return;
    const previous = visibilityTransitions.get(element);
    if (animate && previous?.visible === visible) return;
    const hidden = element.classList.contains('hidden');
    // Capture every animated property before cancelling. Reversals then continue
    // from this exact visible shape, without an old completion hiding the panel.
    const style = getComputedStyle(element);
    const start = {opacity:hidden ? '0' : style.opacity};
    if (element === dock) {
      start.clipPath = style.clipPath;
      start.translate = style.translate === 'none' ? '0px 0px' : style.translate;
    }
    const contents = element === dock ? [dock.querySelector('.chat-dock-header'), byId('chat-dock-body')] : [];
    const contentOpacity = contents.map(child => hidden ? '0' : getComputedStyle(child).opacity);
    previous?.animation?.cancel();
    previous?.contents?.forEach(animation => animation.cancel());
    element.inert = !visible;
    element.setAttribute('aria-hidden', String(!visible));
    element.classList.toggle('is-closing', !visible);
    if (!animate || reducedMotion.matches || typeof element.animate !== 'function') {
      element.classList.toggle('hidden', !visible);
      element.classList.remove('is-transitioning');
      visibilityTransitions.set(element, {visible});
      return;
    }
    element.classList.remove('hidden');
    element.classList.add('is-transitioning');
    let end = {opacity:visible ? '1' : '0'};
    if (element === dock) {
      const rect = dock.getBoundingClientRect();
      const button = launcherBounds();
      const small = {
        clipPath:`inset(${Math.max(0, rect.height - button.height)}px 0px 0px ${Math.max(0, rect.width - button.width)}px round 24px)`,
        translate:`${button.right - rect.right}px ${button.bottom - rect.bottom}px`,
        opacity:'0.35',
      };
      // Leave space around the open shape for its shadow and resize handle.
      const large = {clipPath:'inset(-48px -48px -48px -48px round 24px)', translate:'0px 0px', opacity:'1'};
      if (hidden) Object.assign(start, small);
      else if (start.clipPath === 'none') start.clipPath = large.clipPath;
      end = visible ? large : small;
    }
    const opening = !collapsed;
    const timing = {duration:opening ? 560 : 460, easing:'cubic-bezier(.22, .68, .2, 1)', fill:'both'};
    // Hand the surface from the button to the panel early on opening, and
    // reveal the button near the end of closing instead of showing two panels.
    const frames = element === launcher && !previous?.animation
      ? (visible ? [start, {...start, offset:.65}, end] : [start, {...end, offset:.35}, end])
      : [start, end];
    const animation = element.animate(frames, timing);
    // The panel reveals content at its natural size; only the glass boundary
    // grows. Text never scales, wraps or squeezes during the transition.
    const contentAnimations = contents.map((child, index) => child.animate([
      {opacity:contentOpacity[index]},
      {opacity:visible ? '1' : '0'},
    ], timing));
    visibilityTransitions.set(element, {visible, animation, contents:contentAnimations});
    animation.finished.then(() => {
      if (visibilityTransitions.get(element)?.animation !== animation) return;
      element.classList.toggle('hidden', !visible);
      element.classList.remove('is-transitioning');
      visibilityTransitions.set(element, {visible});
      animation.cancel();
      contentAnimations.forEach(content => content.cancel());
    }, () => { /* A reversed transition continues from its current frame. */ });
  }

  function applyLayout({animate=false}={}) {
    launcher?.setAttribute('aria-expanded', String(!collapsed));
    byId('chat-collapse')?.setAttribute('aria-expanded', String(!collapsed));
    history?.classList.toggle('hidden', historyCollapsed);
    historyToggle?.setAttribute('aria-expanded', String(!historyCollapsed));
    dock?.classList.toggle('history-collapsed', historyCollapsed);
    if (resizeHandle) {
      const enabled = historyVisible();
      resizeHandle.classList.toggle('hidden', !enabled);
      resizeHandle.tabIndex = enabled ? 0 : -1;
      resizeHandle.setAttribute('aria-disabled', String(!enabled));
    }
    const hidden = dock?.classList.contains('hidden');
    if (!collapsed) dock?.classList.remove('hidden');
    applyHeight({restoreScroll:false});
    if (hidden) dock?.classList.add('hidden');
    updateCounts();
    setVisible(dock, !collapsed, animate);
    setVisible(launcher, collapsed, animate);
    if (historyVisible()) {
      // Let the restored history acquire its height before restoring its position.
      requestAnimationFrame(() => {
        if (!historyVisible()) return;
        if (followingLatest) scrollToLatest();
        else if (conversation) conversation.scrollTop = savedScrollTop;
      });
    }
  }

  function open({focus=false, approval=false}={}) {
    if (collapsed) {
      collapsed = false;
      applyLayout({animate:true});
      persist();
    }
    if (approval) {
      requestAnimationFrame(async () => {
        await Promise.allSettled((dock?.getAnimations() || []).map(animation => animation.finished));
        if (collapsed) return;
        const card = byId('chat-approvals')?.querySelector('.approval-card, .queue-card');
        if (card) {
          card.scrollIntoView({block:'nearest'});
          card.focus({preventScroll:true});
        } else if (focus) byId('feedback-note')?.focus({preventScroll:true});
      });
    } else if (focus) byId('feedback-note')?.focus({preventScroll:true});
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
    const pending = state.feedbackTransport === 'mcp_events' ? 0 : (state.approvals || []).length +
      (state.queue || []).filter(item => item?.feedback_id && (['blocked_stale','delivery_uncertain'].includes(item.status) || (item.status === 'failed' && !item.turn_id))).length;
    const waitingForApproval = pending > 0 || state.agent?.status === 'awaiting_approval';
    const approvalPanel = byId('chat-approvals');
    approvalPanel?.classList.toggle('hidden', !pending);
    const count = byId('chat-approval-count');
    if (count) count.textContent = pending > 1 ? pending + ' 项待处理' : '';
    const review = byId('review-pending');
    review?.classList.toggle('hidden', !pending);
    if (review) review.textContent = '到会话中处理待确认操作' + (pending > 1 ? '（' + pending + '）' : '');
    const status = state.agent?.status || 'disconnected';
    const failed = !!state.networkError || state.sessionStatus === 'error' ||
      (state.sessionStatus !== 'connecting' && ['error', 'disconnected', 'delivery_uncertain'].includes(status));
    const label = state.sessionStatus === 'error' ? '连接失败'
      : state.networkError ? '连接中断，正在重试'
      : state.sessionStatus === 'connecting' ? '正在连接'
      : state.sessionStatus !== 'open' ? '会话已结束'
      : waitingForApproval ? '等待你确认'
      : state.submitting ? '正在发送'
      : ({idle:'等待反馈', running:'Codex 正在处理', awaiting_approval:'等待审批',
        waiting:'反馈已排队', disconnected:'Codex 连接中断', delivery_uncertain:'送达待核实',
        error:'Codex 执行出错', waiting_for_mcp:'等待 MCP 读取', external_idle:'等待 MCP 读取'})[status] || '正在连接';
    const compactStatus = byId('chat-status');
    const running = !waitingForApproval && !failed && state.sessionStatus === 'open' && (state.submitting || status === 'running');
    if (compactStatus) {
      compactStatus.textContent = label;
      compactStatus.title = state.networkError || state.agent?.error || byId('agent-status')?.textContent || label;
      compactStatus.classList.toggle('error', failed);
      compactStatus.classList.toggle('running', running);
      compactStatus.classList.toggle('queued', !failed && ['awaiting_approval', 'waiting'].includes(status));
    }
    launcher?.classList.toggle('has-attention', failed || waitingForApproval);
    launcherStatus = label;
    if (launcher) {
      launcher.classList.toggle('is-running', running);
      launcher.classList.toggle('needs-approval', waitingForApproval);
      const launcherLabel = launcher.querySelector('[data-chat-launcher-label]');
      if (launcherLabel) launcherLabel.textContent = waitingForApproval ? '待确认' : '展开会话';
      launcher.setAttribute('aria-busy', String(running));
      launcher.title = label + (unread ? ' · ' + unread + ' 条新消息' : '');
    }
    updateCounts();
  }

  resizeHandle?.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || event.isPrimary === false || resizeDrag || !historyVisible()) return;
    event.preventDefault(); event.stopPropagation();
    rememberScroll();
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
    if (!historyVisible()) return;
    event.preventDefault(); event.stopPropagation();
    finishResize(); rememberScroll();
    dockHeight = null;
    applyHeight();
    persist();
  });
  resizeHandle?.addEventListener('keydown', (event) => {
    if (!historyVisible() || event.isComposing || !['ArrowUp', 'ArrowDown', 'Home', 'End', 'Escape'].includes(event.key)) return;
    if (event.key === 'Escape') { finishResize(); return; }
    event.preventDefault(); event.stopPropagation();
    const bounds = heightBounds();
    const step = event.shiftKey ? 64 : 24;
    const next = event.key === 'Home' ? bounds.min : event.key === 'End' ? bounds.max
      : dock.getBoundingClientRect().height + (event.key === 'ArrowUp' ? step : -step);
    setHeight(next);
    persist();
  });
  window.addEventListener('blur', () => finishResize());
  reducedMotion.addEventListener('change', () => {
    if (reducedMotion.matches) applyLayout();
  });
  function resizeViewport() {
    // A viewport change invalidates the launcher-to-panel path. Settle at the
    // requested state before fitting the new screen instead of drifting outside it.
    if ([dock, launcher].some(element => visibilityTransitions.get(element)?.animation)) applyLayout();
    scheduleHeightUpdate();
  }
  window.addEventListener('resize', resizeViewport);
  window.visualViewport?.addEventListener('resize', resizeViewport);
  window.visualViewport?.addEventListener('scroll', scheduleHeightUpdate);
  if (typeof ResizeObserver === 'function') {
    const observer = new ResizeObserver(scheduleHeightUpdate);
    for (const element of [dock, dock?.querySelector('.chat-dock-header'),
      dock?.querySelector('.composer'), byId('chat-approvals'), byId('timeline-panel')]) {
      if (element) observer.observe(element);
    }
  }

  byId('chat-collapse')?.addEventListener('click', () => {
    finishResize();
    rememberScroll();
    collapsed = true;
    applyLayout({animate:true});
    persist();
    launcher?.focus({preventScroll:true});
  });
  launcher?.addEventListener('click', () => open({focus:true, approval:launcher.classList.contains('needs-approval')}));
  byId('review-pending')?.addEventListener('click', () => {
    document.dispatchEvent(new CustomEvent('workspace-sidebar-close',{detail:{afterClose:()=>open({approval:true})}}));
  });
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

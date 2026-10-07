// One compact, nonmodal surface for references clicked in the composer.
// The native textarea keeps its own focus, selection, editing and IME behavior.
export function createPromptReferencePopover({getInlineHit, closeDelay = 160} = {}) {
  let active = null, leaveTimer = null, destroyed = false;

  function cancelLeave() {
    if (leaveTimer !== null) active?.window.clearTimeout(leaveTimer);
    leaveTimer = null;
  }

  function close() {
    if (!active) return;
    const current = active;
    cancelLeave();
    active = null;
    current.listeners.abort();
    current.observer.disconnect();
    current.element.hidden = true;
    current.anchor.setAttribute('aria-expanded', 'false');
    if (current.controls === null) current.anchor.removeAttribute('aria-controls');
    else current.anchor.setAttribute('aria-controls', current.controls);
    current.element.dispatchEvent(new current.window.Event('close'));
  }

  function leave() {
    if (!active || leaveTimer !== null) return;
    leaveTimer = active.window.setTimeout(close, closeDelay);
  }

  function sameInlineReference(hit, current) {
    const range = current.range;
    return !!hit && hit.start === range.start && hit.end === range.end &&
      hit.entry?.token === current.entry?.token && hit.entry?.alias === current.entry?.alias;
  }

  function onSource(event, current) {
    if (!current.anchor.contains(event.target)) return false;
    if (!current.range) return true;
    if (typeof getInlineHit !== 'function') return false;
    return sameInlineReference(getInlineHit(event.clientX, event.clientY), current);
  }

  function place(current, event) {
    const {element, anchor, window} = current;
    const viewport = window.visualViewport;
    const leftEdge = (viewport?.offsetLeft || 0) + 8;
    const topEdge = (viewport?.offsetTop || 0) + 8;
    const width = viewport?.width || window.innerWidth;
    const height = viewport?.height || window.innerHeight;
    const rightEdge = leftEdge + width - 16;
    const bottomEdge = topEdge + height - 16;
    element.style.maxHeight = `${Math.max(40, Math.min(340, height - 16))}px`;
    element.style.maxWidth = `${Math.max(40, width - 16)}px`;
    const source = anchor.getBoundingClientRect();
    const hasPoint = Number.isFinite(event?.clientX) && Number.isFinite(event?.clientY) && event.detail !== 0;
    let rect = source;
    if (current.range && hasPoint) {
      const halfLine = Math.max(6, Math.min(24, Number.parseFloat(window.getComputedStyle(anchor).lineHeight) / 2 || 10));
      rect = {left: event.clientX, right: event.clientX, top: event.clientY - halfLine, bottom: event.clientY + halfLine};
    }
    const bounds = element.getBoundingClientRect();
    const above = rect.top - topEdge - 7;
    const below = bottomEdge - rect.bottom - 7;
    const top = above >= bounds.height || above > below
      ? rect.top - bounds.height - 7 : rect.bottom + 7;
    element.style.left = `${Math.max(leftEdge, Math.min(rect.left, rightEdge - bounds.width))}px`;
    element.style.top = `${Math.max(topEdge, Math.min(top, bottomEdge - bounds.height))}px`;
  }

  function show(element, {anchor, event, range = null, entry = range?.entry} = {}) {
    if (destroyed) return;
    if (!element?.isConnected || !anchor?.isConnected || element.ownerDocument !== anchor.ownerDocument) {
      throw new TypeError('A reference popover needs connected content and a source in the same document');
    }
    close();
    const document = anchor.ownerDocument, window = document.defaultView;
    const listeners = new window.AbortController();
    const options = {capture: true, signal: listeners.signal};
    const current = {
      element, anchor, window, listeners, range: range && {start: range.start, end: range.end},
      entry: entry && {token: entry.token, alias: entry.alias},
      controls: anchor.getAttribute('aria-controls'),
      value: range ? anchor.value : null,
    };
    active = current;
    element.setAttribute('role', 'dialog');
    element.setAttribute('aria-modal', 'false');
    element.hidden = false;
    anchor.setAttribute('aria-expanded', 'true');
    if (element.id) anchor.setAttribute('aria-controls', element.id);
    place(current, event);

    const move = pointerEvent => {
      if (pointerEvent.pointerType === 'touch') return;
      if (element.contains(pointerEvent.target) || onSource(pointerEvent, current)) cancelLeave();
      else leave();
    };
    const outside = pointerEvent => {
      if (pointerEvent === event || element.contains(pointerEvent.target)) return;
      // Keyboard activation of the source has no meaningful viewport point.
      if (pointerEvent.detail === 0 && !current.range && anchor.contains(pointerEvent.target)) return;
      if (!onSource(pointerEvent, current)) close();
    };
    const input = inputEvent => { if (anchor.contains(inputEvent.target)) close(); };
    document.addEventListener('pointermove', move, options);
    document.addEventListener('pointerout', pointerEvent => {
      if (pointerEvent.pointerType !== 'touch' && !pointerEvent.relatedTarget) leave();
    }, options);
    document.addEventListener('pointerdown', outside, options);
    document.addEventListener('click', outside, options);
    document.addEventListener('keydown', keyEvent => {
      if (keyEvent.key === 'Escape') {
        keyEvent.preventDefault();keyEvent.stopPropagation();close();
      }
    }, options);
    for (const name of ['input', 'change', 'compositionstart']) document.addEventListener(name, input, options);
    document.addEventListener('scroll', scrollEvent => {
      if (!element.contains(scrollEvent.target)) close();
    }, options);
    window.addEventListener('resize', close, options);
    window.addEventListener('blur', blurEvent => {
      // Capturing sees descendant blur too. Moving focus into a preview action
      // must leave the source-bound surface open.
      if (blurEvent.target === window) close();
    }, options);
    viewportEvents(window.visualViewport, 'addEventListener', close, listeners.signal);
    current.observer = new window.MutationObserver(() => {
      if (!anchor.isConnected || !element.isConnected || !anchor.getClientRects().length ||
          (current.range && anchor.value !== current.value)) close();
    });
    current.observer.observe(document.documentElement, {childList: true, subtree: true,
      attributes: true, attributeFilter: ['hidden', 'class', 'style', 'inert']});
    // Decoded image dimensions may arrive after the click; keep the compact
    // surface within the viewport without taking focus or changing its source.
    element.addEventListener('load', () => {
      if (active === current) place(current, event);
    }, options);
  }

  return {
    show,
    close,
    destroy() { close(); destroyed = true; },
  };
}

function viewportEvents(viewport, method, listener, signal) {
  if (!viewport) return;
  for (const name of ['resize', 'scroll']) viewport[method](name, listener, {signal});
}

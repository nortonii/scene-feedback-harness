import { ERASER_RADIUS } from './eraser.js';

// View preferences are separate from feedback evidence and scoped to the session.
export function setupWorkspaceControls({getState}) {
  const byId = id => document.getElementById(id);
  const workspace = document.querySelector('.workspace');
  const divider = byId('workspace-divider');
  const gallery = byId('scene-snapshots');
  const strip = byId('scene-snapshot-strip');
  const toggle = byId('snapshot-strip-toggle');
  const reveal = byId('scene-snapshot-reveal');
  const countLabel = byId('snapshot-strip-count');
  const size = byId('eraser-size');
  const sizeValue = byId('eraser-size-value');
  const mobile = matchMedia('(max-width: 640px)');
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  let sessionId = null, ratio = .5, collapsed = false, diameter = ERASER_RADIUS * 2;
  let drag = null;

  function save() {
    if (!sessionId) return;
    try { localStorage.setItem('astra-visual-workspace:' + sessionId, JSON.stringify({ratio, collapsed, diameter})); }
    catch { /* Controls still work without browser storage. */ }
  }
  function limits() {
    const available = Math.max(1, workspace.clientWidth - divider.offsetWidth);
    const min = Math.max(.2, Math.min(.45, 280 / available));
    return {min, max:1-min, available};
  }
  function applySplit() {
    const {min, max} = limits();
    const visible = clamp(ratio, min, max);
    workspace.style.setProperty('--reference-share', visible + 'fr');
    workspace.style.setProperty('--scene-share', (1-visible) + 'fr');
    divider.setAttribute('aria-valuemin', String(Math.round(min * 100)));
    divider.setAttribute('aria-valuemax', String(Math.round(max * 100)));
    divider.setAttribute('aria-valuenow', String(Math.round(visible * 100)));
    divider.setAttribute('aria-valuetext', `参考图 ${Math.round(visible*100)}%，场景 ${Math.round((1-visible)*100)}%`);
  }
  function applySize() {
    size.value = String(diameter);
    sizeValue.value = diameter + ' px';
    size.setAttribute('aria-valuetext', '直径 ' + diameter + ' 像素');
    const side = diameter + 4, center = side / 2;
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${side}" height="${side}"><circle cx="${center}" cy="${center}" r="${diameter/2}" fill="none" stroke="white" stroke-width="3"/><circle cx="${center}" cy="${center}" r="${diameter/2}" fill="none" stroke="#171715" stroke-width="1"/></svg>`;
    document.body.style.setProperty('--eraser-cursor', `url("data:image/svg+xml,${encodeURIComponent(svg)}") ${center} ${center}, crosshair`);
  }
  function sizeGallery() {
    // Animate the actual visible width, including when the list overflows.
    // A fixed oversized max-width would delay the start/end of the motion.
    const gap = parseFloat(getComputedStyle(strip).columnGap) || 0;
    const cards = [...strip.children];
    const contentWidth = cards.reduce((total, card) => total + card.getBoundingClientRect().width, 0) + Math.max(0, cards.length-1) * gap;
    const available = Math.max(0, gallery.parentElement.clientWidth - 20 - 6 - 28);
    gallery.style.setProperty('--snapshot-strip-width', Math.min(contentWidth, available) + 'px');
  }
  function refresh() {
    const state = getState();
    if (state.sessionId && sessionId !== state.sessionId) {
      sessionId = state.sessionId; ratio = .5; collapsed = false; diameter = ERASER_RADIUS * 2;
      try {
        const stored = JSON.parse(localStorage.getItem('astra-visual-workspace:' + sessionId) || 'null');
        if (Number.isFinite(stored?.ratio)) ratio = clamp(stored.ratio, .2, .8);
        if (typeof stored?.collapsed === 'boolean') collapsed = stored.collapsed;
        if (Number.isFinite(stored?.diameter)) diameter = clamp(Math.round(stored.diameter / 2) * 2, 8, 96);
      } catch { /* Ignore invalid preferences. */ }
      applySplit(); applySize();
    }
    const count = state.sceneSnapshots.length;
    gallery.classList.toggle('hidden', !count);
    gallery.classList.toggle('is-collapsed', collapsed);
    reveal.inert = collapsed || !count;
    reveal.setAttribute('aria-hidden', String(collapsed || !count));
    toggle.setAttribute('aria-expanded', String(!collapsed));
    toggle.setAttribute('aria-label', (collapsed ? '展开' : '收起') + '截图栏，共 ' + count + ' 张');
    toggle.title = (collapsed ? '展开' : '收起') + '截图栏 · ' + count + ' 张';
    countLabel.textContent = String(count);
    sizeGallery();
  }
  toggle.addEventListener('click', () => { collapsed = !collapsed; refresh(); save(); });
  size.addEventListener('input', () => { diameter = Number(size.value); applySize(); save(); });
  divider.addEventListener('pointerdown', event => {
    if (event.button !== 0 || mobile.matches) return;
    event.preventDefault(); divider.focus({preventScroll:true});
    divider.setPointerCapture(event.pointerId);
    drag = {pointerId:event.pointerId, startX:event.clientX, ratio, visible:Number(divider.getAttribute('aria-valuenow')) / 100};
    document.body.classList.add('resizing-workspace');
  });
  divider.addEventListener('pointermove', event => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    const {min,max,available} = limits();
    ratio = clamp(drag.visible + (event.clientX-drag.startX) / available, min, max);
    applySplit();
  });
  function finish(cancel=false) {
    if (!drag) return;
    const previous = drag; drag = null;
    if (cancel) ratio = previous.ratio;
    if (divider.hasPointerCapture(previous.pointerId)) divider.releasePointerCapture(previous.pointerId);
    document.body.classList.remove('resizing-workspace');
    applySplit(); if (!cancel) save();
  }
  divider.addEventListener('pointerup', () => finish());
  divider.addEventListener('pointercancel', () => finish(true));
  divider.addEventListener('lostpointercapture', () => finish(true));
  divider.addEventListener('dblclick', () => { finish(true); ratio = .5; applySplit(); save(); });
  divider.addEventListener('keydown', event => {
    if (mobile.matches) return;
    const {min,max} = limits();
    const step = event.shiftKey ? .1 : .02;
    const current = clamp(ratio,min,max);
    const next = {ArrowLeft:current-step, ArrowRight:current+step, Home:min, End:max, Enter:.5}[event.key];
    if (next === undefined) return;
    event.preventDefault(); ratio = clamp(next,min,max); applySplit(); save();
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') finish(true); });
  window.addEventListener('blur', () => finish(true));
  mobile.addEventListener('change', () => { finish(true); applySplit(); });
  new ResizeObserver(applySplit).observe(workspace);
  new ResizeObserver(sizeGallery).observe(gallery.parentElement);
  applySplit(); applySize(); refresh();
  return {refresh, eraserRadius:() => diameter / 2};
}

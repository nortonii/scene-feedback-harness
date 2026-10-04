import { ERASER_RADIUS } from './eraser.js';

// View preferences are separate from feedback evidence and scoped to the session.
export function setupWorkspaceControls({getState, onLabelsChange=() => {}}) {
  const byId = id => document.getElementById(id);
  const workspace = document.querySelector('.workspace');
  const divider = byId('workspace-divider');
  const labelsToggle = byId('scene-labels-toggle');
  const mobile = matchMedia('(max-width: 640px)');
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const diameter = ERASER_RADIUS * 2;
  let sessionId = null, ratio = .5;
  let sceneLabelsVisible = true;
  let drag = null;

  function save() {
    if (!sessionId) return;
    try { localStorage.setItem('astra-visual-workspace:' + sessionId, JSON.stringify({ratio, sceneLabelsVisible})); }
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
  function applyEraserCursor() {
    const side = diameter + 4, center = side / 2;
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${side}" height="${side}"><circle cx="${center}" cy="${center}" r="${diameter/2}" fill="none" stroke="white" stroke-width="3"/><circle cx="${center}" cy="${center}" r="${diameter/2}" fill="none" stroke="#171715" stroke-width="1"/></svg>`;
    document.body.style.setProperty('--eraser-cursor', `url("data:image/svg+xml,${encodeURIComponent(svg)}") ${center} ${center}, crosshair`);
  }
  function refresh() {
    const state = getState();
    if (state.sessionId && sessionId !== state.sessionId) {
      sessionId = state.sessionId; ratio = .5; sceneLabelsVisible = true;
      try {
        const stored = JSON.parse(localStorage.getItem('astra-visual-workspace:' + sessionId) || 'null');
        if (Number.isFinite(stored?.ratio)) ratio = clamp(stored.ratio, .2, .8);
        if (typeof stored?.sceneLabelsVisible === 'boolean') sceneLabelsVisible = stored.sceneLabelsVisible;
      } catch { /* Ignore invalid preferences. */ }
      applySplit();
    }
    labelsToggle.disabled = state.sceneView !== 'snapshot' || !state.snapshot;
    labelsToggle.setAttribute('aria-pressed', String(sceneLabelsVisible));
    labelsToggle.textContent = sceneLabelsVisible ? '隐藏名称' : '显示名称';
    labelsToggle.title = sceneLabelsVisible ? '仅隐藏截图上的标记名称，标记和引用保留' : '显示截图上的标记名称';
  }
  labelsToggle.addEventListener('click', () => {
    sceneLabelsVisible = !sceneLabelsVisible; refresh(); save(); onLabelsChange();
  });
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
  applySplit(); applyEraserCursor(); refresh();
  return {refresh, eraserRadius:() => diameter / 2, sceneLabelsVisible:() => sceneLabelsVisible};
}

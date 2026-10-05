// The floating tool dock reuses the real controls; scene/evidence state stays in app.js.
export function setupWorkspaceChrome({getState, setMode, activateToolPane, revealReference}) {
  const byId = id => document.getElementById(id);
  const root = document.documentElement;
  const dock = byId('annotation-tool-panel');
  const sceneButton = byId('scene-annotation-toggle');
  const reference = byId('reference-pane');
  const referenceMedia = byId('reference-media');
  const referenceCanvas = byId('reference-annotations');
  const textEditor = byId('text-editor');
  const scene = document.querySelector('.scene-pane');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  let target = 'scene';
  let opened = false;
  let previousMode = 'select';
  let previousView = 'live';
  let animation = null;
  let frame = 0;
  let referenceCloseTimer = null, referenceTouch = false, referenceDismissed = false, suppressReferenceFocus = false;
  const activePointers = new Set();

  function referenceReady() {
    const state = getState();
    return state.workspaceReady && state.activeReferenceId && !reference.inert && !referenceMedia.classList.contains('hidden');
  }
  function gestureActive() {
    const state = getState();
    return activePointers.size || state.drag || state.referencePanning || state.poseEditDrag || state.textPending;
  }
  function inReferenceArea(element) {
    return !!element && (referenceMedia.contains(element) || dock.contains(element) || textEditor.contains(element));
  }
  function referenceAreaActive() {
    const focused = document.activeElement;
    return referenceMedia.matches(':hover') || dock.matches(':hover') || textEditor.matches(':hover') ||
      (focused?.matches(':focus-visible') && inReferenceArea(focused));
  }
  function cancelReferenceClose() { clearTimeout(referenceCloseTimer); referenceCloseTimer = null; }
  function scheduleReferenceClose() {
    cancelReferenceClose();
    if (!opened || target !== 'reference' || referenceTouch) return;
    referenceCloseTimer = setTimeout(() => {
      referenceCloseTimer = null;
      if (!opened || target !== 'reference' || referenceTouch || referenceAreaActive()) return;
      if (gestureActive()) { scheduleReferenceClose(); return; }
      close({resetTool:false});
    },220);
  }
  function openReferenceAutomatically() {
    if (!referenceReady() || gestureActive() || referenceDismissed) return;
    cancelReferenceClose();
    if (!opened || target !== 'reference') open('reference');
  }

  function position() {
    if (!opened) return;
    const immersive = root.dataset.layout === 'immersive';
    const anchor = (target === 'reference' ? reference : byId('scene-stage')).getBoundingClientRect();
    const sceneHead = scene.querySelector('.pane-head').getBoundingClientRect();
    const top = Math.max(12, anchor.top + (target === 'reference' ? 46 : 60), immersive ? sceneHead.bottom + 64 : 0);
    const available = innerHeight - top - 16;
    dock.classList.toggle('is-compact', available < 530);
    const rect = dock.getBoundingClientRect();
    // Reference tools sit alongside the floating card when there is room.
    const outside = target === 'reference' && immersive && anchor.right + rect.width + 20 < innerWidth;
    const left = Math.min(innerWidth - rect.width - 10, Math.max(10, outside ? anchor.right + 10 : anchor.right - rect.width - 10));
    const y = Math.max(10, Math.min(top, innerHeight - rect.height - 12));
    dock.style.left = `${Math.round(left)}px`;
    dock.style.top = `${Math.round(y)}px`;
    for (const details of dock.querySelectorAll('details[open]')) {
      const popup = details.querySelector('.popover-content');
      popup.style.setProperty('--annotation-popover-top', '0px');
      const bounds = popup.getBoundingClientRect();
      const dy = Math.min(0, innerHeight - 12 - bounds.bottom) + Math.max(0, 12 - bounds.top);
      popup.style.setProperty('--annotation-popover-top', `${Math.round(dy)}px`);
    }
  }
  function schedulePosition() {
    if (frame) return;
    frame = requestAnimationFrame(() => { frame = 0; position(); });
  }
  function render() {
    dock.dataset.context = target;
    root.dataset.annotationTarget = opened ? target : '';
    root.dataset.toolsVisible = String(opened);
    dock.inert = !opened;
    dock.setAttribute('aria-hidden', String(!opened));
    const liveScene = target === 'scene' && getState().sceneView === 'live';
    dock.setAttribute('aria-label', target === 'reference' ? '参考图标注工具' : liveScene ? '场景标注工具' : '场景截图标注工具');
    byId('annotation-context-label').textContent = target === 'reference' ? '参考' : liveScene ? '场景' : '截图';
    sceneButton.setAttribute('aria-expanded', String(opened && target === 'scene'));
    byId('scene-labels-toggle').hidden = target !== 'scene';
    byId('pose-edit-tool').hidden = target !== 'reference';
    reference.classList.toggle('is-annotation-target', opened && target === 'reference');
    scene.classList.toggle('is-annotation-target', opened && target === 'scene');
    dock.hidden = !opened;
    position();
  }
  function open(pane, {toggle=false}={}) {
    cancelReferenceClose();
    if (toggle && opened && target === pane) { close({focus:true}); return; }
    if (pane === 'reference' && reference.inert) revealReference?.();
    if (pane !== 'reference') referenceTouch = false;
    const wasOpen = opened;
    target = pane; opened = true;
    activateToolPane?.(pane);
    animation?.cancel(); render();
    if (!wasOpen && !reduced.matches) animation = dock.animate([
      {opacity:0, translate:'6px 0'}, {opacity:1, translate:'0 0'}
    ], {duration:240,easing:'cubic-bezier(.22,1,.36,1)'});
  }
  function close({focus=false, resetTool=true}={}) {
    cancelReferenceClose();
    if (!opened) return;
    const referenceTarget = target === 'reference';
    const trigger = referenceTarget ? referenceCanvas : sceneButton;
    if (referenceTarget) {
      referenceDismissed = referenceMedia.matches(':hover') || dock.matches(':hover');
      referenceTouch = false;
    }
    animation?.cancel();
    opened = false;
    if (resetTool && getState().mode !== 'select') setMode('select',target);
    render();
    if (focus) {
      suppressReferenceFocus = referenceTarget;
      (trigger.hidden ? byId('scene-live-card') : trigger).focus({preventScroll:true});
      suppressReferenceFocus = false;
    }
  }
  function syncState() {
    const state = getState();
    sceneButton.hidden = false;
    sceneButton.title = state.sceneView === 'live' ? '展开场景标注工具，开始绘制时自动固定当前视角' : '展开当前截图的标注工具';
    byId('selection-level-switch').hidden = state.sceneView !== 'live';
    if (opened && target === 'reference' && !referenceReady()) close({resetTool:false});
    const viewChanged = previousView !== state.sceneView;
    const newSnapshot = state.sceneView === 'snapshot' && viewChanged;
    const newTool = state.mode !== 'select' && previousMode !== state.mode;
    previousView = state.sceneView; previousMode = state.mode;
    if (newSnapshot) open('scene');
    else if (newTool && !opened) open(state.toolPane === 'reference' && !reference.inert ? 'reference' : 'scene');
    else if (viewChanged) render();
    schedulePosition();
  }
  function layoutChanged() {
    if (opened && target === 'reference' && reference.inert) close({resetTool:false});
    schedulePosition();
  }
  referenceMedia.addEventListener('pointerenter', event => {
    if (event.pointerType !== 'mouse') return;
    referenceTouch = false; openReferenceAutomatically();
  });
  referenceMedia.addEventListener('pointerleave', event => {
    if (event.pointerType !== 'mouse') return;
    referenceDismissed = false; scheduleReferenceClose();
  });
  referenceMedia.addEventListener('focusin', () => {
    if (suppressReferenceFocus) return;
    referenceDismissed = false; openReferenceAutomatically();
  });
  for (const element of [referenceMedia,dock,textEditor]) {
    if (element !== referenceMedia) {
      element.addEventListener('pointerenter', cancelReferenceClose);
      element.addEventListener('pointerleave', event => { if (event.pointerType === 'mouse') scheduleReferenceClose(); });
    }
    element.addEventListener('focusin', cancelReferenceClose);
    element.addEventListener('focusout', scheduleReferenceClose);
  }
  sceneButton.addEventListener('click', () => open('scene',{toggle:true}));
  byId('annotation-tools-close').addEventListener('click', () => close({focus:true}));
  document.addEventListener('click', event => { if (event.target.closest('#scene-live-card')) close(); });
  // Context follows the actual clicked image, retaining cross-pane annotation.
  document.addEventListener('pointerdown', event => {
    if (opened && target === 'reference' && !inReferenceArea(event.target) && !gestureActive()) close({resetTool:false});
    const pane = event.target.closest('#reference-media') ? 'reference'
      : event.target.closest('#scene-annotations, #viewport canvas') ? 'scene' : null;
    if (!pane) {
      if (dock.contains(event.target)) activePointers.add(event.pointerId);
      return;
    }
    const state = getState();
    if (pane === 'reference') {
      referenceDismissed = false; referenceTouch = event.pointerType !== 'mouse'; openReferenceAutomatically();
    } else if (!(state.sceneView === 'live' && state.paneModes.scene === 'select') &&
        (opened || state.paneModes.scene !== 'select')) open('scene');
    activePointers.add(event.pointerId);
  }, {capture:true});
  for (const type of ['pointerup','pointercancel']) document.addEventListener(type, event => {
    activePointers.delete(event.pointerId); scheduleReferenceClose();
  }, {capture:true});
  window.addEventListener('blur', () => { activePointers.clear(); scheduleReferenceClose(); });
  for (const details of document.querySelectorAll('details.popover')) details.addEventListener('toggle', schedulePosition);
  const pill = byId('session-pill');
  const syncStatus = () => { pill.title = pill.textContent; pill.setAttribute('aria-label', pill.textContent); };
  new MutationObserver(syncStatus).observe(pill,{childList:true,characterData:true,subtree:true});
  syncStatus();
  const observer = new ResizeObserver(schedulePosition);
  for (const element of [reference,byId('scene-stage'),dock]) observer.observe(element);
  window.addEventListener('resize', schedulePosition);
  window.addEventListener('scroll', schedulePosition, {passive:true});
  render(); syncState();
  return {open, close, syncState, layoutChanged};
}

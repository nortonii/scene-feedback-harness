// The floating tool dock reuses the real controls; scene/evidence state stays in app.js.
export function setupWorkspaceChrome({getState, setMode, revealReference}) {
  const byId = id => document.getElementById(id);
  const root = document.documentElement;
  const dock = byId('annotation-tool-panel');
  const refButton = byId('annotate-reference-button');
  const sceneButton = byId('scene-annotation-toggle');
  const reference = byId('reference-pane');
  const scene = document.querySelector('.scene-pane');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  let target = 'scene';
  let opened = false;
  let previousMode = 'select';
  let previousView = 'live';
  let animation = null;
  let frame = 0;

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
  function positionMenus() {
    const safeTop = Math.max(10,document.querySelector('.topbar').getBoundingClientRect().bottom+8);
    for (const details of document.querySelectorAll('.view-popover[open], #compare-panel[open]')) {
      const popup = details.querySelector('.popover-content');
      if (root.dataset.layout !== 'compare' || innerWidth > 640) continue;
      const anchor = details.querySelector('summary').getBoundingClientRect();
      const above = Math.max(0,anchor.top-safeTop-8), below = Math.max(0,innerHeight-anchor.bottom-18);
      const useAbove = above > below;
      popup.style.setProperty('--workspace-menu-height', `${Math.max(80,useAbove ? above : below)}px`);
      const height = popup.getBoundingClientRect().height;
      const top = useAbove ? anchor.top-height-8 : anchor.bottom+8;
      popup.style.setProperty('--workspace-menu-top', `${Math.max(safeTop,Math.min(innerHeight-height-10,top))}px`);
    }
    for (const popup of document.querySelectorAll('.import-popover[open] > .popover-content')) {
      popup.style.setProperty('--panel-max-height', `${Math.max(80,innerHeight-safeTop-10)}px`);
      popup.style.setProperty('--panel-shift-x','0px');
      popup.style.setProperty('--panel-shift-y','0px');
      const rect = popup.getBoundingClientRect();
      popup.style.setProperty('--panel-shift-x', `${Math.min(0,innerWidth-10-rect.right)+Math.max(0,10-rect.left)}px`);
      popup.style.setProperty('--panel-shift-y', `${Math.min(0,innerHeight-10-rect.bottom)+Math.max(0,safeTop-rect.top)}px`);
    }
  }
  function schedulePosition() {
    if (frame) return;
    frame = requestAnimationFrame(() => { frame = 0; position(); positionMenus(); });
  }
  function render() {
    dock.dataset.context = target;
    root.dataset.annotationTarget = opened ? target : '';
    root.dataset.toolsVisible = String(opened);
    dock.inert = !opened;
    dock.setAttribute('aria-hidden', String(!opened));
    dock.setAttribute('aria-label', target === 'reference' ? '参考图标注工具' : '场景截图标注工具');
    byId('annotation-context-label').textContent = target === 'reference' ? '参考' : '截图';
    refButton.setAttribute('aria-expanded', String(opened && target === 'reference'));
    sceneButton.setAttribute('aria-expanded', String(opened && target === 'scene'));
    byId('scene-labels-toggle').hidden = target !== 'scene';
    reference.classList.toggle('is-annotation-target', opened && target === 'reference');
    scene.classList.toggle('is-annotation-target', opened && target === 'scene');
    dock.hidden = !opened;
    position();
  }
  function open(pane, {toggle=false}={}) {
    if (toggle && opened && target === pane) { close({focus:true}); return; }
    if (pane === 'reference' && reference.inert) revealReference?.();
    const wasOpen = opened;
    target = pane; opened = true;
    animation?.cancel(); render();
    if (!wasOpen && !reduced.matches) animation = dock.animate([
      {opacity:0, translate:'6px 0'}, {opacity:1, translate:'0 0'}
    ], {duration:240,easing:'cubic-bezier(.22,1,.36,1)'});
  }
  function close({focus=false, resetTool=true}={}) {
    if (!opened) return;
    const trigger = target === 'reference' ? refButton : sceneButton;
    animation?.cancel();
    opened = false;
    if (resetTool && getState().mode !== 'select') setMode('select');
    render();
    if (focus) (trigger.hidden ? byId('browse-button') : trigger).focus({preventScroll:true});
  }
  function syncState() {
    const state = getState();
    sceneButton.hidden = state.sceneView !== 'snapshot';
    document.querySelector('.selection-popover').hidden = state.sceneView !== 'live';
    byId('selection-level-label').textContent = state.selectionLevel === 'part' ? '部件' : '物体';
    document.querySelector('.selection-popover > summary').setAttribute('aria-label', `选择层级：${state.selectionLevel === 'part' ? '部件' : '物体'}`);
    refButton.disabled = !state.workspaceReady || !state.activeReferenceId;
    const newSnapshot = state.sceneView === 'snapshot' && previousView !== state.sceneView;
    const newTool = state.mode !== 'select' && previousMode !== state.mode;
    previousView = state.sceneView; previousMode = state.mode;
    if (newSnapshot) open('scene');
    else if (newTool && !opened) open(target === 'reference' && !reference.inert ? 'reference' : 'scene');
    schedulePosition();
  }
  function layoutChanged() {
    if (opened && target === 'reference' && reference.inert) close();
    schedulePosition();
  }
  refButton.addEventListener('click', () => open('reference',{toggle:true}));
  sceneButton.addEventListener('click', () => open('scene',{toggle:true}));
  byId('annotation-tools-close').addEventListener('click', () => close({focus:true}));
  byId('browse-button').addEventListener('click', () => close());
  // Context follows the actual clicked image, retaining cross-pane annotation.
  document.addEventListener('pointerdown', event => {
    const pane = event.target.closest('#reference-annotations') ? 'reference'
      : event.target.closest('#scene-annotations, #viewport canvas') ? 'scene' : null;
    if (pane && (opened || getState().mode !== 'select')) {
      if (pane === 'scene' && getState().sceneView === 'live' && getState().mode === 'select') close();
      else open(pane);
    }
  }, {capture:true});
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

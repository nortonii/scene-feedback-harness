// Layout preferences never alter the scene camera, snapshots or feedback evidence.
export function setupImmersive({onResize=() => {}, getState=() => ({})}={}) {
  const root = document.documentElement;
  const toggle = document.getElementById('immersive-toggle');
  const referenceToggle = document.getElementById('immersive-reference-toggle');
  const reference = document.querySelector('.reference-pane');
  const toolsToggle = document.getElementById('immersive-tools-toggle');
  const toolPanel = document.querySelector('.tool-panel');
  let toolsVisible = false;
  let previousMode = getState().mode;
  let previousView = getState().sceneView;
  const stage = document.getElementById('scene-stage');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  let immersive = root.dataset.layout === 'immersive';
  let referenceVisible = false;
  let transition = null;
  let desiredLayout = immersive;
  let layoutRequest = 0;
  let fallback = null;
  let referenceAnimation = null;
  let measuring = false;

  function measure() {
    if (!immersive) return;
    const header = document.querySelector('.topbar').getBoundingClientRect();
    root.style.setProperty('--immersive-tools-top', `${header.bottom + 10}px`);
    const tools = document.querySelector('.tool-panel').getBoundingClientRect();
    const sceneTop = header.bottom + 10 + (toolsVisible ? tools.height + 10 : 0);
    root.style.setProperty('--immersive-scene-top', `${sceneTop}px`);
    const sceneHead = document.querySelector('.scene-pane > .pane-head').getBoundingClientRect();
    root.style.setProperty('--immersive-content-top', `${sceneTop + sceneHead.height + 12}px`);
    const timeline = document.getElementById('timeline-panel');
    const bottom = timeline.classList.contains('hidden') ? 18 : innerHeight - timeline.getBoundingClientRect().top + 12;
    root.style.setProperty('--immersive-bottom', `${bottom}px`);
  }
  function refresh() {
    root.dataset.layout = immersive ? 'immersive' : 'compare';
    root.dataset.referenceVisible = String(referenceVisible);
    root.dataset.toolsVisible = String(toolsVisible);
    toolsToggle.hidden = !immersive;
    toolsToggle.setAttribute('aria-expanded', String(toolsVisible));
    toolsToggle.title = toolsVisible ? '收起标注工具' : '展开标注工具';
    toolPanel.inert = immersive && !toolsVisible;
    toolPanel.setAttribute('aria-hidden', String(toolPanel.inert));
    toggle.setAttribute('aria-pressed', String(immersive));
    toggle.title = immersive ? '返回参考图与场景双栏对照' : '让场景铺满整个窗口';
    toggle.setAttribute('aria-label', immersive ? '退出沉浸视图' : '进入沉浸视图');
    toggle.querySelector('[data-immersive-label]').textContent = immersive ? '对照' : '沉浸';
    referenceToggle.hidden = !immersive;
    referenceToggle.setAttribute('aria-expanded', String(referenceVisible));
    referenceToggle.title = referenceVisible ? '收起浮动参考图' : '展开浮动参考图';
    reference.inert = immersive && !referenceVisible;
    reference.setAttribute('aria-hidden', String(reference.inert));
    measure();
    onResize();
    window.dispatchEvent(new Event('resize'));
  }
  function setReference(value, {focus=false}={}) {
    const from = getComputedStyle(reference).opacity;
    referenceAnimation?.cancel();
    referenceVisible = value;
    if (value) toolsVisible = true;
    refresh();
    if (immersive && !reduced.matches) {
      referenceAnimation = reference.animate([
        {opacity:from, transform:value ? 'translateY(12px) scale(.975)' : 'none'},
        {opacity:value ? 1 : 0, transform:value ? 'none' : 'translateY(8px) scale(.985)'}
      ], {duration:320, easing:'cubic-bezier(.22,1,.36,1)'});
    }
    if (focus || (!value && reference.contains(document.activeElement))) referenceToggle.focus({preventScroll:true});
  }
  function setLayout(value) {
    if (immersive === value && !transition) return;
    const request = ++layoutRequest;
    desiredLayout = value;
    transition?.skipTransition(); fallback?.cancel(); referenceAnimation?.cancel();
    const before = stage.getBoundingClientRect();
    const update = () => {
      if (request !== layoutRequest) return;
      immersive = value;
      if (value && (getState().sceneView === 'snapshot' || getState().mode !== 'select')) toolsVisible = true;
      // Keep focus outside content that becomes inert.
      if (immersive && reference.contains(document.activeElement)) toggle.focus({preventScroll:true});
      refresh();
      try { localStorage.setItem('astra-workspace-layout', value ? 'immersive' : 'compare'); } catch { /* Optional storage. */ }
    };
    if (!reduced.matches && document.startViewTransition) {
      const current = document.startViewTransition(update);
      transition = current;
      current.finished.catch(() => {}).finally(() => { if (transition === current) transition = null; });
    } else {
      update();
      if (!reduced.matches) {
        const after = stage.getBoundingClientRect();
        fallback = stage.animate([
          {transformOrigin:'0 0', transform:`translate(${before.x-after.x}px,${before.y-after.y}px) scale(${before.width/after.width},${before.height/after.height})`},
          {transformOrigin:'0 0', transform:'none'}
        ], {duration:500, easing:'cubic-bezier(.22,1,.36,1)'});
      }
    }
  }
  function syncState() {
    const {mode, sceneView} = getState();
    const needsTools = (sceneView === 'snapshot' && previousView !== sceneView) || (mode !== 'select' && previousMode !== mode);
    previousMode = mode; previousView = sceneView;
    if (immersive && needsTools && !toolsVisible) { toolsVisible = true; refresh(); }
  }
  toolsToggle.addEventListener('click', () => {
    toolsVisible = !toolsVisible;
    for (const details of toolPanel.querySelectorAll('details[open]')) details.open = false;
    refresh();
  });
  toggle.addEventListener('click', () => setLayout(!desiredLayout));
  referenceToggle.addEventListener('click', () => setReference(!referenceVisible));
  document.getElementById('immersive-reference-close').addEventListener('click', () => setReference(false, {focus:true}));
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || event.defaultPrevented || !immersive || !referenceVisible ||
        document.querySelector('dialog[open], details.popover[open]') ||
        event.target.closest('input, textarea, select, [contenteditable]')) return;
    setReference(false, {focus:true});
  }, {capture:true});
  const observer = new ResizeObserver(() => {
    if (measuring) return;
    measuring = true;
    requestAnimationFrame(() => { measuring = false; measure(); });
  });
  for (const selector of ['.topbar','.tool-panel','.scene-pane > .pane-head','#timeline-panel']) observer.observe(document.querySelector(selector));
  window.addEventListener('resize', measure);
  refresh();
  return {refresh, setLayout, syncState};
}

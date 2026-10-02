// A movable reference window changes only the workspace, never image coordinates.
export function setupReferenceWindow({onChange=()=>{}}={}) {
  const root = document.documentElement;
  const pane = document.getElementById('reference-pane');
  const header = pane.querySelector('.pane-head');
  const reset = document.getElementById('reference-window-reset');
  const storageKey = 'astra-reference-window:v1';
  let preferred = null;
  let gesture = null;
  let frame = 0;
  const immersive = () => root.dataset.layout === 'immersive';
  try {
    const value = JSON.parse(localStorage.getItem(storageKey));
    if (value && ['left','top','width','height'].every(key => Number.isFinite(value[key]))) preferred = value;
  } catch { /* Optional browser preference. */ }
  function limits() {
    const margin = 10;
    const top = Math.min(innerHeight - 100, document.querySelector('.topbar').getBoundingClientRect().bottom + 10);
    const maxWidth = Math.max(100, innerWidth - margin * 2);
    const maxHeight = Math.max(80, innerHeight - top - margin);
    const px = value => parseFloat(value) || 0;
    const style = getComputedStyle(pane);
    let chromeHeight = px(style.paddingTop)+px(style.paddingBottom)+px(style.borderTopWidth)+px(style.borderBottomWidth);
    for (const child of pane.children) {
      if (child.id === 'reference-stage' || child.classList.contains('reference-window-resize')) continue;
      const css = getComputedStyle(child);
      if (css.display === 'none' || ['absolute','fixed'].includes(css.position)) continue;
      chromeHeight += child.getBoundingClientRect().height+px(css.marginTop)+px(css.marginBottom);
    }
    return {left:margin, top:Math.max(margin,top), right:innerWidth-margin, bottom:innerHeight-margin,
      minWidth:Math.min(280,maxWidth), minHeight:Math.min(Math.max(220,chromeHeight+90),maxHeight), maxWidth, maxHeight};
  }
  function constrain(rect) {
    const b = limits();
    const width = Math.max(b.minWidth,Math.min(b.maxWidth,rect.width));
    const height = Math.max(b.minHeight,Math.min(b.maxHeight,rect.height));
    return {width,height,left:Math.max(b.left,Math.min(b.right-width,rect.left)),top:Math.max(b.top,Math.min(b.bottom-height,rect.top))};
  }
  function notify() {
    if (!frame) frame = requestAnimationFrame(() => { frame = 0; onChange(); });
  }
  function apply(rect) {
    pane.dataset.referenceCustom = 'true';
    for (const key of ['left','top','width','height']) pane.style.setProperty(`--reference-window-${key}`, `${rect[key]}px`);
    notify();
  }
  function save() {
    try {
      if (preferred) localStorage.setItem(storageKey,JSON.stringify(preferred));
      else localStorage.removeItem(storageKey);
    } catch { /* Layout works without storage. */ }
  }
  function refresh() {
    header.tabIndex = immersive() ? 0 : -1;
    header.title = immersive() ? '拖动移动参考窗 · 方向键微调位置' : '';
    if (immersive() && preferred && !gesture) apply(constrain(preferred));
  }
  function rectangle() {
    const {left,top,width,height} = pane.getBoundingClientRect();
    return {left,top,width,height};
  }
  function resize(start, direction, dx, dy) {
    const b=limits();
    let left=start.left, top=start.top, right=left+start.width, bottom=top+start.height;
    if (direction.includes('e')) right=Math.max(left+b.minWidth,Math.min(b.right,right+dx));
    if (direction.includes('s')) bottom=Math.max(top+b.minHeight,Math.min(b.bottom,bottom+dy));
    if (direction.includes('w')) left=Math.min(right-b.minWidth,Math.max(b.left,left+dx));
    if (direction.includes('n')) top=Math.min(bottom-b.minHeight,Math.max(b.top,top+dy));
    return constrain({left,top,width:right-left,height:bottom-top});
  }
  pane.addEventListener('pointerdown', event => {
    if (!immersive() || pane.inert || event.button !== 0 || gesture) return;
    const handle=event.target.closest('[data-reference-resize]');
    if (!handle && (!header.contains(event.target) || event.target.closest('button,summary,input,select,a,label,.popover-content'))) return;
    event.preventDefault();
    const start=rectangle();
    gesture={id:event.pointerId,x:event.clientX,y:event.clientY,start,previous:preferred,direction:handle?.dataset.referenceResize || ''};
    pane.dataset.referenceGesture=handle ? 'resize' : 'move';
    apply(start);
    pane.setPointerCapture(event.pointerId);
    (handle?.hasAttribute('tabindex') ? handle : header).focus({preventScroll:true});
  });
  pane.addEventListener('pointermove', event => {
    if (gesture?.id !== event.pointerId) return;
    const dx=event.clientX-gesture.x,dy=event.clientY-gesture.y;
    const next=gesture.direction ? resize(gesture.start,gesture.direction,dx,dy)
      : constrain({...gesture.start,left:gesture.start.left+dx,top:gesture.start.top+dy});
    apply(next);
  });
  function finish(cancel=false) {
    if (!gesture) return;
    const previous=gesture.previous;
    const id=gesture.id;
    preferred=cancel ? previous : rectangle();
    gesture=null;
    delete pane.dataset.referenceGesture;
    if (pane.hasPointerCapture(id)) pane.releasePointerCapture(id);
    if (!preferred) delete pane.dataset.referenceCustom;
    refresh(); save(); notify();
  }
  pane.addEventListener('pointerup', event => { if (gesture?.id === event.pointerId) finish(); });
  pane.addEventListener('pointercancel', () => finish(true));
  pane.addEventListener('lostpointercapture', () => finish(true));
  pane.addEventListener('keydown', event => {
    if (!immersive()) return;
    if (event.key === 'Escape' && gesture) { event.preventDefault(); event.stopPropagation(); finish(true); return; }
    const handle=event.target.closest('[data-reference-resize]');
    if (event.target !== header && !handle) return;
    if (!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)) return;
    event.preventDefault(); event.stopPropagation();
    const step=event.shiftKey ? 24 : 8;
    const dx=event.key==='ArrowLeft' ? -step : event.key==='ArrowRight' ? step : 0;
    const dy=event.key==='ArrowUp' ? -step : event.key==='ArrowDown' ? step : 0;
    const start=rectangle();
    preferred=handle ? resize(start,handle.dataset.referenceResize,dx,dy) : constrain({...start,left:start.left+dx,top:start.top+dy});
    apply(preferred); save();
  });
  reset.addEventListener('click', () => {
    finish(true); preferred=null; delete pane.dataset.referenceCustom; save(); notify();
  });
  window.addEventListener('resize', () => { if (gesture) finish(true); refresh(); });
  // Video controls, saved moments and pose editors may appear after resizing.
  const contents = new ResizeObserver(() => refresh());
  for (const child of [header,pane.querySelector('.reference-navigation'),document.getElementById('pose-edit-panel'),document.getElementById('timeline-panel')]) {
    if (child) contents.observe(child);
  }
  refresh();
  return {refresh};
}

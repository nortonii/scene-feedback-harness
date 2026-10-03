// Animate real heights together, without scaling text or recreating the panels.
export function createChatSectionMotion({dock,panels,onFinish}) {
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  let running=null;
  const measure=element=>({height:element.getBoundingClientRect().height,
    opacity:getComputedStyle(element).opacity,marginBottom:getComputedStyle(element).marginBottom});
  function finish() {
    const previous=running;if(!previous)return;
    running=null;
    for(const animation of previous.animations) animation.cancel();
    for(const [element,hidden] of previous.hidden) {
      element.classList.toggle('hidden',hidden);
      element.style.removeProperty('flex');element.style.removeProperty('overflow');
    }
    dock.classList.remove('sections-animating');onFinish?.();
  }
  function run(change) {
    const fromDock=measure(dock),from=panels.map(measure);
    // Measure before cancellation so a quick reversal starts at the visible
    // intermediate height, rather than jumping to the previous destination.
    finish();change();
    if(reduced.matches || dock.classList.contains('hidden') || !dock.animate) {onFinish?.();return;}
    const targetDock=measure(dock),to=panels.map(measure);
    const hidden=panels.map(element=>[element,element.classList.contains('hidden')]);
    if(Math.abs(targetDock.height-fromDock.height)<.5 && from.every((item,i)=>Math.abs(item.height-to[i].height)<.5)) {onFinish?.();return;}
    const timing={duration:420,easing:'cubic-bezier(.22,.68,.2,1)',fill:'both'};
    dock.classList.add('sections-animating');
    const animations=[dock.animate([{height:fromDock.height+'px'},{height:targetDock.height+'px'}],timing)];
    panels.forEach((element,index)=>{
      const start=from[index],end=to[index];
      if(!start.height && !end.height)return;
      element.classList.remove('hidden');element.style.flex='0 0 auto';element.style.overflow='hidden';
      animations.push(element.animate([
        {height:start.height+'px',opacity:start.height?start.opacity:0,marginBottom:start.height?start.marginBottom:'0px'},
        {height:end.height+'px',opacity:end.height?1:0,marginBottom:end.height?end.marginBottom:'0px'}
      ],timing));
    });
    const current={animations,hidden};running=current;
    Promise.all(animations.map(animation=>animation.finished)).then(()=>{if(running===current)finish();}).catch(()=>{});
  }
  reduced.addEventListener('change',()=>{if(reduced.matches)finish();});
  return {run,finish,get active(){return !!running;}};
}

export function setupChatPanelSizing({getState,animateChange,beforeResize,onResize}) {
  const byId=id=>document.getElementById(id),dock=byId('chat-dock'),note=byId('feedback-note');
  const evidence=byId('feedback-evidence'),toggle=byId('feedback-evidence-summary');
  const scroll=byId('feedback-evidence-scroll');
  let sessionId=null,opened=false,noteHeight=44,evidenceHeight=180,drag=null;
  const controls=[{handle:byId('prompt-resize-handle'),element:note,key:'note',default:44,min:44},
    {handle:byId('feedback-evidence-resize'),element:scroll,key:'evidence',default:180,min:96}];
  const key=()=> 'astra-chat-panels:'+sessionId;
  function max(control) {
    const viewport=window.visualViewport?.height || innerHeight;
    const budget=Math.min(parseFloat(getComputedStyle(dock).maxHeight) || viewport,viewport-24);
    const bodyStyle=getComputedStyle(byId('chat-dock-body'));
    const fixed=byId('chat-dock').querySelector('.chat-dock-header').getBoundingClientRect().height+
      (parseFloat(bodyStyle.paddingTop)||0)+(parseFloat(bodyStyle.paddingBottom)||0)+
      byId('chat-approvals').getBoundingClientRect().height+byId('prompt-image-refs').getBoundingClientRect().height+
      dock.querySelector('.prompt-input-actions').getBoundingClientRect().height+4+
      (byId('chat-history').classList.contains('hidden')?0:70);
    const other=control.key==='note'?(opened?scroll.getBoundingClientRect().height+22:0):note.getBoundingClientRect().height+22;
    return Math.max(control.min,Math.min(control.key==='note'?300:400,viewport*(control.key==='note' ? 0.38 : 0.46),budget-fixed-other));
  }
  function persist(){if(sessionId)try{localStorage.setItem(key(),JSON.stringify({noteHeight,evidenceHeight,evidenceOpen:opened}));}catch{}}
  function apply() {
    for(const control of controls) {
      const preferred=control.key==='note'?noteHeight:evidenceHeight;
      const height=Math.round(Math.max(control.min,Math.min(max(control),preferred)));
      control.element.style.height=height+'px';
      for(const [name,value] of Object.entries({'aria-valuemin':control.min,'aria-valuemax':Math.round(max(control)),'aria-valuenow':height,'aria-valuetext':height+' 像素'})) control.handle.setAttribute(name,String(value));
    }
    onResize?.();
  }
  function setOpen(value) {
    opened=value;evidence.classList.toggle('hidden',!opened);evidence.inert=!opened;
    evidence.setAttribute('aria-hidden',String(!opened));toggle.setAttribute('aria-expanded',String(opened));
    toggle.title=opened?'收起本次反馈':'展开本次反馈';
    evidence.dispatchEvent(new CustomEvent('evidence-visibility',{detail:{open:opened}}));
  }
  function finishResize() {
    if(!drag)return;
    const {control,pointerId}=drag;drag=null;
    control.handle.classList.remove('is-resizing');dock.classList.remove('panel-resizing');
    if(control.handle.hasPointerCapture(pointerId))control.handle.releasePointerCapture(pointerId);
    persist();
  }
  function setHeight(control,value) {
    const height=Math.round(Math.max(control.min,Math.min(max(control),value)));
    if(control.key==='note')noteHeight=height;else evidenceHeight=height;
    apply();
  }
  for(const control of controls) {
    const enabled=()=>!dock.inert && !dock.classList.contains('hidden') && (control.key!=='evidence' || opened);
    control.handle.addEventListener('pointerdown',event=>{
      if(!enabled() || event.button!==0 || event.isPrimary===false || drag)return;
      event.preventDefault();event.stopPropagation();beforeResize?.();
      control.handle.focus({preventScroll:true});
      drag={control,pointerId:event.pointerId,y:event.clientY,height:control.element.getBoundingClientRect().height};
      control.handle.classList.add('is-resizing');dock.classList.add('panel-resizing');
      try{control.handle.setPointerCapture(event.pointerId);}catch{finishResize();}
    });
    control.handle.addEventListener('pointermove',event=>{
      if(drag?.control!==control || drag.pointerId!==event.pointerId)return;
      if(event.pointerType==='mouse' && !event.buttons){finishResize();return;}
      event.preventDefault();setHeight(control,drag.height+drag.y-event.clientY);
    });
    for(const event of ['pointerup','pointercancel','lostpointercapture'])control.handle.addEventListener(event,e=>{if(drag?.pointerId===e.pointerId)finishResize();});
    control.handle.addEventListener('dblclick',event=>{if(!enabled())return;event.preventDefault();beforeResize?.();setHeight(control,control.default);persist();});
    control.handle.addEventListener('keydown',event=>{
      if(!enabled() || event.isComposing)return;
      if(event.key==='Escape'){finishResize();return;}
      if(!['ArrowUp','ArrowDown','Home','End'].includes(event.key))return;
      event.preventDefault();event.stopPropagation();beforeResize?.();
      const current=control.element.getBoundingClientRect().height,step=event.shiftKey?48:16;
      setHeight(control,event.key==='Home'?control.min:event.key==='End'?max(control):current+(event.key==='ArrowUp'?step:-step));persist();
    });
  }
  toggle.addEventListener('click',()=>{
    finishResize();animateChange(()=>{setOpen(!opened);apply();});persist();
  });
  window.addEventListener('blur',finishResize);
  window.addEventListener('resize',()=>{beforeResize?.();apply();});
  window.visualViewport?.addEventListener('resize',()=>{beforeResize?.();apply();});
  setOpen(false);apply();
  return {finishResize,fit:apply,refresh(){
    const current=getState()?.sessionId;if(!current || current===sessionId)return;
    finishResize();sessionId=current;noteHeight=44;evidenceHeight=180;opened=false;
    try {
      const saved=JSON.parse(localStorage.getItem(key()) || '{}');
      if(Number.isFinite(saved.noteHeight) && saved.noteHeight>=44)noteHeight=Math.min(300,saved.noteHeight);
      if(Number.isFinite(saved.evidenceHeight) && saved.evidenceHeight>=96)evidenceHeight=Math.min(400,saved.evidenceHeight);
      opened=saved.evidenceOpen===true;
    }catch{}
    setOpen(opened);apply();
  }};
}

// A single modal drawer; changing its contents never resizes the scene canvas.
export function setupWorkspaceSidebar({onOpen=()=>{},onPage=()=>{}}={}) {
  const dialog=document.getElementById('projects-dialog'),panel=dialog.querySelector('.sidebar-panel'),shade=dialog.querySelector('.sidebar-shade');
  const launcher=document.getElementById('projects-dialog-button'),closeButton=document.getElementById('close-projects');
  const pages=[...dialog.querySelectorAll('[data-sidebar-view]')];
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  let targetOpen=false,page='home',motion=null,veil=null,pageMotion=null,epoch=0;
  let previousFocus=null,completion=Promise.resolve(true);
  const duration=440;
  const scrollPositions=new Map();
  function showPage(value,{focus=true}={}) {
    if(!pages.some(node=>node.dataset.sidebarView===value)) return;
    const previous=pages.find(node=>node.dataset.sidebarView===page);
    if(previous && !previous.hidden) scrollPositions.set(page,(previous.querySelector('.sidebar-page-body') || previous).scrollTop);
    pageMotion?.cancel();page=value;dialog.dataset.page=value;
    for(const node of pages) {node.hidden=node.dataset.sidebarView!==value;node.inert=node.hidden;}
    const current=pages.find(node=>node.dataset.sidebarView===value);
    if(value==='create') document.getElementById('create-project-panel').open=true;
    (current.querySelector('.sidebar-page-body') || current).scrollTop=scrollPositions.get(value) || 0;
    onPage(value);
    if(dialog.open && !reduced.matches) pageMotion=current.animate([{opacity:.25,translate:'-8px 0'},{opacity:1,translate:'0 0'}],{duration:180,easing:'cubic-bezier(.22,1,.36,1)'});
    if(focus && dialog.open) (current.querySelector('[data-sidebar-home]') || closeButton).focus({preventScroll:true});
  }
  function setOpen(value,{restoreFocus=true}={}) {
    if(value===targetOpen && dialog.open) return completion;
    const ticket=++epoch;
    targetOpen=value;
    const fresh=!dialog.open;
    if(fresh && !value) return Promise.resolve(true);
    if(fresh) {
      previousFocus=document.activeElement;
      // Set the closed appearance before showModal so there is no exposed first frame.
      motion?.cancel();veil?.cancel();
      const timing={duration,easing:'cubic-bezier(.4,0,.2,1)',fill:'both'};
      motion=panel.animate([{transform:'translate3d(calc(-100% - 24px),0,0)'},{transform:'translate3d(0,0,0)'}],timing);
      veil=shade.animate([{opacity:0},{opacity:1}],timing);
      motion.pause();veil.pause();motion.currentTime=0;veil.currentTime=0;
      dialog.showModal();
    }
    launcher.setAttribute('aria-expanded',String(value));
    dialog.dataset.phase=value?'opening':'closing';
    if(value) closeButton.focus({preventScroll:true});
    // Reuse the timeline in both directions: repeated requests never restart easing.
    for(const animation of [motion,veil]) {
      animation.updatePlaybackRate(value?1:-1);
      if(reduced.matches) animation.currentTime=value?duration:0;
      animation.play();
      if(reduced.matches) animation.finish();
    }
    completion=(async()=>{
      try {await motion.finished;} catch {return false;}
      if(ticket!==epoch) return false;
      dialog.dataset.phase=value?'open':'closed';
      if(!value) {
        dialog.close();motion.cancel();veil.cancel();
        if(restoreFocus) (previousFocus?.isConnected && !previousFocus.disabled ? previousFocus : launcher).focus({preventScroll:true});
      }
      return true;
    })();
    // Fetch after the drawer's initial paint, rather than competing with its first frame.
    if(fresh) requestAnimationFrame(()=>requestAnimationFrame(()=>{if(dialog.open && targetOpen) onOpen();}));
    return completion;
  }
  function open(value=page) {showPage(value,{focus:false});return setOpen(true);}
  function close(options) {return setOpen(false,options);}
  closeButton.addEventListener('click',()=>setOpen(!targetOpen));
  shade.addEventListener('click',()=>close());
  dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
  dialog.addEventListener('keydown',event=>{
    if(event.key!=='Tab') return;
    const stops=[...dialog.querySelectorAll('button,input,select,textarea,summary,a[href],[tabindex="0"]')]
      .filter(node=>!node.disabled && !node.closest('[inert]') && node.getClientRects().length && getComputedStyle(node).visibility!=='hidden');
    const first=stops[0],last=stops.at(-1);
    if(!first) {event.preventDefault();return;}
    if(event.shiftKey && (document.activeElement===first || !stops.includes(document.activeElement))) {event.preventDefault();last.focus();}
    else if(!event.shiftKey && document.activeElement===last) {event.preventDefault();first.focus();}
  });
  dialog.addEventListener('close',()=>{
    // Native close events are queued: an immediate reopen may have already happened.
    if(dialog.open) return;
    ++epoch;targetOpen=false;motion?.cancel();veil?.cancel();
    launcher.setAttribute('aria-expanded','false');
  });
  dialog.addEventListener('click',event=>{
    const next=event.target.closest('[data-sidebar-page]');
    if(next && !next.disabled) showPage(next.dataset.sidebarPage);
    else if(event.target.closest('[data-sidebar-home]')) showPage('home');
  });
  document.addEventListener('workspace-sidebar-close',event=>close({restoreFocus:false}).then(finished=>{if(finished) event.detail?.afterClose?.();}));
  reduced.addEventListener('change',()=>{
    if(dialog.open && reduced.matches) for(const animation of [motion,veil]) {
      animation.currentTime=targetOpen?duration:0;animation.finish();
    }
  });
  showPage('home',{focus:false});
  return {open,close,showPage,toggle:()=>setOpen(!targetOpen),get page(){return page;},get isOpen(){return targetOpen;}};
}

// A viewport-mounted popup keeps attachments reachable above the glass chat dock.
export function setupPromptAttachments({getOptions,getSaved,onCapture,onSaved,isEnabled,onBeforeOpen,onError}) {
  const byId=id=>document.getElementById(id),button=byId('prompt-attach-button'),menu=byId('prompt-attach-menu');
  const home=byId('prompt-attach-home'),library=byId('prompt-attach-library'),list=byId('prompt-attach-list'),status=byId('prompt-attach-status');
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  let opened=false,view='home',busy=false,request=0,animation=null,signature='';
  const options={reference:byId('drag-reference-image'),scene:byId('drag-scene-image')};
  function position() {
    if(!opened) return;
    const r=button.getBoundingClientRect(),v=window.visualViewport;
    const left=v?.offsetLeft || 0,top=v?.offsetTop || 0,width=v?.width || innerWidth,height=v?.height || innerHeight;
    const available=Math.max(100,height-20),w=Math.min(304,width-20);
    menu.style.width=w+'px';menu.style.maxHeight=Math.min(360,available)+'px';
    menu.style.left=Math.max(left+10,Math.min(r.left,left+width-w-10))+'px';
    const h=menu.getBoundingClientRect().height;
    menu.style.top=Math.max(top+10,Math.min(r.top-h-8,top+height-h-10))+'px';
  }
  function close({focus=false}={}) {
    if(!opened) return;
    opened=false;++request;busy=false;
    button.setAttribute('aria-expanded','false');menu.inert=true;
    animation?.cancel();
    animation=menu.animate([{opacity:1,transform:'translateY(0)'},{opacity:0,transform:'translateY(5px)'}],{duration:reduced.matches?0:140,easing:'ease-in',fill:'both'});
    animation.finished.then(()=>{if(!opened) {menu.hidden=true;animation.cancel();}}).catch(()=>{});
    if(focus) button.focus({preventScroll:true});
  }
  async function choose(action) {
    if(busy || !isEnabled()) return;
    const ticket=++request;busy=true;status.hidden=true;refresh();
    try {
      const added=await action(()=>opened && ticket===request);
      if(ticket===request && added) close();
    } catch(error) {
      if(ticket===request) {status.textContent=error.message;status.hidden=false;onError(error.message);}
    } finally {if(ticket===request) {busy=false;refresh();}}
  }
  function refresh() {
    button.disabled=!isEnabled();
    if(!opened) return;
    menu.setAttribute('aria-busy',String(busy));
    if(button.disabled || byId('chat-dock').hidden || byId('chat-dock').inert) {close();return;}
    for(const [pane,option] of Object.entries(getOptions())) {
      const target=options[pane];target.textContent=option.label;target.disabled=busy || !!option.disabled;target.title=option.disabled || option.label;
    }
    const saved=getSaved();
    byId('prompt-attach-saved').disabled=busy;
    if(view==='saved') {
      const next=JSON.stringify([busy,saved.map(item=>[item.id,item.title,item.disabled])]);
      if(next!==signature) {
        const focused=document.activeElement?.dataset.attachmentId;signature=next;list.replaceChildren();
        if(!saved.length) {const empty=document.createElement('p');empty.className='attachment-empty';empty.textContent='还没有保存截图，先在场景上方点击「截图」。';list.append(empty);}
        for(const item of saved) {
          const choice=document.createElement('button');choice.type='button';choice.className='attachment-saved';choice.dataset.attachmentId=item.id;choice.disabled=busy || item.disabled;choice.title=item.title;choice.setAttribute('aria-label','引用 '+item.title);
          const img=document.createElement('img');img.src=item.scene;img.alt='';
          const label=document.createElement('span');label.textContent=item.name || '截图';
          const detail=document.createElement('small');detail.textContent=item.title;
          choice.append(img,label,detail);choice.addEventListener('click',()=>choose(current=>onSaved(item.id,current)));list.append(choice);
        }
        if(focused) [...list.children].find(node=>node.dataset.attachmentId===focused)?.focus({preventScroll:true});
      }
    }
    position();
  }
  function showPage(next) {
    view=next;home.hidden=next!=='home';library.hidden=next!=='saved';status.hidden=true;signature='';refresh();
    (next==='saved'?byId('prompt-attach-back'):byId('prompt-attach-saved')).focus({preventScroll:true});
  }
  function open() {
    if(!isEnabled()) return;
    onBeforeOpen();opened=true;view='home';busy=false;++request;
    animation?.cancel();menu.hidden=false;menu.inert=false;home.hidden=false;library.hidden=true;status.hidden=true;
    button.setAttribute('aria-expanded','true');refresh();
    animation=menu.animate([{opacity:0,transform:'translateY(6px)'},{opacity:1,transform:'translateY(0)'}],{duration:reduced.matches?0:190,easing:'cubic-bezier(.22,1,.36,1)'});
    home.querySelector('button:not(:disabled)')?.focus({preventScroll:true});
  }
  button.addEventListener('click',()=>opened?close({focus:true}):open());
  for(const [pane,target] of Object.entries(options)) target.addEventListener('click',()=>choose(()=>onCapture(pane)));
  byId('prompt-attach-saved').addEventListener('click',()=>showPage('saved'));
  byId('prompt-attach-back').addEventListener('click',()=>showPage('home'));
  menu.addEventListener('keydown',event=>{
    if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key)) return;
    const choices=[...menu.querySelectorAll('button:not(:disabled)')].filter(node=>node.getClientRects().length);
    const index=choices.indexOf(document.activeElement);if(!choices.length)return;
    event.preventDefault();choices[event.key==='Home'?0:event.key==='End'?choices.length-1:(index+(event.key==='ArrowUp'?-1:1)+choices.length)%choices.length].focus();
  });
  document.addEventListener('keydown',event=>{if(opened && event.key==='Escape') {event.preventDefault();event.stopImmediatePropagation();close({focus:true});}},true);
  document.addEventListener('pointerdown',event=>{if(opened && !menu.contains(event.target) && !button.contains(event.target)) close();});
  document.addEventListener('focusin',event=>{if(opened && !menu.contains(event.target) && !button.contains(event.target)) close();});
  document.addEventListener('chat-preview-compact',()=>close());
  new MutationObserver(()=>{if(opened && (byId('chat-dock').inert || byId('chat-dock').hidden)) close();}).observe(byId('chat-dock'),{attributes:true,attributeFilter:['inert','hidden']});
  window.addEventListener('resize',position);window.addEventListener('scroll',position,true);
  window.visualViewport?.addEventListener('resize',position);window.visualViewport?.addEventListener('scroll',position);
  return {refresh,close};
}

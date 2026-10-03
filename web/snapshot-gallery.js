import {actionIcon} from './action-icons.js';

// Both kinds of evidence share navigation, while their saved payloads stay separate.
export function setupSnapshotGallery({getItems, getTimeline, onOpen, onRemove, resourceURL}) {
  const strip=document.getElementById('scene-snapshot-strip');
  const gallery=document.getElementById('scene-snapshots'),stage=document.getElementById('scene-stage');
  function placeGallery() {
    const parent=document.documentElement.dataset.layout==='immersive' ? document.body : stage;
    if(gallery.parentElement!==parent) parent.append(gallery);
  }
  new MutationObserver(placeGallery).observe(document.documentElement,{attributes:true,attributeFilter:['data-layout']});
  placeGallery();
  const ticks=document.getElementById('timeline-marks');
  const preview=document.createElement('div');preview.className='snapshot-preview';preview.hidden=true;
  preview.id='snapshot-gallery-preview';preview.setAttribute('role','tooltip');document.body.append(preview);
  let signature='',tickSignature='',timer=null,anchor=null,locked=false,suppressFocusPreview=false;
  const hover=matchMedia('(hover: hover)');
  function el(tag,text,cls) {const node=document.createElement(tag);if(text) node.textContent=text;if(cls) node.className=cls;return node;}
  function hide() {clearTimeout(timer);preview.hidden=true;anchor?.removeAttribute('aria-describedby');anchor=null;locked=false;}
  function position() {
    if(!anchor?.isConnected) {hide();return;}
    const r=anchor.getBoundingClientRect(),p=preview.getBoundingClientRect();
    preview.style.left=Math.max(8,Math.min(innerWidth-p.width-8,r.right-p.width))+'px';
    const below=r.bottom+8;
    preview.style.top=Math.max(8,below+p.height<innerHeight-8 ? below : r.top-p.height-8)+'px';
  }
  function show(button,items,{choose=false}={}) {
    clearTimeout(timer);if(document.querySelector('dialog[open]')) return;
    anchor?.removeAttribute('aria-describedby');anchor=button;locked=choose;
    preview.replaceChildren();
    for(const item of items) {
      const section=el('div',null,'snapshot-preview-item');
      if(choose) {const open=el('button',item.title,'compact-button');open.type='button';open.addEventListener('click',()=>{hide();onOpen(item.id);});section.append(open);}
      else section.append(el('strong',item.name));
      section.append(el('small',item.title));
      const pair=el('div',null,'snapshot-preview-pair');
      for(const [label,url] of [['参考帧',item.reference],['场景截图',item.scene]]) {
        if(!url) continue;
        const figure=el('figure'),img=el('img');img.src=resourceURL(url);img.alt=label;img.addEventListener('load',position,{once:true});figure.append(img,el('figcaption',label));pair.append(figure);
      }
      section.append(pair);preview.append(section);
    }
    preview.setAttribute('role',choose?'group':'tooltip');preview.setAttribute('aria-label',choose?'选择此时刻的截图':'截图预览');
    preview.hidden=false;button.setAttribute('aria-describedby',preview.id);position();
    if(choose) preview.querySelector('button')?.focus({preventScroll:true});
  }
  function scheduleHide() {if(!locked) {clearTimeout(timer);timer=setTimeout(hide,160);}}
  function bindPreview(button,items) {
    button.addEventListener('pointerenter',()=>{if(hover.matches && !locked) {clearTimeout(timer);timer=setTimeout(()=>show(button,items),240);}});
    button.addEventListener('pointerleave',scheduleHide);
    button.addEventListener('focus',()=>{if(!suppressFocusPreview && button.matches(':focus-visible')) show(button,items);});
    button.addEventListener('blur',scheduleHide);
  }
  function render() {
    const items=getItems();
    const next=JSON.stringify(items.map(({id,title,active,disabled,cover})=>[id,title,active,disabled,cover?.slice(0,80),cover?.length]));
    if(next!==signature) {
      signature=next;hide();
      const focused=document.activeElement?.closest('.snapshot-card');const focusedId=focused?.dataset.snapshotId, wasRemove=document.activeElement?.classList.contains('snapshot-remove');
      const scroll=strip.scrollLeft;strip.replaceChildren();
      for(const item of items) {
        const card=el('div',null,'snapshot-card');card.dataset.kind=item.dynamic?'moment':'static';card.dataset.snapshotId=item.id;
        const open=el('button',null,'snapshot-open');open.type='button';open.disabled=item.disabled;open.dataset.snapshotId=item.id;open.setAttribute('aria-pressed',String(item.active));open.setAttribute('aria-label',item.title);
        const thumb=el('span',null,'snapshot-thumb'),img=el('img');img.src=resourceURL(item.cover);img.alt='';thumb.append(img);
        if(item.dynamic) {const badge=el('span',null,'snapshot-video-icon');badge.append(actionIcon('play'));thumb.append(badge);}
        if(item.count) thumb.append(el('span',String(item.count),'snapshot-mark-count'));
        open.append(thumb,el('span',item.name));bindPreview(open,[item]);
        open.addEventListener('click',()=>{hide();onOpen(item.id);});
        const remove=el('button','×','snapshot-remove');remove.type='button';remove.disabled=item.disabled;remove.title='删除'+item.name+'及其标记，可撤销';remove.setAttribute('aria-label',remove.title);
        remove.addEventListener('click',()=>{hide();onRemove(item.id);});card.append(open,remove);strip.append(card);
      }
      strip.scrollLeft=scroll;
      if(focusedId) strip.querySelector(`[data-snapshot-id="${focusedId}"] .${wasRemove?'snapshot-remove':'snapshot-open'}`)?.focus({preventScroll:true});
    }
    const {duration,viewId}=getTimeline();
    const moments=items.filter(item=>item.dynamic && item.time>=0 && item.time<=duration && (!viewId || item.viewId===viewId));
    const nextTicks=JSON.stringify([duration,viewId,moments.map(({id,title,active,disabled})=>[id,title,active,disabled])]);
    if(tickSignature===nextTicks) return;
    tickSignature=nextTicks;ticks.replaceChildren();
    const groups=new Map();
    for(const item of moments) {const key=Math.round(item.time*1e6);if(!groups.has(key)) groups.set(key,[]);groups.get(key).push(item);}
    for(const entries of groups.values()) {
      const item=entries[0],button=el('button',null,'timeline-mark');button.type='button';button.disabled=item.disabled;
      button.style.left=(duration ? item.time/duration*100 : 0)+'%';button.setAttribute('aria-label',item.name+' · '+entries.length+' 张保存截图');button.setAttribute('aria-pressed',String(entries.some(entry=>entry.active)));
      button.dataset.snapshotId=item.id;bindPreview(button,entries);
      button.addEventListener('click',()=> {if(entries.length===1) {hide();onOpen(item.id);} else show(button,entries,{choose:true});});ticks.append(button);
    }
    ticks.hidden=!groups.size;
  }
  preview.addEventListener('pointerenter',()=>clearTimeout(timer));preview.addEventListener('pointerleave',scheduleHide);
  preview.addEventListener('focusout',event=>{if(!preview.contains(event.relatedTarget)) hide();});
  document.addEventListener('pointerdown',event=>{if(!preview.contains(event.target) && event.target!==anchor) hide();},{capture:true});
  document.addEventListener('keydown',event=> {if(event.key==='Escape' && !preview.hidden) {const target=anchor,wasLocked=locked;hide();if(wasLocked) {suppressFocusPreview=true;target?.focus({preventScroll:true});suppressFocusPreview=false;}event.stopImmediatePropagation();event.preventDefault();}},{capture:true});
  document.addEventListener('scroll',event=>{if(!preview.contains(event.target)) hide();},{capture:true,passive:true});
  window.addEventListener('resize',hide);
  return {render,hide};
}

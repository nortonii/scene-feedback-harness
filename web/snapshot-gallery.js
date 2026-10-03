import {actionIcon} from './action-icons.js';

// The live scene leads the collection; saved evidence keeps its creation order.
export function setupSnapshotGallery({getItems, getLive=()=>({active:true,disabled:false}), getTimeline, onLive=()=>{}, onOpen, onRemove, resourceURL}) {
  const strip=document.getElementById('scene-snapshot-strip');
  const gallery=document.getElementById('scene-snapshots'),stage=document.getElementById('scene-stage');
  function placeGallery() {
    if(document.documentElement.dataset.layout==='immersive') {
      if(gallery.parentElement!==document.body) document.body.append(gallery);
    } else if(gallery.parentElement!==stage.parentElement || gallery.nextElementSibling!==stage) {
      stage.parentElement.insertBefore(gallery,stage);
    }
  }
  new MutationObserver(placeGallery).observe(document.documentElement,{attributes:true,attributeFilter:['data-layout']});
  placeGallery();
  const ticks=document.getElementById('timeline-marks');
  const preview=document.createElement('div');preview.className='snapshot-preview';preview.hidden=true;
  preview.id='snapshot-gallery-preview';preview.setAttribute('role','tooltip');document.body.append(preview);
  let signature='',tickSignature='',activeKey=null,timer=null,hoverTimer=null,anchor=null,locked=false,suppressFocusPreview=false;
  const hover=matchMedia('(hover: hover)');
  function el(tag,text,cls) {const node=document.createElement(tag);if(text) node.textContent=text;if(cls) node.className=cls;return node;}
  function liveItem() {
    const live=getLive() || {};
    return {...live,id:'live',live:true,name:live.name || '3D',title:live.title || '实时 3D · 拖动旋转、滚轮缩放',
      detail:'实时 · 可旋转浏览',scene:live.cover};
  }
  function cube() {
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    for(const [name,value] of Object.entries({viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':'1.35','stroke-linejoin':'round','aria-hidden':'true'})) svg.setAttribute(name,value);
    const path=document.createElementNS(svg.namespaceURI,'path');
    path.setAttribute('d','m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Zm0 9 8-4.5M12 12 4 7.5M12 12v9');svg.append(path);
    return svg;
  }
  function hide({keepHover=false}={}) {
    clearTimeout(timer);if(!keepHover) {clearTimeout(hoverTimer);hoverTimer=null;}
    preview.hidden=true;anchor?.removeAttribute('aria-describedby');anchor=null;locked=false;
  }
  function position() {
    if(!anchor?.isConnected) {hide();return;}
    const r=anchor.getBoundingClientRect(),p=preview.getBoundingClientRect();
    preview.style.left=Math.max(8,Math.min(innerWidth-p.width-8,r.right-p.width))+'px';
    const below=r.bottom+8;
    preview.style.top=Math.max(8,below+p.height<innerHeight-8 ? below : r.top-p.height-8)+'px';
  }
  function show(button,items,{choose=false}={}) {
    clearTimeout(timer);clearTimeout(hoverTimer);hoverTimer=null;if(document.querySelector('dialog[open]')) return;
    anchor?.removeAttribute('aria-describedby');anchor=button;locked=choose;
    preview.replaceChildren();
    for(const item of items) {
      const section=el('div',null,'snapshot-preview-item');
      if(choose) {const open=el('button',item.title,'compact-button');open.type='button';open.addEventListener('click',()=>{hide();onOpen(item.id);});section.append(open);}
      else section.append(el('strong',item.name));
      section.append(el('small',item.title));
      const pair=el('div',null,'snapshot-preview-pair');
      for(const [label,url] of [['参考帧',item.reference],[item.live?'实时 3D 预览':'场景截图',item.scene]]) {
        if(!url) continue;
        const figure=el('figure'),img=el('img');img.src=resourceURL(url);img.alt=label;img.addEventListener('load',position,{once:true});figure.append(img,el('figcaption',label));pair.append(figure);
      }
      section.append(pair);preview.append(section);
    }
    preview.setAttribute('role',choose?'group':'tooltip');preview.setAttribute('aria-label',choose?'选择此时刻的截图':'截图预览');
    preview.hidden=false;button.setAttribute('aria-describedby',preview.id);position();
    if(choose) preview.querySelector('button')?.focus({preventScroll:true});
  }
  function scheduleHide() {if(!locked) {clearTimeout(timer);clearTimeout(hoverTimer);hoverTimer=null;timer=setTimeout(hide,160);}}
  function bindPreview(button,items) {
    const currentItems=()=>typeof items==='function' ? items() : items;
    button.addEventListener('pointerenter',()=>{if(hover.matches && !locked) {
      clearTimeout(timer);clearTimeout(hoverTimer);
      hoverTimer=setTimeout(()=>{hoverTimer=null;if(button.isConnected && button.matches(':hover')) show(button,currentItems());},240);
    }});
    button.addEventListener('pointerleave',scheduleHide);
    button.addEventListener('focus',()=>{if(!suppressFocusPreview && button.matches(':focus-visible')) show(button,currentItems());});
    button.addEventListener('blur',scheduleHide);
  }
  function render() {
    const items=getItems();
    const live=liveItem();
    const cards=[live,...items];
    const nextActive=cards.find(item=>item.active)?.id || null,activeChanged=nextActive!==activeKey;
    activeKey=nextActive;
    gallery.classList.remove('hidden','is-collapsed');
    strip.setAttribute('aria-label','实时 3D 与保留截图');
    const next=JSON.stringify(cards.map(({id,title,name,detail,count,active,disabled,cover,live})=>[id,title,name,detail,count,active,disabled,live ? null : cover?.slice(0,80),live ? null : cover?.length]));
    if(next!==signature) {
      signature=next;hide();
      const focused=document.activeElement?.closest('.snapshot-card');const focusedId=focused?.dataset.galleryKey, wasRemove=document.activeElement?.classList.contains('snapshot-remove');
      const focusedIndex=[...strip.children].indexOf(focused);
      const scroll=strip.scrollLeft;strip.replaceChildren();
      for(const item of cards) {
        const card=el('div',null,'snapshot-card');card.dataset.kind=item.live?'live':item.dynamic?'moment':'static';card.dataset.galleryKey=item.id;
        if(!item.live) card.dataset.snapshotId=item.id;
        const open=el('button',null,'snapshot-open');open.type='button';open.disabled=item.disabled;
        if(item.live) open.id='scene-live-card';else open.dataset.snapshotId=item.id;
        open.setAttribute('aria-pressed',String(!!item.active));open.setAttribute('aria-label',item.title);open.title=item.title;
        const thumb=el('span',null,'snapshot-thumb'),img=el('img');img.alt='';thumb.append(img);
        if(item.live) {
          const placeholder=el('span',null,'snapshot-live-placeholder');placeholder.append(cube());thumb.append(placeholder);
          img.addEventListener('error',()=>{img.dataset.failedUrl=img.getAttribute('src') || '';img.hidden=true;placeholder.hidden=false;});
          thumb.append(el('span','实时','snapshot-live-badge'));
        } else img.src=resourceURL(item.cover);
        if(item.dynamic) {const badge=el('span',null,'snapshot-video-icon');badge.append(actionIcon('play'));thumb.append(badge);}
        if(item.count) thumb.append(el('span',String(item.count),'snapshot-mark-count'));
        const meta=el('span',null,'snapshot-card-meta');
        meta.append(el('strong',item.name,'snapshot-name'),el('small',item.detail || (item.dynamic?'视频保留时刻':'固定视角'),'snapshot-source'));
        open.append(thumb,meta);bindPreview(open,item.live ? ()=>[liveItem()] : [item]);
        open.addEventListener('click',()=>{hide();if(item.live) onLive();else onOpen(item.id);});card.append(open);
        if(!item.live) {
          const remove=el('button','×','snapshot-remove');remove.type='button';remove.disabled=item.disabled;remove.title='删除'+item.name+'及其标记，可撤销';remove.setAttribute('aria-label',remove.title);
          remove.addEventListener('click',()=>{hide();onRemove(item.id);});card.append(remove);
        }
        strip.append(card);
      }
      strip.scrollLeft=scroll;
      if(focusedId) {
        const card=[...strip.children].find(card=>card.dataset.galleryKey===focusedId) || strip.children[Math.max(0,Math.min(focusedIndex,strip.children.length-1))];
        (wasRemove && card?.querySelector('.snapshot-remove') || card?.querySelector('.snapshot-open'))?.focus({preventScroll:true});
      }
    }
    if(activeChanged && nextActive) requestAnimationFrame(()=>{
      if(activeKey!==nextActive) return;
      const card=[...strip.children].find(card=>card.dataset.galleryKey===nextActive);
      card?.querySelector('.snapshot-open')?.scrollIntoView({block:'nearest',inline:'nearest'});
    });
    // Updating the live preview must not rebuild saved cards, reset their
    // keyboard focus or close a hovered evidence preview.
    const liveThumb=strip.querySelector('[data-kind="live"] .snapshot-thumb');
    const liveImage=liveThumb?.querySelector('img'),placeholder=liveThumb?.querySelector('.snapshot-live-placeholder');
    if(liveImage) {
      const url=live.cover ? resourceURL(live.cover) : null;
      if(url && liveImage.getAttribute('src')!==url) {delete liveImage.dataset.failedUrl;liveImage.src=url;}
      if(!url) liveImage.removeAttribute('src');
      const available=!!url && liveImage.dataset.failedUrl!==url;
      liveImage.hidden=!available;placeholder.hidden=available;
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
  strip.addEventListener('keydown',event=>{
    if(!event.target.closest('.snapshot-open') || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    const buttons=[...strip.querySelectorAll('.snapshot-open:not(:disabled)')],index=buttons.indexOf(event.target.closest('.snapshot-open'));
    const next=event.key==='Home' ? 0 : event.key==='End' ? buttons.length-1 : Math.max(0,Math.min(buttons.length-1,index+(event.key==='ArrowRight'?1:-1)));
    if(!buttons[next]) return;
    event.preventDefault();buttons[next].focus({preventScroll:true});buttons[next].scrollIntoView({block:'nearest',inline:'nearest'});
  });
  new ResizeObserver(()=>document.documentElement.style.setProperty('--scene-gallery-height',gallery.getBoundingClientRect().height+'px')).observe(gallery);
  preview.addEventListener('pointerenter',()=>clearTimeout(timer));preview.addEventListener('pointerleave',scheduleHide);
  preview.addEventListener('focusout',event=>{if(!preview.contains(event.relatedTarget)) hide();});
  document.addEventListener('pointerdown',event=>{if(!preview.contains(event.target) && event.target!==anchor) hide();},{capture:true});
  document.addEventListener('keydown',event=> {if(event.key==='Escape' && !preview.hidden) {const target=anchor,wasLocked=locked;hide();if(wasLocked) {suppressFocusPreview=true;target?.focus({preventScroll:true});suppressFocusPreview=false;}event.stopImmediatePropagation();event.preventDefault();}},{capture:true});
  // Auto scrolling a large card into view can dispatch scroll immediately
  // after pointerenter. Close an existing preview without cancelling the new
  // target's pending hover; pointerleave still cancels it if the card moves away.
  document.addEventListener('scroll',event=>{if(!preview.hidden && !preview.contains(event.target)) hide({keepHover:true});},{capture:true,passive:true});
  window.addEventListener('resize',hide);
  return {render,hide};
}

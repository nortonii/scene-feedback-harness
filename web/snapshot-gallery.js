import {actionIcon} from './action-icons.js';

// The live scene leads the collection; saved evidence keeps its creation order.
export function setupSnapshotGallery({getItems, getLive=()=>({active:true,disabled:false}), getTimeline, onLive=()=>{}, onOpen, onTime=()=>{}, onRemove, resourceURL}) {
  const strip=document.getElementById('scene-snapshot-strip');
  const gallery=document.getElementById('scene-snapshots'),footer=document.querySelector('.scene-pane-footer');
  function placeGallery() {
    if(document.documentElement.dataset.layout==='immersive') {
      if(gallery.parentElement!==document.body) document.body.append(gallery);
    } else if(gallery.parentElement!==footer) {
      footer.append(gallery);
    }
  }
  new MutationObserver(placeGallery).observe(document.documentElement,{attributes:true,attributeFilter:['data-layout']});
  placeGallery();
  const ticks=document.getElementById('timeline-marks');
  const preview=document.createElement('div');preview.className='snapshot-preview';preview.hidden=true;
  preview.id='snapshot-gallery-preview';preview.setAttribute('role','group');preview.setAttribute('aria-label','选择此时刻的截图');document.body.append(preview);
  let signature='',tickSignature='',activeKey=null,anchor=null;
  function keepExpandedCardVisible(card) {
    if (!card.isConnected || card.dataset.kind === 'live') return;
    const bounds=strip.getBoundingClientRect(),rect=card.getBoundingClientRect(),live=strip.firstElementChild;
    const left=getComputedStyle(live).position === 'sticky' ? live.getBoundingClientRect().right+8 : bounds.left+1;
    const right=bounds.left+strip.clientWidth-8;
    if (rect.left<left) strip.scrollLeft+=rect.left-left;
    else if (rect.right>right) strip.scrollLeft+=rect.right-right;
  }
  // Keep the expanding card inside the row throughout its size transition.
  // Moving only the strip preserves the pointer's target and the scene below.
  const cardWidths=new WeakMap();
  const cardSizes=new ResizeObserver(entries=>{
    for (const {target,contentRect} of entries) {
      const prior=cardWidths.get(target);cardWidths.set(target,contentRect.width);
      if (contentRect.width>124 && (prior===undefined || contentRect.width>prior) &&
          target.matches(':hover, :has(:focus-visible)')) keepExpandedCardVisible(target);
    }
  });
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
  function hide() {
    preview.hidden=true;anchor?.removeAttribute('aria-describedby');anchor=null;
  }
  function position() {
    if(!anchor?.isConnected) {hide();return;}
    const r=anchor.getBoundingClientRect(),p=preview.getBoundingClientRect();
    preview.style.left=Math.max(8,Math.min(innerWidth-p.width-8,r.right-p.width))+'px';
    const below=r.bottom+8;
    preview.style.top=Math.max(8,below+p.height<innerHeight-8 ? below : r.top-p.height-8)+'px';
  }
  function showChooser(button,items) {
    if(document.querySelector('dialog[open]')) return;
    anchor?.removeAttribute('aria-describedby');anchor=button;
    preview.replaceChildren();
    for(const item of items) {
      const section=el('div',null,'snapshot-preview-item');
      const open=el('button',item.title,'compact-button');open.type='button';open.addEventListener('click',()=>{hide();onOpen(item.id);});section.append(open);
      section.append(el('small',item.title));
      const pair=el('div',null,'snapshot-preview-pair');
      for(const [label,url] of [['参考帧',item.reference],['场景截图',item.scene]]) {
        if(!url) continue;
        const figure=el('figure'),img=el('img');img.src=resourceURL(url);img.alt=label;img.addEventListener('load',position,{once:true});figure.append(img,el('figcaption',label));pair.append(figure);
      }
      section.append(pair);preview.append(section);
    }
    preview.hidden=false;button.setAttribute('aria-describedby',preview.id);position();
    preview.querySelector('button')?.focus({preventScroll:true});
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
      const focused=document.activeElement?.closest('.snapshot-card');const focusedId=focused?.dataset.galleryKey, wasRemove=document.activeElement?.classList.contains('snapshot-remove'), wasTime=document.activeElement?.classList.contains('snapshot-time-reference');
      const focusedIndex=[...strip.children].indexOf(focused);
      const scroll=strip.scrollLeft;cardSizes.disconnect();strip.replaceChildren();
      for(const item of cards) {
        const card=el('div',null,'snapshot-card');card.dataset.kind=item.live?'live':item.dynamic?'moment':'static';card.dataset.galleryKey=item.id;
        if(!item.live) card.dataset.snapshotId=item.id;
        const open=el('button',null,'snapshot-open');open.type='button';open.disabled=item.disabled;
        if(item.live) open.id='scene-live-card';else open.dataset.snapshotId=item.id;
        open.setAttribute('aria-pressed',String(!!item.active));open.setAttribute('aria-label',item.title);
        const thumb=el('span',null,'snapshot-thumb'),img=el('img');img.alt='';thumb.append(img);
        if(item.live) {
          const placeholder=el('span',null,'snapshot-live-placeholder');placeholder.append(cube());thumb.append(placeholder);
          img.addEventListener('error',()=>{img.dataset.failedUrl=img.getAttribute('src') || '';img.hidden=true;placeholder.hidden=false;});
          thumb.append(el('span','实时','snapshot-live-badge'));
        } else img.src=resourceURL(item.cover);
        if(item.dynamic) {const badge=el('span',null,'snapshot-video-icon');badge.append(actionIcon('play'));thumb.append(badge);}
        if(item.count) thumb.append(el('span',String(item.count),'snapshot-mark-count'));
        const meta=el('span',null,'snapshot-card-meta');
        meta.append(el('strong',item.name,'snapshot-name'),el('small',item.dynamic ? '\u00a0' : item.detail || '固定视角',item.dynamic ? 'snapshot-time-space' : 'snapshot-source'));
        open.append(thumb,meta);
        open.addEventListener('click',()=>{hide();if(item.live) onLive();else onOpen(item.id);});card.append(open);
        if(item.dynamic) {
          // A separate button survives both clicks of a double click. Quoting
          // its stored time never opens the snapshot or seeks another view.
          const time=el('button',item.detail,'snapshot-source snapshot-time-reference');time.type='button';
          time.dataset.snapshotId=item.id;time.disabled=item.disabled;
          time.title='双击或按 Enter / 空格引用'+item.name+'的时间';time.setAttribute('aria-label',time.title+' · '+item.detail);
          time.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();if(event.detail===0){hide();onTime(item.id);}});
          time.addEventListener('dblclick',event=>{event.preventDefault();event.stopPropagation();hide();onTime(item.id);});
          card.append(time);
        }
        if(!item.live) {
          const remove=el('button','×','snapshot-remove');remove.type='button';remove.disabled=item.disabled;remove.title='删除'+item.name+'及其标记，可撤销';remove.setAttribute('aria-label',remove.title);
          remove.addEventListener('click',()=>{hide();onRemove(item.id);});card.append(remove);
        }
        strip.append(card);cardSizes.observe(card);
      }
      strip.scrollLeft=scroll;
      if(focusedId) {
        const card=[...strip.children].find(card=>card.dataset.galleryKey===focusedId) || strip.children[Math.max(0,Math.min(focusedIndex,strip.children.length-1))];
        (wasRemove && card?.querySelector('.snapshot-remove') || wasTime && card?.querySelector('.snapshot-time-reference') || card?.querySelector('.snapshot-open'))?.focus({preventScroll:true});
      }
    }
    if(activeChanged && nextActive) requestAnimationFrame(()=>{
      if(activeKey!==nextActive) return;
      const card=[...strip.children].find(card=>card.dataset.galleryKey===nextActive);
      card?.querySelector('.snapshot-open')?.scrollIntoView({block:'nearest',inline:'nearest'});
    });
    // Updating the live cover must not rebuild saved cards or reset their focus.
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
      button.dataset.snapshotId=item.id;
      button.addEventListener('click',()=> {if(entries.length===1) {hide();onOpen(item.id);} else showChooser(button,entries);});ticks.append(button);
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
  const layoutSizes=new ResizeObserver(()=>{
    const root=document.documentElement;
    root.style.setProperty('--scene-gallery-height',gallery.getBoundingClientRect().height+'px');
    root.style.setProperty('--workspace-footer-height',footer.getBoundingClientRect().height+'px');
    const timeline=document.getElementById('timeline-panel');
    root.style.setProperty('--workspace-timeline-height',(!timeline.classList.contains('hidden') && !timeline.classList.contains('reference-timeline') ? timeline.getBoundingClientRect().height : 0)+'px');
  });
  for(const node of [gallery,footer,document.getElementById('timeline-panel')]) layoutSizes.observe(node);
  preview.addEventListener('focusout',event=>{if(!preview.contains(event.relatedTarget)) hide();});
  document.addEventListener('pointerdown',event=>{if(!preview.contains(event.target) && event.target!==anchor) hide();},{capture:true});
  document.addEventListener('keydown',event=> {if(event.key==='Escape' && !preview.hidden) {const target=anchor;hide();target?.focus({preventScroll:true});event.stopImmediatePropagation();event.preventDefault();}},{capture:true});
  document.addEventListener('scroll',event=>{if(!preview.hidden && !preview.contains(event.target)) hide();},{capture:true,passive:true});
  window.addEventListener('resize',hide);
  return {render,hide};
}

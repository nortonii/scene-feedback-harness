// Keep native textarea selection and IME behavior for the visible short names.
// One menu replaces either an @ reference or a / time query. The caller owns
// source validation and inserts through the textarea's usual editing path.
export function findPromptMentionRange(text, start, end=start) {
  if (start !== end || !Number.isInteger(start)) return null;
  const at=text.lastIndexOf('@',start-1);
  if (at < 0 || at >= start || /[A-Za-z0-9._%+\-]/.test(text[at-1] || '')) return null;
  const query=text.slice(at+1,start);
  if (/[\s@\[\]【】]/.test(query)) return null;
  const open=text.lastIndexOf('[[',at), close=text.lastIndexOf(']]',at);
  if (open > close) return null;
  return {start:at,end:start,query,text:text.slice(at,start)};
}

export function findPromptTimeRange(text, start, end=start) {
  if (start !== end || !Number.isInteger(start)) return null;
  const slash=text.lastIndexOf('/',start-1);
  // Avoid opening for URLs, paths, ratios or a slash inside a short reference.
  if (slash < 0 || slash >= start || /[A-Za-z0-9._%+/:\\\-]/.test(text[slash-1] || '')) return null;
  const query=text.slice(slash+1,start);
  if (/[\s/@\[\]【】]/.test(query)) return null;
  const open=text.lastIndexOf('[[',slash), close=text.lastIndexOf(']]',slash);
  if (open > close) return null;
  if (text.lastIndexOf('【',slash) > text.lastIndexOf('】',slash)) return null;
  return {start:slash,end:start,query,text:text.slice(slash,start),trigger:'/'};
}

export function findPromptQueryRange(text, start, end=start) {
  const mention=findPromptMentionRange(text,start,end), time=findPromptTimeRange(text,start,end);
  if (time && (!mention || time.start > mention.start)) return time;
  return mention ? {...mention,trigger:'@'} : null;
}

export function filterPromptMentions(candidates, query) {
  const normalize=(value) => String(value || '').normalize('NFKC').toLocaleLowerCase().replace(/\s+/g,'');
  const search=normalize(query);
  return candidates.filter((item) => !search || normalize([item.label,item.detail,item.kind,item.search].join(' ')).includes(search));
}

export function createPromptMentions({input,menu,list,status,getCandidates,onSelect,isEnabled,onError,getEmptyMessage}) {
  // The chat dock is transformed and scrollable. Mount the fixed popup outside
  // it so viewport coordinates and mobile keyboard bounds stay correct.
  document.body.append(menu);
  let range=null, candidates=[], activeKey=null, composing=false, dismissed=null;
  let selecting=false,previewDismissed=null;
  const identity=(value) => value ? (value.value ?? input.value)+'\u0000'+value.start+':'+value.end : null;
  const isComposing=(event) => composing || event?.isComposing || event?.keyCode === 229;
  input.setAttribute('aria-autocomplete','list');
  input.setAttribute('aria-haspopup','listbox');
  input.setAttribute('aria-controls',list.id);
  input.setAttribute('aria-expanded','false');

  function close({dismiss=false}={}) {
    if (dismiss) {dismissed=identity(range);previewDismissed=null;}
    range=null; candidates=[]; activeKey=null;
    menu.classList.add('hidden');
    input.setAttribute('aria-expanded','false');
    input.removeAttribute('aria-activedescendant');
  }
  function position() {
    if (!range) return;
    const bounds=input.getBoundingClientRect(), viewport=window.visualViewport;
    const left=viewport?.offsetLeft || 0, top=viewport?.offsetTop || 0;
    const width=viewport?.width || innerWidth, height=viewport?.height || innerHeight;
    menu.style.left=Math.max(left+8,Math.min(bounds.left,left+width-208))+'px';
    menu.style.width=Math.max(192,Math.min(bounds.width,width-16))+'px';
    const above=Math.max(0,bounds.top-top-12), below=Math.max(0,top+height-bounds.bottom-12);
    const placeAbove=above >= Math.min(220,below);
    const available=Math.max(80,Math.min(300,placeAbove ? above : below));
    menu.style.maxHeight=available+'px';
    if (placeAbove) {
      menu.style.top='auto'; menu.style.bottom=Math.max(8,innerHeight-bounds.top+6)+'px';
    } else {
      menu.style.bottom='auto'; menu.style.top=Math.max(top+8,bounds.bottom+6)+'px';
    }
  }
  function paintActive(scroll=false) {
    for (const button of list.children) {
      const active=button.dataset.mentionKey === activeKey;
      button.setAttribute('aria-selected',String(active));
      if (active) {
        input.setAttribute('aria-activedescendant',button.id);
        if (scroll) button.scrollIntoView({block:'nearest'});
      }
    }
    if (!activeKey) input.removeAttribute('aria-activedescendant');
  }
  function refresh() {
    if (selecting) return;
    const next=findPromptQueryRange(input.value,input.selectionStart,input.selectionEnd);
    if (composing || document.activeElement !== input || !isEnabled() || !next || identity(next) === dismissed) { close(); return; }
    range={...next,value:input.value};
    const available=getCandidates(range);
    const all=filterPromptMentions(available,range.query);
    candidates=all.slice(0,80);
    if (!candidates.some((item) => item.key === activeKey && !item.disabled)) activeKey=candidates.find((item) => !item.disabled)?.key || null;
    const previousScroll=list.scrollTop;
    list.replaceChildren();
    candidates.forEach((item,index) => {
      const button=document.createElement('button'); button.type='button'; button.tabIndex=-1;
      button.className='prompt-mention-option'; button.id=list.id+'-option-'+index;
      button.dataset.mentionKey=item.key; button.dataset.mentionKind=item.kind;
      button.setAttribute('role','option'); button.setAttribute('aria-disabled',String(!!item.disabled));
      button.disabled=!!item.disabled;
      const name=document.createElement('strong'); name.textContent=item.label;
      const detail=document.createElement('small'); detail.textContent=item.disabled || item.detail || '';
      button.append(name,detail);
      button.addEventListener('pointerdown',(event) => { event.preventDefault(); });
      button.addEventListener('click',() => choose(item));
      list.append(button);
    });
    const timeQuery=range.trigger === '/';
    list.setAttribute('aria-label',timeQuery ? '选择片段时间' : '选择提示引用');
    status.textContent=!available.length ? (getEmptyMessage?.(range) || (timeQuery ? '当前没有可引用的片段时间' : '先选中物体或添加标记'))
      : !all.length ? (timeQuery ? '没有匹配的时间，可输入秒数、机位或标记名' : '没有匹配的引用')
        : all.length+(timeQuery ? ' 个时间' : ' 个引用')+(all.length > 80 ? '，请继续输入名称缩小范围' : ' · ↑↓ 选择，Enter 插入，Esc 关闭');
    menu.classList.remove('hidden'); input.setAttribute('aria-expanded','true');
    paintActive(); list.scrollTop=previousScroll; position();
  }
  function choose(item) {
    if (!range || selecting || isComposing() || item.disabled) return false;
    const current=findPromptQueryRange(input.value,input.selectionStart,input.selectionEnd);
    if (!current || identity(current) !== identity(range) || !isEnabled()) { close(); return false; }
    const note=input.value, start=input.selectionStart, end=input.selectionEnd;
    selecting=true;
    input.setSelectionRange(range.start,range.end);
    let inserted=false;
    try { inserted=onSelect(item) === true; }
    catch (error) { onError(error.message || '这处引用暂时无法插入。'); }
    finally {
      selecting=false;
      if (!inserted) { input.value=note; input.setSelectionRange(start,end); }
    }
    if (inserted) { close(); dismissed=null; }
    else refresh();
    return inserted;
  }
  function handleKeydown(event) {
    if (isComposing(event)) return false;
    // Re-check the caret before handling a key; source changes can invalidate
    // a previously rendered candidate without changing the textarea.
    if (!range) refresh();
    if (!range) return false;
    if (!['ArrowDown','ArrowUp','Enter','Escape'].includes(event.key)) return false;
    event.preventDefault(); event.stopPropagation();
    if (event.key === 'Escape') close({dismiss:true});
    else if (event.key === 'Enter') {
      // Ctrl/Cmd Enter selects here too. It never submits while this menu is open.
      const item=candidates.find((candidate) => candidate.key === activeKey);
      if (item) choose(item);
    } else {
      const enabled=candidates.filter((item) => !item.disabled);
      if (enabled.length) {
        let index=enabled.findIndex((item) => item.key === activeKey);
        index=(index+(event.key === 'ArrowDown' ? 1 : -1)+enabled.length)%enabled.length;
        activeKey=enabled[index].key; paintActive(true);
      }
    }
    return true;
  }
  function resumePreviewQuery() {
    if(previewDismissed && dismissed===previewDismissed) {dismissed=null;previewDismissed=null;}
  }
  input.addEventListener('input',() => { dismissed=null;previewDismissed=null;refresh(); });
  for(const event of ['click','focus'])input.addEventListener(event,resumePreviewQuery);
  input.addEventListener('keydown',event=>{if(['ArrowUp','ArrowDown','Enter'].includes(event.key) && !isComposing(event))resumePreviewQuery();});
  for (const event of ['click','keyup','select','focus']) input.addEventListener(event,refresh);
  input.addEventListener('blur',() => { if (!menu.contains(document.activeElement)) close(); });
  input.addEventListener('compositionstart',() => { composing=true; close(); });
  input.addEventListener('compositionend',() => { composing=false; dismissed=null; setTimeout(refresh,0); });
  input.addEventListener('scroll',position);
  document.addEventListener('pointerdown',(event) => { if (event.target !== input && !menu.contains(event.target)) close({dismiss:true}); });
  document.addEventListener('chat-preview-compact',()=>{close({dismiss:true});previewDismissed=dismissed;});
  window.addEventListener('resize',position);
  window.visualViewport?.addEventListener('resize',position);
  window.visualViewport?.addEventListener('scroll',position);
  return {refresh,close,handleKeydown,isComposing,get active() { return !!range; },get candidates() { return candidates; }};
}

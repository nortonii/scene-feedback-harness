// Mount before setupWorkspaceSidebar so the existing drawer owns this page too.
export function setupWorkspaceFolderImport({
  api, isEnabled=()=>true, getInitialPath=()=>'', onImported=()=>{}, onError=()=>{}
}={}) {
  const dialog=document.getElementById('projects-dialog');
  const panel=dialog?.querySelector('.sidebar-panel');
  const home=document.getElementById('sidebar-home') || dialog?.querySelector('[data-sidebar-view="home"]');
  const actions=home?.querySelector('.sidebar-list-heading > div');
  if(!panel || !actions || typeof api!=='function') throw new Error('无法初始化文件夹入口。');

  const make=(tag,className,text)=>{
    const node=document.createElement(tag);
    if(className) node.className=className;
    if(text!==undefined) node.textContent=text;
    return node;
  };
  function button(text,className='') {
    const node=make('button',className,text);node.type='button';return node;
  }
  function folderIcon() {
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('fill','none');
    svg.setAttribute('stroke','currentColor');svg.setAttribute('stroke-width','1.5');
    svg.setAttribute('stroke-linecap','round');svg.setAttribute('stroke-linejoin','round');
    svg.setAttribute('aria-hidden','true');svg.setAttribute('focusable','false');
    const path=document.createElementNS('http://www.w3.org/2000/svg','path');
    path.setAttribute('d','M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10H3V7Z');
    svg.append(path);return svg;
  }

  const launcher=button('');launcher.id='sidebar-open-folder';
  launcher.dataset.sidebarPage='import';launcher.title='打开文件夹';
  launcher.setAttribute('aria-label','打开文件夹');launcher.append(folderIcon());
  actions.insertBefore(launcher,actions.firstChild);

  const page=make('section','sidebar-page sidebar-detail sidebar-folder-import');
  page.id='sidebar-folder-import';page.dataset.sidebarView='import';page.hidden=true;page.inert=true;
  page.setAttribute('aria-labelledby','folder-import-title');
  const heading=make('div','sidebar-page-heading');
  const back=button('‹ 返回场景');back.dataset.sidebarHome='';
  const title=make('h2','','打开文件夹');title.id='folder-import-title';heading.append(back,title);
  const body=make('div','sidebar-page-body');
  const help=make('p','folder-import-help','浏览工作台服务所在机器的文件夹；局域网访问时，这里显示的是服务机器的目录。');
  help.id='folder-import-help';
  const form=make('form','folder-import-form');
  const label=make('label','','文件夹路径');label.htmlFor='folder-import-path';
  const pathRow=make('div','folder-import-path-row');
  const input=make('input');input.type='text';input.id='folder-import-path';
  input.autocomplete='off';input.spellcheck=false;input.placeholder='输入或浏览文件夹路径';
  input.setAttribute('aria-describedby','folder-import-help folder-import-description');
  const browse=button('前往','folder-import-browse');browse.id='folder-import-browse';
  browse.title='浏览输入的路径';pathRow.append(input,browse);
  const navigation=make('div','folder-import-navigation');
  const up=button('↑ 上一级','folder-import-up');up.id='folder-import-up';
  const browseStatus=make('p','muted folder-import-browse-status');
  browseStatus.id='folder-import-browse-status';browseStatus.setAttribute('role','status');
  navigation.append(up,browseStatus);
  const list=make('ul','folder-import-directory-list');list.id='folder-import-directories';
  list.setAttribute('aria-label','子文件夹');
  const empty=make('p','muted folder-import-empty','选择要打开的文件夹。');
  const description=make('p','folder-import-description','打开后，目录中所有已准备好的实例会加入场景列表。');
  description.id='folder-import-description';
  const submit=button('打开并导入','folder-import-submit');submit.type='submit';submit.id='folder-import-submit';
  const resultStatus=make('p','folder-import-result');resultStatus.id='folder-import-result';
  resultStatus.setAttribute('role','status');
  const errorDetails=make('details','folder-import-errors');errorDetails.hidden=true;
  const errorSummary=make('summary');const errorList=make('ul');errorDetails.append(errorSummary,errorList);
  form.append(label,pathRow,navigation,list,empty,description,submit,resultStatus,errorDetails);
  body.append(help,form);page.append(heading,body);panel.append(page);

  const storageKey='workspace-folder-import-pending-v1';
  const pending=new Map();
  try {
    const saved=JSON.parse(sessionStorage.getItem(storageKey) || '[]');
    if(Array.isArray(saved)) for(const entry of saved) {
      if(Array.isArray(entry) && typeof entry[0]==='string' && /^[a-f0-9]{32}$/.test(entry[1])) pending.set(entry[0],entry[1]);
    }
  } catch { /* Private browsing may make session storage unavailable. */ }
  function savePending() {
    try {sessionStorage.setItem(storageKey,JSON.stringify([...pending]));} catch {}
  }
  function requestId() {
    if(globalThis.crypto?.randomUUID) return crypto.randomUUID().replaceAll('-','');
    const bytes=new Uint8Array(16);
    if(globalThis.crypto?.getRandomValues) crypto.getRandomValues(bytes);
    else for(let index=0;index<bytes.length;index++) bytes[index]=Math.floor(Math.random()*256);
    return [...bytes].map(value=>value.toString(16).padStart(2,'0')).join('');
  }
  let currentPath='',parentPath=null,directories=[],browseEpoch=0;
  let loading=false,importing=false,loaded=false,active=false;
  function enabled() {return !!isEnabled();}
  function notifyError(error) {
    try {onError(error.message || String(error),error);} catch {}
  }
  function clearResult() {
    resultStatus.textContent='';resultStatus.classList.remove('is-error');
    errorDetails.hidden=true;errorDetails.open=false;errorList.replaceChildren();
  }
  function renderControls() {
    const available=enabled(),busy=loading || importing,path=input.value.trim();
    launcher.hidden=!available;launcher.disabled=importing;
    input.disabled=!available || importing;
    browse.disabled=!available || busy;
    up.disabled=!available || busy || !parentPath || path!==currentPath;
    submit.disabled=!available || busy || !path;
    submit.textContent=importing?'正在打开…':'打开并导入';
    page.setAttribute('aria-busy',String(busy));
    for(const entry of list.querySelectorAll('button')) entry.disabled=!available || busy;
  }
  function renderDirectories() {
    list.replaceChildren();
    const matches=input.value.trim()===currentPath;
    if(matches) for(const directory of directories) {
      if(typeof directory?.name!=='string' || typeof directory?.path!=='string') continue;
      const item=make('li');const entry=button('','folder-import-directory');
      entry.append(folderIcon(),make('span','folder-import-directory-name',directory.name));
      const arrow=make('span','folder-import-directory-arrow','›');arrow.setAttribute('aria-hidden','true');
      entry.append(arrow);entry.title=directory.path;
      entry.addEventListener('click',()=>load(directory.path));item.append(entry);list.append(item);
    }
    empty.hidden=!!list.children.length || loading;
    empty.textContent=matches && loaded?'这个文件夹没有子文件夹。':'点击「前往」浏览这个路径，或直接打开并导入。';
    renderControls();
  }
  async function load(path) {
    if(importing || !enabled()) {renderControls();return null;}
    const chosen=path===undefined ? input.value.trim() || String(getInitialPath() || '').trim() : String(path || '').trim();
    const ticket=++browseEpoch;
    input.value=chosen;loading=true;clearResult();browseStatus.classList.remove('is-error');
    browseStatus.textContent='正在读取…';renderDirectories();
    try {
      const result=await api('/api/projects/folders',{method:'POST',body:chosen?{path:chosen}:{}});
      if(ticket!==browseEpoch) return null;
      if(typeof result.path!=='string') throw new Error('服务没有返回可浏览的文件夹路径。');
      currentPath=result.path;parentPath=typeof result.parent==='string'?result.parent:null;
      directories=Array.isArray(result.directories)?result.directories:[];
      input.value=currentPath;loaded=true;
      browseStatus.textContent=result.truncated?'子文件夹较多，仅显示部分；可输入完整路径前往。':'';
      return result;
    } catch(error) {
      if(ticket!==browseEpoch) return null;
      browseStatus.textContent='无法浏览：'+(error.message || String(error));
      browseStatus.classList.add('is-error');notifyError(error);return null;
    } finally {
      if(ticket===browseEpoch) {loading=false;renderDirectories();}
    }
  }
  function showResult(result) {
    const imported=Array.isArray(result.imported)?result.imported:[];
    const skipped=Array.isArray(result.skipped)?result.skipped:[];
    const errors=Array.isArray(result.errors)?result.errors:[];
    const details=[...errors,...skipped.map(item=>({...item,error:item.reason || '已跳过。'}))];
    clearResult();
    if(!imported.length && !skipped.length && !errors.length) {
      resultStatus.textContent='这个文件夹中没有可导入的已准备好实例。';
    } else {
      resultStatus.textContent=`已导入 ${imported.length} · 已跳过 ${skipped.length} · 失败 ${errors.length}`;
      if(!imported.length && skipped.length && !errors.length) resultStatus.textContent+='。没有新增场景。';
    }
    if(result.truncated)resultStatus.textContent+='。扫描达到边界，尚未遍历全部目录；请选择更具体的文件夹继续。';
    if(details.length) {
      errorDetails.hidden=false;errorSummary.textContent=`查看 ${details.length} 项跳过与失败详情`;
      for(const error of details.slice(0,100)) {
        const item=make('li');
        if(typeof error?.path==='string') item.append(make('strong','',error.path));
        item.append(make('span','',typeof error?.error==='string'?error.error:'导入失败。'));
        errorList.append(item);
      }
      if(details.length>100)errorList.append(make('li','','仅展示前 100 项；可选择更具体的文件夹继续。'));
    }
    return errors.length;
  }
  async function importFolder() {
    const path=input.value.trim();
    if(importing || loading || !enabled() || !path) {renderControls();return null;}
    if(!pending.has(path)) {pending.set(path,requestId());savePending();}
    const id=pending.get(path);++browseEpoch;importing=true;clearResult();
    resultStatus.textContent='正在查找并导入已准备好的实例…';renderControls();
    try {
      let result;
      try {
        result=await api('/api/projects/import-folder',{method:'POST',body:{path,request_id:id}});
        if(!showResult(result)) {pending.delete(path);savePending();}
      } catch(error) {
        resultStatus.textContent='导入结果未确认：'+(error.message || String(error))+'。再次点击会重试同一次请求。';
        resultStatus.classList.add('is-error');notifyError(error);return null;
      }
      try {await onImported(result);} catch(error) {
        resultStatus.textContent+='。场景列表刷新失败，请刷新列表。';notifyError(error);
      }
      return result;
    } finally {importing=false;renderControls();}
  }
  form.addEventListener('submit',event=>{event.preventDefault();importFolder();});
  browse.addEventListener('click',()=>load(input.value.trim()));
  up.addEventListener('click',()=>{if(parentPath) load(parentPath);});
  input.addEventListener('input',()=>{
    // Typing a new destination invalidates the old browse response immediately.
    ++browseEpoch;loading=false;browseStatus.textContent='';browseStatus.classList.remove('is-error');
    clearResult();renderDirectories();
  });
  function onPage(value) {
    active=value==='import';
    if(!active) {
      ++browseEpoch;
      if(loading) {loading=false;browseStatus.textContent='';renderDirectories();}
      else renderControls();
      return;
    }
    renderControls();
    if(!loaded && !loading && !importing) return load();
  }
  renderControls();
  return {load,open:load,importFolder,onPage,
    refresh:()=>active?load():renderControls(),refreshAvailability:renderControls};
}

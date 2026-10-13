const STORAGE_KEY='scene-feedback-project-folder-v1';
function folderPath(value) {
  if(typeof value!=='string' || !value.startsWith('/') || value.includes('\0') || value.split('/').some(part=>part==='.' || part==='..'))return null;
  return value.replace(/\/+$/,'') || '/';
}

export function setupProjectFolderScope({onChange}) {
  let path=null;
  const read=()=>{try{return folderPath(JSON.parse(localStorage.getItem(STORAGE_KEY)));}catch{return null;}};
  path=read();
  const bar=document.createElement('div');bar.id='project-folder-scope';bar.className='project-folder-scope';
  const name=document.createElement('span');name.id='project-folder-name';
  const all=document.createElement('button');all.type='button';all.id='project-folder-all';all.textContent='全部场景';
  all.title='显示其他文件夹中已导入的场景';bar.append(name,all);
  document.querySelector('.sidebar-list-heading').after(bar);
  function refresh({disabled=false}={}) {
    bar.hidden=path===null;all.disabled=disabled;
    name.textContent=path===null ? '' : path.split('/').filter(Boolean).slice(-2).join('/') || '/';
    name.title=path || '';bar.setAttribute('aria-label',path ? '当前文件夹：'+path : '全部场景');
  }
  function change(value) {
    path=value;
    try {if(path===null)localStorage.removeItem(STORAGE_KEY);else localStorage.setItem(STORAGE_KEY,JSON.stringify(path));}
    catch { /* Keep the folder view usable when browser storage is unavailable. */ }
    refresh();onChange();
  }
  function set(value) {const next=folderPath(value);if(next===null)return false;change(next);return true;}
  function clear() {change(null);}
  all.addEventListener('click',clear);
  window.addEventListener('storage',event=>{
    if(event.key!==STORAGE_KEY && event.key!==null)return;
    const next=read();if(next===path)return;
    path=next;refresh();onChange();
  });
  function filter(projects) {
    const seen=new Set();
    return projects.filter(project=>{
      if(!project || typeof project.project_id!=='string' || seen.has(project.project_id))return false;
      const source=project.project_dir;
      if(path!==null && !(typeof source==='string' && (source===path || source.startsWith(path==='/' ? '/' : path+'/'))))return false;
      seen.add(project.project_id);return true;
    });
  }
  refresh();
  return {set,clear,filter,refresh,get path(){return path;}};
}

// Hide only immersive chrome; the scene and its evidence remain untouched.
export function setupSceneToolbar() {
  const root=document.documentElement,head=document.querySelector('.scene-pane > .pane-head');
  const toggle=document.getElementById('scene-toolbar-toggle');
  head.id='scene-toolbar';
  let collapsed=false;
  try {collapsed=localStorage.getItem('astra-immersive-tools-collapsed')==='true';} catch {}
  function refresh() {
    const immersive=root.dataset.layout==='immersive',hidden=immersive && collapsed;
    root.dataset.sceneToolsCollapsed=String(hidden);
    toggle.hidden=!immersive;toggle.setAttribute('aria-expanded',String(!hidden));
    toggle.title=hidden?'展开场景工具栏':'收起场景工具栏';toggle.setAttribute('aria-label',toggle.title);
    if(hidden) {
      for(const menu of head.querySelectorAll('details[open]')) menu.open=false;
      if(head.contains(document.activeElement))toggle.focus({preventScroll:true});
    }
    head.inert=hidden;head.setAttribute('aria-hidden',String(hidden));
  }
  toggle.addEventListener('click',()=>{
    if(root.dataset.layout!=='immersive')return;
    collapsed=!collapsed;refresh();
    try {localStorage.setItem('astra-immersive-tools-collapsed',String(collapsed));}catch{}
  });
  refresh();
  return {refresh,focus:()=>toggle.focus({preventScroll:true})};
}

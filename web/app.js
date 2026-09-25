import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

const $ = (selector) => document.querySelector(selector);
const id = (name) => document.getElementById(name);
const vec = (array) => new THREE.Vector3(...array);
const arr = (vector) => [vector.x, vector.y, vector.z].map((n) => Number(n.toFixed(5)));
const fmt = (n) => Number.isFinite(n) ? Number(n.toFixed(2)).toString() : '—';
const clampSize = (n) => Math.max(0.001, Number(n) || 0.001);

const ui = {
  viewport: id('viewport'), hint: id('viewport-hint'), toast: id('toast'),
  objectList: id('object-list'), objectCount: id('object-count'),
  inspectorTitle: id('inspector-title'), inspectorSubtitle: id('inspector-subtitle'),
  selectionEmpty: id('selection-empty'), selectionControls: id('selection-controls'),
  objectId: id('object-id'), objectType: id('object-type'), objectDimensions: id('object-dimensions'),
  targetEnabled: id('target-enabled'), anchor: id('anchor-select'),
  annotationList: id('annotation-list'), annotationCount: id('annotation-count'),
  sessionPill: id('session-pill'), submit: id('submit-button'), cancel: id('cancel-button'),
  note: id('feedback-note'), caption: id('submit-caption'),
  size: ['x','y','z'].map((axis) => id(`size-${axis}`)),
  position: ['x','y','z'].map((axis) => id(`position-${axis}`))
};

const state = {
  sceneRevision: null, sceneObjects: [], objectNodes: new Map(),
  sessionId: null, sessionStatus: 'connecting', sceneStale: false,
  selectedId: null, mode: 'select', targets: new Map(), lines: [],
  pendingPoint: null, nextLineId: 1, toastTimer: null
};

const threeScene = new THREE.Scene();
threeScene.background = new THREE.Color('#1b2731');
threeScene.fog = new THREE.Fog('#1b2731', 14, 36);
const camera = new THREE.PerspectiveCamera(44, 1, 0.01, 2000);
camera.up.set(0,0,1);
camera.position.set(5.5,-8.5,6.5);
const renderer = new THREE.WebGLRenderer({antialias:true, preserveDrawingBuffer:true});
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.65;
renderer.domElement.tabIndex = 0;
ui.viewport.appendChild(renderer.domElement);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.target.set(0,0,0.65);
controls.minDistance = 0.3;
controls.maxDistance = 250;
controls.screenSpacePanning = false;
controls.update();

threeScene.add(new THREE.HemisphereLight(0xdcefff, 0x7e8a93, 2.3));
const keyLight = new THREE.DirectionalLight(0xffedda, 3.5);
keyLight.position.set(5,-4,10); threeScene.add(keyLight);
const fillLight = new THREE.DirectionalLight(0x9bc6ea, 1.8);
fillLight.position.set(-5,6,5); threeScene.add(fillLight);

const grid = new THREE.GridHelper(30, 30, 0x6b8493, 0x465966);
grid.rotateX(Math.PI/2);
grid.position.z = -0.003;
grid.material.transparent = true;
grid.material.opacity = 0.4;
threeScene.add(grid);
const ground = new THREE.Mesh(new THREE.PlaneGeometry(200,200), new THREE.MeshBasicMaterial({color:0x1b2832,transparent:true,opacity:0.48,depthWrite:false,side:THREE.DoubleSide}));
ground.position.z = -0.008;
threeScene.add(ground);

const objectLayer = new THREE.Group();
const feedbackLayer = new THREE.Group();
threeScene.add(objectLayer, feedbackLayer);
const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
const groundPlane = new THREE.Plane(new THREE.Vector3(0,0,1), 0);
const gltfLoader = new GLTFLoader();
let selectionHelper = null;
let targetHelper = null;
let previewLine = null;
let pointerDown = null;

function announce(message, error=false) {
  clearTimeout(state.toastTimer);
  ui.toast.textContent = message;
  ui.toast.classList.toggle('error', error);
  ui.toast.classList.add('show');
  state.toastTimer = setTimeout(() => ui.toast.classList.remove('show'), 4000);
}

async function api(path, options={}) {
  const init = {...options};
  if (options.body && typeof options.body !== 'string') {
    init.body = JSON.stringify(options.body);
    init.headers = {'Content-Type':'application/json', ...(options.headers || {})};
  }
  const response = await fetch(path, init);
  let body;
  try { body = await response.json(); } catch { body = {}; }
  if (!response.ok) throw new Error(body.error || body.detail || `HTTP ${response.status}`);
  return body;
}

function getObject(idValue) { return state.sceneObjects.find((item) => item.id === idValue); }
function setHint(text) { ui.hint.querySelector('span:last-child').textContent = text; }
function editable() { return state.sessionStatus === 'open' && !state.sceneStale; }

function setSessionStatus(status) {
  state.sessionStatus = status;
  ui.sessionPill.textContent = ({open:'会话进行中',submitted:'已提交',cancelled:'已取消',connecting:'连接中',error:'连接失败'})[status] || status.toUpperCase();
  ui.sessionPill.className = `session-pill ${status}`;
  const closed = status !== 'open' || state.sceneStale;
  ui.submit.disabled = closed;
  ui.cancel.disabled = closed;
  ui.note.disabled = closed;
  ui.targetEnabled.disabled = closed;
  [...ui.size, ...ui.position, ui.anchor, id('reset-target'), id('clear-annotations')].forEach((el) => el.disabled = closed);
  if (status === 'submitted') {
    ui.caption.textContent = '反馈已交回 Codex。可以关闭此页面。';
    setHint('反馈已提交，Codex 可以继续处理');
  } else if (status === 'cancelled') {
    ui.caption.textContent = '本次反馈会话已取消。';
    setHint('会话已取消');
  } else if (state.sceneStale) {
    ui.caption.textContent = '场景版本已更新。请刷新页面后重新标注。';
  } else {
    ui.caption.textContent = '提交后，反馈会返回等待中的 Codex 工具调用。';
  }
}

async function ensureSession() {
  const params = new URLSearchParams(location.search);
  const existing = params.get('session_id') || params.get('session');
  try {
    let session;
    if (existing) {
      session = await api(`/api/sessions/${encodeURIComponent(existing)}`);
    } else {
      session = await api('/api/sessions', {method:'POST', body:{}});
      params.set('session_id', session.session_id);
      history.replaceState(null, '', `${location.pathname}?${params.toString()}`);
    }
    state.sessionId = session.session_id;
    setSessionStatus(session.status || 'open');
  } catch (error) {
    setSessionStatus('error');
    announce(`会话连接失败：${error.message}`, true);
  }
}

function makePrimitive(item) {
  let geometry;
  if (item.type === 'sphere') geometry = new THREE.SphereGeometry(0.5, 32, 20);
  else if (item.type === 'cylinder') {
    geometry = new THREE.CylinderGeometry(0.5,0.5,1,32);
    geometry.rotateX(Math.PI/2);
  } else geometry = new THREE.BoxGeometry(1,1,1);
  const material = new THREE.MeshStandardMaterial({color:item.color || '#9aaeb8',roughness:0.66,metalness:0.08});
  const mesh = new THREE.Mesh(geometry,material);
  const scale = (item.size || [1,1,1]).map(clampSize);
  mesh.scale.set(...scale);
  return mesh;
}

async function addObject(item) {
  const root = new THREE.Group();
  root.userData.objectId = item.id;
  root.position.set(...(item.position || [0,0,0]));
  root.rotation.set(...(item.rotation || [0,0,0]));
  objectLayer.add(root);
  state.objectNodes.set(item.id, root);
  if (item.type === 'model') {
    const placeholder = new THREE.Mesh(new THREE.BoxGeometry(0.5,0.5,0.5), new THREE.MeshBasicMaterial({color:0x6694a8,wireframe:true}));
    root.add(placeholder);
    try {
      if (!item.url) throw new Error('缺少 GLB 地址');
      const gltf = await gltfLoader.loadAsync(item.url);
      if (state.objectNodes.get(item.id) !== root) return;
      root.remove(placeholder);
      gltf.scene.scale.set(...(item.size || [1,1,1]));
      root.add(gltf.scene);
      root.userData.loaded = true;
      if (state.selectedId === item.id) renderSelection();
      frameAllIfInitial();
    } catch (error) {
      placeholder.material.color.set(0xc47472);
      root.userData.loaded = true;
      root.userData.loadError = error.message;
      announce(`模型 ${item.name || item.id} 加载失败：${error.message}`, true);
      frameAllIfInitial();
    }
  } else {
    root.add(makePrimitive(item));
    root.userData.loaded = true;
  }
}

let firstFramePending = true;
function frameAllIfInitial() {
  if (!firstFramePending) return;
  if (state.sceneObjects.some((item) => item.type === 'model' && !state.objectNodes.get(item.id)?.userData.loaded)) return;
  firstFramePending = false;
  frameAll();
}

async function loadScene() {
  const data = await api('/api/scene');
  state.sceneRevision = data.revision;
  state.sceneObjects = data.objects || [];
  objectLayer.clear(); state.objectNodes.clear();
  state.selectedId = null;
  state.targets.clear(); state.lines = []; state.pendingPoint = null;
  firstFramePending = true;
  const nodes = state.sceneObjects.map(addObject);
  await Promise.allSettled(nodes);
  frameAllIfInitial();
  renderObjectList(); renderInspector(); renderAnnotations(); renderSelection();
  id('revision-label').textContent = `版本 ${data.revision}`;
  id('object-count').textContent = String(state.sceneObjects.length);
  id('scene-name').textContent = data.name || '当前场景';
  id('viewport-scene-title').textContent = data.name || '场景总览';
}

function objectBox(objectId) {
  const node = state.objectNodes.get(objectId);
  if (!node) return null;
  const box = new THREE.Box3().setFromObject(node);
  return box.isEmpty() ? null : box;
}

function objectBoxData(objectId) {
  const box = objectBox(objectId);
  if (!box) return {center:[0,0,0], size:[1,1,1]};
  return {center:arr(box.getCenter(new THREE.Vector3())), size:arr(box.getSize(new THREE.Vector3()))};
}

function frameBox(box) {
  if (!box || box.isEmpty()) return;
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const radius = Math.max(size.length()/2, 0.45);
  const fov = camera.fov * Math.PI / 180;
  const distance = Math.max(radius / Math.sin(fov/2) * 1.25, 2.5);
  const direction = new THREE.Vector3(1,-1.4,0.95).normalize();
  camera.position.copy(center).addScaledVector(direction,distance);
  controls.target.copy(center);
  camera.near = Math.max(distance/1000,0.005);
  camera.far = Math.max(distance*100,100);
  camera.updateProjectionMatrix();
  controls.update();
}

function frameAll() {
  const box = new THREE.Box3();
  for (const root of state.objectNodes.values()) box.expandByObject(root);
  if (box.isEmpty()) box.setFromCenterAndSize(new THREE.Vector3(0,0,0.5),new THREE.Vector3(3,3,2));
  frameBox(box);
}

function renderObjectList() {
  ui.objectList.replaceChildren();
  if (!state.sceneObjects.length) {
    const empty = document.createElement('div'); empty.className='empty-state'; empty.textContent='场景中还没有对象。'; ui.objectList.append(empty); return;
  }
  for (const item of state.sceneObjects) {
    const button = document.createElement('button'); button.type='button';
    button.className = `object-item ${state.selectedId===item.id?'active':''}`;
    button.dataset.objectId = item.id;
    const swatch = document.createElement('span'); swatch.className='object-color'; swatch.style.backgroundColor=item.color || '#91adbb';
    const name = document.createElement('strong'); name.textContent=item.name || item.id;
    const type = document.createElement('small'); type.textContent=({box:'方盒',sphere:'球体',cylinder:'圆柱',model:'模型'})[item.type] || item.type;
    button.append(swatch,name,type);
    button.addEventListener('click', () => selectObject(item.id));
    ui.objectList.append(button);
  }
}

function selectObject(objectId) {
  if (!getObject(objectId)) return;
  state.selectedId=objectId;
  if (state.mode==='box' && editable()) ensureTarget(objectId);
  renderObjectList(); renderInspector(); renderSelection(); renderAnnotations();
  setHint(state.mode==='line'?'在表面或网格上点击两个点绘制辅助线':state.mode==='box'?'在右侧修改目标包围框':'已选择对象，可绘制辅助线或设置目标框');
}

function renderInspector() {
  const item = getObject(state.selectedId);
  ui.selectionEmpty.classList.toggle('hidden',!!item);
  ui.selectionControls.classList.toggle('hidden',!item);
  if (!item) {
    ui.inspectorTitle.textContent='未选择对象';
    ui.inspectorSubtitle.textContent='在视口或左侧列表中选择一个对象。';
    return;
  }
  const box = objectBoxData(item.id);
  ui.inspectorTitle.textContent=item.name || item.id;
  ui.inspectorSubtitle.textContent='对照当前几何，给 Codex 一个明确的目标。';
  ui.objectId.textContent=item.id;
  ui.objectType.textContent=({box:'方盒',sphere:'球体',cylinder:'圆柱',model:'GLB 模型'})[item.type] || item.type;
  ui.objectDimensions.textContent=box.size.map(fmt).join(' × ');
  const target=state.targets.get(item.id);
  const dimensions=target?.size || box.size;
  const center=target?.center || box.center;
  ui.targetEnabled.checked=!!target;
  ui.anchor.value=target?.anchor || 'center';
  ui.size.forEach((input,index) => input.value=fmt(dimensions[index]));
  ui.position.forEach((input,index) => input.value=fmt(center[index]));
}

function ensureTarget(objectId) {
  if (!state.targets.has(objectId)) {
    const box=objectBoxData(objectId);
    state.targets.set(objectId,{center:[...box.center],size:box.size.map(clampSize),anchor:'center'});
  }
  return state.targets.get(objectId);
}

function updateTargetFromInputs(changedField,axisIndex) {
  if (!state.selectedId || !editable()) return;
  const input=(changedField==='size'?ui.size:ui.position)[axisIndex];
  if (!input.value.trim()) return;
  const target=ensureTarget(state.selectedId);
  const oldBottom=target.center[2]-target.size[2]/2;
  const value=Number(input.value);
  if (!Number.isFinite(value)) return;
  if (changedField==='size') target.size[axisIndex]=clampSize(value);
  else target.center[axisIndex]=value;
  if (changedField==='size' && axisIndex===2 && target.anchor==='bottom') {
    target.center[2]=oldBottom+target.size[2]/2;
    ui.position[2].value=fmt(target.center[2]);
  }
  ui.targetEnabled.checked=true;
  renderSelection(); renderAnnotations();
}

function clearOverlay(ref) {
  if (!ref) return null;
  feedbackLayer.remove(ref);
  ref.traverse((child) => {
    if (child.geometry) child.geometry.dispose();
    if (child.material) {
      const materials=Array.isArray(child.material)?child.material:[child.material];
      materials.forEach((material)=>material.dispose());
    }
  });
  return null;
}

function renderSelection() {
  selectionHelper=clearOverlay(selectionHelper);
  targetHelper=clearOverlay(targetHelper);
  if (!state.selectedId) return;
  const box=objectBox(state.selectedId);
  if (box) {
    selectionHelper=new THREE.Box3Helper(box,0x91d5df);
    selectionHelper.material.transparent=true;
    selectionHelper.material.opacity=.84;
    feedbackLayer.add(selectionHelper);
  }
  const target=state.targets.get(state.selectedId);
  if (target) {
    targetHelper=new THREE.Group();
    const edge=new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(1,1,1)),new THREE.LineBasicMaterial({color:0xffb76a,transparent:true,opacity:1,depthTest:false}));
    edge.scale.set(...target.size);
    targetHelper.position.set(...target.center);
    targetHelper.add(edge);
    const dotGeometry=new THREE.SphereGeometry(Math.max(Math.min(...target.size)*.025,.015),8,8);
    for (const sx of [-.5,.5]) for (const sy of [-.5,.5]) for (const sz of [-.5,.5]) {
      const dot=new THREE.Mesh(dotGeometry,new THREE.MeshBasicMaterial({color:0xffcb88,depthTest:false}));
      dot.position.set(sx*target.size[0],sy*target.size[1],sz*target.size[2]);
      targetHelper.add(dot);
    }
    feedbackLayer.add(targetHelper);
  }
}

function makeLineVisual(start,end,preview=false) {
  const group=new THREE.Group();
  const points=[vec(start),vec(end)];
  const geometry=new THREE.BufferGeometry().setFromPoints(points);
  const line=new THREE.Line(geometry,new THREE.LineBasicMaterial({color:preview?0x92dce8:0xffbc78,transparent:true,opacity:preview?.6:1,depthTest:false}));
  group.add(line);
  const dotGeometry=new THREE.SphereGeometry(.045,10,10);
  points.forEach((point)=>{const dot=new THREE.Mesh(dotGeometry,new THREE.MeshBasicMaterial({color:preview?0x9bdce7:0xffc58c,depthTest:false}));dot.position.copy(point);group.add(dot);});
  group.renderOrder=20;
  return group;
}

function renderLines() {
  [...feedbackLayer.children].filter((node)=>node.userData.annotationLine).forEach(clearOverlay);
  for (const line of state.lines) {
    const visual=makeLineVisual(line.start,line.end);
    visual.userData.annotationLine=true;
    feedbackLayer.add(visual);
  }
}

function renderAnnotations() {
  ui.annotationList.replaceChildren();
  const count=state.targets.size+state.lines.length;
  ui.annotationCount.textContent=String(count);
  if (!count) {
    const empty=document.createElement('div'); empty.className='feedback-empty'; empty.textContent='辅助线和目标包围框会显示在这里。'; ui.annotationList.append(empty); return;
  }
  for (const [objectId,target] of state.targets) {
    const obj=getObject(objectId);
    addAnnotationRow('□',`${obj?.name || objectId} · 目标框`,`${target.size.map(fmt).join(' × ')} · ${target.anchor==='bottom'?'底部固定':'中心固定'}`,()=>{state.targets.delete(objectId);renderInspector();renderSelection();renderAnnotations();});
  }
  state.lines.forEach((line,index)=>{
    const obj=getObject(line.object_id);
    addAnnotationRow('╱',`${obj?.name || line.object_id} · 辅助线 ${index+1}`,`${line.start.map(fmt).join(', ')} → ${line.end.map(fmt).join(', ')}`,()=>{state.lines=state.lines.filter((item)=>item.id!==line.id);renderLines();renderAnnotations();});
  });
}

function addAnnotationRow(symbol,title,subtitle,onRemove) {
  const row=document.createElement('div'); row.className='annotation-item';
  const icon=document.createElement('span'); icon.className='annotation-icon'; icon.textContent=symbol;
  const text=document.createElement('div');
  const strong=document.createElement('strong'); strong.textContent=title;
  const small=document.createElement('small'); small.textContent=subtitle;
  text.append(strong,small);
  const remove=document.createElement('button'); remove.type='button'; remove.className='remove-annotation'; remove.textContent='×'; remove.title='删除标注'; remove.disabled=!editable(); remove.addEventListener('click',onRemove);
  row.append(icon,text,remove); ui.annotationList.append(row);
}

function setMode(mode) {
  if (!['select','line','box'].includes(mode)) return;
  state.mode=mode;
  state.pendingPoint=null;
  previewLine=clearOverlay(previewLine);
  document.querySelectorAll('.tool-button').forEach((button)=>button.classList.toggle('active',button.dataset.tool===mode));
  ui.viewport.style.cursor=mode==='line'?'crosshair':'grab';
  if (mode==='box' && state.selectedId && editable()) ensureTarget(state.selectedId);
  renderInspector(); renderSelection(); renderAnnotations();
  setHint(({select:'点击对象以查看和标注',line:state.selectedId?'点击两个表面或网格点绘制辅助线':'先选择一个对象，再绘制辅助线',box:state.selectedId?'在右侧修改目标包围框':'先选择一个对象，再设置目标包围框'})[mode]);
}

function setPointerFromEvent(event) {
  const bounds=renderer.domElement.getBoundingClientRect();
  pointer.set(((event.clientX-bounds.left)/bounds.width)*2-1,-((event.clientY-bounds.top)/bounds.height)*2+1);
  raycaster.setFromCamera(pointer,camera);
}

function pick(event) {
  setPointerFromEvent(event);
  const intersections=raycaster.intersectObjects([...state.objectNodes.values()],true);
  for (const hit of intersections) {
    let node=hit.object;
    while (node && !node.userData.objectId) node=node.parent;
    if (node?.userData.objectId) return {objectId:node.userData.objectId,point:hit.point};
  }
  const groundPoint=new THREE.Vector3();
  const onGround=raycaster.ray.intersectPlane(groundPlane,groundPoint);
  return {objectId:null,point:onGround ? groundPoint : null};
}

function handleCanvasClick(event) {
  const hit=pick(event);
  if (state.mode==='line') {
    if (!editable()) return;
    if (!state.selectedId) {announce('请先选择要关联的对象。',true);return;}
    if (!hit.point) {announce('请点击可见表面或地面网格。',true);return;}
    const point=arr(hit.point);
    if (!state.pendingPoint) {
      state.pendingPoint=point;
      setHint('点击第二个点完成辅助线 · Esc 取消');
    } else {
      if (vec(state.pendingPoint).distanceTo(vec(point))<.005) return;
      state.lines.push({id:state.nextLineId++,object_id:state.selectedId,start:state.pendingPoint,end:point});
      state.pendingPoint=null;
      previewLine=clearOverlay(previewLine);
      renderLines(); renderAnnotations();
      setHint('辅助线已添加。继续点击可绘制下一条');
    }
  } else if (hit.objectId) selectObject(hit.objectId);
}

function updateHover(event) {
  if (state.mode==='line' && state.pendingPoint) {
    const hit=pick(event);
    previewLine=clearOverlay(previewLine);
    if (hit.point) {
      previewLine=makeLineVisual(state.pendingPoint,arr(hit.point),true);
      feedbackLayer.add(previewLine);
    }
  }
  const hit=pick(event);
  id('cursor-label').textContent=hit.point ? `X ${fmt(hit.point.x)}   Y ${fmt(hit.point.y)}   Z ${fmt(hit.point.z)}` : 'X —   Y —   Z —';
}

function feedbackPayload() {
  const annotations=[];
  for (const [objectId,target] of state.targets) annotations.push({
    type:'target_box', object_id:objectId, coordinate_frame:'world',
    center:[...target.center], size:[...target.size], anchor:target.anchor,
    source_box:objectBoxData(objectId)
  });
  for (const line of state.lines) annotations.push({
    type:'guide_line', object_id:line.object_id, coordinate_frame:'world',
    start:[...line.start], end:[...line.end]
  });
  const feedback = {
    scene_revision:state.sceneRevision, annotations,
    note:ui.note.value.trim(),
    camera:{position:arr(camera.position),target:arr(controls.target),up:arr(camera.up),fov:camera.fov}
  };
  try {
    const screenshot=renderer.domElement.toDataURL('image/jpeg',0.78);
    // Backend limits decoded screenshots to 4 MiB. Leave visual data out
    // when it could block otherwise valid geometric feedback.
    if (screenshot.startsWith('data:image/jpeg;base64,') && screenshot.length<5_400_000)
      feedback.screenshot_data_url=screenshot;
  } catch { /* Coordinates and camera pose remain usable if image capture fails. */ }
  return feedback;
}

async function submitFeedback() {
  if (!editable() || !state.sessionId) return;
  const payload=feedbackPayload();
  if (!payload.annotations.length && !payload.note) {announce('请添加标注或备注后再提交。',true);return;}
  ui.submit.disabled=true;
  ui.submit.querySelector('span:first-child').textContent='正在提交…';
  try {
    await api(`/api/sessions/${encodeURIComponent(state.sessionId)}/feedback`,{method:'POST',body:payload});
    setSessionStatus('submitted');
    announce('反馈已提交，Codex 正在继续处理。');
  } catch(error) {
    ui.submit.disabled=false;
    announce(`提交失败：${error.message}`,true);
  } finally {
    ui.submit.querySelector('span:first-child').textContent='提交反馈';
  }
}

async function cancelFeedback() {
  if (!editable() || !state.sessionId) return;
  ui.cancel.disabled=true;
  try {
    await api(`/api/sessions/${encodeURIComponent(state.sessionId)}/cancel`,{method:'POST',body:{}});
    setSessionStatus('cancelled');
    announce('反馈会话已取消。');
  } catch(error) {
    ui.cancel.disabled=false;
    announce(`取消失败：${error.message}`,true);
  }
}

function bindEvents() {
  document.querySelectorAll('.tool-button').forEach((button)=>button.addEventListener('click',()=>setMode(button.dataset.tool)));
  ui.targetEnabled.addEventListener('change',()=>{
    if (!state.selectedId || !editable()) return;
    if (ui.targetEnabled.checked) ensureTarget(state.selectedId);
    else state.targets.delete(state.selectedId);
    renderSelection();renderAnnotations();
  });
  ui.size.forEach((input,index)=>input.addEventListener('input',()=>updateTargetFromInputs('size',index)));
  ui.position.forEach((input,index)=>input.addEventListener('input',()=>updateTargetFromInputs('position',index)));
  ui.anchor.addEventListener('change',()=>{
    if (!state.selectedId || !editable()) return;
    ensureTarget(state.selectedId).anchor=ui.anchor.value;
    renderAnnotations();
  });
  id('reset-target').addEventListener('click',()=>{
    if (!state.selectedId || !editable()) return;
    const box=objectBoxData(state.selectedId);
    state.targets.set(state.selectedId,{center:[...box.center],size:box.size.map(clampSize),anchor:'center'});
    renderInspector();renderSelection();renderAnnotations();
  });
  id('clear-annotations').addEventListener('click',()=>{
    if (!editable()) return;
    state.targets.clear();state.lines=[];state.pendingPoint=null;
    previewLine=clearOverlay(previewLine);
    renderLines();renderSelection();renderInspector();renderAnnotations();
  });
  id('copy-id').addEventListener('click',async()=>{
    if (!state.selectedId) return;
    try {await navigator.clipboard.writeText(state.selectedId);announce('对象 ID 已复制。');}
    catch {announce('复制失败，请手动选择 ID。',true);}
  });
  id('frame-button').addEventListener('click',()=>state.selectedId ? frameBox(objectBox(state.selectedId)) : frameAll());
  id('reset-button').addEventListener('click',frameAll);
  id('grid-button').addEventListener('click',()=>{grid.visible=!grid.visible;id('grid-button').classList.toggle('active',grid.visible);});
  ui.submit.addEventListener('click',submitFeedback);
  ui.cancel.addEventListener('click',cancelFeedback);
  id('help-button').addEventListener('click',()=>id('help-dialog').showModal());
  id('close-help').addEventListener('click',()=>id('help-dialog').close());
  renderer.domElement.addEventListener('pointerdown',(event)=>{pointerDown={x:event.clientX,y:event.clientY,button:event.button};});
  renderer.domElement.addEventListener('pointerup',(event)=>{
    if (!pointerDown || pointerDown.button!==0) return;
    const distance=Math.hypot(event.clientX-pointerDown.x,event.clientY-pointerDown.y);
    pointerDown=null;
    if (distance<5) handleCanvasClick(event);
  });
  renderer.domElement.addEventListener('pointermove',updateHover);
  renderer.domElement.addEventListener('pointerleave',()=>{previewLine=clearOverlay(previewLine);id('cursor-label').textContent='X —   Y —   Z —';});
  document.addEventListener('keydown',(event)=>{
    if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLSelectElement) return;
    if (event.key==='1') setMode('select');
    if (event.key==='2') setMode('line');
    if (event.key==='3') setMode('box');
    if (event.key==='Escape') {state.pendingPoint=null;previewLine=clearOverlay(previewLine);setHint('点击两个点绘制辅助线');}
    if (event.key==='Backspace' && state.lines.length && editable()) {event.preventDefault();state.lines.pop();renderLines();renderAnnotations();}
  });
}

function resizeCanvas() {
  const width=ui.viewport.clientWidth;
  const height=ui.viewport.clientHeight;
  if (!width || !height) return;
  camera.aspect=width/height;
  camera.updateProjectionMatrix();
  renderer.setSize(width,height,false);
}
new ResizeObserver(resizeCanvas).observe(ui.viewport);
function animate() {requestAnimationFrame(animate);controls.update();renderer.render(threeScene,camera);}
animate();

async function poll() {
  if (!state.sessionId) return;
  try {
    const [scene,session]=await Promise.all([api('/api/scene'),api(`/api/sessions/${encodeURIComponent(state.sessionId)}`)]);
    if (scene.revision!==state.sceneRevision && !state.sceneStale) {
      state.sceneStale=true;
      setSessionStatus(state.sessionStatus);
      announce('场景已更新。请刷新页面后重新标注。',true);
    }
    if (session.status && session.status!==state.sessionStatus) setSessionStatus(session.status);
  } catch { /* The next poll may recover from a transient network error. */ }
}

bindEvents();
resizeCanvas();
try { await loadScene(); } catch(error) { announce(`场景加载失败：${error.message}`,true); }
await ensureSession();
setInterval(poll,4000);

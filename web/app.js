import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

const id = (name) => document.getElementById(name);
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const array = (value) => [value.x, value.y, value.z].map((n) => Number(n.toFixed(5)));
const labels = {point:'点', rectangle:'方框', line:'线段', arrow:'箭头', text:'文字'};
const glyphs = {point:'●', rectangle:'▢', line:'╱', arrow:'↗', text:'T'};
const circled = ['','①','②','③','④','⑤','⑥','⑦','⑧','⑨'];
const ui = {
  viewport:id('viewport'), sceneStage:id('scene-stage'), sceneCanvas:id('scene-annotations'),
  referenceStage:id('reference-stage'), referenceMedia:id('reference-media'),
  referenceImage:id('reference-image'), referenceCanvas:id('reference-annotations'),
  referenceEmpty:id('reference-empty'), referenceStrip:id('reference-strip'),
  referenceTitle:id('reference-title'), referenceInput:id('reference-input'),
  compareImage:id('compare-image'), compareEnabled:id('compare-enabled'),
  compareOpacity:id('compare-opacity'), opacityValue:id('opacity-value'),
  objectList:id('object-list'), selectionSummary:id('selection-summary'),
  clearSelection:id('clear-selection'), selectedChip:id('selected-chip'),
  annotationList:id('annotation-list'), annotationCount:id('annotation-count'),
  note:id('feedback-note'), submit:id('submit-button'), caption:id('submit-caption'),
  pill:id('session-pill'), toast:id('toast'), sceneHint:id('scene-hint'),
  referenceHint:id('reference-hint'), groupSelect:id('group-select'),
  textEditor:id('text-editor'), annotationText:id('annotation-text')
};
const state = {
  sessionId:null, sessionStatus:'connecting', feedbackCount:0,
  sceneRevision:null, sceneObjects:[], objectNodes:new Map(),
  references:[], activeReferenceId:null, selectedId:null, selectedSceneNode:null,
  annotations:[], mode:'select', groupId:'', drag:null, textPending:null,
  toastTimer:null, submitting:false, uploading:false, firstFrame:true,
  restoredSceneRevision:null, restoredModelUrl:null
};

const threeScene = new THREE.Scene();
threeScene.background = new THREE.Color('#1b2731');
threeScene.fog = new THREE.Fog('#1b2731', 14, 36);
const camera = new THREE.PerspectiveCamera(44, 1, 0.01, 2000);
camera.up.set(0, 0, 1);
camera.position.set(5.5, -8.5, 6.5);
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
controls.target.set(0, 0, 0.65);
controls.minDistance = 0.3;
controls.maxDistance = 250;
controls.screenSpacePanning = false;
controls.update();
threeScene.add(new THREE.HemisphereLight(0xdcefff, 0x7e8a93, 2.3));
const keyLight = new THREE.DirectionalLight(0xffedda, 3.5);
keyLight.position.set(5, -4, 10);
threeScene.add(keyLight);
const fillLight = new THREE.DirectionalLight(0x9bc6ea, 1.8);
fillLight.position.set(-5, 6, 5);
threeScene.add(fillLight);
const grid = new THREE.GridHelper(30, 30, 0x6b8493, 0x465966);
grid.rotateX(Math.PI / 2);
grid.position.z = -0.003;
grid.material.transparent = true;
grid.material.opacity = 0.4;
threeScene.add(grid);
const ground = new THREE.Mesh(
  new THREE.PlaneGeometry(200, 200),
  new THREE.MeshBasicMaterial({color:0x1b2832, transparent:true, opacity:0.48, depthWrite:false, side:THREE.DoubleSide})
);
ground.position.z = -0.008;
threeScene.add(ground);
const objectLayer = new THREE.Group();
const feedbackLayer = new THREE.Group();
threeScene.add(objectLayer, feedbackLayer);
const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
const gltfLoader = new GLTFLoader();
let selectionHelper = null;
let pointerDown = null;

function announce(message, error=false) {
  clearTimeout(state.toastTimer);
  ui.toast.textContent = message;
  ui.toast.classList.toggle('error', error);
  ui.toast.classList.add('show');
  state.toastTimer = setTimeout(() => ui.toast.classList.remove('show'), 4500);
}

async function api(path, options={}) {
  const request = {...options};
  if (options.body && typeof options.body !== 'string') {
    request.body = JSON.stringify(options.body);
    request.headers = {'Content-Type':'application/json', ...(options.headers || {})};
  }
  const response = await fetch(path, request);
  let body;
  try { body = await response.json(); } catch { body = {}; }
  if (!response.ok) throw new Error(body.error || body.detail || 'HTTP ' + response.status);
  return body;
}

function storageKey() { return 'astra-visual-draft:' + state.sessionId; }
function saveDraft() {
  if (!state.sessionId || state.sceneRevision === null) return;
  try {
    localStorage.setItem(storageKey(), JSON.stringify({
      annotations:state.annotations, selectedId:state.selectedId,
      selectedSceneNode:state.selectedSceneNode,
      sceneRevision:state.sceneRevision,
      selectedModelUrl:sceneObject(state.selectedId)?.url || null,
      activeReferenceId:state.activeReferenceId, note:ui.note.value,
      groupId:state.groupId, camera:{position:array(camera.position), target:array(controls.target)}
    }));
  } catch { /* A full or disabled local store should not block feedback. */ }
}
function restoreDraft() {
  try {
    const draft = JSON.parse(localStorage.getItem(storageKey()) || '{}');
    if (Array.isArray(draft.annotations)) state.annotations = draft.annotations.filter((a) => a && labels[a.type] && ['reference','scene'].includes(a.pane) && a.coordinates);
    state.selectedId = typeof draft.selectedId === 'string' ? draft.selectedId : null;
    state.selectedSceneNode = draft.selectedSceneNode && Array.isArray(draft.selectedSceneNode.node_path)
      ? draft.selectedSceneNode : null;
    state.restoredSceneRevision = Number.isInteger(draft.sceneRevision) ? draft.sceneRevision : null;
    state.restoredModelUrl = typeof draft.selectedModelUrl === 'string' ? draft.selectedModelUrl : null;
    state.activeReferenceId = typeof draft.activeReferenceId === 'string' ? draft.activeReferenceId : null;
    state.groupId = typeof draft.groupId === 'string' ? draft.groupId : '';
    ui.groupSelect.value = state.groupId;
    ui.note.value = typeof draft.note === 'string' ? draft.note : '';
    if (draft.camera?.position?.length === 3 && draft.camera?.target?.length === 3) {
      camera.position.set(...draft.camera.position);
      controls.target.set(...draft.camera.target);
      controls.update();
      state.firstFrame = false;
    }
  } catch { /* Ignore a stale or corrupt local draft. */ }
}

function editable() { return state.sessionStatus === 'open' && !state.submitting && !state.uploading; }
function setSession(session) {
  state.sessionId = session.session_id;
  state.sessionStatus = session.status || 'open';
  state.feedbackCount = Number(session.feedback_count) || 0;
  id('feedback-count-label').textContent = '已发 ' + state.feedbackCount + ' 轮';
  ui.pill.textContent = ({open:'工作台已连接', submitted:'已结束', cancelled:'已取消', error:'连接失败', connecting:'连接中'})[state.sessionStatus] || state.sessionStatus;
  ui.pill.className = 'session-pill ' + state.sessionStatus;
  ui.submit.disabled = !editable();
  ui.note.disabled = state.sessionStatus !== 'open';
  ui.referenceInput.disabled = state.sessionStatus !== 'open';
  id('clear-annotations').disabled = state.sessionStatus !== 'open';
  if (state.sessionStatus === 'open' && state.feedbackCount) {
    ui.caption.textContent = '已发送 ' + state.feedbackCount + ' 轮。每次会再次发送当前保留的全部标记；可删除或清空后继续。';
  } else if (state.sessionStatus === 'open') {
    ui.caption.textContent = '每次会发送当前全部标记、原图、场景视角和你的话。标记会保留；不想重复发送可删除或清空。';
  } else {
    ui.caption.textContent = '这个会话已结束。已保存的标记仍可查看。';
  }
}

async function ensureSession() {
  const params = new URLSearchParams(location.search);
  let sessionId = params.get('session_id') || params.get('session');
  let session;
  if (sessionId) {
    session = await api('/api/sessions/' + encodeURIComponent(sessionId));
  } else {
    session = await api('/api/sessions', {method:'POST', body:{}});
    sessionId = session.session_id;
    params.set('session_id', sessionId);
    history.replaceState(null, '', location.pathname + '?' + params.toString());
  }
  setSession(session);
  restoreDraft();
  setReferences(session.reference_images || []);
}

function sceneObject(objectId) { return state.sceneObjects.find((item) => item.id === objectId); }
function makePrimitive(item) {
  let geometry;
  if (item.type === 'sphere') geometry = new THREE.SphereGeometry(0.5, 32, 20);
  else if (item.type === 'cylinder') {
    geometry = new THREE.CylinderGeometry(0.5, 0.5, 1, 32);
    geometry.rotateX(Math.PI / 2);
  } else geometry = new THREE.BoxGeometry(1, 1, 1);
  const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({
    color:item.color || '#9aaeb8', roughness:0.66, metalness:0.08
  }));
  mesh.scale.set(...(item.size || [1,1,1]).map((n) => Math.max(0.001, Number(n) || 0.001)));
  return mesh;
}

async function addObject(item) {
  const root = new THREE.Group();
  root.userData.objectId = item.id;
  root.position.set(...(item.position || [0,0,0]));
  root.rotation.set(...(item.rotation || [0,0,0]));
  objectLayer.add(root);
  state.objectNodes.set(item.id, root);
  if (item.type !== 'model') {
    root.add(makePrimitive(item));
    root.userData.loaded = true;
    return;
  }
  const placeholder = new THREE.Mesh(new THREE.BoxGeometry(0.5,0.5,0.5), new THREE.MeshBasicMaterial({color:0x6694a8, wireframe:true}));
  root.add(placeholder);
  try {
    if (!item.url) throw new Error('缺少 GLB 地址');
    const gltf = await gltfLoader.loadAsync(item.url);
    if (state.objectNodes.get(item.id) !== root) return;
    root.remove(placeholder);
    gltf.scene.scale.set(...(item.size || [1,1,1]));
    root.add(gltf.scene);
    root.userData.gltfRoot = gltf.scene;
    root.userData.loaded = true;
    if (state.selectedId === item.id) renderSelection();
    if (state.firstFrame) frameAllIfReady();
  } catch (error) {
    placeholder.material.color.set(0xc47472);
    root.userData.loaded = true;
    announce('模型 ' + (item.name || item.id) + ' 加载失败：' + error.message, true);
  }
}

function frameAllIfReady() {
  if (!state.firstFrame) return;
  if (state.sceneObjects.some((item) => item.type === 'model' && !state.objectNodes.get(item.id)?.userData.loaded)) return;
  state.firstFrame = false;
  frameAll();
}
function objectBox(objectId) {
  const root = state.objectNodes.get(objectId);
  if (!root) return null;
  const box = new THREE.Box3().setFromObject(root);
  return box.isEmpty() ? null : box;
}
function resolveSceneNode(reference) {
  if (!reference || !Array.isArray(reference.node_path)) return null;
  const root = state.objectNodes.get(reference.parent_object_id)?.userData.gltfRoot;
  if (!root) return null;
  let node = root;
  for (const index of reference.node_path) {
    if (!Number.isInteger(index) || index < 0 || !node.children[index]) return null;
    node = node.children[index];
  }
  if (reference.node_name && node.name?.trim().slice(0, 160) !== reference.node_name) return null;
  return node;
}
function nodeReference(objectId, hitObject) {
  const modelRoot = state.objectNodes.get(objectId)?.userData.gltfRoot;
  if (!modelRoot) return null;
  let chosen = hitObject;
  // A named group is usually the human-readable part; unnamed meshes still
  // retain a stable child-index path within this GLB's scene graph.
  while (chosen !== modelRoot && !chosen.name?.trim()) chosen = chosen.parent;
  if (chosen === modelRoot) chosen = hitObject;
  const path = [];
  let child = chosen;
  while (child && child !== modelRoot) {
    const parent = child.parent;
    if (!parent) return null;
    path.unshift(parent.children.indexOf(child));
    child = parent;
  }
  if (child !== modelRoot || path.some((index) => index < 0)) return null;
  if (path.length > 32) return null;
  const reference = {parent_object_id:objectId, node_path:path};
  if (chosen.name?.trim()) reference.node_name = chosen.name.trim().slice(0, 160);
  return reference;
}
function frameBox(box) {
  if (!box || box.isEmpty()) return;
  const center = box.getCenter(new THREE.Vector3());
  const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 0.45);
  const distance = Math.max(radius / Math.sin(camera.fov * Math.PI / 360) * 1.25, 2.5);
  camera.position.copy(center).addScaledVector(new THREE.Vector3(1,-1.4,0.95).normalize(), distance);
  controls.target.copy(center);
  camera.near = Math.max(distance / 1000, 0.005);
  camera.far = Math.max(distance * 100, 100);
  camera.updateProjectionMatrix();
  controls.update();
  saveDraft();
}
function frameAll() {
  const box = new THREE.Box3();
  for (const root of state.objectNodes.values()) box.expandByObject(root);
  if (box.isEmpty()) box.setFromCenterAndSize(new THREE.Vector3(0,0,0.5), new THREE.Vector3(3,3,2));
  frameBox(box);
}
function clearSelectionHelper() {
  if (!selectionHelper) return;
  feedbackLayer.remove(selectionHelper);
  selectionHelper.geometry.dispose();
  selectionHelper.material.dispose();
  selectionHelper = null;
}
function renderSelection() {
  clearSelectionHelper();
  const item = sceneObject(state.selectedId);
  if (item) {
    const selectedNode = resolveSceneNode(state.selectedSceneNode);
    if (state.selectedSceneNode && !selectedNode) state.selectedSceneNode = null;
    const box = selectedNode ? new THREE.Box3().setFromObject(selectedNode) : objectBox(item.id);
    if (box) {
      selectionHelper = new THREE.Box3Helper(box, 0x9fe1e8);
      selectionHelper.material.transparent = true;
      selectionHelper.material.opacity = 0.95;
      selectionHelper.material.depthTest = false;
      selectionHelper.renderOrder = 20;
      feedbackLayer.add(selectionHelper);
    }
    ui.selectionSummary.className = 'selection-summary';
    ui.selectionSummary.replaceChildren();
    const name = document.createElement('div');
    name.className = 'selected-name';
    const nodeLabel = state.selectedSceneNode
      ? (state.selectedSceneNode.node_name || '子节点 ' + state.selectedSceneNode.node_path.join('/'))
      : '';
    name.textContent = (item.name || item.id) + (nodeLabel ? ' / ' + nodeLabel : '');
    const objectId = document.createElement('div');
    objectId.className = 'selected-id';
    objectId.textContent = item.id + (state.selectedSceneNode ? ' · 节点 ' + (state.selectedSceneNode.node_path.join('/') || '根') : '');
    ui.selectionSummary.append(name, objectId);
    ui.selectedChip.textContent = '已选 · ' + (nodeLabel || item.name || item.id);
    ui.selectedChip.classList.remove('hidden');
    ui.clearSelection.classList.remove('hidden');
  } else {
    state.selectedId = null;
    state.selectedSceneNode = null;
    ui.selectionSummary.className = 'selection-summary muted';
    ui.selectionSummary.textContent = '未选对象也能提交。比如：参考图里缺了一个东西。';
    ui.selectedChip.classList.add('hidden');
    ui.clearSelection.classList.add('hidden');
  }
  renderObjectList();
}
function renderObjectList() {
  ui.objectList.replaceChildren();
  id('object-count').textContent = String(state.sceneObjects.length);
  if (!state.sceneObjects.length) {
    const empty = document.createElement('p');
    empty.className = 'muted';
    empty.textContent = '场景里还没有对象。可以直接在参考图上标出缺失的东西。';
    ui.objectList.append(empty);
    return;
  }
  for (const item of state.sceneObjects) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'object-item' + (state.selectedId === item.id ? ' active' : '');
    button.dataset.objectId = item.id;
    const swatch = document.createElement('span');
    swatch.className = 'object-color';
    swatch.style.backgroundColor = item.color || '#94b5bf';
    const name = document.createElement('strong');
    name.textContent = item.name || item.id;
    const kind = document.createElement('small');
    kind.textContent = ({box:'方盒',sphere:'球体',cylinder:'圆柱',model:'模型'})[item.type] || item.type;
    button.append(swatch, name, kind);
    button.addEventListener('click', () => selectObject(item.id));
    ui.objectList.append(button);
  }
}
function selectObject(objectId, sceneNode=null) {
  if (!sceneObject(objectId)) return;
  state.selectedId = objectId;
  state.selectedSceneNode = sceneNode;
  renderSelection();
  saveDraft();
}
async function loadScene(sceneData) {
  const scene = sceneData || await api('/api/scene');
  const priorRevision = state.sceneRevision;
  if (priorRevision === scene.revision) return;
  const priorSelectedObject = sceneObject(state.selectedId);
  state.sceneRevision = scene.revision;
  state.sceneObjects = scene.objects || [];
  if (priorRevision !== null && state.selectedSceneNode && priorSelectedObject?.url !== sceneObject(state.selectedId)?.url) {
    state.selectedSceneNode = null;
  }
  if (priorRevision === null && state.selectedSceneNode &&
      (state.restoredModelUrl !== (sceneObject(state.selectedId)?.url || null) ||
       (state.restoredModelUrl === null && state.restoredSceneRevision !== scene.revision))) {
    state.selectedSceneNode = null;
  }
  objectLayer.clear();
  state.objectNodes.clear();
  await Promise.allSettled(state.sceneObjects.map(addObject));
  frameAllIfReady();
  if (state.selectedId && !sceneObject(state.selectedId)) state.selectedId = null;
  if (state.selectedSceneNode && !resolveSceneNode(state.selectedSceneNode)) state.selectedSceneNode = null;
  renderSelection();
  renderAnnotations();
  drawOverlays();
  id('revision-label').textContent = '版本 ' + scene.revision;
  id('scene-name').textContent = scene.name || '当前场景';
  id('scene-title').textContent = scene.name || 'Astra 搭出的结果';
  if (priorRevision !== null) {
    announce('场景已更新到版本 ' + scene.revision + '。旧场景标记已保留并标明来源版本。');
  }
  saveDraft();
}

function setReferences(references) {
  const currentIds = state.references.map((ref) => ref.id).join(',');
  const nextIds = references.map((ref) => ref.id).join(',');
  if (currentIds === nextIds) return;
  state.references = references;
  if (!references.some((ref) => ref.id === state.activeReferenceId)) {
    state.activeReferenceId = references[0]?.id || null;
  }
  renderReferenceStrip();
  showActiveReference();
  renderAnnotations();
  saveDraft();
}
function activeReference() { return state.references.find((ref) => ref.id === state.activeReferenceId); }
function renderReferenceStrip() {
  ui.referenceStrip.replaceChildren();
  if (!state.references.length) {
    const empty = document.createElement('span');
    empty.className = 'muted';
    empty.textContent = '还没有参考图';
    ui.referenceStrip.append(empty);
    return;
  }
  for (const ref of state.references) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'thumb' + (ref.id === state.activeReferenceId ? ' active' : '');
    button.title = ref.name || '参考图';
    button.setAttribute('aria-label', '查看 ' + (ref.name || '参考图'));
    const image = document.createElement('img');
    image.src = ref.url;
    image.alt = '';
    button.append(image);
    const count = state.annotations.filter((a) => a.pane === 'reference' && a.reference_image_id === ref.id).length;
    if (count) {
      const badge = document.createElement('span');
      badge.className = 'thumb-count';
      badge.textContent = String(count);
      button.append(badge);
    }
    button.addEventListener('click', () => {
      state.activeReferenceId = ref.id;
      renderReferenceStrip();
      showActiveReference();
      saveDraft();
    });
    ui.referenceStrip.append(button);
  }
}
function showActiveReference() {
  const ref = activeReference();
  const hasReference = !!ref;
  ui.referenceMedia.classList.toggle('hidden', !hasReference);
  ui.referenceEmpty.classList.toggle('hidden', hasReference);
  ui.referenceHint.classList.toggle('hidden', !hasReference || state.mode === 'select');
  ui.referenceTitle.textContent = ref?.name || '照片里的目标';
  ui.compareImage.classList.toggle('hidden', !hasReference || !ui.compareEnabled.checked);
  ui.compareEnabled.disabled = !hasReference;
  ui.compareOpacity.disabled = !hasReference || !ui.compareEnabled.checked;
  if (!hasReference) {
    ui.referenceImage.removeAttribute('src');
    drawOverlays();
    return;
  }
  ui.referenceImage.src = ref.url;
  ui.compareImage.src = ref.url;
  ui.compareImage.style.opacity = Number(ui.compareOpacity.value) / 100;
  if (ui.referenceImage.complete) updateReferenceGeometry();
}
function updateReferenceGeometry() {
  const image = ui.referenceImage;
  const stage = ui.referenceStage;
  if (!image.naturalWidth || !image.naturalHeight || !stage.clientWidth || !stage.clientHeight) return;
  // The canvas occupies the actual contained bitmap, never the surrounding letterbox.
  const scale = Math.min(stage.clientWidth / image.naturalWidth, stage.clientHeight / image.naturalHeight);
  ui.referenceMedia.style.width = Math.max(1, image.naturalWidth * scale) + 'px';
  ui.referenceMedia.style.height = Math.max(1, image.naturalHeight * scale) + 'px';
  drawOverlays();
}
function readFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error('无法读取图片'));
    reader.readAsDataURL(file);
  });
}
async function uploadReferences(files) {
  if (!state.sessionId || state.sessionStatus !== 'open') return;
  const images = [...files];
  if (!images.length) return;
  state.uploading = true;
  ui.submit.disabled = true;
  try {
    for (const file of images) {
      if (!['image/jpeg','image/png'].includes(file.type)) throw new Error('请使用 PNG 或 JPEG 图片');
      if (file.size > 25 * 1024 * 1024) throw new Error(file.name + ' 超过 25 MiB');
      const dataUrl = await readFile(file);
      const reference = await api('/api/sessions/' + encodeURIComponent(state.sessionId) + '/references', {
        method:'POST', body:{name:file.name, data_url:dataUrl}
      });
      // The endpoint may return the new item or a list containing it.
      if (reference.reference_images) setReferences(reference.reference_images);
      else if (reference.reference) setReferences([...state.references, reference.reference]);
      else if (reference.id) setReferences([...state.references, reference]);
    }
    const session = await api('/api/sessions/' + encodeURIComponent(state.sessionId));
    setReferences(session.reference_images || []);
    if (images.length) {
      state.activeReferenceId = state.references[state.references.length - 1]?.id || state.activeReferenceId;
      renderReferenceStrip();
      showActiveReference();
      saveDraft();
    }
    announce('已添加 ' + images.length + ' 张参考图。');
  } catch (error) {
    announce('添加参考图失败：' + error.message, true);
  } finally {
    state.uploading = false;
    ui.submit.disabled = !editable();
    ui.referenceInput.value = '';
  }
}

function cameraData() {
  return {
    position:array(camera.position), target:array(controls.target),
    up:array(camera.up), fov:camera.fov, aspect:camera.aspect
  };
}
function updateSceneHint() {
  const oldCount = state.annotations.filter((annotation) =>
    annotation.pane === 'scene' && annotation.scene_revision &&
    annotation.scene_revision !== state.sceneRevision
  ).length;
  if (state.mode === 'select') {
    ui.sceneHint.textContent = oldCount
      ? oldCount + ' 条灰色虚线标记来自旧版本，位置可能已变化'
      : '拖拽旋转 · 滚轮缩放 · 点击对象';
  } else {
    ui.sceneHint.textContent = '在场景上' +
      ({point:'点一下',rectangle:'拖动框选',line:'拖动画线',arrow:'拖动画箭头',text:'点击加文字'})[state.mode] +
      ' · 按 1 返回旋转';
  }
}
function updateMode() {
  document.querySelectorAll('.tool-button').forEach((button) => button.classList.toggle('active', button.dataset.tool === state.mode));
  const drawing = state.mode !== 'select' && state.sessionStatus === 'open';
  ui.referenceCanvas.style.pointerEvents = drawing ? 'auto' : 'none';
  ui.sceneCanvas.style.pointerEvents = drawing ? 'auto' : 'none';
  ui.referenceCanvas.style.cursor = drawing ? 'crosshair' : 'default';
  ui.sceneCanvas.style.cursor = drawing ? 'crosshair' : 'default';
  controls.enabled = state.mode === 'select';
  ui.referenceHint.classList.toggle('hidden', !drawing || !activeReference());
  updateSceneHint();
  state.drag = null;
  drawOverlays();
}
function setMode(mode) {
  if (!['select','point','rectangle','line','arrow','text'].includes(mode)) return;
  state.mode = mode;
  hideTextEditor();
  updateMode();
}
function pointFromPointer(event, canvas) {
  const rect = canvas.getBoundingClientRect();
  return {
    x:clamp((event.clientX - rect.left) / rect.width, 0, 1),
    y:clamp((event.clientY - rect.top) / rect.height, 0, 1)
  };
}
function addAnnotation(annotation) {
  const item = {
    id:crypto.randomUUID(), pane:annotation.pane, type:annotation.type,
    coordinates:annotation.coordinates
  };
  if (state.groupId) item.group_id = state.groupId;
  if (annotation.pane === 'reference') item.reference_image_id = state.activeReferenceId;
  else {
    item.scene_revision = state.sceneRevision;
    item.camera = cameraData();
    if (state.selectedId) item.object_id = state.selectedId;
    if (state.selectedSceneNode) item.scene_node = {...state.selectedSceneNode};
  }
  if (annotation.text) item.text = annotation.text;
  state.annotations.push(item);
  renderAnnotations();
  drawOverlays();
  saveDraft();
}
function annotationPointerDown(event, pane) {
  if (!editable() || state.mode === 'select') return;
  if (pane === 'reference' && !activeReference()) return;
  event.preventDefault();
  const canvas = event.currentTarget;
  const point = pointFromPointer(event, canvas);
  if (state.mode === 'text') {
    showTextEditor(pane, point, event.clientX, event.clientY);
    return;
  }
  canvas.setPointerCapture(event.pointerId);
  state.drag = {pane, type:state.mode, start:point, end:point, pointerId:event.pointerId};
  drawOverlays();
}
function annotationPointerMove(event) {
  if (!state.drag || state.drag.pointerId !== event.pointerId) return;
  state.drag.end = pointFromPointer(event, event.currentTarget);
  drawOverlays();
}
function annotationPointerUp(event) {
  if (!state.drag || state.drag.pointerId !== event.pointerId) return;
  const drag = state.drag;
  state.drag = null;
  const end = pointFromPointer(event, event.currentTarget);
  const distance = Math.hypot(end.x - drag.start.x, end.y - drag.start.y);
  if (drag.type === 'point') {
    addAnnotation({pane:drag.pane, type:'point', coordinates:{x:drag.start.x, y:drag.start.y}});
  } else if (distance > 0.005) {
    addAnnotation({pane:drag.pane, type:drag.type, coordinates:{
      x:drag.start.x, y:drag.start.y, x2:end.x, y2:end.y
    }});
  } else {
    drawOverlays();
  }
}
function showTextEditor(pane, point, clientX, clientY) {
  state.textPending = {pane, point};
  ui.textEditor.classList.remove('hidden');
  ui.textEditor.style.left = clamp(clientX + 8, 8, window.innerWidth - 302) + 'px';
  ui.textEditor.style.top = clamp(clientY + 8, 8, window.innerHeight - 48) + 'px';
  ui.annotationText.value = '';
  ui.annotationText.focus();
}
function hideTextEditor() {
  state.textPending = null;
  ui.textEditor.classList.add('hidden');
}
function saveTextAnnotation() {
  const text = ui.annotationText.value.trim();
  if (text && state.textPending) {
    addAnnotation({
      pane:state.textPending.pane, type:'text',
      coordinates:{x:state.textPending.point.x, y:state.textPending.point.y}, text
    });
  }
  hideTextEditor();
}

function drawAnnotation(ctx, annotation, width, height, preview=false) {
  const p = annotation.coordinates || {};
  const x = clamp(Number(p.x) || 0, 0, 1) * width;
  const y = clamp(Number(p.y) || 0, 0, 1) * height;
  const x2 = clamp(Number(p.x2) || 0, 0, 1) * width;
  const y2 = clamp(Number(p.y2) || 0, 0, 1) * height;
  const stale = annotation.pane === 'scene' && annotation.scene_revision && annotation.scene_revision !== state.sceneRevision;
  const color = stale ? '#bdcad0' : '#ffbd78';
  const scale = Math.max(1, Math.min(width, height) / 550);
  ctx.save();
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = 3 * scale;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.shadowColor = '#15222c';
  ctx.shadowBlur = 3 * scale;
  if (stale) ctx.setLineDash([6 * scale, 5 * scale]);
  if (preview) ctx.globalAlpha = 0.68;
  if (annotation.type === 'point') {
    ctx.beginPath(); ctx.arc(x, y, 10 * scale, 0, Math.PI * 2); ctx.stroke();
    ctx.beginPath(); ctx.arc(x, y, 3 * scale, 0, Math.PI * 2); ctx.fill();
  } else if (annotation.type === 'rectangle') {
    ctx.strokeRect(x, y, x2 - x, y2 - y);
  } else if (annotation.type === 'line' || annotation.type === 'arrow') {
    ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x2, y2); ctx.stroke();
    if (annotation.type === 'arrow') {
      const angle = Math.atan2(y2 - y, x2 - x);
      const size = 14 * scale;
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.moveTo(x2 - size * Math.cos(angle - Math.PI / 6), y2 - size * Math.sin(angle - Math.PI / 6));
      ctx.lineTo(x2, y2);
      ctx.lineTo(x2 - size * Math.cos(angle + Math.PI / 6), y2 - size * Math.sin(angle + Math.PI / 6));
      ctx.stroke();
    }
  } else if (annotation.type === 'text' && annotation.text) {
    ctx.setLineDash([]);
    ctx.font = 'bold ' + Math.round(13 * scale) + 'px sans-serif';
    const text = String(annotation.text).slice(0, 100);
    const textWidth = Math.min(ctx.measureText(text).width, width - 12);
    ctx.fillStyle = '#17242de8';
    ctx.fillRect(x, y - 19 * scale, textWidth + 12 * scale, 25 * scale);
    ctx.fillStyle = color;
    ctx.fillText(text, x + 5 * scale, y, Math.max(0, width - x - 10));
  }
  if (annotation.group_id && circled[Number(annotation.group_id)]) {
    ctx.setLineDash([]);
    ctx.font = 'bold ' + Math.round(20 * scale) + 'px sans-serif';
    ctx.fillStyle = '#15222c';
    ctx.fillRect(x - 5 * scale, y - 30 * scale, 26 * scale, 23 * scale);
    ctx.fillStyle = color;
    ctx.fillText(circled[Number(annotation.group_id)], x - 3 * scale, y - 12 * scale);
  }
  ctx.restore();
}
function prepareCanvas(canvas) {
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.round(rect.width * ratio);
  const height = Math.round(rect.height * ratio);
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const context = canvas.getContext('2d');
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, rect.width, rect.height);
  return {context, width:rect.width, height:rect.height};
}
function drawOverlays() {
  for (const pane of ['reference','scene']) {
    const canvas = pane === 'reference' ? ui.referenceCanvas : ui.sceneCanvas;
    const surface = prepareCanvas(canvas);
    if (!surface) continue;
    for (const annotation of state.annotations) {
      if (annotation.pane !== pane) continue;
      if (pane === 'reference' && annotation.reference_image_id !== state.activeReferenceId) continue;
      drawAnnotation(surface.context, annotation, surface.width, surface.height);
    }
    if (state.drag?.pane === pane) {
      drawAnnotation(surface.context, {
        pane, type:state.drag.type,
        coordinates:{x:state.drag.start.x, y:state.drag.start.y, x2:state.drag.end.x, y2:state.drag.end.y}
      }, surface.width, surface.height, true);
    }
  }
}
function renderAnnotations() {
  ui.annotationList.replaceChildren();
  ui.annotationCount.textContent = String(state.annotations.length);
  renderReferenceStrip();
  updateSceneHint();
  if (!state.annotations.length) {
    const empty = document.createElement('div');
    empty.className = 'muted';
    empty.textContent = '你画的点、框、线和文字会显示在这里。';
    ui.annotationList.append(empty);
    return;
  }
  for (const annotation of state.annotations) {
    const row = document.createElement('div');
    row.className = 'annotation-item';
    const glyph = document.createElement('span');
    glyph.className = 'annotation-glyph';
    glyph.textContent = glyphs[annotation.type] || '●';
    const copy = document.createElement('div');
    copy.className = 'annotation-copy';
    const title = document.createElement('strong');
    const ref = state.references.find((item) => item.id === annotation.reference_image_id);
    title.textContent = (annotation.group_id ? circled[Number(annotation.group_id)] + ' ' : '') +
      (annotation.pane === 'reference' ? '参考图 · ' + (ref?.name || '图片') : '当前场景') +
      ' · ' + (labels[annotation.type] || annotation.type);
    const subtitle = document.createElement('small');
    const stale = annotation.pane === 'scene' && annotation.scene_revision && annotation.scene_revision !== state.sceneRevision;
    subtitle.textContent = annotation.text || (stale ? '来自旧场景版本 ' + annotation.scene_revision : annotation.object_id ? '对象：' + annotation.object_id : '视觉提示');
    if (stale) subtitle.classList.add('stale-label');
    copy.append(title, subtitle);
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'annotation-remove';
    remove.textContent = '×';
    remove.title = '删除这条标记';
    remove.disabled = state.sessionStatus !== 'open';
    remove.addEventListener('click', () => {
      state.annotations = state.annotations.filter((item) => item.id !== annotation.id);
      renderAnnotations();
      drawOverlays();
      saveDraft();
    });
    row.append(glyph, copy, remove);
    ui.annotationList.append(row);
  }
}

async function loadImage(url) {
  const image = new Image();
  image.crossOrigin = 'anonymous';
  image.src = url;
  if (image.decode) await image.decode();
  else await new Promise((resolve, reject) => {
    image.onload = resolve;
    image.onerror = reject;
  });
  return image;
}
function scaledCanvas(width, height, maxEdge=1600) {
  const scale = Math.min(1, maxEdge / Math.max(width, height));
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(width * scale));
  canvas.height = Math.max(1, Math.round(height * scale));
  return canvas;
}
async function captureReferenceAnnotations() {
  const images = [];
  const crops = [];
  for (const ref of state.references) {
    try {
      const image = ref.id === state.activeReferenceId && ui.referenceImage.complete && ui.referenceImage.naturalWidth
        ? ui.referenceImage : await loadImage(ref.url);
      const canvas = scaledCanvas(image.naturalWidth, image.naturalHeight);
      const context = canvas.getContext('2d');
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      const marks = state.annotations.filter((annotation) =>
        annotation.pane === 'reference' && annotation.reference_image_id === ref.id
      );
      for (const annotation of marks) drawAnnotation(context, annotation, canvas.width, canvas.height);
      images.push({reference_id:ref.id, data_url:canvas.toDataURL('image/jpeg', 0.84)});
      if (marks.length) {
        const xs = marks.flatMap((mark) => [mark.coordinates.x, mark.coordinates.x2 ?? mark.coordinates.x]);
        const ys = marks.flatMap((mark) => [mark.coordinates.y, mark.coordinates.y2 ?? mark.coordinates.y]);
        const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
        const centerX = (minX + maxX) / 2, centerY = (minY + maxY) / 2;
        const halfWidth = Math.max(0.1, (maxX - minX) / 2 + 0.08);
        const halfHeight = Math.max(0.1, (maxY - minY) / 2 + 0.08);
        const left = clamp(centerX - halfWidth, 0, 1);
        const right = clamp(centerX + halfWidth, 0, 1);
        const top = clamp(centerY - halfHeight, 0, 1);
        const bottom = clamp(centerY + halfHeight, 0, 1);
        const sx = Math.round(left * canvas.width);
        const sy = Math.round(top * canvas.height);
        const sw = Math.max(1, Math.round((right - left) * canvas.width));
        const sh = Math.max(1, Math.round((bottom - top) * canvas.height));
        const crop = scaledCanvas(sw, sh, 1024);
        crop.getContext('2d').drawImage(canvas, sx, sy, sw, sh, 0, 0, crop.width, crop.height);
        crops.push({source:'reference', reference_id:ref.id, data_url:crop.toDataURL('image/jpeg', 0.86)});
      }
    } catch (error) {
      throw new Error('参考图 ' + (ref.name || ref.id) + ' 无法截取：' + error.message);
    }
  }
  return {images, crops};
}
function captureScene() {
  controls.update();
  renderer.render(threeScene, camera);
  const source = renderer.domElement;
  const original = scaledCanvas(source.width, source.height);
  const context = original.getContext('2d');
  context.drawImage(source, 0, 0, original.width, original.height);
  const annotated = document.createElement('canvas');
  annotated.width = original.width;
  annotated.height = original.height;
  const annotatedContext = annotated.getContext('2d');
  annotatedContext.drawImage(original, 0, 0);
  for (const annotation of state.annotations) {
    if (annotation.pane === 'scene') {
      drawAnnotation(annotatedContext, annotation, annotated.width, annotated.height);
    }
  }
  return {
    scene_original_data_url:original.toDataURL('image/jpeg', 0.84),
    scene_annotated_data_url:annotated.toDataURL('image/jpeg', 0.84)
  };
}
async function feedbackPayload() {
  const imageBundle = captureScene();
  const annotatedReferences = await captureReferenceAnnotations();
  return {
    scene_revision:state.sceneRevision,
    note:ui.note.value.trim(),
    annotations:state.annotations.map((annotation) => {
      const item = {...annotation};
      if (item.object_id && !sceneObject(item.object_id)) {
        item.previous_object_id = item.object_id;
        delete item.object_id;
      }
      if (item.scene_node && sceneObject(item.scene_node.parent_object_id)?.type !== 'model') {
        item.previous_scene_node = item.scene_node;
        delete item.scene_node;
      }
      return item;
    }),
    selected_object_ids:state.selectedId ? [state.selectedId] : [],
    selected_scene_nodes:state.selectedSceneNode ? [{...state.selectedSceneNode}] : [],
    camera:cameraData(),
    reference_images:state.references.map((ref) => ({id:ref.id, url:ref.url, name:ref.name})),
    reference_annotated_data_urls:annotatedReferences.images,
    crops:annotatedReferences.crops,
    ...imageBundle
  };
}
async function submitFeedback() {
  if (!editable() || !state.sessionId) return;
  if (!state.annotations.length && !ui.note.value.trim()) {
    announce('请在任一侧画标记，或写一句话后再发送。', true);
    return;
  }
  state.submitting = true;
  ui.submit.disabled = true;
  ui.submit.querySelector('span:first-child').textContent = '正在准备图片…';
  try {
    const payload = await feedbackPayload();
    ui.submit.querySelector('span:first-child').textContent = '正在发送…';
    await api('/api/sessions/' + encodeURIComponent(state.sessionId) + '/feedback', {
      method:'POST', body:payload
    });
    state.feedbackCount += 1;
    id('feedback-count-label').textContent = '已发 ' + state.feedbackCount + ' 轮';
    ui.caption.textContent = '已发送 ' + state.feedbackCount + ' 轮。当前标记会在下次再次发送；可修改、删除或清空。';
    saveDraft();
    announce('这一轮反馈已发给 Astra。你可以继续标记并再次发送。');
  } catch (error) {
    if (/revision changed|scene revision/i.test(error.message)) {
      try { await loadScene(); } catch { /* Keep the draft for the next poll. */ }
    }
    announce('发送失败：' + error.message, true);
  } finally {
    state.submitting = false;
    ui.submit.disabled = !editable();
    ui.submit.querySelector('span:first-child').textContent = '发给 Astra';
  }
}

function pickScene(event) {
  const bounds = renderer.domElement.getBoundingClientRect();
  pointer.set(
    (event.clientX - bounds.left) / bounds.width * 2 - 1,
    -(event.clientY - bounds.top) / bounds.height * 2 + 1
  );
  raycaster.setFromCamera(pointer, camera);
  const intersections = raycaster.intersectObjects([...state.objectNodes.values()], true);
  for (const hit of intersections) {
    let node = hit.object;
    while (node && !node.userData.objectId) node = node.parent;
    if (node?.userData.objectId) {
      return {objectId:node.userData.objectId, sceneNode:nodeReference(node.userData.objectId, hit.object)};
    }
  }
  return null;
}
function handleSceneClick(event) {
  const selection = pickScene(event);
  if (selection) selectObject(selection.objectId, selection.sceneNode);
}
function resizeScene() {
  const width = ui.sceneStage.clientWidth;
  const height = ui.sceneStage.clientHeight;
  if (!width || !height) return;
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
  renderer.setSize(width, height, false);
  drawOverlays();
}
function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(threeScene, camera);
}
async function poll() {
  if (!state.sessionId || state.submitting || state.uploading || poll.running) return;
  poll.running = true;
  try {
    const [scene, session] = await Promise.all([
      api('/api/scene'),
      api('/api/sessions/' + encodeURIComponent(state.sessionId))
    ]);
    if (scene.revision !== state.sceneRevision) await loadScene(scene);
    if (session.reference_images) setReferences(session.reference_images);
    if (session.status !== state.sessionStatus || Number(session.feedback_count) !== state.feedbackCount) setSession(session);
  } catch { /* A later poll can recover from a temporary network error. */ }
  finally { poll.running = false; }
}
function bindEvents() {
  document.querySelectorAll('.tool-button').forEach((button) => button.addEventListener('click', () => setMode(button.dataset.tool)));
  ui.groupSelect.addEventListener('change', () => {
    state.groupId = ui.groupSelect.value;
    saveDraft();
  });
  ui.referenceInput.addEventListener('change', () => uploadReferences(ui.referenceInput.files));
  ui.referenceImage.addEventListener('load', updateReferenceGeometry);
  ui.referenceImage.addEventListener('error', () => announce('这张参考图无法显示。', true));
  ui.compareEnabled.addEventListener('change', () => {
    ui.compareImage.classList.toggle('hidden', !ui.compareEnabled.checked || !activeReference());
    ui.compareOpacity.disabled = !ui.compareEnabled.checked || !activeReference();
  });
  ui.compareOpacity.addEventListener('input', () => {
    ui.opacityValue.textContent = ui.compareOpacity.value + '%';
    ui.compareImage.style.opacity = Number(ui.compareOpacity.value) / 100;
  });
  for (const [canvas,pane] of [[ui.referenceCanvas,'reference'],[ui.sceneCanvas,'scene']]) {
    canvas.addEventListener('pointerdown', (event) => annotationPointerDown(event, pane));
    canvas.addEventListener('pointermove', annotationPointerMove);
    canvas.addEventListener('pointerup', annotationPointerUp);
    canvas.addEventListener('pointercancel', () => {
      state.drag = null;
      drawOverlays();
    });
  }
  ui.note.addEventListener('input', saveDraft);
  ui.submit.addEventListener('click', submitFeedback);
  id('clear-annotations').addEventListener('click', () => {
    if (state.sessionStatus !== 'open') return;
    state.annotations = [];
    renderAnnotations();
    drawOverlays();
    saveDraft();
  });
  ui.clearSelection.addEventListener('click', () => {
    state.selectedId = null;
    state.selectedSceneNode = null;
    renderSelection();
    saveDraft();
  });
  id('frame-button').addEventListener('click', () => state.selectedId ? frameBox(objectBox(state.selectedId)) : frameAll());
  id('reset-button').addEventListener('click', frameAll);
  id('save-text').addEventListener('click', saveTextAnnotation);
  id('cancel-text').addEventListener('click', hideTextEditor);
  ui.annotationText.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); saveTextAnnotation(); }
    if (event.key === 'Escape') { event.preventDefault(); hideTextEditor(); }
  });
  id('help-button').addEventListener('click', () => id('help-dialog').showModal());
  id('close-help').addEventListener('click', () => id('help-dialog').close());
  renderer.domElement.addEventListener('pointerdown', (event) => {
    pointerDown = {x:event.clientX, y:event.clientY, button:event.button};
  });
  renderer.domElement.addEventListener('pointerup', (event) => {
    if (!pointerDown || pointerDown.button !== 0 || state.mode !== 'select') return;
    const distance = Math.hypot(event.clientX - pointerDown.x, event.clientY - pointerDown.y);
    pointerDown = null;
    if (distance < 5) handleSceneClick(event);
  });
  controls.addEventListener('end', saveDraft);
  document.addEventListener('keydown', (event) => {
    if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLSelectElement) return;
    if (event.key >= '1' && event.key <= '6') {
      setMode(['select','point','rectangle','line','arrow','text'][Number(event.key) - 1]);
    }
    if (event.key === 'Escape') {
      state.drag = null;
      hideTextEditor();
      drawOverlays();
    }
    if (event.key === 'Backspace' && state.annotations.length && state.sessionStatus === 'open') {
      event.preventDefault();
      state.annotations.pop();
      renderAnnotations();
      drawOverlays();
      saveDraft();
    }
  });
  new ResizeObserver(updateReferenceGeometry).observe(ui.referenceStage);
  new ResizeObserver(resizeScene).observe(ui.sceneStage);
}

bindEvents();
resizeScene();
animate();
try {
  await ensureSession();
  await loadScene();
  updateMode();
  renderAnnotations();
  drawOverlays();
} catch (error) {
  state.sessionStatus = 'error';
  ui.pill.textContent = '连接失败';
  ui.pill.className = 'session-pill error';
  ui.submit.disabled = true;
  announce('工作台启动失败：' + error.message, true);
}
setInterval(poll, 4000);

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

const id = (name) => document.getElementById(name);
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const array = (value) => [value.x, value.y, value.z].map((n) => Number(n.toFixed(5)));
function newId() {
  if (typeof globalThis.crypto?.randomUUID === 'function') return globalThis.crypto.randomUUID();
  if (typeof globalThis.crypto?.getRandomValues !== 'function') throw new Error('浏览器无法生成安全随机 ID');
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
const labels = {point:'点', rectangle:'方框', line:'线段', arrow:'箭头', text:'文字', freehand:'自由画笔'};
const glyphs = {point:'●', rectangle:'▢', line:'╱', arrow:'↗', text:'T', freehand:'〰'};
const circled = ['','①','②','③','④','⑤','⑥','⑦','⑧','⑨'];
const ui = {
  viewport:id('viewport'), sceneStage:id('scene-stage'), sceneCanvas:id('scene-annotations'),
  referenceStage:id('reference-stage'), referenceMedia:id('reference-media'),
  referenceImage:id('reference-image'), referenceCanvas:id('reference-annotations'),
  referenceEmpty:id('reference-empty'), referenceStrip:id('reference-strip'),
  referenceTitle:id('reference-title'), referenceInput:id('reference-input'),
  referenceZoomOut:id('reference-zoom-out'), referenceZoomReset:id('reference-zoom-reset'), referenceZoomIn:id('reference-zoom-in'),
  alignReference:id('align-reference-button'), alignmentStatus:id('camera-alignment-status'),
  compareImage:id('compare-image'), compareEnabled:id('compare-enabled'),
  compareOpacity:id('compare-opacity'), opacityValue:id('opacity-value'),
  objectList:id('object-list'), selectionSummary:id('selection-summary'),
  clearSelection:id('clear-selection'), selectedChip:id('selected-chip'),
  annotationList:id('annotation-list'), annotationCount:id('annotation-count'),
  note:id('feedback-note'), submit:id('submit-button'), caption:id('submit-caption'),
  pill:id('session-pill'), toast:id('toast'), sceneHint:id('scene-hint'),
  referenceHint:id('reference-hint'), groupSelect:id('group-select'),
  textEditor:id('text-editor'), annotationText:id('annotation-text'),
  freeze:id('freeze-button'), browse:id('browse-button'), snapshotButton:id('snapshot-button'),
  snapshotMedia:id('scene-snapshot-media'), snapshotImage:id('scene-snapshot-image'),
  newSceneBadge:id('new-scene-badge'), stop:id('stop-button'), agentStatus:id('agent-status'),
  feedbackIntro:id('feedback-intro'),
  approvals:id('approval-list'), queue:id('queue-list'), conversation:id('conversation')
};
const state = {
  sessionId:null, sessionStatus:'connecting', feedbackCount:0,
  browserCapability:null, agent:{status:'disconnected'}, deliveryMode:'app_server',
  externalSubmitted:false, externalWaitId:null, queue:[], approvals:[],
  eventCursor:0, seenEventIds:new Set(), submittingKey:null, workspaceReady:false,
  sceneRevision:null, sceneObjects:[], objectNodes:new Map(),
  references:[], activeReferenceId:null, selectedId:null, selectedSceneNode:null,
  alignedReferenceId:null, alignmentExact:false, restoredCameraForReference:false,
  restoredCameraSignature:null,
  annotations:[], mode:'select', groupId:'', drag:null, textPending:null,
  snapshot:null, sceneView:'live', referenceZoom:1, referencePan:{x:0,y:0},
  referencePanning:null, spacePan:false,
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
let orbitStart = null;

function announce(message, error=false) {
  clearTimeout(state.toastTimer);
  ui.toast.textContent = message;
  ui.toast.classList.toggle('error', error);
  ui.toast.classList.add('show');
  state.toastTimer = setTimeout(() => ui.toast.classList.remove('show'), 4500);
}

async function api(path, options={}) {
  const request = {...options};
  if (state.browserCapability && /^(POST|PUT|PATCH|DELETE)$/i.test(request.method || '') &&
      (path.startsWith('/api/workspace/') || path.startsWith('/api/sessions/'))) {
    request.headers = {...(request.headers || {}), 'X-Workspace-Capability':state.browserCapability};
  }
  if (options.body && typeof options.body !== 'string') {
    request.body = JSON.stringify(options.body);
    request.headers = {'Content-Type':'application/json', ...(request.headers || {})};
  }
  const response = await fetch(path, request);
  let body;
  try { body = await response.json(); } catch { body = {}; }
  if (!response.ok) {
    const error = new Error(body.error || body.detail || 'HTTP ' + response.status);
    error.status = response.status;
    error.detail = body;
    throw error;
  }
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
      groupId:state.groupId, camera:{position:array(camera.position), target:array(controls.target),
        up:array(camera.up), fov:camera.fov, alignedReferenceId:state.alignedReferenceId,
        alignmentExact:state.alignmentExact,
        referenceCameraSignature:state.alignedReferenceId ? JSON.stringify(activeReference()?.camera || null) : null},
      snapshot:state.snapshot, sceneView:state.sceneView,
      referenceZoom:state.referenceZoom, referencePan:state.referencePan,
      eventCursor:state.eventCursor
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
    if (draft.snapshot?.data_url?.startsWith('data:image/jpeg;base64,') && Number.isInteger(draft.snapshot.scene_revision)) {
      state.snapshot = draft.snapshot;
      state.sceneView = draft.sceneView === 'live' ? 'live' : 'snapshot';
    } else {
      // Old drafts had scene marks without a fixed image; they cannot be mapped reliably.
      state.annotations = state.annotations.filter((annotation) => annotation.pane !== 'scene');
    }
    state.referenceZoom = Number.isFinite(draft.referenceZoom) ? clamp(draft.referenceZoom, 1, 8) : 1;
    state.referencePan = Number.isFinite(draft.referencePan?.x) && Number.isFinite(draft.referencePan?.y)
      ? draft.referencePan : {x:0,y:0};
    ui.groupSelect.value = state.groupId;
    ui.note.value = typeof draft.note === 'string' ? draft.note : '';
    if (draft.camera?.position?.length === 3 && draft.camera?.target?.length === 3) {
      camera.position.set(...draft.camera.position);
      if (draft.camera.up?.length === 3) camera.up.set(...draft.camera.up);
      if (Number.isFinite(draft.camera.fov) && draft.camera.fov > 1 && draft.camera.fov < 179) camera.fov = draft.camera.fov;
      controls.target.set(...draft.camera.target);
      controls.update();
      state.alignedReferenceId = typeof draft.camera.alignedReferenceId === 'string' ? draft.camera.alignedReferenceId : null;
      state.alignmentExact = !!draft.camera.alignmentExact;
      state.restoredCameraForReference = !!state.alignedReferenceId;
      state.restoredCameraSignature = draft.camera.referenceCameraSignature || null;
      state.firstFrame = false;
    }
  } catch { /* Ignore a stale or corrupt local draft. */ }
}

function editable() { return state.workspaceReady && state.sessionStatus === 'open' && !state.submitting && !state.uploading && !state.pendingSubmission; }
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
  if (state.sessionStatus !== 'open') {
    ui.caption.textContent = '这个会话已结束。已保存的标记仍可查看。';
  } else {
    updateSubmitLabel();
  }
}

async function ensureSession() {
  const params = new URLSearchParams(location.search);
  const oldSession = params.get('session_id') || params.get('session');
  const workspace = await api('/api/workspace/state' + (oldSession ? '?session_id=' + encodeURIComponent(oldSession) : ''));
  if (!workspace.session_id || !workspace.browser_capability) throw new Error('工作台没有连接到 Codex 项目会话');
  state.browserCapability = workspace.browser_capability;
  state.workspaceReady = true;
  renderWorkspace(workspace);
  const sessionId = workspace.session_id;
  const session = await api('/api/sessions/' + encodeURIComponent(sessionId));
  if (params.get('session_id') !== sessionId || params.has('session')) {
    params.delete('session');
    params.set('session_id', sessionId);
    history.replaceState(null, '', location.pathname + '?' + params.toString());
  }
  setSession(session);
  restoreDraft();
  setReferences(session.reference_images || []);
  renderSceneView();
  state.pendingSubmission = await readOutbox();
  renderWorkspace(workspace);
  await fetchEvents(true);
}

function outboxKey() { return 'visual-outbox:' + state.sessionId; }
function openOutbox() {
  return new Promise((resolve, reject) => {
    if (!window.indexedDB) return reject(new Error('浏览器未提供本地存储'));
    const request = indexedDB.open('visual-reconstruction-workspace', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('outbox');
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error('无法打开本地存储'));
  });
}
async function writeOutbox(packet) {
  try {
    const db = await openOutbox();
    await new Promise((resolve, reject) => {
      const transaction = db.transaction('outbox', 'readwrite');
      transaction.objectStore('outbox').put(packet, outboxKey());
      transaction.oncomplete = resolve;
      transaction.onerror = () => reject(transaction.error);
    });
    db.close();
  } catch {
    try { localStorage.setItem(outboxKey(), JSON.stringify(packet)); }
    catch { throw new Error('浏览器无法保存待发送消息。请检查可用存储空间后重试。'); }
  }
}
async function readOutbox() {
  try {
    const db = await openOutbox();
    const packet = await new Promise((resolve, reject) => {
      const request = db.transaction('outbox', 'readonly').objectStore('outbox').get(outboxKey());
      request.onsuccess = () => resolve(request.result || null);
      request.onerror = () => reject(request.error);
    });
    db.close();
    if (packet) return packet;
  } catch { /* Fall back to local storage. */ }
  try { return JSON.parse(localStorage.getItem(outboxKey()) || 'null'); }
  catch { return null; }
}
async function clearOutbox() {
  try {
    const db = await openOutbox();
    await new Promise((resolve, reject) => {
      const transaction = db.transaction('outbox', 'readwrite');
      transaction.objectStore('outbox').delete(outboxKey());
      transaction.oncomplete = resolve;
      transaction.onerror = () => reject(transaction.error);
    });
    db.close();
  } catch { /* The fallback store is also cleared below. */ }
  try { localStorage.removeItem(outboxKey()); } catch { /* Ignore unavailable local storage. */ }
}

function updateSubmitLabel() {
  const status = state.agent?.status || 'disconnected';
  if (state.deliveryMode === 'external') {
    const waiting = status === 'waiting_for_mcp';
    const label = state.pendingSubmission ? '重试交回当前会话'
      : state.externalSubmitted ? '反馈已提交'
      : waiting ? '送回当前 Codex 会话' : '等待 Codex 请求反馈';
    ui.submit.querySelector('span:first-child').textContent = label;
    ui.submit.disabled = !state.workspaceReady || state.sessionStatus !== 'open' || state.submitting || state.uploading ||
      (!state.pendingSubmission && (!waiting || state.externalSubmitted));
    ui.note.disabled = state.sessionStatus !== 'open' || !!state.pendingSubmission;
    ui.referenceInput.disabled = state.sessionStatus !== 'open' || !!state.pendingSubmission;
    id('clear-annotations').disabled = state.sessionStatus !== 'open' || !!state.pendingSubmission;
    ui.caption.textContent = state.pendingSubmission
      ? '上次提交的送达状态未确认。重试沿用同一消息编号。'
      : state.externalSubmitted ? '反馈已保存，正在交回当前等待中的 MCP 工具。'
      : waiting ? '原图、标注图和场景截图会作为本次 MCP 工具调用的结果返回 Codex。'
      : '当前没有等待中的反馈工具调用。请先在 Codex 会话中请求视觉反馈。';
    return;
  }
  const label = state.pendingSubmission ? '重试上一条消息'
    : status === 'running' || status === 'awaiting_approval' ? '加入下一轮'
    : status === 'disconnected' || status === 'error' ? '保存并等待连接'
    : '发送到 Codex';
  ui.submit.querySelector('span:first-child').textContent = label;
  ui.submit.disabled = !state.workspaceReady || state.sessionStatus !== 'open' || state.submitting || state.uploading;
  ui.note.disabled = state.sessionStatus !== 'open' || !!state.pendingSubmission;
  ui.referenceInput.disabled = state.sessionStatus !== 'open' || !!state.pendingSubmission;
  id('clear-annotations').disabled = state.sessionStatus !== 'open' || !!state.pendingSubmission;
  if (state.pendingSubmission) ui.caption.textContent = '上一条消息的送达状态尚未确认。重试会使用相同编号，不会重复启动一轮。';
  else if (status === 'running' || status === 'awaiting_approval') ui.caption.textContent = 'Codex 正在执行；这条图文消息会加入下一轮。';
  else if (status === 'disconnected' || status === 'error') ui.caption.textContent = 'Codex 暂时未连接；消息会在本机保存，恢复后自动进入同一会话。';
  else ui.caption.textContent = '原图、标注图和场景截图会作为图像输入送入当前 Codex 会话。';
}
function renderWorkspace(workspace) {
  state.deliveryMode = workspace.delivery_mode === 'external' ? 'external' : 'app_server';
  state.agent = workspace.agent || {status:'disconnected'};
  state.queue = Array.isArray(workspace.queue) ? workspace.queue : [];
  state.approvals = Array.isArray(workspace.approvals) ? workspace.approvals : [];
  if (state.deliveryMode === 'external') {
    renderExternalWorkspace(workspace);
    return;
  }
  ui.feedbackIntro.textContent = '圈画后直接发送图文消息。Codex 的进度和新场景会回到这里。';
  const status = state.agent.status;
  const statusText = {
    idle:'Codex 已连接，等待你的消息', running:'Codex 正在处理这一轮…',
    awaiting_approval:'Codex 需要你审批后继续',
    disconnected:'Codex 连接已断开，正在尝试恢复',
    delivery_uncertain:'消息送达状态待核实，请勿重复创建反馈',
    error:'Codex 会话发生错误'
  };
  ui.agentStatus.textContent = statusText[status] || '正在连接 Codex…';
  if (state.agent.error) ui.agentStatus.textContent += '：' + String(state.agent.error).slice(0, 240);
  ui.agentStatus.className = 'agent-status' + (status === 'running' ? ' running' : ['error','disconnected','delivery_uncertain'].includes(status) ? ' error' : '');
  ui.stop.classList.toggle('hidden', !['running','awaiting_approval'].includes(status));
  ui.pill.textContent = ({idle:'Codex 已连接',running:'Codex 执行中',awaiting_approval:'等待审批',disconnected:'连接中断',delivery_uncertain:'送达待核实',error:'连接错误'})[status] || status;
  ui.pill.className = 'session-pill ' + (status === 'running' ? 'running' : status === 'idle' ? 'open' : status === 'awaiting_approval' ? 'queued' : 'error');
  renderQueue();
  renderApprovals();
  updateSubmitLabel();
}
function renderExternalWorkspace(workspace) {
  const waiting = state.agent.status === 'waiting_for_mcp';
  const waitId = workspace.external_wait_id || workspace.wait_id || null;
  if (!waiting || (waitId && state.externalWaitId && waitId !== state.externalWaitId)) state.externalSubmitted = false;
  if (waiting && state.queue.at(-1)?.status === 'awaiting_mcp') state.externalSubmitted = true;
  state.externalWaitId = waitId;
  ui.feedbackIntro.textContent = 'Codex 请求反馈时，在这里圈画并提交。反馈会交回正在等待的 MCP 工具调用。';
  ui.agentStatus.textContent = waiting
    ? state.externalSubmitted ? '视觉反馈已提交，正在交回当前 Codex 工具调用。'
      : '当前 Codex 的反馈工具正在等待你的圈画和文字。'
    : '当前没有等待中的反馈工具调用。请在 Codex 会话中请求视觉反馈。';
  if (state.agent.error) ui.agentStatus.textContent += '：' + String(state.agent.error).slice(0, 240);
  ui.agentStatus.className = 'agent-status' + (waiting ? ' running' : '');
  ui.pill.textContent = waiting ? state.externalSubmitted ? '反馈已提交' : '工具等待反馈' : '等待 Codex 调用';
  ui.pill.className = 'session-pill ' + (waiting ? 'running' : 'open');
  ui.stop.classList.add('hidden');
  ui.approvals.replaceChildren();
  renderExternalDelivery();
  if (ui.conversation.firstElementChild?.classList.contains('muted')) {
    ui.conversation.firstElementChild.textContent = '此模式由 MCP 工具交回反馈；Codex 的完整对话仍显示在原会话中。';
  }
  updateSubmitLabel();
}
function renderExternalDelivery() {
  ui.queue.replaceChildren();
  const item = state.queue.at(-1);
  if (!item || !['queued','submitted','awaiting_mcp','returned_to_mcp'].includes(item.status)) return;
  const card = document.createElement('div');
  card.className = 'queue-card';
  const title = document.createElement('strong');
  title.textContent = item.status === 'returned_to_mcp' ? '已交回当前 Codex 工具' : '反馈已提交';
  const body = document.createElement('div');
  body.textContent = item.status === 'returned_to_mcp'
    ? '图像和标记已经作为 MCP 工具结果返回。'
    : '工作台已保存图像和标记，等待当前 MCP 工具读取。';
  card.append(title, body);
  ui.queue.append(card);
}
function renderQueue() {
  ui.queue.replaceChildren();
  for (const item of state.queue) {
    if (!item || item.status === 'completed') continue;
    const card = document.createElement('div');
    card.className = 'queue-card';
    const title = document.createElement('strong');
    title.textContent = ({queued:'已加入下一轮',dispatching:'正在送达',running:'正在处理',blocked_stale:'请确认旧场景反馈',delivery_uncertain:'送达待核实',failed:'发送失败'})[item.status] || '待处理消息';
    const body = document.createElement('div');
    body.textContent = '针对场景版本 ' + (item.scene_revision ?? '—') + (item.status === 'blocked_stale' ? '，当前场景已有新版本。确认后仍按旧截图发送。' : '');
    card.append(title, body);
    if (item.status === 'blocked_stale' && item.feedback_id) {
      const actions = document.createElement('div');
      actions.className = 'queue-actions';
      const confirm = document.createElement('button');
      confirm.type = 'button';
      confirm.textContent = '确认按旧截图发送';
      confirm.addEventListener('click', async () => {
        confirm.disabled = true;
        try {
          await api('/api/workspace/queue/' + encodeURIComponent(item.feedback_id) + '/confirm', {method:'POST', body:{confirm:true}});
          announce('已确认；Codex 空闲后会处理这条消息。');
          await refreshWorkspace();
        } catch (error) { announce('确认失败：' + error.message, true); confirm.disabled = false; }
      });
      actions.append(confirm);
      const discard = document.createElement('button');
      discard.type = 'button';
      discard.textContent = '舍弃这条';
      discard.addEventListener('click', async () => {
        confirm.disabled = discard.disabled = true;
        try {
          await api('/api/workspace/queue/' + encodeURIComponent(item.feedback_id) + '/confirm', {method:'POST', body:{confirm:false}});
          await refreshWorkspace();
        } catch (error) { announce('舍弃失败：' + error.message, true); confirm.disabled = discard.disabled = false; }
      });
      actions.append(discard);
      card.append(actions);
    }
    if (item.status === 'delivery_uncertain' && item.feedback_id) {
      const warning = document.createElement('div');
      warning.textContent = 'Gateway 在发送途中中断。请先核对 Codex 会话，再选择重试或舍弃。';
      const actions = document.createElement('div');
      actions.className = 'queue-actions';
      for (const [retry,label] of [[true,'确认未送达，重试'],[false,'已处理，舍弃']]) {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = label;
        button.addEventListener('click', async () => {
          actions.querySelectorAll('button').forEach((node) => node.disabled = true);
          try {
            await api('/api/workspace/queue/' + encodeURIComponent(item.feedback_id) + '/confirm', {method:'POST', body:{retry_uncertain:retry}});
            await refreshWorkspace();
          } catch (error) { announce('处理失败：' + error.message, true); actions.querySelectorAll('button').forEach((node) => node.disabled = false); }
        });
        actions.append(button);
      }
      card.append(warning, actions);
    }
    ui.queue.append(card);
  }
}
function approvalDetails(approval) {
  if (approval.details && typeof approval.details === 'object' && !Array.isArray(approval.details)) return approval.details;
  try {
    const parsed = JSON.parse(approval.prompt);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed;
  } catch { /* Older requests can contain a plain prompt. */ }
  return {};
}
function approvalText(parent, value, className='approval-detail') {
  if (value === undefined || value === null || value === '') return;
  const element = document.createElement('div');
  element.className = className;
  element.textContent = String(value);
  parent.append(element);
  return element;
}
function approvalJsonInput(parent, initial, label='结构化 JSON 响应') {
  const field = document.createElement('label');
  field.className = 'approval-field';
  const heading = document.createElement('span');
  heading.textContent = label;
  const editor = document.createElement('textarea');
  editor.className = 'approval-json';
  editor.rows = 5;
  editor.spellcheck = false;
  editor.value = JSON.stringify(initial, null, 2);
  field.append(heading, editor);
  parent.append(field);
  return () => {
    let value;
    try { value = JSON.parse(editor.value); }
    catch { throw new Error('请填写有效的 JSON。'); }
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('JSON 结果必须是对象。');
    return value;
  };
}
function approvalSchemaInput(parent, name, property, required) {
  const schema = property && typeof property === 'object' ? property : {};
  const rawType = Array.isArray(schema.type) ? schema.type.find((item) => item !== 'null') : schema.type;
  const type = rawType || (schema.enum ? typeof schema.enum[0] : 'string');
  const field = document.createElement('label');
  field.className = 'approval-field';
  const heading = document.createElement('span');
  heading.textContent = (schema.title || name) + (required ? ' *' : '');
  field.append(heading);
  let control;
  if (Array.isArray(schema.enum)) {
    control = document.createElement('select');
    if (!required) {
      const blank = document.createElement('option');
      blank.value = '';
      blank.textContent = '不填写';
      control.append(blank);
    } else if (schema.default === undefined) {
      const blank = document.createElement('option');
      blank.value = '';
      blank.textContent = '请选择';
      blank.disabled = true;
      blank.selected = true;
      control.append(blank);
    }
    schema.enum.forEach((value, index) => {
      const option = document.createElement('option');
      option.value = String(index);
      option.textContent = String(value);
      if (schema.default === value) option.selected = true;
      control.append(option);
    });
  } else if (type === 'boolean') {
    control = document.createElement('input');
    control.type = 'checkbox';
    control.checked = schema.default === true;
  } else if (type === 'object' || type === 'array') {
    control = document.createElement('textarea');
    control.className = 'approval-json';
    control.rows = 3;
    control.spellcheck = false;
    control.placeholder = type === 'array' ? '[]' : '{}';
    if (schema.default !== undefined) control.value = JSON.stringify(schema.default, null, 2);
  } else if (schema.format === 'textarea' || schema.format === 'multiline') {
    control = document.createElement('textarea');
    control.rows = 3;
    control.value = schema.default === undefined ? '' : String(schema.default);
  } else {
    control = document.createElement('input');
    control.type = type === 'integer' || type === 'number' ? 'number'
      : schema.format === 'email' ? 'email' : schema.format === 'uri' ? 'url' : 'text';
    if (type === 'integer') control.step = '1';
    else if (type === 'number') control.step = 'any';
    control.value = schema.default === undefined ? '' : String(schema.default);
  }
  control.dataset.fieldName = name;
  if (required && type !== 'boolean') control.required = true;
  if (Number.isFinite(schema.minimum) && 'min' in control) control.min = String(schema.minimum);
  if (Number.isFinite(schema.maximum) && 'max' in control) control.max = String(schema.maximum);
  if (Number.isInteger(schema.minLength) && 'minLength' in control) control.minLength = schema.minLength;
  if (Number.isInteger(schema.maxLength) && 'maxLength' in control) control.maxLength = schema.maxLength;
  if (typeof schema.pattern === 'string' && 'pattern' in control) control.pattern = schema.pattern;
  field.append(control);
  if (schema.description) approvalText(field, schema.description, 'approval-help');
  parent.append(field);
  return () => {
    if (!control.reportValidity()) throw new Error('请检查“' + (schema.title || name) + '”。');
    if (type === 'boolean') return {present:true, value:control.checked};
    const raw = control.value.trim();
    if (!raw) {
      if (required) throw new Error('请填写“' + (schema.title || name) + '”。');
      return {present:false};
    }
    if (Array.isArray(schema.enum)) {
      const index = Number(raw);
      if (!Number.isInteger(index) || index < 0 || index >= schema.enum.length) throw new Error('请为“' + name + '”选择有效选项。');
      return {present:true, value:schema.enum[index]};
    }
    if (type === 'integer' || type === 'number') {
      const number = Number(raw);
      if (!Number.isFinite(number) || (type === 'integer' && !Number.isInteger(number))) throw new Error('“' + name + '”需要数字。');
      return {present:true, value:number};
    }
    if (type === 'object' || type === 'array') {
      let value;
      try { value = JSON.parse(raw); }
      catch { throw new Error('“' + name + '”需要有效 JSON。'); }
      if (type === 'array' ? !Array.isArray(value) : !value || typeof value !== 'object' || Array.isArray(value)) {
        throw new Error('“' + name + '”的 JSON 类型不正确。');
      }
      return {present:true, value};
    }
    return {present:true, value:control.value};
  };
}
function approvalForm(parent, schema) {
  const properties = schema && typeof schema.properties === 'object' && !Array.isArray(schema.properties)
    ? schema.properties : {};
  const required = Array.isArray(schema?.required) ? schema.required : [];
  if ((schema?.type && schema.type !== 'object') || required.some((name) => !(name in properties))) {
    return approvalJsonInput(parent, {}, '表单内容（JSON 对象）');
  }
  const entries = Object.entries(properties);
  if (!entries.length) return () => ({});
  const fields = entries.map(([name, property]) => [name, approvalSchemaInput(parent, name, property, required.includes(name))]);
  return () => {
    const content = {};
    for (const [name, read] of fields) {
      const result = read();
      if (result.present) content[name] = result.value;
    }
    return content;
  };
}
function approvalQuestions(parent, questions) {
  const entries = Array.isArray(questions) ? questions : [];
  const readers = entries.map((question, index) => {
    const questionId = String(question.id || index + 1);
    const field = document.createElement('fieldset');
    field.className = 'approval-question';
    const legend = document.createElement('legend');
    legend.textContent = String(question.header || '问题 ' + (index + 1));
    field.append(legend);
    approvalText(field, question.question || question.prompt || question.description, 'approval-detail');
    const options = Array.isArray(question.options) && !question.isSecret ? question.options : [];
    if (!options.length) {
      const input = question.isSecret ? document.createElement('input') : document.createElement('textarea');
      if (question.isSecret) {
        input.type = 'password';
        input.autocomplete = 'off';
      } else {
        input.rows = 2;
      }
      input.placeholder = '填写回答';
      input.setAttribute('aria-label', questionId + ' 回答');
      field.append(input);
      parent.append(field);
      return [questionId, () => {
        const answer = input.value.trim();
        if (!answer) throw new Error('请回答“' + (question.header || questionId) + '”。');
        return answer;
      }];
    }
    const groupName = 'approval-' + newId();
    let otherInput = null;
    for (const option of options) {
      const row = document.createElement('label');
      row.className = 'approval-option';
      const radio = document.createElement('input');
      radio.type = 'radio';
      radio.name = groupName;
      radio.value = String(option.label || option.value || '');
      row.append(radio);
      const text = document.createElement('span');
      text.textContent = String(option.label || option.value || '其他');
      row.append(text);
      if (option.description) approvalText(row, option.description, 'approval-help');
      if (option.isOther) {
        otherInput = document.createElement('input');
        otherInput.type = 'text';
        otherInput.placeholder = '填写其他答案';
        otherInput.setAttribute('aria-label', questionId + ' 其他答案');
        otherInput.addEventListener('focus', () => { radio.checked = true; });
        row.append(otherInput);
        radio.dataset.other = 'true';
      }
      field.append(row);
    }
    parent.append(field);
    return [questionId, () => {
      const selected = field.querySelector('input[type="radio"]:checked');
      if (!selected) throw new Error('请选择“' + (question.header || questionId) + '”的答案。');
      if (selected.dataset.other === 'true') {
        const answer = otherInput?.value.trim();
        if (!answer) throw new Error('请填写其他答案。');
        return answer;
      }
      return selected.value;
    }];
  });
  return () => {
    const answers = {};
    for (const [questionId, read] of readers) answers[questionId] = {answers:[read()]};
    return answers;
  };
}
function approvalButton(actions, label, makePayload, approval, card) {
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = label;
  button.addEventListener('click', async () => {
    let payload;
    try { payload = makePayload(); }
    catch (error) { announce(error.message, true); return; }
    actions.querySelectorAll('button').forEach((node) => node.disabled = true);
    try {
      await api('/api/workspace/approvals/' + encodeURIComponent(approval.approval_id) + '/respond', {method:'POST', body:payload});
      card.remove();
      try { await refreshWorkspace(); }
      catch { announce('审批已送达，工作台状态会稍后刷新。'); }
    } catch (error) {
      announce('审批响应失败：' + error.message, true);
      actions.querySelectorAll('button').forEach((node) => node.disabled = false);
    }
  });
  actions.append(button);
}
function createApprovalCard(approval) {
  const details = approvalDetails(approval);
  const kind = approval.kind || '';
  const card = document.createElement('div');
  card.className = 'approval-card';
  card.dataset.approvalId = approval.approval_id;
  const title = document.createElement('strong');
  const body = document.createElement('div');
  body.className = 'approval-detail';
  const fields = document.createElement('div');
  fields.className = 'approval-fields';
  const actions = document.createElement('div');
  actions.className = 'approval-actions';
  const addDecision = (decision, label, extra) =>
    approvalButton(actions, label, () => ({decision, ...(extra ? extra() : {})}), approval, card);
  if (approval.details_truncated) {
    title.textContent = '审批内容过长';
    approvalText(body, '请求内容超过工作台可安全展示的长度，不能在这里同意。请拒绝或取消，并让 Codex 缩短请求。');
    approvalText(fields, approval.prompt || kind, 'approval-raw');
    if (kind === 'item/commandExecution/requestApproval' || kind === 'item/fileChange/requestApproval' ||
        kind === 'mcpServer/elicitation/request' || kind === 'item/permissions/requestApproval' ||
        kind === 'item/tool/requestUserInput') {
      addDecision('decline', '拒绝');
      addDecision('cancel', '取消');
    } else {
      approvalText(fields, '此请求没有已知的安全回应格式。可以中断当前这一轮，让 Codex 重新提出更短的请求。', 'approval-help');
      const stop = document.createElement('button');
      stop.type = 'button';
      stop.textContent = '停止本轮';
      stop.addEventListener('click', async () => {
        stop.disabled = true;
        try {
          await api('/api/workspace/interrupt', {method:'POST', body:{}});
          announce('已请求中断本轮。');
          await refreshWorkspace();
        } catch (error) {
          announce('中断失败：' + error.message, true);
          stop.disabled = false;
        }
      });
      actions.append(stop);
    }
    card.append(title, body, fields, actions);
    return card;
  }
  if (kind === 'mcpServer/elicitation/request') {
    const mode = details.mode;
    title.textContent = 'MCP 请求用户输入 · ' + (details.serverName || '服务');
    approvalText(body, details.message || details.title || details.description || 'MCP 服务正在等待你的回应。');
    if (mode === 'url') {
      const address = details.url;
      let validUrl = false;
      try {
        const url = new URL(address);
        if (!['https:','http:'].includes(url.protocol)) throw new Error('unsupported protocol');
        validUrl = true;
        const link = document.createElement('a');
        link.className = 'approval-link';
        link.href = url.href;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = '打开请求页面：' + url.href;
        fields.append(link);
      } catch { approvalText(fields, '请求中的链接无效，请拒绝或取消。', 'approval-help'); }
      approvalText(fields, '同意打开只授权 URL 流程；完成网页操作后仍需按页面提示继续。', 'approval-help');
      if (validUrl) addDecision('accept', '同意打开');
    } else if (mode === 'openai/userVerification') {
      if (details.challenge !== undefined) {
        approvalText(fields, typeof details.challenge === 'string' ? details.challenge : JSON.stringify(details.challenge, null, 2), 'approval-raw');
      }
      approvalText(fields, '请先完成上面的验证步骤，再决定是否接受。', 'approval-help');
      addDecision('accept', '接受请求');
    } else if (['form','openai/form','openaiForm'].includes(mode)) {
      if (details.requestedSchema && typeof details.requestedSchema === 'object' && !Array.isArray(details.requestedSchema)) {
        const content = approvalForm(fields, details.requestedSchema);
        addDecision('accept', '提交表单', () => ({content:content()}));
      } else {
        approvalText(fields, '请求没有有效的表单结构，请拒绝或取消。', 'approval-help');
      }
    } else {
      approvalText(fields, '工作台暂不支持此 MCP 交互模式：' + String(mode || '未指定') + '。请拒绝或取消，并让服务改用表单或 URL。', 'approval-help');
      approvalText(fields, JSON.stringify(details, null, 2), 'approval-raw');
    }
    addDecision('decline', '拒绝');
    addDecision('cancel', '取消');
  } else if (kind === 'item/tool/requestUserInput' || kind === 'tool/requestUserInput') {
    title.textContent = 'Codex 需要你的回答';
    approvalText(body, details.message || details._meta?.message || '请回答以下问题。');
    if (details._meta?.codex_approval_kind === 'mcp_tool_call') {
      const parameters = (details._meta.tool_params_display || []).map((item) =>
        (item.display_name || item.name) + ': ' + String(item.value)).join('\n');
      approvalText(body, parameters);
    }
    const answers = approvalQuestions(fields, details.questions);
    addDecision('accept', '提交回答', () => ({answers:answers()}));
    addDecision('decline', '拒绝');
    addDecision('cancel', '取消');
  } else if (kind === 'item/permissions/requestApproval') {
    title.textContent = 'Codex 请求权限';
    approvalText(body, details.reason || details.message || '请检查所请求的权限。');
    if (details.cwd) approvalText(body, '工作目录：' + details.cwd);
    const requested = details.permissions || details.requestedPermissions || details.requested || {};
    approvalText(fields, '请求的权限范围（授权时将原样授予）：', 'approval-help');
    approvalText(fields, JSON.stringify(requested, null, 2), 'approval-raw');
    const scopeLabel = document.createElement('label');
    scopeLabel.className = 'approval-field';
    const scopeHeading = document.createElement('span');
    scopeHeading.textContent = '授权时长';
    const scope = document.createElement('select');
    for (const [value, label] of [['turn','仅本轮'],['session','此会话']]) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = label;
      scope.append(option);
    }
    scopeLabel.append(scopeHeading, scope);
    fields.append(scopeLabel);
    addDecision('accept', '授权', () => ({scope:scope.value}));
    addDecision('decline', '拒绝');
  } else if (kind === 'item/commandExecution/requestApproval' || kind === 'item/fileChange/requestApproval') {
    title.textContent = kind.includes('fileChange') ? 'Codex 请求修改文件' : 'Codex 请求执行命令';
    approvalText(body, details.reason || details.message);
    if (details.command) approvalText(body, details.command, 'approval-raw');
    else if (details.changes) approvalText(body, JSON.stringify(details.changes, null, 2), 'approval-raw');
    else if (!body.textContent) approvalText(body, approval.prompt || kind);
    addDecision('accept', '允许');
    addDecision('decline', '拒绝');
  } else {
    title.textContent = 'Codex 请求结构化回应';
    approvalText(body, kind || '未知请求');
    approvalText(fields, JSON.stringify(details, null, 2), 'approval-raw');
    const result = approvalJsonInput(fields, {}, '返回给 Codex 的 JSON 对象');
    approvalButton(actions, '发送 JSON 结果', () => ({result:result()}), approval, card);
  }
  card.append(title, body, fields, actions);
  return card;
}
function renderApprovals() {
  const pending = new Map(state.approvals.filter((item) => item?.approval_id)
    .map((item) => [item.approval_id, item]));
  for (const card of [...ui.approvals.children]) {
    if (!pending.has(card.dataset.approvalId)) card.remove();
  }
  const rendered = new Set([...ui.approvals.children].map((card) => card.dataset.approvalId));
  for (const approval of pending.values()) {
    if (!rendered.has(approval.approval_id)) ui.approvals.append(createApprovalCard(approval));
  }
}
function addConversation(type, message, time) {
  if (!message) return;
  if (ui.conversation.firstElementChild?.classList.contains('muted')) ui.conversation.replaceChildren();
  const card = document.createElement('div');
  card.className = 'conversation-item' + (type === 'user' ? ' user' : type === 'error' ? ' error' : '');
  const label = document.createElement('small');
  label.textContent = (type === 'user' ? '你' : type === 'assistant' ? 'Codex' : '执行状态') + (time ? ' · ' + new Date(time).toLocaleTimeString() : '');
  const content = document.createElement('div');
  content.textContent = String(message).slice(0, 4000);
  card.append(label, content);
  ui.conversation.append(card);
  while (ui.conversation.childElementCount > 100) ui.conversation.firstElementChild.remove();
  ui.conversation.scrollTop = ui.conversation.scrollHeight;
}
async function fetchEvents(initial=false) {
  const events = await api('/api/workspace/events?after=' + (initial ? 0 : state.eventCursor));
  const items = Array.isArray(events.items) ? events.items : [];
  for (const event of items) {
    if (!event || state.seenEventIds.has(event.id)) continue;
    state.seenEventIds.add(event.id);
    const payload = event.payload || {};
    const message = payload.text || payload.message || payload.summary || payload.prompt;
    if (state.deliveryMode === 'external') {
      if (event.type === 'feedback_queued' || event.type === 'external_feedback_submitted' || event.type === 'feedback_submitted') {
        addConversation('user', payload.note || '已提交视觉反馈（含原图、标记和场景截图）', event.at);
      } else if (event.type === 'feedback_returned_to_mcp' || event.type === 'mcp_feedback_returned') {
        addConversation('status', '视觉反馈已交回当前 Codex 工具调用。', event.at);
      } else if (event.type === 'scene_published') {
        addConversation('status', message || '新场景已发布。', event.at);
      } else if (event.type === 'feedback_requested' || event.type === 'external_feedback_requested') {
        addConversation('assistant', message || 'Codex 正在请求视觉反馈。', event.at);
      }
      continue;
    }
    if (event.type === 'assistant_message' || event.type === 'assistant_text' || event.type === 'agent_message') {
      addConversation('assistant', message, event.at);
    } else if (event.type === 'feedback_queued') {
      addConversation('user', payload.note || '已发送视觉反馈（含图片和标注）', event.at);
    } else if (event.type === 'turn_started') {
      addConversation('status', 'Codex 开始处理这一轮。', event.at);
    } else if (event.type === 'turn_completed') {
      addConversation('status', message || '这一轮已完成。', event.at);
    } else if (event.type === 'turn_failed' || event.type === 'disconnected') {
      addConversation('error', message || '执行中断，请检查连接。', event.at);
    } else if (event.type === 'scene_published') {
      addConversation('status', message || '新场景已发布。', event.at);
    } else if (event.type === 'feedback_requested') {
      addConversation('assistant', message || '请查看当前场景并给出反馈。', event.at);
    } else if (event.type === 'approval_requested') {
      addConversation('status', 'Codex 正在等待审批。', event.at);
    }
  }
  state.eventCursor = Number.isInteger(events.next_cursor) ? events.next_cursor : state.eventCursor;
  if (items.length) saveDraft();
}
async function refreshWorkspace() {
  const workspace = await api('/api/workspace/state');
  if (workspace.session_id !== state.sessionId) throw new Error('项目会话已改变；请刷新工作台');
  state.browserCapability = workspace.browser_capability || state.browserCapability;
  renderWorkspace(workspace);
  await fetchEvents();
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
function referenceCamera(ref) {
  const raw = ref?.camera;
  const intrinsic = raw?.intrinsics || raw;
  const matrix = raw?.camera_to_world;
  if (!Array.isArray(matrix) || matrix.length !== 4 ||
      matrix.some((row) => !Array.isArray(row) || row.length !== 4 || row.some((n) => typeof n !== 'number' || !Number.isFinite(n)))) return null;
  if (!intrinsic || !['width','height','fx','fy','cx','cy'].every((key) =>
      typeof intrinsic[key] === 'number' && Number.isFinite(intrinsic[key]))) return null;
  if (intrinsic.width <= 0 || intrinsic.height <= 0 || intrinsic.fx <= 0 || intrinsic.fy <= 0 ||
      Math.abs(matrix[3][0]) > 1e-5 || Math.abs(matrix[3][1]) > 1e-5 ||
      Math.abs(matrix[3][2]) > 1e-5 || Math.abs(matrix[3][3] - 1) > 1e-5) return null;
  return {raw, intrinsic, matrix};
}
function updateAlignmentStatus() {
  const ref = activeReference();
  const pose = referenceCamera(ref);
  ui.alignReference.disabled = !pose;
  ui.alignReference.classList.toggle('aligned', !!pose && state.alignedReferenceId === ref?.id && state.alignmentExact);
  if (!ref) {
    ui.alignReference.textContent = '对齐参考视角';
    ui.alignReference.title = '请先选择参考图';
    ui.alignmentStatus.textContent = '选择带相机位姿的参考图，可让场景自动切换到同一视角。';
    return;
  }
  if (!pose) {
    ui.alignReference.textContent = '对齐参考视角';
    ui.alignReference.title = '当前参考图没有可用的相机位姿';
    ui.alignmentStatus.textContent = '这张图没有可用的相机位姿；仍可手动旋转场景对照。';
    return;
  }
  const current = state.alignedReferenceId === ref.id;
  ui.alignReference.textContent = current && state.alignmentExact ? '✓ 已对齐机位' : '对齐参考视角';
  ui.alignReference.title = current ? '重新应用这张参考图的相机位姿' : '切换到这张参考图的拍摄机位';
  const caveats = [];
  if (pose.raw?.calibration_status?.includes('proxy')) caveats.push('内参为近似值');
  if (Array.isArray(pose.raw?.distortion) && pose.raw.distortion.some((n) => Math.abs(n) > 1e-8) &&
      !pose.raw.image_undistorted && !ref.alignment_image_url) caveats.push('镜头畸变未校正');
  const caveat = caveats.length ? '；' + caveats.join('，') + '，边缘可能有偏差' : '';
  if (current && state.alignmentExact) {
    ui.alignmentStatus.textContent = '已按参考图机位对齐' + (ref.alignment_image_url ? '；右侧叠图使用去畸变图' : '') + caveat + '。';
  } else if (current) {
    ui.alignmentStatus.textContent = '已手动调整视角；点击「对齐参考视角」恢复拍摄机位' + caveat + '。';
  } else {
    ui.alignmentStatus.textContent = '这张图有相机位姿；点击「对齐参考视角」切换机位' + caveat + '。';
  }
}
function alignmentOverlayUrl(ref) {
  return state.alignedReferenceId === ref?.id && ref?.alignment_image_url || ref?.url;
}
function leaveReferenceCamera() {
  if (!state.alignedReferenceId) return;
  state.alignedReferenceId = null;
  state.alignmentExact = false;
  camera.up.set(0, 0, 1);
  camera.fov = 44;
  camera.updateProjectionMatrix();
  controls.update();
  resizeScene();
  showActiveReference();
  saveDraft();
}
function alignActiveReference({showLive=true, notify=false}={}) {
  const ref = activeReference();
  const pose = referenceCamera(ref);
  if (!pose) {
    updateAlignmentStatus();
    return false;
  }
  // Clear any remaining damped orbit motion before placing the recorded camera.
  const damping = controls.enableDamping;
  controls.enableDamping = false;
  controls.update();
  controls.enableDamping = damping;
  const matrix = new THREE.Matrix4().set(...pose.matrix.flat());
  const rotation = new THREE.Quaternion().setFromRotationMatrix(matrix);
  const forward = new THREE.Vector3(0, 0, -1).applyQuaternion(rotation);
  const up = new THREE.Vector3(0, 1, 0).applyQuaternion(rotation);
  const position = new THREE.Vector3().setFromMatrixPosition(matrix);
  const box = new THREE.Box3();
  for (const root of state.objectNodes.values()) box.expandByObject(root);
  const sceneCenter = box.isEmpty() ? new THREE.Vector3(0, 0, 0) : box.getCenter(new THREE.Vector3());
  const distance = clamp(sceneCenter.sub(position).dot(forward), 0.5, 100);
  camera.position.copy(position);
  camera.up.copy(up);
  camera.quaternion.copy(rotation);
  controls.target.copy(position).addScaledVector(forward, distance);
  controls.update();
  state.alignedReferenceId = ref.id;
  state.alignmentExact = true;
  state.firstFrame = false;
  if (showLive && state.sceneView === 'snapshot') {
    state.sceneView = 'live';
    renderSceneView();
  }
  showActiveReference();
  resizeScene();
  saveDraft();
  if (notify) announce('已切换到「' + (ref.name || '参考图') + '」的拍摄机位。');
  return true;
}
function frameBox(box) {
  if (!box || box.isEmpty()) return;
  leaveReferenceCamera();
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
  id('scene-title').textContent = scene.name || 'Codex 的当前结果';
  if (priorRevision !== null) {
    announce('场景已更新到版本 ' + scene.revision + '。已有标注仍绑定原截图。');
  }
  renderSceneView();
  saveDraft();
}

function captureLiveScene() {
  controls.update();
  renderer.render(threeScene, camera);
  const source = renderer.domElement;
  const canvas = scaledCanvas(source.width, source.height, 1440);
  canvas.getContext('2d').drawImage(source, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL('image/jpeg', 0.88);
}
function freezeScene() {
  if (!editable() || state.sceneView !== 'live') return;
  const oldMarks = state.annotations.filter((annotation) => annotation.pane === 'scene');
  if (oldMarks.length && !window.confirm('拍新截图会清除旧截图上的 ' + oldMarks.length + ' 条场景标记。继续？')) return;
  const shot = captureLiveScene();
  state.snapshot = {
    id:newId(), data_url:shot, scene_revision:state.sceneRevision,
    camera:cameraData(), selected_object_ids:state.selectedId ? [state.selectedId] : [],
    selected_scene_nodes:state.selectedSceneNode ? [{...state.selectedSceneNode}] : []
  };
  if (oldMarks.length) state.annotations = state.annotations.filter((annotation) => annotation.pane !== 'scene');
  state.sceneView = 'snapshot';
  renderSceneView();
  renderAnnotations();
  saveDraft();
  announce('已固定当前视角。现在可在这张截图上标注。');
}
function updateSnapshotGeometry() {
  if (!state.snapshot || !ui.snapshotImage.naturalWidth || !ui.snapshotImage.naturalHeight) return;
  const scale = Math.min(
    ui.sceneStage.clientWidth / ui.snapshotImage.naturalWidth,
    ui.sceneStage.clientHeight / ui.snapshotImage.naturalHeight
  );
  ui.snapshotMedia.style.width = Math.max(1, ui.snapshotImage.naturalWidth * scale) + 'px';
  ui.snapshotMedia.style.height = Math.max(1, ui.snapshotImage.naturalHeight * scale) + 'px';
  drawOverlays();
}
function renderSceneView() {
  const hasSnapshot = !!state.snapshot;
  if (!hasSnapshot) state.sceneView = 'live';
  const showingSnapshot = hasSnapshot && state.sceneView === 'snapshot';
  if (hasSnapshot && ui.snapshotImage.src !== state.snapshot.data_url) ui.snapshotImage.src = state.snapshot.data_url;
  ui.snapshotMedia.classList.toggle('hidden', !showingSnapshot);
  ui.browse.classList.toggle('hidden', !showingSnapshot);
  ui.snapshotButton.classList.toggle('hidden', !hasSnapshot || showingSnapshot);
  ui.freeze.classList.toggle('hidden', showingSnapshot);
  ui.newSceneBadge.classList.toggle('hidden', !hasSnapshot || state.snapshot.scene_revision === state.sceneRevision);
  if (hasSnapshot && state.snapshot.scene_revision !== state.sceneRevision) {
    ui.newSceneBadge.textContent = '新结果：版本 ' + state.sceneRevision + '；当前标注仍对应截图版本 ' + state.snapshot.scene_revision;
  }
  ui.compareImage.classList.toggle('hidden', showingSnapshot || !activeReference() || !ui.compareEnabled.checked);
  controls.enabled = state.mode === 'select' && !showingSnapshot;
  updateSnapshotGeometry();
  updateMode();
  updateSceneHint();
  saveDraft();
}

function setReferences(references) {
  if (JSON.stringify(state.references) === JSON.stringify(references)) return;
  const previousActive = activeReference();
  const previousCamera = JSON.stringify(previousActive?.camera || null);
  state.references = references;
  if (!references.some((ref) => ref.id === state.activeReferenceId)) {
    state.activeReferenceId = references[0]?.id || null;
  }
  renderReferenceStrip();
  showActiveReference();
  const current = activeReference();
  if (referenceCamera(current)) {
    if (state.restoredCameraForReference && state.alignedReferenceId === current.id &&
        state.restoredCameraSignature === JSON.stringify(current.camera)) {
      resizeScene();
      updateAlignmentStatus();
    } else if (previousActive?.id !== current.id || previousCamera !== JSON.stringify(current.camera)) {
      alignActiveReference({showLive:!!previousActive});
    }
  } else if (state.alignedReferenceId) {
    leaveReferenceCamera();
  }
  state.restoredCameraForReference = false;
  state.restoredCameraSignature = null;
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
    if (referenceCamera(ref)) {
      const cameraBadge = document.createElement('span');
      cameraBadge.className = 'thumb-camera';
      cameraBadge.textContent = '机位';
      button.append(cameraBadge);
      button.title = (ref.name || '参考图') + ' · 有相机位姿';
    }
    const count = state.annotations.filter((a) => a.pane === 'reference' && a.reference_image_id === ref.id).length;
    if (count) {
      const badge = document.createElement('span');
      badge.className = 'thumb-count';
      badge.textContent = String(count);
      button.append(badge);
    }
    button.addEventListener('click', () => {
      state.activeReferenceId = ref.id;
      state.referenceZoom = 1;
      state.referencePan = {x:0,y:0};
      renderReferenceStrip();
      showActiveReference();
      if (referenceCamera(ref)) alignActiveReference({notify:true});
      else leaveReferenceCamera();
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
  updateAlignmentStatus();
  ui.compareImage.classList.toggle('hidden', !hasReference || !ui.compareEnabled.checked || state.sceneView === 'snapshot');
  ui.compareEnabled.disabled = !hasReference;
  ui.compareOpacity.disabled = !hasReference || !ui.compareEnabled.checked;
  if (!hasReference) {
    ui.referenceImage.removeAttribute('src');
    drawOverlays();
    return;
  }
  ui.referenceImage.src = ref.url;
  const compareUrl = alignmentOverlayUrl(ref);
  if (ui.compareImage.getAttribute('src') !== compareUrl) ui.compareImage.src = compareUrl;
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
  updateReferenceTransform();
  drawOverlays();
}
function updateReferenceTransform() {
  ui.referenceMedia.style.transform = `translate(${state.referencePan.x}px, ${state.referencePan.y}px) scale(${state.referenceZoom})`;
}
function setReferenceZoom(nextZoom, anchorEvent) {
  const oldZoom = state.referenceZoom;
  const zoom = clamp(nextZoom, 1, 8);
  if (zoom === oldZoom) return;
  if (anchorEvent) {
    const rect = ui.referenceStage.getBoundingClientRect();
    const x = anchorEvent.clientX - rect.left - rect.width / 2;
    const y = anchorEvent.clientY - rect.top - rect.height / 2;
    const factor = zoom / oldZoom;
    state.referencePan.x = x - (x - state.referencePan.x) * factor;
    state.referencePan.y = y - (y - state.referencePan.y) * factor;
  }
  state.referenceZoom = zoom;
  if (zoom === 1) state.referencePan = {x:0,y:0};
  updateReferenceTransform();
  saveDraft();
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
      if (referenceCamera(activeReference())) alignActiveReference();
      else leaveReferenceCamera();
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
  const result = {
    position:array(camera.position), target:array(controls.target),
    up:array(camera.up), fov:camera.fov, aspect:camera.aspect
  };
  const ref = activeReference();
  const pose = state.alignedReferenceId === ref?.id && referenceCamera(ref);
  if (pose) {
    result.reference_image_id = ref.id;
    result.alignment_exact = state.alignmentExact;
    result.intrinsics = {...pose.intrinsic};
    result.projection = 'pinhole';
    if (pose.raw.calibration_status) result.calibration_status = pose.raw.calibration_status;
  }
  return result;
}
function updateSceneHint() {
  if (state.sceneView === 'live') {
    ui.sceneHint.textContent = state.mode === 'select'
      ? '拖拽旋转 · 滚轮缩放 · 点击对象 · 标注前先固定视角'
      : '点击「标注当前视角」后，在固定截图上圈画';
  } else if (state.mode === 'select') {
    ui.sceneHint.textContent = '固定截图 · 版本 ' + state.snapshot.scene_revision + ' · 点击「浏览新结果」可继续旋转';
  } else {
    ui.sceneHint.textContent = '在场景上' +
      ({point:'点一下',rectangle:'拖动框选',line:'拖动画线',arrow:'拖动画箭头',text:'点击加文字',freehand:'随手圈画'})[state.mode] +
      ' · 标记绑定当前截图';
  }
}
function updateMode() {
  document.querySelectorAll('.tool-button').forEach((button) => button.classList.toggle('active', button.dataset.tool === state.mode));
  const drawing = state.mode !== 'select' && state.sessionStatus === 'open';
  ui.referenceCanvas.style.pointerEvents = drawing ? 'auto' : 'none';
  ui.sceneCanvas.style.pointerEvents = drawing && state.sceneView === 'snapshot' ? 'auto' : 'none';
  ui.referenceCanvas.style.cursor = drawing ? 'crosshair' : 'default';
  ui.sceneCanvas.style.cursor = drawing ? 'crosshair' : 'default';
  controls.enabled = state.mode === 'select' && state.sceneView === 'live';
  ui.referenceHint.classList.toggle('hidden', !drawing || !activeReference());
  updateSceneHint();
  state.drag = null;
  drawOverlays();
}
function setMode(mode) {
  if (!['select','point','rectangle','line','arrow','text','freehand'].includes(mode)) return;
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
    id:newId(), pane:annotation.pane, type:annotation.type,
    coordinates:annotation.coordinates
  };
  if (state.groupId) item.group_id = state.groupId;
  if (annotation.pane === 'reference') item.reference_image_id = state.activeReferenceId;
  else {
    item.scene_revision = state.snapshot.scene_revision;
    item.snapshot_id = state.snapshot.id;
    item.camera = state.snapshot.camera;
    if (state.snapshot.selected_object_ids[0]) item.object_id = state.snapshot.selected_object_ids[0];
    if (state.snapshot.selected_scene_nodes[0]) item.scene_node = {...state.snapshot.selected_scene_nodes[0]};
  }
  if (annotation.text) item.text = annotation.text;
  if (annotation.points) item.points = annotation.points;
  state.annotations.push(item);
  renderAnnotations();
  drawOverlays();
  saveDraft();
}
function annotationPointerDown(event, pane) {
  if (!editable() || state.mode === 'select' || state.spacePan) return;
  if (pane === 'reference' && !activeReference()) return;
  if (pane === 'scene' && (state.sceneView !== 'snapshot' || !state.snapshot)) return;
  event.preventDefault();
  const canvas = event.currentTarget;
  const point = pointFromPointer(event, canvas);
  if (state.mode === 'text') {
    showTextEditor(pane, point, event.clientX, event.clientY);
    return;
  }
  canvas.setPointerCapture(event.pointerId);
  state.drag = {pane, type:state.mode, start:point, end:point, pointerId:event.pointerId,
    points:state.mode === 'freehand' ? [point] : null};
  drawOverlays();
}
function annotationPointerMove(event) {
  if (!state.drag || state.drag.pointerId !== event.pointerId) return;
  state.drag.end = pointFromPointer(event, event.currentTarget);
  if (state.drag.type === 'freehand' && state.drag.points.length < 256) {
    const last = state.drag.points.at(-1);
    if (Math.hypot(last.x - state.drag.end.x, last.y - state.drag.end.y) > 0.003) state.drag.points.push(state.drag.end);
  }
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
  } else if (drag.type === 'freehand' && drag.points.length > 1) {
    addAnnotation({pane:drag.pane, type:'freehand', coordinates:{x:drag.start.x, y:drag.start.y}, points:drag.points});
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
  const stale = annotation.pane === 'scene' && state.snapshot && annotation.snapshot_id !== state.snapshot.id;
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
  } else if (annotation.type === 'freehand' && Array.isArray(annotation.points) && annotation.points.length > 1) {
    ctx.beginPath();
    annotation.points.forEach((point, index) => {
      const px = clamp(Number(point.x) || 0, 0, 1) * width;
      const py = clamp(Number(point.y) || 0, 0, 1) * height;
      if (index === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    });
    ctx.stroke();
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
  // clientWidth is the bitmap's untransformed size. Using the transformed
  // bounding box here would allocate enormous canvases when a photo is zoomed.
  const logicalWidth = canvas.clientWidth;
  const logicalHeight = canvas.clientHeight;
  if (!logicalWidth || !logicalHeight) return null;
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.round(logicalWidth * ratio);
  const height = Math.round(logicalHeight * ratio);
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const context = canvas.getContext('2d');
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, logicalWidth, logicalHeight);
  return {context, width:logicalWidth, height:logicalHeight};
}
function drawOverlays() {
  for (const pane of ['reference','scene']) {
    const canvas = pane === 'reference' ? ui.referenceCanvas : ui.sceneCanvas;
    const surface = prepareCanvas(canvas);
    if (!surface) continue;
    for (const annotation of state.annotations) {
      if (annotation.pane !== pane) continue;
      if (pane === 'reference' && annotation.reference_image_id !== state.activeReferenceId) continue;
      if (pane === 'scene' && (!state.snapshot || annotation.snapshot_id !== state.snapshot.id)) continue;
      drawAnnotation(surface.context, annotation, surface.width, surface.height);
    }
    if (state.drag?.pane === pane) {
      drawAnnotation(surface.context, {
        pane, type:state.drag.type,
        coordinates:{x:state.drag.start.x, y:state.drag.start.y, x2:state.drag.end.x, y2:state.drag.end.y},
        points:state.drag.points
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
    const stale = annotation.pane === 'scene' && annotation.scene_revision !== state.sceneRevision;
    subtitle.textContent = annotation.text || (stale ? '固定截图版本 ' + annotation.scene_revision : annotation.object_id ? '对象：' + annotation.object_id : '视觉提示');
    if (stale) subtitle.classList.add('stale-label');
    copy.append(title, subtitle);
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'annotation-remove';
    remove.textContent = '×';
    remove.title = '删除这条标记';
    remove.disabled = state.sessionStatus !== 'open' || !!state.pendingSubmission;
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
        const xs = marks.flatMap((mark) => (mark.points?.length ? mark.points : [mark.coordinates,{x:mark.coordinates.x2 ?? mark.coordinates.x}]).map((point) => point.x));
        const ys = marks.flatMap((mark) => (mark.points?.length ? mark.points : [mark.coordinates,{y:mark.coordinates.y2 ?? mark.coordinates.y}]).map((point) => point.y));
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
function snapshotForFeedback() {
  if (!state.snapshot) return null;
  const sceneMarks = state.annotations.some((annotation) => annotation.pane === 'scene' && annotation.snapshot_id === state.snapshot.id);
  return state.sceneView === 'snapshot' || sceneMarks ? state.snapshot : null;
}
async function captureScene(snapshot) {
  const dataUrl = snapshot?.data_url || captureLiveScene();
  const source = await loadImage(dataUrl);
  const original = document.createElement('canvas');
  original.width = source.naturalWidth;
  original.height = source.naturalHeight;
  original.getContext('2d').drawImage(source, 0, 0);
  const annotated = document.createElement('canvas');
  annotated.width = original.width;
  annotated.height = original.height;
  const annotatedContext = annotated.getContext('2d');
  annotatedContext.drawImage(original, 0, 0);
  for (const annotation of state.annotations) {
    if (annotation.pane === 'scene' && snapshot && annotation.snapshot_id === snapshot.id) {
      drawAnnotation(annotatedContext, annotation, annotated.width, annotated.height);
    }
  }
  return {
    scene_original_data_url:dataUrl,
    scene_annotated_data_url:annotated.toDataURL('image/jpeg', 0.84)
  };
}
async function feedbackPayload() {
  const snapshot = snapshotForFeedback();
  const imageBundle = await captureScene(snapshot);
  const annotatedReferences = await captureReferenceAnnotations();
  const submittedCamera = snapshot?.camera || cameraData();
  return {
    scene_revision:snapshot?.scene_revision || state.sceneRevision,
    latest_scene_revision:state.sceneRevision,
    active_reference_id:state.activeReferenceId,
    aligned_reference_id:submittedCamera.alignment_exact && submittedCamera.reference_image_id === state.activeReferenceId
      ? state.activeReferenceId : null,
    note:ui.note.value.trim() || (state.references.length && !state.annotations.length ? '请参考这些图片开始或继续重建场景。' : ''),
    annotations:state.annotations.map((annotation) => {
      const item = {...annotation};
      if (!snapshot && item.object_id && !sceneObject(item.object_id)) {
        item.previous_object_id = item.object_id;
        delete item.object_id;
      }
      if (!snapshot && item.scene_node && sceneObject(item.scene_node.parent_object_id)?.type !== 'model') {
        item.previous_scene_node = item.scene_node;
        delete item.scene_node;
      }
      return item;
    }),
    selected_object_ids:snapshot?.selected_object_ids || (state.selectedId ? [state.selectedId] : []),
    selected_scene_nodes:snapshot?.selected_scene_nodes || (state.selectedSceneNode ? [{...state.selectedSceneNode}] : []),
    camera:submittedCamera,
    reference_images:state.references.map((ref) => ({id:ref.id, url:ref.url, name:ref.name})),
    reference_annotated_data_urls:annotatedReferences.images,
    crops:annotatedReferences.crops,
    ...imageBundle
  };
}
async function submitFeedback() {
  if (!state.workspaceReady || !state.sessionId || state.submitting || state.uploading) return;
  if (state.deliveryMode === 'external' && !state.pendingSubmission &&
      (state.agent.status !== 'waiting_for_mcp' || state.externalSubmitted)) {
    announce('当前没有等待反馈的 Codex 工具调用。请先在原会话中请求视觉反馈。', true);
    return;
  }
  if (!state.pendingSubmission && !state.annotations.length && !ui.note.value.trim() && !state.references.length) {
    announce('请添加参考图、画标记，或写一句话后再发送。', true);
    return;
  }
  const chosenSnapshot = snapshotForFeedback();
  const staleSnapshot = chosenSnapshot && chosenSnapshot.scene_revision !== state.sceneRevision;
  if (!state.pendingSubmission && staleSnapshot &&
      !window.confirm('这条反馈针对场景版本 ' + chosenSnapshot.scene_revision + ' 的固定截图，当前已是版本 ' + state.sceneRevision + '。仍按旧截图发送给 Codex？')) return;
  state.submitting = true;
  ui.submit.disabled = true;
  ui.submit.querySelector('span:first-child').textContent = '正在准备图片…';
  try {
    if (!state.pendingSubmission) {
      const payload = await feedbackPayload();
      const key = newId();
      state.pendingSubmission = {key, payload:{...payload, idempotency_key:key,
        confirm_stale:!!staleSnapshot}};
      try { await writeOutbox(state.pendingSubmission); }
      catch (error) { state.pendingSubmission = null; throw error; }
    }
    ui.submit.querySelector('span:first-child').textContent = '正在发送…';
    const result = await api('/api/sessions/' + encodeURIComponent(state.sessionId) + '/feedback', {
      method:'POST', body:state.pendingSubmission.payload
    });
    await clearOutbox();
    state.pendingSubmission = null;
    state.feedbackCount += 1;
    id('feedback-count-label').textContent = '已发 ' + state.feedbackCount + ' 轮';
    const delivery = result.delivery?.status || (state.deliveryMode === 'external' ? 'submitted' : 'queued');
    if (state.deliveryMode === 'external') state.externalSubmitted = true;
    ui.caption.textContent = '标记仍保留；再次发送前可删除或清空。';
    saveDraft();
    if (state.deliveryMode === 'external') {
      announce(delivery === 'returned_to_mcp'
        ? '视觉反馈已交回当前 Codex 工具调用。'
        : '视觉反馈已提交，正在等待当前 Codex 工具调用读取。');
    } else {
      announce(delivery === 'running' ? '图文消息已送进当前 Codex 会话。'
        : delivery === 'blocked_stale' ? '已保存消息；场景已更新，请在右侧确认旧截图后继续发送。'
        : delivery === 'delivery_uncertain' ? '消息已保存，送达状态待核实。'
        : '图文消息已加入 Codex 的下一轮。');
    }
    try { await refreshWorkspace(); } catch { /* The next poll will recover status. */ }
  } catch (error) {
    if (error.status === 409 && /scene revision changed/i.test(error.message)) {
      await clearOutbox();
      state.pendingSubmission = null;
      try { await loadScene(); } catch { /* Poll will retry. */ }
      announce('场景在发送时更新了，草稿已保留。请检查当前版本后再发送。', true);
    } else {
      announce('发送失败：' + error.message + '。可点击重试，系统会沿用同一消息编号。', true);
    }
  } finally {
    state.submitting = false;
    updateSubmitLabel();
    renderAnnotations();
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
  const ref = activeReference();
  const pose = state.alignedReferenceId === ref?.id && referenceCamera(ref);
  if (pose) {
    const overlay = ui.compareImage;
    const sourceMatches = overlay.getAttribute('src') === alignmentOverlayUrl(ref);
    const imageWidth = sourceMatches && overlay.naturalWidth || pose.intrinsic.width;
    const imageHeight = sourceMatches && overlay.naturalHeight || pose.intrinsic.height;
    const scale = Math.min(width / imageWidth, height / imageHeight);
    const viewWidth = Math.max(1, Math.round(imageWidth * scale));
    const viewHeight = Math.max(1, Math.round(imageHeight * scale));
    ui.viewport.style.left = (width - viewWidth) / 2 + 'px';
    ui.viewport.style.top = (height - viewHeight) / 2 + 'px';
    ui.viewport.style.right = 'auto';
    ui.viewport.style.bottom = 'auto';
    ui.viewport.style.width = viewWidth + 'px';
    ui.viewport.style.height = viewHeight + 'px';
    camera.near = 0.005;
    camera.far = 2000;
    camera.fov = 2 * Math.atan(pose.intrinsic.height / (2 * pose.intrinsic.fy)) * 180 / Math.PI;
    camera.aspect = imageWidth / imageHeight;
    camera.updateProjectionMatrix();
    const {width:iw, height:ih, fx, fy, cx, cy} = pose.intrinsic;
    const near = camera.near;
    camera.projectionMatrix.makePerspective(
      -cx * near / fx, (iw - cx) * near / fx,
      cy * near / fy, -(ih - cy) * near / fy,
      near, camera.far, renderer.coordinateSystem
    );
    camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
    renderer.setSize(viewWidth, viewHeight, false);
  } else {
    ui.viewport.style.left = '0';
    ui.viewport.style.top = '0';
    ui.viewport.style.right = '0';
    ui.viewport.style.bottom = '0';
    ui.viewport.style.width = '100%';
    ui.viewport.style.height = '100%';
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    renderer.setSize(width, height, false);
  }
  updateSnapshotGeometry();
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
    await refreshWorkspace();
  } catch {
    ui.agentStatus.textContent = '工作台连接中断，正在重试…';
    ui.agentStatus.className = 'agent-status error';
  }
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
  ui.compareImage.addEventListener('load', resizeScene);
  ui.alignReference.addEventListener('click', () => alignActiveReference({notify:true}));
  ui.referenceZoomIn.addEventListener('click', () => setReferenceZoom(state.referenceZoom * 1.4));
  ui.referenceZoomOut.addEventListener('click', () => setReferenceZoom(state.referenceZoom / 1.4));
  ui.referenceZoomReset.addEventListener('click', () => {
    state.referenceZoom = 1;
    state.referencePan = {x:0,y:0};
    updateReferenceTransform();
    saveDraft();
  });
  ui.referenceStage.addEventListener('wheel', (event) => {
    if (!activeReference()) return;
    event.preventDefault();
    setReferenceZoom(state.referenceZoom * (event.deltaY < 0 ? 1.15 : 1 / 1.15), event);
  }, {passive:false});
  ui.referenceStage.addEventListener('pointerdown', (event) => {
    if (!activeReference() || !(state.mode === 'select' || state.spacePan)) return;
    event.preventDefault();
    ui.referenceStage.setPointerCapture(event.pointerId);
    state.referencePanning = {pointerId:event.pointerId, x:event.clientX, y:event.clientY,
      panX:state.referencePan.x, panY:state.referencePan.y};
  });
  ui.referenceStage.addEventListener('pointermove', (event) => {
    const pan = state.referencePanning;
    if (!pan || pan.pointerId !== event.pointerId) return;
    state.referencePan = {x:pan.panX + event.clientX - pan.x, y:pan.panY + event.clientY - pan.y};
    updateReferenceTransform();
  });
  ui.referenceStage.addEventListener('pointerup', (event) => {
    if (state.referencePanning?.pointerId !== event.pointerId) return;
    state.referencePanning = null;
    saveDraft();
  });
  ui.referenceStage.addEventListener('pointercancel', () => { state.referencePanning = null; });
  ui.snapshotImage.addEventListener('load', updateSnapshotGeometry);
  ui.freeze.addEventListener('click', freezeScene);
  ui.browse.addEventListener('click', () => { state.sceneView = 'live'; setMode('select'); renderSceneView(); });
  ui.snapshotButton.addEventListener('click', () => { state.sceneView = 'snapshot'; renderSceneView(); });
  ui.compareEnabled.addEventListener('change', () => {
    ui.compareImage.classList.toggle('hidden', !ui.compareEnabled.checked || !activeReference() || state.sceneView === 'snapshot');
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
  ui.stop.addEventListener('click', async () => {
    ui.stop.disabled = true;
    try {
      await api('/api/workspace/interrupt', {method:'POST', body:{}});
      announce('已请求停止当前 Codex 执行。');
      await refreshWorkspace();
    } catch (error) { announce('停止失败：' + error.message, true); }
    finally { ui.stop.disabled = false; }
  });
  id('clear-annotations').addEventListener('click', () => {
    if (!editable()) return;
    state.annotations = [];
    renderAnnotations();
    drawOverlays();
    saveDraft();
  });
  ui.clearSelection.addEventListener('click', () => {
    if (!editable()) return;
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
  controls.addEventListener('start', () => {
    orbitStart = {position:camera.position.clone(), target:controls.target.clone(), quaternion:camera.quaternion.clone()};
  });
  controls.addEventListener('end', () => {
    const moved = orbitStart && (
      camera.position.distanceToSquared(orbitStart.position) > 1e-10 ||
      controls.target.distanceToSquared(orbitStart.target) > 1e-10 ||
      1 - Math.abs(camera.quaternion.dot(orbitStart.quaternion)) > 1e-10
    );
    orbitStart = null;
    if (moved && state.alignedReferenceId && state.alignmentExact) {
      state.alignmentExact = false;
      updateAlignmentStatus();
    }
    saveDraft();
  });
  document.addEventListener('keydown', (event) => {
    if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLSelectElement) return;
    if (event.code === 'Space') { event.preventDefault(); state.spacePan = true; }
    if (event.key >= '1' && event.key <= '7') {
      setMode(['select','point','rectangle','line','arrow','text','freehand'][Number(event.key) - 1]);
    }
    if (event.key === 'Escape') {
      state.drag = null;
      hideTextEditor();
      drawOverlays();
    }
    if (event.key === 'Backspace' && state.annotations.length && editable()) {
      event.preventDefault();
      state.annotations.pop();
      renderAnnotations();
      drawOverlays();
      saveDraft();
    }
  });
  document.addEventListener('keyup', (event) => { if (event.code === 'Space') state.spacePan = false; });
  window.addEventListener('blur', () => { state.spacePan = false; state.referencePanning = null; });
  window.addEventListener('beforeunload', saveDraft);
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
setInterval(poll, 1500);

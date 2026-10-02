import * as THREE from 'three';
import { SceneNavigation } from './scene-navigation.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { frameAtTime, nearestFrameAtTime, stepTime, feedbackScope, markMatchesMoment, viewForReferenceImage } from './dynamic.js';
import { setupMinimalLayout } from './layout.js';
import { setupImmersive } from './immersive.js';
import { setupTheme } from './theme.js';
import { setupWorkspaceControls } from './workspace-controls.js';
import { createAnnotationHistory } from './annotation-history.js';
import { eraserHitsAnnotation } from './eraser.js';
import { assignAnnotationNames } from './annotation-names.js';
import { drawAnnotationName, hitsAnnotationName } from './annotation-labels.js';
import { createFrameImageCache } from './frame-image-cache.js';
import { projectPrefix, scopedURL, projectNavigationURL } from './projects.js';
import { poseToken, collectPoseReferences, poseFrameForReference, poseFrameLabel, poseEvidenceLabel, drawPoseSkeleton, POSE_COLORS,
  handJointIndices, handJointLabel, poseEditToken, validPoseEdit, collectPoseEdits, correctedPoseFrame } from './human-pose.js';
import { imageToken, collectImageReferences, createPromptImageStore } from './prompt-images.js';
import { createPromptMentions } from './prompt-mentions.js';

const id = (name) => document.getElementById(name);
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const array = (value) => [value.x, value.y, value.z].map((n) => Number(n.toFixed(5)));
const workspacePrefix = projectPrefix(location.pathname);
const resourceURL = (url) => scopedURL(url, workspacePrefix);
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
const annotationFontFamily = getComputedStyle(document.body).fontFamily || 'sans-serif';
const ui = {
  sceneName:id('scene-name'), projectsButton:id('projects-dialog-button'), projectsDialog:id('projects-dialog'),
  taskButton:id('task-dialog-button'), tasksDialog:id('tasks-dialog'),
  directTaskHelp:id('direct-task-help'), eventTaskHelp:id('event-task-help'),
  directDeliveryStatusHelp:id('direct-delivery-status-help'), eventDeliveryStatusHelp:id('event-delivery-status-help'),
  directDeliveryHelp:id('direct-delivery-help'), eventDeliveryHelp:id('event-delivery-help'),
  projectsList:id('project-list'), projectsStatus:id('project-list-status'), refreshProjects:id('refresh-projects'),
  createProjectPanel:id('create-project-panel'), createProjectForm:id('create-project-form'),
  projectName:id('project-name'), projectModel:id('project-model'), projectEffort:id('project-effort'),
  projectPermissions:id('project-permissions'), createProject:id('create-project'),
  openCreatedProject:id('open-created-project'), createProjectHelp:id('create-project-help'),
  viewport:id('viewport'), sceneStage:id('scene-stage'), sceneCanvas:id('scene-annotations'),
  referenceStage:id('reference-stage'), referenceMedia:id('reference-media'),
  referenceImage:id('reference-image'), referenceCanvas:id('reference-annotations'),
  humanPanel:id('human-pose-panel'), humanCanvas:id('human-pose-overlay'),
  humanStatus:id('human-pose-status'), humanJobs:id('human-pose-jobs'),
  humanLatest:id('human-pose-latest'), humanHideAll:id('human-pose-hide-all'),
  poseEditPanel:id('pose-edit-panel'), poseEditSource:id('pose-edit-source'), poseEditHand:id('pose-edit-hand'),
  poseEditJoint:id('pose-edit-joint'), poseEditVisibility:id('pose-edit-visibility'), poseEditHint:id('pose-edit-hint'),
  poseEditReference:id('pose-edit-reference'), poseEditDownload:id('pose-edit-download'), poseEditFinish:id('pose-edit-finish'),
  poseEditReset:id('pose-edit-reset-joint'),
  referenceEmpty:id('reference-empty'), referenceStrip:id('reference-strip'),
  referenceTitle:id('reference-title'), referenceInput:id('reference-input'),
  clipInput:id('clip-input'), clipFps:id('clip-fps'), clipName:id('clip-name'), clipStatus:id('clip-import-status'),
  viewControl:id('reference-view-control'), viewSelect:id('reference-view-select'),
  timeline:id('timeline-panel'), play:id('timeline-play'), seek:id('timeline-seek'), time:id('timeline-time'),
  timelineSource:id('timeline-source'), moments:id('moment-strip'), animationChoices:id('animation-choices'),
  scope:id('feedback-scope'), range:id('feedback-range'), rangeStart:id('range-start'), rangeEnd:id('range-end'),
  referenceZoomOut:id('reference-zoom-out'), referenceZoomReset:id('reference-zoom-reset'), referenceZoomIn:id('reference-zoom-in'),
  alignReference:id('align-reference-button'), alignmentStatus:id('camera-alignment-status'),
  compareImage:id('compare-image'), snapshotCompareImage:id('snapshot-compare-image'), compareToggle:id('compare-toggle'),
  compareOpacity:id('compare-opacity'), opacityValue:id('opacity-value'),
  compareSummary:id('compare-summary'), compareStatus:id('compare-status'),
  comparePresets:[...document.querySelectorAll('[data-compare-opacity]')],
  targetPicker:id('target-picker'), currentTarget:id('current-target'), targetSelect:id('target-select'),
  targetChoiceDetails:id('target-choice-details'),
  switchTarget:id('switch-target'), refreshTargets:id('refresh-targets'), targetHelp:id('target-help'),
  manualTargetId:id('manual-target-id'), manualSwitchTarget:id('manual-switch-target'),
  createTargetPanel:id('create-target-panel'), createTitle:id('create-title'), createModel:id('create-model'),
  createEffort:id('create-effort'), createPermissions:id('create-permissions'),
  createTarget:id('create-target'), createTargetHelp:id('create-target-help'),
  objectList:id('object-list'), selectionSummary:id('selection-summary'),
  clearSelection:id('clear-selection'), selectedChip:id('selected-chip'),
  annotationList:id('annotation-list'), annotationCount:id('annotation-count'),
  undoAnnotation:id('undo-annotation'), redoAnnotation:id('redo-annotation'),
  clearAnnotations:id('clear-annotations'), clearDialog:id('clear-annotations-dialog'),
  confirmClear:id('confirm-clear-annotations'), cancelClear:id('cancel-clear-annotations'),
  referenceAllAnnotations:id('reference-all-annotations'),
  note:id('feedback-note'),
  mentionMenu:id('prompt-mentions'), mentionList:id('prompt-mention-list'), mentionStatus:id('prompt-mention-status'),
  dragReference:id('drag-reference-image'), dragScene:id('drag-scene-image'),
  imageRefs:id('prompt-image-refs'), imagePreview:id('prompt-image-preview-dialog'),
  imagePreviewTitle:id('prompt-image-preview-title'), imagePreviewImage:id('prompt-image-preview-image'),
  imagePreviewToggle:id('prompt-image-preview-toggle'),
  submit:id('submit-button'), caption:id('submit-caption'),
  pill:id('session-pill'), toast:id('toast'), sceneHint:id('scene-hint'),
  referenceHint:id('reference-hint'), groupSelect:id('group-select'),
  textEditor:id('text-editor'), annotationText:id('annotation-text'),
  frame:id('frame-button'), resetView:id('reset-button'),
  savedSnapshot:id('saved-snapshot-button'), captureScene:id('capture-scene-button'), snapshotStrip:id('scene-snapshot-strip'), browse:id('browse-button'), snapshotButton:id('snapshot-button'),
  snapshotMedia:id('scene-snapshot-media'), snapshotImage:id('scene-snapshot-image'),
  newSceneBadge:id('new-scene-badge'), stop:id('stop-button'), agentStatus:id('agent-status'),
  feedbackIntro:id('feedback-intro'),
  approvals:id('approval-list'), pendingQueue:id('chat-pending-queue'), queue:id('queue-list'), conversation:id('conversation')
};
const comparePreferences = {sessionId:null, enabled:true, opacity:45, lastPositive:45};
const state = {
  projectId:null, projectName:null, sceneDisplayName:null, projects:null, loadingProjects:false,
  projectLoadError:null, projectListSignature:null, projectModelChoice:null, projectEffortChoice:'',
  projectModelSignature:null, projectEffortSignature:null, creatingProject:false, navigatingProject:false,
  pendingProjectCreate:null, projectCreationResult:null, projectCreationError:null,
  sessionId:null, sessionStatus:'connecting', feedbackCount:0,
  browserCapability:null, agent:{status:'disconnected'}, deliveryMode:'app_server', feedbackTransport:null, eventDelivery:null,
  boundThreadId:null, desktopAvailable:false, projectCreationSupported:false, queue:[], approvals:[], targets:null, targetChoice:null,
  targetOptionsSignature:null, loadingTargets:false, switchingTarget:false, targetLoadError:null,
  models:null, defaultModel:null, modelChoice:null, effortChoice:'', loadingModels:false,
  canSetPermissions:false,
  modelOptionsSignature:null, effortOptionsSignature:null, modelLoadError:null, creatingTarget:false,
  eventCursor:0, seenEventIds:new Set(), submittingKey:null, workspaceReady:false,
  sceneRevision:null, sceneLoading:false, sceneObjects:[], objectNodes:new Map(),
  references:[], activeReferenceId:null, selectedId:null, selectedSceneNode:null,
  lastPickedDetailNode:null, selectionLevel:'item',
  referencedSceneNodes:[],
  imageRefs:[], draftImageSignature:null, restoredImageRefIds:[], imageReferencesSupported:false,
  poseRefs:[], humanJobs:[], humanDetails:new Map(), humanDetailLoads:new Set(), humanDetailErrors:new Map(),
  humanLoading:false, humanOverlayChoice:'latest',
  poseEdits:[], poseEditor:null, poseEditDrag:null, poseCorrectionsSupported:false,
  humanError:null, humanJobsSignature:null,
  alignedReferenceId:null, alignmentExact:false, restoredCameraForReference:false,
  restoredCameraSignature:null,
  annotations:[], annotationNameCounters:{}, selectedAnnotationId:null, mode:'select', groupId:'', drag:null, textPending:null,
  sceneSnapshots:[], snapshot:null, sceneView:'live', referenceZoom:1, referencePan:{x:0,y:0},
  referencePanning:null, spacePan:false,
  toastTimer:null, submitting:false, uploading:false, firstFrame:true,
  groundAxis:'auto', detectedUpAxis:'z', restoredUpAxis:null,
  restoredSceneRevision:null, restoredModelUrl:null
};
Object.assign(state, {referenceClip:null, clipEnabled:true, time:0, playing:false, playbackStart:null,
  activeViewId:null, pendingViewId:null, referenceClipSignature:null, viewOptionsSignature:null,
  animations:new Map(), animationChoices:{}, dynamicSnapshots:[], draftMomentSignature:null,
  seekGeneration:0, seeking:false, timelineTarget:null, scrubRequest:null, timelineSaveTimer:null});

const rawFrameImages = createFrameImageCache();
const frameImages = {
  prepare:(urls) => rawFrameImages.prepare(urls.map(resourceURL)),
  prefetch:(urls) => rawFrameImages.prefetch(urls.map(resourceURL)),
  retain:(urls) => rawFrameImages.retain(urls.map(resourceURL)),
  clear:() => rawFrameImages.clear()
};

let minimalLayout = null;
let promptMentions = null;
let mentionSceneCache = null;
let workspaceControls = null;
let immersiveWorkspace = null;
let backgroundTransition = null;
let annotationReferenceDrag = null;
const annotationDragGhost = document.createElement('div');
annotationDragGhost.className = 'annotation-drag-ghost hidden';
annotationDragGhost.setAttribute('aria-hidden', 'true');
document.body.append(annotationDragGhost);
const annotationHistory = createAnnotationHistory();
const promptImageStore = createPromptImageStore(momentDatabase);
const PROMPT_DRAG_MIME = 'application/x-scene-feedback-reference';
let promptDrag = null;
let imagePreviewReference = null;
const threeScene = new THREE.Scene();
threeScene.background = new THREE.Color('#eae9e3');
threeScene.fog = new THREE.Fog('#eae9e3', 14, 36);
const camera = new THREE.PerspectiveCamera(44, 1, 0.01, 2000);
camera.up.set(0, 0, 1);
camera.position.set(5.5, -8.5, 6.5);
const renderer = new THREE.WebGLRenderer({antialias:true, preserveDrawingBuffer:true});
const rendererSize = new THREE.Vector2();
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.65;
renderer.domElement.tabIndex = 0;
ui.viewport.appendChild(renderer.domElement);
const controls = new SceneNavigation(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.target.set(0, 0, 0.65);
controls.minDistance = 0.3;
controls.maxDistance = 250;
controls.screenSpacePanning = true;
controls.update();
const hemisphereLight = new THREE.HemisphereLight(0xdcefff, 0x7e8a93, 2.3);
threeScene.add(hemisphereLight);
const keyLight = new THREE.DirectionalLight(0xffedda, 3.5);
keyLight.position.set(5, -4, 10);
threeScene.add(keyLight);
const fillLight = new THREE.DirectionalLight(0x9bc6ea, 1.8);
fillLight.position.set(-5, 6, 5);
threeScene.add(fillLight);
const grid = new THREE.GridHelper(30, 30, 0xb8b8ad, 0xc9c9bf);
grid.rotateX(Math.PI / 2);
grid.position.z = -0.003;
grid.material.transparent = true;
grid.material.opacity = 0.18;
threeScene.add(grid);
const ground = new THREE.Mesh(
  new THREE.PlaneGeometry(200, 200),
  new THREE.MeshBasicMaterial({color:0xeae9e3, transparent:true, opacity:0.18, depthWrite:false, side:THREE.DoubleSide})
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
      (path.startsWith('/api/workspace/') || path.startsWith('/api/sessions/') || path === '/api/projects')) {
    request.headers = {...(request.headers || {}), 'X-Workspace-Capability':state.browserCapability};
  }
  if (options.body && typeof options.body !== 'string') {
    request.body = JSON.stringify(options.body);
    request.headers = {'Content-Type':'application/json', ...(request.headers || {})};
  }
  const response = await fetch(resourceURL(path), request);
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
function ensureAnnotationNames() {
  const named = assignAnnotationNames(state.annotations, state.annotationNameCounters);
  state.annotations = named.annotations;
  state.annotationNameCounters = named.counters;
  if (state.sessionId) {
    try { localStorage.setItem('astra-annotation-names:' + state.sessionId, JSON.stringify(named.counters)); }
    catch { /* Naming still works for this page when storage is unavailable. */ }
  }
}
function saveDraft() {
  clearTimeout(state.timelineSaveTimer); state.timelineSaveTimer = null;
  if (!state.sessionId || state.sceneRevision === null || state.playing) return;
  try {
    localStorage.setItem(storageKey(), JSON.stringify({
      annotations:state.annotations, annotationNameCounters:state.annotationNameCounters, annotationMode:state.mode, selectedId:state.selectedId,
      selectedSceneNode:state.selectedSceneNode,
      lastPickedDetailNode:state.lastPickedDetailNode,
      selectionLevel:state.selectionLevel,
      referencedSceneNodes:state.referencedSceneNodes,
      poseRefs:state.poseRefs,
      poseEdits:state.poseEdits, poseEditor:state.poseEditor,
      imageRefIds:state.imageRefs.map((item) => item.id),
      humanOverlayChoice:state.humanOverlayChoice,
      sceneRevision:state.sceneRevision,
      selectedModelUrl:sceneObject(state.selectedId)?.url || null,
      activeReferenceId:state.activeReferenceId, note:ui.note.value,
      groundAxis:state.groundAxis, worldUpAxis:controls.worldUp.y === 1 ? 'y' : 'z',
      groupId:state.groupId, camera:{position:array(camera.position), target:array(controls.target),
        up:array(camera.up), fov:camera.fov, alignedReferenceId:state.alignedReferenceId,
        alignmentExact:state.alignmentExact, freeRotation:controls.freeRotation,
        referenceCameraSignature:state.alignedReferenceId ? JSON.stringify(activeReference()?.camera || null) : null},
      snapshot:state.snapshot, sceneView:state.sceneView, sceneSnapshotIds:state.sceneSnapshots.map(entry => entry.id),
      dynamicTime:state.time, activeViewId:state.activeViewId, clipEnabled:state.clipEnabled, animationChoices:state.animationChoices,
      feedbackScope:ui.scope.value, rangeStart:ui.rangeStart.value, rangeEnd:ui.rangeEnd.value,
      referenceZoom:state.referenceZoom, referencePan:state.referencePan,
      eventCursor:state.eventCursor
    }));
  } catch { /* A full or disabled local store should not block feedback. */ }
  saveMomentDraft();
  saveImageReferenceDraft();
}
function restoreDraft() {
  try {
    const draft = JSON.parse(localStorage.getItem(storageKey()) || '{}');
    let counters = {};
    try { counters = JSON.parse(localStorage.getItem('astra-annotation-names:' + state.sessionId) || '{}'); }
    catch { /* A broken naming preference must not discard the visual draft. */ }
    state.annotationNameCounters = {...draft.annotationNameCounters};
    for (const [type, value] of Object.entries(counters || {})) {
      const saved = state.annotationNameCounters[type];
      const previous = Number.isSafeInteger(saved) && saved >= 0 && saved <= 1e9 ? saved : 0;
      if (Number.isSafeInteger(value) && value >= 0 && value <= 1e9 && value > previous) state.annotationNameCounters[type] = value;
    }
    if (Array.isArray(draft.annotations)) state.annotations = draft.annotations.filter((a) => a && labels[a.type] && ['reference','scene'].includes(a.pane) && a.coordinates);
    state.selectedId = typeof draft.selectedId === 'string' ? draft.selectedId : null;
    state.selectedSceneNode = draft.selectedSceneNode && Array.isArray(draft.selectedSceneNode.node_path)
      ? draft.selectedSceneNode : null;
    state.lastPickedDetailNode = draft.lastPickedDetailNode && Array.isArray(draft.lastPickedDetailNode.node_path)
      ? draft.lastPickedDetailNode : null;
    state.selectionLevel = ['item','part'].includes(draft.selectionLevel)
      ? draft.selectionLevel : state.selectedSceneNode ? 'part' : 'item';
    updateSelectionLevelControls();
    state.referencedSceneNodes = Array.isArray(draft.referencedSceneNodes)
      ? draft.referencedSceneNodes.filter((node) => node && typeof node.parent_object_id === 'string' && Array.isArray(node.node_path))
      : [];
    state.poseRefs = Array.isArray(draft.poseRefs) ? draft.poseRefs.filter((item) => poseToken(item?.job_id,item?.reference_id)).slice(0,64) : [];
    state.poseEdits = Array.isArray(draft.poseEdits) ? draft.poseEdits.filter(validPoseEdit).slice(0,8) : [];
    state.poseEditor = draft.poseEditor && /^[0-9a-f]{32}$/.test(draft.poseEditor.jobId || '') && /^[0-9a-f]{32}$/.test(draft.poseEditor.referenceId || '')
      ? {...draft.poseEditor,hand:draft.poseEditor.hand === 'right' ? 'right' : 'left',visibility:['visible','occluded','missing'].includes(draft.poseEditor.visibility) ? draft.poseEditor.visibility : 'visible'} : null;
    state.restoredImageRefIds = Array.isArray(draft.imageRefIds) ? draft.imageRefIds.filter((value) => imageToken(value)).slice(0,8) : [];
    state.humanOverlayChoice = ['latest','hidden'].includes(draft.humanOverlayChoice) || /^[0-9a-f]{32}$/.test(draft.humanOverlayChoice || '')
      ? draft.humanOverlayChoice : 'latest';
    state.restoredSceneRevision = Number.isInteger(draft.sceneRevision) ? draft.sceneRevision : null;
    state.restoredModelUrl = typeof draft.selectedModelUrl === 'string' ? draft.selectedModelUrl : null;
    state.activeReferenceId = typeof draft.activeReferenceId === 'string' ? draft.activeReferenceId : null;
    state.groupId = typeof draft.groupId === 'string' ? draft.groupId : '';
    if (draft.snapshot?.data_url?.startsWith('data:image/jpeg;base64,') && Number.isInteger(draft.snapshot.scene_revision)) {
      state.snapshot = draft.snapshot;
      state.sceneView = draft.sceneView === 'live' ? 'live' : 'snapshot';
      state.mode = ['select','point','rectangle','line','arrow','text','freehand','erase'].includes(draft.annotationMode) ? draft.annotationMode : 'rectangle';
    } else if (!draft.sceneSnapshotIds?.length) {
      // Old drafts had scene marks without a fixed image; they cannot be mapped reliably.
      state.annotations = state.annotations.filter((annotation) => annotation.pane !== 'scene');
    }
    state.time = Number.isFinite(draft.dynamicTime) ? Math.max(0, draft.dynamicTime) : 0;
    state.activeViewId = typeof draft.activeViewId === 'string' ? draft.activeViewId : null;
    state.clipEnabled = draft.clipEnabled !== false;
    state.animationChoices = draft.animationChoices && typeof draft.animationChoices === 'object' ? draft.animationChoices : {};
    if (['frame','range','clip'].includes(draft.feedbackScope)) ui.scope.value = draft.feedbackScope;
    if (Number.isFinite(Number(draft.rangeStart))) ui.rangeStart.value = draft.rangeStart;
    if (Number.isFinite(Number(draft.rangeEnd))) ui.rangeEnd.value = draft.rangeEnd;
    state.referenceZoom = Number.isFinite(draft.referenceZoom) ? clamp(draft.referenceZoom, 1, 8) : 1;
    state.referencePan = Number.isFinite(draft.referencePan?.x) && Number.isFinite(draft.referencePan?.y)
      ? draft.referencePan : {x:0,y:0};
    ui.groupSelect.value = state.groupId;
    // Preserve drafts from the former two-field composer as one freeform prompt.
    const oldPrompts = typeof draft.objectPromptsText === 'string' ? draft.objectPromptsText.trim() : '';
    const currentNote = typeof draft.note === 'string' ? draft.note : '';
    ui.note.value = [oldPrompts, currentNote].filter(Boolean).join('\n\n');
    state.groundAxis = ['y','z'].includes(draft.groundAxis) ? draft.groundAxis : 'auto';
    state.restoredUpAxis = ['y','z'].includes(draft.worldUpAxis) ? draft.worldUpAxis : null;
    applyGroundAxis(state.groundAxis === 'auto' ? state.restoredUpAxis || 'z' : state.groundAxis);
    if (draft.camera?.position?.length === 3 && draft.camera?.target?.length === 3) {
      controls.setFree(draft.camera.freeRotation === true, {notify:false});
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
  ensureAnnotationNames();
}

function editable() { return state.workspaceReady && Number.isInteger(state.sceneRevision) && state.sessionStatus === 'open' && !state.sceneLoading && !state.submitting && !state.uploading && !state.pendingSubmission && !state.creatingProject && !state.navigatingProject; }
function updateAnnotationHistory() {
  ui.undoAnnotation.disabled = !editable() || !annotationHistory.canUndo;
  ui.redoAnnotation.disabled = !editable() || !annotationHistory.canRedo;
  ui.clearAnnotations.disabled = !editable() || (!state.annotations.length && !state.dynamicSnapshots.length && !state.sceneSnapshots.length && !state.snapshot);
  ui.confirmClear.disabled = ui.clearAnnotations.disabled;
  ui.referenceAllAnnotations.disabled = !editable() || !state.annotations.length;
}
function annotationEditState() {
  return {annotations:[...state.annotations], sceneSnapshots:[...state.sceneSnapshots], dynamicSnapshots:[...state.dynamicSnapshots],
    snapshot:state.snapshot, sceneView:state.sceneView};
}
function recordAnnotationEdit(before) {
  annotationHistory.record(before, annotationEditState());
  updateAnnotationHistory();
}
function applyAnnotationEdit(from, to) {
  // Preserve moments saved by navigation since this edit. Only replay its own changes.
  const removed = new Set(from.dynamicSnapshots.filter((moment) =>
    !to.dynamicSnapshots.some((entry) => entry.id === moment.id)).map((moment) => moment.id));
  const restored = to.dynamicSnapshots.filter((moment) =>
    !from.dynamicSnapshots.some((entry) => entry.id === moment.id));
  const moments = state.dynamicSnapshots.filter((moment) => !removed.has(moment.id));
  for (const moment of restored) if (!moments.some((entry) => entry.id === moment.id)) moments.push(moment);
  if (moments.length > 8) {
    announce('恢复标记需要原截图，请先移除多余时刻（最多保留 8 个）。', true);
    return false;
  }
  pauseTimeline(); hideTextEditor(); state.drag = null;
  state.annotations = [...to.annotations];
  state.dynamicSnapshots = moments;
  state.sceneSnapshots = [...to.sceneSnapshots];
  if (from.snapshot !== to.snapshot) {
    state.snapshot = to.snapshot;
    state.sceneView = to.sceneView;
    if (state.snapshot?.time_sec !== undefined) openMoment(state.snapshot.id);
  } else if (state.snapshot?.time_sec !== undefined && !moments.some((moment) => moment.id === state.snapshot.id)) {
    state.snapshot = null; state.sceneView = 'live';
  }
  renderSceneView(); renderAnnotations(); renderTimeline(); drawOverlays(); saveDraft();
  return true;
}
function undoAnnotationEdit() {
  if (!editable()) return;
  if (annotationHistory.undo(applyAnnotationEdit)) announce('已撤销标注操作。');
  updateAnnotationHistory();
}
function redoAnnotationEdit() {
  if (!editable()) return;
  if (annotationHistory.redo(applyAnnotationEdit)) announce('已重做标注操作。');
  updateAnnotationHistory();
}
function removeAnnotation(annotationId) {
  if (!editable() || !state.annotations.some((mark) => mark.id === annotationId)) return;
  const before = annotationEditState();
  state.annotations = state.annotations.filter((mark) => mark.id !== annotationId);
  recordAnnotationEdit(before);
  renderAnnotations(); drawOverlays(); saveDraft();
}
function setSession(session) {
  if (state.sessionId !== session.session_id) {
    annotationHistory.clear(); promptDrag = null;
    state.annotationNameCounters = {}; state.selectedAnnotationId = null;
    promptMentions?.close();
    state.imageRefs = []; state.draftImageSignature = null; state.restoredImageRefIds = [];
  }
  state.sessionId = session.session_id;
  restoreComparePreferences();
  state.sessionStatus = session.status || 'open';
  state.feedbackCount = Number(session.feedback_count) || 0;
  id('feedback-count-label').textContent = '已提交 ' + state.feedbackCount + ' 条';
  ui.pill.textContent = ({open:'工作台已连接', submitted:'已结束', cancelled:'已取消', error:'连接失败', connecting:'连接中'})[state.sessionStatus] || state.sessionStatus;
  ui.pill.className = 'session-pill ' + state.sessionStatus;
  ui.submit.disabled = !editable();
  ui.note.disabled = state.sessionStatus !== 'open' || state.submitting || !!state.pendingSubmission;
  ui.referenceInput.disabled = state.sessionStatus !== 'open';
  ui.clipInput.disabled = !editable();
  if (state.sessionStatus !== 'open') {
    ui.caption.textContent = '会话已结束，标记仍可查看。';
  } else {
    updateSubmitLabel();
  }
  renderTimeline();
  updateAnnotationHistory();
  minimalLayout?.refresh();
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
  await restoreMomentDraft();
  await restoreImageReferenceDraft();
  setReferenceClip(session.reference_clip || null, {restore:true});
  setReferences(session.reference_images || []);
  renderSceneView();
  state.pendingSubmission = await readOutbox();
  renderTimeline(); updateMode();
  renderWorkspace(workspace);
  await fetchEvents(true);
  if (state.feedbackTransport !== 'mcp_events' && state.deliveryMode === 'external' && state.desktopAvailable) loadTargets().catch((error) => {
    ui.targetHelp.textContent = '无法读取任务列表：' + error.message;
  });
  if (state.feedbackTransport !== 'mcp_events' && state.deliveryMode === 'external' && state.desktopAvailable) loadModels().catch(() => {
    /* The new-task form shows the connection error. */
  });
  loadProjects().catch(() => { /* The scene picker shows catalog errors. */ });
  loadHumanPoses().catch(() => { /* The result panel shows connection errors. */ });
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

function setSubmitLabel(label) {
  const text = ui.submit.querySelector('[data-submit-label]');
  if (text) text.textContent = label;
  ui.submit.setAttribute('aria-label', label);
  ui.submit.setAttribute('aria-busy', String(state.submitting));
  ui.submit.title = label;
}

function updateSubmitLabel() {
  updateAnnotationHistory();
  const status = state.agent?.status || 'disconnected';
  if (state.feedbackTransport === 'mcp_events') {
    setSubmitLabel(state.submitting ? '正在发送…' : state.pendingSubmission ? '重试发送' : '发送反馈');
    ui.submit.disabled = !state.workspaceReady || !Number.isInteger(state.sceneRevision) || state.sessionStatus !== 'open' || state.sceneLoading || state.submitting || state.uploading || state.creatingProject || state.navigatingProject;
    ui.note.disabled = state.sessionStatus !== 'open' || state.submitting || !!state.pendingSubmission;
    ui.referenceInput.disabled = state.sessionStatus !== 'open' || !!state.pendingSubmission || state.creatingProject || state.navigatingProject;
    ui.caption.textContent = ['submitted', 'cancelled'].includes(state.sessionStatus)
      ? '会话已结束，标记仍可查看。'
      : state.pendingSubmission ? '保存结果尚未确认；使用同一消息编号重试。'
      : (state.eventDelivery?.subscriber_count || 0) > 0
        ? '发送后由订阅插件接收事件；事件送达不代表模型已执行。'
      : '反馈会先保存在工作台；插件订阅后可接收后续事件。';
    return;
  }
  if (state.deliveryMode === 'external') {
    const bound = !!state.boundThreadId;
    const label = state.submitting ? '正在发送…' : state.pendingSubmission ? '重试发送'
      : bound && ['running', 'awaiting_approval', 'waiting'].includes(status) ? '加入下一轮'
      : bound ? '发送反馈' : '保存反馈';
    setSubmitLabel(label);
    ui.submit.disabled = !state.workspaceReady || !Number.isInteger(state.sceneRevision) || state.sessionStatus !== 'open' || state.sceneLoading || state.submitting || state.uploading || state.creatingProject || state.navigatingProject;
    ui.note.disabled = state.sessionStatus !== 'open' || state.submitting || !!state.pendingSubmission;
    ui.referenceInput.disabled = state.sessionStatus !== 'open' || !!state.pendingSubmission || state.creatingProject || state.navigatingProject;
    ui.caption.textContent = ['submitted', 'cancelled'].includes(state.sessionStatus)
      ? '会话已结束，标记仍可查看。'
      : state.pendingSubmission
        ? '送达未确认，重试不会重复创建反馈。'
      : bound && ['running', 'awaiting_approval', 'waiting'].includes(status)
        ? '发送后排队，目标任务空闲时自动处理。'
      : bound && status === 'delivery_uncertain'
        ? '请先在目标任务核对送达情况；新反馈会先保存。'
      : bound && ['disconnected', 'error'].includes(status)
        ? '暂未连接；反馈先保存，恢复后自动发送。'
      : bound ? '' : '反馈先保存，等待 MCP 工具读取。';
    return;
  }
  const label = state.submitting ? '正在发送…' : state.pendingSubmission ? '重试发送'
    : ['running', 'awaiting_approval', 'waiting'].includes(status) ? '加入下一轮'
    : status === 'disconnected' || status === 'error' ? '保存反馈'
    : '发送反馈';
  setSubmitLabel(label);
  ui.submit.disabled = !state.workspaceReady || !Number.isInteger(state.sceneRevision) || state.sessionStatus !== 'open' || state.sceneLoading || state.submitting || state.uploading || state.creatingProject || state.navigatingProject;
  ui.note.disabled = state.sessionStatus !== 'open' || state.submitting || !!state.pendingSubmission;
  ui.referenceInput.disabled = state.sessionStatus !== 'open' || !!state.pendingSubmission || state.creatingProject || state.navigatingProject;
  if (['submitted', 'cancelled'].includes(state.sessionStatus)) ui.caption.textContent = '会话已结束，标记仍可查看。';
  else if (state.pendingSubmission) ui.caption.textContent = '送达未确认，重试不会重复创建反馈。';
  else if (['running', 'awaiting_approval', 'waiting'].includes(status)) ui.caption.textContent = '发送后排队，Codex 空闲时自动处理。';
  else if (status === 'delivery_uncertain') ui.caption.textContent = '送达状态待核实，请先查看执行记录。';
  else if (status === 'disconnected' || status === 'error') ui.caption.textContent = '暂未连接；反馈先保存，恢复后自动发送。';
  else ui.caption.textContent = '';
}
function shortTaskId(threadId) {
  return typeof threadId === 'string' && threadId.length > 12
    ? threadId.slice(0, 8) + '…' + threadId.slice(-6) : (threadId || '未绑定');
}
function targetStatusLabel(status) {
  return ({idle:'空闲',inProgress:'执行中',in_progress:'执行中',running:'执行中',active:'执行中',completed:'已完成',
    archived:'已归档',notLoaded:'未打开',recoverable:'可恢复',unknown:'状态未知'})[status] || (status || '');
}
function targetIsBusy(item) {
  return ['inProgress', 'in_progress', 'running', 'active', 'awaiting_approval'].includes(item?.status);
}
function targetModelLabel(item) {
  return [item?.model, item?.reasoning_effort].filter(Boolean).join(' · ');
}
function targetName(threadId) {
  const target = state.targets?.find((item) => item.thread_id === threadId);
  return target?.title || shortTaskId(threadId);
}
function updateProjectTitle() {
  const catalogName = state.projects?.find((item) => item.project_id === state.projectId)?.name;
  const name = catalogName || state.projectName || state.sceneDisplayName || '当前场景';
  ui.sceneName.textContent = name;
  ui.projectsButton.title = name + ' · 切换或新建场景';
  ui.projectsButton.setAttribute('aria-label', '场景：' + name + '，切换或新建场景');
}
function projectBusyReason({allowCreation=false}={}) {
  if (state.submitting) return '正在保存反馈，请稍后切换场景。';
  if (state.uploading) return '正在导入文件，请稍后切换场景。';
  if (state.creatingTarget || state.switchingTarget) return '正在连接 Codex 任务，请稍后切换场景。';
  if (state.creatingProject && !allowCreation) return '正在创建场景，请等待创建结果。';
  if (state.navigatingProject) return '正在切换场景…';
  if (state.sceneLoading) return '正在加载场景，请稍后切换。';
  return null;
}
const projectRequestKey = 'visual-project-create-request';
function persistProjectRequest(request) {
  try { localStorage.setItem(projectRequestKey, JSON.stringify(request)); }
  catch { throw new Error('浏览器无法保存创建请求，请允许本地存储后再试。'); }
  state.pendingProjectCreate = request;
}
function clearProjectRequest() {
  try { localStorage.removeItem(projectRequestKey); } catch { /* A completed request remains safe to recover. */ }
  state.pendingProjectCreate = null;
  state.projectCreationResult = null;
}
function restoreProjectRequest() {
  try {
    const request = JSON.parse(localStorage.getItem(projectRequestKey) || 'null');
    if (!request || !/^[0-9a-f]{32}$/i.test(request.request_id || '') ||
        request.body?.request_id !== request.request_id || typeof request.body.name !== 'string' ||
        typeof request.body.model !== 'string' || !['workspace_write','full_access','read_only'].includes(request.body.permission_mode)) return;
    state.pendingProjectCreate = request;
    state.projectModelChoice = request.body.model;
    state.projectEffortChoice = request.body.reasoning_effort || '';
    ui.projectName.value = request.body.name;
    ui.projectPermissions.value = request.body.permission_mode;
    if (projectNavigationURL(request.project, location.origin)) state.projectCreationResult = request.project;
    state.projectCreationError = request.error || null;
    ui.createProjectPanel.open = true;
  } catch { /* A malformed saved request cannot be submitted. */ }
}
function rememberCreatedProject(project, error=null) {
  if (!projectNavigationURL(project, location.origin)) return false;
  state.projectCreationResult = project;
  state.projectCreationError = error;
  const request = state.pendingProjectCreate;
  if (request) {
    try { persistProjectRequest({...request, project, status:'created', error}); }
    catch { /* The original request ID is already persisted before submission. */ }
  }
  return true;
}
function renderProjectPicker() {
  updateProjectTitle();
  ui.createProjectPanel.classList.toggle('hidden', state.feedbackTransport === 'mcp_events');
  if (!ui.projectsDialog.open) return;
  const projects = Array.isArray(state.projects) ? state.projects : [];
  const busy = projectBusyReason();
  const signature = JSON.stringify([projects, state.projectId, busy, state.loadingProjects, state.feedbackTransport]);
  if (signature !== state.projectListSignature) {
    state.projectListSignature = signature;
    ui.projectsList.replaceChildren();
    for (const project of projects) {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'project-item';
      const heading = document.createElement('div'); heading.className = 'project-name';
      const name = document.createElement('strong'); name.textContent = project.name || '未命名场景';
      const current = project.project_id === state.projectId;
      const unavailable = project.creation_status === 'unavailable';
      const label = document.createElement('span'); label.textContent = unavailable ? '不可用' : current ? '当前' : '打开 ↗';
      heading.append(name, label);
      const detail = document.createElement('div'); detail.className = 'project-detail';
      detail.textContent = (Number.isInteger(project.scene_revision) ? '版本 ' + project.scene_revision : '空白场景') +
        (state.feedbackTransport === 'mcp_events' ? '' : project.thread_id ? ' · 已连接 Codex 任务' : ' · 尚未连接任务');
      button.append(heading, detail);
      if (project.creation_error) {
        const error = document.createElement('div'); error.className = 'project-detail project-error';
        error.textContent = (unavailable ? '场景不可用：' : '任务创建未完成：') + String(project.creation_error).slice(0, 200);
        button.append(error);
      } else if (['creating','pending','in_progress'].includes(project.creation_status)) {
        detail.textContent += ' · 正在创建任务';
      }
      button.disabled = unavailable || current || !!busy || !projectNavigationURL(project, location.origin);
      button.addEventListener('click', () => navigateProject(project));
      ui.projectsList.append(button);
    }
    if (!projects.length) {
      const empty = document.createElement('p'); empty.className = 'muted';
      empty.textContent = state.loadingProjects ? '正在读取场景…' : '还没有可切换的场景。';
      ui.projectsList.append(empty);
    }
  }
  ui.refreshProjects.disabled = state.loadingProjects || state.creatingProject || state.navigatingProject;
  ui.projectsStatus.textContent = busy || (state.projectLoadError ? '场景列表读取失败：' + state.projectLoadError : '');
  renderCreateProject();
}
function renderCreateProject() {
  const pending = state.pendingProjectCreate;
  const models = Array.isArray(state.models) ? state.models.filter((item) => typeof item?.model === 'string') : [];
  if (pending) {
    state.projectModelChoice = pending.body.model;
    state.projectEffortChoice = pending.body.reasoning_effort || '';
    ui.projectName.value = pending.body.name;
    ui.projectPermissions.value = pending.body.permission_mode;
  } else if (!models.some((item) => item.model === state.projectModelChoice)) {
    state.projectModelChoice = models.find((item) => item.model === state.defaultModel)?.model ||
      models.find((item) => item.is_default)?.model || models[0]?.model || null;
    state.projectEffortChoice = '';
  }
  const choices = [...models];
  if (pending && !choices.some((item) => item.model === pending.body.model)) choices.push({model:pending.body.model});
  const modelSignature = JSON.stringify([choices.map((item) => [item.model,item.display_name]), state.loadingModels]);
  if (modelSignature !== state.projectModelSignature) {
    state.projectModelSignature = modelSignature;
    ui.projectModel.replaceChildren();
    if (!choices.length) {
      const option = document.createElement('option'); option.value = '';
      option.textContent = state.loadingModels ? '正在读取可用模型…' : '暂无可用模型'; ui.projectModel.append(option);
    }
    for (const item of choices) {
      const option = document.createElement('option'); option.value = item.model;
      option.textContent = item.display_name || item.model; ui.projectModel.append(option);
    }
  }
  ui.projectModel.value = state.projectModelChoice || '';
  const model = choices.find((item) => item.model === state.projectModelChoice);
  const efforts = Array.isArray(model?.supported_reasoning_efforts) ? [...model.supported_reasoning_efforts] : [];
  if (pending?.body.reasoning_effort && !efforts.includes(pending.body.reasoning_effort)) efforts.push(pending.body.reasoning_effort);
  if (!pending && state.projectEffortChoice && !efforts.includes(state.projectEffortChoice)) state.projectEffortChoice = '';
  const effortSignature = JSON.stringify([model?.model, model?.default_reasoning_effort, efforts]);
  if (effortSignature !== state.projectEffortSignature) {
    state.projectEffortSignature = effortSignature; ui.projectEffort.replaceChildren();
    const defaultOption = document.createElement('option'); defaultOption.value = '';
    defaultOption.textContent = model?.default_reasoning_effort ? '模型默认（' + model.default_reasoning_effort + '）' : '模型默认';
    ui.projectEffort.append(defaultOption);
    for (const effort of efforts) {
      const option = document.createElement('option'); option.value = effort; option.textContent = effort; ui.projectEffort.append(option);
    }
  }
  ui.projectEffort.value = state.projectEffortChoice;
  const busy = projectBusyReason();
  const locked = !!pending || !!busy;
  ui.projectName.disabled = locked;
  ui.projectModel.disabled = locked || !models.length;
  ui.projectEffort.disabled = locked || !efforts.length;
  ui.projectPermissions.disabled = locked || !state.canSetPermissions;
  ui.createProject.classList.toggle('hidden', !!state.projectCreationResult);
  ui.createProject.disabled = !state.workspaceReady || !state.projectCreationSupported || !!busy ||
    (!pending && (!ui.projectName.value.trim() || !state.projectModelChoice || !state.canSetPermissions));
  ui.createProject.textContent = state.creatingProject ? '正在创建…' : pending ? '重试同一次创建' : '新建场景 + Codex 任务';
  ui.openCreatedProject.classList.toggle('hidden', !state.projectCreationResult);
  ui.openCreatedProject.disabled = !!busy || state.projectCreationResult?.creation_status === 'unavailable';
  if (state.creatingProject) ui.createProjectHelp.textContent = '正在创建新场景与独立 Codex 任务…';
  else if (state.projectCreationResult?.creation_status === 'unavailable') ui.createProjectHelp.textContent =
    '场景文件不可用：' + (state.projectCreationResult.creation_error || state.projectCreationError || '请恢复场景目录后刷新列表。');
  else if (state.projectCreationResult) ui.createProjectHelp.textContent = state.projectCreationError
    ? '场景已建立，任务未完成：' + state.projectCreationError + (state.deliveryMode === 'external' ? '。打开该场景后可在「任务」中继续连接。' : '。请检查后端连接；该次请求不会重复创建会话。')
    : '场景已建立，可以打开。';
  else if (pending) ui.createProjectHelp.textContent = '上次创建的结果尚未确认。可刷新场景列表找回，或使用同一请求重试；不会重复创建任务。';
  else if (state.projectCreationError) ui.createProjectHelp.textContent = state.projectCreationError;
  else if (state.modelLoadError) ui.createProjectHelp.textContent = '无法读取可用模型：' + state.modelLoadError;
  else if (!state.projectCreationSupported) ui.createProjectHelp.textContent = '当前服务未启用场景创建，请启用 Codex CLI 或连接 Codex Desktop。';
  else if (!state.canSetPermissions) ui.createProjectHelp.textContent = '正在读取创建任务所需的模型和权限选项。';
  else ui.createProjectHelp.textContent = '创建空白场景和独立的 Codex 任务，然后进入新场景。';
}
async function loadProjects() {
  if (state.loadingProjects) return;
  state.loadingProjects = true; renderProjectPicker();
  try {
    const result = await api('/api/projects');
    state.projects = Array.isArray(result.projects) ? result.projects : [];
    state.projectId = result.current_project_id || state.projectId;
    state.projectLoadError = null;
    const pending = state.pendingProjectCreate;
    const recoveryId = pending?.project?.project_id || pending?.project_id;
    const recovered = pending && state.projects.find((project) => project.request_id === pending.request_id ||
      (recoveryId && project.project_id === recoveryId));
    if (recovered) rememberCreatedProject(recovered, recovered.creation_error || null);
  } catch (error) { state.projectLoadError = error.message; throw error; }
  finally { state.loadingProjects = false; renderProjectPicker(); }
}
async function navigateProject(project, {allowCreation=false}={}) {
  if (project?.creation_status === 'unavailable') {
    announce('场景文件不可用，请恢复场景目录后刷新列表。', true); return false;
  }
  const busy = projectBusyReason({allowCreation});
  if (busy) { announce(busy, true); renderProjectPicker(); return false; }
  const url = projectNavigationURL(project, location.origin);
  if (!url) { announce('场景地址无效，请刷新场景列表。', true); return false; }
  state.navigatingProject = true; renderProjectPicker(); updateSubmitLabel(); updateMode();
  pauseTimeline(); hideTextEditor(); state.drag = null;
  saveDraft();
  await saveMomentDraft.pending;
  await promptImageStore.pending.catch(() => {});
  try {
    location.assign(url);
    if (state.projectCreationResult?.project_id === project.project_id) clearProjectRequest();
    return true;
  } catch (error) {
    state.navigatingProject = false; renderProjectPicker(); updateSubmitLabel(); updateMode();
    announce('无法打开场景：' + error.message, true); return false;
  }
}
async function createSceneProject() {
  if (state.feedbackTransport === 'mcp_events' || !state.workspaceReady || !state.projectCreationSupported || projectBusyReason() || state.projectCreationResult) return;
  let request = state.pendingProjectCreate;
  if (!request) {
    const name = ui.projectName.value.trim();
    if (!name || !state.projectModelChoice || !state.canSetPermissions) return;
    const requestId = newId().replace(/-/g, '');
    const body = {name, model:state.projectModelChoice, permission_mode:ui.projectPermissions.value, request_id:requestId};
    if (state.projectEffortChoice) body.reasoning_effort = state.projectEffortChoice;
    request = {request_id:requestId, body, status:'pending'};
    try { persistProjectRequest(request); }
    catch (error) { state.projectCreationError = error.message; renderCreateProject(); return; }
  }
  state.creatingProject = true; state.projectCreationError = null; renderProjectPicker(); updateSubmitLabel(); updateMode(); renderTimeline();
  try {
    const result = await api('/api/projects', {method:'POST', body:request.body});
    if (!rememberCreatedProject(result.project)) throw new Error('服务未返回可打开的新场景地址');
    await navigateProject(result.project, {allowCreation:true});
  } catch (error) {
    const detail = error.detail?.detail || error.detail || {};
    const created = detail.project || error.detail?.project;
    if (created && rememberCreatedProject(created, created.creation_error || error.message)) {
      await loadProjects().catch(() => {});
    } else {
      state.projectCreationError = '创建结果未确认：' + error.message;
      const recoveryId = created?.project_id || detail.project_id;
      try { persistProjectRequest({...request, ...(recoveryId ? {project_id:recoveryId} : {}), status:'unknown', error:state.projectCreationError}); } catch { /* Keep the saved request ID. */ }
      await loadProjects().catch(() => {});
      if (!state.projectCreationResult && !recoveryId && [400, 422].includes(error.status)) {
        clearProjectRequest();
        state.projectCreationError = '创建请求被拒绝：' + error.message + '。请调整选项后重新提交。';
      }
    }
  } finally { state.creatingProject = false; renderProjectPicker(); updateSubmitLabel(); updateMode(); renderTimeline(); }
}
function openProjectsDialog() {
  for (const dialog of document.querySelectorAll('dialog[open]')) if (dialog !== ui.projectsDialog) dialog.close();
  if (!ui.projectsDialog.open) ui.projectsDialog.showModal();
  ui.projectsButton.setAttribute('aria-expanded', 'true'); renderProjectPicker();
  loadProjects().catch(() => {});
  if (state.feedbackTransport !== 'mcp_events') loadModels({forProjects:true}).catch(() => {});
}
function renderCreateTarget() {
  const models = Array.isArray(state.models) ? state.models.filter((item) => typeof item?.model === 'string') : [];
  if (!models.some((item) => item.model === state.modelChoice)) {
    state.modelChoice = models.find((item) => item.model === state.defaultModel)?.model ||
      models.find((item) => item.is_default)?.model || models[0]?.model || null;
    state.effortChoice = '';
  }
  const modelSignature = JSON.stringify({models:models.map((item) => [item.model, item.display_name]),
    loading:state.loadingModels});
  if (modelSignature !== state.modelOptionsSignature) {
    state.modelOptionsSignature = modelSignature;
    ui.createModel.replaceChildren();
    if (!models.length) {
      const option = document.createElement('option');
      option.value = '';
      option.textContent = state.loadingModels ? '正在读取可用模型…' : '暂无可用模型';
      ui.createModel.append(option);
    }
    for (const item of models) {
      const option = document.createElement('option');
      option.value = item.model;
      option.textContent = item.display_name || item.model;
      ui.createModel.append(option);
    }
  }
  ui.createModel.value = state.modelChoice || '';
  const model = models.find((item) => item.model === state.modelChoice);
  const efforts = Array.isArray(model?.supported_reasoning_efforts) ? model.supported_reasoning_efforts : [];
  if (state.effortChoice && !efforts.includes(state.effortChoice)) state.effortChoice = '';
  const effortSignature = JSON.stringify([model?.model, model?.default_reasoning_effort, efforts]);
  if (effortSignature !== state.effortOptionsSignature) {
    state.effortOptionsSignature = effortSignature;
    ui.createEffort.replaceChildren();
    const defaultOption = document.createElement('option');
    defaultOption.value = '';
    defaultOption.textContent = model?.default_reasoning_effort
      ? '模型默认（' + model.default_reasoning_effort + '）' : '模型默认';
    ui.createEffort.append(defaultOption);
    for (const effort of efforts) {
      const option = document.createElement('option');
      option.value = effort;
      option.textContent = effort;
      ui.createEffort.append(option);
    }
  }
  ui.createEffort.value = state.effortChoice;
  const busy = state.creatingTarget || state.switchingTarget || state.submitting || !!state.pendingSubmission || state.creatingProject || state.navigatingProject;
  ui.createModel.disabled = !models.length || busy;
  ui.createEffort.disabled = !models.length || !efforts.length || busy;
  ui.createPermissions.disabled = busy || !state.canSetPermissions;
  ui.createTitle.disabled = busy;
  ui.createTarget.disabled = !models.length || busy || !state.canSetPermissions;
  if (state.creatingTarget) ui.createTargetHelp.textContent = '正在创建任务并连接工作台…';
  else if (state.pendingSubmission) ui.createTargetHelp.textContent = '请先确认上一条反馈的送达状态。';
  else if (state.modelLoadError) ui.createTargetHelp.textContent = '无法读取可用模型：' + state.modelLoadError;
  else if (!state.canSetPermissions) ui.createTargetHelp.textContent = '服务正在更新任务权限功能；请等当前回合结束后刷新页面。';
  else ui.createTargetHelp.textContent = '权限模式只应用于新任务；当前场景和参考图会留在工作台。';
}
function renderTargetPicker() {
  minimalLayout?.refresh();
  ui.targetPicker.classList.toggle('hidden', state.feedbackTransport === 'mcp_events' || state.deliveryMode !== 'external' || !state.desktopAvailable);
  if (state.feedbackTransport === 'mcp_events' || state.deliveryMode !== 'external' || !state.desktopAvailable) return;
  const bound = state.boundThreadId;
  const selected = state.targets?.find((item) => item.thread_id === bound);
  ui.currentTarget.textContent = bound
    ? '当前：' + targetName(bound) + (targetModelLabel(selected) ? ' · ' + targetModelLabel(selected) : '') +
      (selected?.status ? ' · ' + targetStatusLabel(selected.status) : '')
    : '当前场景尚未连接 Codex 任务；可选择下方任务或新建。';
  ui.currentTarget.title = bound || '';
  const targets = Array.isArray(state.targets) ? state.targets.filter((item) => typeof item?.thread_id === 'string') : [];
  if (bound && !targets.some((item) => item.thread_id === bound)) {
    targets.unshift({thread_id:bound, title:'当前任务（未列出）'});
  }
  const signature = JSON.stringify({bound, targets:targets.map((item) => [item.thread_id, item.title, item.model, item.reasoning_effort, item.status]),
    loading:state.loadingTargets && !state.targets});
  if (signature !== state.targetOptionsSignature) {
    state.targetOptionsSignature = signature;
    ui.targetSelect.replaceChildren();
    for (const item of targets) {
      const option = document.createElement('option');
      option.value = item.thread_id;
      const displayTitle = item.title?.startsWith('未命名任务 · ') ? '未命名任务' : (item.title || '未命名任务');
      option.textContent = displayTitle + ' · ' + shortTaskId(item.thread_id) +
        (targetModelLabel(item) ? ' · ' + targetModelLabel(item) : '') +
        (item.status ? ' · ' + targetStatusLabel(item.status) : '') +
        (item.thread_id === bound ? '（当前）' : '');
      option.title = (item.title || '未命名任务') + ' · ' + item.thread_id;
      ui.targetSelect.append(option);
    }
    if (!targets.length) {
      const option = document.createElement('option');
      option.textContent = state.loadingTargets ? '正在读取任务…' : '没有可切换的任务';
      option.value = '';
      ui.targetSelect.append(option);
    }
  }
  if (!targets.some((item) => item.thread_id === state.targetChoice)) state.targetChoice = bound || targets[0]?.thread_id || null;
  ui.targetSelect.value = state.targetChoice || '';
  ui.targetSelect.disabled = !targets.length || state.switchingTarget || state.creatingTarget;
  const chosen = targets.find((item) => item.thread_id === state.targetChoice);
  ui.targetChoiceDetails.textContent = chosen
    ? '已选：' + (chosen.title || '未命名任务') +
      (targetModelLabel(chosen) ? ' · ' + targetModelLabel(chosen) : '') + ' · ' + chosen.thread_id
    : '尚未选择任务';
  ui.switchTarget.disabled = !state.targetChoice || state.targetChoice === bound || state.switchingTarget || state.creatingTarget ||
    state.submitting || !!state.pendingSubmission || targetIsBusy(chosen);
  const manualId = ui.manualTargetId.value.trim();
  const manualItem = targets.find((item) => item.thread_id === manualId);
  ui.manualSwitchTarget.disabled = !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(manualId) || manualId === bound ||
    state.switchingTarget || state.creatingTarget || state.submitting || !!state.pendingSubmission || targetIsBusy(manualItem);
  ui.manualTargetId.disabled = state.switchingTarget || state.creatingTarget;
  ui.refreshTargets.disabled = state.loadingTargets || state.loadingModels || state.switchingTarget || state.creatingTarget;
  if (state.pendingSubmission) ui.targetHelp.textContent = '请先完成上次未确认的提交，再切换目标任务。';
  else if (targetIsBusy(chosen)) ui.targetHelp.textContent = '所选任务仍在执行；请等它空闲后切换。';
  else if (state.targetLoadError) ui.targetHelp.textContent = '无法读取任务列表：' + state.targetLoadError;
  else ui.targetHelp.textContent = '选择已打开的任务，或在下方新建。新反馈发往所选任务；未送达的旧反馈会等切回原任务。';
  renderCreateTarget();
}
async function loadTargets() {
  if (state.feedbackTransport === 'mcp_events' || state.deliveryMode !== 'external' || !state.desktopAvailable || state.loadingTargets) return;
  state.loadingTargets = true;
  renderTargetPicker();
  try {
    const result = await api('/api/workspace/targets');
    state.targets = Array.isArray(result.targets) ? result.targets : [];
    state.targetLoadError = null;
  } catch (error) {
    state.targetLoadError = error.message;
    throw error;
  } finally {
    state.loadingTargets = false;
    renderTargetPicker();
  }
}
async function loadModels({forProjects=false}={}) {
  if (state.feedbackTransport === 'mcp_events' || (forProjects ? !state.projectCreationSupported : !state.desktopAvailable || state.deliveryMode !== 'external') || state.loadingModels) return;
  state.loadingModels = true;
  renderTargetPicker();
  renderCreateProject();
  try {
    const result = await api('/api/workspace/models');
    state.models = Array.isArray(result.models) ? result.models : [];
    state.defaultModel = typeof result.default_model === 'string' ? result.default_model : null;
    state.canSetPermissions = result.permission_modes_supported === true;
    state.modelLoadError = null;
  } catch (error) {
    state.canSetPermissions = false;
    state.modelLoadError = error.message;
    throw error;
  } finally {
    state.loadingModels = false;
    renderTargetPicker();
    renderCreateProject();
  }
}
async function createTask() {
  if (state.feedbackTransport === 'mcp_events' || !state.desktopAvailable || !state.modelChoice || !state.canSetPermissions || state.creatingTarget || state.switchingTarget || state.submitting || state.pendingSubmission || state.creatingProject || state.navigatingProject) return;
  const oldThreadId = state.boundThreadId;
  const body = {model:state.modelChoice, permission_mode:ui.createPermissions.value};
  const title = ui.createTitle.value.trim();
  if (title) body.title = title;
  if (state.effortChoice) body.reasoning_effort = state.effortChoice;
  state.creatingTarget = true;
  renderTargetPicker();
  try {
    const result = await api('/api/workspace/targets', {method:'POST', body});
    if (result.workspace) renderWorkspace(result.workspace);
    await refreshWorkspace();
    await loadTargets();
    ui.createTitle.value = '';
    ui.createTargetPanel.open = false;
    announce('已新建并切换到任务：' + targetName(result.thread_id || state.boundThreadId));
  } catch (error) {
    try {
      await refreshWorkspace();
      await loadTargets();
    } catch { /* Keep the original creation error visible. */ }
    if (state.boundThreadId && state.boundThreadId !== oldThreadId) {
      ui.createTargetPanel.open = false;
      announce('新任务已连接：' + targetName(state.boundThreadId));
    } else if (error.detail?.detail?.thread_id || error.detail?.thread_id) {
      const createdThreadId = error.detail?.detail?.thread_id || error.detail.thread_id;
      announce('任务已创建但未连接。请刷新任务列表后选择该任务：' + shortTaskId(createdThreadId), true);
    } else {
      announce('新建任务失败：' + error.message + '。请先刷新任务列表核对，再决定是否重试。', true);
    }
  } finally {
    state.creatingTarget = false;
    renderTargetPicker();
  }
}
async function switchTask(threadId) {
  if (state.feedbackTransport === 'mcp_events' || !state.desktopAvailable || !threadId || threadId === state.boundThreadId || state.switchingTarget || state.creatingTarget || state.pendingSubmission || state.creatingProject || state.navigatingProject) return;
  const target = state.targets?.find((item) => item.thread_id === threadId);
  if (targetIsBusy(target)) return;
  state.switchingTarget = true;
  renderTargetPicker();
  try {
    await api('/api/workspace/target', {method:'POST', body:{thread_id:threadId}});
    await refreshWorkspace();
    ui.manualTargetId.value = '';
    await loadTargets();
    announce('目标任务已切换到：' + targetName(state.boundThreadId || threadId));
  } catch (error) {
    announce('切换任务失败：' + error.message, true);
  } finally {
    state.switchingTarget = false;
    renderTargetPicker();
  }
}
function renderWorkspace(workspace) {
  state.poseCorrectionsSupported = workspace.pose_corrections_supported === true;
  state.imageReferencesSupported = workspace.image_references_supported === true;
  renderPromptDragControls();
  promptMentions?.refresh();
  state.networkError = null;
  state.projectId = workspace.project_id || state.projectId;
  if (typeof workspace.project_name === 'string' && workspace.project_name.trim()) state.projectName = workspace.project_name;
  state.deliveryMode = workspace.delivery_mode === 'external' ? 'external' : 'app_server';
  state.feedbackTransport = workspace.feedback_transport === 'mcp_events' ? 'mcp_events' : null;
  state.eventDelivery = state.feedbackTransport === 'mcp_events' && workspace.event_delivery?.session_id === workspace.session_id
    ? workspace.event_delivery : null;
  state.desktopAvailable = workspace.desktop_available ?? !!workspace.thread_id;
  state.projectCreationSupported = state.feedbackTransport !== 'mcp_events' && (workspace.project_creation_supported ?? state.desktopAvailable);
  const nextThreadId = state.feedbackTransport !== 'mcp_events' && state.deliveryMode === 'external' ? workspace.thread_id || null : null;
  if (nextThreadId !== state.boundThreadId) state.targetChoice = nextThreadId;
  state.boundThreadId = nextThreadId;
  ui.taskButton.classList.toggle('hidden', state.feedbackTransport === 'mcp_events');
  ui.directTaskHelp.classList.toggle('hidden', state.feedbackTransport === 'mcp_events');
  ui.eventTaskHelp.classList.toggle('hidden', state.feedbackTransport !== 'mcp_events');
  ui.directDeliveryStatusHelp.classList.toggle('hidden', state.feedbackTransport === 'mcp_events');
  ui.eventDeliveryStatusHelp.classList.toggle('hidden', state.feedbackTransport !== 'mcp_events');
  ui.directDeliveryHelp.classList.toggle('hidden', state.feedbackTransport === 'mcp_events');
  ui.eventDeliveryHelp.classList.toggle('hidden', state.feedbackTransport !== 'mcp_events');
  if (state.feedbackTransport === 'mcp_events' && ui.tasksDialog.open) ui.tasksDialog.close();
  renderProjectPicker();
  state.agent = workspace.agent || {status:'disconnected'};
  state.queue = Array.isArray(workspace.queue) ? workspace.queue : [];
  state.approvals = Array.isArray(workspace.approvals) ? workspace.approvals : [];
  if (state.feedbackTransport === 'mcp_events') {
    renderEventWorkspace();
    return;
  }
  if (state.deliveryMode === 'external') {
    renderExternalWorkspace(workspace);
    return;
  }
  renderTargetPicker();
  ui.feedbackIntro.textContent = '圈画后直接发送图文消息。Codex 的进度和新场景会回到这里。';
  const status = state.agent.status;
  const statusText = {
    idle:'Codex 已连接，等待你的消息', running:'Codex 正在处理这一轮…',
    awaiting_approval:'Codex 需要你审批后继续',
    waiting:'反馈已保存，等待 Codex 空闲后自动发送',
    disconnected:'Codex 连接已断开，正在尝试恢复',
    delivery_uncertain:'消息送达状态待核实，请勿重复创建反馈',
    error:'Codex 会话发生错误'
  };
  ui.agentStatus.textContent = statusText[status] || '正在连接 Codex…';
  if (state.agent.error) ui.agentStatus.textContent += '：' + String(state.agent.error).slice(0, 240);
  ui.agentStatus.className = 'agent-status' + (status === 'running' ? ' running' : ['error','disconnected','delivery_uncertain'].includes(status) ? ' error' : '');
  ui.stop.classList.toggle('hidden', !['running','awaiting_approval'].includes(status));
  ui.pill.textContent = ({idle:'Codex 已连接',running:'Codex 执行中',awaiting_approval:'等待审批',waiting:'等待自动重试',disconnected:'连接中断',delivery_uncertain:'送达待核实',error:'连接错误'})[status] || status;
  ui.pill.className = 'session-pill ' + (status === 'running' ? 'running' : status === 'idle' ? 'open' : ['awaiting_approval', 'waiting'].includes(status) ? 'queued' : 'error');
  renderQueue();
  renderApprovals();
  updateSubmitLabel();
  minimalLayout?.refresh();
}
function renderEventWorkspace() {
  const delivery=state.eventDelivery || {};
  const subscribers=Number.isInteger(delivery.subscriber_count) ? delivery.subscriber_count : 0;
  const pending=Number.isInteger(delivery.pending_count) ? delivery.pending_count : 0;
  const waiting=Number.isInteger(delivery.waiting_count) ? delivery.waiting_count : 0;
  const last=delivery.last_delivery || [...state.queue].reverse().find((item) => item.feedback_transport === 'mcp_events');
  renderTargetPicker();
  ui.feedbackIntro.textContent='图文反馈通过 MCP 事件投递给订阅插件；这里显示的是事件投递状态。';
  if (last?.status === 'event_failed') {
    ui.agentStatus.textContent=subscribers
      ? '事件投递已失败；反馈仍保存在工作台，可通过 MCP 工具读取。'
      : '事件投递已失败，当前没有活动订阅；反馈仍保存在工作台。';
  } else if (!subscribers) {
    ui.agentStatus.textContent=waiting
      ? `等待插件订阅；${waiting} 条新反馈已保存，订阅后尝试投递。`
      : pending ? `当前没有活动订阅；${pending} 条事件仍在待投递队列。`
        : '当前没有活动订阅；反馈可先保存。';
  } else if (last?.status === 'event_delivered') {
    ui.agentStatus.textContent='事件已送达插件；模型是否继续处理由插件所在宿主决定。';
  } else {
    ui.agentStatus.textContent=pending
      ? `插件已订阅；${pending} 条反馈正在等待事件投递。`
      : '插件已订阅，可以接收视觉反馈。';
  }
  ui.agentStatus.className='agent-status' + (last?.status === 'event_failed' ? ' error' : '');
  ui.pill.textContent=last?.status === 'event_failed' ? '投递失败'
    : !subscribers ? waiting ? '等待插件订阅' : '未订阅'
      : last?.status === 'event_delivered' ? '事件已送达' : '插件已订阅';
  ui.pill.className='session-pill ' + (last?.status === 'event_failed' ? 'error' : !subscribers ? 'queued' : 'open');
  ui.stop.classList.add('hidden');
  ui.approvals.replaceChildren();
  renderEventDelivery();
  if (ui.conversation.firstElementChild?.classList.contains('muted')) {
    ui.conversation.firstElementChild.textContent='视觉反馈由订阅插件接收；事件送达不表示 Codex 已开始或完成执行。';
  }
  updateSubmitLabel();
  minimalLayout?.refresh();
}
function renderEventDelivery() {
  ui.queue.replaceChildren();
  const delivery=state.eventDelivery || {};
  const last=delivery.last_delivery || [...state.queue].reverse().find((item) => item.feedback_transport === 'mcp_events');
  if (!last) return;
  const subscribers=Number.isInteger(delivery.subscriber_count) ? delivery.subscriber_count : 0;
  const card=document.createElement('div'); card.className='queue-card';
  const title=document.createElement('strong');
  title.textContent=last.status === 'event_unsubscribed' && last.awaiting_subscription
    ? '等待插件订阅'
    : ({event_pending:subscribers ? '等待事件投递' : '事件待投递',
      event_delivered:'事件已送达插件',event_failed:'事件投递失败',
      event_unsubscribed:'当前无活动订阅'})[last.status] || '事件投递状态';
  const body=document.createElement('div');
  body.textContent=last.status === 'event_delivered'
    ? '插件的接收端已确认收到事件；这不表示 Codex 已开始或完成处理。'
    : last.status === 'event_failed'
      ? '自动投递已经停止。反馈和图像仍保存在工作台；请检查插件连接，必要时让插件调用 workspace_get_feedback 读取这条反馈。'
      : last.status === 'event_unsubscribed'
        ? last.awaiting_subscription
          ? '反馈已保存，尚未分配订阅。插件订阅后将尝试投递；也可通过 workspace_get_feedback 读取。'
          : '此前订阅已结束或取消，事件不会自动重新投递。反馈仍保存在工作台，可通过 workspace_get_feedback 读取。'
        : subscribers ? '反馈已保存，等待订阅插件接收事件。'
          : '反馈已保存，当前没有活动订阅。';
  card.append(title,body);
  if (last.error) {
    const error=document.createElement('div'); error.className='queue-warning';
    error.textContent='最近一次原因：' + String(last.error).slice(0,240); card.append(error);
  }
  const counts=document.createElement('div'); counts.className='queue-target';
  counts.textContent=`已送达 ${delivery.delivered_count || 0} · 待订阅 ${delivery.waiting_count || 0} · 待投递 ${delivery.pending_count || 0} · 失败 ${delivery.failed_count || 0}`;
  card.append(counts); ui.queue.append(card);
}
function renderExternalWorkspace(workspace) {
  const bound = !!state.boundThreadId;
  const status = state.agent.status;
  renderTargetPicker();
  ui.feedbackIntro.textContent = bound
    ? '圈画后发送图文反馈；工作台会送入当前选择的 Codex 任务，并显示处理进度。'
    : '圈画后保存视觉反馈；MCP 工具读取后才会交回 Codex。';
  const statusText = bound ? {
    idle:'已连接当前 Codex 任务，可以发送图文反馈。',
    running:'当前 Codex 任务正在处理视觉反馈。',
    awaiting_approval:'当前 Codex 任务需要审批后继续。',
    waiting:'反馈已保存，等待目标任务空闲或连接恢复后自动发送。',
    delivery_uncertain:'反馈送达状态待核实，请先查看目标任务。',
    disconnected:'暂时无法连接当前 Codex 任务；反馈会先保存。',
    error:'当前 Codex 任务连接或执行出错。'
  } : {
    waiting_for_mcp:'反馈可保存；MCP 工具读取后才会进入 Codex。',
    external_idle:'反馈可保存；MCP 工具读取后才会进入 Codex。',
    disconnected:'工作台尚未连接到 MCP 服务。',
    error:'MCP 反馈通道出错。'
  };
  ui.agentStatus.textContent = statusText[status] || (bound
    ? '正在连接当前 Codex 任务…' : '反馈可保存，等待 MCP 工具读取。');
  if (state.agent.error) ui.agentStatus.textContent += '：' + String(state.agent.error).slice(0, 240);
  ui.agentStatus.className = 'agent-status' + (status === 'running' ? ' running'
    : ['error', 'disconnected', 'delivery_uncertain'].includes(status) ? ' error' : '');
  ui.pill.textContent = bound ? ({idle:'当前任务已连接',running:'任务执行中',awaiting_approval:'等待审批',
    waiting:'等待自动重试',delivery_uncertain:'送达待核实',disconnected:'连接中断',error:'连接错误'})[status] || '连接目标任务'
    : '等待 MCP 读取';
  ui.pill.className = 'session-pill ' + (status === 'running' ? 'running'
    : ['awaiting_approval', 'waiting'].includes(status) ? 'queued'
    : ['error', 'disconnected', 'delivery_uncertain'].includes(status) ? 'error' : 'open');
  ui.stop.classList.toggle('hidden', !bound || !['running', 'awaiting_approval'].includes(status));
  if (bound) {
    renderQueue({boundExternal:true});
    renderApprovals();
  } else {
    ui.approvals.replaceChildren();
    renderExternalDelivery();
  }
  if (ui.conversation.firstElementChild?.classList.contains('muted')) {
    ui.conversation.firstElementChild.textContent = bound
      ? '图文反馈会送入目标 Codex 任务；完整对话请在目标任务中查看。'
      : '反馈保存在工作台；MCP 读取后，请在原 Codex 任务中查看后续。';
  }
  updateSubmitLabel();
  minimalLayout?.refresh();
}
function renderExternalDelivery() {
  ui.queue.replaceChildren();
  const item = state.queue.at(-1);
  if (!item || !['queued','submitted','awaiting_mcp','returned_to_mcp'].includes(item.status)) return;
  const card = document.createElement('div');
  card.className = 'queue-card';
  const title = document.createElement('strong');
  title.textContent = item.status === 'returned_to_mcp' ? 'MCP 已读取反馈' : '等待 MCP 读取';
  const body = document.createElement('div');
  body.textContent = item.status === 'returned_to_mcp'
    ? '反馈已从工作台取走；请在原 Codex 任务中查看是否继续执行。'
    : '图像和标记已保存。MCP 工具读取后才能交回 Codex。';
  card.append(title, body);
  ui.queue.append(card);
}
function renderQueue({boundExternal=false}={}) {
  ui.queue.replaceChildren();
  const pendingIds = new Set();
  const latest = state.queue.at(-1);
  for (const item of state.queue) {
    if (!item || ((item.status === 'completed' || item.status === 'discarded') && (!boundExternal || item !== latest))) continue;
    const oldTarget = boundExternal && !!item.target_thread_id && item.target_thread_id !== state.boundThreadId;
    const retryableFailed = item.status === 'failed' && !item.turn_id;
    const manual = !!item.feedback_id && (['blocked_stale','delivery_uncertain'].includes(item.status) || retryableFailed);
    const signature = JSON.stringify([item, state.sceneRevision, boundExternal, state.boundThreadId]);
    if (manual) {
      pendingIds.add(item.feedback_id);
      const saved = [...ui.pendingQueue.children].find(node => node.dataset.feedbackId === item.feedback_id);
      if (saved?.dataset.signature === signature) continue;
      saved?.remove();
    }
    const card = document.createElement('div');
    card.className = 'queue-card' + (item.status === 'queued' && item.error ? ' retrying'
      : item.status === 'delivery_uncertain' ? ' uncertain'
      : item.status === 'failed' ? ' failed' : '');
    const title = document.createElement('strong');
    title.textContent = ({queued:item.error ? '等待自动重试' : boundExternal ? '等待送入目标任务' : '已加入下一轮',dispatching:'正在送达',running:'正在处理',
      completed:'这一轮已完成',interrupted:'Codex 回合已中断',discarded:'这条反馈已舍弃',
      awaiting_mcp:'等待反馈通道读取',returned_to_mcp:'MCP 已读取反馈',
      blocked_stale:'请确认旧场景反馈',delivery_uncertain:item.quarantined_at ? '送达待核实 · 已隔离' : '送达待核实',
      failed:retryableFailed ? '发送失败' : 'Codex 回合失败'})[item.status] || '待处理消息';
    const body = document.createElement('div');
    body.textContent = '针对场景版本 ' + (item.scene_revision ?? '—') + (item.status === 'blocked_stale'
      ? '，当前场景已有新版本。确认后仍按旧截图发送。'
      : item.status === 'queued' && item.error ? '，反馈已保存；连接恢复或目标任务空闲后会自动重试。'
      : boundExternal && item.status === 'queued' ? '，反馈已保存，等待目标任务空闲后送入。'
      : boundExternal && item.status === 'running' ? '，图文反馈已送入目标 Codex 任务。'
      : retryableFailed ? '，反馈仍保存在工作台，可在问题解决后手动重试。'
      : item.status === 'failed' ? '，反馈已送入目标任务，但该回合执行失败；请检查目标任务后再提交新的反馈。'
      : item.status === 'interrupted' ? '，反馈已送入目标任务，但回合中断；请在目标任务中查看原因。'
      : item.status === 'discarded' ? '，这条反馈不会再次发送。'
      : '');
    if (state.sceneRevision !== null && item.scene_revision !== state.sceneRevision &&
        ['queued','dispatching','running'].includes(item.status)) {
      body.textContent += ' 当前场景是版本 ' + state.sceneRevision + '；反馈会保留原截图版本交给 Codex。';
    }
    if (item.error && ['queued','failed'].includes(item.status)) body.textContent += ' 最近一次原因：' + String(item.error).slice(0, 240);
    card.append(title, body);
    if (boundExternal && item.target_thread_id) {
      const target = document.createElement('div');
      target.className = 'queue-target' + (oldTarget ? ' old-target' : '');
      target.textContent = '目标任务：' + (item.target_title || targetName(item.target_thread_id)) +
        (oldTarget ? ' · 已固定到切换前的任务' : '');
      target.title = item.target_thread_id;
      card.append(target);
    }
    if (oldTarget && ['queued','dispatching','blocked_stale'].includes(item.status)) {
      const warning = document.createElement('div');
      warning.className = 'queue-warning';
      warning.textContent = '这条反馈仍发往切换前的任务；切回该任务后才会继续发送。';
      card.append(warning);
    }
    if (item.status === 'blocked_stale' && item.feedback_id) {
      const actions = document.createElement('div');
      actions.className = 'queue-actions';
      const confirm = document.createElement('button');
      confirm.type = 'button';
      confirm.textContent = '确认按旧截图发送';
      confirm.addEventListener('click', async () => {
        actions.querySelectorAll('button').forEach(button => button.disabled = true);
        try {
          await api('/api/workspace/queue/' + encodeURIComponent(item.feedback_id) + '/confirm', {method:'POST', body:{confirm:true}});
          announce('已确认；Codex 空闲后会处理这条消息。');
          await refreshWorkspace();
        } catch (error) { announce('确认失败：' + error.message, true); actions.querySelectorAll('button').forEach(button => button.disabled = false); }
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
      warning.className = 'queue-warning';
      warning.textContent = item.quarantined_at
        ? '这条反馈的送达结果仍无法确认，已暂时隔离；新反馈可以继续发送。请先核对它的目标任务，确认没有收到这条反馈后再重试。'
        : '这条反馈是否进入目标任务尚不确定；连接可用时工作台会尝试核对。请先查看它的目标任务，确认没有收到后再重试。';
      if (oldTarget) warning.textContent += ' 如需重试，请先切回这条反馈的目标任务。';
      if (item.error) warning.textContent += ' 当前原因：' + String(item.error).slice(0, 240);
      const actions = document.createElement('div');
      actions.className = 'queue-actions';
      for (const [retry,label] of [[true,'确认未收到，重试'],[false,'已处理，舍弃']]) {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = label;
        button.disabled = oldTarget && retry;
        button.addEventListener('click', async () => {
          actions.querySelectorAll('button').forEach((node) => node.disabled = true);
          try {
            await api('/api/workspace/queue/' + encodeURIComponent(item.feedback_id) + '/confirm', {method:'POST', body:{retry_uncertain:retry}});
            await refreshWorkspace();
          } catch (error) { announce('处理失败：' + error.message, true); actions.querySelectorAll('button').forEach((node) => { node.disabled = oldTarget && node.textContent === '确认未收到，重试'; }); }
        });
        actions.append(button);
      }
      card.append(warning, actions);
    }
    if (retryableFailed && item.feedback_id) {
      if (oldTarget) {
        const warning = document.createElement('div');
        warning.className = 'queue-warning';
        warning.textContent = '如需重试，请先切回这条反馈的目标任务。';
        card.append(warning);
      }
      const actions = document.createElement('div');
      actions.className = 'queue-actions';
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.textContent = '重试这条反馈';
      retry.disabled = oldTarget;
      retry.addEventListener('click', async () => {
        retry.disabled = true;
        try {
          await api('/api/workspace/queue/' + encodeURIComponent(item.feedback_id) + '/confirm', {method:'POST', body:{retry_failed:true}});
          announce('已加入队列；连接恢复或目标任务空闲后会发送。');
          await refreshWorkspace();
        } catch (error) { announce('重试失败：' + error.message, true); retry.disabled = false; }
      });
      actions.append(retry);
      card.append(actions);
    }
    if (manual) {
      card.dataset.feedbackId = item.feedback_id;
      card.dataset.signature = signature;
      card.tabIndex = -1;
      ui.pendingQueue.append(card);
    } else ui.queue.append(card);
  }
  for (const card of [...ui.pendingQueue.children]) if (!pendingIds.has(card.dataset.feedbackId)) card.remove();
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
    catch (error) { card.querySelector('.approval-error').textContent = error.message; return; }
    card.querySelector('.approval-error').textContent = '';
    actions.querySelectorAll('button').forEach((node) => node.disabled = true);
    try {
      await api('/api/workspace/approvals/' + encodeURIComponent(approval.approval_id) + '/respond', {method:'POST', body:payload});
      state.approvals = state.approvals.filter(item => item.approval_id !== approval.approval_id);
      card.remove();
      minimalLayout?.refresh();
      try { await refreshWorkspace(); }
      catch { announce('审批已送达，工作台状态会稍后刷新。'); }
    } catch (error) {
      card.querySelector('.approval-error').textContent = '发送失败：' + error.message + '。请重试。';
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
  card.tabIndex = -1;
  const error = document.createElement('p'); error.className = 'approval-error'; error.setAttribute('role', 'alert');
  card.append(error);
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
    if (details.command || details.changes) {
      const disclosure = document.createElement('details');
      const summary = document.createElement('summary'); summary.textContent = details.command ? '命令 · ' + String(details.command).split('\n')[0].slice(0, 96) : '查看文件修改';
      disclosure.append(summary);
      approvalText(disclosure, details.command || JSON.stringify(details.changes, null, 2), 'approval-raw');
      body.append(disclosure);
    }
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
function addConversation(type, message, time, {historical=false}={}) {
  if (!message) return;
  const previous = minimalLayout?.beforeConversationAppend();
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
  minimalLayout?.conversationAppended(previous, {historical});
}
function feedbackEventText(payload) {
  return [payload.object_prompts_summary, payload.note].filter((value) => typeof value === 'string' && value.trim()).join('\n') ||
    '已提交视觉反馈（含原图、标记和场景截图）';
}
async function fetchEvents(initial=false) {
  const events = await api('/api/workspace/events?after=' + (initial ? 0 : state.eventCursor));
  const items = Array.isArray(events.items) ? events.items : [];
  const appendConversation = (type, message, time) => addConversation(type, message, time, {historical:initial});
  for (const event of items) {
    if (!event || state.seenEventIds.has(event.id)) continue;
    state.seenEventIds.add(event.id);
    const payload = event.payload || {};
    const message = payload.text || payload.message || payload.summary || payload.prompt;
    if (state.feedbackTransport === 'mcp_events') {
      if (['feedback_queued','feedback_submitted','external_feedback_submitted','feedback_event_submitted'].includes(event.type)) {
        appendConversation('user',feedbackEventText(payload),event.at);
      } else if (['feedback_event_delivered','event_feedback_delivered'].includes(event.type)) {
        appendConversation('status','视觉反馈事件已送达订阅插件；模型处理状态请在插件所在宿主查看。',event.at);
      } else if (['feedback_event_failed','event_feedback_failed'].includes(event.type)) {
        appendConversation('error','视觉反馈事件投递失败；反馈仍保存在工作台。',event.at);
      } else if (event.type === 'scene_published') {
        appendConversation('status',message || '新场景已发布。',event.at);
      } else if (event.type === 'feedback_requested' || event.type === 'external_feedback_requested') {
        appendConversation('status',message || '订阅插件请求视觉反馈。',event.at);
      }
      continue;
    }
    if (state.deliveryMode === 'external' && !state.boundThreadId) {
      if (event.type === 'feedback_queued' || event.type === 'external_feedback_submitted' || event.type === 'feedback_submitted') {
        appendConversation('user', feedbackEventText(payload), event.at);
      } else if (event.type === 'feedback_returned_to_mcp' || event.type === 'mcp_feedback_returned') {
        appendConversation('status', 'MCP 已读取视觉反馈；请在原 Codex 任务中查看后续。', event.at);
      } else if (event.type === 'scene_published') {
        appendConversation('status', message || '新场景已发布。', event.at);
      } else if (event.type === 'feedback_requested' || event.type === 'external_feedback_requested') {
        appendConversation('assistant', message || 'Codex 正在请求视觉反馈。', event.at);
      }
      continue;
    }
    if (event.type === 'assistant_message' || event.type === 'assistant_text' || event.type === 'agent_message') {
      appendConversation('assistant', message, event.at);
    } else if (event.type === 'feedback_queued') {
      appendConversation('user', feedbackEventText(payload), event.at);
    } else if (event.type === 'turn_started') {
      appendConversation('status', 'Codex 开始处理这一轮。', event.at);
    } else if (event.type === 'turn_completed') {
      appendConversation('status', message || '这一轮已完成。', event.at);
    } else if (event.type === 'turn_failed' || event.type === 'disconnected') {
      appendConversation('error', message || '执行中断，请检查连接。', event.at);
    } else if (event.type === 'scene_published') {
      appendConversation('status', message || '新场景已发布。', event.at);
    } else if (event.type === 'feedback_requested') {
      appendConversation('assistant', message || '请查看当前场景并给出反馈。', event.at);
    } else if (event.type === 'approval_requested') {
      appendConversation('status', 'Codex 正在等待你确认，请在输入框上方处理。', event.at);
    } else if (event.type === 'approval_resolved') {
      appendConversation('status', ({accept:'已允许该操作。', decline:'已拒绝该操作。', cancel:'已取消该操作。', resolved_elsewhere:'该请求已在其他入口处理。', structured:'已提交所需回答。'})[payload.decision] || '确认已处理。', event.at);
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
    const gltf = await gltfLoader.loadAsync(resourceURL(item.url));
    if (state.objectNodes.get(item.id) !== root) return;
    root.remove(placeholder);
    root.scale.set(...(item.size || [1,1,1]));
    root.add(gltf.scene);
    root.userData.gltfRoot = gltf.scene;
    const asset = gltf.parser.json.asset || {};
    const declaredUp = String(gltf.scene.userData.up_axis || asset.extras?.up_axis || '').toLowerCase();
    root.userData.upAxis = ['y','z'].includes(declaredUp) ? declaredUp :
      asset.generator === 'scene-feedback-harness room demo' ? 'z' : 'y';
    root.userData.loaded = true;
    registerAnimations(item, gltf);
    if (state.selectedId === item.id) renderSelection();
  } catch (error) {
    placeholder.material.color.set(0xc47472);
    root.userData.loaded = true;
    announce('模型 ' + (item.name || item.id) + ' 加载失败：' + error.message, true);
  }
}

function applyGroundAxis(axis) {
  const yUp = axis === 'y';
  controls.setWorldUp(new THREE.Vector3(0, yUp ? 1 : 0, yUp ? 0 : 1));
  grid.rotation.set(yUp ? 0 : Math.PI / 2, 0, 0);
  grid.position.copy(controls.worldUp).multiplyScalar(-.003);
  ground.rotation.set(yUp ? -Math.PI / 2 : 0, 0, 0);
  ground.position.copy(controls.worldUp).multiplyScalar(-.008);
  hemisphereLight.position.copy(controls.worldUp);
  keyLight.position.copy(navigationDirection([5,-4,10]));
  fillLight.position.copy(navigationDirection([-5,6,5]));
  id('ground-axis').value = state.groundAxis;
}
function navigationDirection(values) {
  const vector = new THREE.Vector3(...values);
  return controls.worldUp.y === 1 ? vector.set(vector.x, vector.z, -vector.y) : vector;
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
function nodeReference(objectId, hitObject, level='part') {
  const modelRoot = state.objectNodes.get(objectId)?.userData.gltfRoot;
  if (!modelRoot) return null;
  let chosen = hitObject;
  if (level === 'item') {
    // One direct child of the glTF scene is one selectable item in this viewer.
    while (chosen?.parent && chosen.parent !== modelRoot) chosen = chosen.parent;
    if (chosen?.parent !== modelRoot) return null;
  } else {
    // Use the named hit node for a detailed part, skipping unnamed loader meshes.
    while (chosen !== modelRoot && !chosen.name?.trim() && !chosen.userData?.semantic_id && !chosen.userData?.stable_id) chosen = chosen.parent;
    if (chosen === modelRoot) chosen = hitObject;
  }
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
  for (const key of ['stable_id','semantic_id']) {
    if (typeof chosen.userData[key] === 'string' && chosen.userData[key].length >= 1 && chosen.userData[key].length <= 160 && !/[\x00-\x1f]/.test(chosen.userData[key])) reference[key] = chosen.userData[key];
  }
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
    ui.alignReference.textContent = '对齐';
    ui.alignReference.title = '请先选择参考图';
    ui.alignmentStatus.textContent = '选择带相机位姿的参考图，可让场景自动切换到同一视角。';
    return;
  }
  if (!pose) {
    ui.alignReference.textContent = '对齐';
    ui.alignReference.title = '当前参考图没有可用的相机位姿';
    ui.alignmentStatus.textContent = '这张图没有可用的相机位姿；仍可手动旋转场景对照。';
    return;
  }
  const current = state.alignedReferenceId === ref.id;
  ui.alignReference.textContent = current && state.alignmentExact ? '✓ 机位' : '对齐';
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
  camera.up.copy(controls.worldUp);
  camera.fov = 44;
  camera.updateProjectionMatrix();
  controls.update();
  resizeScene();
  showActiveReference();
  saveDraft();
}
function alignActiveReference({showLive=true, notify=false, persist=true}={}) {
  const ref = activeReference();
  const pose = referenceCamera(ref);
  if (!pose) {
    updateAlignmentStatus();
    return false;
  }
  controls.cancelTransition();
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
  if (persist) saveDraft();
  if (notify) announce('已切换到「' + (ref.name || '参考图') + '」的拍摄机位。');
  return true;
}
function frameBox(box, {smooth=false, keepDirection=false}={}) {
  if (!box || box.isEmpty()) return;
  controls.cancelTransition();
  controls.flush();
  const direction = keepDirection ? camera.position.clone().sub(controls.target).normalize() : navigationDirection([1,-1.4,0.95]).normalize();
  leaveReferenceCamera();
  const center = box.getCenter(new THREE.Vector3());
  const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 0.45);
  const halfFov = Math.min(camera.fov * Math.PI / 360, Math.atan(Math.tan(camera.fov * Math.PI / 360) * camera.aspect));
  const distance = Math.max(radius / Math.sin(halfFov) * 1.15, 0.6);
  camera.near = Math.max(distance / 1000, 0.005);
  camera.far = Math.max(distance * 100, 100);
  camera.updateProjectionMatrix();
  controls.moveTo(center.clone().addScaledVector(direction, distance), center, {smooth});
  saveDraft();
}
function frameAll(options={}) {
  const box = new THREE.Box3();
  for (const root of state.objectNodes.values()) box.expandByObject(root);
  if (box.isEmpty()) box.setFromCenterAndSize(new THREE.Vector3(0,0,0.5), new THREE.Vector3(3,3,2));
  frameBox(box, options);
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
      selectionHelper = new THREE.Box3Helper(box, document.documentElement.dataset.theme === 'dark' ? 0xecece8 : 0x292925);
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
    name.title = '拖到提示中引用选中的物品或部件';
    bindPromptDrag(name,selectedPromptReference,'selection');
    const objectId = document.createElement('div');
    objectId.className = 'selected-id';
    objectId.textContent = item.id + (state.selectedSceneNode ? ' · 节点 ' + (state.selectedSceneNode.node_path.join('/') || '根') : '');
    ui.selectionSummary.append(name, objectId);
    if (state.selectedSceneNode) {
      const cite = document.createElement('button');
      cite.type = 'button';
      cite.className = 'reference-insert selected-reference-insert';
      cite.textContent = '引用';
      cite.title = '在提示中引用选中的' + (state.selectionLevel === 'item' ? '物品' : '部件');
      cite.disabled = !editable();
      cite.addEventListener('mousedown', (event) => event.preventDefault());
      cite.addEventListener('click', () => insertSceneNodeReference({...state.selectedSceneNode}, nodeLabel));
      ui.selectionSummary.append(cite);
    }
    ui.selectedChip.textContent = '已选' + (state.selectedSceneNode ? (state.selectionLevel === 'item' ? '物品' : '部件') : '对象') + ' · ' + (nodeLabel || item.name || item.id);
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
  ui.frame.disabled = !editable() || !state.selectedId;
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
    const row = document.createElement('div');
    row.className = 'object-row';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'object-item' + (state.selectedId === item.id ? ' active' : '');
    button.dataset.objectId = item.id;
    button.title = '点选物体，或拖到提示中引用';
    bindPromptDrag(button,() => ({kind:'object',objectId:item.id,label:item.name || item.id}),'object');
    const swatch = document.createElement('span');
    swatch.className = 'object-color';
    swatch.style.backgroundColor = item.color || '#94b5bf';
    const name = document.createElement('strong');
    name.textContent = item.name || item.id;
    const kind = document.createElement('small');
    kind.textContent = ({box:'方盒',sphere:'球体',cylinder:'圆柱',model:'模型'})[item.type] || item.type;
    button.append(swatch, name, kind);
    button.addEventListener('click', () => selectObject(item.id));
    const cite = document.createElement('button');
    cite.type = 'button';
    cite.className = 'reference-insert object-reference-insert';
    cite.textContent = '引用';
    cite.title = '在提示中引用 ' + (item.name || item.id);
    cite.disabled = !editable();
    cite.addEventListener('mousedown', (event) => event.preventDefault());
    cite.addEventListener('click', () => insertNoteReference(item.name || item.id, `[[object:${item.id}]]`));
    row.append(button, cite);
    ui.objectList.append(row);
  }
}
function selectObject(objectId, sceneNode=null, detailNode=null) {
  if (!editable()) return;
  if (!sceneObject(objectId)) return;
  state.selectedId = objectId;
  state.selectedSceneNode = sceneNode;
  state.lastPickedDetailNode = detailNode;
  renderSelection();
  saveDraft();
}
async function loadScene(sceneData) {
  const scene = sceneData || await api('/api/scene');
  const priorRevision = state.sceneRevision;
  if (state.sceneLoading || priorRevision === scene.revision) return;
  state.sceneLoading = true;
  updateSubmitLabel(); updateMode(); renderTimeline();
  try {
    const priorSelectedObject = sceneObject(state.selectedId);
    state.sceneRevision = scene.revision;
    state.sceneObjects = scene.objects || [];
    if (priorRevision !== null && priorSelectedObject?.url !== sceneObject(state.selectedId)?.url) {
      state.selectedSceneNode = null;
      state.lastPickedDetailNode = null;
    }
    if (priorRevision === null && (state.selectedSceneNode || state.lastPickedDetailNode) &&
        (state.restoredModelUrl !== (sceneObject(state.selectedId)?.url || null) ||
         (state.restoredModelUrl === null && state.restoredSceneRevision !== scene.revision))) {
      state.selectedSceneNode = null;
      state.lastPickedDetailNode = null;
    }
    pauseTimeline();
    for (const entry of state.animations.values()) {
      entry.mixer.stopAllAction();
      entry.mixer.uncacheRoot(entry.root);
    }
    state.animations.clear();
    objectLayer.traverse((node) => {
      node.geometry?.dispose();
      for (const material of (Array.isArray(node.material) ? node.material : node.material ? [node.material] : [])) {
        for (const value of Object.values(material)) if (value?.isTexture) value.dispose();
        material.dispose();
      }
    });
    objectLayer.clear();
    state.objectNodes.clear();
    await Promise.allSettled(state.sceneObjects.map(addObject));
    const axes = new Set([...state.objectNodes.values()].filter(root => root.userData.loaded).map(root => root.userData.upAxis || 'z'));
    // Mixed or primitive scenes retain the workspace convention; GLB uses Y-up unless explicitly declared.
    state.detectedUpAxis = axes.size === 1 ? [...axes][0] : 'z';
    const axis = state.groundAxis === 'auto' ? state.detectedUpAxis : state.groundAxis;
    const changedAxis = (controls.worldUp.y === 1 ? 'y' : 'z') !== axis;
    const alignedPose = state.alignedReferenceId ? {position:camera.position.clone(), up:camera.up.clone(), quaternion:camera.quaternion.clone()} : null;
    applyGroundAxis(axis);
    if (alignedPose) {
      camera.position.copy(alignedPose.position); camera.up.copy(alignedPose.up); camera.quaternion.copy(alignedPose.quaternion);
    } else if (changedAxis) state.firstFrame = true;
    applyAnimationTime(state.time);
    renderTimeline();
    renderAnimationChoices();
    frameAllIfReady();
    if (state.selectedId && !sceneObject(state.selectedId)) state.selectedId = null;
    if (state.selectedSceneNode && !resolveSceneNode(state.selectedSceneNode)) state.selectedSceneNode = null;
    if (state.lastPickedDetailNode && !resolveSceneNode(state.lastPickedDetailNode)) state.lastPickedDetailNode = null;
    renderSelection();
    renderAnnotations();
    drawOverlays();
    id('revision-label').textContent = '版本 ' + scene.revision;
    state.sceneDisplayName = scene.name || '当前场景';
    updateProjectTitle();
    id('scene-title').textContent = scene.name || '';
    if (priorRevision !== null) {
      announce('场景已更新到版本 ' + scene.revision + '。已有标注仍绑定原截图。');
    }
    renderSceneView();
    saveDraft();
  } finally {
    state.sceneLoading = false;
    updateSubmitLabel(); updateMode(); renderTimeline();
    renderProjectPicker();
  }
}

function settleOrbit() {
  const damping = controls.enableDamping;
  controls.enableDamping = false; controls.update(); controls.enableDamping = damping;
}
function captureLiveScene({includeSize=false}={}) {
  controls.update();
  renderer.render(threeScene, camera);
  const source = renderer.domElement;
  const canvas = scaledCanvas(source.width, source.height, 1440);
  canvas.getContext('2d').drawImage(source, 0, 0, canvas.width, canvas.height);
  const dataUrl = canvas.toDataURL('image/jpeg', 0.88);
  return includeSize ? {data_url:dataUrl, width:canvas.width, height:canvas.height} : dataUrl;
}
async function openSavedSnapshot() {
  if (!editable() || state.sceneView === 'snapshot') return;
  const snapshot = state.snapshot || state.sceneSnapshots.at(-1) || state.dynamicSnapshots.at(-1);
  if (!snapshot) {
    announce('当前还没有截图，请点击「＋ 截图」新增。');
    return;
  }
  if (snapshot.time_sec !== undefined) {
    await openMoment(snapshot.id);
  } else {
    openSceneSnapshot(snapshot.id);
  }
}
function freezeScene() {
  if (!editable() || state.sceneView !== 'live') return;
  if (dynamicEnabled()) { ensureDynamicMoment({showSnapshot:true}); return; }
  if (state.sceneSnapshots.length >= 8) { announce('最多保留 8 张截图，请先删除一张再截取。', true); return; }
  const comparison = captureSnapshotComparison();
  if (comparison === false) return;
  const before = annotationEditState();
  if (state.mode === 'select') state.mode = 'rectangle';
  settleOrbit();
  const shot = captureLiveScene({includeSize:true});
  state.snapshot = {
    id:newId(), name:'截图 ' + (Math.max(0, ...state.sceneSnapshots.map(entry => entry.number || 0)) + 1),
    number:Math.max(0, ...state.sceneSnapshots.map(entry => entry.number || 0)) + 1,
    data_url:shot.data_url, image_width:shot.width, image_height:shot.height, comparison,
    scene_revision:state.sceneRevision,
    camera:cameraData(), selected_object_ids:state.selectedId ? [state.selectedId] : [],
    selected_scene_nodes:state.selectedSceneNode ? [{...state.selectedSceneNode}] : []
  };
  state.sceneSnapshots.push(state.snapshot);
  state.sceneView = 'snapshot';
  recordAnnotationEdit(before);
  renderSceneView();
  renderAnnotations();
  saveDraft();
  announce('已固定当前视角。现在可在这张截图上标注。');
}
function openSceneSnapshot(snapshotId) {
  if (!editable()) return;
  const snapshot = state.sceneSnapshots.find(entry => entry.id === snapshotId);
  if (!snapshot) return;
  pauseTimeline(); hideTextEditor(); state.drag = null;
  state.snapshot = snapshot; state.sceneView = 'snapshot';
  renderSceneView(); renderAnnotations();
}
function renderSceneSnapshots() {
  ui.snapshotStrip.replaceChildren();
  for (const snapshot of state.sceneSnapshots) {
    const card = document.createElement('div'); card.className = 'snapshot-card';
    const open = document.createElement('button'); open.type = 'button'; open.className = 'snapshot-open';
    open.dataset.snapshotId = snapshot.id;
    open.disabled = !editable();
    open.setAttribute('aria-pressed', String(state.sceneView === 'snapshot' && state.snapshot?.id === snapshot.id));
    const count = state.annotations.filter(mark => mark.pane === 'scene' && mark.snapshot_id === snapshot.id).length;
    open.title = snapshot.name + ' · 场景版本 ' + snapshot.scene_revision + ' · ' + count + ' 个标记';
    const image = document.createElement('img'); image.src = snapshot.data_url; image.alt = '';
    const label = document.createElement('span'); label.textContent = snapshot.name + (count ? ' · ' + count : '');
    open.append(image, label); open.addEventListener('click', () => openSceneSnapshot(snapshot.id));
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'snapshot-remove'; remove.textContent = '×';
    remove.title = '删除' + snapshot.name + '及其标记，可撤销'; remove.setAttribute('aria-label', remove.title);
    remove.disabled = !editable();
    remove.addEventListener('click', () => {
      if (!editable()) return;
      const before = annotationEditState();
      hideTextEditor(); state.drag = null;
      state.sceneSnapshots = state.sceneSnapshots.filter(entry => entry.id !== snapshot.id);
      state.annotations = state.annotations.filter(mark => mark.pane !== 'scene' || mark.snapshot_id !== snapshot.id);
      if (state.snapshot?.id === snapshot.id) { state.snapshot = null; state.sceneView = 'live'; state.mode = 'select'; }
      recordAnnotationEdit(before); renderSceneView(); renderAnnotations(); saveDraft();
      announce('已删除' + snapshot.name + '，可撤销恢复。');
    });
    card.append(open, remove); ui.snapshotStrip.append(card);
  }
  workspaceControls?.refresh();
}
function updateSnapshotGeometry() {
  renderPromptDragControls();
  if (!state.snapshot) return;
  // Size a new screenshot immediately. Its image can still be decoding when
  // the user's first drag starts, or the previous image can have another aspect.
  const width = state.snapshot.image_width || (ui.snapshotImage.complete && ui.snapshotImage.naturalWidth);
  const height = state.snapshot.image_height || (ui.snapshotImage.complete && ui.snapshotImage.naturalHeight);
  if (!width || !height) return;
  const scale = Math.min(
    ui.sceneStage.clientWidth / width,
    ui.sceneStage.clientHeight / height
  );
  ui.snapshotMedia.style.width = Math.max(1, width * scale) + 'px';
  ui.snapshotMedia.style.height = Math.max(1, height * scale) + 'px';
  drawOverlays();
}
function restoreComparePreferences() {
  if (!state.sessionId || comparePreferences.sessionId === state.sessionId) return;
  Object.assign(comparePreferences, {sessionId:state.sessionId, enabled:true, opacity:45, lastPositive:45});
  try {
    const stored = JSON.parse(localStorage.getItem('astra-visual-compare:' + state.sessionId) || 'null');
    if (typeof stored?.enabled === 'boolean') comparePreferences.enabled = stored.enabled;
    if (Number.isFinite(stored?.opacity) && stored.opacity >= 0 && stored.opacity <= 100) {
      comparePreferences.opacity = Math.round(stored.opacity);
      if (comparePreferences.opacity > 0) comparePreferences.lastPositive = comparePreferences.opacity;
    }
    if (Number.isFinite(stored?.lastPositive) && stored.lastPositive > 0 && stored.lastPositive <= 100) {
      comparePreferences.lastPositive = Math.max(1, Math.round(stored.lastPositive));
    }
    if (!comparePreferences.opacity) comparePreferences.enabled = false;
  } catch { /* View controls still work when browser storage is unavailable. */ }
  renderCompareControls();
}
function saveComparePreferences() {
  if (!state.sessionId) return;
  const {enabled, opacity, lastPositive} = comparePreferences;
  try {
    localStorage.setItem('astra-visual-compare:' + state.sessionId, JSON.stringify({enabled, opacity, lastPositive}));
  } catch { /* Keep this browser-only preference independent of feedback evidence. */ }
}
function currentComparison() {
  return state.sceneView === 'snapshot' ? state.snapshot?.comparison : comparePreferences;
}
function compareAvailable() {
  return state.sceneView === 'snapshot' ? !!state.snapshot?.comparison : !!activeReference();
}
function renderCompareControls() {
  const frozen = state.sceneView === 'snapshot';
  const available = compareAvailable();
  const {enabled=false, opacity=45} = currentComparison() || {};
  const visible = available && enabled && opacity > 0;
  const summary = !available ? '—' : visible ? opacity + '%' : '关';
  const status = !available ? (frozen ? '这张截图没有保存叠图参考；请回到 3D 新增截图。' : '先添加参考图，再使用叠图对比。')
    : frozen ? '本图参考：' + state.snapshot.comparison.reference_name + '；叠图设置和标记将随反馈发送。'
    : visible ? '调整透明度，对照参考图与实时场景。' : '叠图已关闭，点击「叠图」开启。';
  ui.compareImage.classList.toggle('hidden', frozen || !visible);
  ui.compareImage.style.opacity = comparePreferences.opacity / 100;
  const overlay = ui.snapshotCompareImage;
  overlay.classList.toggle('hidden', !frozen || !visible);
  if (frozen && available) {
    const comparison = state.snapshot.comparison;
    if (overlay.getAttribute('src') !== comparison.data_url) overlay.src = comparison.data_url;
    overlay.style.opacity = opacity / 100;
    for (const [property, field] of [['left','x'],['top','y'],['width','width'],['height','height']]) {
      overlay.style[property] = comparison.rect[field] * 100 + '%';
    }
  }
  ui.compareOpacity.disabled = !available;
  ui.compareOpacity.value = String(opacity);
  ui.compareOpacity.setAttribute('aria-valuetext', opacity + '%');
  ui.opacityValue.textContent = opacity + '%';
  ui.compareToggle.disabled = !available;
  ui.compareToggle.setAttribute('aria-pressed', String(visible));
  ui.compareToggle.classList.toggle('active', visible);
  ui.compareToggle.title = !available ? status : visible ? '关闭叠图' : '开启叠图';
  ui.compareSummary.textContent = summary;
  ui.compareStatus.textContent = status;
  for (const button of ui.comparePresets) {
    const selected = visible && Number(button.dataset.compareOpacity) === opacity;
    button.disabled = !available;
    button.setAttribute('aria-pressed', String(selected));
    button.classList.toggle('active', selected);
  }
}
function updateComparison(next) {
  if (state.sceneView === 'snapshot') {
    // Replace evidence immutably so undo snapshots and queued IDB writes retain
    // their original settings. The reference pixels/placement never change.
    state.snapshot = {...state.snapshot, comparison:next};
    for (const key of ['sceneSnapshots', 'dynamicSnapshots']) {
      state[key] = state[key].map(entry => entry.id === state.snapshot.id ? state.snapshot : entry);
    }
    saveDraft();
  } else {
    Object.assign(comparePreferences, next);
    saveComparePreferences();
  }
  renderCompareControls();
}
function setCompareOpacity(value) {
  if (!editable() || !compareAvailable() || !Number.isFinite(value)) return;
  const opacity = Math.round(clamp(value, 0, 100));
  const current = currentComparison();
  updateComparison({...current, opacity, enabled:opacity > 0, lastPositive:opacity || current.lastPositive || 45});
}
function toggleCompare() {
  if (!editable() || !compareAvailable()) return;
  const current = currentComparison();
  const enabled = !(current.enabled && current.opacity > 0);
  updateComparison({...current, enabled, opacity:enabled && !current.opacity ? current.lastPositive || 45 : current.opacity});
}
function captureSnapshotComparison() {
  const ref = activeReference();
  if (!ref) return null;
  const image = ui.compareImage;
  const sourceUrl = alignmentOverlayUrl(ref);
  if (!imageReadyAtUrl(image, sourceUrl)) {
    announce('叠图参考正在加载，请稍后再截图或发送。', true);
    return false;
  }
  const stage = ui.sceneStage.getBoundingClientRect(), viewport = ui.viewport.getBoundingClientRect();
  const scale = Math.min(stage.width / image.naturalWidth, stage.height / image.naturalHeight);
  const width = image.naturalWidth * scale, height = image.naturalHeight * scale;
  const canvas = scaledCanvas(image.naturalWidth, image.naturalHeight, 1440);
  canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height);
  return {reference_id:ref.id, reference_name:ref.name || '参考图',
    source:sourceUrl === ref.alignment_image_url ? 'undistorted' : 'original', source_url:sourceUrl,
    data_url:canvas.toDataURL('image/png'),
    enabled:comparePreferences.enabled, opacity:comparePreferences.opacity, lastPositive:comparePreferences.lastPositive,
    alignment_exact:state.alignedReferenceId === ref.id && state.alignmentExact,
    rect:{x:(stage.left + (stage.width - width) / 2 - viewport.left) / viewport.width,
      y:(stage.top + (stage.height - height) / 2 - viewport.top) / viewport.height,
      width:width / viewport.width, height:height / viewport.height}};
}
function comparisonMatchesLive(snapshot) {
  const comparison = snapshot?.comparison, ref = activeReference();
  if (!comparison) return !ref;
  return comparison.reference_id === ref?.id && comparison.opacity === comparePreferences.opacity &&
    comparison.enabled === comparePreferences.enabled &&
    comparison.source === (alignmentOverlayUrl(ref) === ref?.alignment_image_url ? 'undistorted' : 'original');
}

function renderSceneView({persist=true}={}) {
  const hasSnapshot = !!state.snapshot;
  if (!hasSnapshot) state.sceneView = 'live';
  const showingSnapshot = hasSnapshot && state.sceneView === 'snapshot';
  if (hasSnapshot && ui.snapshotImage.src !== state.snapshot.data_url) ui.snapshotImage.src = state.snapshot.data_url;
  ui.snapshotMedia.classList.toggle('hidden', !showingSnapshot);
  ui.browse.setAttribute('aria-pressed', String(!showingSnapshot));
  ui.snapshotButton.setAttribute('aria-pressed', String(showingSnapshot));
  ui.snapshotButton.title = '浏览上一次操作的截图；新增截图请点击「＋ 截图」';
  ui.savedSnapshot.classList.toggle('hidden', !hasSnapshot || showingSnapshot);
  ui.newSceneBadge.classList.toggle('hidden', !showingSnapshot || state.snapshot.scene_revision === state.sceneRevision);
  if (hasSnapshot && state.snapshot.scene_revision !== state.sceneRevision) {
    ui.newSceneBadge.textContent = '标注 v' + state.snapshot.scene_revision + ' · 查看最新 v' + state.sceneRevision + ' ↗';
    ui.newSceneBadge.title = '这些标记保留在版本 ' + state.snapshot.scene_revision + ' 的截图上。点击查看版本 ' + state.sceneRevision + '。';
  }
  renderCompareControls();
  renderSceneSnapshots();
  controls.enabled = editable() && state.mode === 'select' && !showingSnapshot;
  updateSnapshotGeometry();
  updateMode();
  updateSceneHint();
  if (persist) saveDraft();
}

function setReferences(references) {
  if (JSON.stringify(state.references) === JSON.stringify(references)) return;
  const previousActive = activeReference();
  const previousCamera = JSON.stringify(previousActive?.camera || null);
  state.references = references;
  if (!clipReference() && !references.some((ref) => ref.id === state.activeReferenceId)) {
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
function activeReference() { return clipReference() || state.references.find((ref) => ref.id === state.activeReferenceId); }
function renderReferenceStrip() {
  renderReferenceViews();
  ui.referenceStrip.replaceChildren();
  if (state.referenceClip) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'compact-button';
    button.textContent = (state.clipEnabled ? '✓ ' : '↺ ') + (referenceViews().length > 1 ? '同步参考' : state.referenceClip.name);
    button.addEventListener('click', () => {
      if (!editable()) return;
      state.clipEnabled = true; seekTimeline(state.time);
      renderReferenceStrip();
    });
    ui.referenceStrip.append(button);
  }
  if (!state.references.length && !state.referenceClip) {
    const empty = document.createElement('span');
    empty.className = 'muted';
    empty.textContent = '还没有参考图';
    ui.referenceStrip.append(empty);
    return;
  }
  for (const ref of state.references) {
    const view = viewForReferenceImage(referenceViews(), ref);
    const button = document.createElement('button');
    button.type = 'button';
    const selected = view && state.clipEnabled ? view.clip_id === (state.pendingViewId || state.activeViewId) : ref.id === state.activeReferenceId;
    button.className = 'thumb' + (selected ? ' active' : '');
    button.title = ref.name || '参考图';
    button.setAttribute('aria-label', '查看 ' + (ref.name || '参考图'));
    const image = document.createElement('img');
    image.src = resourceURL(ref.url);
    image.alt = '';
    button.append(image);
    if (referenceCamera(ref)) {
      const cameraBadge = document.createElement('span');
      cameraBadge.className = 'thumb-camera';
      cameraBadge.textContent = view ? '同步' : '机位';
      button.append(cameraBadge);
      button.title = (ref.name || '参考图') + (view ? ' · 切换同步机位，保持当前时间' : ' · 有相机位姿');
    }
    const count = state.annotations.filter((a) => a.pane === 'reference' && a.reference_image_id === ref.id).length;
    if (count) {
      const badge = document.createElement('span');
      badge.className = 'thumb-count';
      badge.textContent = String(count);
      button.append(badge);
    }
    button.addEventListener('click', () => {
      if (!editable()) return;
      if (view) {
        state.referenceZoom = 1;
        state.referencePan = {x:0,y:0};
        seekTimeline(state.timelineTarget ?? state.time, {viewId:view.clip_id, preserveTime:true});
        return;
      }
      pauseTimeline();
      state.clipEnabled = false;
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
  renderHumanPosePanel();
  renderCompareControls();
  if (!hasReference) {
    ui.referenceImage.removeAttribute('src');
    drawOverlays();
    return;
  }
  if (!imageMatchesUrl(ui.referenceImage, ref.url)) {
    delete ui.referenceImage.dataset.sourceUrl;
    ui.referenceImage.src = resourceURL(ref.url);
  }
  const compareUrl = alignmentOverlayUrl(ref);
  if (!imageMatchesUrl(ui.compareImage, compareUrl)) {
    delete ui.compareImage.dataset.sourceUrl;
    ui.compareImage.src = resourceURL(compareUrl);
  }
  if (ui.referenceImage.complete) updateReferenceGeometry();
}
function imageMatchesUrl(image, url) {
  const source = image.getAttribute('src');
  const resolved = resourceURL(url);
  return source === resolved || (source?.startsWith('blob:') && image.dataset.sourceUrl === resolved);
}
function imageReadyAtUrl(image, url) {
  return image.complete && image.naturalWidth > 0 && imageMatchesUrl(image, url);
}
function installTimelineImages(image, overlay) {
  if (image !== ui.referenceImage) {
    image.id = 'reference-image'; image.alt = '当前参考帧';
    image.onload = updateReferenceGeometry;
    image.onerror = () => announce('这张参考图无法显示。', true);
    ui.referenceImage.replaceWith(image); ui.referenceImage = image;
  }
  overlay.id = ui.compareImage.id;
  overlay.alt = ui.compareImage.alt;
  overlay.className = ui.compareImage.className;
  overlay.style.cssText = ui.compareImage.style.cssText;
  overlay.onload = resizeScene;
  ui.compareImage.replaceWith(overlay); ui.compareImage = overlay;
  renderCompareControls();
}
function humanJobNumber(job) {
  const ordered = [...state.humanJobs].sort((a,b) => String(a.created_at || '').localeCompare(String(b.created_at || '')) || a.job_id.localeCompare(b.job_id));
  return Math.max(0, ordered.findIndex((item) => item.job_id === job.job_id)) + 1;
}
function humanJobName(job) { return '人体结果' + humanJobNumber(job); }
function humanOverlayJob() {
  if (state.humanOverlayChoice === 'hidden') return null;
  const jobs=state.humanJobs.filter((job) => job.status === 'completed' && humanJobContainsCurrentReference(job));
  if (state.humanOverlayChoice !== 'latest') return jobs.find((job) => job.job_id === state.humanOverlayChoice) || null;
  const observations=jobs.filter((job) => job.evidence_kind !== 'projected_3d');
  return [...(observations.length ? observations : jobs)].sort((a,b) =>
    String(b.finished_at || b.created_at || '').localeCompare(String(a.finished_at || a.created_at || '')) ||
    String(b.created_at || '').localeCompare(String(a.created_at || '')) || b.job_id.localeCompare(a.job_id))[0] || null;
}
function setHumanOverlayChoice(choice) {
  if (!['latest','hidden'].includes(choice) && !/^[0-9a-f]{32}$/.test(choice || '')) return;
  state.humanOverlayChoice=choice;
  renderHumanPosePanel(); drawHumanPoseOverlay(); saveDraft();
  if (state.playing && state.sessionId) {
    // Playback keeps its visual draft fixed; this display preference can still
    // be saved without replacing the stored frame, camera or annotations.
    try {
      const draft=JSON.parse(localStorage.getItem(storageKey()) || '{}');
      draft.humanOverlayChoice=choice;
      localStorage.setItem(storageKey(),JSON.stringify(draft));
    } catch { /* A display preference should not block playback. */ }
  }
}
function humanAutomaticJob(job) { return job.automatic === true || job.all_views === true; }
function humanPosePageOffset(job, referenceId) {
  if (!humanAutomaticJob(job) || !referenceId || !Array.isArray(job.views)) return null;
  if (job.source_kind === 'static_references') {
    const index=job.views.findIndex((item) => item.reference_id === referenceId);
    return index < 0 ? null : Math.floor(index/32)*32;
  }
  const view=referenceViews().find((item) => item.frames?.some((frame) => frame.id === referenceId));
  const index=job.views.findIndex((item) => item.view_id === view?.clip_id);
  const frameIndex=view?.frames?.findIndex((frame) => frame.id === referenceId) ?? -1;
  if (index < 0 || frameIndex < 0 || !job.views.slice(0,index).every((item) => Number.isInteger(item.sampled_frames))) return null;
  const absolute=job.views.slice(0,index).reduce((sum,item) => sum+item.sampled_frames,0)+frameIndex;
  return Math.floor(absolute/32)*32;
}
function humanPoseLoadKey(job, referenceId) {
  if (!humanAutomaticJob(job) || !referenceId) return job.job_id;
  const pageOffset=humanPosePageOffset(job,referenceId);
  return job.job_id + ':' + (pageOffset === null ? referenceId : 'page:' + pageOffset);
}
function humanCurrentFrame(job) {
  const detail = state.humanDetails.get(job.job_id);
  const ref = activeReference();
  // Automatic jobs sample every source frame. Never paint a cached skeleton
  // on a different frame while that frame's result is still loading.
  if (humanAutomaticJob(job)) return detail?.frames?.find((frame) => frame.reference_id === ref?.id) || null;
  return poseFrameForReference(detail, ref?.id, clipReference() ? referenceView()?.clip_id : null, state.time);
}
function renderHumanPosePanel() {
  renderPoseEditor();
  const jobs=state.humanJobs.filter((job) => job.status === 'completed');
  ui.humanPanel.classList.toggle('hidden',!jobs.length);
  ui.humanLatest.setAttribute('aria-pressed',String(state.humanOverlayChoice === 'latest'));
  ui.humanHideAll.setAttribute('aria-pressed',String(state.humanOverlayChoice === 'hidden'));
  const overlayJob=humanOverlayJob();
  ui.humanStatus.textContent=state.humanError || '';
  ui.humanStatus.classList.toggle('hidden',!state.humanError);
  for (const job of state.humanJobs) {
    if (job.status === 'completed' && humanJobContainsCurrentReference(job) && !humanCurrentFrame(job)) {
      const referenceId=humanAutomaticJob(job) ? activeReference()?.id : null;
      const loadKey=humanPoseLoadKey(job,referenceId);
      const busyForJob=humanAutomaticJob(job) && [...state.humanDetailLoads].some((key) => key.startsWith(job.job_id + ':'));
      if (!busyForJob && !state.humanDetailLoads.has(loadKey) && !state.humanDetailErrors.has(loadKey)) {
        Promise.resolve().then(() => ensureHumanPoseDetail(job,{referenceId}));
      }
    }
  }
  const jobsSignature=JSON.stringify([jobs,state.poseRefs,state.poseEdits,state.poseCorrectionsSupported,editable(),state.humanOverlayChoice,overlayJob?.job_id,
    jobs.map((job) => [job.job_id,humanCurrentFrame(job)?.reference_id,
      state.humanDetailLoads.has(job.job_id),state.humanDetailErrors.get(job.job_id),
      state.humanDetailLoads.has(humanPoseLoadKey(job,activeReference()?.id)),
      state.humanDetailErrors.get(humanPoseLoadKey(job,activeReference()?.id))])]);
  if (jobsSignature === state.humanJobsSignature) return;
  state.humanJobsSignature=jobsSignature; ui.humanJobs.replaceChildren();
  for (const job of [...jobs].reverse()) {
    const row=document.createElement('div'); row.className='human-pose-job';
    const heading=document.createElement('div'); heading.className='human-pose-job-heading';
    const name=document.createElement('strong'); name.textContent=humanJobName(job);
    const status=document.createElement('span'); status.textContent=({awaiting_import:'等待外部结果',completed:'已完成',failed:'失败',cancelled:'已取消',interrupted:'已中断'})[job.status] || job.status;
    heading.append(name,status); row.append(heading);
    const source=document.createElement('small'); source.textContent=job.multi_view
      ? `${job.source_kind === 'static_references' ? '参考图' : '多机位'} · ${(Array.isArray(job.views) ? job.views : []).map((item) => item.view_name || referenceView(item.view_id)?.name || item.view_id).join('、')}`
      : job.view_name || job.reference_name || (humanAutomaticJob(job) ? '全部参考图' : '参考图'); row.append(source);
    if (job.status === 'completed') {
      const origin=document.createElement('small'); origin.textContent=poseEvidenceLabel(job) + (job.keypoint_profile ? ' · ' + job.keypoint_profile : '');
      if (job.evidence_kind === 'projected_3d') origin.title='从三维人体投影得到，用来检查重建；不表示图像里独立追踪到的关键点。';
      row.append(origin);
    }
    const frame=humanCurrentFrame(job), ref=activeReference();
    if (frame) {
      const detail=document.createElement('small'); detail.textContent=(frame.reference_id !== ref?.id ? '近邻采样：' : '来源：') +
        poseFrameLabel(frame,job.reference_name) + (frame.tracking_status === 'lost' ? ' · 未找到人物' : ''); row.append(detail);
    }
    const error=job.error || state.humanDetailErrors.get(job.job_id) || state.humanDetailErrors.get(humanPoseLoadKey(job,ref?.id));
    if (error) { const detail=document.createElement('small'); detail.className='human-pose-error'; detail.textContent=String(error); row.append(detail); }
    if (job.status === 'completed') {
      const actions=document.createElement('div'); actions.className='human-pose-job-actions';
      const selected=state.humanOverlayChoice === job.job_id || state.humanOverlayChoice === 'latest' && overlayJob?.job_id === job.job_id;
      const display=document.createElement('button'); display.type='button'; display.className='compact-button human-pose-display';
      display.textContent=selected ? '隐藏' : '显示'; display.setAttribute('aria-pressed',String(selected));
      display.title=selected ? '隐藏参考图上的人体结果' : '在参考图上只显示这份人体结果';
      display.addEventListener('click',() => setHumanOverlayChoice(selected ? 'hidden' : job.job_id)); actions.append(display);
      const cite=document.createElement('button'); cite.type='button'; cite.className='compact-button emphasis-button'; cite.textContent='引用人体';
      cite.disabled=!editable() || !frame; cite.title=frame ? '引用当前来源帧的' + poseEvidenceLabel(job) + '和骨架图' : '切到有结果的机位和帧后引用';
      cite.addEventListener('mousedown',event => event.preventDefault()); cite.addEventListener('click',() => insertHumanPoseReference(job,frame)); actions.append(cite);
      const names=state.humanDetails.get(job.job_id)?.keypoint_names || job.keypoint_names;
      if (state.poseCorrectionsSupported && handJointIndices(names,'left').length === 21 && handJointIndices(names,'right').length === 21) {
        const edit=document.createElement('button'); edit.type='button'; edit.className='compact-button human-pose-edit-action';
        edit.textContent='修正关键点'; edit.disabled=!editable() || frame?.reference_id !== activeReference()?.id || !frame?.image_sha256;
        edit.addEventListener('click',() => beginPoseEdit(job,frame)); actions.append(edit);
      }
      if (!frame) {
        const viewButton=document.createElement('button'); viewButton.type='button'; viewButton.className='compact-button'; viewButton.textContent='查看结果';
        viewButton.disabled=!editable() || state.humanDetailLoads.has(job.job_id);
        viewButton.addEventListener('click',() => viewHumanPose(job)); actions.append(viewButton);
      }
      const download=document.createElement('button'); download.type='button'; download.className='compact-button'; download.textContent='下载 JSON';
      download.addEventListener('click',() => downloadHumanPose(job)); actions.append(download);
      row.append(actions);
      for (const sample of state.poseEdits.filter((item) => item.job_id === job.job_id)) {
        const draft=document.createElement('div'); draft.className='human-pose-edit-draft';
        draft.append(document.createTextNode((sample.label || '手部修正') + ' · ' + sample.edits.length + ' 点 '));
        const open=document.createElement('button'); open.type='button'; open.className='text-button'; open.textContent='回看';
        open.disabled=!editable(); open.addEventListener('click',() => revisitPoseEdit(sample));
        const cite=document.createElement('button'); cite.type='button'; cite.className='text-button'; cite.textContent='引用修正';
        cite.disabled=!editable() || !state.poseCorrectionsSupported; cite.addEventListener('click',() => referencePoseEdit(sample));
        draft.append(open,cite); row.append(draft);
      }
    }
    ui.humanJobs.append(row);
  }
}
function humanJobContainsCurrentReference(job) {
  const ref=activeReference(), view=clipReference() ? referenceView() : null;
  if (humanAutomaticJob(job)) return job.source_kind === 'static_references'
    ? !!ref && job.view_ids?.includes(ref.id)
    : !!view && job.view_ids?.includes(view.clip_id);
  return job.multi_view ? job.view_ids?.includes(view?.clip_id)
    : job.view_id ? job.view_id === view?.clip_id
      : job.reference_id === ref?.id;
}
function drawHumanPoseOverlay() {
  const surface=prepareCanvas(ui.humanCanvas);
  if (!surface) return;
  const ref=activeReference();
  const job=humanOverlayJob();
  if (!referencePixelsReady() || !job) return;
  const frame=humanCurrentFrame(job), detail=state.humanDetails.get(job.job_id);
  // Historical runs can cover the same source image. Paint only the chosen
  // result, and keep the layer empty until that exact image's frame is loaded.
  if (!frame || frame.reference_id !== ref?.id) return;
  const color=POSE_COLORS[(humanJobNumber(job)-1)%POSE_COLORS.length];
  const sample=state.poseEdits.find((item) => item.job_id === job.job_id && item.reference_id === frame.reference_id);
  const corrected=correctedPoseFrame(frame,sample);
  drawPoseSkeleton(surface.context,corrected,surface.width,surface.height,{color,fontFamily:annotationFontFamily,threshold:job.confidence_threshold ?? 0.3,
    edges:detail.skeleton_edges, label:humanJobName(job) + ' · ' + poseEvidenceLabel(job) + ' · ' + poseFrameLabel(frame,job.reference_name)});
  drawPoseEditHandles(surface.context,corrected,surface.width,surface.height);
}
function poseEditorContext() {
  const editor=state.poseEditor;
  if (!editor) return null;
  const job=state.humanJobs.find((item) => item.job_id === editor.jobId);
  const detail=state.humanDetails.get(editor.jobId);
  const frame=detail?.frames?.find((item) => item.reference_id === editor.referenceId);
  const sample=state.poseEdits.find((item) => item.job_id === editor.jobId && item.reference_id === editor.referenceId);
  return {editor,job,detail,frame,sample,names:detail?.keypoint_names || job?.keypoint_names || []};
}
function renderPoseEditor() {
  const context=poseEditorContext();
  ui.poseEditPanel.classList.toggle('hidden',!context || !state.poseCorrectionsSupported);
  const active=!!context?.frame && context.editor.referenceId === activeReference()?.id && referencePixelsReady() &&
    editable() && state.poseCorrectionsSupported && !state.seeking;
  ui.humanCanvas.classList.toggle('pose-editing',active);
  if (!context) return;
  const {editor,frame,sample,names}=context;
  const indices=handJointIndices(names,editor.hand);
  const optionSignature=editor.hand + ':' + indices.map((index) => names[index]).join(',');
  if (ui.poseEditJoint.dataset.options !== optionSignature) {
    ui.poseEditJoint.replaceChildren();
    for (const index of indices) {
      const option=document.createElement('option'); option.value=names[index]; option.textContent=handJointLabel(names[index]);
      ui.poseEditJoint.append(option);
    }
    ui.poseEditJoint.dataset.options=optionSignature;
  }
  if (!indices.some((index) => names[index] === editor.jointName)) editor.jointName=names[indices[0]];
  ui.poseEditHand.value=editor.hand; ui.poseEditJoint.value=editor.jointName || '';
  ui.poseEditVisibility.value=editor.visibility;
  ui.poseEditSource.textContent=frame ? poseFrameLabel(frame) : sample?.label || '加载原结果…';
  const original=frame?.keypoints?.find((point) => point.name === editor.jointName);
  const edit=sample?.edits.find((point) => point.name === editor.jointName);
  const confidence=Number.isFinite(original?.score) ? ' · 原评分 ' + original.score.toFixed(2) +
    (original.score < (context.job?.confidence_threshold ?? .3) ? '（低置信）' : '') : '';
  ui.poseEditHint.textContent=!active ? '这份修正固定在原参考帧；切回该机位和帧后继续编辑。'
    : handJointLabel(editor.jointName) + confidence + (edit ? ' · 已人工修正' : ' · 原结果未修改') +
      '。点击定位或拖动关节；可缩放，空格拖动平移。遮挡/缺失不作为可见点。';
  for (const control of [ui.poseEditHand,ui.poseEditJoint,ui.poseEditVisibility]) control.disabled=!active;
  ui.poseEditReset.disabled=!active || !edit;
  ui.poseEditReference.disabled=!editable() || !sample?.edits.length || !state.poseCorrectionsSupported;
  ui.poseEditDownload.disabled=!sample?.edits.length || !state.poseCorrectionsSupported;
}
function drawPoseEditHandles(context,frame,width,height) {
  const current=poseEditorContext();
  if (!current || current.editor.referenceId !== activeReference()?.id || frame.reference_id !== current.editor.referenceId ||
      humanOverlayJob()?.job_id !== current.editor.jobId || !state.poseCorrectionsSupported) return;
  context.save();
  for (const index of handJointIndices(current.names,current.editor.hand)) {
    const point=frame.keypoints[index];
    if (!point || point.in_frame === false && !(point.manual_visibility === 'occluded' && point.manual_position) || point.manual_visibility === 'missing' ||
        point.manual_visibility === 'occluded' && !point.manual_position) continue;
    const x=point.x*width,y=point.y*height, selected=point.name === current.editor.jointName;
    context.strokeStyle=point.manual_source ? '#b65320' : point.score < (current.job.confidence_threshold ?? .3) ? '#77776f' :
      current.editor.hand === 'left' ? '#b65320' : '#6856aa';
    context.fillStyle=point.manual_visibility === 'visible' ? '#b65320' : '#f5f4ef';
    context.lineWidth=selected ? 2.5 : 1.5;
    context.beginPath(); context.arc(x,y,selected ? 7 : 4.5,0,Math.PI*2); context.fill(); context.stroke();
    if (selected) {
      context.font='12px ' + annotationFontFamily; context.fillStyle='#292925';
      const label=handJointLabel(point.name),left=clamp(x+10,4,Math.max(4,width-context.measureText(label).width-4));
      context.fillText(label,left,Math.max(15,y-10));
    }
  }
  context.restore();
}
function beginPoseEdit(job,frame) {
  if (!editable() || !state.poseCorrectionsSupported || frame?.reference_id !== activeReference()?.id || !frame?.image_sha256) return;
  pauseTimeline(); setMode('select');
  state.poseEditor={jobId:job.job_id,referenceId:frame.reference_id,hand:'left',jointName:null,visibility:'visible'};
  setHumanOverlayChoice(job.job_id);
  minimalLayout?.closeReferences(); drawOverlays(); saveDraft();
  announce('在左图点选或拖动手部关节；只修改这张来源帧。');
}
function poseEditSample(context,{create=false}={}) {
  if (context.sample) return context.sample;
  if (!create) return null;
  if (state.poseEdits.length >= 8) throw new Error('一条提示最多保留 8 帧关键点修正，请先发送或移除一份。');
  const {frame,job}=context;
  const sample={id:newId().replace(/-/g,''),job_id:job.job_id,reference_id:frame.reference_id,
    image_sha256:frame.image_sha256,image_orientation:frame.image_orientation,keypoint_profile:job.keypoint_profile,
    label:poseFrameLabel(frame),edits:[]};
  state.poseEdits.push(sample); return sample;
}
function updatePoseJoint(visibility,position=null) {
  const context=poseEditorContext();
  if (!context?.frame || context.editor.referenceId !== activeReference()?.id || !editable() || !state.poseCorrectionsSupported) return null;
  try {
    const sample=poseEditSample(context,{create:true});
    const edit={name:context.editor.jointName,visibility,...(visibility !== 'missing' && position ?
      {x:Math.min(visibility === 'visible' ? 1-1e-7 : 1,position.x),y:Math.min(visibility === 'visible' ? 1-1e-7 : 1,position.y)} : {})};
    const index=sample.edits.findIndex((item) => item.name === edit.name);
    if (index < 0) sample.edits.push(edit); else sample.edits[index]=edit;
    drawOverlays(); saveDraft(); return sample;
  } catch (error) { announce(error.message,true); return null; }
}
function referencePoseEdit(sample,{focus=true}={}) {
  if (!editable() || !sample?.edits.length || !state.poseCorrectionsSupported) return;
  const token=poseEditToken(sample.id);
  if (ui.note.value.includes(token)) { if (focus) { minimalLayout?.openChat(); ui.note.focus(); } return; }
  insertNoteText('手部修正（' + sample.label + '） ' + token,{replaceSelection:false,focus});
}
async function downloadPoseEdit(sample) {
  if (!sample?.edits.length || !state.poseCorrectionsSupported) return;
  try {
    const edits=collectPoseEdits(poseEditToken(sample.id),[sample]);
    const result=await api('/api/workspace/pose/corrections/export',{method:'POST',body:{session_id:state.sessionId,pose_edits:edits}});
    const correction=result.documents?.[0];
    if (!correction || correction.correction_id !== sample.id) throw new Error('修正导出与当前来源不匹配。');
    const url=URL.createObjectURL(new Blob([JSON.stringify(correction,null,2)],{type:'application/json'}));
    const link=document.createElement('a'); link.href=url; link.download='pose-correction-' + sample.id + '.json'; document.body.append(link);
    link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url),1000);
  } catch (error) { announce('修正导出失败：' + error.message,true); }
}
async function revisitPoseEdit(sample) {
  const job=state.humanJobs.find((item) => item.job_id === sample.job_id);
  if (!job || !editable()) return;
  const detail=await ensureHumanPoseDetail(job,{referenceId:sample.reference_id,retry:true});
  const frame=detail?.frames.find((item) => item.reference_id === sample.reference_id);
  if (!frame) return;
  const view=referenceViews().find((item) => item.frames.some((ref) => ref.id === frame.reference_id));
  if (view) await seekTimeline(frame.time_sec,{viewId:view.clip_id,preserveTime:true});
  else if (state.references.some((ref) => ref.id === frame.reference_id)) {
    pauseTimeline(); state.clipEnabled=false; state.activeReferenceId=frame.reference_id;
    showActiveReference(); renderReferenceStrip();
    if (referenceCamera(activeReference())) alignActiveReference(); else leaveReferenceCamera();
  } else { announce('原参考帧已从当前场景移除，修正仍保留且可下载。',true); return; }
  beginPoseEdit(job,frame);
}
function bindPoseEditEvents() {
  ui.poseEditHand.addEventListener('change',() => {
    if (!state.poseEditor) return;
    state.poseEditor.hand=ui.poseEditHand.value; state.poseEditor.jointName=null; state.poseEditor.visibility='visible';
    renderPoseEditor(); drawHumanPoseOverlay(); saveDraft();
  });
  ui.poseEditJoint.addEventListener('change',() => {
    const context=poseEditorContext(); if (!context) return;
    context.editor.jointName=ui.poseEditJoint.value;
    context.editor.placeSelected=true;
    context.editor.visibility=context.sample?.edits.find((point) => point.name === context.editor.jointName)?.visibility || 'visible';
    renderPoseEditor(); drawHumanPoseOverlay(); saveDraft();
  });
  ui.poseEditVisibility.addEventListener('change',() => {
    if (!state.poseEditor) return;
    state.poseEditor.visibility=ui.poseEditVisibility.value;
    if (state.poseEditor.visibility !== 'visible') {
      const sample=updatePoseJoint(state.poseEditor.visibility);
      if (sample) referencePoseEdit(sample,{focus:false});
    }
    renderPoseEditor(); saveDraft();
  });
  ui.poseEditReference.addEventListener('click',() => referencePoseEdit(poseEditorContext()?.sample));
  ui.poseEditDownload.addEventListener('click',() => downloadPoseEdit(poseEditorContext()?.sample));
  ui.poseEditFinish.addEventListener('click',() => { state.poseEditor=null; state.poseEditDrag=null; drawOverlays(); saveDraft(); });
  ui.poseEditReset.addEventListener('click',() => {
    const context=poseEditorContext(); if (!context?.sample || !editable()) return;
    const sample=context.sample; sample.edits=sample.edits.filter((point) => point.name !== context.editor.jointName);
    if (!sample.edits.length) {
      state.poseEdits=state.poseEdits.filter((item) => item.id !== sample.id);
      ui.note.value=ui.note.value.split(poseEditToken(sample.id)).join('');
    }
    context.editor.visibility='visible'; drawOverlays(); saveDraft();
  });
  ui.humanCanvas.addEventListener('pointerdown',(event) => {
    const context=poseEditorContext();
    if (!context?.frame || context.editor.referenceId !== activeReference()?.id || !editable() || !state.poseCorrectionsSupported ||
        state.spacePan || state.seeking || event.button !== 0) return;
    event.preventDefault(); event.stopPropagation();
    const point=pointFromPointer(event,ui.humanCanvas),rect=ui.humanCanvas.getBoundingClientRect();
    const effective=correctedPoseFrame(context.frame,context.sample);
    let closest=null,distance=12;
    for (const index of handJointIndices(context.names,context.editor.hand)) {
      const joint=effective.keypoints[index];
      if (joint?.in_frame === false && !(joint.manual_visibility === 'occluded' && joint.manual_position) || joint?.manual_visibility === 'missing') continue;
      const value=Math.hypot((joint.x-point.x)*rect.width,(joint.y-point.y)*rect.height);
      if (value < distance) { distance=value; closest=joint; }
    }
    if (closest && !context.editor.placeSelected) {
      context.editor.jointName=closest.name;
      context.editor.visibility=closest.manual_visibility || 'visible';
    }
    context.editor.placeSelected=false;
    if (context.editor.visibility === 'missing') { renderPoseEditor(); return; }
    ui.humanCanvas.setPointerCapture(event.pointerId);
    state.poseEditDrag={pointerId:event.pointerId,jobId:context.editor.jobId,referenceId:context.editor.referenceId};
    updatePoseJoint(context.editor.visibility,point);
  });
  ui.humanCanvas.addEventListener('pointermove',(event) => {
    const drag=state.poseEditDrag,context=poseEditorContext();
    if (!drag || drag.pointerId !== event.pointerId || drag.referenceId !== activeReference()?.id || context?.editor.jobId !== drag.jobId) return;
    event.preventDefault(); event.stopPropagation();
    updatePoseJoint(context.editor.visibility,pointFromPointer(event,ui.humanCanvas));
  });
  const finish=(event) => {
    if (state.poseEditDrag?.pointerId !== event.pointerId) return;
    event.stopPropagation(); state.poseEditDrag=null;
    const sample=poseEditorContext()?.sample;
    if (sample) referencePoseEdit(sample,{focus:false});
    saveDraft();
  };
  ui.humanCanvas.addEventListener('pointerup',finish);
  ui.humanCanvas.addEventListener('pointercancel',finish);
}
async function ensureHumanPoseDetail(job, {retry=false,referenceId=null,first=false}={}) {
  const cached=state.humanDetails.get(job.job_id);
  if (!humanAutomaticJob(job) && cached) return cached;
  if (referenceId && cached?.frames?.some((frame) => frame.reference_id === referenceId)) return cached;
  if (first && cached?.frames?.length) return cached;
  const key=referenceId ? humanPoseLoadKey(job,referenceId) : job.job_id + (first ? ':first' : '');
  if (state.humanDetailLoads.has(key) || (!retry && state.humanDetailErrors.has(key))) return null;
  state.humanDetailLoads.add(key); renderHumanPosePanel();
  try {
    const pageOffset=referenceId ? humanPosePageOffset(job,referenceId) : null;
    const query=humanAutomaticJob(job) ? referenceId ? pageOffset === null
      ? '?reference_id=' + encodeURIComponent(referenceId)
      : '?frame_offset=' + pageOffset + '&max_frames=32'
      : '?frame_offset=0&max_frames=1' : '';
    let detail=await api('/api/workspace/pose/' + encodeURIComponent(job.job_id) + query);
    if (detail.session_id !== state.sessionId || detail.status !== 'completed' || !Array.isArray(detail.frames)) throw new Error('人体结果与当前场景会话不匹配，请刷新重试。');
    if (referenceId && !detail.frames.some((frame) => frame.reference_id === referenceId)) {
      // The reference set may have changed since this job was created. Ask the
      // archive for the immutable source ID before deciding the frame is gone.
      detail=await api('/api/workspace/pose/' + encodeURIComponent(job.job_id) + '?reference_id=' + encodeURIComponent(referenceId));
    }
    if (referenceId && !detail.frames.some((frame) => frame.reference_id === referenceId)) throw new Error('该帧没有人体结果。');
    if (humanAutomaticJob(job)) {
      const latest=state.humanDetails.get(job.job_id);
      const retained=(latest?.frames || []).filter((frame) => !detail.frames.some((item) => item.reference_id === frame.reference_id));
      // Keep the active working set small even for all-camera, full-rate jobs.
      detail.frames=[...retained,...detail.frames].slice(-128);
    }
    state.humanDetails.set(job.job_id,detail); state.humanDetailErrors.delete(key); return detail;
  } catch (error) { state.humanDetailErrors.set(key,error.message); return null; }
  finally { state.humanDetailLoads.delete(key); renderHumanPosePanel(); drawHumanPoseOverlay(); promptMentions?.refresh(); }
}
async function loadHumanPoses() {
  if (!state.sessionId || state.humanLoading || state.navigatingProject) return;
  state.humanLoading=true;
  try {
    const result=await api('/api/workspace/pose?session_id=' + encodeURIComponent(state.sessionId));
    state.humanJobs=(Array.isArray(result.jobs) ? result.jobs : []).filter(job => job.session_id === state.sessionId && /^[0-9a-f]{32}$/.test(job.job_id || ''));
    if (state.humanError?.startsWith('人体结果连接失败：')) state.humanError=null;
  } catch (error) { state.humanError='人体结果连接失败：' + error.message; throw error; }
  finally { state.humanLoading=false; renderHumanPosePanel(); drawHumanPoseOverlay(); }
}
function insertHumanPoseReference(job, frame) {
  if (!editable() || !frame || job.session_id !== state.sessionId) return false;
  const ref={job_id:job.job_id,reference_id:frame.reference_id}, token=poseToken(ref.job_id,ref.reference_id);
  if (!token) return false;
  const existing=state.poseRefs.some(item => item.job_id === ref.job_id && item.reference_id === ref.reference_id);
  try {
    const cited=collectPoseReferences(ui.note.value,state.poseRefs);
    if (!cited.some(item => item.job_id === ref.job_id && item.reference_id === ref.reference_id) && cited.length >= 8) {
      announce('每轮最多引用 8 个人体结果。',true); return false;
    }
  } catch (error) { announce(error.message,true); return false; }
  if (insertNoteReference('@' + humanJobName(job) + '（' + poseEvidenceLabel(job) + ' · ' + poseFrameLabel(frame,job.reference_name) + '）',token)) {
    if (!existing) state.poseRefs.push(ref);
    saveDraft(); renderHumanPosePanel();
    return true;
  }
  return false;
}
async function viewHumanPose(job) {
  const detail=await ensureHumanPoseDetail(job,{retry:true,first:true});
  const selectedView=job.multi_view && job.view_ids?.includes(referenceView()?.clip_id) ? referenceView()?.clip_id : job.view_ids?.[0];
  const frame=detail?.frames?.find((item) => item.view_id === selectedView) || detail?.frames?.[0];
  if (!frame || !editable()) return;
  setHumanOverlayChoice(job.job_id);
  setMode('select');
  if (job.source_kind === 'static_references' || !job.view_id && !job.multi_view) {
    if (!state.references.some(ref => ref.id === frame.reference_id)) { announce('该人体结果的原参考图已被移除。',true); return; }
    pauseTimeline(); state.clipEnabled=false; state.activeReferenceId=frame.reference_id;
    renderReferenceStrip(); showActiveReference();
    if (referenceCamera(activeReference())) alignActiveReference(); else leaveReferenceCamera();
  } else {
    const viewId=frame.view_id || job.view_id;
    if (!referenceViews().some((view) => view.clip_id === viewId)) { announce('该人体结果的原机位已不在当前片段。',true); return; }
    await seekTimeline(frame.time_sec,{viewId,preserveTime:true});
  }
  minimalLayout?.closeReferences(); renderHumanPosePanel(); drawHumanPoseOverlay(); saveDraft();
}
async function downloadHumanPose(job) {
  let jsonText;
  try {
    const response=await fetch(resourceURL('/api/workspace/pose/' + encodeURIComponent(job.job_id) + (humanAutomaticJob(job) ? '?download=1' : '')));
    jsonText=await response.text();
    const detail=JSON.parse(jsonText);
    if (!response.ok) throw new Error(detail.error || detail.detail || 'HTTP ' + response.status);
    if (detail.job_id !== job.job_id || detail.session_id !== state.sessionId ||
        (!job.imported_external && detail.status !== 'completed') || !Array.isArray(detail.frames)) {
      throw new Error('下载结果与当前场景会话不匹配');
    }
  } catch (error) { announce('人体结果未能下载：' + error.message,true); return; }
  // Preserve the server's numeric representation, including 0.0. Rewriting
  // parsed JSON in JavaScript can change the immutable import digest.
  const blob=new Blob([jsonText],{type:'application/json;charset=utf-8'});
  const url=URL.createObjectURL(blob), link=document.createElement('a');
  link.href=url; link.download=humanJobName(job) + '_' + job.job_id.slice(0,8) + '.json';
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url),1000);
}
function bindHumanPoseEvents() {
  ui.humanLatest.addEventListener('click',() => setHumanOverlayChoice('latest'));
  ui.humanHideAll.addEventListener('click',() => setHumanOverlayChoice('hidden'));
  id('references-dialog-button').addEventListener('click',() => {
    renderHumanPosePanel(); loadHumanPoses().catch(() => {});
  });
  setInterval(() => {
    if (state.workspaceReady && !state.navigatingProject && !document.hidden) {
      loadHumanPoses().catch(() => {});
    }
  },5000);
}
function updateReferenceGeometry() {
  renderPromptDragControls();
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
  if (!state.sessionId || state.sessionStatus !== 'open' || state.creatingProject || state.navigatingProject) return;
  const images = [...files];
  if (!images.length) return;
  state.uploading = true;
  ui.submit.disabled = true;
  updateAnnotationHistory();
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
    updateAnnotationHistory();
    ui.referenceInput.value = '';
  }
}

function dynamicEnabled() { return !!state.referenceClip || state.animations.size > 0 || state.dynamicSnapshots.length > 0; }
function referenceViews() {
  return state.referenceClip ? [state.referenceClip, ...(state.referenceClip.views || [])] : [];
}
function referenceView(viewId=state.activeViewId) {
  return referenceViews().find((view) => view.clip_id === viewId) || state.referenceClip;
}
function referenceViewForMoment(moment) {
  return referenceViews().find((view) => view.clip_id === (moment.view_id || moment.clip_id));
}
function timelineViewId() {
  const staticView = !state.clipEnabled && viewForReferenceImage(referenceViews(), activeReference());
  return state.pendingViewId || staticView?.clip_id || state.activeViewId;
}
function viewFrameAtTime(view, time) {
  return referenceViews().length > 1 ? nearestFrameAtTime(view?.frames, time) : frameAtTime(view?.frames, time);
}
function renderReferenceViews() {
  const views = referenceViews();
  const signature = JSON.stringify(views.map((view) => [view.clip_id, view.name]));
  if (state.viewOptionsSignature !== signature) {
    state.viewOptionsSignature = signature;
    ui.viewSelect.replaceChildren();
    for (const view of views) {
      const option = document.createElement('option'); option.value = view.clip_id;
      option.textContent = view.name; ui.viewSelect.append(option);
    }
  }
  ui.viewControl.classList.toggle('hidden', views.length < 2);
  ui.viewSelect.value = state.pendingViewId || state.activeViewId || state.referenceClip?.clip_id || '';
  ui.viewSelect.disabled = !editable();
}
function timelineDuration() {
  return state.referenceClip ? Math.max(...referenceViews().map((view) => view.duration_sec))
    : Math.max(0, ...[...state.animations.values()].map((entry) => entry.clip.duration), ...state.dynamicSnapshots.map((entry) => entry.time_sec + 1 / timelineFps()));
}
function timelineFps() { return state.referenceClip?.fps || 30; }
function momentFrameIndex(moment) {
  if (!moment?.reference_frame_id) return null;
  if (moment.clip_id === state.referenceClip?.clip_id) {
    const index = referenceViewForMoment(moment)?.frames.findIndex((frame) => frame.id === moment.reference_frame_id) ?? -1;
    if (index >= 0) return index;
  }
  return Number.isInteger(moment.frame_index) && moment.frame_index >= 0 ? moment.frame_index : null;
}
function annotationFrameIndex(annotation) {
  const moment = state.dynamicSnapshots.find((entry) => entry.id === annotation.frame_id);
  if (moment) return momentFrameIndex(moment);
  return Number.isInteger(annotation.frame_index) && annotation.frame_index >= 0 ? annotation.frame_index : null;
}
function frameTimeLabel(time, index, viewName='') {
  if (!Number.isFinite(time)) return '';
  return (viewName ? '机位 ' + viewName + ' · ' : '') + (index !== null ? '片段第 ' + (index + 1) + ' 帧 · ' : '') + time.toFixed(3) + ' s';
}
function annotationTimeLabel(annotation) {
  const snapshot = state.sceneSnapshots.find(entry => entry.id === annotation.snapshot_id);
  if (snapshot) return snapshot.name;
  const moment = state.dynamicSnapshots.find((entry) => entry.id === annotation.frame_id);
  return frameTimeLabel(annotation.time_sec, annotationFrameIndex(annotation), annotation.view_name || moment?.view_name || referenceViewForMoment(annotation)?.name || '');
}
function cacheMomentFrameIndices() {
  let changed = false;
  state.dynamicSnapshots = state.dynamicSnapshots.map((moment) => {
    const index = momentFrameIndex(moment);
    const currentView = moment.reference_frame_id ? referenceViewForMoment(moment) : null;
    const view = currentView?.frames.some((frame) => frame.id === moment.reference_frame_id) ? currentView : null;
    if (index === null || (moment.frame_index === index && (!view || moment.view_id === view.clip_id && moment.view_name === view.name))) return moment;
    changed = true;
    return {...moment, frame_index:index, ...(view ? {view_id:view.clip_id, view_name:view.name} : {})};
  });
  if (changed) {
    state.snapshot = state.dynamicSnapshots.find((moment) => moment.id === state.snapshot?.id) || state.snapshot;
    // Moment IDs stay the same, but their newly recovered metadata must reach IDB.
    state.draftMomentSignature = null;
    saveMomentDraft();
  }
}
function clipReference() {
  return state.clipEnabled ? viewFrameAtTime(referenceView(), state.time) : null;
}
function registerAnimations(item, gltf) {
  if (!gltf.animations?.length) return;
  const choice = state.animationChoices[item.id];
  const clip = gltf.animations[choice?.index] || gltf.animations.find((entry) => entry.name === choice) || gltf.animations[0];
  const mixer = new THREE.AnimationMixer(gltf.scene);
  const action = mixer.clipAction(clip);
  action.setLoop(THREE.LoopOnce, 1); action.clampWhenFinished = true;
  const entry = {mixer, action, clip, clips:gltf.animations, root:gltf.scene};
  state.animations.set(item.id, entry);
  state.animationChoices[item.id] = {index:gltf.animations.indexOf(clip), name:clip.name};
  applyAnimationTime(state.time);
}
function applyAnimationTime(time) {
  for (const entry of state.animations.values()) {
    // reset is needed for backward seeks after a LoopOnce action reached its end.
    entry.action.reset().play();
    entry.mixer.setTime(clamp(time, 0, entry.clip.duration));
    entry.root.updateMatrixWorld(true);
    entry.root.traverse((node) => {
      if (node.isSkinnedMesh) {
        node.skeleton.update(); node.computeBoundingBox(); node.computeBoundingSphere();
      }
    });
  }
  if (selectionHelper && state.selectedId) {
    const node = resolveSceneNode(state.selectedSceneNode) || state.objectNodes.get(state.selectedId);
    if (node) selectionHelper.box.setFromObject(node, true);
  }
}
function renderAnimationChoices() {
  ui.animationChoices.replaceChildren();
  for (const [objectId, entry] of state.animations) {
    if (entry.clips.length < 2) continue;
    const label = document.createElement('label');
    label.textContent = (sceneObject(objectId)?.name || objectId) + ' 动作 ';
    const select = document.createElement('select');
    entry.clips.forEach((clip, index) => {
      const option = document.createElement('option'); option.value = String(index);
      option.textContent = clip.name || '动作 ' + (index + 1); option.selected = clip === entry.clip;
      select.append(option);
    });
    select.addEventListener('change', () => {
      if (!editable()) return;
      pauseTimeline(); entry.mixer.stopAllAction();
      entry.clip = entry.clips[Number(select.value)];
      entry.action = entry.mixer.clipAction(entry.clip);
      entry.action.setLoop(THREE.LoopOnce, 1); entry.action.clampWhenFinished = true;
      state.animationChoices[objectId] = {index:entry.clips.indexOf(entry.clip), name:entry.clip.name};
      seekTimeline(state.time, {forcePose:true}); saveDraft();
    });
    label.append(select); ui.animationChoices.append(label);
  }
}
function setReferenceClip(clip, {restore=false}={}) {
  const signature = clip ? JSON.stringify([clip.clip_id, clip.views_revision || 0,
    [clip, ...(clip.views || [])].map((view) => [view.clip_id, view.name, view.fps, view.duration_sec, view.frames.length])]) : null;
  if (state.referenceClipSignature === signature) return;
  const replaced = (state.referenceClip?.clip_id || null) !== (clip?.clip_id || null);
  const inspectedReference = !state.clipEnabled ? activeReference() : null;
  pauseTimeline();
  cacheMomentFrameIndices();
  if (replaced) annotationHistory.clear();
  updateAnnotationHistory();
  frameImages.clear();
  state.referenceClip = clip;
  state.referenceClipSignature = signature;
  if (!referenceViews().some((view) => view.clip_id === state.activeViewId)) state.activeViewId = clip?.clip_id || null;
  const inspectedView = !restore && viewForReferenceImage(referenceViews(), inspectedReference);
  if (inspectedView) state.activeViewId = inspectedView.clip_id;
  cacheMomentFrameIndices();
  // A newly published clip follows the timeline by default. Restore deliberate
  // static inspection on reload until the user next operates the timeline.
  if (clip?.frames?.length && !restore) state.clipEnabled = true;
  state.time = clamp(state.time, 0, timelineDuration());
  if (state.clipEnabled && clip?.frames.length && referenceViews().length === 1) state.time = frameAtTime(clip.frames, state.time).time_sec;
  applyAnimationTime(state.time);
  syncTimelineReference({forceAlign:!restore && state.alignmentExact});
  showActiveReference(); resizeScene(); renderReferenceStrip(); renderTimeline();
  prefetchTimelineFrames(state.time);
}
function syncTimelineReference({forceAlign=false}={}) {
  const ref = clipReference();
  if (!ref) return;
  const changed = state.activeReferenceId !== ref.id;
  const followCamera = forceAlign || state.alignmentExact || !state.alignedReferenceId;
  state.activeReferenceId = ref.id;
  if (changed || forceAlign) {
    if (referenceCamera(ref) && followCamera) alignActiveReference({showLive:false, persist:false});
    else {
      if (!referenceCamera(ref)) {
        state.alignedReferenceId = null; state.alignmentExact = false;
        resizeScene();
      }
      showActiveReference();
    }
    drawOverlays();
  }
}
function pauseTimeline() {
  state.playing = false; state.playbackStart = null;
  state.seekGeneration++; state.seeking = false;
  if (state.scrubRequest !== null) cancelAnimationFrame(state.scrubRequest);
  state.scrubRequest = null; state.timelineTarget = null; state.pendingViewId = null;
  ui.play.textContent = '播放';
}
function timelineOverlayUrl(ref, {forceAlign=false}={}) {
  const willAlign = referenceCamera(ref) && (forceAlign || state.alignmentExact || !state.alignedReferenceId);
  return (willAlign || state.alignedReferenceId === ref.id) && ref.alignment_image_url || ref.url;
}
function prefetchTimelineFrames(time, direction=1) {
  const frames = referenceView()?.frames;
  if (!frames?.length || !state.clipEnabled) { frameImages.prefetch([]); return; }
  const center = frames.indexOf(viewFrameAtTime(referenceView(), time));
  const urls = [];
  // Read ahead in the direction of travel, while keeping a few reverse steps warm.
  for (let offset = 1; offset <= 8; offset++) {
    for (const index of [center + direction * offset, ...(offset <= 3 ? [center - direction * offset] : [])]) {
      const ref = frames[index];
      if (ref) urls.push(ref.url, timelineOverlayUrl(ref));
    }
  }
  frameImages.prefetch(urls);
}
function scheduleTimelineDraft() {
  clearTimeout(state.timelineSaveTimer);
  state.timelineSaveTimer = setTimeout(() => saveDraft(), 180);
}
function scrubTimeline(time, {final=false}={}) {
  if (!editable()) return;
  const viewId = timelineViewId();
  pauseTimeline();
  state.timelineTarget = clamp(time, 0, timelineDuration());
  state.pendingViewId = viewId !== state.activeViewId ? viewId : null;
  renderTimeline({moments:false});
  if (final) { seekTimeline(state.timelineTarget, {viewId}); return; }
  state.scrubRequest = requestAnimationFrame(() => {
    state.scrubRequest = null;
    seekTimeline(state.timelineTarget, {viewId});
  });
}
function stepReferenceTimeline(direction) {
  const view = referenceView(timelineViewId());
  if (referenceViews().length > 1 && view?.frames?.length) {
    const index = view.frames.indexOf(viewFrameAtTime(view, state.time));
    const next = index + direction;
    if (next < 0 || next >= view.frames.length) return;
    seekTimeline(view.frames[next].time_sec, {viewId:view.clip_id});
  } else seekTimeline(stepTime(view?.frames, state.time, direction, view?.fps || timelineFps(), timelineDuration()), {viewId:view?.clip_id});
}
async function seekTimeline(time, {playback=false, forcePose=false, viewId=timelineViewId(), preserveTime=false}={}) {
  if (!playback && !editable()) return false;
  if (playback && state.seeking) return false;
  if (!playback) { pauseTimeline(); hideTextEditor(); state.drag = null; }
  const generation = ++state.seekGeneration;
  const wanted = clamp(time, 0, timelineDuration());
  // Timeline actions always follow GT, including after inspecting a static image.
  const view = referenceView(viewId);
  const switchingView = !!view && view.clip_id !== state.activeViewId;
  const ref = viewFrameAtTime(view, wanted);
  const next = ref && referenceViews().length === 1 && !preserveTime ? ref.time_sec : wanted;
  const overlayUrl = ref ? timelineOverlayUrl(ref, {forceAlign:switchingView}) : null;
  const imagesReady = !ref || (imageReadyAtUrl(ui.referenceImage, ref.url) &&
    imageReadyAtUrl(ui.compareImage, overlayUrl));
  if (playback && Math.abs(state.time - next) < 1e-7 && imagesReady &&
      state.sceneView === 'live' && (!ref || state.clipEnabled)) return true;
  if (!playback) {
    state.timelineTarget = next;
    state.pendingViewId = switchingView ? view.clip_id : null;
    renderTimeline({moments:false});
  }
  try {
    if (ref && (state.activeReferenceId !== ref.id || !imagesReady)) {
      state.seeking = true;
      const [sourceImage, sourceOverlay] = await frameImages.prepare([ref.url, overlayUrl]);
      if (generation !== state.seekGeneration) return false;
      // Keep cached images separate from DOM nodes, whose src may later change
      // when the user opens a static reference.
      const image = sourceImage.cloneNode();
      const overlay = sourceOverlay.cloneNode();
      await Promise.all([image, overlay].map((entry) => entry.decode ? entry.decode() : Promise.resolve()));
      if (generation !== state.seekGeneration) return false;
      // Commit decoded reference pixels and geometry together, so slow image
      // loading cannot leave a newer pose next to an older reference frame.
      installTimelineImages(image, overlay);
    } else if (ref) frameImages.retain([ref.url, overlayUrl]);
    if (generation !== state.seekGeneration) return false;
    const resumedReference = !!ref && !state.clipEnabled;
    if (resumedReference) state.clipEnabled = true;
    const previousTime = state.time;
    const changed = Math.abs(state.time - next) > 1e-7;
    const leavingSnapshot = state.sceneView === 'snapshot';
    state.time = next;
    if (view) state.activeViewId = view.clip_id;
    if (changed || forcePose) applyAnimationTime(next);
    syncTimelineReference({forceAlign:switchingView});
    if (resumedReference || switchingView) renderReferenceStrip();
    if (leavingSnapshot) { state.sceneView = 'live'; renderSceneView({persist:false}); }
    if (changed || !playback) updateReferenceGeometry();
    state.timelineTarget = null; state.pendingViewId = null;
    renderTimeline({moments:leavingSnapshot});
    if (!playback) scheduleTimelineDraft();
    if (changed || !playback) prefetchTimelineFrames(next, next < previousTime ? -1 : 1);
    return true;
  } catch (error) {
    if (generation !== state.seekGeneration) return false;
    pauseTimeline(); renderTimeline({moments:false});
    announce('参考帧无法加载：' + error.message, true);
    return false;
  } finally {
    if (generation === state.seekGeneration) {
      state.seeking = false;
      state.timelineTarget = null; state.pendingViewId = null;
      renderTimeline({moments:false});
      drawOverlays();
    }
  }
}

function advanceTimeline(timestamp) {
  if (!state.playing || !Number.isFinite(timestamp)) return;
  if (!state.playbackStart) state.playbackStart = {timestamp, time:state.time};
  const next = state.playbackStart.time + (timestamp - state.playbackStart.timestamp) / 1000;
  seekTimeline(next, {playback:true});
  if (next >= timelineDuration() && !state.seeking) { pauseTimeline(); saveDraft(); }
}
function renderTimeline({moments=true}={}) {
  renderPromptDragControls();
  renderPoseEditor();
  const enabled = dynamicEnabled();
  ui.timeline.classList.toggle('hidden', !enabled);
  const duration = timelineDuration();
  const displayedTime = state.timelineTarget ?? state.time;
  ui.seek.max = String(duration || 1); ui.seek.value = String(displayedTime);
  ui.time.textContent = displayedTime.toFixed(3) + ' / ' + duration.toFixed(3) + ' s' + (state.timelineTarget !== null ? ' · 更新中' : '');
  ui.play.textContent = state.playing ? '暂停' : '播放';
  ui.timelineSource.textContent = state.referenceClip
    ? referenceView().name + ' · ' + referenceView().frames.length + ' 帧 · ' + referenceView().fps + ' fps · ' + (state.clipEnabled ? '同步参考帧' : '正在看静态参考')
    : 'GLB 动画 · ' + timelineFps() + ' fps';
  ui.range.classList.toggle('hidden', ui.scope.value !== 'range');
  for (const element of [ui.play, ui.seek, id('timeline-prev'), id('timeline-next'), id('save-moment'), ui.scope, ui.rangeStart, ui.rangeEnd, ui.clipInput]) {
    element.disabled = !editable() || (element !== ui.clipInput && duration <= 0);
  }
  renderReferenceViews();
  if (!moments) return;
  ui.moments.replaceChildren();
  if (!state.dynamicSnapshots.length) {
    const hint = document.createElement('span'); hint.className = 'muted';
    hint.textContent = '圈画时自动保留时刻；也可先点「保留此刻」。最多 8 个。'; ui.moments.append(hint);
  }
  for (const moment of state.dynamicSnapshots) {
    const card = document.createElement('div');
    card.className = 'moment-card' + (state.snapshot?.id === moment.id && state.sceneView === 'snapshot' ? ' active' : '');
    const button = document.createElement('button'); button.type = 'button';
    const count = state.annotations.filter((mark) => markMatchesMoment(mark, moment)).length;
    button.textContent = frameTimeLabel(moment.time_sec, momentFrameIndex(moment), moment.view_name || referenceViewForMoment(moment)?.name || '') + ' · v' + moment.scene_revision + ' · ' + count + ' 标记';
    button.addEventListener('click', () => openMoment(moment.id));
    const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '×';
    remove.title = '移除此刻和它的标记'; remove.disabled = !editable();
    remove.addEventListener('click', () => {
      if (!editable()) return;
      const before = annotationEditState();
      state.annotations = state.annotations.filter((mark) => !markMatchesMoment(mark, moment));
      state.dynamicSnapshots = state.dynamicSnapshots.filter((entry) => entry.id !== moment.id);
      if (state.snapshot?.id === moment.id) { state.snapshot = null; state.sceneView = 'live'; }
      recordAnnotationEdit(before);
      renderSceneView(); renderAnnotations(); renderTimeline(); saveDraft();
    });
    card.append(button, remove); ui.moments.append(card);
  }
}
function referencePixelsReady() {
  const ref = activeReference();
  return !ref || imageReadyAtUrl(ui.referenceImage, ref.url);
}
function ensureDynamicMoment({showSnapshot=false}={}) {
  if (!editable()) return null;
  if (state.seeking || state.pendingViewId) { announce('参考帧正在加载，请稍后再标注。', true); return null; }
  pauseTimeline();
  if (!referencePixelsReady()) { announce('参考帧正在加载，请稍后再标注。', true); return null; }
  const matching = state.dynamicSnapshots.find((entry) => entry.id === state.snapshot?.id &&
    entry.clip_id === (state.referenceClip?.clip_id || null) && entry.reference_id === (activeReference()?.id || null) &&
    Math.abs(entry.time_sec - state.time) < 1e-6 &&
    (state.sceneView === 'snapshot' || (entry.scene_revision === state.sceneRevision && comparisonMatchesLive(entry) &&
      JSON.stringify(entry.camera) === JSON.stringify(cameraData()) &&
      JSON.stringify(entry.selected_scene_nodes) === JSON.stringify(state.selectedSceneNode ? [state.selectedSceneNode] : []) &&
      JSON.stringify(entry.selected_object_ids) === JSON.stringify(state.selectedId ? [state.selectedId] : []))));
  if (matching) {
    if (showSnapshot) { state.sceneView = 'snapshot'; renderSceneView(); }
    return matching;
  }
  if (state.dynamicSnapshots.length >= 8) { announce('已保留 8 个时刻，请先移除一个再圈画。', true); return null; }
  const ref = activeReference();
  const comparison = captureSnapshotComparison();
  if (comparison === false) return null;
  settleOrbit();
  const selectedShot = captureLiveScene();
  const priorVisibility = feedbackLayer.visible;
  feedbackLayer.visible = false;
  const shot = captureLiveScene({includeSize:true});
  feedbackLayer.visible = priorVisibility;
  const moment = {id:newId(), data_url:shot.data_url, selected_data_url:selectedShot, comparison,
    image_width:shot.width, image_height:shot.height,
    time_sec:state.time, clip_id:state.referenceClip?.clip_id || null,
    scene_revision:state.sceneRevision, camera:cameraData(),
    reference_frame_id:clipReference()?.id || null,
    reference_id:ref?.id || null, reference_url:ref?.url || null,
    selected_object_ids:state.selectedId ? [state.selectedId] : [],
    selected_scene_nodes:state.selectedSceneNode ? [{...state.selectedSceneNode}] : [],
    animation_clips:[...state.animations].map(([object_id, entry]) => ({object_id, name:entry.clip.name.slice(0,160), index:entry.clips.indexOf(entry.clip)}))};
  if (clipReference()) {
    moment.view_id = referenceView().clip_id;
    moment.view_name = referenceView().name;
    moment.reference_time_sec = clipReference().time_sec;
  }
  const frameIndex = momentFrameIndex(moment);
  if (frameIndex !== null) moment.frame_index = frameIndex;
  state.dynamicSnapshots.push(moment); state.snapshot = moment;
  if (showSnapshot) state.sceneView = 'snapshot';
  renderSceneView(); renderTimeline(); saveDraft();
  return moment;
}
async function openMoment(momentId) {
  if (!editable()) return;
  const moment = state.dynamicSnapshots.find((entry) => entry.id === momentId);
  if (!moment) return;
  pauseTimeline(); hideTextEditor(); state.drag = null;
  if (moment.reference_frame_id) {
    const view = referenceViewForMoment(moment);
    if (!view?.frames.some((frame) => frame.id === moment.reference_frame_id)) {
      announce('此截图的机位已替换，请先删除该旧时刻再发送。', true); return;
    }
    if (!await seekTimeline(moment.time_sec, {viewId:view.clip_id, preserveTime:true}) || !editable()) return;
  } else {
    state.time = moment.time_sec; applyAnimationTime(state.time);
    state.clipEnabled = false;
    state.activeReferenceId = moment.reference_id; showActiveReference();
  }
  state.snapshot = moment; state.sceneView = 'snapshot';
  renderSceneView(); renderTimeline(); drawOverlays(); saveDraft();
}
async function uploadClip(files) {
  if (!editable() || !files.length) return;
  if (!state.referenceClip && state.dynamicSnapshots.length) {
    announce('请先发送或清空当前时刻标注，再添加第一段动态参考。', true); return;
  }
  const fps = Number(ui.clipFps.value);
  if (!Number.isFinite(fps) || fps < 0.1 || fps > 120) { announce('帧率需在 0.1–120 之间。', true); return; }
  const ordered = [...files].sort((a,b) => a.name.localeCompare(b.name, undefined, {numeric:true}));
  const isVideo = (file) => file.type.startsWith('video/') || /\.(mp4|webm|mov)$/i.test(file.name);
  const videos = ordered.every(isVideo);
  if (!videos && ordered.some(isVideo)) { announce('视频与图片序列请分开导入。多个视频会各自添加一个机位。', true); return; }
  const count = videos ? ordered.length : 1;
  if (referenceViews().length + count > 8) { announce('最多 8 个机位，请减少本次导入的机位数量。', true); return; }
  if (videos ? ordered.some((file) => file.size > 40 * 1024 * 1024)
      : ordered.length > 600 || ordered.reduce((sum, file) => sum + file.size, 0) > 40 * 1024 * 1024) {
    announce('每个机位最多 600 帧；单个视频或一组图片序列最多 40 MiB。', true); return;
  }
  const name = ui.clipName.value.trim();
  pauseTimeline(); state.uploading = true; renderTimeline(); updateSubmitLabel();
  let added = 0, latestViewId = null, failure = null;
  try {
    const health = await api('/api/health');
    if (!health.reference_multiview) throw new Error('机位功能正在更新，请稍后刷新工作台再导入');
    const imports = videos ? ordered.map((file, index) => ({files:[file], video:true,
      name:name ? name + (ordered.length > 1 ? ' · ' + (index + 1) : '') : file.name}))
      : [{files:ordered, video:false, name:name || ordered[0].name + ' · 帧序列'}];
    for (const entry of imports) {
      ui.clipStatus.textContent = '正在添加机位 ' + (added + 1) + ' / ' + count + '…';
      const body = {name:[...entry.name].slice(0,180).join(''), fps, append_view:true};
      if (entry.video) body.video_data_url = await readFile(entry.files[0]);
      else body.frames = await Promise.all(entry.files.map(async (file,index) => {
        if (!['image/png','image/jpeg'].includes(file.type)) throw new Error('序列请使用 PNG / JPEG；视频请单独选择。');
        return {name:file.name, time_sec:index / fps, data_url:await readFile(file)};
      }));
      const known = new Set(referenceViews().map((view) => view.clip_id));
      const result = await api('/api/sessions/' + encodeURIComponent(state.sessionId) + '/clip', {method:'POST', body});
      setReferenceClip(result.reference_clip || result);
      latestViewId = referenceViews().find((view) => !known.has(view.clip_id))?.clip_id || latestViewId;
      added++;
    }
    ui.clipName.value = '';
    ui.rangeStart.value = '0'; ui.rangeEnd.value = String(timelineDuration());
  } catch (error) { failure = error.message; }
  finally {
    state.uploading = false; ui.clipInput.value = ''; ui.clipStatus.textContent = '';
    renderTimeline(); updateSubmitLabel();
  }
  if (latestViewId && editable()) await seekTimeline(state.time, {viewId:latestViewId, preserveTime:true});
  saveDraft();
  if (failure) announce((added ? '已添加 ' + added + ' 个机位。' : '') + '添加失败：' + failure + '；原有机位和标记保留。', true);
  else announce('已添加 ' + added + ' 个机位，可在左侧切换；当前时间和标记保留。');
}

function bindTimelineEvents() {
  ui.clipInput.addEventListener('change', () => uploadClip(ui.clipInput.files));
  ui.play.addEventListener('click', async () => {
    if (!editable()) return;
    if (state.playing) { pauseTimeline(); saveDraft(); }
    else {
      setMode('select'); hideTextEditor();
      const startTime = state.time >= timelineDuration() - 1 / timelineFps() ? 0 : state.time;
      // Decode the starting GT frame before anchoring the playback clock.
      if (!await seekTimeline(startTime) || !editable()) return;
      state.playing = true; state.playbackStart = null;
    }
    renderTimeline(); drawOverlays();
  });
  ui.seek.addEventListener('input', () => scrubTimeline(Number(ui.seek.value)));
  ui.seek.addEventListener('change', () => scrubTimeline(Number(ui.seek.value), {final:true}));
  ui.viewSelect.addEventListener('change', () => seekTimeline(state.timelineTarget ?? state.time, {viewId:ui.viewSelect.value, preserveTime:true}));
  id('timeline-prev').addEventListener('click', () => stepReferenceTimeline(-1));
  id('timeline-next').addEventListener('click', () => stepReferenceTimeline(1));
  id('save-moment').addEventListener('click', () => ensureDynamicMoment({showSnapshot:true}));
  ui.scope.addEventListener('change', () => { renderTimeline(); saveDraft(); });
  for (const input of [ui.rangeStart, ui.rangeEnd]) input.addEventListener('change', saveDraft);
}
function momentDatabase() {
  return new Promise((resolve,reject) => {
    const request = indexedDB.open('astra-visual-moments', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('drafts');
    request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
  });
}
function saveImageReferenceDraft() {
  if (!state.sessionId) return;
  const signature = state.imageRefs.map((entry) => entry.id).join(',');
  if (signature === state.draftImageSignature) return;
  state.draftImageSignature = signature;
  const sessionId = state.sessionId;
  promptImageStore.save(sessionId, state.imageRefs).catch(() => {
    if (state.sessionId !== sessionId) return;
    state.draftImageSignature = null;
    if (state.imageRefs.length) announce('图片草稿无法保存到浏览器，刷新前请先发送或保留当前页面。', true);
  });
}
async function restoreImageReferenceDraft() {
  try {
    const images = await promptImageStore.load(state.sessionId);
    state.imageRefs = images.filter((item) => state.restoredImageRefIds.includes(item.id));
    state.draftImageSignature = state.imageRefs.map((item) => item.id).join(',');
  } catch { /* Missing image references are reported before sending, never silently omitted. */ }
  renderPromptImageReferences();
}
function saveMomentDraft() {
  if (!state.sessionId) return;
  const signature = JSON.stringify([state.dynamicSnapshots, state.sceneSnapshots].map(entries => entries.map(entry =>
    [entry.id, entry.comparison?.enabled, entry.comparison?.opacity, entry.comparison?.lastPositive])));
  if (signature === state.draftMomentSignature) return;
  state.draftMomentSignature = signature;
  const sessionId = state.sessionId, moments = {dynamicSnapshots:[...state.dynamicSnapshots], sceneSnapshots:[...state.sceneSnapshots]};
  // Serial writes prevent an earlier async save from replacing a newer collection.
  saveMomentDraft.pending = (saveMomentDraft.pending || Promise.resolve()).catch(() => {}).then(async () => {
    const db = await momentDatabase();
    try {
      await new Promise((resolve,reject) => {
        const tx = db.transaction('drafts', 'readwrite'); tx.objectStore('drafts').put(moments, sessionId);
        tx.oncomplete = resolve; tx.onerror = () => reject(tx.error); tx.onabort = () => reject(tx.error);
      });
    } finally { db.close(); }
  }).catch(() => { state.draftMomentSignature = null; });
}
async function restoreMomentDraft() {
  try {
    const db = await momentDatabase();
    const moments = await new Promise((resolve,reject) => {
      const request = db.transaction('drafts').objectStore('drafts').get(state.sessionId);
      request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
    }); db.close();
    const dynamic = Array.isArray(moments) ? moments : moments?.dynamicSnapshots;
    state.dynamicSnapshots = Array.isArray(dynamic) ? dynamic.filter((entry) => entry?.id && Number.isFinite(entry.time_sec) && entry.data_url?.startsWith('data:image/')).slice(0,8) : [];
    if (state.snapshot?.time_sec !== undefined && !state.dynamicSnapshots.some((entry) => entry.id === state.snapshot.id)) state.dynamicSnapshots = [...state.dynamicSnapshots.slice(0,7), state.snapshot];
    state.sceneSnapshots = (Array.isArray(moments?.sceneSnapshots) ? moments.sceneSnapshots : []).filter(entry => entry?.id && Number.isInteger(entry.scene_revision) && entry.data_url?.startsWith('data:image/')).slice(0,8);
    if (state.snapshot && state.snapshot.time_sec === undefined && !state.sceneSnapshots.some(entry => entry.id === state.snapshot.id)) {
      state.snapshot = {...state.snapshot, name:state.snapshot.name || '截图 1', number:state.snapshot.number || 1};
      state.sceneSnapshots = [...state.sceneSnapshots.slice(0,7), state.snapshot];
    }
    // The active draft is saved synchronously. It may be newer than an IDB
    // write interrupted by reload, so reconcile it into the saved collection.
    if (state.snapshot) {
      for (const key of ['sceneSnapshots', 'dynamicSnapshots']) {
        state[key] = state[key].map(entry => entry.id === state.snapshot.id ? state.snapshot : entry);
      }
    }
    state.draftMomentSignature = null;
    saveMomentDraft();
  } catch {
    if (state.snapshot && state.snapshot.time_sec === undefined) {
      state.snapshot = {...state.snapshot, name:state.snapshot.name || '截图 1', number:state.snapshot.number || 1};
      state.sceneSnapshots = [state.snapshot];
    }
  }
}
async function captureDynamicFrames() {
  const entries = [];
  for (const moment of state.dynamicSnapshots) {
    const sceneBundle = await captureScene(moment);
    const entry = {id:moment.id, time_sec:moment.time_sec, scene_revision:moment.scene_revision,
      reference_frame_id:moment.reference_frame_id, static_reference_id:moment.reference_frame_id ? null : moment.reference_id, camera:moment.camera,
      selected_object_ids:moment.selected_object_ids, selected_scene_nodes:moment.selected_scene_nodes,
      animation_clips:moment.animation_clips, ...sceneBundle};
    if (moment.reference_frame_id) {
      entry.view_id = moment.view_id || referenceViewForMoment(moment)?.clip_id;
      entry.view_name = moment.view_name || referenceViewForMoment(moment)?.name;
      if (Number.isFinite(moment.reference_time_sec)) entry.reference_time_sec = moment.reference_time_sec;
    }
    const frameIndex = momentFrameIndex(moment);
    if (frameIndex !== null) entry.frame_index = frameIndex;
    if (moment.reference_url) {
      const image = await loadImage(moment.reference_url);
      const canvas = scaledCanvas(image.naturalWidth, image.naturalHeight);
      const context = canvas.getContext('2d'); context.drawImage(image, 0, 0, canvas.width, canvas.height);
      entry.reference_original_data_url = canvas.toDataURL('image/jpeg', 0.88);
      for (const mark of state.annotations) {
        if (mark.pane === 'reference' && markMatchesMoment(mark, moment)) drawAnnotation(context, mark, canvas.width, canvas.height);
      }
      entry.reference_annotated_data_url = canvas.toDataURL('image/jpeg', 0.84);
    }
    entries.push(entry);
  }
  return entries;
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
  if (state.sceneLoading) { ui.sceneHint.textContent = '新场景正在加载，完成后可继续标注和发送。'; return; }
  if (state.mode === 'erase') {
    ui.sceneHint.textContent = state.sceneView === 'snapshot' ? '划过标记擦除整条 · 可撤销' : '先选择一张截图，再擦除标记';
    return;
  }
  if (state.sceneView === 'live') {
    ui.sceneHint.textContent = state.mode === 'select'
      ? (controls.freeRotation ? '自由旋转 · ' : '') + '左拖旋转 · 右拖平移 · 滚轮缩放 · 双击聚焦'
      : '直接在渲染图上圈画，自动保留当前截图';
  } else if (state.mode === 'select') {
    const selected = selectedAnnotation();
    ui.sceneHint.textContent = selected?.pane === 'scene' ? selected.name + ' · Backspace 删除 · 拖到会话引用' : '点击标记选中 · 拖到会话引用 · 3D 浏览可旋转场景';
  } else {
    ui.sceneHint.textContent = '在场景上' +
      ({point:'点一下',rectangle:'拖动框选',line:'拖动画线',arrow:'拖动画箭头',text:'点击加文字',freehand:'随手圈画'})[state.mode] +
      ' · 标记绑定当前截图';
  }
}
function updateMode() {
  updateAnnotationHistory();
  renderPromptDragControls();
  ui.frame.disabled = !editable() || !state.selectedId;
  ui.resetView.disabled = !editable();
  for (const button of [ui.savedSnapshot, ui.browse, ui.snapshotButton]) button.disabled = !editable();
  ui.captureScene.disabled = !editable() || state.sceneView !== 'live';
  renderSceneSnapshots();
  document.body.dataset.tool = state.mode;
  immersiveWorkspace?.syncState();
  minimalLayout?.refresh();
  document.querySelectorAll('.tool-button').forEach((button) => button.classList.toggle('active', button.dataset.tool === state.mode));
  const drawing = state.mode !== 'select' && editable();
  ui.referenceCanvas.style.pointerEvents = editable() ? 'auto' : 'none';
  ui.sceneCanvas.style.pointerEvents = editable() && state.sceneView === 'snapshot' ? 'auto' : 'none';
  const cursor = drawing ? (state.mode === 'erase' ? '' : 'crosshair') : 'pointer';
  ui.referenceCanvas.style.cursor = cursor;
  ui.sceneCanvas.style.cursor = cursor;
  renderer.domElement.style.cursor = drawing && state.sceneView === 'live' ? (state.mode === 'erase' ? 'not-allowed' : 'crosshair') : '';
  updateAnnotationSelectionHint();
  controls.enabled = editable() && state.mode === 'select' && state.sceneView === 'live';
  id('camera-navigation').classList.toggle('hidden', state.sceneView !== 'live' || state.mode !== 'select');
  id('camera-navigation').querySelectorAll('button').forEach(button => button.disabled = !controls.enabled);
  id('ground-axis').disabled = !controls.enabled;
  id('free-rotation').disabled = id('upright-camera').disabled = !controls.enabled;
  id('free-rotation').setAttribute('aria-pressed', String(controls.freeRotation));
  ui.referenceHint.classList.toggle('hidden', !activeReference());
  updateSceneHint();
  state.drag = null;
  drawOverlays();
}
function setMode(mode) {
  if (state.submitting || state.pendingSubmission) return;
  if (!['select','point','rectangle','line','arrow','text','freehand','erase'].includes(mode)) return;
  state.mode = mode;
  if (mode !== 'select') { state.poseEditor=null; state.poseEditDrag=null; }
  if (mode !== 'select') pauseTimeline();
  hideTextEditor();
  finishAnnotationReferenceDrag();
  if (mode !== 'select') state.selectedAnnotationId = null;
  updateMode(); saveDraft();
}
function browseScene() {
  if (!editable()) return;
  state.sceneView = 'live'; state.selectedAnnotationId = null;
  setMode('select'); renderSceneView();
}
function updateSelectionLevelControls() {
  document.querySelectorAll('[data-selection-level]').forEach((button) => {
    const active = button.dataset.selectionLevel === state.selectionLevel;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
  id('selection-level-hint').textContent = state.selectionLevel === 'item'
    ? '物品：点击 GLB 的顶层物品；基本图元整体选中。'
    : '部件：点击 GLB 的具体节点；基本图元整体选中。';
}
function setSelectionLevel(level) {
  if (state.submitting || state.pendingSubmission) return;
  if (!['item','part'].includes(level)) return;
  state.selectionLevel = level;
  updateSelectionLevelControls();
  const detail = resolveSceneNode(state.lastPickedDetailNode || state.selectedSceneNode);
  if (detail && state.selectedId) {
    state.selectedSceneNode = nodeReference(state.selectedId, detail, level);
    renderSelection();
  }
  browseScene();
  updateSceneHint();
  saveDraft();
}
function pointFromPointer(event, canvas) {
  const rect = canvas.getBoundingClientRect();
  return {
    x:clamp((event.clientX - rect.left) / rect.width, 0, 1),
    y:clamp((event.clientY - rect.top) / rect.height, 0, 1)
  };
}
function addAnnotation(annotation) {
  const before = annotationEditState();
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
  if (dynamicEnabled() && state.snapshot?.time_sec !== undefined) {
    item.frame_id = state.snapshot.id;
    item.time_sec = state.snapshot.time_sec;
    item.clip_id = state.snapshot.clip_id;
    if (state.snapshot.view_id) {
      item.view_id = state.snapshot.view_id;
      item.view_name = state.snapshot.view_name;
    }
    item.scene_revision = state.snapshot.scene_revision;
    const frameIndex = momentFrameIndex(state.snapshot);
    if (frameIndex !== null) item.frame_index = frameIndex;
  }
  state.annotations.push(item);
  ensureAnnotationNames();
  recordAnnotationEdit(before);
  renderAnnotations();
  drawOverlays();
  saveDraft();
}
function prepareSceneAnnotation() {
  if (state.sceneView === 'snapshot' && state.snapshot) return true;
  if (dynamicEnabled()) return !!ensureDynamicMoment({showSnapshot:true});
  settleOrbit();
  const snapshot = state.snapshot;
  const reusable = snapshot && comparisonMatchesLive(snapshot) && snapshot.scene_revision === state.sceneRevision &&
    JSON.stringify(snapshot.camera) === JSON.stringify(cameraData()) &&
    JSON.stringify(snapshot.selected_object_ids) === JSON.stringify(state.selectedId ? [state.selectedId] : []) &&
    JSON.stringify(snapshot.selected_scene_nodes) === JSON.stringify(state.selectedSceneNode ? [state.selectedSceneNode] : []);
  if (reusable) { state.sceneView = 'snapshot'; renderSceneView(); return true; }
  // A different camera gets its own screenshot; previous views and marks stay intact.
  freezeScene();
  return state.sceneView === 'snapshot' && !!state.snapshot;
}
function annotationVisibleInPane(annotation, pane) {
  if (annotation.pane !== pane) return false;
  if (pane === 'scene') return state.sceneView === 'snapshot' && annotation.snapshot_id === state.snapshot?.id;
  if (annotation.reference_image_id !== state.activeReferenceId) return false;
  return !annotation.frame_id || (!state.playing && annotation.frame_id === state.snapshot?.id && Math.abs(annotation.time_sec - state.time) < 1e-6);
}
function selectedAnnotation() {
  const mark = state.annotations.find(mark => mark.id === state.selectedAnnotationId);
  return mark && annotationVisibleInPane(mark,mark.pane) ? mark : null;
}
function updateAnnotationSelectionHint() {
  const mark = selectedAnnotation();
  ui.referenceHint.textContent = state.mode === 'erase' ? '划过标记擦除整条 · 可撤销'
    : state.mode !== 'select' ? '在图片上圈出想让 Codex 注意的地方'
    : mark?.pane === 'reference' ? mark.name + ' · Backspace 删除 · 拖到会话引用'
    : '点击标记选中 · 拖到会话引用 · 空白处拖动平移';
  for (const button of ui.annotationList.querySelectorAll('[data-annotation-id]')) {
    button.setAttribute('aria-pressed', String(button.dataset.annotationId === mark?.id));
  }
  for (const [canvas,pane,label] of [[ui.referenceCanvas,'reference','参考图片标注层'],[ui.sceneCanvas,'scene','场景截图标注层']]) {
    canvas.setAttribute('aria-label', label + (mark?.pane === pane ? '，已选中 ' + mark.name : ''));
  }
  updateSceneHint();
}
function annotationReferenceLabel(annotation) {
  const ref = [...state.references,...referenceViews().flatMap(view => view.frames)].find(item => item.id === annotation.reference_image_id);
  const location = [annotation.pane === 'reference' ? '参考图' + (ref?.name ? ' ' + ref.name : '') : '',annotationTimeLabel(annotation)].filter(Boolean).join(' · ');
  return annotation.name + (location ? '（' + location + '）' : '');
}
function insertAnnotationReference(annotation) {
  return insertNoteReference(annotationReferenceLabel(annotation), `[[annotation:${annotation.id}]]`);
}
async function revealAnnotation(annotation) {
  if (!editable()) return;
  if (annotation.frame_id) await openMoment(annotation.frame_id);
  else if (annotation.pane === 'scene') openSceneSnapshot(annotation.snapshot_id);
  else {
    pauseTimeline(); state.clipEnabled = false; state.activeReferenceId = annotation.reference_image_id;
    state.referenceZoom = 1; state.referencePan = {x:0,y:0};
    renderReferenceStrip(); showActiveReference();
  }
  setMode('select');
  state.selectedAnnotationId = annotation.id;
  minimalLayout?.closeReferences(); drawOverlays(); saveDraft();
  (annotation.pane === 'scene' ? ui.sceneCanvas : ui.referenceCanvas).focus({preventScroll:true});
}
function selectAnnotationFromPointer(event,pane) {
  if (pane === 'scene' && state.sceneView !== 'snapshot') return;
  const canvas = event.currentTarget;
  const point = pointFromPointer(event,canvas), rect = canvas.getBoundingClientRect();
  const zoom = rect.width / canvas.clientWidth;
  const scale = Math.max(1,Math.min(canvas.clientWidth,canvas.clientHeight)/550)*zoom;
  const context = canvas.getContext('2d');
  context.save(); context.font = 'bold ' + Math.round(13*scale/zoom) + 'px ' + annotationFontFamily;
  const mark = [...state.annotations].reverse().find(mark => {
    if (!annotationVisibleInPane(mark,pane)) return false;
    const textWidth = mark.type === 'text' ? Math.min(context.measureText(String(mark.text||'').slice(0,100)).width,canvas.clientWidth-12)*zoom : 0;
    return eraserHitsAnnotation(mark,point,point,{width:rect.width,height:rect.height,scale,textWidth,radius:5}) ||
      ((pane !== 'scene' || workspaceControls.sceneLabelsVisible()) && hitsAnnotationName(context,mark,point,point,{width:canvas.clientWidth,height:canvas.clientHeight,zoom,radius:3}));
  });
  context.restore();
  state.selectedAnnotationId = mark?.id || null;
  canvas.focus({preventScroll:true}); drawOverlays();
  if (!mark) return;
  event.preventDefault(); event.stopPropagation();
  canvas.setPointerCapture(event.pointerId);
  annotationReferenceDrag = {pointerId:event.pointerId,canvas,id:mark.id,x:event.clientX,y:event.clientY,dragging:false};
}
function overAnnotationDropTarget(event) {
  const dock = id('chat-dock');
  if (dock.inert || dock.classList.contains('hidden')) return false;
  const rect = dock.getBoundingClientRect();
  return event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom;
}
function moveAnnotationReferenceDrag(event) {
  const drag = annotationReferenceDrag;
  if (!drag || drag.pointerId !== event.pointerId) return false;
  if (!editable() || !selectedAnnotation()) { finishAnnotationReferenceDrag(); return true; }
  if (!drag.dragging && Math.hypot(event.clientX-drag.x,event.clientY-drag.y) < 6) return true;
  if (!drag.dragging) { drag.dragging = true; minimalLayout?.openChat(); }
  event.preventDefault(); event.stopPropagation();
  const mark = selectedAnnotation(), over = overAnnotationDropTarget(event);
  document.body.classList.add('dragging-annotation-reference');
  id('chat-dock').classList.add('annotation-drop-target');
  id('chat-dock').classList.toggle('annotation-drop-over',over);
  annotationDragGhost.classList.remove('hidden');
  annotationDragGhost.textContent = mark.name + (over ? ' · 松开引用' : ' · 拖到会话引用');
  annotationDragGhost.style.left = clamp(event.clientX+14,8,window.innerWidth-annotationDragGhost.offsetWidth-8) + 'px';
  annotationDragGhost.style.top = clamp(event.clientY+14,8,window.innerHeight-40) + 'px';
  return true;
}
function finishAnnotationReferenceDrag(event=null) {
  const drag = annotationReferenceDrag;
  if (!drag) return;
  annotationReferenceDrag = null;
  if (drag.canvas.hasPointerCapture(drag.pointerId)) drag.canvas.releasePointerCapture(drag.pointerId);
  const mark = state.annotations.find(mark => mark.id === drag.id);
  const dropped = event && event.pointerId === drag.pointerId && drag.dragging && overAnnotationDropTarget(event);
  annotationDragGhost.classList.add('hidden');
  document.body.classList.remove('dragging-annotation-reference');
  id('chat-dock').classList.remove('annotation-drop-target','annotation-drop-over');
  if (dropped && mark && editable() && annotationVisibleInPane(mark,mark.pane)) insertAnnotationReference(mark);
}

function sweepEraser(to, canvas) {
  const drag = state.drag;
  const rect = canvas.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return;
  const zoom = rect.width / canvas.clientWidth;
  const scale = Math.max(1, Math.min(canvas.clientWidth, canvas.clientHeight) / 550) * zoom;
  const context = canvas.getContext('2d');
  context.save(); context.font = 'bold ' + Math.round(13 * scale / zoom) + 'px ' + annotationFontFamily;
  for (const mark of state.annotations) {
    if (drag.erased.has(mark.id) || !annotationVisibleInPane(mark, drag.pane)) continue;
    const textWidth = mark.type === 'text' ? Math.min(context.measureText(String(mark.text || '').slice(0,100)).width, canvas.clientWidth - 12) * zoom : 0;
    const radius = workspaceControls.eraserRadius();
    if (eraserHitsAnnotation(mark, drag.end, to, {width:rect.width, height:rect.height, scale, textWidth, radius}) ||
        ((drag.pane !== 'scene' || workspaceControls.sceneLabelsVisible()) && hitsAnnotationName(context,mark,drag.end,to,{width:canvas.clientWidth,height:canvas.clientHeight,zoom,radius}))) drag.erased.add(mark.id);
  }
  context.restore(); drag.end = to;
}
function annotationPointerDown(event, pane) {
  if (!editable() || state.spacePan || event.button !== 0) return;
  if (state.mode === 'select') { selectAnnotationFromPointer(event,pane); return; }
  if (pane === 'reference' && !activeReference()) return;
  const canvas = event.currentTarget;
  // Preserve the clicked pixel before saving a moment can resize the timeline.
  const point = pointFromPointer(event, canvas);
  event.preventDefault();
  if (state.mode === 'erase') {
    if (pane === 'scene' && state.sceneView !== 'snapshot') { announce('请先选择一张截图，再擦除上面的标记。'); return; }
    canvas.setPointerCapture(event.pointerId);
    state.drag = {pane, type:'erase', start:point, end:point, pointerId:event.pointerId, erased:new Set()};
    sweepEraser(point, canvas); drawOverlays();
    return;
  }
  if (pane === 'scene') { if (!prepareSceneAnnotation()) return; }
  else if (dynamicEnabled() && !ensureDynamicMoment()) return;
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
  if (moveAnnotationReferenceDrag(event)) return;
  if (!state.drag || state.drag.pointerId !== event.pointerId) return;
  if (state.drag.type === 'erase') {
    if (!editable()) { state.drag = null; drawOverlays(); return; }
    sweepEraser(pointFromPointer(event, event.currentTarget), event.currentTarget); drawOverlays(); return;
  }
  state.drag.end = pointFromPointer(event, state.drag.pane === 'scene' ? ui.sceneCanvas : event.currentTarget);
  if (state.drag.type === 'freehand' && state.drag.points.length < 256) {
    const last = state.drag.points.at(-1);
    if (Math.hypot(last.x - state.drag.end.x, last.y - state.drag.end.y) > 0.003) state.drag.points.push(state.drag.end);
  }
  drawOverlays();
}
function annotationPointerUp(event) {
  if (annotationReferenceDrag?.pointerId === event.pointerId) { finishAnnotationReferenceDrag(event); return; }
  if (!editable()) { state.drag = null; drawOverlays(); return; }
  if (!state.drag || state.drag.pointerId !== event.pointerId) return;
  const drag = state.drag;
  if (drag.type === 'erase') {
    sweepEraser(pointFromPointer(event, event.currentTarget), event.currentTarget);
    state.drag = null;
    if (drag.erased.size) {
      const before = annotationEditState();
      state.annotations = state.annotations.filter(mark => !drag.erased.has(mark.id));
      recordAnnotationEdit(before); renderAnnotations(); renderTimeline(); saveDraft();
      announce('已擦除 ' + drag.erased.size + ' 条标记，可撤销。');
    }
    drawOverlays(); return;
  }
  state.drag = null;
  const end = pointFromPointer(event, drag.pane === 'scene' ? ui.sceneCanvas : event.currentTarget);
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
  if (!editable()) { hideTextEditor(); return; }
  const text = ui.annotationText.value.trim();
  if (text && state.textPending) {
    addAnnotation({
      pane:state.textPending.pane, type:'text',
      coordinates:{x:state.textPending.point.x, y:state.textPending.point.y}, text
    });
  }
  hideTextEditor();
}

function drawAnnotation(ctx, annotation, width, height, preview=false, selected=false, showName=true) {
  const p = annotation.coordinates || {};
  const x = clamp(Number(p.x) || 0, 0, 1) * width;
  const y = clamp(Number(p.y) || 0, 0, 1) * height;
  const x2 = clamp(Number(p.x2) || 0, 0, 1) * width;
  const y2 = clamp(Number(p.y2) || 0, 0, 1) * height;
  const stale = false;
  const color = selected ? '#171715' : stale ? '#8a8a83' : '#bd4c37';
  const scale = Math.max(1, Math.min(width, height) / 550);
  ctx.save();
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = 3 * scale;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.shadowColor = '#f5f4ef';
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
    ctx.font = 'bold ' + Math.round(13 * scale) + 'px ' + annotationFontFamily;
    const text = String(annotation.text).slice(0, 100);
    const textWidth = Math.min(ctx.measureText(text).width, width - 12);
    ctx.fillStyle = '#f5f4efed';
    ctx.fillRect(x, y - 19 * scale, textWidth + 12 * scale, 25 * scale);
    ctx.fillStyle = color;
    ctx.fillText(text, x + 5 * scale, y, Math.max(0, width - x - 10));
  }
  if (annotation.group_id && circled[Number(annotation.group_id)]) {
    ctx.setLineDash([]);
    ctx.font = 'bold ' + Math.round(20 * scale) + 'px ' + annotationFontFamily;
    ctx.fillStyle = '#f5f4ef';
    ctx.fillRect(x - 5 * scale, y - 30 * scale, 26 * scale, 23 * scale);
    ctx.fillStyle = color;
    ctx.fillText(circled[Number(annotation.group_id)], x - 3 * scale, y - 12 * scale);
  }
  ctx.restore();
  if (!preview && showName) drawAnnotationName(ctx,annotation,width,height,selected);
}
function prepareCanvas(canvas) {
  // clientWidth is the bitmap's untransformed size. Using the transformed
  // bounding box here would allocate enormous canvases when a photo is zoomed.
  const logicalWidth = canvas.clientWidth;
  const logicalHeight = canvas.clientHeight;
  const context = canvas.getContext('2d');
  // Image replacement/layout can temporarily give a layer no logical size.
  // Clear the entire old bitmap even then, so hiding or pending frame loads
  // cannot leave historical pixels to appear when the pane becomes visible.
  context.setTransform(1,0,0,1,0,0);
  context.clearRect(0,0,canvas.width,canvas.height);
  if (!logicalWidth || !logicalHeight) return null;
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.round(logicalWidth * ratio);
  const height = Math.round(logicalHeight * ratio);
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  return {context, width:logicalWidth, height:logicalHeight};
}
function drawOverlays() {
  if (!selectedAnnotation()) state.selectedAnnotationId = null;
  updateAnnotationSelectionHint();
  for (const pane of ['reference','scene']) {
    const canvas = pane === 'reference' ? ui.referenceCanvas : ui.sceneCanvas;
    canvas.dataset.selectedAnnotation = selectedAnnotation()?.pane === pane ? state.selectedAnnotationId : '';
    const surface = prepareCanvas(canvas);
    if (!surface) continue;
    for (const annotation of state.annotations) {
      if (!annotationVisibleInPane(annotation, pane) || state.drag?.erased?.has(annotation.id)) continue;
      drawAnnotation(surface.context, annotation, surface.width, surface.height, false, annotation.id === state.selectedAnnotationId, pane !== 'scene' || workspaceControls.sceneLabelsVisible());
    }
    if (state.drag?.pane === pane && state.drag.type !== 'erase') {
      drawAnnotation(surface.context, {
        pane, type:state.drag.type,
        coordinates:{x:state.drag.start.x, y:state.drag.start.y, x2:state.drag.end.x, y2:state.drag.end.y},
        points:state.drag.points
      }, surface.width, surface.height, true);
    }
  }
  renderHumanPosePanel();
  drawHumanPoseOverlay();
}
function renderAnnotations() {
  ensureAnnotationNames();
  updateSubmitLabel();
  ui.annotationList.replaceChildren();
  ui.annotationCount.textContent = String(state.annotations.length);
  renderSceneSnapshots();
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
    copy.dataset.annotationId = annotation.id;
    copy.title = '回看并选中标记，或拖到提示中引用';
    bindPromptDrag(copy,() => ({kind:'annotation',annotationId:annotation.id,label:annotationReferenceLabel(annotation)}),'annotation');
    const title = document.createElement('button');
    title.type = 'button'; title.className = 'annotation-select annotation-preview' + (annotation.pane === 'scene' ? ' annotation-open-scene' : '');
    title.disabled = !editable();
    title.dataset.annotationId = annotation.id;
    title.setAttribute('aria-pressed', String(annotation.id === state.selectedAnnotationId));
    const ref = [...state.references, ...referenceViews().flatMap((view) => view.frames)].find((item) => item.id === annotation.reference_image_id);
    title.textContent = (annotation.group_id ? circled[Number(annotation.group_id)] + ' ' : '') +
      (annotation.pane === 'reference' ? '参考图 · ' + (ref?.name || '图片') : '当前场景') +
      ' · ' + annotation.name + (annotationTimeLabel(annotation) ? ' · ' + annotationTimeLabel(annotation) : '');
    const subtitle = document.createElement('small');
    const stale = annotation.pane === 'scene' && annotation.scene_revision !== state.sceneRevision;
    subtitle.textContent = annotation.text || (stale ? '固定截图版本 ' + annotation.scene_revision : annotation.object_id ? '对象：' + annotation.object_id : '视觉提示');
    if (stale) subtitle.classList.add('stale-label');
    title.title = '选中 ' + annotation.name;
    title.addEventListener('click', () => revealAnnotation(annotation));
    copy.append(title, subtitle);
    const cite = document.createElement('button');
    cite.type = 'button';
    cite.className = 'reference-insert annotation-reference-insert';
    cite.textContent = '引用';
    cite.title = '在提示中引用这条标记';
    cite.disabled = !editable();
    cite.addEventListener('mousedown', (event) => event.preventDefault());
    cite.addEventListener('click', () => insertAnnotationReference(annotation));
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'annotation-remove';
    remove.textContent = '×';
    remove.title = '删除这条标记';
    remove.disabled = !editable();
    remove.addEventListener('click', () => removeAnnotation(annotation.id));
    row.append(glyph, copy, cite, remove);
    ui.annotationList.append(row);
  }
}

async function openAnnotationPreview(annotation) {
  if (!editable()) return;
  hideTextEditor(); pauseTimeline(); setMode('select');
  if (annotation.frame_id) {
    await openMoment(annotation.frame_id);
    if (state.snapshot?.id !== annotation.frame_id || state.sceneView !== 'snapshot') return;
  } else if (annotation.pane === 'scene') {
    if (!state.snapshot || state.snapshot.id !== annotation.snapshot_id) {
      announce('这条标记的原截图已无法恢复，请重新圈画。',true); return;
    }
    state.sceneView='snapshot'; renderSceneView(); drawOverlays();
  } else {
    const reference=state.references.find((item) => item.id === annotation.reference_image_id);
    if (!reference) { announce('这条标记的原参考图已被移除。',true); return; }
    state.clipEnabled=false; state.activeReferenceId=reference.id;
    renderReferenceStrip(); showActiveReference();
    if (referenceCamera(reference)) alignActiveReference(); else leaveReferenceCamera();
  }
  minimalLayout?.closeReferences(); saveDraft();
}

async function loadImage(url) {
  const image = new Image();
  image.crossOrigin = 'anonymous';
  image.src = resourceURL(url);
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
        annotation.pane === 'reference' && annotation.reference_image_id === ref.id && !annotation.frame_id
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
  if (snapshot?.selected_data_url) annotatedContext.drawImage(await loadImage(snapshot.selected_data_url), 0, 0);
  else annotatedContext.drawImage(original, 0, 0);
  for (const annotation of state.annotations) {
    if (annotation.pane === 'scene' && snapshot && annotation.snapshot_id === snapshot.id) {
      drawAnnotation(annotatedContext, annotation, annotated.width, annotated.height);
    }
  }
  const comparison = snapshot ? snapshot.comparison : captureSnapshotComparison();
  if (comparison === false) throw new Error('叠图参考尚未加载，反馈未发送，请稍后重试。');
  const extra = {};
  if (comparison) {
    const {data_url, lastPositive, ...metadata} = comparison;
    extra.comparison = metadata;
    if (comparison.enabled && comparison.opacity > 0) {
      const composite = document.createElement('canvas');
      composite.width = original.width; composite.height = original.height;
      const context = composite.getContext('2d');
      context.drawImage(original, 0, 0);
      const reference = await loadImage(data_url), rect = comparison.rect;
      context.globalAlpha = comparison.opacity / 100;
      context.drawImage(reference, rect.x * composite.width, rect.y * composite.height,
        rect.width * composite.width, rect.height * composite.height);
      context.globalAlpha = 1;
      for (const annotation of state.annotations) {
        if (annotation.pane === 'scene' && snapshot && annotation.snapshot_id === snapshot.id) {
          drawAnnotation(context, annotation, composite.width, composite.height);
        }
      }
      extra.scene_comparison_data_url = composite.toDataURL('image/jpeg', 0.9);
    }
  }
  return {scene_original_data_url:dataUrl,
    scene_annotated_data_url:annotated.toDataURL('image/jpeg', 0.84), ...extra};
}
function promptImageLabel(text) {
  return String(text || '图片').replace(/[\x00-\x1f\x7f]/g, ' ').trim().slice(0,180) || '图片';
}
function promptImageUnavailable(pane) {
  if (!state.imageReferencesSupported) return '服务尚不支持图片引用，请更新服务并刷新页面。';
  if (!editable()) return '当前暂时无法加入图片引用。';
  if (state.seeking || state.pendingViewId) return '当前帧正在加载，请稍后再引用图片。';
  if (pane === 'reference' && (!activeReference() || !referencePixelsReady())) return '参考图正在加载，请稍后再引用。';
  if (pane === 'scene' && state.sceneView === 'snapshot' &&
      (!state.snapshot || !imageReadyAtUrl(ui.snapshotImage,state.snapshot.data_url))) return '标注截图正在加载，请稍后再引用。';
  const comparison=pane === 'scene' && state.sceneView === 'snapshot' ? state.snapshot?.comparison : null;
  if (comparison?.enabled && comparison.opacity > 0 && !imageReadyAtUrl(ui.snapshotCompareImage,comparison.data_url)) return '截图的叠图参考正在加载，请稍后再引用。';
  return null;
}
function renderPromptDragControls() {
  for (const [pane,button] of [['reference',ui.dragReference],['scene',ui.dragScene]]) {
    if (!button) continue;
    const reason = promptImageUnavailable(pane);
    button.disabled = !!reason;
    button.title = reason || '拖到提示，也可点击引用当前' + (pane === 'reference' ? '参考图' : '渲染图');
  }
  promptMentions?.refresh();
}
function capturePromptImage(pane) {
  const reason = promptImageUnavailable(pane);
  if (reason) throw new Error(reason);
  // Capture synchronously at dragstart. The pixels and metadata in this object
  // never follow later camera movements, source seeks, or scene publications.
  pauseTimeline();
  const entry = {id:newId().replace(/-/g,''), pane};
  let source, marks, comparison=null;
  if (pane === 'reference') {
    const ref = activeReference();
    source = ui.referenceImage;
    entry.reference_id = ref.id;
    entry.label = promptImageLabel('参考图 · ' + (ref.name || '图片'));
    const view = referenceViews().find((candidate) => candidate.frames.some((frame) => frame.id === ref.id));
    if (view && state.referenceClip) {
      entry.clip_id = state.referenceClip.clip_id;
      entry.view_id = view.clip_id;
      entry.time_sec = ref.time_sec;
      const index = view.frames.findIndex((frame) => frame.id === ref.id);
      entry.label = promptImageLabel('参考图 · ' + frameTimeLabel(ref.time_sec,index,view.name || ''));
    }
    marks = state.annotations.filter((mark) => mark.pane === 'reference' && mark.reference_image_id === ref.id &&
      (!mark.frame_id || mark.frame_id === state.snapshot?.id && Math.abs(mark.time_sec-state.time) <= 1e-6));
  } else {
    const snapshot = state.sceneView === 'snapshot' ? state.snapshot : null;
    entry.scene_revision = snapshot?.scene_revision ?? state.sceneRevision;
    entry.selected_object_ids = [...(snapshot?.selected_object_ids || (state.selectedId ? [state.selectedId] : []))];
    entry.selected_scene_nodes = structuredClone(snapshot?.selected_scene_nodes || (state.selectedSceneNode ? [state.selectedSceneNode] : []));
    entry.label = '场景 · 版本 ' + entry.scene_revision;
    if (snapshot) {
      source = ui.snapshotImage;
      entry.original_data_url = snapshot.data_url;
      entry.camera = structuredClone(snapshot.camera);
      if (snapshot.comparison?.enabled && snapshot.comparison.opacity > 0) comparison=snapshot.comparison;
      marks = state.annotations.filter((mark) => mark.pane === 'scene' && mark.snapshot_id === snapshot.id);
      if (Number.isFinite(snapshot.time_sec)) {
        entry.time_sec = snapshot.time_sec;
        if (snapshot.clip_id) entry.clip_id = snapshot.clip_id;
        const viewId = snapshot.view_id || referenceViewForMoment(snapshot)?.clip_id;
        if (viewId) entry.view_id = viewId;
        const referenceId = snapshot.reference_frame_id || snapshot.reference_id;
        if (referenceId) entry.reference_id = referenceId;
        entry.label += ' · ' + snapshot.time_sec.toFixed(3) + ' s';
      }
    } else {
      settleOrbit();
      const visible = feedbackLayer.visible;
      feedbackLayer.visible = false;
      try {
        renderer.render(threeScene,camera);
        source = document.createElement('canvas');
        source.width = renderer.domElement.width; source.height = renderer.domElement.height;
        source.getContext('2d').drawImage(renderer.domElement,0,0);
        entry.camera = structuredClone(cameraData());
      } finally { feedbackLayer.visible = visible; renderer.render(threeScene,camera); }
      marks = [];
      if (dynamicEnabled()) {
        entry.time_sec = state.time;
        if (state.referenceClip && clipReference()) {
          entry.clip_id = state.referenceClip.clip_id;
          entry.view_id = referenceView().clip_id;
          entry.reference_id = clipReference().id;
        }
        entry.label += ' · ' + state.time.toFixed(3) + ' s';
      }
    }
  }
  const canvas = scaledCanvas(source.naturalWidth || source.width,source.naturalHeight || source.height);
  const context = canvas.getContext('2d');
  context.drawImage(source,0,0,canvas.width,canvas.height);
  entry.image_width = canvas.width; entry.image_height = canvas.height;
  entry.original_data_url ||= canvas.toDataURL('image/jpeg',.88);
  if (comparison) {
    const rect=comparison.rect;
    context.globalAlpha=comparison.opacity/100;
    context.drawImage(ui.snapshotCompareImage,rect.x*canvas.width,rect.y*canvas.height,rect.width*canvas.width,rect.height*canvas.height);
    context.globalAlpha=1;
  }
  if (marks.length || comparison) {
    for (const mark of marks) drawAnnotation(context,mark,canvas.width,canvas.height);
    entry.annotated_data_url = canvas.toDataURL('image/jpeg',.88);
  }
  return entry;
}
function addPromptImageReference(entry) {
  if (!editable()) return false;
  if (!state.imageReferencesSupported) { announce('服务尚不支持图片引用，请更新服务并刷新页面。',true); return false; }
  let cited;
  try { cited = collectImageReferences(ui.note.value,state.imageRefs); }
  catch (error) { announce(error.message,true); return false; }
  if (cited.length >= 8 && !cited.some((item) => item.id === entry.id)) {
    announce('一条提示最多引用 8 张图片，请先移除不需要的图片。',true); return false;
  }
  const previous = state.imageRefs;
  state.imageRefs = [...cited.filter((item) => item.id !== entry.id),entry];
  if (!insertNoteReference(entry.label,imageToken(entry.id))) { state.imageRefs = previous; return false; }
  renderPromptImageReferences();
  return true;
}
function previewPromptImage(entry) {
  imagePreviewReference = entry;
  ui.imagePreviewTitle.textContent = entry.label;
  ui.imagePreviewImage.src = entry.annotated_data_url || entry.original_data_url;
  ui.imagePreviewToggle.classList.toggle('hidden',!entry.annotated_data_url);
  ui.imagePreviewToggle.textContent = '查看原图';
  ui.imagePreviewToggle.dataset.original = 'false';
  ui.imagePreview.showModal();
}
function renderPromptImageReferences() {
  ui.imageRefs.replaceChildren();
  const ids = new Set([...ui.note.value.matchAll(/\[\[image:([A-Za-z0-9_-]{1,64})\]\]/g)].map((match) => match[1]));
  const entries = state.imageRefs.filter((item) => ids.has(item.id));
  ui.imageRefs.classList.toggle('hidden',!entries.length);
  for (const entry of entries) {
    const chip = document.createElement('div'); chip.className = 'prompt-image-chip'; chip.dataset.imageRefId = entry.id;
    const preview = document.createElement('button'); preview.type = 'button'; preview.className = 'prompt-image-preview';
    preview.title = '预览引用时固定的图片'; preview.dataset.imageRefId = entry.id;
    const image = document.createElement('img'); image.src = entry.annotated_data_url || entry.original_data_url; image.alt = '';
    const label = document.createElement('span'); label.textContent = entry.label;
    preview.append(image,label); preview.addEventListener('click',() => previewPromptImage(entry));
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'prompt-image-remove';
    remove.dataset.imageRefId = entry.id; remove.textContent = '×'; remove.title = '移除图片引用';
    remove.setAttribute('aria-label','移除 ' + entry.label); remove.disabled = !editable();
    remove.addEventListener('click',() => {
      if (!editable()) return;
      const token = imageToken(entry.id);
      ui.note.value = ui.note.value.split(token).join('');
      state.imageRefs = state.imageRefs.filter((item) => item.id !== entry.id);
      renderPromptImageReferences(); saveDraft(); ui.note.focus();
    });
    chip.append(preview,remove); ui.imageRefs.append(chip);
  }
}
function selectedPromptReference() {
  const item = sceneObject(state.selectedId);
  if (!item) return null;
  const node = state.selectedSceneNode;
  return node?.node_path?.length
    ? {kind:'node',node:structuredClone(node),label:node.node_name || item.name || item.id,
      model_url:item.url || null,scene_revision:state.sceneRevision}
    : {kind:'object',objectId:item.id,label:item.name || item.id};
}
function promptMentionImageSource(pane) {
  const snapshot=pane === 'scene' && state.sceneView === 'snapshot' ? state.snapshot : null;
  return JSON.stringify(pane === 'reference'
    ? [state.sessionId,activeReference()?.id,activeReference()?.url,referenceView()?.clip_id]
    : [state.sessionId,state.sceneRevision,state.sceneView,snapshot?.id,snapshot?.scene_revision,
      dynamicEnabled() ? state.time : null,clipReference()?.id,referenceView()?.clip_id]);
}
function mentionSceneNodes() {
  const roots=state.sceneObjects.map((item) => state.objectNodes.get(item.id)?.userData.gltfRoot || null);
  if (mentionSceneCache?.revision === state.sceneRevision && mentionSceneCache.roots.length === roots.length &&
      roots.every((root,index) => root === mentionSceneCache.roots[index])) return mentionSceneCache.nodes;
  const nodes=[];
  // Traverse only when models change, never for each query character. Bound
  // pathological imported trees while retaining named, addressable nodes.
  let visited=0;
  state.sceneObjects.forEach((item,index) => {
    const root=roots[index];
    if (!root) return;
    const pending=[...root.children];
    while (pending.length && visited < 20000 && nodes.length < 6000) {
      const node=pending.pop(); visited++;
      pending.push(...node.children);
      if (!node.name?.trim() && !node.userData?.semantic_id && !node.userData?.stable_id) continue;
      const reference=nodeReference(item.id,node,'part');
      if (!reference?.node_path.length) continue;
      nodes.push({reference,label:reference.node_name || reference.semantic_id || reference.stable_id || '节点 ' + reference.node_path.join('/'),
        category:reference.node_path.length === 1 ? '物品' : '部件',modelName:item.name || item.id,
        model_url:item.url || null,scene_revision:state.sceneRevision});
    }
  });
  mentionSceneCache={revision:state.sceneRevision,roots,nodes};
  return nodes;
}
function getPromptMentionCandidates() {
  if (!editable()) return [];
  const sessionId=state.sessionId, candidates=[], keys=new Set();
  const add=(item) => {
    if (keys.has(item.key)) return;
    keys.add(item.key); candidates.push({...item,sessionId});
  };
  const addNode=(node,label,source={},context='') => {
    const item=sceneObject(node?.parent_object_id);
    if (!item || !node.node_path?.length || !resolveSceneNode(node)) return;
    const modelURL=source.model_url ?? item.url ?? null;
    if (modelURL !== (item.url || null)) return;
    const key=node.parent_object_id+':'+node.node_path.join('/');
    add({key:'node:'+key,kind:'node',label:label || node.node_name || node.semantic_id || node.stable_id || key,
      detail:(context || (node.node_path.length === 1 ? '物品' : '部件'))+' · '+(item.name || item.id)+' · '+node.node_path.join('/'),
      search:['物品 对象 部件 节点 node part item',key,node.node_name,node.semantic_id,node.stable_id].join(' '),
      descriptor:{kind:'node',node:structuredClone(node),label:label || node.node_name || key,
        model_url:modelURL,scene_revision:source.scene_revision ?? state.sceneRevision}});
  };
  const selection=selectedPromptReference();
  if (selection?.kind === 'node') addNode(selection.node,selection.label,selection,'当前选中部件');
  for (const node of state.referencedSceneNodes) addNode(node,node.node_name || node.semantic_id || node.stable_id,node,'已引用部件');
  for (const item of state.sceneObjects) add({key:'object:'+item.id,kind:'object',label:item.name || item.id,
    detail:'对象 · '+item.id,search:'物体 物品 对象 object item '+item.id,
    descriptor:{kind:'object',objectId:item.id,label:item.name || item.id}});
  for (const node of mentionSceneNodes()) addNode(node.reference,node.label,node,node.category);
  state.annotations.forEach((mark,index) => {
    const number=circled[Number(mark.group_id)] || String(index+1), time=annotationTimeLabel(mark);
    const label=mark.name || '标记'+number+' · '+(labels[mark.type] || '标记');
    add({key:'annotation:'+mark.id,kind:'annotation',label,
      detail:(mark.pane === 'reference' ? '参考图' : '场景')+(time ? ' · '+time : '')+(mark.text ? ' · '+mark.text : ''),
      search:['标注 标记 提示 annotation mark',labels[mark.type],mark.name,index+1,mark.group_id,mark.id,mark.text,mark.object_id,mark.reference_image_id].join(' '),
      descriptor:{kind:'annotation',annotationId:mark.id,label:annotationReferenceLabel(mark)}});
  });
  if (state.imageReferencesSupported) {
    for (const pane of ['reference','scene']) {
      const reason=promptImageUnavailable(pane), ref=activeReference();
      const snapshot=pane === 'scene' && state.sceneView === 'snapshot' ? state.snapshot : null;
      const time=pane === 'reference' ? ref?.time_sec : snapshot?.time_sec ?? (dynamicEnabled() ? state.time : null);
      add({key:'current-image:'+pane,kind:'current-image',label:pane === 'reference' ? '左侧参考图' : '右侧渲染图',
        detail:reason || (pane === 'reference' ? ref?.name || ref?.id || '当前参考图' : '版本 '+(snapshot?.scene_revision ?? state.sceneRevision))+
          (Number.isFinite(time) ? ' · '+time.toFixed(3)+' s' : '')+' · 选中时固定截图',
        search:'图片 图像 截图 image photo screenshot '+(pane === 'reference' ? '左图 参考 reference '+(ref?.name || '')+' '+(ref?.id || '') : '右图 场景 scene 渲染'),
        disabled:reason,descriptor:{kind:'capture-image',pane,source:promptMentionImageSource(pane)}});
    }
    state.imageRefs.forEach((entry,index) => add({key:'saved-image:'+entry.id,kind:'saved-image',label:entry.label,
      detail:'已保存图片 '+(index+1)+' · 固定截图',search:'图片 图像 已保存 image photo screenshot '+entry.id+' '+(index+1),
      descriptor:{kind:'saved-image',imageId:entry.id}}));
  }
  for (const job of state.humanJobs) {
    const frame=humanCurrentFrame(job);
    if (job.status !== 'completed' || job.session_id !== state.sessionId || !frame || frame.reference_id !== activeReference()?.id) continue;
    add({key:'pose:'+job.job_id+':'+frame.reference_id,kind:'pose',label:humanJobName(job),
      detail:poseEvidenceLabel(job)+' · '+poseFrameLabel(frame,job.reference_name),
      search:'人体 骨架 关键点 人体证据 pose human skeleton '+humanJobNumber(job)+' '+job.job_id+' '+frame.reference_id,
      descriptor:{kind:'pose',jobId:job.job_id,referenceId:frame.reference_id}});
  }
  if (state.poseCorrectionsSupported) for (const sample of state.poseEdits.filter(validPoseEdit)) {
    add({key:'pose-edit:'+sample.id,kind:'pose-edit',label:'手部修正 · '+(sample.label || sample.reference_id),
      detail:'人工二维修正 · '+sample.edits.length+' 点',
      search:'手部 修正 关键点 草稿 pose_edit correction manual '+sample.id+' '+sample.job_id+' '+sample.reference_id+' '+sample.edits.map((edit) => edit.name).join(' '),
      descriptor:{kind:'pose-edit',editId:sample.id,jobId:sample.job_id,referenceId:sample.reference_id,imageSha256:sample.image_sha256}});
  }
  return candidates;
}
function insertPromptMention(candidate) {
  if (!editable() || candidate.sessionId !== state.sessionId || candidate.disabled) return false;
  const descriptor=candidate.descriptor;
  if (descriptor.kind === 'capture-image') {
    if (descriptor.source !== promptMentionImageSource(descriptor.pane)) throw new Error('画面已切换，请重新选择当前图片。');
    return addPromptImageReference(capturePromptImage(descriptor.pane));
  }
  if (descriptor.kind === 'saved-image') {
    const entry=state.imageRefs.find((item) => item.id === descriptor.imageId);
    if (!entry) throw new Error('这张保存图片已移除，请重新引用图片。');
    return addPromptImageReference(entry);
  }
  if (descriptor.kind === 'pose') {
    const job=state.humanJobs.find((item) => item.job_id === descriptor.jobId && item.status === 'completed' && item.session_id === state.sessionId);
    const frame=job && humanCurrentFrame(job);
    if (frame?.reference_id !== descriptor.referenceId || activeReference()?.id !== descriptor.referenceId) throw new Error('人体来源帧已切换，请重新选择当前帧结果。');
    return insertHumanPoseReference(job,frame);
  }
  if (descriptor.kind === 'pose-edit') {
    if (!state.poseCorrectionsSupported) throw new Error('服务尚不支持关键点修正，请更新服务并刷新页面。');
    const sample=state.poseEdits.find((item) => item.id === descriptor.editId);
    if (!validPoseEdit(sample) || sample.job_id !== descriptor.jobId || sample.reference_id !== descriptor.referenceId || sample.image_sha256 !== descriptor.imageSha256) throw new Error('这份修正草稿已失效，请重新选择。');
    collectPoseEdits(ui.note.value,state.poseEdits);
    return insertNoteReference('手部修正（'+sample.label+'）',poseEditToken(sample.id));
  }
  if (descriptor.kind === 'node' && !resolveSceneNode(descriptor.node)) throw new Error('这处部件已失效，请重新选择。');
  return insertPromptDragReference(descriptor);
}
function insertPromptDragReference(descriptor) {
  if (!editable() || !descriptor) return false;
  if (descriptor.kind === 'image') return addPromptImageReference(descriptor.image);
  else if (descriptor.kind === 'object' && sceneObject(descriptor.objectId)) return insertNoteReference(descriptor.label,`[[object:${descriptor.objectId}]]`);
  else if (descriptor.kind === 'node' && sceneObject(descriptor.node.parent_object_id)?.url === descriptor.model_url) {
    return insertSceneNodeReference(descriptor.node,descriptor.label,
      {model_url:descriptor.model_url,scene_revision:descriptor.scene_revision});
  }
  else if (descriptor.kind === 'annotation') {
    const marks=state.annotations.filter((mark) => mark.id === descriptor.annotationId);
    if (marks.length === 1) return insertAnnotationReference(marks[0]);
    announce('这处标记已失效，请重新选择。',true);
  }
  else announce('这处引用已失效，请重新选择后拖入。',true);
  return false;
}
function bindPromptDrag(element,getDescriptor,kind) {
  element.draggable = true; element.dataset.promptDrag = kind;
  let dragged = false;
  element.addEventListener('click',(event) => { if (dragged) { event.preventDefault(); event.stopImmediatePropagation(); } },true);
  element.addEventListener('dragstart',(event) => {
    if (!editable() || !event.dataTransfer) { event.preventDefault(); return; }
    try {
      const descriptor = getDescriptor();
      if (!descriptor) { event.preventDefault(); return; }
      dragged = true;
      promptDrag = {nonce:newId(),sessionId:state.sessionId,descriptor};
      event.dataTransfer.effectAllowed = 'copy';
      event.dataTransfer.setData(PROMPT_DRAG_MIME,promptDrag.nonce);
      // Defer closing the modal until the browser has captured its drag image.
      requestAnimationFrame(() => { if (promptDrag) { minimalLayout?.closeReferences(); minimalLayout?.openChat(); } });
    } catch (error) { event.preventDefault(); announce(error.message,true); }
  });
  element.addEventListener('dragend',() => {
    promptDrag = null; ui.note.closest('.prompt-input').classList.remove('prompt-drop-active');
    setTimeout(() => { dragged = false; },0);
  });
}
function bindPromptReferenceEvents() {
  for (const [pane,button] of [['reference',ui.dragReference],['scene',ui.dragScene]]) {
    const descriptor = () => ({kind:'image',image:capturePromptImage(pane)});
    bindPromptDrag(button,descriptor,pane);
    button.addEventListener('click',() => { try { insertPromptDragReference(descriptor()); } catch (error) { announce(error.message,true); } });
  }
  bindPromptDrag(ui.selectedChip,selectedPromptReference,'selection');
  ui.selectedChip.tabIndex = 0; ui.selectedChip.setAttribute('role','button');
  ui.selectedChip.title = '拖到提示引用选中的物品或部件，也可点击引用';
  ui.selectedChip.addEventListener('click',() => insertPromptDragReference(selectedPromptReference()));
  ui.selectedChip.addEventListener('keydown',(event) => {
    if (!['Enter',' '].includes(event.key)) return;
    event.preventDefault(); insertPromptDragReference(selectedPromptReference());
  });
  const target = ui.note.closest('.prompt-input');
  const canDrop = (event) => editable() && promptDrag?.sessionId === state.sessionId &&
    Array.from(event.dataTransfer?.types || []).includes(PROMPT_DRAG_MIME);
  for (const name of ['dragenter','dragover']) target.addEventListener(name,(event) => {
    if (!canDrop(event)) return;
    event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; target.classList.add('prompt-drop-active');
  });
  target.addEventListener('dragleave',(event) => { if (!target.contains(event.relatedTarget)) target.classList.remove('prompt-drop-active'); });
  target.addEventListener('drop',(event) => {
    target.classList.remove('prompt-drop-active');
    // Never insert URLs, files, or a forged custom payload from another page.
    event.preventDefault();
    if (!canDrop(event) || event.dataTransfer.getData(PROMPT_DRAG_MIME) !== promptDrag.nonce) return;
    const descriptor = promptDrag.descriptor; promptDrag = null;
    insertPromptDragReference(descriptor);
  });
  id('prompt-image-preview-close').addEventListener('click',() => ui.imagePreview.close());
  ui.imagePreview.addEventListener('close',() => { imagePreviewReference = null; ui.imagePreviewImage.removeAttribute('src'); });
  ui.imagePreviewToggle.addEventListener('click',() => {
    if (!imagePreviewReference?.annotated_data_url) return;
    const original = ui.imagePreviewToggle.dataset.original !== 'true';
    ui.imagePreviewToggle.dataset.original = String(original);
    ui.imagePreviewToggle.textContent = original ? '查看标注图' : '查看原图';
    ui.imagePreviewImage.src = original ? imagePreviewReference.original_data_url : imagePreviewReference.annotated_data_url;
  });
}
function insertNoteText(text, {replaceSelection=true,focus=true}={}) {
  if (!editable()) return false;
  const start = replaceSelection ? ui.note.selectionStart : ui.note.selectionEnd;
  const end = ui.note.selectionEnd;
  const before = start > 0 && !/\s/.test(ui.note.value[start - 1]) ? ' ' : '';
  const after = end < ui.note.value.length && !/\s/.test(ui.note.value[end]) ? ' ' : '';
  const insertion = before + text + after;
  if (ui.note.value.length - (end - start) + insertion.length > 10000) {
    announce('加入引用会超过提示的 10000 字上限，请先精简提示或逐条引用。', true);
    return false;
  }
  minimalLayout?.closeReferences();
  minimalLayout?.openChat();
  ui.note.setRangeText(insertion, start, end, 'end');
  // Selecting or quoting a mark preserves the current screenshot.
  setMode('select');
  if (focus) ui.note.focus();
  renderPromptImageReferences();
  saveDraft();
  return true;
}
function insertNoteReference(label, token) {
  return insertNoteText(label + ' ' + token);
}
function insertAllAnnotationReferences() {
  if (!editable() || !state.annotations.length) return;
  const existing = new Set([...ui.note.value.matchAll(/\[\[annotation:([A-Za-z0-9_-]{1,64})\]\]/g)].map((match) => match[1]));
  const references = state.annotations.flatMap((annotation, index) => {
    if (existing.has(annotation.id)) return [];
    return [annotationReferenceLabel(annotation) + ` [[annotation:${annotation.id}]]`];
  });
  if (!references.length) {
    minimalLayout?.closeReferences(); minimalLayout?.openChat({focus:true});
    announce('全部标记已在提示中引用。');
    return;
  }
  if (insertNoteText(references.join('\n'), {replaceSelection:false})) {
    announce('已引用 ' + references.length + ' 条标记，可继续补充提示。');
  }
}
function insertSceneNodeReference(node, label, source=null) {
  if (!editable() || !node?.node_path?.length) return false;
  const key = node.parent_object_id + ':' + node.node_path.join('/');
  const index = state.referencedSceneNodes.findIndex((entry) => entry.parent_object_id + ':' + entry.node_path.join('/') === key);
  const captured = {...node, model_url:source?.model_url ?? sceneObject(node.parent_object_id)?.url ?? null,
    scene_revision:source?.scene_revision ?? state.sceneRevision};
  if (!insertNoteReference(label || '场景节点', `[[node:${key}]]`)) return false;
  if (index < 0) state.referencedSceneNodes.push(captured);
  else state.referencedSceneNodes[index] = captured;
  saveDraft();
  return true;
}
function promptReferences(note=ui.note.value) {
  if (note.length > 10000) throw new Error('提示最多 10000 字，请精简后再发送。');
  if (/\[\[image:/.test(note) && !state.imageReferencesSupported) throw new Error('服务尚不支持图片引用，请更新服务并刷新页面后发送。');
  collectImageReferences(note,state.imageRefs);
  collectPoseReferences(note,state.poseRefs);
  if (/\[\[pose_edit:/.test(note) && !state.poseCorrectionsSupported) throw new Error('服务尚不支持关键点修正，请更新服务并刷新页面后发送。');
  collectPoseEdits(note,state.poseEdits);
  const tokenPattern = /\[\[(object|annotation|node):([A-Za-z0-9_-]{1,64})(?::(\d+(?:\/\d+)*))?\]\]/g;
  const tokens = [...note.matchAll(tokenPattern)];
  const starts = new Set(tokens.map((match) => match.index));
  for (const match of note.matchAll(/\[\[(?:object|annotation|node):/g)) {
    if (!starts.has(match.index)) throw new Error('提示里有不完整的引用；请重新点击物体或标记旁的「引用」。');
  }
  const nodes = [];
  const seenNodes = new Set();
  for (const match of tokens) {
    const [, kind, objectId, path] = match;
    if (kind !== 'node' && path !== undefined) throw new Error('提示里的物体或标记引用格式不正确。');
    if (kind === 'annotation') {
      if (state.annotations.filter((item) => item.id === objectId).length !== 1) {
        throw new Error('提示引用的标记已删除或重复，请更新这处引用。');
      }
    } else if (kind === 'object') {
      if (!sceneObject(objectId) && !snapshotForFeedback() && !state.sceneSnapshots.length) {
        throw new Error('提示引用的对象已不在当前场景，请更新这处引用。');
      }
    } else {
      if (path === undefined) throw new Error('提示里的场景节点引用缺少路径。');
      const key = objectId + ':' + path;
      if (seenNodes.has(key)) continue;
      seenNodes.add(key);
      const node = state.referencedSceneNodes.find((item) => item.parent_object_id === objectId && item.node_path.join('/') === path);
      if (!node) throw new Error('提示引用的场景节点已失效，请重新选中并引用它。');
      const model = sceneObject(objectId);
      if (model && node.model_url !== model.url && !snapshotForFeedback() && !state.sceneSnapshots.length) {
        throw new Error('提示引用的场景节点来自旧模型，请重新选中并引用它。');
      }
      nodes.push({parent_object_id:node.parent_object_id, node_path:[...node.node_path],
        ...(node.node_name ? {node_name:node.node_name} : {}),
        ...(node.stable_id ? {stable_id:node.stable_id} : {}), ...(node.semantic_id ? {semantic_id:node.semantic_id} : {})});
      if (nodes.length > 64) throw new Error('一条提示最多引用 64 个场景节点。');
    }
  }
  return nodes;
}
function clearSubmittedPrompt(submission) {
  const submittedDraft = submission.draftNote;
  const submittedNote = submission.payload.note;
  const unchanged = typeof submittedDraft === 'string'
    ? ui.note.value === submittedDraft
    : ui.note.value.trim() === String(submittedNote || '').trim();
  if (!unchanged) return false;
  ui.note.value = '';
  state.referencedSceneNodes = [];
  state.poseRefs = [];
  state.poseEdits = []; state.poseEditor=null; state.poseEditDrag=null;
  state.imageRefs = [];
  renderPromptImageReferences();
  return true;
}
async function clearSubmittedDraft(submission) {
  // Controls remain locked until the save is acknowledged. If a newer prompt
  // was restored or changed meanwhile, preserve its associated visual draft.
  if (!clearSubmittedPrompt(submission)) return false;
  hideTextEditor(); state.drag = null;
  state.annotations = [];
  state.dynamicSnapshots = [];
  state.sceneSnapshots = [];
  state.snapshot = null;
  state.sceneView = 'live';
  state.selectedId = null;
  state.selectedSceneNode = null;
  state.lastPickedDetailNode = null;
  state.groupId = ''; ui.groupSelect.value = '';
  state.mode = 'select';
  // A submitted round is a fresh undo boundary. Its evidence lives in the
  // saved feedback packet, rather than being restored into a later round.
  annotationHistory.clear();
  ui.snapshotImage.removeAttribute('src');
  renderSelection();
  renderSceneView({persist:false});
  renderAnnotations(); renderTimeline(); drawOverlays();
  state.draftMomentSignature = null;
  saveDraft();
  // Drain the serialized moment writes before unlocking the next round, so an
  // immediate reload cannot recover the old dynamic screenshots.
  await saveMomentDraft.pending;
  await promptImageStore.pending.catch(() => {});
  return true;
}
async function feedbackPayload(referencedSceneNodes, promptText) {
  const imageRefs = collectImageReferences(promptText,state.imageRefs);
  if (imageRefs.length && !state.imageReferencesSupported) throw new Error('服务尚不支持图片引用，请更新服务并刷新页面后发送。');
  const poseEdits=collectPoseEdits(promptText,state.poseEdits);
  if (poseEdits.length && !state.poseCorrectionsSupported) throw new Error('服务尚不支持关键点修正，请更新服务并刷新页面后发送。');
  const snapshot = snapshotForFeedback();
  const submittedCamera = snapshot?.camera || cameraData();
  const imageBundle = await captureScene(snapshot);
  const annotatedReferences = await captureReferenceAnnotations();
  const dynamicFrames = dynamicEnabled() ? await captureDynamicFrames() : [];
  const sceneSnapshots = [];
  for (const view of state.sceneSnapshots) {
    sceneSnapshots.push({id:view.id, name:view.name, scene_revision:view.scene_revision, camera:view.camera,
      selected_object_ids:view.selected_object_ids, selected_scene_nodes:view.selected_scene_nodes, ...await captureScene(view)});
  }
  const note = promptText.trim();
  return {
    scene_revision:Math.min(snapshot?.scene_revision || state.sceneRevision, ...dynamicFrames.map((entry) => entry.scene_revision), ...sceneSnapshots.map(entry => entry.scene_revision)),
    latest_scene_revision:state.sceneRevision,
    ...(sceneSnapshots.length ? {scene_snapshots:sceneSnapshots} : {}),
    active_reference_id:clipReference() ? null : state.activeReferenceId,
    aligned_reference_id:!clipReference() && submittedCamera.alignment_exact && submittedCamera.reference_image_id === state.activeReferenceId
      ? state.activeReferenceId : null,
    ...(dynamicEnabled() ? {timeline:{clip_id:state.referenceClip?.clip_id || null, ...(state.referenceClip ? {view_id:referenceView().clip_id} : {}), time_sec:state.time, duration_sec:timelineDuration(), fps:timelineFps(),
      scope:feedbackScope(ui.scope.value, state.time, Number(ui.rangeStart.value), Number(ui.rangeEnd.value), timelineDuration())}, dynamic_frames:dynamicFrames} : {}),
    note:note || (state.references.length && !state.annotations.length ? '请参考这些图片开始或继续重建场景。' : ''),
    pose_refs:collectPoseReferences(promptText,state.poseRefs),
    pose_edits:poseEdits,
    image_refs:structuredClone(imageRefs),
    referenced_scene_nodes:referencedSceneNodes,
    annotations:state.annotations.map((annotation) => {
      const item = {...annotation};
      const savedView = snapshot || state.sceneSnapshots.find(view => view.id === item.snapshot_id);
      if (!savedView && item.object_id && !sceneObject(item.object_id)) {
        item.previous_object_id = item.object_id;
        delete item.object_id;
      }
      if (!savedView && item.scene_node && sceneObject(item.scene_node.parent_object_id)?.type !== 'model') {
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
  if (!state.workspaceReady || !state.sessionId || !Number.isInteger(state.sceneRevision) || state.sceneLoading || state.submitting || state.uploading || state.creatingProject || state.navigatingProject) return;
  const promptText = ui.note.value;
  if (!state.poseCorrectionsSupported && (/\[\[pose_edit:/.test(promptText) || state.pendingSubmission?.payload?.pose_edits?.length)) {
    announce('服务尚不支持关键点修正，请更新服务并刷新页面后发送。',true); return;
  }
  if (!state.imageReferencesSupported && (/\[\[image:/.test(promptText) || state.pendingSubmission?.payload?.image_refs?.length)) {
    announce('服务尚不支持图片引用，请更新服务并刷新页面后发送。',true); return;
  }
  let referencedSceneNodes = [];
  if (!state.pendingSubmission) {
    try { referencedSceneNodes = promptReferences(promptText); }
    catch (error) { announce(error.message, true); ui.note.focus(); return; }
  }
  if (!state.pendingSubmission && !state.annotations.length && !promptText.trim() && !state.references.length && !state.referenceClip) {
    announce('请添加参考图、画标记，或填写提示后再发送。', true);
    return;
  }
  if (!state.pendingSubmission && dynamicEnabled()) {
    const mismatched = state.dynamicSnapshots.some((entry) => entry.clip_id !== (state.referenceClip?.clip_id || null) ||
      entry.reference_frame_id && !referenceViewForMoment(entry)?.frames.some((frame) => frame.id === entry.reference_frame_id));
    if (state.annotations.some((mark) => mark.frame_id && !state.dynamicSnapshots.some((moment) => moment.id === mark.frame_id))) {
      announce('有标记的原截图未能恢复，请移除这条标记并重新圈画。', true); return;
    }
    if (mismatched) { announce('动态参考已替换，请移除旧片段的时刻和标记后再提交。', true); return; }
    if (!state.dynamicSnapshots.length && !ensureDynamicMoment()) return;
  }
  pauseTimeline();
  hideTextEditor(); state.drag = null; settleOrbit();
  state.submitting = true;
  minimalLayout?.refresh();
  updateMode();
  renderTimeline();
  ui.submit.disabled = true;
  ui.note.disabled = true;
  setSubmitLabel('准备图片…');
  try {
    if (!state.pendingSubmission) {
      const capabilities = await api('/api/health');
      if ((activeReference() || [...state.sceneSnapshots, ...state.dynamicSnapshots].some(view => view.comparison)) && !capabilities.snapshot_comparison_supported) throw new Error('叠图反馈服务需要更新，请刷新后重试；截图和标记已保留。');
      if (state.sceneSnapshots.length && !capabilities.scene_snapshots_supported) throw new Error('多截图服务正在更新，请稍后再发送；截图和标记已保留。');
      if (state.annotations.some(mark => mark.pane === 'scene' && !mark.frame_id && !state.sceneSnapshots.some(view => view.id === mark.snapshot_id))) throw new Error('有标记的原截图未恢复，请删除该标记或重新加载草稿后再发送。');
      const payload = await feedbackPayload(referencedSceneNodes, promptText);
      const key = newId();
      // Clicking Send authorizes using the attached evidence at its original
      // revision, including if a newer scene is published before this POST.
      state.pendingSubmission = {key, payload:{...payload, idempotency_key:key,
        confirm_stale:true}, draftNote:promptText};
      try { await writeOutbox(state.pendingSubmission); }
      catch (error) { state.pendingSubmission = null; throw error; }
    }
    setSubmitLabel('正在发送…');
    if (state.pendingSubmission.payload.image_refs?.length && !state.imageReferencesSupported) {
      throw new Error('服务尚不支持图片引用，请更新服务并刷新页面后发送。');
    }
    if (state.pendingSubmission.payload.pose_edits?.length && !state.poseCorrectionsSupported) {
      throw new Error('服务尚不支持关键点修正，请更新服务并刷新页面后发送。');
    }
    const result = await api('/api/sessions/' + encodeURIComponent(state.sessionId) + '/feedback', {
      method:'POST', body:state.pendingSubmission.payload
    });
    const submitted = state.pendingSubmission;
    await clearOutbox();
    state.pendingSubmission = null;
    const draftCleared = await clearSubmittedDraft(submitted);
    state.feedbackCount += 1;
    id('feedback-count-label').textContent = '已提交 ' + state.feedbackCount + ' 条';
    const delivery = result.delivery?.status || (state.feedbackTransport === 'mcp_events' ? 'event_pending'
      : state.deliveryMode === 'external' ? 'submitted' : 'queued');
    ui.caption.textContent = draftCleared ? '' : '新草稿已保留，可以继续编辑。';
    saveDraft();
    if (state.feedbackTransport === 'mcp_events') {
      const subscribers=Number.isInteger(result.delivery?.subscriber_count) ? result.delivery.subscriber_count
        : state.eventDelivery?.subscriber_count || 0;
      announce(delivery === 'event_delivered' ? '视觉反馈事件已送达订阅插件；请在插件所在宿主查看后续。'
        : delivery === 'event_failed' ? '反馈已保存，但事件投递失败；请查看记录。'
        : delivery === 'event_unsubscribed' && result.delivery?.awaiting_subscription
          ? '反馈已保存，等待插件订阅后尝试投递。'
        : delivery === 'event_unsubscribed' || !subscribers ? '反馈已保存，当前没有活动插件订阅；可在记录中查看。'
          : '反馈已保存，正在通过 MCP 事件投递给插件。');
    } else if (state.deliveryMode === 'external') {
      announce(delivery === 'running' ? '图文反馈已送入原 Codex 任务。'
        : delivery === 'dispatching' ? '工作台正在将图文反馈送入原 Codex 任务。'
        : delivery === 'queued' ? '反馈已保存，等待原 Codex 任务空闲后送入。'
        : delivery === 'completed' ? '原 Codex 任务已处理这条反馈。'
        : delivery === 'blocked_stale' ? '反馈已保存；请确认是否仍按旧场景截图发送。'
        : delivery === 'delivery_uncertain' ? '反馈已保存，送达状态待核实，请查看原 Codex 任务。'
        : delivery === 'failed' ? '反馈已保存，但发送失败；请查看工作台状态。'
        : delivery === 'returned_to_mcp' ? 'MCP 已读取视觉反馈；请查看原 Codex 任务是否继续执行。'
        : '视觉反馈已保存，等待 MCP 工具读取。');
    } else {
      announce(delivery === 'running' ? '图文消息已送进当前 Codex 会话。'
        : delivery === 'blocked_stale' ? '已保存消息；场景已更新，请在右侧确认旧截图后继续发送。'
        : delivery === 'delivery_uncertain' ? '消息已保存，送达状态待核实。'
        : '图文消息已加入 Codex 的下一轮。');
    }
    try { await refreshWorkspace(); } catch { /* The next poll will recover status. */ }
  } catch (error) {
    const revisionConflict = error.status === 409 && (error.detail?.code === 'feedback_revision_conflict' ||
      /^(scene revision changed|dynamic frame scene_revision must be|submitted scene_revision must be the oldest)/i.test(error.message));
    if (error.status === 400 || error.status === 422 || revisionConflict) {
      await clearOutbox();
      state.pendingSubmission = null;
      if (revisionConflict) {
        try { await loadScene(); } catch { /* Poll will retry. */ }
        announce('反馈版本校验未通过，已解除重试锁定。原截图和提示仍保留，可修改后重新发送。', true);
      } else {
        announce('反馈需要修改：' + error.message + '。草稿已保留，请修改后再发送。', true);
      }
    } else {
      announce('发送失败：' + error.message + '。可点击重试，系统会沿用同一消息编号。', true);
    }
  } finally {
    state.submitting = false;
    minimalLayout?.refresh();
    renderTimeline();
    updateSubmitLabel();
    renderAnnotations();
    updateMode();
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
      const objectId = node.userData.objectId;
      const detailNode = nodeReference(objectId, hit.object, 'part');
      return {objectId, detailNode,
        sceneNode:state.selectionLevel === 'item' ? nodeReference(objectId, hit.object, 'item') : detailNode};
    }
  }
  return null;
}
function handleSceneClick(event) {
  const selection = pickScene(event);
  if (selection) selectObject(selection.objectId, selection.sceneNode, selection.detailNode);
}
function resizeScene() {
  const width = ui.sceneStage.clientWidth;
  const height = ui.sceneStage.clientHeight;
  if (!width || !height) return;
  const ref = activeReference();
  const pose = state.alignedReferenceId === ref?.id && referenceCamera(ref);
  if (pose) {
    const overlay = ui.compareImage;
    const sourceMatches = imageMatchesUrl(overlay, alignmentOverlayUrl(ref));
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
    setRendererSize(viewWidth, viewHeight);
  } else {
    ui.viewport.style.left = '0';
    ui.viewport.style.top = '0';
    ui.viewport.style.right = '0';
    ui.viewport.style.bottom = '0';
    ui.viewport.style.width = '100%';
    ui.viewport.style.height = '100%';
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    setRendererSize(width, height);
  }
  updateSnapshotGeometry();
  drawOverlays();
}
function setRendererSize(width, height) {
  renderer.getSize(rendererSize);
  if (rendererSize.x !== width || rendererSize.y !== height) renderer.setSize(width, height, false);
}
function applyTheme({dark, animate=false}) {
  const palette = {background:new THREE.Color(dark ? '#202327' : '#eae9e3'), grid:new THREE.Color(dark ? '#8e9ba8' : '#ffffff')};
  const apply = () => {
    threeScene.background.copy(palette.background); threeScene.fog.color.copy(palette.background);
    ground.material.color.copy(palette.background); grid.material.color.copy(palette.grid);
  };
  if (animate) backgroundTransition = {start:performance.now(), from:threeScene.background.clone(), gridFrom:grid.material.color.clone(), ...palette};
  else { backgroundTransition = null; apply(); }
  if (selectionHelper) selectionHelper.material.color.set(dark ? '#ecece8' : '#292925');
}
function animate(timestamp) {
  requestAnimationFrame(animate);
  if (backgroundTransition) {
    const t = Math.min(1, (timestamp - backgroundTransition.start) / 380);
    const ease = t * t * (3 - 2 * t);
    threeScene.background.lerpColors(backgroundTransition.from, backgroundTransition.background, ease);
    threeScene.fog.color.copy(threeScene.background); ground.material.color.copy(threeScene.background);
    grid.material.color.lerpColors(backgroundTransition.gridFrom, backgroundTransition.grid, ease);
    if (t === 1) backgroundTransition = null;
  }
  advanceTimeline(timestamp);
  controls.tick(timestamp);
  if (state.sceneView === 'live') renderer.render(threeScene, camera);
}
async function poll() {
  if (!state.sessionId || state.submitting || state.uploading || state.navigatingProject || poll.running) return;
  poll.running = true;
  try {
    const [scene, session] = await Promise.all([
      api('/api/scene'),
      api('/api/sessions/' + encodeURIComponent(state.sessionId))
    ]);
    // A send may have started while these reads were in flight. Keep its
    // captured scene and version together until the packet has been saved.
    if (state.submitting || state.uploading || state.navigatingProject) return;
    if (scene.revision !== state.sceneRevision) await loadScene(scene);
    setReferenceClip(session.reference_clip || null);
    if (session.reference_images) setReferences(session.reference_images);
    if (session.status !== state.sessionStatus || Number(session.feedback_count) !== state.feedbackCount) setSession(session);
    await refreshWorkspace();
  } catch {
    state.networkError = '工作台连接中断，正在重试…';
    ui.agentStatus.textContent = '工作台连接中断，正在重试…';
    ui.agentStatus.className = 'agent-status error';
    minimalLayout?.refresh();
  }
  finally { poll.running = false; }
}
function bindEvents() {
  workspaceControls = setupWorkspaceControls({getState:() => state, onLabelsChange:drawOverlays});
  minimalLayout = setupMinimalLayout({getState:() => state});
  setupTheme({onChange:applyTheme});
  immersiveWorkspace = setupImmersive({getState:() => state, onResize:() => { resizeScene(); updateReferenceGeometry(); }});
  bindPromptReferenceEvents();
  bindPoseEditEvents();
  promptMentions=createPromptMentions({input:ui.note,menu:ui.mentionMenu,list:ui.mentionList,status:ui.mentionStatus,
    getCandidates:getPromptMentionCandidates,onSelect:insertPromptMention,isEnabled:editable,
    onError:(message) => announce(message,true)});
  restoreProjectRequest();
  ui.projectsButton.addEventListener('click', openProjectsDialog);
  id('close-projects').addEventListener('click', () => ui.projectsDialog.close());
  ui.projectsDialog.addEventListener('close', () => {
    ui.projectsButton.setAttribute('aria-expanded', 'false');
    ui.projectsButton.focus({preventScroll:true});
  });
  ui.projectsDialog.addEventListener('click', (event) => {
    if (event.target !== ui.projectsDialog) return;
    const bounds = ui.projectsDialog.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) ui.projectsDialog.close();
  });
  ui.refreshProjects.addEventListener('click', () => {
    loadProjects().catch(() => {});
    if (state.modelLoadError) loadModels({forProjects:true}).catch(() => {});
  });
  ui.createProjectPanel.addEventListener('toggle', () => {
    if (ui.createProjectPanel.open && (!state.models || state.modelLoadError)) loadModels({forProjects:true}).catch(() => {});
  });
  ui.projectName.addEventListener('input', renderCreateProject);
  ui.projectModel.addEventListener('change', () => {
    state.projectModelChoice = ui.projectModel.value || null; state.projectEffortChoice = ''; renderCreateProject();
  });
  ui.projectEffort.addEventListener('change', () => { state.projectEffortChoice = ui.projectEffort.value || ''; renderCreateProject(); });
  ui.createProjectForm.addEventListener('submit', (event) => { event.preventDefault(); createSceneProject(); });
  ui.openCreatedProject.addEventListener('click', () => navigateProject(state.projectCreationResult));
  ui.targetSelect.addEventListener('change', () => {
    state.targetChoice = ui.targetSelect.value || null;
    renderTargetPicker();
  });
  ui.refreshTargets.addEventListener('click', () => {
    loadTargets().catch((error) => announce('刷新任务列表失败：' + error.message, true));
    loadModels().catch((error) => announce('刷新模型列表失败：' + error.message, true));
  });
  ui.switchTarget.addEventListener('click', () => switchTask(state.targetChoice));
  ui.manualTargetId.addEventListener('input', renderTargetPicker);
  ui.manualSwitchTarget.addEventListener('click', () => switchTask(ui.manualTargetId.value.trim()));
  ui.createTargetPanel.addEventListener('toggle', () => {
    if (ui.createTargetPanel.open && !state.models && !state.loadingModels) {
      loadModels().catch(() => { /* The form shows the connection error. */ });
    }
  });
  ui.createModel.addEventListener('change', () => {
    state.modelChoice = ui.createModel.value || null;
    state.effortChoice = '';
    renderCreateTarget();
  });
  ui.createEffort.addEventListener('change', () => {
    state.effortChoice = ui.createEffort.value || '';
    renderCreateTarget();
  });
  ui.createTarget.addEventListener('click', createTask);
  document.querySelectorAll('.tool-button').forEach((button) => button.addEventListener('click', () => setMode(button.dataset.tool)));
  document.querySelectorAll('[data-selection-level]').forEach((button) => button.addEventListener('click', () => setSelectionLevel(button.dataset.selectionLevel)));
  updateSelectionLevelControls();
  ui.groupSelect.addEventListener('change', () => {
    if (!editable()) return;
    state.groupId = ui.groupSelect.value;
    saveDraft();
  });
  bindTimelineEvents();
  bindHumanPoseEvents();
  ui.referenceInput.addEventListener('change', () => uploadReferences(ui.referenceInput.files));
  ui.referenceImage.addEventListener('load', updateReferenceGeometry);
  ui.referenceImage.addEventListener('error', () => announce('这张参考图无法显示。', true));
  ui.compareImage.addEventListener('load', resizeScene);
  ui.alignReference.addEventListener('click', () => { if (editable()) { pauseTimeline(); alignActiveReference({notify:true}); } });
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
  ui.snapshotCompareImage.addEventListener('load', renderPromptDragControls);
  ui.savedSnapshot.addEventListener('click', openSavedSnapshot);
  ui.captureScene.addEventListener('click', freezeScene);
  ui.browse.addEventListener('click', browseScene);
  ui.newSceneBadge.addEventListener('click', browseScene);
  ui.snapshotButton.addEventListener('click', openSavedSnapshot);
  ui.compareToggle?.addEventListener('click', toggleCompare);
  ui.compareOpacity.addEventListener('input', () => setCompareOpacity(Number(ui.compareOpacity.value)));
  for (const button of ui.comparePresets) {
    button.addEventListener('click', () => setCompareOpacity(Number(button.dataset.compareOpacity)));
  }
  renderCompareControls();
  for (const [canvas,pane] of [[ui.referenceCanvas,'reference'],[ui.sceneCanvas,'scene'],[renderer.domElement,'scene']]) {
    canvas.addEventListener('pointerdown', (event) => annotationPointerDown(event, pane));
    canvas.addEventListener('pointermove', annotationPointerMove);
    canvas.addEventListener('pointerup', annotationPointerUp);
    canvas.addEventListener('pointercancel', () => {
      finishAnnotationReferenceDrag();
      state.drag = null;
      drawOverlays();
    });
  }
  for (const canvas of [ui.referenceCanvas,ui.sceneCanvas]) {
    canvas.tabIndex = 0;
    canvas.addEventListener('lostpointercapture', () => finishAnnotationReferenceDrag());
  }
  ui.note.addEventListener('input',() => { renderPromptImageReferences(); saveDraft(); });
  ui.note.addEventListener('keydown', (event) => {
    if (promptMentions.isComposing(event) || promptMentions.handleKeydown(event)) return;
    if (event.key !== 'Enter' || !(event.ctrlKey || event.metaKey) || event.altKey ||
        event.isComposing || event.keyCode === 229 || ui.submit.disabled) return;
    event.preventDefault();
    submitFeedback();
  });
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
  ui.clearAnnotations.addEventListener('click', () => {
    if (!editable() || ui.clearAnnotations.disabled) return;
    ui.clearDialog.showModal();
    ui.cancelClear.focus();
  });
  ui.cancelClear.addEventListener('click', () => ui.clearDialog.close());
  ui.clearDialog.addEventListener('close', () => {
    const target = ui.clearAnnotations.disabled ? ui.undoAnnotation : ui.clearAnnotations;
    if (!target.disabled) target.focus({preventScroll:true});
  });
  ui.confirmClear.addEventListener('click', () => {
    if (!ui.clearDialog.open) return;
    if (!editable()) { ui.clearDialog.close(); return; }
    pauseTimeline(); hideTextEditor(); state.drag = null;
    const before = annotationEditState();
    state.annotations = [];
    state.dynamicSnapshots = [];
    state.sceneSnapshots = [];
    state.snapshot = null; state.sceneView = 'live'; renderSceneView();
    recordAnnotationEdit(before);
    renderTimeline();
    renderAnnotations();
    drawOverlays();
    saveDraft();
    ui.clearDialog.close();
    announce('已清空标记和截图，可点击「撤销」恢复。');
  });
  ui.undoAnnotation.addEventListener('click', undoAnnotationEdit);
  ui.redoAnnotation.addEventListener('click', redoAnnotationEdit);
  ui.referenceAllAnnotations.addEventListener('mousedown', (event) => event.preventDefault());
  ui.referenceAllAnnotations.addEventListener('click', insertAllAnnotationReferences);
  ui.clearSelection.addEventListener('click', () => {
    if (!editable()) return;
    state.selectedId = null;
    state.selectedSceneNode = null;
    state.lastPickedDetailNode = null;
    renderSelection();
    saveDraft();
  });
  const focusSelection = () => {
    if (!editable()) return;
    browseScene();
    const selectedNode = resolveSceneNode(state.selectedSceneNode);
    if (selectedNode) frameBox(new THREE.Box3().setFromObject(selectedNode), {smooth:true, keepDirection:true});
    else if (state.selectedId) frameBox(objectBox(state.selectedId), {smooth:true, keepDirection:true});
    else frameAll({smooth:true});
    minimalLayout?.closeReferences();
  };
  ui.frame.addEventListener('click', focusSelection);
  renderer.domElement.addEventListener('dblclick', (event) => {
    if (!editable() || state.sceneView !== 'live' || state.mode !== 'select') return;
    const selection = pickScene(event);
    if (!selection) return;
    selectObject(selection.objectId, selection.sceneNode, selection.detailNode);
    focusSelection();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key.toLowerCase() !== 'f' || event.ctrlKey || event.metaKey || event.altKey || event.isComposing || event.keyCode === 229 ||
        event.target.closest('input, textarea, select, [contenteditable], dialog[open]') || document.querySelector('dialog[open]') || state.sceneView !== 'live') return;
    event.preventDefault(); focusSelection();
  });
  const directions = {front:[0,-1,0], back:[0,1,0], left:[-1,0,0], right:[1,0,0], top:[0,-.0001,1], bottom:[0,-.0001,-1], iso:[1,-1.4,.95]};
  id('camera-navigation').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-camera-view]');
    if (!button || !editable() || state.sceneView !== 'live') return;
    leaveReferenceCamera();
    controls.setFree(false);
    const direction = navigationDirection(directions[button.dataset.cameraView]).normalize();
    controls.moveTo(controls.target.clone().addScaledVector(direction, Math.max(.3, controls.getDistance())), controls.target, {up:controls.worldUp});
    updateMode();
  });
  id('ground-axis').addEventListener('change', () => {
    if (!editable() || state.sceneView !== 'live') return;
    leaveReferenceCamera();
    state.groundAxis = id('ground-axis').value;
    applyGroundAxis(state.groundAxis === 'auto' ? state.detectedUpAxis : state.groundAxis);
    controls.setFree(false); frameAll({smooth:true}); updateMode(); saveDraft();
  });
  id('free-rotation').addEventListener('click', () => {
    if (!editable() || state.sceneView !== 'live') return;
    leaveReferenceCamera(); controls.setFree(!controls.freeRotation); updateMode(); saveDraft();
  });
  id('upright-camera').addEventListener('click', () => {
    if (!editable() || state.sceneView !== 'live') return;
    leaveReferenceCamera(); controls.setFree(false); updateMode(); saveDraft();
  });
  id('frame-all').addEventListener('click', () => {
    if (!editable() || state.sceneView !== 'live') return;
    controls.setFree(false); frameAll({smooth:true}); updateMode();
  });
  ui.resetView.addEventListener('click', () => {
    if (!editable()) return;
    browseScene(); controls.setFree(false); frameAll({smooth:true}); updateMode(); minimalLayout?.closeReferences();
  });
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
    if (!pointerDown || pointerDown.button !== 0 || state.mode !== 'select' || state.sceneView !== 'live') return;
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
    if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.isComposing &&
        !event.target.closest('input, textarea, select, [contenteditable]:not([contenteditable=false]), dialog[open]')) {
      const key = event.key.toLowerCase();
      if (key === 'z' || (event.ctrlKey && key === 'y')) {
        if (!editable()) return;
        event.preventDefault();
        if (event.shiftKey || key === 'y') redoAnnotationEdit();
        else undoAnnotationEdit();
        return;
      }
    }
    if (event.key === 'Escape' && annotationReferenceDrag) { finishAnnotationReferenceDrag(); event.preventDefault(); return; }
    if (['Backspace','Delete'].includes(event.key) && !event.isComposing && !event.ctrlKey && !event.metaKey && !event.altKey &&
        !event.target.closest('input, textarea, select, [contenteditable]:not([contenteditable=false]), dialog[open]')) {
      const mark = selectedAnnotation();
      if (mark && editable()) { event.preventDefault(); removeAnnotation(mark.id); }
      return;
    }
    if (event.key === 'Escape' && state.drag?.type === 'erase') {
      state.drag = null; drawOverlays(); return;
    }
    if (event.key === '8' && !event.ctrlKey && !event.metaKey && !event.altKey && !event.isComposing &&
        !event.target.closest('input, textarea, select, [contenteditable]:not([contenteditable=false]), dialog[open]')) {
      event.preventDefault(); setMode('erase'); return;
    }
    if (event.target.closest('input, textarea, select, button, summary, a, [contenteditable=true], dialog[open]')) return;
    if (event.code === 'Space') { event.preventDefault(); state.spacePan = true; }
    if (event.key >= '1' && event.key <= '8') {
      setMode(['select','point','rectangle','line','arrow','text','freehand','erase'][Number(event.key) - 1]);
    }
    if (event.key === 'Escape') {
      state.drag = null; state.selectedAnnotationId = null;
      hideTextEditor();
      drawOverlays();
    }

  });
  document.addEventListener('keyup', (event) => { if (event.code === 'Space') state.spacePan = false; });
  window.addEventListener('blur', () => { finishAnnotationReferenceDrag(); state.spacePan = false; state.referencePanning = null; if (state.drag?.type === 'erase') { state.drag = null; drawOverlays(); } });
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
  state.networkError = '工作台启动失败：' + error.message;
  ui.pill.textContent = '连接失败';
  ui.pill.className = 'session-pill error';
  ui.submit.disabled = true;
  minimalLayout?.refresh();
  announce('工作台启动失败：' + error.message, true);
}
setInterval(poll, 1500);
setInterval(() => {
  if (state.workspaceReady && state.feedbackTransport !== 'mcp_events' && state.deliveryMode === 'external' && state.desktopAvailable && !state.loadingTargets && !state.switchingTarget) {
    loadTargets().catch(() => { /* The picker shows the connection error. */ });
  }
}, 20000);

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { TrackballControls } from 'three/addons/controls/TrackballControls.js';

// Keep one target and event interface while offering upright orbit and free rotation.
export class SceneNavigation extends THREE.EventDispatcher {
  constructor(camera, element) {
    super();
    this.camera = camera;
    this.element = element;
    this.orbit = new OrbitControls(camera, element);
    this.free = new TrackballControls(camera, element);
    this.free.keys = []; // Typing in the composer must never switch camera tools.
    this.free.staticMoving = true;
    this.free.rotateSpeed = 2.4;
    this.free.panSpeed = 0.5;
    this.free.zoomSpeed = 1.1;
    this.target = this.orbit.target;
    this.free.target = this.target;
    this.freeRotation = false;
    this._enabled = true;
    this.transition = null;
    this.orbit.zoomToCursor = true;
    for (const control of [this.orbit, this.free]) {
      for (const type of ['start', 'end', 'change']) control.addEventListener(type, () => this.dispatchEvent({type}));
    }
    this.element.addEventListener('pointerdown', () => this.cancelTransition(), {capture:true});
    this.element.addEventListener('wheel', () => this.cancelTransition(), {capture:true, passive:true});
    this.resizeObserver = new ResizeObserver(() => this.free.handleResize());
    this.resizeObserver.observe(element);
    this.syncEnabled();
  }
  get active() { return this.freeRotation ? this.free : this.orbit; }
  get enabled() { return this._enabled; }
  set enabled(value) {
    if (!value) this.cancelTransition();
    this._enabled = value;
    this.syncEnabled();
  }
  syncEnabled() {
    this.orbit.enabled = this._enabled && !this.freeRotation && !this.transition;
    this.free.enabled = this._enabled && this.freeRotation && !this.transition;
  }
  get enableDamping() { return this.orbit.enableDamping; }
  set enableDamping(value) { this.orbit.enableDamping = value; }
  set dampingFactor(value) { this.orbit.dampingFactor = value; }
  set minDistance(value) { this.orbit.minDistance = this.free.minDistance = value; }
  set maxDistance(value) { this.orbit.maxDistance = this.free.maxDistance = value; }
  set screenSpacePanning(value) { this.orbit.screenSpacePanning = value; }
  getDistance() { return this.camera.position.distanceTo(this.target); }
  update() { this.active.update(); }
  flush() {
    const damping = this.orbit.enableDamping;
    this.orbit.enableDamping = false;
    this.active.update();
    this.orbit.enableDamping = damping;
  }
  cancelTransition() {
    if (!this.transition) return;
    this.transition = null;
    this.syncEnabled();
    this.dispatchEvent({type:'end'});
  }
  setFree(value, {notify=true}={}) {
    this.cancelTransition();
    this.flush();
    this.freeRotation = !!value;
    if (!this.freeRotation) this.camera.up.set(0, 0, 1);
    this.free.handleResize();
    this.syncEnabled();
    this.update();
    if (notify) this.dispatchEvent({type:'end'});
  }
  moveTo(position, target, {up=this.camera.up, smooth=true}={}) {
    this.cancelTransition();
    this.flush();
    const endCamera = this.camera.clone();
    endCamera.position.copy(position);
    endCamera.up.copy(up);
    endCamera.lookAt(target);
    const duration = smooth && !matchMedia('(prefers-reduced-motion: reduce)').matches ? 380 : 0;
    if (!duration) {
      this.camera.position.copy(position);
      this.target.copy(target);
      this.camera.up.copy(up);
      this.camera.lookAt(target);
      this.update();
      this.dispatchEvent({type:'end'});
      return;
    }
    this.transition = {start:performance.now(), duration, fromTarget:this.target.clone(), target:target.clone(),
      fromPosition:this.camera.position.clone(), position:position.clone(), fromQuaternion:this.camera.quaternion.clone(),
      quaternion:endCamera.quaternion.clone(), up:up.clone()};
    this.syncEnabled();
  }
  tick(now) {
    const t = this.transition;
    if (!t) { if (this.enabled) this.update(); return; }
    const progress = Math.min(1, (now - t.start) / t.duration);
    const ease = progress * progress * (3 - 2 * progress);
    this.target.lerpVectors(t.fromTarget, t.target, ease);
    // Interpolate direction as a rotation, so opposite views never pass through the model.
    const q = new THREE.Quaternion().slerpQuaternions(t.fromQuaternion, t.quaternion, ease);
    const distance = THREE.MathUtils.lerp(t.fromPosition.distanceTo(t.fromTarget), t.position.distanceTo(t.target), ease);
    this.camera.position.copy(this.target).add(new THREE.Vector3(0, 0, distance).applyQuaternion(q));
    this.camera.quaternion.copy(q);
    this.camera.up.set(0, 1, 0).applyQuaternion(q);
    if (progress === 1) {
      this.camera.position.copy(t.position);
      this.camera.up.copy(t.up);
      this.transition = null;
      this.syncEnabled();
      this.update();
      this.dispatchEvent({type:'end'});
    }
  }
}

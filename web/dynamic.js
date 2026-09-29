// Keep shared scene time and the displayed reference sample with each piece of evidence.
export function frameAtTime(frames, time) {
  if (!frames?.length) return null;
  let low = 0, high = frames.length - 1;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (frames[middle].time_sec <= time + 1e-7) low = middle;
    else high = middle - 1;
  }
  return frames[low];
}

// Synchronized views can have different sampling rates. Match the shared
// scene time to the closest available frame without moving that shared clock.
export function nearestFrameAtTime(frames, time) {
  const before = frameAtTime(frames, time);
  if (!before) return null;
  const index = frames.indexOf(before);
  const after = frames[index + 1];
  return after && Math.abs(after.time_sec - time) < Math.abs(before.time_sec - time) - 1e-7 ? after : before;
}

function cameraMatches(left, right) {
  if (!left || !right) return false;
  const matrix = (camera) => Array.isArray(camera.camera_to_world) && camera.camera_to_world.length === 4 &&
    camera.camera_to_world.every((row) => Array.isArray(row) && row.length === 4) ? camera.camera_to_world.flat() : null;
  const a = matrix(left), b = matrix(right);
  const close = (x,y) => Number.isFinite(x) && Number.isFinite(y) && Math.abs(x-y) <= 1e-6;
  if (!a || !b || !a.every((value,index) => close(value,b[index]))) return false;
  const intrinsic = (camera) => {
    const i = camera.intrinsics;
    if (!i || !(i.width > 0) || !(i.height > 0)) return null;
    return [i.width/i.height, i.fx/i.width, i.fy/i.height, i.cx/i.width, i.cy/i.height];
  };
  const ai = intrinsic(left), bi = intrinsic(right);
  if (!ai || !bi || !ai.every((value,index) => close(value,bi[index]))) return false;
  const ad = left.distortion || [], bd = right.distortion || [];
  if (!Array.isArray(ad) || !Array.isArray(bd)) return false;
  for (let index=0; index < Math.max(ad.length,bd.length); index++) {
    if (!close(ad[index] ?? 0,bd[index] ?? 0)) return false;
  }
  return true;
}

// A calibrated thumbnail can select its corresponding video even when the
// thumbnail and video frames have different resolutions. Ambiguous cameras
// remain static references rather than guessing which clip the user intended.
export function viewForReferenceImage(views, reference) {
  if (!reference?.camera) return null;
  const matches = views.filter((view) => view.frames?.some((frame) => cameraMatches(reference.camera,frame.camera)));
  return matches.length === 1 ? matches[0] : null;
}

export function stepTime(frames, time, direction, fps, duration) {
  if (frames?.length) {
    const index = frames.indexOf(frameAtTime(frames, time));
    return frames[Math.max(0, Math.min(frames.length - 1, index + direction))].time_sec;
  }
  return Math.max(0, Math.min(duration, Math.round((time + direction / fps) * fps) / fps));
}

export function feedbackScope(kind, time, start, end, duration) {
  if (kind === 'range') {
    if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start || end > duration + 1e-7) {
      throw new Error('时间范围需满足：0 ≤ 起点 < 终点 ≤ 片段时长。');
    }
    return {kind, start_sec:start, end_sec:end};
  }
  return {kind:kind === 'clip' ? 'clip' : 'frame'};
}

export function markMatchesMoment(mark, moment) {
  return mark.frame_id === moment.id || (mark.pane === 'scene' && mark.snapshot_id === moment.id);
}

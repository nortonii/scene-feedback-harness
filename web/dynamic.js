// All evidence uses the displayed reference timestamp, including irregular sequences.
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

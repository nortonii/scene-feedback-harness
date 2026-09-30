export const COCO_EDGES = [[0,1],[0,2],[1,3],[2,4],[5,6],[5,7],[7,9],[6,8],[8,10],[5,11],[6,12],[11,12],[11,13],[13,15],[12,14],[14,16]];
export const POSE_COLORS = ['#087f8c','#b65320','#6856aa','#287a46','#be4066'];

export function rectangleBBox(coordinates) {
  const values = [coordinates?.x, coordinates?.y, coordinates?.x2, coordinates?.y2];
  if (!values.every(Number.isFinite)) return null;
  const [x,y,x2,y2] = values.map((value) => Math.max(0, Math.min(1, value)));
  const bbox = [Math.min(x,x2), Math.min(y,y2), Math.abs(x2-x), Math.abs(y2-y)];
  return validBBox(bbox) ? bbox : null;
}
export function validBBox(bbox) {
  return Array.isArray(bbox) && bbox.length === 4 && bbox.every(Number.isFinite) &&
    bbox[0] >= 0 && bbox[1] >= 0 && bbox[2] > 0.01 && bbox[3] > 0.01 &&
    bbox[0]+bbox[2] <= 1.000001 && bbox[1]+bbox[3] <= 1.000001;
}
export function sampledPoseFrameCount(frames, start, end, fps) {
  if (![start,end,fps].every(Number.isFinite) || end < start || fps <= 0) return 0;
  let count=0, nextTime=-Infinity;
  for (const frame of Array.isArray(frames) ? frames : []) {
    if (!Number.isFinite(frame?.time_sec) || frame.time_sec < start-1e-6 || frame.time_sec > end+1e-6) continue;
    if (frame.time_sec+1e-6 >= nextTime) { count++; nextTime=frame.time_sec+1/fps; }
  }
  return count;
}
export function firstPoseFrame(view, start) {
  return Array.isArray(view?.frames) && Number.isFinite(start)
    ? view.frames.find((frame) => Number.isFinite(frame?.time_sec) && frame.time_sec >= start-1e-6) || null
    : null;
}
export function sampledPoseViewCount(views, start, end, fps) {
  return Array.isArray(views) ? views.reduce((sum, view) => sum + sampledPoseFrameCount(view?.frames, start, end, fps), 0) : 0;
}
export function poseToken(jobId, referenceId) {
  if (![jobId, referenceId].every((value) => typeof value === 'string' && /^[0-9a-f]{32}$/.test(value))) return null;
  return `[[pose:${jobId}:${referenceId}]]`;
}
export function collectPoseReferences(note, candidates, limit=8) {
  const pattern = /\[\[pose:([0-9a-f]{32}):([0-9a-f]{32})\]\]/g;
  const tokens = [...note.matchAll(pattern)];
  const starts = new Set(tokens.map((match) => match.index));
  for (const match of note.matchAll(/\[\[pose:/g)) {
    if (!starts.has(match.index)) throw new Error('提示里的人体引用不完整，请重新点击「引用人体」。');
  }
  const refs = [], seen = new Set();
  for (const [,jobId,referenceId] of tokens) {
    const key = jobId + ':' + referenceId;
    if (seen.has(key)) continue;
    if (!candidates.some((item) => item.job_id === jobId && item.reference_id === referenceId)) {
      throw new Error('提示里的人体引用已失效，请重新点击「引用人体」。');
    }
    seen.add(key); refs.push({job_id:jobId, reference_id:referenceId});
    if (refs.length > limit) throw new Error('一条反馈最多引用 ' + limit + ' 个人体结果。');
  }
  return refs;
}
export function poseFrameForReference(job, referenceId, viewId, time) {
  if (!Array.isArray(job?.frames) || !job.frames.length) return null;
  const viewIds = Array.isArray(job.view_ids) && job.view_ids.length ? job.view_ids : job.view_id ? [job.view_id] : [];
  if (!viewIds.length) return job.reference_id === referenceId
    ? job.frames.find((frame) => frame.reference_id === referenceId) || null : null;
  if (!viewId || !viewIds.includes(viewId) || !Number.isFinite(time)) return null;
  const frames = job.frames.filter((frame) => frame.view_id === viewId || !job.multi_view && !frame.view_id);
  if (!frames.length) return null;
  /* A multi-camera job stores a flat list of frames; each camera has its own
     independent 2D track and must never inherit another camera's skeleton. */
  const exact = frames.find((frame) => frame.reference_id === referenceId);
  if (exact) return exact;
  const sampled = frames.filter((frame) => Number.isFinite(frame.time_sec));
  let nearest = null;
  for (const frame of sampled) {
    if (!nearest || Math.abs(frame.time_sec-time) < Math.abs(nearest.time_sec-time)) nearest = frame;
  }
  if (!nearest) return null;
  const ordered = [...sampled].sort((a,b) => a.time_sec-b.time_sec);
  const first=ordered[0], last=ordered.at(-1);
  const firstGap=ordered.length > 1 ? ordered[1].time_sec-first.time_sec : 1/(job.sample_fps || 5);
  const lastGap=ordered.length > 1 ? last.time_sec-ordered.at(-2).time_sec : 1/(job.sample_fps || 5);
  return time >= first.time_sec-firstGap/2-1e-6 && time <= last.time_sec+lastGap/2+1e-6 ? nearest : null;
}
export function poseFrameLabel(frame, fallback='参考图') {
  const parts = [frame.view_name || fallback];
  if (Number.isInteger(frame.frame_index)) parts.push('第 ' + (frame.frame_index+1) + ' 帧');
  if (Number.isFinite(frame.time_sec)) parts.push(frame.time_sec.toFixed(3) + ' s');
  return parts.join(' · ');
}
export function drawPoseSkeleton(context, frame, width, height, {color=POSE_COLORS[0], threshold=0.3, edges=COCO_EDGES, label='', fontFamily='sans-serif'}={}) {
  context.save();
  context.strokeStyle=color; context.fillStyle=color; context.lineWidth=2;
  const bbox = validBBox(frame.bbox) ? frame.bbox : null;
  const lost = frame.tracking_status === 'lost';
  if (bbox) {
    context.setLineDash(lost ? [5,4] : [3,3]);
    context.strokeRect(bbox[0]*width,bbox[1]*height,bbox[2]*width,bbox[3]*height);
    context.setLineDash([]);
  }
  const points = Array.isArray(frame.keypoints) ? frame.keypoints : [];
  const usable = (point) => !lost && point?.in_frame !== false && Number.isFinite(point?.x) && Number.isFinite(point?.y) &&
    Number.isFinite(point?.score) && point.score >= threshold && point.score <= 1 && point.x >= 0 && point.x <= 1 && point.y >= 0 && point.y <= 1;
  for (const edge of Array.isArray(edges) ? edges : COCO_EDGES) {
    if (!Array.isArray(edge) || edge.length !== 2 || !edge.every(Number.isInteger)) continue;
    const [a,b]=edge.map((index) => points[index]);
    if (!usable(a) || !usable(b)) continue;
    context.beginPath(); context.moveTo(a.x*width,a.y*height); context.lineTo(b.x*width,b.y*height); context.stroke();
  }
  for (const point of points) {
    if (!usable(point)) continue;
    context.beginPath(); context.arc(point.x*width,point.y*height,3,0,Math.PI*2); context.fill();
  }
  if (label) {
    const x = Math.max(4, Math.min(width-4,(bbox?.[0] || 0)*width));
    const y = Math.max(17,(bbox?.[1] || 0)*height-5);
    const text = label + (lost ? ' · 未找到人物' : '');
    context.font='11px ' + fontFamily;
    const textWidth=Math.min(width-8,context.measureText(text).width+8);
    const left=Math.min(x,Math.max(4,width-textWidth-4));
    context.fillStyle='#f5f4efed'; context.fillRect(left-3,y-13,textWidth,17);
    context.fillStyle=color; context.fillText(text,left,y,Math.max(1,width-left-4));
  }
  context.restore();
}

export const COCO_EDGES = [[0,1],[0,2],[1,3],[2,4],[5,6],[5,7],[7,9],[6,8],[8,10],[5,11],[6,12],[11,12],[11,13],[13,15],[12,14],[14,16]];
export const POSE_COLORS = ['#087f8c','#b65320','#6856aa','#287a46','#be4066'];

export function poseEvidenceLabel(result) {
  if (result?.evidence_kind === 'manual_2d') return '人工二维修正';
  if (result?.evidence_kind === 'projected_3d') return '三维投影参考';
  const kind=result?.provenance?.kind;
  const inferred=kind === 'image_inference' || (!kind && /vitpose/i.test(result?.model?.name || ''));
  return inferred ? '二维估计' : '二维观测';
}

export function handJointIndices(names, side) {
  if (!Array.isArray(names) || !['left','right'].includes(side)) return [];
  return names.flatMap((name,index) => typeof name === 'string' &&
    new RegExp('^' + side + '_(?:hand_root|thumb[1-4]|forefinger[1-4]|middle_finger[1-4]|ring_finger[1-4]|pinky_finger[1-4])$').test(name) ? [index] : []);
}
export function handJointLabel(name) {
  const side = name?.startsWith('left_') ? '左手' : '右手';
  const suffix = String(name || '').replace(/^(left|right)_/,'');
  if (suffix === 'hand_root') return side + ' · 手腕';
  const match = /^(thumb|forefinger|middle_finger|ring_finger|pinky_finger)([1-4])$/.exec(suffix);
  return match ? side + ' · ' + ({thumb:'拇指',forefinger:'食指',middle_finger:'中指',ring_finger:'无名指',pinky_finger:'小指'})[match[1]] +
    (match[2] === '4' ? '指尖' : '关节 ' + match[2]) : name;
}
export function poseEditToken(editId) { return /^[0-9a-f]{32}$/.test(editId || '') ? `[[pose_edit:${editId}]]` : null; }
export function validPoseEdit(sample) {
  return sample && poseEditToken(sample.id) && /^[0-9a-f]{32}$/.test(sample.job_id || '') &&
    /^[0-9a-f]{32}$/.test(sample.reference_id || '') && /^[0-9a-f]{64}$/.test(sample.image_sha256 || '') &&
    sample.image_orientation === 'exif_oriented_display' && typeof sample.keypoint_profile === 'string' &&
    Array.isArray(sample.edits) && sample.edits.length > 0 && sample.edits.length <= 256 &&
    new Set(sample.edits.map((point) => point?.name)).size === sample.edits.length && sample.edits.every((point) =>
      typeof point?.name === 'string' && ['visible','occluded','missing'].includes(point.visibility) &&
      (point.visibility === 'missing' ? point.x === undefined && point.y === undefined :
        point.x === undefined && point.y === undefined ? point.visibility === 'occluded' :
          Number.isFinite(point.x) && Number.isFinite(point.y) && point.x >= 0 && point.x <= 1 && point.y >= 0 && point.y <= 1 &&
            (point.visibility !== 'visible' || point.x < 1 && point.y < 1)));
}
export function collectPoseEdits(note,candidates) {
  const tokens=[...note.matchAll(/\[\[pose_edit:([0-9a-f]{32})\]\]/g)], starts=new Set(tokens.map((match) => match.index));
  for (const match of note.matchAll(/\[\[pose_edit:/g)) if (!starts.has(match.index)) throw new Error('手部修正引用不完整，请重新点击「引用修正」。');
  const ids=[...new Set(tokens.map((match) => match[1]))];
  if (ids.length > 8) throw new Error('一条提示最多引用 8 帧关键点修正。');
  return ids.map((id) => {
    const found=candidates.filter((sample) => sample.id === id);
    if (found.length !== 1 || !validPoseEdit(found[0])) throw new Error('关键点修正引用已失效，请重新修正或引用。');
    const sample=found[0];
    return {id:sample.id,job_id:sample.job_id,reference_id:sample.reference_id,image_sha256:sample.image_sha256,
      image_orientation:sample.image_orientation,keypoint_profile:sample.keypoint_profile,edits:structuredClone(sample.edits)};
  });
}
export function correctedPoseFrame(frame,sample) {
  if (!sample) return frame;
  const keypoints=(frame.keypoints || []).map((point) => {
    const edit=sample.edits.find((item) => item.name === point.name);
    return !edit ? point : {...point,...(edit.x !== undefined ? {x:edit.x,y:edit.y} : {}),
      manual_visibility:edit.visibility,manual_source:'manual_2d',manual_position:edit.x !== undefined,in_frame:edit.visibility === 'visible'};
  });
  return {...frame,keypoints};
}

export function validBBox(bbox) {
  return Array.isArray(bbox) && bbox.length === 4 && bbox.every(Number.isFinite) &&
    bbox[0] >= 0 && bbox[1] >= 0 && bbox[2] > 0.01 && bbox[3] > 0.01 &&
    bbox[0]+bbox[2] <= 1.000001 && bbox[1]+bbox[3] <= 1.000001;
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
export function drawPoseSkeleton(context, frame, width, height, {color=POSE_COLORS[0], threshold=0.3, edges=COCO_EDGES}={}) {
  context.save();
  context.strokeStyle=color; context.fillStyle=color; context.lineWidth=2;
  const lost = frame.tracking_status === 'lost' && !frame.keypoints?.some((point) => point.manual_visibility === 'visible');
  const points = Array.isArray(frame.keypoints) ? frame.keypoints : [];
  const usable = (point) => point?.in_frame !== false && Number.isFinite(point?.x) && Number.isFinite(point?.y) &&
    point.x >= 0 && point.x <= 1 && point.y >= 0 && point.y <= 1 && (point.manual_visibility !== undefined
      ? point.manual_visibility === 'visible' : !lost && Number.isFinite(point.score) && point.score >= threshold && point.score <= 1);
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
  context.restore();
}

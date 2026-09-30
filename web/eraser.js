// Hit-test a swept, screen-sized eraser against vector marks. Empty rectangle
// interiors are not marks; fast pointer movement must still hit crossed strokes.
export const ERASER_RADIUS = 10;

function pointSegmentDistance(p, a, b) {
  const dx = b.x - a.x, dy = b.y - a.y;
  const length = dx * dx + dy * dy;
  const t = length ? Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / length)) : 0;
  return Math.hypot(p.x - a.x - t * dx, p.y - a.y - t * dy);
}
function segmentDistance(a, b, c, d) {
  const cross = (p, q, r) => (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x);
  const abC = cross(a,b,c), abD = cross(a,b,d), cdA = cross(c,d,a), cdB = cross(c,d,b);
  if (abC * abD < 0 && cdA * cdB < 0) return 0;
  return Math.min(pointSegmentDistance(a,c,d), pointSegmentDistance(b,c,d),
    pointSegmentDistance(c,a,b), pointSegmentDistance(d,a,b));
}
export function eraserHitsAnnotation(mark, from, to, {width, height, scale=1, textWidth=0, radius=ERASER_RADIUS}) {
  const pixel = p => ({x:Math.max(0, Math.min(1, Number(p.x) || 0)) * width,
    y:Math.max(0, Math.min(1, Number(p.y) || 0)) * height});
  const a = {x:from.x * width, y:from.y * height}, b = {x:to.x * width, y:to.y * height};
  const start = pixel(mark.coordinates || {});
  const end = pixel({x:mark.coordinates?.x2, y:mark.coordinates?.y2});
  const hitsLine = (p,q) => segmentDistance(a,b,p,q) <= radius + 1.5 * scale;
  const hitsBox = (x,y,w,h,filled=false) => {
    const corners = [{x,y},{x:x+w,y},{x:x+w,y:y+h},{x,y:y+h}];
    const inside = p => p.x >= Math.min(x,x+w) && p.x <= Math.max(x,x+w) && p.y >= Math.min(y,y+h) && p.y <= Math.max(y,y+h);
    return (filled && (inside(a) || inside(b))) || corners.some((p,i) => hitsLine(p,corners[(i+1)%4]));
  };
  if (mark.group_id && hitsBox(start.x-5*scale,start.y-30*scale,26*scale,23*scale,true)) return true;
  if (mark.type === 'point') return pointSegmentDistance(start,a,b) <= radius + 11.5 * scale;
  if (mark.type === 'rectangle') return hitsBox(start.x,start.y,end.x-start.x,end.y-start.y);
  if (mark.type === 'text') return hitsBox(start.x,start.y-19*scale,textWidth+12*scale,25*scale,true);
  if (mark.type === 'freehand') {
    return (mark.points || []).some((p,i,points) => i > 0 && hitsLine(pixel(points[i-1]),pixel(p)));
  }
  if (mark.type === 'line' || mark.type === 'arrow') {
    if (hitsLine(start,end)) return true;
    if (mark.type === 'arrow') {
      const angle = Math.atan2(end.y-start.y,end.x-start.x);
      return [-1,1].some(sign => hitsLine(end, {x:end.x-14*scale*Math.cos(angle+sign*Math.PI/6),
        y:end.y-14*scale*Math.sin(angle+sign*Math.PI/6)}));
    }
  }
  return false;
}

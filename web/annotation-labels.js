import { eraserHitsAnnotation } from './eraser.js';

export function annotationNameBox(context, mark, width, height) {
  if (!mark.name) return null;
  const scale = Math.max(1, Math.min(width,height)/550);
  const font = `600 ${Math.round(11*scale)}px ${getComputedStyle(document.body).fontFamily}`;
  context.save(); context.font = font;
  const text = String(mark.name).slice(0,40);
  const boxWidth = Math.min(width-8, context.measureText(text).width+10*scale);
  context.restore();
  const boxHeight = 20*scale;
  const x = Math.max(4, Math.min(width-boxWidth-4, (Number(mark.coordinates?.x)||0)*width + (mark.group_id ? 28 : 14)*scale));
  const y = Math.max(4, Math.min(height-boxHeight-4, (Number(mark.coordinates?.y)||0)*height-25*scale));
  return {x,y,width:boxWidth,height:boxHeight,scale,font,text};
}
export function drawAnnotationName(context, mark, width, height, selected=false) {
  const box = annotationNameBox(context,mark,width,height);
  if (!box) return;
  context.save();
  context.shadowBlur = 0; context.font = box.font; context.textBaseline = 'middle';
  context.fillStyle = selected ? '#171715' : '#f5f4efe8';
  context.beginPath(); context.roundRect(box.x,box.y,box.width,box.height,4*box.scale); context.fill();
  context.fillStyle = selected ? '#f5f4ef' : '#923f32';
  context.fillText(box.text,box.x+5*box.scale,box.y+box.height/2,Math.max(1,box.width-10*box.scale));
  context.restore();
}
export function hitsAnnotationName(context, mark, from, to, {width,height,zoom=1,radius=0}) {
  const box = annotationNameBox(context,mark,width,height);
  if (!box) return false;
  const inside = p => p.x*width >= box.x && p.x*width <= box.x+box.width && p.y*height >= box.y && p.y*height <= box.y+box.height;
  return inside(from) || inside(to) || eraserHitsAnnotation({type:'rectangle',coordinates:{x:box.x/width,y:box.y/height,x2:(box.x+box.width)/width,y2:(box.y+box.height)/height}},from,to,{width:width*zoom,height:height*zoom,scale:0,radius});
}

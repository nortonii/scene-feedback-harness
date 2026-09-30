import test from 'node:test';
import assert from 'node:assert/strict';
import { eraserHitsAnnotation as hit } from '../web/eraser.js';
const view = {width:1000, height:500, scale:1};
const mark = (type,coordinates,extra={}) => ({type, coordinates,...extra});
const point = (x,y) => ({x,y});
test('swept eraser hits fast crossings but leaves rectangle interiors empty', () => {
  const line=mark('line',{x:.5,y:.1,x2:.5,y2:.9});
  assert(hit(line,point(.1,.5),point(.9,.5),view));
  assert(!hit(line,point(.1,.1),point(.2,.2),view));
  const box=mark('rectangle',{x:.2,y:.2,x2:.8,y2:.8});
  assert(!hit(box,point(.5,.5),point(.5,.5),view));
  assert(hit(box,point(.5,.2),point(.5,.2),view));
  assert(hit(box,point(.1,.5),point(.9,.5),view));
});
test('all visible mark forms can be erased, including text and numbered badges', () => {
  for (const type of ['point','line','arrow','rectangle','text','freehand']) {
    const annotation=mark(type,{x:.2,y:.2,x2:.7,y2:.7},{text:'Label',points:[point(.2,.2),point(.4,.7)]});
    assert(hit(annotation,point(.2,.2),point(.2,.2),{...view,textWidth:50}),type);
  }
  assert(hit(mark('text',{x:.2,y:.2}),point(.24,.18),point(.24,.18),{...view,textWidth:50}));
  assert(!hit(mark('text',{x:.2,y:.2}),point(.5,.2),point(.5,.2),{...view,textWidth:50}));
  const badge=mark('line',{x:.2,y:.2,x2:.7,y2:.7},{group_id:'1'});
  assert(hit(badge,point(.205,.15),point(.205,.15),view));
});
test('screen radius stays fixed across sizes and handles zero-length marks', () => {
  const line=mark('line',{x:.5,y:.2,x2:.5,y2:.8});
  for (const width of [340,1000,2000]) {
    assert(hit(line,point(.5+9/width,.5),point(.5+9/width,.5),{...view,width}));
    assert(!hit(line,point(.5+20/width,.5),point(.5+20/width,.5),{...view,width}));
  }
  assert(hit(mark('line',{x:.5,y:.5,x2:.5,y2:.5}),point(.4,.5),point(.6,.5),view));
});

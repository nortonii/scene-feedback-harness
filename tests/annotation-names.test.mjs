import test from 'node:test';
import assert from 'node:assert/strict';
import { assignAnnotationNames } from '../web/annotation-names.js';

const mark = (id, extra = {}) => ({id, type:'point', pane:'scene', ...extra});

test('old drafts gain names shared across references and all saved screenshots', () => {
  const old = [mark('a', {snapshot_id:'first'}), mark('b', {snapshot_id:'second'}),
    mark('c', {pane:'reference'}), mark('d', {type:'rectangle'}), mark('e', {type:'arrow'})];
  const result = assignAnnotationNames(old);
  assert.deepEqual(result.annotations.map(item => item.name), ['点1', '点2', '点3', '方框1', '箭头1']);
  assert(old.every(item => !Object.hasOwn(item, 'name')), 'migration must not mutate history objects');
  const again = assignAnnotationNames(result.annotations, result.counters);
  assert.deepEqual(again, result);
  result.annotations.forEach((item, index) => assert.equal(again.annotations[index], item));
});

test('deletion, undo, clear and reload never reuse issued numbers', () => {
  const first = assignAnnotationNames([mark('a'), mark('b')]);
  const removed = assignAnnotationNames([first.annotations[0]], first.counters);
  const third = assignAnnotationNames([...removed.annotations, mark('c')], removed.counters);
  assert.equal(third.annotations[1].name, '点3');
  const restored = assignAnnotationNames(first.annotations, third.counters);
  assert.deepEqual(restored.annotations.map(item => item.name), ['点1', '点2']);
  const cleared = assignAnnotationNames([], restored.counters);
  const reloaded = assignAnnotationNames([mark('d')], JSON.parse(JSON.stringify(cleared.counters)));
  assert.equal(reloaded.annotations[0].name, '点4');
  assert.equal(assignAnnotationNames([mark('other-session')]).annotations[0].name, '点1');
});

test('migration reserves later saved names and repairs duplicates or damaged metadata', () => {
  const result = assignAnnotationNames([
    mark('old'), mark('named', {name:'点4'}), mark('duplicate', {name:'点4'}),
    mark('custom', {name:'入口'}), mark('invalid', {name:'\n'}),
    mark('arrow', {type:'arrow'}), mark('pen', {type:'freehand'}), mark('text', {type:'text'})
  ], {point:-1, arrow:'3', freehand:NaN, text:7, unknown:200});
  assert.deepEqual(result.annotations.map(item => item.name),
    ['点5', '点4', '点6', '入口', '点7', '箭头1', '笔迹1', '文字8']);
  assert.equal(new Set(result.annotations.map(item => item.name)).size, result.annotations.length);
  assert.equal(result.counters.point, 7);
  assert(!Object.hasOwn(result.counters, 'unknown'));
});


test('damaged counters near integer precision limits cannot stall migration', () => {
  for (const point of [1e9 + 1, Number.MAX_SAFE_INTEGER - 1, Number.MAX_SAFE_INTEGER,
    Number.MAX_SAFE_INTEGER + 1, Infinity]) {
    const saved = mark('saved', {name:'点9007199254740992'});
    const result = assignAnnotationNames([saved, mark('a'), mark('b'), mark('c')], {point});
    assert.equal(result.annotations[0], saved, 'existing evidence names stay intact');
    assert.deepEqual(result.annotations.slice(1).map(item => item.name), ['点1', '点2', '点3']);
    assert.equal(result.counters.point, 3);
  }
});

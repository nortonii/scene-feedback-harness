import test from 'node:test';
import assert from 'node:assert/strict';
import {createPromptReferenceText} from '../web/prompt-reference-text.js';
import {referenceAliasLabel} from '../web/prompt-reference-icons.js';
import {findPromptQueryRange} from '../web/prompt-mentions.js';

const object='[[object:cabinet]]', point='[[annotation:point_a]]', image='[[image:saved_a]]';
const sources={
  [object]:{name:'柜子',kind:'object',label:'current / Kitchen_Cabinet',title:'场景中的柜子'},
  [point]:{name:'点1',kind:'annotation',label:'场景 · 截图 1 · 点1'},
  [image]:{name:'参考图',kind:'image',label:'参考图 · original.png · 第 120 帧 · 5.000 s'}
};
const create=()=>createPromptReferenceText({resolve:token=>sources[token] || null});

test('compact prose retains positions and expands short names with exact tokens',()=>{
  const codec=create();
  const canonical=`请把 ${sources[object].label} ${object} 移到\n${sources[point].label} ${point} 的位置；参照 ${sources[image].label} ${image}。`;
  const visible='请把 【柜子】 移到\n【点1】 的位置；参照 【图1】。';
  assert.equal(codec.compact(canonical),visible);
  assert.equal(codec.expand(visible),`请把 【柜子】 ${object} 移到\n【点1】 ${point} 的位置；参照 【图1】 ${image}。`);
  assert.equal(codec.compact(codec.expand(visible)),visible);
  assert.equal(codec.expand(codec.expand(visible)),codec.expand(visible));
  assert.deepEqual(codec.entries(visible).map(item=>item.token),[object,point,image]);
});

test('adjacent and repeated references remain distinct and entries follow first occurrence',()=>{
  const codec=create();
  assert.equal(codec.compact(`${image} [[image:saved_b]] ${object} ${image}`),'【图1】 【图2】 【柜子】 【图1】');
  assert.deepEqual(codec.entries('【柜子】 【图2】 【图1】 【柜子】').map(item=>item.token),[object,'[[image:saved_b]]',image]);
  assert.equal(codec.compact(`【柜子】 and ${image}`),'【柜子】 and 【图1】');
});

test('same names receive stable suffixes even after one reference is removed',()=>{
  const codec=createPromptReferenceText({resolve:()=>({name:'柜子'})});
  assert.equal(codec.remember('首个柜子',object).alias,'【柜子】');
  assert.equal(codec.remember('第二个柜子','[[object:b]]').alias,'【柜子2】');
  assert.equal(codec.remove('把 【柜子】 放到 【柜子2】 旁，再看【柜子】。',object),'把  放到 【柜子2】 旁，再看。');
  assert.equal(codec.remember('另一个柜子','[[object:c]]').alias,'【柜子3】');
  assert.equal(codec.remember('重命名',object).alias,'【柜子】');
});

test('a saved draft restores aliases and omits large or untrusted descriptor properties',()=>{
  const codec=createPromptReferenceText({resolve:()=>({name:'截图',label:'完整来源',original_data_url:'data:image/jpeg;base64,'+'A'.repeat(2000),
    camera:{position:[1,2,3]},time_sec:1.25})});
  const entry=codec.remember('原图',image);
  assert.equal(entry.time_sec,1.25);
  assert.equal(entry.original_data_url,undefined);
  const records=JSON.parse(JSON.stringify(codec.exportRecords()));
  assert.equal(records[0].camera,undefined);
  assert.equal(records[0].time_sec,undefined);
  const restored=createPromptReferenceText(); restored.reset(records);
  assert.equal(restored.expand('继续改【图1】'),`继续改【图1】 ${image}`);
  assert.equal(restored.entries('【图1】')[0].label,'完整来源');
  assert.equal(restored.remember('新截图','[[image:next]]').alias,'【图2】');
  records[0].labels.push('外部修改');
  assert(!restored.exportRecords()[0].labels.includes('外部修改'));
});

test('old long labels and updated source labels migrate without deleting user prose',()=>{
  let source={name:'柜子',label:'旧来源 / 长名称'};
  const codec=createPromptReferenceText({resolve:()=>source});
  codec.remember('旧来源 / 长名称',object);
  source={name:'新柜子',label:'新来源 / 改名'};
  codec.remember('新来源 / 改名',object);
  assert.equal(codec.compact(`说明 旧来源 / 长名称 ${object} 然后 新来源 / 改名 ${object}`),'说明 【柜子】 然后 【柜子】');
  assert.equal(codec.entries('【柜子】')[0].name,'新柜子');
  const fresh=create();
  assert.equal(fresh.compact(`用户自己的完整描述 ${object}`),'用户自己的完整描述 【柜子】');
  assert.equal(fresh.compact(`【历史名】 ${object}`),'【柜子】');
});

test('a canonical short alias can restore itself, but collisions cannot point to another source',()=>{
  const codec=createPromptReferenceText();
  assert.equal(codec.compact(`【沙发】 ${object}`),'【沙发】');
  assert.equal(codec.compact('【沙发】 [[object:other]]'),'【物体】');
  assert.equal(codec.expand('【沙发】 【物体】'),`【沙发】 ${object} 【物体】 [[object:other]]`);
  assert.equal(codec.compact(`【图9】 ${image}`),'【图9】');
  assert.equal(codec.remember('下一张','[[image:next]]').alias,'【图10】');
});

test('missing sources retain exact validation tokens while malformed tokens stay untouched',()=>{
  const codec=createPromptReferenceText();
  assert.equal(codec.compact(`未恢复的来源 ${image}`),'未恢复的来源 【图1】');
  assert.equal(codec.expand('未恢复的来源 【图1】'),`未恢复的来源 【图1】 ${image}`);
  const invalid=['[[unknown:id]]','[[image:bad space]]','[[node:no_path]]','[[object:part:1]]','[[image:'+ 'a'.repeat(65)+']]','[[node:a:'+ '1'.repeat(600)+']]','[[pose:abc:def]]','[[pose_edit:abc]]','[[[image:abc]]]','[[unknown:[[image:abc]]'];
  for (const value of invalid) { assert.equal(codec.compact(value),value); assert.equal(codec.expand(value),value); }
  assert.equal(codec.remember('x','[[bogus:1]]'),null);
});

test('Unicode names stay readable and bounded, including emoji graphemes',()=>{
  const codec=createPromptReferenceText({resolve:()=>({name:'🏠家庭客厅非常非常长的柜子名称'})});
  assert.equal(codec.remember('长物体',object).alias,'【🏠家庭客厅非常非常长的…】');
  const family=createPromptReferenceText({resolve:()=>({name:'👨‍👩‍👧‍👦家庭'})});
  assert.equal(family.remember('家庭',object).alias,'【👨‍👩‍👧‍👦家庭】');
  assert.equal(family.expand('【👨‍👩‍👧‍👦家庭】'),`【👨‍👩‍👧‍👦家庭】 ${object}`);
});

test('image numbers are stable, reserve collisions, and restart only on session reset',()=>{
  const codec=createPromptReferenceText({resolve:()=>({name:'图1'})});
  codec.remember('一个物体',object);
  assert.equal(codec.remember('首图',image).alias,'【图2】');
  codec.remove('【图2】',image);
  assert.equal(codec.remember('再引用',image).alias,'【图2】');
  assert.equal(codec.remember('第二图','[[image:b]]').alias,'【图3】');
  codec.reset();
  assert.equal(codec.expand('【图2】'),'【图2】');
  assert.equal(codec.remember('新会话',image).alias,'【图1】');
});

test('partial, nested and unregistered aliases never become active references',()=>{
  const codec=create(); codec.remember(sources[image].label,image);
  for (const text of ['【图','图1】','【图1','【图2】','【【图1】】','[[invalid:【图1】]]']) {
    assert.equal(codec.expand(text),text);
    assert.deepEqual(codec.entries(text),[]);
  }
  assert.equal(codec.remove('【图1】 【图1】',image),' ');
  assert.equal(codec.remove(`文字 ${sources[image].label} ${image} 仍在`,image),'文字  仍在');
});

test('draft records reject invalid data and repair duplicate aliases without stealing later names',()=>{
  const codec=createPromptReferenceText();
  codec.reset([null,{token:'[[bad:a]]',alias:'【坏】'},
    {token:object,alias:'【柜子】',name:'柜子'},
    {token:'[[object:b]]',alias:'【柜子】',name:'柜子'},
    {token:'[[object:c]]',alias:'【柜子2】',name:'柜子'},
    {token:image,alias:'【图999999999999999999】',name:'图片'},
    {token:'[[image:b]]',alias:'【图7】',name:'图片'},
    {token:object,alias:'【别名】',name:'坏覆盖'}]);
  const records=codec.exportRecords();
  assert.equal(new Set(records.map(item=>item.alias)).size,5);
  assert.equal(records[0].alias,'【柜子】');
  assert.equal(records[1].alias,'【柜子3】');
  assert.equal(records[2].alias,'【柜子2】');
  assert.equal(records[3].alias,'【图1】');
  assert.equal(records[4].alias,'【图7】');
  assert.equal(codec.remember('新的','[[image:c]]').alias,'【图8】');
});

test('all supported source kinds preserve their exact backend identifiers',()=>{
  const a='a'.repeat(32), b='b'.repeat(32);
  const tokens=['[[node:room:0/2/4]]',`[[pose:${a}:${b}]]`,`[[pose_edit:${a}]]`];
  const codec=createPromptReferenceText();
  const visible=codec.compact(tokens.join('\n'));
  assert.equal(visible,'【部件】\n【人体】\n【修正】');
  assert.equal(codec.expand(visible),tokens.map((token,index)=>['【部件】','【人体】','【修正】'][index]+' '+token).join('\n'));
});

test('raw pasted references are discoverable without rewriting their submission text',()=>{
  const codec=create();
  const raw=`稍后看 ${sources[image].label} ${image} 和 ${point}`;
  assert.deepEqual(codec.entries(raw).map(item=>item.token),[image,point]);
  assert.equal(codec.expand(raw),raw);
  assert.equal(codec.remove(raw,image),'稍后看  和 【点1】');
  const records=codec.exportRecords();
  const restored=createPromptReferenceText({resolve:()=>null});restored.reset(records);
  assert.equal(restored.compact(raw),'稍后看 【图1】 和 【点1】');
  assert.equal(restored.expand('【图1】 【点1】'),`【图1】 ${image} 【点1】 ${point}`);
});

test('source-label suffixes cannot consume the end of unrelated user prose',()=>{
  const codec=createPromptReferenceText({resolve:()=>({name:'柜子',label:'柜子'})});
  assert.equal(codec.compact(`小柜子 ${object}`),'小柜子 【柜子】');
  assert.equal(codec.compact(`我的柜子 ${object}`),'我的柜子 【柜子】');
  assert.equal(codec.compact(`修改：柜子 ${object}`),'修改：【柜子】');
  assert.equal(codec.compact(`修改 柜子 ${object}`),'修改 【柜子】');
});

test('opaque identifiers stay in metadata and use meaningful source-kind aliases',()=>{
  for (const name of ['a'.repeat(32),'123e4567-e89b-12d3-a456-426614174000','node_0123456789abcdef','123456789']) {
    const codec=createPromptReferenceText({resolve:()=>({name,label:name})});
    const entry=codec.remember(name,object);
    assert.equal(entry.alias,'【物体】');
    assert.equal(entry.label,name);
  }
  const codec=createPromptReferenceText({resolve:()=>({name:'KitchenCabinetDoorPanel'})});
  assert.equal(codec.remember('语义名称',object).alias,'【KitchenCabi…】');
});

test('numbered time aliases freeze precise prose and reuse only the same source and time',()=>{
  const codec=createPromptReferenceText({resolve:()=>({name:'Changed live frame'})});
  const text='片段1.500s · 参考1.000s · 侧面 #3';
  const first=codec.rememberTime(text,'view:side;clock:1.5');
  assert.equal(first.alias,'【时间戳1】');
  assert.equal(codec.rememberTime(text,'view:side;clock:1.5'),first);
  assert.equal(codec.rememberTime(text,'view:another;clock:1.5').alias,'【时间戳2】');
  const next=codec.rememberTime('片段2.125000001s · 参考2.000s · 侧面 #5','view:side;clock:1.5');
  assert.equal(next.alias,'【时间戳3】');
  const visible='按【时间戳1】修改，再看【时间戳3】。';
  const expanded=codec.expand(visible);
  assert.equal(expanded,`按【时间戳1】 ${text}修改，再看【时间戳3】 ${next.timeText}。`);
  assert(!expanded.includes('[[time:'));
  assert.equal(codec.expand(expanded),expanded);
  assert.equal(codec.compact(expanded),visible);
  assert.equal(codec.compact('用户自写时间 1.500s · 侧面 #3'),'用户自写时间 1.500s · 侧面 #3');
  assert.equal(codec.remember('',first.token).timeText,text);
});

test('time records survive reload, removal and undo without losing precision or stealing numbers',()=>{
  const codec=createPromptReferenceText({resolve:token=>token==='[[object:time_named_object]]'?{name:'时间戳1'}:sources[token]});
  codec.remember('时间戳1','[[object:time_named_object]]');
  const text='片段0.100000001s · 原机位 #123';
  const first=codec.rememberTime(text,'source-a');
  assert.equal(first.alias,'【时间戳2】');
  const visible='在【时间戳2】改【柜子】';codec.remember('柜子',object);
  const canonical=codec.expand(visible),records=JSON.parse(JSON.stringify(codec.exportRecords()));
  const restored=create();restored.reset(records);
  assert.equal(restored.compact(canonical),visible);
  assert.equal(restored.expand(visible),canonical);
  assert.equal(restored.rememberTime(text,'source-a').alias,'【时间戳2】');
  assert.equal(restored.entries(visible)[0].title,text);
  assert.equal(restored.remove(visible,first.token),'在改【柜子】');
  assert.equal(restored.expand(visible),canonical,'undo can restore the original alias');
  assert.equal(restored.rememberTime('片段0.200s','source-b').alias,'【时间戳3】');
  restored.reset();
  assert.equal(restored.expand('【时间戳2】'),'【时间戳2】');
  assert.equal(restored.rememberTime('片段3.000s','source-c').alias,'【时间戳1】');
});

test('incomplete time records stay literal and long precise descriptions round trip without truncation',()=>{
  const codec=createPromptReferenceText();
  codec.reset([{token:'[[time:t1]]',alias:'【时间戳1】'},{token:'[[time:t2]]',alias:'【时间戳2】',timeText:'\u0000'}]);
  assert.deepEqual(codec.exportRecords(),[]);
  assert.equal(codec.compact('[[time:t1]] 【时间戳1】'),'[[time:t1]] 【时间戳1】');
  assert.equal(codec.rememberTime('', 'empty'),null);
  const description='片段0.000–10.125s · '+('原机位及标记说明'.repeat(240));
  const entry=codec.rememberTime(description,'range-a');
  assert.equal(entry.alias,'【时间戳1】');
  const wire=codec.expand(entry.alias);
  const restored=createPromptReferenceText();restored.reset(JSON.parse(JSON.stringify(codec.exportRecords())));
  assert.equal(restored.expand(entry.alias),wire);
  assert.equal(restored.compact(wire),entry.alias);
  assert.equal(restored.entries(entry.alias)[0].timeText,description);
});

test('numbered composer references keep independent counters and exact identities for every source kind',()=>{
  const codec=createPromptReferenceText({numbered:true,resolve:token=>sources[token] || null});
  const a='a'.repeat(32),b='b'.repeat(32);
  const tokens=[object,'[[object:another]]','[[node:room:0/1]]',point,'[[annotation:line_a]]',image,`[[pose:${a}:${b}]]`,`[[pose_edit:${a}]]`];
  const visible=codec.compact(tokens.join(' '));
  assert.equal(visible,'【物体1】 【物体2】 【部件1】 【标记1】 【标记2】 【图片1】 【人体1】 【修正1】');
  tokens.forEach(token=>assert(codec.expand(visible).includes(token)));
  assert.equal(codec.remember('renamed',object).alias,'【物体1】');
  const time=codec.rememberTime('片段0.125s','current-frame');
  assert.equal(time.alias,'【时间戳1】');
  assert.equal(codec.compact(codec.expand(visible+' '+time.alias)),visible+' '+time.alias);
});

test('numbered restoration migrates only registered old aliases without stealing new image symbols',()=>{
  const codec=createPromptReferenceText({numbered:true});
  codec.reset([{token:object,alias:'【图片1】',name:'图片1',label:'柜子'},
    {token:image,alias:'【图1】',name:'参考图',label:'original.png'},
    {token:point,alias:'【点1】',name:'点1',label:'原图上的点1'}]);
  assert.equal(codec.compact('改【图片1】参考【图1】和【点1】；用户自己的【其他】'),
    '改【物体1】参考【图片1】和【标记1】；用户自己的【其他】');
  assert.equal(codec.expand('新【图片1】'),`新【图片1】 ${image}`);
  assert.equal(codec.compact(`【图片1】 ${object} 与【图1】 ${image}`),'【物体1】 与【图片1】');
  const records=codec.exportRecords(),restored=createPromptReferenceText({numbered:true});restored.reset(records);
  assert.equal(restored.compact(codec.expand('【物体1】 【图片1】 【标记1】')),'【物体1】 【图片1】 【标记1】');
  assert.equal(restored.remember('another image','[[image:other]]').alias,'【图片2】');
  assert.equal(restored.remember('another mark','[[annotation:other]]').alias,'【标记2】');
});

test('icon references number every kind independently and keep exact payloads',()=>{
  const codec=createPromptReferenceText({icons:true,resolve:token=>sources[token]});
  const a='a'.repeat(32),b='b'.repeat(32);
  const tokens=[object,'[[object:another]]','[[node:room:0/2]]',point,image,`[[pose:${a}:${b}]]`,`[[pose_edit:${a}]]`];
  const text=codec.compact(tokens.join(' '));
  assert.equal(text,'🧊1 🧊2 🧩1 📍1 🖼️1 🧍1 ✏️1');
  const time=codec.rememberTime('片段0.100000001s · 正面 #9','clock');
  assert.equal(time.alias,'🕒1');
  const visible=text+' '+time.alias,wire=codec.expand(visible);
  tokens.forEach(token=>assert(wire.includes(token)));
  assert(wire.includes(time.timeText));assert(!wire.includes('[[time:'));
  assert.equal(codec.compact(wire),visible);
  assert.equal(codec.expand(wire),wire);
  assert.equal(codec.remember('renamed',object).alias,'🧊1');
  assert.equal(referenceAliasLabel('🖼️1'),'图片1');
  const restored=createPromptReferenceText({icons:true});restored.reset(codec.exportRecords());
  assert.equal(restored.compact(wire),visible);
  assert.equal(restored.expand(visible),wire);
});

test('icon drafts migrate numbered and named brackets without changing targets or ordinal gaps',()=>{
  const codec=createPromptReferenceText({icons:true});
  const precise='片段0.000000001s · 原始机位 #8';
  codec.reset([{token:object,alias:'【物体8】',label:'柜子'},
    {token:'[[object:other]]',alias:'【图片1】',label:'另一个柜子'},
    {token:image,alias:'【图片1】',label:'original.png'},
    {token:point,alias:'【点1】',label:'原图上的点'},
    {token:'[[time:t1]]',alias:'【时间戳3】',timeText:precise,timeKey:'old-source'}]);
  const visible=codec.compact(`改【物体8】 ${object} 与【图片1】 [[object:other]]，参考【图片1】 ${image} 和【点1】 ${point}，在【时间戳3】 ${precise}；用户自写【其他】。`);
  assert.equal(visible,'改🧊8 与🧊9，参考🖼️1 和📍1，在🕒3；用户自写【其他】。');
  assert.equal(codec.rememberTime(precise,'old-source').alias,'🕒3');
  assert.equal(codec.remember('', '[[object:next]]').alias,'🧊10');
  assert.equal(codec.remember('', '[[image:next]]').alias,'🖼️2');
  const restored=createPromptReferenceText({icons:true});restored.reset(codec.exportRecords());
  assert.equal(restored.compact(codec.expand(visible)),visible);
  assert.equal(restored.expand(visible),codec.expand(visible));
});

test('icon scanning and native edit ranges never mistake ordinal prefixes or nested literals for references',()=>{
  const codec=createPromptReferenceText({icons:true});
  codec.remember('',object);
  const literal='🧊10 🧊100 🧊12345678 🧊0 🧊01 🧊 【🧊1】 [[🧊1]] [[unknown:🧊1]]';
  assert.equal(codec.expand(literal),literal);
  assert.deepEqual(codec.ranges(literal),[]);
  const visible='🙂改🧊1，再看🧊10和🧊1。';
  assert.deepEqual(codec.ranges(visible).map(({start,end})=>[start,end]),[[3,6],[14,17]]);
  assert.equal(codec.remove(visible,object),'🙂改，再看🧊10和。');
  const tenth='[[object:tenth]]';codec.reset([{token:object,alias:'🧊1'},{token:tenth,alias:'🧊10'}]);
  assert.equal(codec.expand('🧊1 🧊10'),`🧊1 ${object} 🧊10 ${tenth}`);
  assert.deepEqual(codec.ranges('🧊1 🧊10').map(item=>item.entry.token),[object,tenth]);
});

test('registered old literal aliases migrate once and cannot hijack later icon references',()=>{
  const codec=createPromptReferenceText({icons:true});
  codec.reset([{token:object,alias:'【🖼️1】',name:'🖼️1'},{token:image,alias:'🖼️1'}]);
  assert.equal(codec.compact('改【🖼️1】参考🖼️1'),'改🧊1参考🖼️1');
  assert.equal(codec.expand('🖼️1'),`🖼️1 ${image}`);
  assert.equal(codec.compact('未注册【🖼️1】'),'未注册【🖼️1】');
});

test('menus can follow a registered icon without treating email, ratios or unknown icons as references',()=>{
  const codec=createPromptReferenceText({icons:true});codec.remember('',object);
  for(const trigger of ['@','/']) {
    const value='🧊1'+trigger+'柜子';
    assert.equal(findPromptQueryRange(value,value.length,value.length,codec.ranges(value))?.trigger,trigger);
    for(const literal of ['🧊10'+trigger+'test','test'+trigger+'example.com','1'+trigger+'2'])
      assert.equal(findPromptQueryRange(literal,literal.length,literal.length,codec.ranges(literal)),null);
  }
});

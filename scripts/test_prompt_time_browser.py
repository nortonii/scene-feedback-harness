#!/usr/bin/env python3
"""Exercise prompt time options with real multi-view frames and isolated feedback."""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'scripts'),str(ROOT/'tests')]
from test_prompt_drag_browser import image_data,tiny_named_glb
from test_prompt_mentions_browser import draw_mark,select_model,ready,canonical
from workspace_ui_helpers import control


def query(page,text='',note=None):
    if not page.locator('#chat-dock').is_visible(): control(page,'#chat-launcher').click()
    field=page.locator('#feedback-note')
    if note is not None: field.fill(note)
    field.focus();field.press('Control+End');field.press_sequentially('/'+text)
    page.wait_for_function("!document.getElementById('prompt-mentions').classList.contains('hidden')")


def choose(page,text):
    option=page.locator('.prompt-mention-option[data-mention-kind="time"]').filter(has_text=text).first
    option.click()
    page.wait_for_function("document.getElementById('prompt-mentions').classList.contains('hidden')")


def candidates(page): return page.evaluate('__mentionCheck.getPromptTimeCandidates()')


def reject_stale(page,candidate):
    return page.evaluate('''c=>{const before=__mentionCheck.ui.note.value;let ok=false;
      try{ok=__mentionCheck.insertPromptTimeCandidate(c);}catch(e){}
      return !ok && before===__mentionCheck.ui.note.value;}''',candidate)


def main():
    args=argparse.ArgumentParser(description=__doc__);args.add_argument('--browser-executable');args=args.parse_args()
    from playwright.sync_api import sync_playwright
    from server import make_server
    from mcp_server import _visual_tool_result
    with tempfile.TemporaryDirectory(prefix='prompt-time-') as folder:
        root=Path(folder);project=root/'project';project.mkdir()
        server=make_server(port=0,data_dir=root/'data',project_dir=project,web_dir=ROOT/'web',external_review=True,feedback_transport='mcp_events')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        store=server.scene_store;session=server.workspace_gateway.ensure()['session_id']
        model=root/'cabinet.glb';tiny_named_glb(model);store.import_model(str(model),object_id='fixture_model',name='模型柜子')
        camera={'camera_to_world':[[1,0,0,0],[0,1,0,0],[0,0,1,3],[0,0,0,1]],'intrinsics':{'width':320,'height':240,'fx':300,'fy':300,'cx':160,'cy':120}}
        front=store.set_reference_clip(session,{'name':'正面 [[object:fixture_model]]','fps':2,'frames':[{'name':f'front{i}.png','data_url':image_data('navy'),'time_sec':i*.5,'camera':camera} for i in range(3)]})['reference_clip']
        clip=store.set_reference_clip(session,{'append_view':True,'name':'侧面 【Door】','fps':2,'frames':[{'name':f'side{i}.png','data_url':image_data('maroon'),'time_sec':i*.5,'camera':camera} for i in range(3)]})['reference_clip']
        side=next(v for v in clip['views'] if v['clip_id']!=front['clip_id'])
        errors=[];posts=[]
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,**({'executable_path':args.browser_executable} if args.browser_executable else {}),args=['--no-sandbox','--no-proxy-server','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader'])
                context=browser.new_context(viewport={'width':1440,'height':950})
                legacy={'feedbackScope':'range','rangeStart':'0','rangeEnd':'1','note':''}
                context.add_init_script('const k='+json.dumps('astra-visual-draft:'+session)+';if(!localStorage.getItem(k))localStorage.setItem(k,'+json.dumps(json.dumps(legacy))+');')
                hook='\nwindow.__mentionCheck={state,ui,promptText,camera,cameraData,renderSelection,renderAnnotations,selectObject,nodeReference,removeAnnotation,undoAnnotationEdit,getPromptMentionCandidates,getPromptTimeCandidates,insertPromptTimeCandidate,promptMentions};'
                context.route('**/app.js',lambda route:route.fulfill(status=200,content_type='application/javascript',body=(ROOT/'web/app.js').read_text()+hook))
                page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                page.on('request',lambda r:posts.append(r.post_data_json) if r.method=='POST' and r.url.endswith('/feedback') else None)
                page.goto(f'http://127.0.0.1:{server.server_port}/');ready(page);field=page.locator('#feedback-note')
                assert page.locator('#feedback-scope,#feedback-range,#range-start,#range-end').count()==0
                assert page.locator('.timeline-options').count()==0
                query(page,note='前面 ')
                assert page.locator('.prompt-mention-option[data-mention-kind="time"]').count()>=2
                choose(page,'当前参考帧')
                assert '正面' in field.input_value() and '0.000' in field.input_value()
                assert '片段参考0.000s' in field.input_value() and '#1' in field.input_value()
                assert '机位「' not in field.input_value() and '第 1 帧' not in field.input_value()
                assert '[[object:' not in canonical(page), 'A source name must not register a prompt object reference'
                assert not posts
                print('PASS: legacy range fields disappear; slash inserts a precise current reference time into prose without submitting',flush=True)

                select_model(page,'part');field.press('Space');field.press_sequentially('@Door')
                page.locator('.prompt-mention-option[data-mention-kind="node"]').click()
                assert '[[node:fixture_model:' in canonical(page) and '【Door】' in field.input_value()
                query(page,'整个');field.press('Control+Enter')
                assert '整个片段' in field.input_value() and '1.500' in field.input_value()
                assert '0.000–1.500s · 全部机位' in field.input_value()
                assert not posts
                first_note=field.input_value()
                print('PASS: time options and compact @ part references coexist; Ctrl+Enter picks an option instead of sending',flush=True)

                first_mark=draw_mark(page,'reference','rectangle')
                current=next(c for c in candidates(page) if c['descriptor']['sourceType']=='reference')
                page.locator('#reference-strip button[data-view-id="' + side['clip_id'] + '"]').first.click()
                page.locator('#timeline-seek').focus();page.locator('#timeline-seek').press('End')
                page.wait_for_function('(id)=>__mentionCheck.state.activeReferenceId===id && !__mentionCheck.state.seeking',arg=side['frames'][-1]['id'])
                assert reject_stale(page,current)
                side_mark=draw_mark(page,'reference','line')
                control(page,'#capture-scene-button').click()
                page.wait_for_function("__mentionCheck.state.sceneView==='snapshot'")
                scene_mark=draw_mark(page,'scene','arrow')
                # Another real camera pose at the same clip time joins the
                # same option. Its screenshot caption must remain visible,
                # while the shared source frame appears just once.
                control(page,'#scene-live-card').click()
                viewport=page.locator('#viewport canvas').bounding_box();assert viewport
                page.mouse.move(viewport['x']+viewport['width']*.5,viewport['y']+viewport['height']*.5);page.mouse.down()
                page.mouse.move(viewport['x']+viewport['width']*.6,viewport['y']+viewport['height']*.56,steps=6);page.mouse.up()
                control(page,'#capture-scene-button').click()
                page.wait_for_function("__mentionCheck.state.sceneView==='snapshot'")
                rows=candidates(page)
                assert all(c['kind']=='time' for c in rows)
                for mark in (first_mark,side_mark,scene_mark):
                    query(page,mark['name'],note=first_note+' ')
                    assert page.locator('.prompt-mention-option').count()>0,mark['name']
                    field.press('Escape')
                scene=next(c for c in rows if c['descriptor']['sourceType']=='snapshot')
                assert '1.500' in scene['label']+' '+scene['detail'] and '1.000' in scene['label']+' '+scene['detail']
                assert '片段1.500s · 参考1.000s' in scene['text'] and '#3' in scene['text']
                assert scene['text'].startswith('截图') and '场景截图（' not in scene['text']
                same_time=next(c for c in rows if c['descriptor']['sourceType']=='round' and len(c['descriptor']['members'])>=2)
                assert same_time['text'].count('参考1.000s')==1 and same_time['text'].count('#3')==1,same_time['text']
                for member in same_time['descriptor']['members']:
                    assert str(int(member['name'].split()[-1])) in same_time['text'],same_time['text']
                assert all(mark['name'].replace(' ','') in same_time['text'] or
                    mark['name'].startswith('标记 ') and mark['name'].split()[-1] in same_time['text']
                    for mark in same_time['descriptor']['marks']),same_time['text']
                current_reference=next(c for c in rows if c['descriptor']['sourceType']=='reference')
                assert current_reference['text'].startswith('片段参考1.000s') and '（场景1.500s）' in current_reference['text']
                query(page,'本轮',note=first_note+' ');choose(page,'本轮标记时段')
                assert '0.000' in field.input_value() and '1.500' in field.input_value()
                assert '片段0.000–1.500s' in field.input_value() and '正面' in field.input_value() and '侧面' in field.input_value()
                frozen=page.evaluate('JSON.stringify({snapshot:__mentionCheck.state.snapshot,camera:__mentionCheck.cameraData(),time:__mentionCheck.state.time,marks:__mentionCheck.state.annotations})')
                query(page,'当前参考帧');choose(page,'当前参考帧')
                assert '侧面' in field.input_value() and '1.000' in field.input_value()
                assert canonical(page).count('[[node:fixture_model:')==1 and '[[object:' not in canonical(page), 'Source names must not expand existing compact aliases or protocol tokens'
                assert page.evaluate('JSON.stringify({snapshot:__mentionCheck.state.snapshot,camera:__mentionCheck.cameraData(),time:__mentionCheck.state.time,marks:__mentionCheck.state.annotations})')==frozen
                draft=field.input_value()
                print('PASS: compact cross-view times preserve exact scene/sample distinctions, endpoints and screenshot captions; duplicate source frames appear once without moving the viewport',flush=True)

                query(page,first_mark['name'],note='')
                old=page.evaluate('__mentionCheck.promptMentions.candidates[0]')
                page.evaluate('(id)=>__mentionCheck.removeAnnotation(id)',first_mark['id'])
                assert page.locator('.prompt-mention-option').count()==0
                assert reject_stale(page,old)
                page.evaluate('__mentionCheck.undoAnnotationEdit()')
                assert page.locator('.prompt-mention-option').count()>0
                field.press('Escape')
                for text in ('https://example.com/path','1/2','/tmp/example','【inside/slash】'):
                    field.fill(text);field.focus();field.press('End')
                    assert page.locator('#prompt-mentions').is_hidden(),text
                query(page,'not-a-time',note='');assert not page.locator('.prompt-mention-option').count()
                field.press('Enter');assert not posts;field.press('Escape')
                query(page,note='');page.dispatch_event('#feedback-note','compositionstart')
                page.dispatch_event('#feedback-note','keydown',{'key':'Enter','code':'Enter','isComposing':True,'keyCode':229,'ctrlKey':True})
                assert field.input_value()=='/' and not posts
                page.dispatch_event('#feedback-note','compositionend',{'data':''});field.press('Escape')
                long='字'*9998+' /';field.fill(long);field.focus();field.press('End');field.press('Enter')
                assert field.input_value()==long and not posts;field.press('Escape')
                print('PASS: removed sources, paths, URLs, fractions, IME and capacity failure cannot insert stale times or send accidentally',flush=True)

                for width in (390,340):
                    page.set_viewport_size({'width':width,'height':920});query(page,note='')
                    box=page.locator('#prompt-mentions').bounding_box();assert box
                    assert box['x']>=-1 and box['x']+box['width']<=width+1 and box['y']>=-1 and box['y']+box['height']<=921
                    field.press('Escape')
                page.set_viewport_size({'width':1440,'height':950});field.fill(draft);field.focus();field.press('End')
                page.reload();ready(page);assert field.input_value()==draft
                stored=page.evaluate('(k)=>JSON.parse(localStorage.getItem(k))','astra-visual-draft:'+session)
                assert not any(k in stored for k in ('feedbackScope','rangeStart','rangeEnd'))
                assert len(page.evaluate('__mentionCheck.state.annotations'))==3
                print('PASS: small-screen menus stay reachable; reload keeps time prose and source evidence while dropping the obsolete range preference',flush=True)

                health={'service':'scene-feedback-harness','status':'ok','snapshot_comparison_supported':True,'scene_snapshots_supported':True,'prompt_time_supported':False}
                def old_health(route):route.fulfill(status=200,content_type='application/json',body=json.dumps(health))
                page.route('**/api/health',old_health);before=len(posts);page.locator('#submit-button').click()
                page.wait_for_function('!__mentionCheck.state.submitting')
                assert len(posts)==before and field.input_value()==draft and not store.state['feedback']
                page.unroute('**/api/health',old_health)
                def reject(route):route.fulfill(status=503,content_type='application/json',body='{"error":"temporary isolated rejection"}')
                page.route('**/api/sessions/*/feedback',reject);page.locator('#submit-button').click()
                page.wait_for_function('!__mentionCheck.state.submitting')
                assert 'scope' not in posts[-1]['timeline'] and field.input_value()==draft and not store.state['feedback']
                page.route('**/api/health',old_health);before=len(posts);page.locator('#submit-button').click()
                page.wait_for_function('!__mentionCheck.state.submitting');assert len(posts)==before
                page.unroute('**/api/health',old_health);page.unroute('**/api/sessions/*/feedback',reject)
                print('PASS: an older service cannot silently restore a range during new submission or a retry; failures retain the exact draft',flush=True)

                page.locator('#submit-button').click();page.wait_for_function('__mentionCheck.state.feedbackCount===1 && !__mentionCheck.state.submitting')
                packet=store.state['feedback'][0]
                assert 'scope' not in packet['timeline'] and packet['note'].strip()==posts[-1]['note'].strip()
                assert '整个片段' in packet['note'] and '本轮标记时段' in packet['note'] and '[[node:fixture_model:' in packet['note']
                assert len(packet['annotations'])==3 and len(packet['dynamic_frames'])>=2
                result=_visual_tool_result({'items':[copy.deepcopy(packet)]},store.data_dir)
                assert 'scope' not in result.structured_content['items'][0]['timeline']
                assert sum(item.type=='image' for item in result.content)>=4
                assert field.input_value()=='' and not page.evaluate('__mentionCheck.state.annotations')
                assert not any(c['descriptor']['sourceType'] in ('round','range','snapshot') for c in candidates(page))
                assert not errors,errors
                print('PASS: real feedback and MCP images retain the chosen time prose and evidence without an implicit scope; success clears the round',flush=True)
                browser.close()
        finally:
            server.shutdown();thread.join(5);server.server_close()
    print('ALL PROMPT TIME BROWSER CHECKS PASSED',flush=True)


if __name__=='__main__':main()

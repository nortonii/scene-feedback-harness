#!/usr/bin/env python3
"""Exercise annotation double clicks and inline details in an isolated workbench."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import threading

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'scripts'),str(ROOT/'tests')]
from server import make_server
from test_object_double_click_browser import model
from test_prompt_drag_browser import image_data
from workspace_ui_helpers import control


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--browser-executable');args=parser.parse_args()
    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory(prefix='reference-interactions-') as temporary:
        root=Path(temporary);project=root/'project';project.mkdir()
        server=make_server(port=0,data_dir=root/'data',project_dir=project,web_dir=ROOT/'web',external_review=True,feedback_transport='mcp_events')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        store=server.scene_store;session=server.workspace_gateway.ensure()['session_id']
        asset=root/'model.glb';model(asset);store.import_model(str(asset),object_id='fixture_model',name='柜子模型')
        camera={'camera_to_world':[[1,0,0,0],[0,1,0,0],[0,0,1,3],[0,0,0,1]],'intrinsics':{'width':320,'height':240,'fx':300,'fy':300,'cx':160,'cy':120}}
        store.set_reference_clip(session,{'name':'正面','fps':2,'frames':[{'name':f'frame{i}.png','data_url':image_data('navy'),'time_sec':i*.5,'camera':camera} for i in range(2)]})
        errors=[]
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,**({'executable_path':args.browser_executable} if args.browser_executable else {}),args=['--no-sandbox','--no-proxy-server','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader'])
                context=browser.new_context(viewport={'width':1440,'height':1000})
                hook='\nwindow.__refs={state,ui,setMode,promptText,promptReferenceText,selectObject,nodeReference,cameraData,quoteObjectReference,selectedPromptReference};'
                context.route('**/app.js',lambda route:route.fulfill(status=200,content_type='application/javascript',body=(ROOT/'web/app.js').read_text()+hook))
                page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(f'http://127.0.0.1:{server.server_port}/')
                page.wait_for_function("window.__refs && __refs.state.workspaceReady && !__refs.state.sceneLoading && !__refs.state.seeking && document.getElementById('reference-image').naturalWidth>0")
                note=page.locator('#feedback-note')
                def ready():page.wait_for_function("window.__refs && __refs.state.workspaceReady && !__refs.state.sceneLoading && !__refs.state.seeking && !__refs.state.submitting")
                def evidence():return page.evaluate('JSON.stringify({camera:__refs.cameraData(),time:__refs.state.time,reference:__refs.state.activeReferenceId,snapshot:__refs.state.snapshot,marks:__refs.state.annotations})')
                def point(canvas,x=.32,y=.38):
                    rect=page.locator(canvas).bounding_box();assert rect
                    return {'x':rect['x']+rect['width']*x,'y':rect['y']+rect['height']*y}
                def draw(pane):
                    page.evaluate("pane=>__refs.setMode('point',pane)",pane)
                    p=point('#'+pane+'-annotations');page.mouse.click(**p)
                    mark=page.evaluate('structuredClone(__refs.state.annotations.at(-1))');assert mark['pane']==pane
                    page.evaluate("pane=>__refs.setMode('select',pane)",pane)
                    return mark
                def close_chat():
                    if page.locator('#chat-dock').is_visible():control(page,'#chat-collapse').click()

                left=draw('reference');before=evidence();p=point('#reference-annotations')
                page.mouse.click(**p);assert note.input_value()==''
                page.mouse.dblclick(**p,delay=70)
                assert note.input_value()=='📍1' and f"[[annotation:{left['id']}]]" in page.evaluate('__refs.promptText()')
                assert evidence()==before
                print('PASS: reference mark single-click selects; real double-click cites the same source without changing evidence',flush=True)

                note.fill('');close_chat();control(page,'#capture-scene-button').click()
                page.wait_for_function("__refs.state.sceneView==='snapshot'")
                right=draw('scene');before=evidence();p=point('#scene-annotations')
                page.mouse.dblclick(**p,delay=70)
                assert note.input_value()=='📍2' and evidence()==before
                note.fill('');close_chat();p=point('#scene-annotations')
                page.mouse.move(**p);page.mouse.down();page.mouse.move(p['x']+40,p['y']+20,steps=4);page.mouse.move(**p,steps=4);page.mouse.up()
                page.mouse.click(**p)
                page.locator('#scene-annotations').dispatch_event('dblclick',{'button':0,'clientX':p['x'],'clientY':p['y']})
                assert note.input_value()==''
                page.locator('#scene-annotations').dispatch_event('pointercancel',{'pointerId':1})
                page.locator('#scene-annotations').dispatch_event('dblclick',{'button':0,'clientX':p['x'],'clientY':p['y']})
                assert note.input_value()==''
                print('PASS: frozen scene mark double-click cites its own annotation; drag-return and cancellation cannot cite',flush=True)

                page.evaluate("document.getElementById('references-dialog').showModal()")
                row=page.locator('.annotation-select[data-annotation-id="'+right['id']+'"]');element=row.element_handle()
                row.dblclick(delay=70)
                assert note.input_value()=='📍2' and evidence()==before
                assert not page.locator('#references-dialog').evaluate('el=>el.open')
                # After its double-click timeout, no delayed single-click may
                # steal focus from the composer or revisit another frame.
                page.wait_for_timeout(550)
                assert page.locator('#feedback-note').evaluate('el=>document.activeElement===el')
                assert evidence()==before
                print('PASS: list double-click survives the first click without rebuilding a static screenshot row or running a delayed reveal',flush=True)

                note.fill('');page.evaluate("__refs.ui.toast.classList.remove('show')")
                page.locator('#timeline-time').dblclick(delay=70)
                assert note.input_value()=='🕒1' and '正面' in page.evaluate('__refs.promptText()')
                assert not page.locator('#toast').evaluate("el=>el.classList.contains('show')")
                assert evidence()==before
                note.click(position={'x':22,'y':20})
                page.wait_for_function("document.getElementById('prompt-reference-dialog').open")
                assert '时间戳1' in page.locator('#prompt-reference-title').inner_text()
                assert '正面' in page.locator('#prompt-reference-detail').inner_text()
                page.locator('#prompt-reference-close').click()
                note.click(position={'x':note.bounding_box()['width']-25,'y':20})
                assert not page.locator('#prompt-reference-dialog').evaluate('el=>el.open')
                assert evidence()==before
                print('PASS: timeline double-click inserts silently; clicking the native inline time icon opens exact details, blank space does not',flush=True)

                # Detailed selection may hit the same root path as item-level
                # selection. Display bindings differ; the target remains exact.
                note.fill('')
                for level in ('item','part'):
                    page.evaluate("""level=>{const m=__refs,n=m.state.objectNodes.get('fixture_model').userData.gltfRoot.children[0];
                        m.state.selectionLevel=level;const ref=m.nodeReference('fixture_model',n,level);
                        m.selectObject('fixture_model',ref,ref);m.quoteObjectReference(m.selectedPromptReference());}""",level)
                assert note.input_value()=='🧊1 🧩1'
                assert page.evaluate("__refs.promptText().split('[[node:fixture_model:0]]').length-1")==2
                note.click(position={'x':22,'y':20})
                page.wait_for_function("document.getElementById('prompt-reference-dialog').open")
                assert '物体1' in page.locator('#prompt-reference-title').inner_text()
                assert page.locator('#prompt-reference-detail').inner_text().startswith('物体 · Cabinet')
                page.locator('#prompt-reference-close').click()
                page.reload();ready()
                assert note.input_value()=='🧊1 🧩1'
                assert page.locator('.prompt-reference-chip').count()==2
                page.locator('.prompt-reference-chip').filter(has_text='🧊1').locator('.prompt-reference-remove').click()
                assert note.input_value().strip()=='🧩1'
                note.press('Control+z');assert note.input_value()=='🧊1 🧩1'
                page.locator('#submit-button').click()
                page.wait_for_function("__refs.state.feedbackCount===1 && !__refs.state.submitting")
                saved=store.state['feedback'][0]
                assert '🧊1 [[node:fixture_model:0]]' in saved['note'] and '🧩1 [[node:fixture_model:0]]' in saved['note']
                assert len(saved['referenced_scene_nodes'])==1
                assert set(saved['referenced_scene_nodes'][0])<= {'parent_object_id','node_path','node_name','stable_id','semantic_id'}
                assert note.input_value()=='' and not errors,errors
                print('PASS: same exact node has separate item/part labels and details through reload, removal/undo and real schema-checked isolated feedback',flush=True)
                browser.close()
        finally:server.shutdown();thread.join(5);server.server_close()
    print('ALL REFERENCE INTERACTION BROWSER CHECKS PASSED',flush=True)


if __name__=='__main__':main()

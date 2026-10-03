"""Real pointer/keyboard regressions for pane tools and explicit feedback scope.
Runs only against temporary data with Codex disabled.
"""
from pathlib import Path
import sys,tempfile,threading,base64,json,re
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'examples/room_demo')]
from core import SceneStore
from server import make_server
from build_scene import build
from playwright.sync_api import sync_playwright,expect
from immersive_theme_smoke import HOOK,wait_ready,settle,point,bounds,assert_inside
from workspace_ui_helpers import control,choose_tool,open_annotation_tools

def main():
 with tempfile.TemporaryDirectory(prefix='workspace-refinement-',dir=ROOT.parent/'tmp') as directory:
  tmp=Path(directory);store=SceneStore(tmp/'data');session=store.create_session(reference_images=[str(ROOT/'examples/room_demo/reference.png')]);store.set_scene_preview(str(build(ROOT/'examples/room_demo/scene.json',tmp/'room.glb')))
  server=make_server(port=0,data_dir=tmp/'data',project_dir=ROOT,web_dir=ROOT/'web',enable_codex=False);store=server.scene_store;server.workspace_gateway.ensure(session['session_id']);store.workspace_agent(status='idle');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,args=['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader','--disable-accelerated-2d-canvas'])
    page=browser.new_page(viewport={'width':1440,'height':960},color_scheme='light');errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.route('**/app.js',lambda route:route.fulfill(status=200,content_type='text/javascript',body=(ROOT/'web/app.js').read_text()+HOOK))
    page.goto(server.browser_url(session['session_id']));wait_ready(page)
    control(page,'#chat-collapse').click();choose_tool(page,'point','reference');point(page,'#reference-annotations',.35,.4)
    page.wait_for_function('__appearanceCheck.state.annotations.length===1')
    before=page.evaluate('__appearanceCheck.pose()');r=bounds(page,'#viewport canvas');x,y=r['x']+r['width']*.45,r['y']+r['height']*.5
    page.mouse.move(x,y);page.mouse.down();page.mouse.move(x+100,y+25,steps=12);page.mouse.up();settle(page)
    assert page.evaluate('__appearanceCheck.state.sceneView')=='live'
    assert page.evaluate('__appearanceCheck.state.sceneSnapshots.length')==0
    assert page.evaluate('__appearanceCheck.pose()')!=before
    assert page.evaluate('__appearanceCheck.state.annotations.length')==1
    assert page.evaluate('__appearanceCheck.state.paneModes')=={'reference':'point','scene':'select'}
    control(page,'#capture-scene-button').click();choose_tool(page,'line','scene');r=bounds(page,'#scene-annotations');page.mouse.move(r['x']+r['width']*.3,r['y']+r['height']*.4);page.mouse.down();page.mouse.move(r['x']+r['width']*.5,r['y']+r['height']*.5,steps=8);page.mouse.up()
    page.wait_for_function('__appearanceCheck.state.annotations.length===2')
    point(page,'#reference-annotations',.55,.5)
    page.wait_for_function('__appearanceCheck.state.annotations.length===3')
    assert page.evaluate('__appearanceCheck.state.annotations.map(a=>a.type)')==['point','line','point']
    assert page.evaluate('__appearanceCheck.state.paneModes')=={'reference':'point','scene':'line'}
    assert not page.locator('#snapshot-context').is_hidden()
    page.reload();page.wait_for_function('window.__appearanceCheck?.state.workspaceReady && !__appearanceCheck.state.sceneLoading');settle(page)
    assert page.evaluate('__appearanceCheck.state.paneModes')=={'reference':'point','scene':'line'}
    print('PASS independent tools, real 3D rotation during reference drawing, screenshot tool memory and reload',flush=True)
    open_annotation_tools(page,'reference');control(page,'#clear-annotations').click();expect(page.locator('#clear-annotations-description')).to_contain_text('2 个标记');control(page,'#cancel-clear-annotations').click()
    assert page.evaluate('__appearanceCheck.state.annotations.length')==3
    control(page,'#clear-annotations').click();control(page,'#confirm-clear-annotations').click()
    assert page.evaluate('__appearanceCheck.state.annotations.map(a=>a.type)')==['line']
    assert page.evaluate('__appearanceCheck.state.sceneSnapshots.length')==1
    control(page,'#undo-annotation').click();assert page.evaluate('__appearanceCheck.state.annotations.length')==3
    open_annotation_tools(page,'scene');control(page,'#clear-annotations').click();expect(page.locator('#clear-annotations-description')).to_contain_text('1 个标记');control(page,'#confirm-clear-annotations').click()
    assert page.evaluate('__appearanceCheck.state.annotations.map(a=>a.type)')==['point','point']
    assert page.evaluate('__appearanceCheck.state.sceneView')=='snapshot'
    control(page,'#undo-annotation').click()
    control(page,'#clear-round').click();expect(page.locator('#clear-annotations-description')).to_contain_text('1 张截图');expect(page.locator('#clear-annotations-description')).to_contain_text('3 个标记');control(page,'#confirm-clear-annotations').click();expect(page.locator('#projects-dialog')).to_be_hidden()
    assert page.evaluate('__appearanceCheck.state.annotations.length + __appearanceCheck.state.sceneSnapshots.length')==0
    page.keyboard.press('Control+z');page.wait_for_function('__appearanceCheck.state.annotations.length===3')
    print('PASS image-local clear, cancellation, preserved other-pane marks and snapshots, global scope and undo',flush=True)
    control(page,'#browse-button').click();control(page,'#immersive-toggle').click();settle(page);control(page,'#immersive-reference-toggle').click();settle(page);choose_tool(page,'erase','reference')
    r=bounds(page,'#reference-annotations');page.mouse.move(r['x']+r['width']*.35,r['y']+r['height']*.4);page.mouse.down();page.keyboard.press('Escape');page.mouse.up()
    expect(page.locator('#reference-pane')).to_be_visible();assert page.evaluate('__appearanceCheck.state.annotations.length')==3
    page.keyboard.press('Escape');expect(page.locator('#reference-pane')).to_be_hidden()
    print('PASS Escape cancels an eraser gesture before closing the reference window',flush=True)
    control(page,'#comparison-layout-button').click();settle(page);control(page,'#chat-launcher').click();page.locator('#feedback-note').fill('调整图中桌子的位置')
    expect(page.locator('#feedback-evidence-summary')).to_have_attribute('aria-label',re.compile('1 张截图'));expect(page.locator('#feedback-evidence-summary')).to_have_attribute('aria-label',re.compile('3 个标记'))
    page.locator('#feedback-evidence-summary').click();expect(page.locator('#feedback-evidence-list .evidence-card')).to_have_count(2)
    page.locator('#feedback-evidence-summary').click()
    # A failed save retains exactly the frozen evidence for retry.
    page.route('**/api/sessions/*/feedback*',lambda route:route.fulfill(status=503,content_type='application/json',body=json.dumps({'error':'isolated temporary save failure'})),times=1)
    control(page,'#submit-button').click();page.wait_for_function('__appearanceCheck.state.pendingSubmission && !__appearanceCheck.state.submitting')
    expect(page.locator('#feedback-evidence-description')).to_contain_text('冻结')
    expect(page.locator('#feedback-evidence-summary')).to_have_attribute('aria-label',re.compile('3 个标记'))
    assert not store.state['feedback']
    control(page,'#submit-button').click();page.wait_for_function('__appearanceCheck.state.feedbackCount===1 && !__appearanceCheck.state.submitting',timeout=30000)
    page.evaluate('__appearanceCheck.refreshWorkspace()')
    if page.locator('#chat-history-toggle').get_attribute('aria-expanded')=='false':control(page,'#chat-history-toggle').click()
    receipt=page.locator('.feedback-receipt').last;expect(receipt).to_be_visible();receipt.click()
    expect(page.locator('#feedback-preview-status')).to_contain_text('3 个标记')
    expect(page.locator('#feedback-preview-media .evidence-card')).to_have_count(3)
    assert page.locator('#feedback-preview-media img').evaluate_all('(imgs)=>imgs.every(img=>img.getAttribute("src").includes("/media/") || img.getAttribute("src").includes("/screenshots/"))')
    page.wait_for_function('Array.from(document.querySelectorAll("#feedback-preview-media img")).every(img=>img.complete && img.naturalWidth>0)')
    for button in page.locator('#feedback-preview-media .evidence-variants button').all():
     button.click();page.wait_for_function('Array.from(document.querySelectorAll("#feedback-preview-media img")).every(img=>img.complete && img.naturalWidth>0)')
    zoom=page.locator('#feedback-preview-media .evidence-image').first;zoom.click();expect(zoom).to_have_attribute('aria-expanded','true')
    assert page.locator('#feedback-preview-media .is-expanded').bounding_box()['width'] > 600
    zoom.click();expect(zoom).to_have_attribute('aria-expanded','false')
    out=ROOT.parent/'inspection/workspace-refinement';out.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(out/'saved-evidence.png'))
    feedback=store.state['feedback'][-1];assert len(feedback['annotations'])==3 and len(feedback['scene_snapshots'])==1
    control(page,'#feedback-preview-close').click();page.reload();wait_ready(page);settle(page)
    expect(page.locator('.feedback-receipt')).to_be_visible();page.locator('.feedback-receipt').last.click();expect(page.locator('#feedback-preview-status')).to_contain_text('3 个标记');control(page,'#feedback-preview-close').click()
    assert page.evaluate('__appearanceCheck.state.annotations.length')==0
    print('PASS accurate draft summary, frozen retry after failed save, actual original/annotated images and receipt after reload',flush=True)
    control(page,'#chat-collapse').click()
    image='data:image/png;base64,'+base64.b64encode((ROOT/'examples/room_demo/reference.png').read_bytes()).decode();store.set_reference_clip(session['session_id'],{'name':'走廊参考.mp4','fps':2,'frames':[{'name':'frame0.png','data_url':image,'time_sec':0},{'name':'frame1.png','data_url':image,'time_sec':.5}]});page.evaluate('__appearanceCheck.refreshWorkspace()');page.wait_for_function('__appearanceCheck.state.referenceClip!==null');settle(page)
    expect(page.locator('#reference-title')).to_contain_text('走廊参考');expect(page.locator('#reference-frame-label')).to_contain_text('帧');expect(page.locator('#timeline-time')).to_contain_text('00:00.0')
    choose_tool(page,'point','reference');point(page,'#reference-annotations',.4,.4);page.wait_for_function('__appearanceCheck.state.annotations.length===1');control(page,'#timeline-next').click();settle(page);point(page,'#reference-annotations',.6,.5);page.wait_for_function('__appearanceCheck.state.annotations.length===2')
    control(page,'#clear-annotations').click();expect(page.locator('#clear-annotations-description')).to_contain_text('1 个标记');control(page,'#confirm-clear-annotations').click();assert page.evaluate('__appearanceCheck.state.annotations.length')==1;assert page.evaluate('__appearanceCheck.state.dynamicSnapshots.length')==2
    control(page,'#undo-annotation').click();assert page.evaluate('__appearanceCheck.state.annotations.length')==2
    for width,height in [(1440,960),(390,844)]:
     page.set_viewport_size({'width':width,'height':height});settle(page)
     control(page,'#annotation-tools-close').click() if page.locator('#annotation-tool-panel').is_visible() else None
     control(page,'.timeline-options > summary').click();assert_inside(page,'#save-moment');page.keyboard.press('Escape')
     assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    print('PASS video identity/frame/time, per-frame clearing and undo, retained moments and mobile playback menus',flush=True)
    assert not errors,errors;browser.close()
  finally:server.shutdown();server.server_close();thread.join(timeout=3)
if __name__=='__main__':main()

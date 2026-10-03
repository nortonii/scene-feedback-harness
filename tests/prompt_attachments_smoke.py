"""Composer-only image references, frozen saved evidence and clean scene canvas."""
from pathlib import Path
import sys,tempfile,threading,base64
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'examples/room_demo')]
from core import SceneStore
from server import make_server
from build_scene import build
from playwright.sync_api import sync_playwright,expect
from immersive_theme_smoke import HOOK,point,bounds,settle
from workspace_ui_helpers import control,choose_tool

def main():
 with tempfile.TemporaryDirectory(prefix='attachments-',dir=ROOT.parent/'tmp') as directory:
  tmp=Path(directory);seed=SceneStore(tmp/'data');session=seed.create_session(reference_images=[str(ROOT/'examples/room_demo/reference.png')]);seed.set_scene_preview(str(build(ROOT/'examples/room_demo/scene.json',tmp/'room.glb')))
  server=make_server(port=0,data_dir=tmp/'data',project_dir=ROOT,web_dir=ROOT/'web',enable_codex=False);store=server.scene_store;server.workspace_gateway.ensure(session['session_id']);store.workspace_agent(status='idle');threading.Thread(target=server.serve_forever,daemon=True).start()
  try:
   with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,args=['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader','--disable-accelerated-2d-canvas'])
    page=browser.new_page(viewport={'width':1440,'height':960});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.route('**/app.js',lambda route:route.fulfill(status=200,content_type='text/javascript',body=(ROOT/'web/app.js').read_text()+HOOK))
    page.goto(server.browser_url(session['session_id']));page.wait_for_function('window.__appearanceCheck?.state.workspaceReady && !__appearanceCheck.state.sceneLoading');settle(page)
    plus=page.locator('#prompt-attach-button');menu=page.locator('#prompt-attach-menu');out=ROOT.parent/'inspection/prompt-attachments';out.mkdir(parents=True,exist_ok=True)
    expect(page.locator('#scene-stage #scene-hint')).to_have_count(0);expect(page.locator('#scene-stage #drag-scene-image')).to_have_count(0);expect(page.locator('#reference-stage #drag-reference-image')).to_have_count(0)
    plus.click();page.locator('#prompt-attach-saved').click();expect(page.locator('.attachment-empty')).to_be_visible();page.keyboard.press('Escape');expect(plus).to_be_focused();expect(menu).to_be_hidden()
    control(page,'#drag-reference-image').click();expect(menu).to_be_hidden();expect(page.locator('.prompt-input .prompt-image-chip')).to_have_count(1);expect(page.locator('#feedback-note')).to_be_focused()
    page.locator('.prompt-image-preview').click();expect(page.locator('#prompt-image-preview-dialog')).to_be_visible();page.keyboard.press('Escape')
    page.locator('.prompt-image-remove').click();expect(page.locator('.prompt-image-chip')).to_have_count(0);assert not page.evaluate('__appearanceCheck.state.imageRefs.length')
    print('PASS clean canvas, empty library, reference capture, inline thumbnail preview/removal and keyboard dismissal',flush=True)
    control(page,'#capture-scene-button').click();shot=page.evaluate('__appearanceCheck.state.snapshot.id');choose_tool(page,'point','scene');point(page,'#scene-annotations',.5,.4)
    snapshot=page.evaluate('__appearanceCheck.state.snapshot');control(page,'#annotation-tools-close').click();control(page,'#browse-button').click()
    original_pose=page.evaluate('__appearanceCheck.pose()');original_state=page.evaluate('({view:__appearanceCheck.state.sceneView,time:__appearanceCheck.state.time,snapshot:__appearanceCheck.state.snapshot?.id})')
    plus.click();page.locator('#prompt-attach-saved').click();page.locator(f'[data-attachment-id="{shot}"]').click();expect(menu).to_be_hidden();expect(page.locator('.prompt-image-chip')).to_have_count(1)
    image=page.evaluate('__appearanceCheck.state.imageRefs[0]');assert image['original_data_url']==snapshot['data_url'];assert image['camera']==snapshot['camera'];assert image.get('annotated_data_url');assert page.evaluate('__appearanceCheck.pose()')==original_pose
    assert page.evaluate('({view:__appearanceCheck.state.sceneView,time:__appearanceCheck.state.time,snapshot:__appearanceCheck.state.snapshot?.id})')==original_state
    control(page,'#snapshot-button').click();plus.click();expect(page.locator('#drag-scene-image')).to_have_text('引用这张截图');page.keyboard.press('Escape')
    page.reload();page.wait_for_function('window.__appearanceCheck?.state.workspaceReady && !__appearanceCheck.state.sceneLoading');settle(page);expect(page.locator('.prompt-image-chip')).to_have_count(1)
    assert page.evaluate('__appearanceCheck.state.imageRefs[0].original_data_url')==image['original_data_url']
    print('PASS saved screenshot freezes original, camera, annotations and overlay without changing active view; references survive reload',flush=True)
    data='data:image/png;base64,'+base64.b64encode((ROOT/'examples/room_demo/reference.png').read_bytes()).decode()
    store.set_reference_clip(session['session_id'],{'name':'引用视频','fps':2,'frames':[{'name':f'frame-{n}','data_url':data,'time_sec':n*.5} for n in range(3)]});page.reload();page.wait_for_function('window.__appearanceCheck?.state.referenceClip && !__appearanceCheck.state.sceneLoading');settle(page)
    control(page,'#timeline-next').click();control(page,'#chat-collapse').click();expect(page.locator('#chat-dock')).to_be_hidden();choose_tool(page,'point','reference');point(page,'#reference-annotations',.4,.4);control(page,'#annotation-tools-close').click();control(page,'#chat-launcher').click();plus.click();expect(page.locator('#drag-reference-image')).to_have_text('引用当前视频帧');page.locator('#drag-reference-image').click();expect(menu).to_be_hidden()
    assert page.evaluate('__appearanceCheck.state.imageRefs.at(-1).time_sec')==.5;assert page.evaluate('!!__appearanceCheck.state.imageRefs.at(-1).annotated_data_url')
    control(page,'#browse-button').click();control(page,'#capture-scene-button').click();moment=page.evaluate('__appearanceCheck.state.snapshot.id');control(page,'#annotation-tools-close').click();control(page,'#timeline-next').click()
    plus.click();page.locator('#prompt-attach-saved').click();expect(page.locator('.attachment-saved')).to_have_count(2);page.locator(f'[data-attachment-id="{moment}"]').click();expect(menu).to_be_hidden();assert page.evaluate('__appearanceCheck.state.imageRefs.at(-1).time_sec')==.5;assert page.evaluate('__appearanceCheck.state.time')==1
    print('PASS video-aware action and saved moment reference preserve captured time while leaving current video position intact',flush=True)
    for width,height in [(1440,960),(390,844),(340,760)]:
     page.set_viewport_size({'width':width,'height':height})
     for layout in ['compare','immersive']:
      control(page,'#comparison-layout-button' if layout=='compare' else '#immersive-toggle').click();settle(page)
      for theme in ['light','dark']:
       if page.locator('html').get_attribute('data-theme')!=theme:control(page,'#theme-toggle').click();settle(page)
       plus.click();expect(menu).to_be_visible();r=bounds(page,'#prompt-attach-menu');assert r['x']>=0 and r['y']>=0 and r['x']+r['width']<=width+1 and r['y']+r['height']<=height+1
       page.wait_for_function("Number(getComputedStyle(document.querySelector('#prompt-attach-menu')).opacity)>.99");page.screenshot(path=str(out/f'{layout}-{theme}-{width}.png'));page.locator('#prompt-attach-saved').click();r=bounds(page,'#prompt-attach-menu');assert r['y']>=0 and r['y']+r['height']<=height+1
       page.keyboard.press('Escape');expect(menu).to_be_hidden();assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
      control(page,'#chat-collapse').click();expect(page.locator('#chat-dock')).to_be_hidden();control(page,'.view-popover > summary').click();page.locator('.camera-help summary').click() if not page.locator('.camera-help').evaluate('el=>el.open') else None
      expect(page.locator('#scene-hint')).to_be_visible();page.keyboard.press('Escape');control(page,'#chat-launcher').click();settle(page)
    print('PASS desktop and narrow-screen day/night menus fit above composer; camera instructions are available only in the camera menu',flush=True)
    page.set_viewport_size({'width':1440,'height':960});control(page,'#comparison-layout-button').click();settle(page)
    refs=page.evaluate('__appearanceCheck.state.imageRefs');assert len(refs)==3
    for _ in range(5):control(page,'#drag-reference-image').click();expect(menu).to_be_hidden()
    control(page,'#drag-reference-image').click();expect(page.locator('#toast')).to_contain_text('最多引用 8 张');assert page.evaluate('__appearanceCheck.state.imageRefs.length')==8;page.keyboard.press('Escape')
    for _ in range(5):page.locator('.prompt-image-remove').last.click()
    page.locator('#feedback-note').fill('核对引用图文 '+page.locator('#feedback-note').input_value())
    page.locator('#submit-button').click();page.wait_for_function('__appearanceCheck.state.feedbackCount===1 && !__appearanceCheck.state.submitting',timeout=30000)
    packet=store.list_all_feedback()[0];assert len(packet['image_refs'])==3
    assert [r.get('time_sec') for r in packet['image_refs']]==[r.get('time_sec') for r in refs]
    expect(page.locator('.prompt-image-chip')).to_have_count(0);expect(page.locator('#feedback-note')).to_have_value('')
    print('PASS eight-image limit, removal and real feedback save preserve all three cited sources and clear the composer after success',flush=True)
    assert not errors,errors;browser.close()
  finally:server.shutdown();server.server_close()
if __name__=='__main__':main()

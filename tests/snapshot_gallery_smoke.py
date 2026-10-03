"""Unified still/video collection, fixed evidence during playback, and real save.
Uses a temporary server with Codex disabled; no production sessions are touched.
"""
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
 with tempfile.TemporaryDirectory(prefix='unified-gallery-',dir=ROOT.parent/'tmp') as directory:
  tmp=Path(directory);store=SceneStore(tmp/'data');session=store.create_session(reference_images=[str(ROOT/'examples/room_demo/reference.png')]);store.set_scene_preview(str(build(ROOT/'examples/room_demo/scene.json',tmp/'room.glb')))
  server=make_server(port=0,data_dir=tmp/'data',project_dir=ROOT,web_dir=ROOT/'web',enable_codex=False);store=server.scene_store;server.workspace_gateway.ensure(session['session_id']);store.workspace_agent(status='idle');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,args=['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader','--disable-accelerated-2d-canvas'])
    page=browser.new_page(viewport={'width':1440,'height':960});errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    page.route('**/app.js',lambda route:route.fulfill(status=200,content_type='text/javascript',body=(ROOT/'web/app.js').read_text()+HOOK))
    page.goto(server.browser_url(session['session_id']));page.wait_for_function('window.__appearanceCheck?.state.workspaceReady && !__appearanceCheck.state.sceneLoading');settle(page)
    control(page,'#chat-collapse').click();control(page,'#capture-scene-button').click();static=page.evaluate('__appearanceCheck.state.snapshot.id');control(page,'#browse-button').click()
    image='data:image/png;base64,'+base64.b64encode((ROOT/'examples/room_demo/reference.png').read_bytes()).decode()
    frames=[{'name':f'frame-{n}.png','data_url':image,'time_sec':n*.5} for n in range(4)]
    clip=store.set_reference_clip(session['session_id'],{'name':'正面视频','fps':2,'frames':frames})['reference_clip'];page.reload();page.wait_for_function('window.__appearanceCheck?.state.workspaceReady && !__appearanceCheck.state.sceneLoading && __appearanceCheck.state.referenceClip');settle(page)
    control(page,'#capture-scene-button').click();first=page.evaluate('__appearanceCheck.state.snapshot');first_id=first['id']
    gallery=page.locator('#scene-snapshots');expect(page.locator('#moment-strip')).to_have_count(0);expect(page.locator('.snapshot-card')).to_have_count(2)
    expect(page.locator('.snapshot-card[data-kind=moment] .snapshot-open')).to_have_text('00:00.0');expect(page.locator('#snapshot-strip-count')).to_have_text('2')
    assert gallery.evaluate("el=>!!el.closest('#scene-stage')")
    choose_tool(page,'point','scene');point(page,'#scene-annotations',.63,.45);page.wait_for_function('__appearanceCheck.state.annotations.length===1')
    first_pixels=page.locator('#scene-snapshot-image').get_attribute('src')
    control(page,'#timeline-next').click();page.wait_for_function('__appearanceCheck.state.time===.5');assert page.evaluate('__appearanceCheck.state.sceneView')=='snapshot';assert page.evaluate('__appearanceCheck.state.snapshot.id')==first_id
    assert page.locator('#scene-snapshot-image').get_attribute('src')==first_pixels;expect(page.locator('#snapshot-context')).to_contain_text('00:00.0')
    point(page,'#scene-annotations',.72,.5);assert page.evaluate('__appearanceCheck.state.annotations.at(-1).time_sec')==0
    choose_tool(page,'point','reference');point(page,'#reference-annotations',.4,.4);point(page,'#reference-annotations',.6,.5)
    assert page.evaluate('__appearanceCheck.state.dynamicSnapshots.length')==2
    assert page.evaluate('__appearanceCheck.state.snapshot.id')==first_id
    assert page.evaluate('__appearanceCheck.state.annotations.slice(-2).map(a=>a.time_sec)')==[.5,.5]
    second_id=page.evaluate('__appearanceCheck.state.dynamicSnapshots.at(-1).id')
    expect(page.locator('#timeline-marks button')).to_have_count(2)
    print('PASS unified cards and counts; seeking and reference marking retain the fixed right screenshot and correct per-frame evidence',flush=True)
    control(page,'#annotation-tools-close').click()
    second=page.locator(f'.snapshot-open[data-snapshot-id="{second_id}"]');second.hover();expect(page.locator('#snapshot-gallery-preview')).to_be_visible();expect(page.locator('#snapshot-gallery-preview img')).to_have_count(2)
    page.wait_for_function('Array.from(document.querySelectorAll("#snapshot-gallery-preview img")).every(img=>img.complete && img.naturalWidth>0)')
    page.keyboard.press('Escape');expect(page.locator('#snapshot-gallery-preview')).to_be_hidden()
    second.click();page.wait_for_function(f'__appearanceCheck.state.snapshot.id==="{second_id}"');expect(page.locator('#timeline-seek')).to_have_value('0.5');expect(page.locator('#snapshot-context')).to_contain_text('00:00.5')
    control(page,'#timeline-play').click();page.wait_for_function('__appearanceCheck.state.playing');page.wait_for_function('__appearanceCheck.state.time>1');control(page,'#timeline-play').click() if page.evaluate('__appearanceCheck.state.playing') else None
    assert page.evaluate('__appearanceCheck.state.snapshot.id')==second_id;assert page.evaluate('__appearanceCheck.state.sceneView')=='snapshot';assert page.evaluate('__appearanceCheck.state.dynamicSnapshots.length')==2
    page.locator(f'#timeline-marks [data-snapshot-id="{first_id}"]').click();page.wait_for_function('__appearanceCheck.state.time===0');assert page.evaluate('__appearanceCheck.state.snapshot.id')==first_id
    page.locator(f'.snapshot-card[data-snapshot-id="{second_id}"] .snapshot-remove').click();assert page.evaluate('__appearanceCheck.state.annotations.length')==2;assert page.evaluate('__appearanceCheck.state.snapshot.id')==first_id
    page.keyboard.press('Control+z');page.wait_for_function('__appearanceCheck.state.dynamicSnapshots.length===2');assert page.evaluate('__appearanceCheck.state.annotations.length')==4
    print('PASS paired hover preview, playback without new captures, timeline navigation, scoped deletion and undo',flush=True)
    control(page,'#browse-button').click();r=bounds(page,'#viewport canvas');page.mouse.move(r['x']+r['width']*.5,r['y']+r['height']*.5);page.mouse.down();page.mouse.move(r['x']+r['width']*.65,r['y']+r['height']*.48,steps=10);page.mouse.up();page.wait_for_timeout(250)
    control(page,'#capture-scene-button').click();third_id=page.evaluate('__appearanceCheck.state.snapshot.id');assert third_id!=first_id
    control(page,'#annotation-tools-close').click();page.locator('#timeline-marks button').first.click();expect(page.locator('#snapshot-gallery-preview[role=group]')).to_be_visible();expect(page.locator('#snapshot-gallery-preview button')).to_have_count(2)
    expect(page.locator('#snapshot-gallery-preview button').first).to_be_focused()
    page.keyboard.press('Escape');expect(page.locator('#snapshot-gallery-preview')).to_be_hidden();page.locator('#timeline-marks button').first.click()
    page.locator('#snapshot-gallery-preview button').first.click();page.wait_for_function(f'__appearanceCheck.state.snapshot.id==="{first_id}"')
    clip=store.set_reference_clip(session['session_id'],{'name':'侧面视频','fps':2,'frames':frames,'append_view':True})['reference_clip'];page.evaluate('__appearanceCheck.refreshWorkspace()');page.wait_for_function('__appearanceCheck.state.referenceClip.views?.length===1');side=clip['views'][0]['clip_id']
    page.locator('#reference-view-select').select_option(side);page.wait_for_function(f'__appearanceCheck.state.activeViewId==="{side}"');expect(page.locator('#timeline-marks button')).to_have_count(0)
    assert page.evaluate('__appearanceCheck.state.snapshot.id')==first_id
    page.locator(f'.snapshot-open[data-snapshot-id="{second_id}"]').click();page.wait_for_function('__appearanceCheck.state.activeViewId===__appearanceCheck.state.referenceClip.clip_id');expect(page.locator('#timeline-marks button')).to_have_count(2)
    print('PASS same-time camera variants stay selectable and gallery entries restore their saved video view and time',flush=True)
    control(page,'#annotation-tools-close').click() if page.locator('#annotation-tool-panel').is_visible() else None
    control(page,'#snapshot-strip-toggle').click();expect(page.locator('#snapshot-strip-count')).to_have_text('4');expect(page.locator('#scene-snapshot-strip')).to_be_hidden()
    page.reload();page.wait_for_function('window.__appearanceCheck?.state.workspaceReady && !__appearanceCheck.state.sceneLoading');settle(page)
    assert page.evaluate('__appearanceCheck.state.snapshot.id')==second_id;expect(page.locator('#scene-snapshot-strip')).to_be_hidden();expect(page.locator('#snapshot-strip-count')).to_have_text('4');control(page,'#snapshot-strip-toggle').click()
    out=ROOT.parent/'inspection/unified-gallery';out.mkdir(parents=True,exist_ok=True)
    for width,height in [(1440,960),(390,844)]:
     page.set_viewport_size({'width':width,'height':height})
     for layout in ['compare','immersive']:
      control(page,'#comparison-layout-button' if layout=='compare' else '#immersive-toggle').click();settle(page)
      if layout=='immersive' and page.locator('#immersive-reference-toggle').get_attribute('aria-expanded')=='false':control(page,'#immersive-reference-toggle').click();settle(page)
      expect(gallery).to_be_visible();box=gallery.bounding_box();assert box['x']>=0 and box['x']+box['width']<=width+1
      page.locator('.snapshot-open').last.scroll_into_view_if_needed();page.locator('.snapshot-open').last.click()
      assert page.evaluate('__appearanceCheck.state.snapshot.id')==third_id
      assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
      if layout=='immersive' and width==390:
       ref=bounds(page,'#reference-pane');assert ref['y']>=box['y']+box['height'],(ref,box)
      page.screenshot(path=str(out/f'{layout}-{width}.png'))
      control(page,'#theme-toggle').click();settle(page);page.screenshot(path=str(out/f'{layout}-{width}-dark.png'));control(page,'#theme-toggle').click();settle(page)
    print('PASS mixed-gallery collapse and reload, desktop/mobile layouts and reachable collection with immersive reference open',flush=True)
    page.set_viewport_size({'width':1440,'height':960});control(page,'#comparison-layout-button').click();settle(page);control(page,'#chat-launcher').click();page.locator('#feedback-note').fill('检查不同时间的场景差异');control(page,'#submit-button').click();page.wait_for_function('__appearanceCheck.state.feedbackCount===1 && !__appearanceCheck.state.submitting',timeout=30000)
    saved=store.state['feedback'][-1];assert len(saved['dynamic_frames'])==3 and len(saved['scene_snapshots'])==1 and len(saved['annotations'])==4
    for mark in saved['annotations']:assert mark['time_sec']==(0 if mark['pane']=='scene' else .5)
    expect(gallery).to_be_hidden();expect(page.locator('#timeline-marks button')).to_have_count(0)
    assert not errors,errors
    print('PASS real isolated feedback retains original per-frame evidence and clears both unified entry points after saving',flush=True)
    browser.close()
  finally:server.shutdown();server.server_close();thread.join(timeout=3)
if __name__=='__main__':main()

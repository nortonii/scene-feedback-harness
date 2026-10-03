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

def assert_gallery_contract(page, layout, expected_order=None):
    gallery=page.locator('#scene-snapshots');expect(gallery).to_be_visible()
    cards=page.locator('#scene-snapshot-strip .snapshot-card')
    expect(cards.first).to_have_attribute('data-kind','live')
    expect(cards.first.locator('#scene-live-card')).to_have_count(1)
    expect(cards.first.locator('.snapshot-remove')).to_have_count(0)
    expect(page.locator('#browse-button,#snapshot-button,#snapshot-strip-toggle,#snapshot-strip-count,#scene-snapshot-reveal')).to_have_count(0)
    for thumb in cards.locator('.snapshot-thumb').all():
        size=thumb.bounding_box();assert size and abs(size['width']-160)<1 and abs(size['height']-90)<1,size
    placement=gallery.evaluate("el=>({parent:el.parentElement.tagName,compareParent:el.parentElement.matches('.scene-pane'),position:getComputedStyle(el).position,beforeStage:el.nextElementSibling?.id==='scene-stage'})")
    if layout=='immersive':assert placement['parent']=='BODY' and placement['position']=='fixed',placement
    else:assert placement['compareParent'] and placement['beforeStage'] and placement['position'] not in ('absolute','fixed'),placement
    if expected_order is not None:
        assert cards.filter(has=page.locator('.snapshot-open[data-snapshot-id]')).evaluate_all('rows=>rows.map(row=>row.dataset.snapshotId)')==expected_order


def main():
 with tempfile.TemporaryDirectory(prefix='unified-gallery-',dir=ROOT.parent/'tmp') as directory:
  tmp=Path(directory);store=SceneStore(tmp/'data');session=store.create_session(reference_images=[str(ROOT/'examples/room_demo/reference.png')]);store.set_scene_preview(str(build(ROOT/'examples/room_demo/scene.json',tmp/'room.glb')))
  server=make_server(port=0,data_dir=tmp/'data',project_dir=ROOT,web_dir=ROOT/'web',enable_codex=False);store=server.scene_store;server.workspace_gateway.ensure(session['session_id']);store.workspace_agent(status='idle');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,args=['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader','--disable-accelerated-2d-canvas'])
    page=browser.new_page(viewport={'width':1440,'height':960});errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    page.route('**/app.js',lambda route:route.fulfill(status=200,content_type='text/javascript',body=(ROOT/'web/app.js').read_text()+HOOK+'\nObject.assign(__appearanceCheck,{renderSceneView,saveDraft});'))
    page.goto(server.browser_url(session['session_id']));page.wait_for_function('window.__appearanceCheck?.state.workspaceReady && !__appearanceCheck.state.sceneLoading');settle(page);assert_gallery_contract(page,'compare',[]);expect(page.locator('#scene-live-card')).to_have_attribute('aria-pressed','true')
    control(page,'#chat-collapse').click();control(page,'#capture-scene-button').click();static=page.evaluate('__appearanceCheck.state.snapshot.id');control(page,'#scene-live-card').click()
    image='data:image/png;base64,'+base64.b64encode((ROOT/'examples/room_demo/reference.png').read_bytes()).decode()
    frames=[{'name':f'frame-{n}.png','data_url':image,'time_sec':n*.5} for n in range(4)]
    clip=store.set_reference_clip(session['session_id'],{'name':'正面视频 10–20 秒','fps':2,'frames':frames})['reference_clip'];page.reload();page.wait_for_function('window.__appearanceCheck?.state.workspaceReady && !__appearanceCheck.state.sceneLoading && __appearanceCheck.state.referenceClip');settle(page)
    control(page,'#capture-scene-button').click();first=page.evaluate('__appearanceCheck.state.snapshot');first_id=first['id']
    gallery=page.locator('#scene-snapshots');expect(page.locator('#moment-strip')).to_have_count(0);expect(page.locator('.snapshot-card:not([data-kind=live])')).to_have_count(2)
    moment_card=page.locator('.snapshot-card[data-kind=moment] .snapshot-open')
    expect(moment_card.locator('.snapshot-source')).to_have_text('00:00.000');expect(moment_card).not_to_contain_text('正面视频');expect(moment_card).not_to_contain_text('10–20')
    assert moment_card.get_attribute('title') is None
    assert gallery.evaluate("el=>el.parentElement.matches('.scene-pane') && el.nextElementSibling?.id==='scene-stage' && getComputedStyle(el).position!=='absolute'")
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
    second=page.locator(f'.snapshot-open[data-snapshot-id="{second_id}"]');preview=page.locator('#snapshot-gallery-preview')
    live_card=page.locator('#scene-live-card');static_card=page.locator(f'.snapshot-open[data-snapshot-id="{static}"]');first_card=page.locator(f'.snapshot-open[data-snapshot-id="{first_id}"]')
    first_tick=page.locator(f'#timeline-marks [data-snapshot-id="{first_id}"]')
    for target in [live_card,static_card,second,first_tick]:
     target.hover();page.wait_for_timeout(350);expect(preview).to_be_hidden()
    second.focus();second.press('Home');expect(live_card).to_be_focused()
    for target in [live_card,static_card,first_card,second]:
     expect(target).to_be_focused();page.wait_for_timeout(350);expect(preview).to_be_hidden()
     if target is not second:target.press('ArrowRight')
    first_tick.focus();assert first_tick.evaluate("el=>el.matches(':focus-visible')")
    page.wait_for_timeout(350);expect(preview).to_be_hidden();expect(preview.locator('img')).to_have_count(0)
    second.click();page.wait_for_function(f'__appearanceCheck.state.snapshot.id==="{second_id}"');expect(page.locator('#timeline-seek')).to_have_value('0.5');expect(page.locator('#snapshot-context')).to_contain_text('00:00.5')
    control(page,'#timeline-play').click();page.wait_for_function('__appearanceCheck.state.playing');page.wait_for_function('__appearanceCheck.state.time>1');control(page,'#timeline-play').click() if page.evaluate('__appearanceCheck.state.playing') else None
    assert page.evaluate('__appearanceCheck.state.snapshot.id')==second_id;assert page.evaluate('__appearanceCheck.state.sceneView')=='snapshot';assert page.evaluate('__appearanceCheck.state.dynamicSnapshots.length')==2
    page.locator(f'#timeline-marks [data-snapshot-id="{first_id}"]').click();page.wait_for_function('__appearanceCheck.state.time===0');assert page.evaluate('__appearanceCheck.state.snapshot.id')==first_id
    page.locator(f'.snapshot-card[data-snapshot-id="{second_id}"] .snapshot-remove').click();assert page.evaluate('__appearanceCheck.state.annotations.length')==2;assert page.evaluate('__appearanceCheck.state.snapshot.id')==first_id
    page.keyboard.press('Control+z');page.wait_for_function('__appearanceCheck.state.dynamicSnapshots.length===2');assert page.evaluate('__appearanceCheck.state.annotations.length')==4
    print('PASS timestamp-only cards, no hover/focus preview, keyboard navigation, playback, timeline navigation, scoped deletion and undo',flush=True)
    control(page,'#scene-live-card').click();r=bounds(page,'#viewport canvas');page.mouse.move(r['x']+r['width']*.5,r['y']+r['height']*.5);page.mouse.down();page.mouse.move(r['x']+r['width']*.65,r['y']+r['height']*.48,steps=10);page.mouse.up();page.wait_for_timeout(250)
    control(page,'#capture-scene-button').click();third_id=page.evaluate('__appearanceCheck.state.snapshot.id');assert third_id!=first_id
    control(page,'#annotation-tools-close').click();page.locator('#timeline-marks button').first.click();expect(page.locator('#snapshot-gallery-preview[role=group]')).to_be_visible();expect(page.locator('#snapshot-gallery-preview button')).to_have_count(2)
    expect(page.locator('#snapshot-gallery-preview button').first).to_be_focused()
    page.keyboard.press('Escape');expect(page.locator('#snapshot-gallery-preview')).to_be_hidden();expect(page.locator('#timeline-marks button').first).to_be_focused();page.wait_for_timeout(350);expect(preview).to_be_hidden();page.locator('#timeline-marks button').first.click()
    page.locator('#snapshot-gallery-preview button').first.click();page.wait_for_function(f'__appearanceCheck.state.snapshot.id==="{first_id}"')
    clip=store.set_reference_clip(session['session_id'],{'name':'侧面视频','fps':2,'frames':frames,'append_view':True})['reference_clip'];page.evaluate('__appearanceCheck.refreshWorkspace()');page.wait_for_function('__appearanceCheck.state.referenceClip.views?.length===1');side=clip['views'][0]['clip_id']
    page.locator('#reference-view-select').select_option(side);page.wait_for_function(f'__appearanceCheck.state.activeViewId==="{side}"');expect(page.locator('#timeline-marks button')).to_have_count(0)
    assert page.evaluate('__appearanceCheck.state.snapshot.id')==first_id
    page.locator(f'.snapshot-open[data-snapshot-id="{second_id}"]').click();page.wait_for_function('__appearanceCheck.state.activeViewId===__appearanceCheck.state.referenceClip.clip_id');expect(page.locator('#timeline-marks button')).to_have_count(2)
    print('PASS same-time camera variants stay selectable and gallery entries restore their saved video view and time',flush=True)
    control(page,'#annotation-tools-close').click() if page.locator('#annotation-tool-panel').is_visible() else None
    # Construct a restored mixed draft: capture through the real still-image UI,
    # then restore its existing video moments without offering a new capture mode.
    page.evaluate('__appearanceCheck.setReferenceClip(null)')
    def capture_restored_static():
        control(page,'#scene-live-card').click()
        page.evaluate('''()=>{const c=__appearanceCheck;c.restoredDraftMoments=c.state.dynamicSnapshots;
          c.state.dynamicSnapshots=[];c.state.snapshot=null;c.renderSceneView();c.saveDraft();}''')
        try:control(page,'#capture-scene-button').click()
        finally:page.evaluate('''()=>{const c=__appearanceCheck;c.state.dynamicSnapshots=c.restoredDraftMoments;
          delete c.restoredDraftMoments;c.renderSceneView();c.saveDraft();}''')
        assert page.evaluate('__appearanceCheck.state.snapshot.time_sec===undefined')
    capture_restored_static()
    deleted_still=page.evaluate('__appearanceCheck.state.snapshot.id');deleted_name=page.locator(f'.snapshot-open[data-snapshot-id="{deleted_still}"] .snapshot-name').inner_text()
    page.locator(f'.snapshot-card[data-snapshot-id="{deleted_still}"] .snapshot-remove').click()
    page.keyboard.press('Control+z');page.wait_for_function('(id)=>__appearanceCheck.state.sceneSnapshots.some(s=>s.id===id)',arg=deleted_still)
    expect(page.locator(f'.snapshot-open[data-snapshot-id="{deleted_still}"]')).to_contain_text(deleted_name)
    page.locator(f'.snapshot-card[data-snapshot-id="{deleted_still}"] .snapshot-remove').click();capture_restored_static()
    later_static=page.evaluate('__appearanceCheck.state.snapshot.id');later_name=page.locator(f'.snapshot-open[data-snapshot-id="{later_static}"] .snapshot-name').inner_text()
    assert int(later_name.removeprefix('截图').strip())>int(deleted_name.removeprefix('截图').strip()), (deleted_name,later_name)
    expected_order=[static,first_id,second_id,third_id,later_static]
    def card_order():return page.locator('.snapshot-card:not([data-kind=live])').evaluate_all('cards=>cards.map(c=>c.dataset.snapshotId)')
    assert card_order()==expected_order
    page.evaluate('(clip)=>__appearanceCheck.setReferenceClip(clip)',clip)
    page.locator(f'.snapshot-open[data-snapshot-id="{second_id}"]').click()
    expect(gallery).to_be_visible();expect(page.locator('#scene-snapshot-strip')).to_be_visible()
    page.reload();page.wait_for_function('window.__appearanceCheck?.state.workspaceReady && !__appearanceCheck.state.sceneLoading');settle(page)
    assert page.evaluate('__appearanceCheck.state.snapshot.id')==second_id;expect(page.locator('#scene-snapshot-strip')).to_be_visible();assert_gallery_contract(page,'compare',expected_order)
    out=ROOT.parent/'inspection/unified-gallery';out.mkdir(parents=True,exist_ok=True)
    for width,height in [(1440,960),(390,844),(340,844)]:
     page.set_viewport_size({'width':width,'height':height})
     for layout in ['compare','immersive']:
      control(page,'#comparison-layout-button' if layout=='compare' else '#immersive-toggle').click();settle(page)
      if layout=='immersive' and page.locator('#immersive-reference-toggle').get_attribute('aria-expanded')=='false':control(page,'#immersive-reference-toggle').click();settle(page)
      assert_gallery_contract(page,layout,expected_order);box=gallery.bounding_box();assert box['x']>=0 and box['x']+box['width']<=width+1
      page.locator(f'.snapshot-open[data-snapshot-id="{third_id}"]').scroll_into_view_if_needed();page.locator(f'.snapshot-open[data-snapshot-id="{third_id}"]').click()
      assert page.evaluate('__appearanceCheck.state.snapshot.id')==third_id
      assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
      if layout=='immersive' and width<=390:
       ref=bounds(page,'#reference-pane');assert ref['y']>=box['y']+box['height'],(ref,box)
      page.screenshot(path=str(out/f'{layout}-{width}.png'))
      control(page,'#theme-toggle').click();settle(page);page.screenshot(path=str(out/f'{layout}-{width}-dark.png'));control(page,'#theme-toggle').click();settle(page)
    print('PASS restored mixed creation order, deletion/undo, nonreused names, reload and permanent gallery at desktop/390/340 in both layouts',flush=True)
    page.set_viewport_size({'width':1440,'height':960});control(page,'#comparison-layout-button').click();settle(page);control(page,'#chat-launcher').click();page.locator('#feedback-note').fill('检查不同时间的场景差异');
    evidence_before=page.evaluate('__appearanceCheck.evidence()');order_before=card_order()
    def reject(route):route.fulfill(status=503,content_type='application/json',body='{"error":"isolated gallery retry"}')
    page.route('**/api/sessions/*/feedback',reject);control(page,'#submit-button').click();page.wait_for_function('!__appearanceCheck.state.submitting')
    assert page.evaluate('__appearanceCheck.evidence()')==evidence_before and card_order()==order_before
    expect(gallery).to_be_visible();page.unroute('**/api/sessions/*/feedback',reject)
    control(page,'#submit-button').click();page.wait_for_function('__appearanceCheck.state.feedbackCount===1 && !__appearanceCheck.state.submitting',timeout=30000)
    saved=store.state['feedback'][-1];assert len(saved['dynamic_frames'])==3 and len(saved['scene_snapshots'])==2 and len(saved['annotations'])==4
    assert 'scope' not in saved['timeline']
    for frame in saved['dynamic_frames']:
     assert 'time_sec' in frame and 'reference_time_sec' in frame
     assert not {'scope','start_sec','end_sec','range','time_range','duration_sec'}.intersection(frame)
    for mark in saved['annotations']:assert mark['time_sec']==(0 if mark['pane']=='scene' else .5)
    expect(gallery).to_be_visible();expect(page.locator('#scene-live-card')).to_have_attribute('aria-pressed','true');expect(page.locator('.snapshot-card:not([data-kind=live])')).to_have_count(0);expect(page.locator('#timeline-marks button')).to_have_count(0)
    page.reload();page.wait_for_function('window.__appearanceCheck?.state.workspaceReady && !__appearanceCheck.state.sceneLoading');settle(page)
    assert_gallery_contract(page,'compare',[]);expect(page.locator('#scene-live-card')).to_have_attribute('aria-pressed','true');expect(page.locator('.snapshot-card')).to_have_count(1)
    assert not errors,errors
    print('PASS real isolated feedback retains original per-frame evidence and clears both unified entry points after saving',flush=True)
    browser.close()
  finally:server.shutdown();server.server_close();thread.join(timeout=3)
if __name__=='__main__':main()

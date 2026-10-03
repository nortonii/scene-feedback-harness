"""Permanent gallery and collapsible immersive controls, isolated from Codex."""
from pathlib import Path
import sys,tempfile,threading
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'examples/room_demo')]
from core import SceneStore
from server import make_server
from build_scene import build
from playwright.sync_api import sync_playwright,expect
from immersive_theme_smoke import HOOK,wait_ready,settle
from workspace_ui_helpers import control

def main():
 with tempfile.TemporaryDirectory(prefix='canvas-controls-',dir=ROOT.parent/'tmp') as folder:
  tmp=Path(folder);seed=SceneStore(tmp/'data');session=seed.create_session(reference_images=[str(ROOT/'examples/room_demo/reference.png')]);seed.set_scene_preview(str(build(ROOT/'examples/room_demo/scene.json',tmp/'room.glb')))
  server=make_server(port=0,data_dir=tmp/'data',project_dir=ROOT,web_dir=ROOT/'web',enable_codex=False);store=server.scene_store;server.workspace_gateway.ensure(session['session_id']);store.workspace_agent(status='idle');threading.Thread(target=server.serve_forever,daemon=True).start()
  try:
   with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,args=['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader','--disable-accelerated-2d-canvas']);page=browser.new_page(viewport={'width':1440,'height':900});errors=[];page.on('pageerror',lambda error:errors.append(str(error)));page.route('**/app.js',lambda route:route.fulfill(status=200,content_type='text/javascript',body=(ROOT/'web/app.js').read_text()+HOOK));page.goto(server.browser_url(session['session_id']));wait_ready(page);settle(page)
    control(page,'#chat-collapse').click();expect(page.locator('#chat-dock')).to_be_hidden()
    for i in range(3):
     if i:control(page,'#scene-live-card').click();page.evaluate('__appearanceCheck.camera.position.x+=.6;__appearanceCheck.controls.update()')
     control(page,'#capture-scene-button').click()
    control(page,'#annotation-tools-close').click()
    before=page.evaluate('({evidence:__appearanceCheck.evidence(),pose:__appearanceCheck.pose()})')
    gallery=page.locator('#scene-snapshots');toolbar=page.locator('#scene-toolbar-toggle');head=page.locator('#scene-toolbar')
    out=ROOT.parent/'inspection/canvas-controls';out.mkdir(parents=True,exist_ok=True)
    for width in [1440,390,340]:
     page.set_viewport_size({'width':width,'height':900 if width>640 else 844});settle(page)
     for mode in ['compare','immersive']:
      control(page,'#immersive-toggle' if mode=='immersive' else '#comparison-layout-button').click();settle(page)
      for theme in ['light','dark']:
       if page.locator('html').get_attribute('data-theme')!=theme:control(page,'#theme-toggle').click();settle(page)
       expect(gallery).to_be_visible();expect(page.locator('#scene-live-card')).to_be_visible()
       expect(page.locator('.snapshot-card:not([data-kind=live])')).to_have_count(3)
       assert gallery.evaluate("el=>el.parentElement===document.body") == (mode=='immersive')
       r=gallery.bounding_box();assert r['x']>=0 and r['x']+r['width']<=width+1
       if mode=='immersive':
        button_box=toolbar.bounding_box();assert button_box['x']+button_box['width']<=width
        # The fixed reveal button must not cover any real tool target.
        for element in head.locator('button:visible,summary:visible').all():
         r=element.bounding_box();assert r['x']+r['width']<=button_box['x']+1 or r['y']>=button_box['y']+button_box['height']-1,(width,r,button_box)
        page.screenshot(path=str(out/f'open-{theme}-{width}.png'))
    assert page.evaluate('({evidence:__appearanceCheck.evidence(),pose:__appearanceCheck.pose()})')==before
    print('PASS permanent live/saved gallery stays reachable in both layouts, themes and desktop/narrow widths',flush=True)
    page.set_viewport_size({'width':1440,'height':900});settle(page)
    control(page,'.view-popover > summary').click();expect(page.locator('.view-popover')).to_have_attribute('open','')
    button_box=toolbar.bounding_box();toolbar.click();expect(head).to_be_hidden();assert head.evaluate('el=>el.inert');assert not page.locator('.view-popover').evaluate('el=>el.open');assert toolbar.bounding_box()==button_box
    toolbar.press('Tab');assert not page.evaluate('document.querySelector("#scene-toolbar").contains(document.activeElement)')
    expect(gallery).to_be_visible();expect(page.locator('.snapshot-card:not([data-kind=live])')).to_have_count(3)
    page.screenshot(path=str(out/'collapsed-dark-1440.png'))
    for _ in range(3):
     toolbar.click();page.wait_for_timeout(60);toolbar.click();page.wait_for_timeout(60)
    expect(head).to_be_hidden();assert page.evaluate('({evidence:__appearanceCheck.evidence(),pose:__appearanceCheck.pose()})')==before
    page.reload();page.wait_for_function('window.__appearanceCheck && !__appearanceCheck.state.sceneLoading');settle(page);expect(head).to_be_hidden();expect(toolbar).to_have_attribute('aria-expanded','false');expect(gallery).to_be_visible()
    control(page,'#comparison-layout-button').click();settle(page);expect(head).to_be_visible();assert not head.evaluate('el=>el.inert');expect(toolbar).to_be_hidden()
    control(page,'#immersive-toggle').click();settle(page);expect(head).to_be_hidden();toolbar.click();expect(head).to_be_visible();control(page,'#immersive-reference-toggle').click();settle(page);expect(page.locator('#reference-pane')).to_be_visible()
    toolbar.click();expect(head).to_be_hidden();page.locator('#immersive-reference-close').click();expect(toolbar).to_be_focused()
    print('PASS immersive toolbar closes its menus, keeps a fixed keyboard-accessible reveal button, and restores preference across reload/layout changes',flush=True)
    page.set_viewport_size({'width':340,'height':844});settle(page);page.screenshot(path=str(out/'collapsed-dark-340.png'))
    page.emulate_media(reduced_motion='reduce');toolbar.click();expect(head).to_be_visible();toolbar.click();expect(head).to_be_hidden();assert head.evaluate('el=>getComputedStyle(el).transitionDuration')=='0s';expect(page.locator('#scene-snapshot-strip')).to_be_visible()
    assert not errors,errors;assert not store.list_all_feedback();browser.close()
    print('PASS reduced motion, narrow collapsed controls, reference focus return and no browser errors or feedback changes',flush=True)
  finally:server.shutdown();server.server_close()
if __name__=='__main__':main()

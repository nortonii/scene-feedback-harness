"""Drawer navigation, focus, motion, canvas geometry and responsive appearance.
Uses an isolated server and never launches Codex or submits production feedback.
"""
from pathlib import Path
import sys,tempfile,threading
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'examples/room_demo')]
from core import SceneStore
from server import make_server
from build_scene import build
from playwright.sync_api import sync_playwright,expect
from immersive_theme_smoke import HOOK,settle,bounds

def main():
 with tempfile.TemporaryDirectory(prefix='sidebar-',dir=ROOT.parent/'tmp') as directory:
  tmp=Path(directory);store=SceneStore(tmp/'data');session=store.create_session(reference_images=[str(ROOT/'examples/room_demo/reference.png')]);store.set_scene_preview(str(build(ROOT/'examples/room_demo/scene.json',tmp/'room.glb')))
  server=make_server(port=0,data_dir=tmp/'data',project_dir=ROOT,web_dir=ROOT/'web',enable_codex=False);store=server.scene_store;server.workspace_gateway.ensure(session['session_id']);store.workspace_agent(status='idle');threading.Thread(target=server.serve_forever,daemon=True).start()
  try:
   with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,args=['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader','--disable-accelerated-2d-canvas'])
    page=browser.new_page(viewport={'width':1440,'height':960});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.route('**/app.js',lambda route:route.fulfill(status=200,content_type='text/javascript',body=(ROOT/'web/app.js').read_text()+HOOK))
    page.goto(server.browser_url(session['session_id']));page.wait_for_function('window.__appearanceCheck?.state.workspaceReady && !__appearanceCheck.state.sceneLoading');settle(page)
    dialog=page.locator('#projects-dialog');launch=page.locator('#projects-dialog-button');close=page.locator('#close-projects');out=ROOT.parent/'inspection/workspace-sidebar';out.mkdir(parents=True,exist_ok=True)
    def open_sidebar():
     launch.click();page.wait_for_function("document.querySelector('#projects-dialog').dataset.phase==='open'")
    def close_sidebar():
     close.click();expect(dialog).not_to_be_visible()
    scene=bounds(page,'#scene-stage');pose=page.evaluate('__appearanceCheck.pose()');assert scene['y']<80,scene
    assert page.locator('.topbar button:visible').count()==1
    open_sidebar();assert bounds(page,'#scene-stage')==scene;assert page.evaluate('__appearanceCheck.pose()')==pose
    expect(close).to_be_focused();assert abs(bounds(page,'.sidebar-panel')['width']-320)<2
    page.locator('#sidebar-new-project').click();page.locator('#project-name').fill('侧栏草稿保留')
    expect(page.locator('#project-effort')).to_be_hidden();page.locator('.sidebar-advanced summary').click();expect(page.locator('#project-permissions')).to_be_visible()
    expect(page.locator('#project-options-summary')).to_contain_text('工作区写入')
    dialog.locator('[data-sidebar-home]:visible').click();page.locator('#help-button').click();expect(page.locator('#help-dialog')).to_be_visible();assert page.locator('dialog[open]').count()==1
    page.locator('#help-dialog .sidebar-page-body').evaluate('el=>el.scrollTop=200');scroll=page.locator('#help-dialog .sidebar-page-body').evaluate('el=>el.scrollTop')
    dialog.locator('[data-sidebar-home]:visible').click();page.locator('#sidebar-new-project').click();expect(page.locator('#project-name')).to_have_value('侧栏草稿保留');expect(page.locator('#project-permissions')).to_have_value('workspace_write')
    close_sidebar();expect(launch).to_be_focused();open_sidebar();expect(page.locator('#project-name')).to_have_value('侧栏草稿保留')
    dialog.locator('[data-sidebar-home]:visible').click();page.locator('#help-button').click();page.wait_for_timeout(250);page.screenshot(path=str(out/'help-scroll.png'));assert page.locator('#help-dialog .sidebar-page-body').evaluate('el=>el.scrollTop')==scroll,(scroll,page.locator('#help-dialog .sidebar-page-body').evaluate('el=>el.scrollTop'))
    page.keyboard.press('Escape');expect(dialog).not_to_be_visible();expect(launch).to_be_focused()
    print('PASS reclaimed canvas height, stable camera, single drawer, form and scroll preservation, Escape and focus restoration',flush=True)
    open_sidebar();dialog.locator('[data-sidebar-home]:visible').click();page.locator('#activity-dialog-button').click();expect(page.locator('#activity-dialog')).to_be_visible();dialog.locator('[data-sidebar-home]:visible').click()
    # Native modal confines keyboard navigation to the drawer.
    for _ in range(16):
     page.keyboard.press('Tab');assert page.evaluate("!!document.activeElement.closest('#projects-dialog')")
    page.mouse.click(900,300);expect(dialog).not_to_be_visible()
    open_sidebar();page.evaluate("document.querySelector('#close-projects').click()")
    page.locator('.sidebar-panel').evaluate('el=>{const a=el.getAnimations()[0];a.pause();a.currentTime=90;}');part=page.locator('.sidebar-panel').evaluate('el=>el.getBoundingClientRect().x');assert -320<part<12,(part,dialog.get_attribute('data-phase'))
    page.evaluate("document.querySelector('#close-projects').click()")
    page.wait_for_function("document.querySelector('#projects-dialog').dataset.phase==='open'");expect(dialog).to_be_visible();assert abs(bounds(page,'.sidebar-panel')['x']-12)<1
    close_sidebar();page.emulate_media(reduced_motion='reduce');open_sidebar();close_sidebar();page.emulate_media(reduced_motion='no-preference')
    print('PASS keyboard containment, outside dismissal, interrupted animation reversal and reduced motion',flush=True)
    for width,height in [(1440,960),(390,844),(340,760)]:
     page.set_viewport_size({'width':width,'height':height});open_sidebar()
     for layout in ['compare','immersive']:
      page.locator('#comparison-layout-button' if layout=='compare' else '#immersive-toggle').click();settle(page);expect(dialog).to_be_visible()
      for theme in ['light','dark']:
       if page.locator('html').get_attribute('data-theme')!=theme:page.locator('#theme-toggle').click();settle(page)
       expect(dialog).to_be_visible();r=bounds(page,'.sidebar-panel');assert r['x']>=0 and r['x']+r['width']<=width
       assert page.locator('.sidebar-panel').evaluate('el=>el.scrollWidth<=el.clientWidth+1')
       page.screenshot(path=str(out/f'sidebar-{layout}-{theme}-{width}.png'))
       close_sidebar();assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
       if layout=='immersive':
        toolbar=bounds(page,'.scene-pane > .pane-head');assert toolbar['y']==12;assert toolbar['x']>=64
       page.screenshot(path=str(out/f'canvas-{layout}-{theme}-{width}.png'));open_sidebar()
     close_sidebar()
    assert not errors,errors
    print('PASS day/night and compare/immersive keep drawer open; 1440/390/340 px layouts stay inside viewport; no page errors',flush=True)
    page.set_viewport_size({'width':1440,'height':960})
    if page.locator('#chat-collapse').is_visible():page.locator('#chat-collapse').click()
    expect(page.locator('#chat-dock')).to_be_hidden()
    with store.lock:
     store.state['workspace']['approvals']=[{'approval_id':'sidebar-review-fixture','request_id':'fixture-command','kind':'item/commandExecution/requestApproval','details':{'reason':'隔离审批显示检查','command':'fixture only'},'prompt':'fixture'}];store._save()
    store.workspace_agent(status='awaiting_approval');page.evaluate('__appearanceCheck.refreshWorkspace()')
    expect(page.locator('#chat-launcher')).to_contain_text('待确认')
    expect(page.locator('#session-pill')).to_be_hidden();assert page.locator('#stop-button').evaluate("el=>!!el.closest('#chat-dock')")
    open_sidebar();expect(page.locator('#attention-count')).to_be_hidden();page.locator('#activity-dialog-button').click();page.locator('#review-pending').click()
    expect(dialog).to_be_hidden();expect(page.locator('[data-approval-id="sidebar-review-fixture"]')).to_be_focused()
    expect(page.locator('#chat-approvals')).to_be_visible();assert not errors,errors
    print('PASS attention stays in conversation and activity review closes drawer before focusing the actual approval',flush=True)
    browser.close()
  finally:server.shutdown();server.server_close()

if __name__=='__main__':main()

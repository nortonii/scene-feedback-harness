"""Isolated browser regressions for the immersive workspace and dark appearance.

Uses an ephemeral loopback server and temporary scene data; never starts Codex or
changes the user's sessions. Run with Playwright and its Chromium installed.
"""
from __future__ import annotations

from pathlib import Path
import base64
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'examples/room_demo')]
from core import SceneStore  # noqa: E402
from server import make_server  # noqa: E402
from build_scene import build  # noqa: E402
from playwright.sync_api import sync_playwright, expect  # noqa: E402
from workspace_ui_helpers import control, open_annotation_tools, choose_tool

HOOK = '''
window.__appearanceCheck = {
  state, camera, controls, refreshWorkspace, setReferenceClip, promptText,
  citedImages: () => collectImageReferences(promptText(),state.imageRefs),
  background: () => threeScene.background.getHexString(),
  model: () => JSON.stringify([...state.objectNodes.values()].flatMap(root => {
    const rows = [];
    root.traverse(node => {
      if (!node.isMesh) return;
      rows.push({transform: node.matrixWorld.toArray(),
        attributes: Object.fromEntries(Object.entries(node.geometry.attributes)
          .map(([name, value]) => [name, Array.from(value.array)])),
        index: node.geometry.index ? Array.from(node.geometry.index.array) : null,
        materials: (Array.isArray(node.material) ? node.material : [node.material])
          .map(material => material.toJSON())});
    });
    return rows;
  })),
  evidence: () => JSON.stringify({snapshots: state.sceneSnapshots,
    annotations: state.annotations, snapshot: state.snapshot}),
  pose: () => ({position: camera.position.toArray(), up: camera.up.toArray(),
    target: controls.target.toArray()})
};
'''


def wait_ready(page):
    page.wait_for_function('''window.__appearanceCheck &&
      __appearanceCheck.state.workspaceReady && !__appearanceCheck.state.sceneLoading &&
      document.querySelector('#reference-image').naturalWidth > 0''', timeout=20000)
    expect(page.locator('#capture-scene-button')).to_be_enabled()


def settle(page):
    # Include the CSS transition and ResizeObserver/renderer update that follows.
    page.wait_for_timeout(800)


def bounds(page, selector):
    result = page.locator(selector).bounding_box()
    assert result is not None, f'{selector} is hidden'
    return result


def assert_inside(page, selector):
    box = bounds(page, selector)
    viewport = page.viewport_size
    assert box['x'] >= -1 and box['y'] >= -1, (selector, box)
    assert box['x'] + box['width'] <= viewport['width'] + 1, (selector, box)
    assert box['y'] + box['height'] <= viewport['height'] + 1, (selector, box)
    assert box['width'] > 15 and box['height'] > 15, (selector, box)


def assert_full_scene(page):
    for selector in ('#scene-stage', '#viewport', '#viewport canvas'):
        box = bounds(page, selector)
        assert abs(box['x']) <= 1 and abs(box['y']) <= 1, (selector, box)
        assert abs(box['width'] - page.viewport_size['width']) <= 1, (selector, box)
        assert abs(box['height'] - page.viewport_size['height']) <= 1, (selector, box)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')


def point(page, selector, x=.5, y=.55):
    box = bounds(page, selector)
    page.mouse.click(box['x'] + box['width'] * x, box['y'] + box['height'] * y)


def contrast(page, selector):
    """Contrast against composited ancestor backgrounds, including glass alpha."""
    return page.locator(selector).evaluate('''el => {
      const rgb = value => (value.match(/[\\d.]+/g) || []).map(Number);
      const chain = []; for (let node=el; node; node=node.parentElement) chain.push(node);
      let bg = [255,255,255];
      for (const node of chain.reverse()) {
        const c = rgb(getComputedStyle(node).backgroundColor), a = c[3] ?? 1;
        if(c.length >= 3) bg = bg.map((v,i) => v * (1-a) + c[i] * a);
      }
      const fg = rgb(getComputedStyle(el).color);
      const luminance = c => c.slice(0,3).map(v => {
        v/=255; return v <= .04045 ? v/12.92 : ((v+.055)/1.055)**2.4;
      }).reduce((sum,v,i) => sum + v * [.2126,.7152,.0722][i],0);
      const a=luminance(fg), b=luminance(bg);
      return {contrast:(Math.max(a,b)+.05)/(Math.min(a,b)+.05), fg:a, bg:b};
    }''')


def main():
    with tempfile.TemporaryDirectory(prefix='immersive-theme-', dir=ROOT.parent / 'tmp') as directory:
        tmp = Path(directory)
        seed = SceneStore(tmp / 'data')
        session = seed.create_session(reference_images=[str(ROOT / 'examples/room_demo/reference.png')])
        seed.set_scene_preview(str(build(ROOT / 'examples/room_demo/scene.json', tmp / 'room.glb')))
        server = make_server(port=0, data_dir=tmp / 'data', project_dir=ROOT,
                             web_dir=ROOT / 'web', enable_codex=False)
        server.workspace_gateway.ensure(session['session_id'])
        server.scene_store.workspace_agent(status='idle')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, args=[
                    '--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader',
                    '--enable-unsafe-swiftshader', '--disable-accelerated-2d-canvas'])
                page = browser.new_page(viewport={'width':1440, 'height':900}, color_scheme='light')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                source = (ROOT / 'web/app.js').read_text() + HOOK
                page.route('**/app.js', lambda route: route.fulfill(
                    status=200, content_type='text/javascript', body=source))
                page.goto(server.browser_url(session['session_id']))
                wait_ready(page)
                expect(page.locator('html')).to_have_attribute('data-layout', 'compare')
                expect(page.locator('html')).to_have_attribute('data-theme', 'light')
                expect(page.locator('#immersive-tools-toggle')).to_have_count(0)
                expect(page.locator('#annotation-tool-panel')).to_be_hidden()
                initial_scene = bounds(page, '#scene-stage')
                assert bounds(page, '.reference-pane')['x'] < initial_scene['x']
                if page.locator('#chat-collapse').is_visible():
                    control(page, '#chat-collapse').click()
                    expect(page.locator('#chat-dock')).to_be_hidden()
                model = page.evaluate('__appearanceCheck.model()')
                light_background = page.evaluate('__appearanceCheck.background()')
                control(page, '#capture-scene-button').click()
                control(page, 'button[data-tool="point"]').click()
                point(page, '#scene-annotations', .6, .55)
                page.wait_for_function('__appearanceCheck.state.annotations.length === 1')
                evidence = page.evaluate('__appearanceCheck.evidence()')
                control(page, '#scene-live-card').click()
                pose = page.evaluate('__appearanceCheck.pose()')
                control(page, '#immersive-toggle').click()
                expect(page.locator('html')).to_have_attribute('data-layout', 'immersive')
                settle(page)
                assert_full_scene(page)
                expect(page.locator('#annotation-tool-panel')).to_be_hidden()
                assert page.locator('#annotation-tool-panel').evaluate('el => el.inert')
                control(page, '#immersive-reference-toggle').click()
                open_annotation_tools(page, 'reference')
                expect(page.locator('#annotation-context-label')).to_have_text('参考')
                assert not page.locator('#annotation-tool-panel').evaluate('el => el.inert')
                expect(page.locator('button[data-tool="point"]')).to_be_visible()
                control(page, '#annotation-tools-close').click()
                expect(page.locator('#annotation-tool-panel')).to_be_hidden()
                control(page, '#immersive-reference-toggle').click()
                page.evaluate('__appearanceCheck.refreshWorkspace()')
                expect(page.locator('#annotation-tool-panel')).to_be_hidden()
                assert page.locator('.reference-pane').evaluate('el => el.inert')
                expect(page.locator('html')).to_have_attribute('data-reference-visible', 'false')
                assert page.evaluate('__appearanceCheck.model()') == model
                actual_pose = page.evaluate('__appearanceCheck.pose()')
                assert max(abs(a-b) for key in pose for a,b in zip(pose[key],actual_pose[key])) < 1e-8, (pose, actual_pose)
                assert page.evaluate('__appearanceCheck.evidence()') == evidence
                print('PASS full-window 3D, hidden inert reference and preserved camera/model/screenshot/marks', flush=True)

                control(page, '#theme-toggle').click()
                expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
                settle(page)
                dark_background = page.evaluate('__appearanceCheck.background()')
                assert dark_background != light_background
                assert int(dark_background[0:2], 16) < int(light_background[0:2], 16)
                for selector in ('#theme-toggle', '#immersive-toggle', '#ground-axis'):
                    colors = contrast(page, selector)
                    assert colors['contrast'] >= 4.5, (selector, colors)
                    assert colors['bg'] < .15, (selector, colors)
                assert page.evaluate('__appearanceCheck.model()') == model
                assert page.evaluate('__appearanceCheck.evidence()') == evidence
                assert page.evaluate("localStorage.getItem('astra-appearance-theme')") == 'dark'
                assert page.evaluate("localStorage.getItem('astra-workspace-layout')") == 'immersive'
                page.reload()
                wait_ready(page)
                settle(page)
                expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
                expect(page.locator('html')).to_have_attribute('data-layout', 'immersive')
                assert_full_scene(page)
                assert page.evaluate('__appearanceCheck.evidence()') == evidence
                print('PASS dark UI contrast/renderer, unmodified materials and persisted theme/layout/evidence', flush=True)

                control(page, '#immersive-reference-toggle').click()
                expect(page.locator('html')).to_have_attribute('data-reference-visible', 'true')
                settle(page)
                assert not page.locator('.reference-pane').evaluate('el => el.inert')
                assert_inside(page, '.reference-pane')
                open_annotation_tools(page, 'reference')
                expect(page.locator('#annotation-context-label')).to_have_text('参考')
                assert not page.locator('#annotation-tool-panel').evaluate('el => el.inert')
                control(page, 'button[data-tool="point"]').click()
                point(page, '#reference-annotations')
                page.wait_for_function('__appearanceCheck.state.annotations.length === 2')
                assert page.evaluate('__appearanceCheck.state.annotations[1].pane') == 'reference'
                control(page, '#help-button').click()
                expect(page.locator('#help-dialog')).to_be_visible()
                page.keyboard.press('Escape')
                expect(page.locator('#help-dialog')).to_be_hidden()
                expect(page.locator('html')).to_have_attribute('data-reference-visible', 'true')
                page.keyboard.press('Escape')
                expect(page.locator('html')).to_have_attribute('data-reference-visible', 'false')
                expect(page.locator('html')).to_have_attribute('data-layout', 'immersive')
                evidence = page.evaluate('__appearanceCheck.evidence()')
                if page.locator('#annotation-tool-panel').is_visible():
                    control(page, '#annotation-tools-close').click()
                expect(page.locator('#annotation-tool-panel')).to_be_hidden()
                page.evaluate('__appearanceCheck.refreshWorkspace()')
                expect(page.locator('#annotation-tool-panel')).to_be_hidden()
                page.locator('.snapshot-card:not([data-kind=live]) .snapshot-open').last.click()
                open_annotation_tools(page, 'scene')
                expect(page.locator('#annotation-tool-panel')).to_be_visible()
                expect(page.locator('#annotation-context-label')).to_have_text('截图')
                assert not page.locator('#annotation-tool-panel').evaluate('el => el.inert')
                assert page.evaluate('__appearanceCheck.state.sceneView') == 'snapshot'
                control(page, '#comparison-layout-button').click()
                settle(page)
                expect(page.locator('html')).to_have_attribute('data-layout', 'compare')
                restored_scene = bounds(page, '#scene-stage')
                assert abs(restored_scene['width'] - initial_scene['width']) < 2
                assert not page.locator('.reference-pane').evaluate('el => el.inert')
                assert not page.locator('#annotation-tool-panel').evaluate('el => el.inert')
                assert page.evaluate('__appearanceCheck.evidence()') == evidence
                assert page.evaluate('__appearanceCheck.state.sceneView') == 'snapshot'
                print('PASS annotatable floating reference, dialog-safe Escape and restored comparison with frozen evidence', flush=True)

                control(page, '#scene-live-card').click()
                control(page, '#immersive-toggle').click()
                settle(page)
                out = ROOT.parent / 'inspection/immersive-theme'
                out.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(out / 'desktop-dark.png'))
                for width, height in ((390,844), (1440,900)):
                    page.set_viewport_size({'width':width, 'height':height})
                    settle(page)
                    assert_full_scene(page)
                    for selector in ('#projects-dialog-button',
                                     '#immersive-reference-toggle', '#scene-live-card',
                                     '#capture-scene-button', '#ground-axis', '#chat-launcher'):
                        assert_inside(page, selector)
                    control(page, '#immersive-reference-toggle').click()
                    settle(page)
                    assert_inside(page, '.reference-pane')
                    page.screenshot(path=str(out / ('mobile-reference.png' if width == 390 else 'desktop-reference.png')))
                    control(page, '#immersive-reference-toggle').click()
                    assert_inside(page, '#compare-opacity')
                    assert_inside(page, '#align-reference-button')
                control(page, '#theme-toggle').click()
                settle(page)
                expect(page.locator('html')).to_have_attribute('data-theme', 'light')
                assert page.evaluate('__appearanceCheck.background()') == light_background
                page.screenshot(path=str(out / 'desktop-light.png'))
                print('PASS desktop/mobile full-window rendering, reference/camera/toolbar reachability and light restoration', flush=True)

                page.emulate_media(reduced_motion='reduce')
                control(page, '#comparison-layout-button').click()
                page.wait_for_timeout(50)
                expect(page.locator('html')).to_have_attribute('data-layout', 'compare')
                animations = page.evaluate('''document.getAnimations().filter(a => {
                  const target=a.effect?.target;
                  return target instanceof Element && !target.closest('.chat-launcher-spinner') &&
                    a.playState==='running' && Number(a.effect.getTiming().duration)>100;
                }).map(a=>({target:a.effect.target.id || a.effect.target.className,
                  duration:a.effect.getTiming().duration}))''')
                assert not animations, animations
                control(page, '#immersive-toggle').click()
                page.wait_for_timeout(80)
                assert_full_scene(page)
                assert page.evaluate('__appearanceCheck.evidence()') == evidence
                page.emulate_media(reduced_motion='no-preference')
                page.evaluate('document.startViewTransition = undefined')
                control(page, '#comparison-layout-button')
                page.locator('#comparison-layout-button').click()
                expect(page.locator('html')).to_have_attribute('data-layout', 'compare')
                assert page.locator('#scene-stage').evaluate('el => el.getAnimations().some(a => a.effect.getTiming().duration === 500)')
                settle(page)
                control(page, '#immersive-toggle').click()
                expect(page.locator('html')).to_have_attribute('data-layout', 'immersive')
                settle(page)
                assert_full_scene(page)
                assert page.evaluate('__appearanceCheck.evidence()') == evidence
                print('PASS contextual annotation tools, manual collapse respected by polling and animated fallback without View Transition API', flush=True)
                image_url = 'data:image/png;base64,' + base64.b64encode((ROOT / 'examples/room_demo/reference.png').read_bytes()).decode()
                server.scene_store.set_reference_clip(session['session_id'], {'name':'reference-video.mp4', 'fps':2,
                    'frames':[{'name':'frame0.png','data_url':image_url,'time_sec':0},
                              {'name':'frame1.png','data_url':image_url,'time_sec':.5}]})
                page.reload(); wait_ready(page); settle(page)
                expect(page.locator('#reference-pane > #timeline-panel')).to_have_count(1)
                expect(page.locator('#timeline-panel')).to_be_hidden()
                assert float(page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--immersive-bottom').replace('px','')")) == 18
                control(page, '#immersive-reference-toggle').click(); settle(page)
                expect(page.locator('#timeline-panel')).to_be_visible()
                for width,height in ((1440,900),(390,844)):
                    page.set_viewport_size({'width':width,'height':height}); settle(page)
                    ref=bounds(page,'.reference-pane'); timeline=bounds(page,'#timeline-panel')
                    assert timeline['x'] >= ref['x'] and timeline['x']+timeline['width'] <= ref['x']+ref['width']+1
                    assert timeline['y'] >= ref['y'] and timeline['y']+timeline['height'] <= ref['y']+ref['height']+1
                    for selector in ('#timeline-seek','#timeline-play','#timeline-time'):
                        assert_inside(page,selector)
                    control(page, '.timeline-options summary').click()
                    assert_inside(page,'.timeline-details');assert_inside(page,'#save-moment')
                    expect(page.locator('#feedback-scope, #range-start, #range-end')).to_have_count(0);page.keyboard.press('Escape')
                    page.screenshot(path=str(out / ('reference-video-mobile.png' if width==390 else 'reference-video-desktop.png')))
                control(page, '#timeline-next').click()
                page.wait_for_function('__appearanceCheck.state.time > 0')
                control(page, '#timeline-prev').click()
                page.wait_for_function('__appearanceCheck.state.time === 0')
                control(page, '#timeline-play').click();expect(page.locator('#timeline-play')).to_have_text('暂停')
                control(page, '#timeline-play').click()
                page.locator('#timeline-seek').focus();page.keyboard.press('End')
                # Single-view playback snaps to an actual frame; this fixture ends at 0.5s.
                page.wait_for_function('__appearanceCheck.state.time === .5')
                page.keyboard.press('Home');page.wait_for_function('__appearanceCheck.state.time === 0')
                slider=bounds(page,'#timeline-seek')
                page.mouse.move(slider['x']+slider['width']*.25,slider['y']+slider['height']/2)
                page.mouse.down();page.mouse.move(slider['x']+slider['width']*.75,slider['y']+slider['height']/2,steps=8);page.mouse.up()
                page.wait_for_function('__appearanceCheck.state.time === .5')
                control(page, '#immersive-reference-toggle').click();settle(page)
                expect(page.locator('#timeline-panel')).to_be_hidden()
                page.set_viewport_size({'width':1440,'height':900});settle(page)
                control(page, '#scene-live-card').click()
                page.mouse.move(40,650);page.wait_for_timeout(200)
                expect(page.locator('#scene-stage #scene-hint')).to_have_count(0)
                assert_inside(page,'#ground-axis')
                assert_inside(page,'#compare-opacity')
                before=page.evaluate('__appearanceCheck.pose()')
                viewport=bounds(page,'#viewport canvas')
                px=viewport['x']+viewport['width']*.47;py=viewport['y']+viewport['height']*.52
                page.mouse.move(px,py);page.mouse.down();page.mouse.move(px+65,py+35,steps=8);page.mouse.up()
                page.wait_for_timeout(250)
                assert page.evaluate('__appearanceCheck.pose()')!=before
                page.screenshot(path=str(out/'direct-camera-controls.png'))
                control(page, '#comparison-layout-button').click();settle(page)
                expect(page.locator('#reference-pane > #timeline-panel')).to_be_visible()
                assert bounds(page,'#timeline-panel')['width'] < 740
                pane=bounds(page,'.scene-pane');header=bounds(page,'.scene-pane > .pane-head')
                gallery=bounds(page,'#scene-snapshots');stage=bounds(page,'#scene-stage')
                assert stage['height'] > 500,stage
                gallery_margin=page.locator('#scene-snapshots').evaluate('el=>parseFloat(getComputedStyle(el).marginBottom) || 0')
                assert abs(pane['height']-header['height']-gallery['height']-gallery_margin-stage['height']) < 2,(pane,header,gallery,stage)
                assert header['y']+header['height'] <= gallery['y']+1 and gallery['y']+gallery['height'] <= stage['y']+1,(header,gallery,stage)
                for row in (header,gallery,stage):
                    assert row['x'] >= pane['x']-1 and row['x']+row['width'] <= pane['x']+pane['width']+1,row
                    assert row['y'] >= pane['y']-1 and row['y']+row['height'] <= pane['y']+pane['height']+1,row
                assert pane['y']+pane['height'] <= 901,pane
                page.evaluate('__appearanceCheck.setReferenceClip(null)')
                expect(page.locator('.workspace > #timeline-panel')).to_have_count(1)
                print('PASS reference-owned video timeline, desktop/mobile controls and video options without feedback range, hidden-reference behavior, scene height below permanent cards, uncropped hint and animation-only fallback',flush=True)
                assert not errors, errors
                browser.close()
                print('PASS reduced-motion transitions, preserved accumulated evidence and no browser errors', flush=True)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == '__main__':
    main()

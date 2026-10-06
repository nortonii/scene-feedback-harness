"""Real UI checks for compact workspace controls; isolated data and no model calls."""
from __future__ import annotations

from pathlib import Path
import base64
import sys
import tempfile
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'examples/room_demo')]
from core import SceneStore
from server import make_server
from build_scene import build
from playwright.sync_api import sync_playwright, expect, TimeoutError as PlaywrightTimeoutError
from immersive_theme_smoke import HOOK, wait_ready, settle, assert_inside, assert_full_scene, point, bounds
from workspace_ui_helpers import control, open_annotation_tools, choose_tool


def unobscured(page, selector):
    # Menu placement is scheduled after <details> opens; wait for the real hit target.
    try:
        page.wait_for_function('''selector => {
          const el=document.querySelector(selector),r=el.getBoundingClientRect();
          const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
          return hit===el || el.contains(hit);
        }''',arg=selector,timeout=3000)
    except PlaywrightTimeoutError:
        pass  # The assertion below supplies the actual obstructing element.
    assert_inside(page, selector)
    hit = page.locator(selector).evaluate('''el => {
      const r=el.getBoundingClientRect(), hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
      return {ok:hit===el || el.contains(hit), hit:hit?.outerHTML.slice(0,300),
        layout:document.documentElement.dataset.layout,theme:document.documentElement.dataset.theme,
        width:innerWidth,rect:{x:r.x,y:r.y,width:r.width,height:r.height}};
    }''')
    assert hit['ok'], (selector, hit)



def verify_reference_window(page, store, session):
    """Move/resize the actual floating reference without touching scene evidence."""
    original_scene = bounds(page, '#scene-stage')
    evidence = page.evaluate('__appearanceCheck.evidence()')
    pose = page.evaluate('__appearanceCheck.pose()')
    def check_pose(step):
        actual = page.evaluate('__appearanceCheck.pose()')
        delta = max(abs(a-b) for key in pose for a,b in zip(pose[key],actual[key]))
        if delta >= 1e-8:
            draft_camera = page.evaluate('''() => JSON.parse(localStorage.getItem(
              'astra-visual-draft:' + new URL(location.href).searchParams.get('session_id')) || '{}').camera''')
            raise AssertionError((step,pose,actual,draft_camera))
    control(page, '#immersive-toggle').click(); settle(page)
    check_pose('enter immersive')
    if page.locator('#immersive-reference-toggle').get_attribute('aria-expanded') != 'true':
        control(page, '#immersive-reference-toggle').click(); settle(page)
    open_annotation_tools(page, 'reference')
    before = bounds(page, '#reference-pane')
    dock_before = bounds(page, '#annotation-tool-panel')
    title = bounds(page, '#reference-title')
    x,y=title['x']+title['width']/2,title['y']+title['height']/2
    page.mouse.move(x,y);page.mouse.down();page.mouse.move(x+150,y+80,steps=12);page.mouse.up()
    settle(page)
    check_pose('header drag')
    moved = bounds(page, '#reference-pane')
    assert abs(moved['x']-before['x']-150) < 2, (before,moved)
    assert abs(moved['y']-before['y']-80) < 2, (before,moved)
    assert abs(moved['width']-before['width']) < 1
    expect(page.locator('#annotation-tool-panel')).to_be_hidden()
    open_annotation_tools(page, 'reference')
    assert bounds(page,'#annotation-tool-panel')['x'] > dock_before['x']+100
    handle = bounds(page, '[data-reference-resize="se"]')
    x,y=handle['x']+handle['width']/2,handle['y']+handle['height']/2
    page.mouse.move(x,y);page.mouse.down();page.mouse.move(x+120,y+65,steps=12);page.mouse.up()
    settle(page)
    check_pose('SE resize')
    resized = bounds(page, '#reference-pane')
    assert abs(resized['width']-moved['width']-120) < 2, (moved,resized)
    assert abs(resized['height']-moved['height']-65) < 2, (moved,resized)
    open_annotation_tools(page, 'reference')
    assert_inside(page,'#annotation-tool-panel')
    # Keyboard alternatives also update the actual window, without modifying the camera.
    page.locator('#reference-pane > .pane-head').focus()
    page.keyboard.press('Shift+ArrowRight')
    page.locator('[data-reference-resize="se"]').focus()
    page.keyboard.press('ArrowDown'); settle(page)
    check_pose('keyboard move/resize')
    saved = bounds(page,'#reference-pane')
    assert abs(saved['x']-resized['x']-24) < 1
    assert abs(saved['height']-resized['height']-8) < 1
    preference = page.evaluate("localStorage.getItem('astra-reference-window:v1')")
    assert preference
    title = bounds(page,'#reference-title')
    x,y=title['x']+title['width']/2,title['y']+title['height']/2
    page.mouse.move(x,y);page.mouse.down();page.mouse.move(x+35,y+20,steps=6)
    page.keyboard.press('Escape');page.mouse.up();settle(page)
    expect(page.locator('#reference-pane')).to_be_visible()
    assert bounds(page,'#reference-pane') == saved
    assert page.evaluate("localStorage.getItem('astra-reference-window:v1')") == preference
    handle=bounds(page,'[data-reference-resize="nw"]')
    x,y=handle['x']+handle['width']/2,handle['y']+handle['height']/2
    page.mouse.move(x,y);page.mouse.down();page.mouse.move(x-35,y-20,steps=6)
    page.keyboard.press('Escape');page.mouse.up();settle(page)
    expect(page.locator('#reference-pane')).to_be_visible()
    assert bounds(page,'#reference-pane') == saved
    assert page.evaluate("localStorage.getItem('astra-reference-window:v1')") == preference
    open_annotation_tools(page, 'reference')
    control(page,'#annotation-tools-close').click()
    control(page,'#immersive-reference-toggle').click();settle(page)
    control(page,'#immersive-reference-toggle').click();settle(page)
    assert bounds(page,'#reference-pane') == saved
    check_pose('before reload')
    # Camera drafts deliberately serialize five decimals. Reload must restore
    # that stored pose, while every UI gesture above keeps the full live pose.
    saved_camera = page.evaluate('''() => JSON.parse(localStorage.getItem(
      'astra-visual-draft:' + new URL(location.href).searchParams.get('session_id'))).camera''')
    page.reload();wait_ready(page);settle(page)
    restored_pose = page.evaluate('__appearanceCheck.pose()')
    assert max(abs(a-b) for key in pose for a,b in zip(saved_camera[key],restored_pose[key])) < 1e-8, (saved_camera,restored_pose)
    pose = restored_pose
    control(page,'#immersive-reference-toggle').click();settle(page)
    assert bounds(page,'#reference-pane') == saved
    assert page.evaluate('__appearanceCheck.evidence()') == evidence
    page.set_viewport_size({'width':390,'height':844});settle(page)
    assert_inside(page,'#reference-pane')
    check_pose('narrow viewport')
    assert page.evaluate("localStorage.getItem('astra-reference-window:v1')") == preference
    page.set_viewport_size({'width':1440,'height':900});settle(page)
    assert bounds(page,'#reference-pane') == saved
    control(page,'#comparison-layout-button').click();settle(page)
    restored=bounds(page,'#scene-stage')
    assert abs(restored['width']-original_scene['width']) < 1
    assert not page.locator('#reference-pane').evaluate('el => el.inert')
    assert page.evaluate('__appearanceCheck.evidence()') == evidence
    check_pose('restored compare layout')
    control(page,'#immersive-toggle').click();settle(page)
    control(page,'#reference-window-reset').click();settle(page)
    assert page.evaluate("localStorage.getItem('astra-reference-window:v1')") is None
    default=bounds(page,'#reference-pane')
    assert abs(default['x']-before['x']) < 1 and abs(default['width']-before['width']) < 1
    # Grow the non-image content of a deliberately short custom window.
    handle=bounds(page,'[data-reference-resize="se"]')
    x,y=handle['x']+handle['width']/2,handle['y']+handle['height']/2
    page.mouse.move(x,y);page.mouse.down();page.mouse.move(x,y-280,steps=10);page.mouse.up();settle(page)
    short=bounds(page,'#reference-pane')
    assert short['height'] < default['height']-100
    image_url='data:image/png;base64,'+base64.b64encode((ROOT/'examples/room_demo/reference.png').read_bytes()).decode()
    store.set_reference_clip(session['session_id'], {'name':'reference-window-video.mp4','fps':2,
        'frames':[{'name':'frame0.png','data_url':image_url,'time_sec':0},
                  {'name':'frame1.png','data_url':image_url,'time_sec':.5}]})
    page.evaluate('__appearanceCheck.refreshWorkspace()')
    expect(page.locator('#reference-pane #timeline-panel')).to_be_visible(timeout=10000)
    settle(page)
    assert bounds(page,'#reference-stage')['height'] >= 89
    assert_inside(page,'#timeline-play')
    assert_inside(page,'#timeline-seek')
    control(page,'#reference-window-reset').click();settle(page)
    control(page,'#comparison-layout-button').click();settle(page)
    print('PASS floating reference drag/resize and keyboard controls, dock following, Escape cancel, saved reopening/reload, viewport clamp, split restoration and unchanged evidence/camera',flush=True)

def verify_live_scene_annotation(browser, server, store):
    """Start real drawing gestures on the renderer, without an explicit capture."""
    image = 'data:image/png;base64,' + base64.b64encode((ROOT/'examples/room_demo/reference.png').read_bytes()).decode()
    for dynamic, layout, width in [(False,'compare',1440), (True,'immersive',390)]:
        session = store.create_session(reference_images=[str(ROOT/'examples/room_demo/reference.png')])
        workspace = server.workspace_gateway.ensure(session['session_id'])
        session = store.get_session(workspace['session_id'])
        if dynamic:
            store.set_reference_clip(session['session_id'], {'name':'Live drawing video','fps':2,
                'frames':[{'name':f'frame-{i}.png','time_sec':i*.5,'data_url':image} for i in range(2)]})
        store.workspace_agent(status='idle')
        context = browser.new_context(viewport={'width':width,'height':900 if width>640 else 844})
        page = context.new_page(); errors=[]; page.on('pageerror',lambda error:errors.append(str(error)))
        page.route('**/app.js',lambda route:route.fulfill(status=200,content_type='text/javascript',
            body=(ROOT/'web/app.js').read_text()+HOOK))
        page.goto(server.browser_url(session['session_id']));wait_ready(page);settle(page)
        control(page,'#chat-collapse').click()
        if dynamic:
            control(page,'#timeline-next').click();page.wait_for_function('__appearanceCheck.state.time===.5 && !__appearanceCheck.state.seeking')
        if layout=='immersive':
            control(page,'#immersive-toggle').click();settle(page)
            if page.locator('#immersive-reference-toggle').get_attribute('aria-expanded')=='true':
                control(page,'#immersive-reference-toggle').click();settle(page)
        expect(page.locator('#scene-annotation-toggle')).to_have_count(0)
        open_annotation_tools(page, 'scene')
        for tool in ['point','rectangle','line','arrow','text','freehand','erase','select']:
            choose_tool(page,tool,'scene')
            assert page.evaluate('__appearanceCheck.state.sceneView')=='live'
            expect(page.locator('.snapshot-card:not([data-kind=live])')).to_have_count(0)
        first=None;previous=None
        for index,tool in enumerate(['point','rectangle','line','arrow','text','freehand']):
            if index: control(page,'#scene-live-card').click()
            choose_tool(page,tool,'scene')
            before=page.evaluate('__appearanceCheck.state.annotations.length')
            r=bounds(page,'#viewport canvas');x=r['x']+r['width']*.28;y=r['y']+r['height']*.51
            page.mouse.move(x,y);page.mouse.down()
            page.wait_for_function("__appearanceCheck.state.sceneView==='snapshot'")
            expect(page.locator('.snapshot-card:not([data-kind=live])')).to_have_count(index+1)
            if tool!='text':
                assert page.evaluate('document.querySelector("#scene-annotations").hasPointerCapture(1)')
            if tool not in ['point','text']:
                page.mouse.move(r['x']+r['width']*.63,r['y']+r['height']*.66,steps=12)
            page.mouse.up()
            if tool=='text':
                page.locator('#annotation-text').fill('自动保存此视角');control(page,'#save-text').click()
            page.wait_for_function('(n)=>__appearanceCheck.state.annotations.length===n+1',arg=before)
            mark=page.evaluate('__appearanceCheck.state.annotations.at(-1)')
            assert mark['type']==tool and abs(mark['coordinates']['x']-.28)<.01 and abs(mark['coordinates']['y']-.51)<.01,mark
            assert page.evaluate('__appearanceCheck.state.drag') is None
            current=page.evaluate('__appearanceCheck.state.snapshot')
            assert mark['snapshot_id']==current['id']
            if dynamic:assert mark['time_sec']==current['time_sec']==.5 and current['reference_time_sec']==.5
            if previous:
                assert current['id']!=previous['id']
                saved=page.evaluate('(id)=>[...__appearanceCheck.state.sceneSnapshots,...__appearanceCheck.state.dynamicSnapshots].find(s=>s.id===id)',previous['id'])
                assert saved==previous,'Starting another live drawing changed saved evidence'
            if first is None:
                first=current;choose_tool(page,'point','scene');point(page,'#scene-annotations',.45,.55)
                expect(page.locator('.snapshot-card:not([data-kind=live])')).to_have_count(1)
                assert page.evaluate('__appearanceCheck.state.snapshot.id')==first['id']
            previous=current
        # Returning to selection restores actual camera dragging without capturing.
        control(page,'#scene-live-card').click();choose_tool(page,'select','scene')
        pose=page.evaluate('__appearanceCheck.pose()');r=bounds(page,'#viewport canvas')
        page.mouse.move(r['x']+r['width']*.25,r['y']+r['height']*.55);page.mouse.down()
        page.mouse.move(r['x']+r['width']*.42,r['y']+r['height']*.6,steps=10);page.mouse.up();page.wait_for_timeout(250)
        assert page.evaluate('__appearanceCheck.pose()')!=pose
        expect(page.locator('.snapshot-card:not([data-kind=live])')).to_have_count(6)
        choose_tool(page,'arrow','scene');r=bounds(page,'#viewport canvas')
        page.mouse.move(r['x']+r['width']*.3,r['y']+r['height']*.52);page.mouse.down()
        # Capture must survive crossing the original renderer's bounds.
        page.mouse.move(r['x']-25,r['y']+r['height']*.62,steps=15);page.mouse.up()
        page.wait_for_function('__appearanceCheck.state.annotations.at(-1).type==="arrow" && !__appearanceCheck.state.drag')
        expect(page.locator('.snapshot-card:not([data-kind=live])')).to_have_count(7)
        assert page.evaluate('__appearanceCheck.state.annotations.at(-1).coordinates.x2')==0
        saved=page.evaluate('(id)=>[...__appearanceCheck.state.sceneSnapshots,...__appearanceCheck.state.dynamicSnapshots].find(s=>s.id===id)',first['id'])
        assert saved==first
        control(page,'#scene-live-card').click();choose_tool(page,'erase','scene')
        r=bounds(page,'#viewport canvas');page.mouse.click(r['x']+r['width']*.3,r['y']+r['height']*.55)
        expect(page.locator('.snapshot-card:not([data-kind=live])')).to_have_count(7)
        assert page.evaluate('__appearanceCheck.state.sceneView')=='live'
        assert not errors,errors
        print(f'PASS live {"video" if dynamic else "static"} {layout}/{width}: all tools, automatic first capture, same-gesture coordinates/capture, saved evidence, selection rotation and outside release',flush=True)
        context.close()


def verify_reference_entry(browser, server, store):
    """The image itself exposes the same drawing dock for pointer, key and touch."""
    session = store.create_session(reference_images=[str(ROOT / 'examples/room_demo/reference.png')])
    server.workspace_gateway.ensure(session['session_id'])
    store.workspace_agent(status='idle')
    for touch in (False, True):
        context = browser.new_context(viewport={'width':390 if touch else 1280, 'height':844 if touch else 900},
                                      has_touch=touch, is_mobile=touch)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/app.js', lambda route: route.fulfill(status=200,
            content_type='text/javascript', body=(ROOT / 'web/app.js').read_text() + HOOK))
        page.goto(server.browser_url(session['session_id']))
        wait_ready(page)
        if page.locator('#chat-dock').is_visible():
            control(page, '#chat-collapse').click()
        panel = page.locator('#annotation-tool-panel')
        expect(panel).to_be_hidden()
        reference = page.locator('#reference-media')
        if touch:
            box = bounds(page, '#reference-media')
            page.touchscreen.tap(box['x'] + box['width'] * .48, box['y'] + box['height'] * .45)
            expect(panel).to_be_visible()
            expect(page.locator('#annotation-context-label')).to_have_text('参考')
            choose_tool(page, 'point', 'reference')
            box = bounds(page, '#reference-annotations')
            page.touchscreen.tap(box['x'] + box['width'] * .42, box['y'] + box['height'] * .5)
            page.wait_for_function('__appearanceCheck.state.annotations.length === 1')
            assert page.evaluate('__appearanceCheck.state.annotations[0].pane') == 'reference'
            print('PASS touch reference tap opens drawing dock and creates a mark', flush=True)
        else:
            reference.hover()
            expect(panel).to_be_visible()
            expect(page.locator('#annotation-context-label')).to_have_text('参考')
            panel.hover()
            expect(panel).to_be_visible()
            page.locator('#scene-stage').hover()
            expect(panel).to_be_hidden()
            page.keyboard.press('Tab')
            page.locator('#reference-annotations').focus()
            expect(panel).to_be_visible()
            point_tool = page.locator('button[data-tool="point"]')
            point_tool.focus(); point_tool.press('Enter')
            assert page.evaluate('__appearanceCheck.state.paneModes.reference') == 'point'
            point(page, '#reference-annotations', .48, .44)
            page.wait_for_function('__appearanceCheck.state.annotations.length === 1')
            choose_tool(page, 'rectangle', 'reference')
            box = bounds(page, '#reference-annotations')
            page.mouse.move(box['x'] + box['width']*.24, box['y'] + box['height']*.31)
            page.mouse.down()
            page.mouse.move(box['x'] + box['width']*.68, box['y'] + box['height']*.66, steps=10)
            page.mouse.up()
            page.wait_for_function('__appearanceCheck.state.annotations.length === 2')
            choose_tool(page, 'text', 'reference')
            point(page, '#reference-annotations', .38, .54)
            expect(page.locator('#text-editor')).to_be_visible()
            page.locator('#annotation-text').fill('查看这一处')
            control(page, '#save-text').click()
            page.wait_for_function('__appearanceCheck.state.annotations.length === 3')
            assert [mark['type'] for mark in page.evaluate('__appearanceCheck.state.annotations')] == ['point','rectangle','text']
            print('PASS pointer hover-to-dock/leave, keyboard focus and real image point/drag/text gestures', flush=True)
        assert not errors, errors
        context.close()

def main():
    with tempfile.TemporaryDirectory(prefix='workspace-chrome-', dir=ROOT.parent / 'tmp') as directory:
        tmp = Path(directory)
        store = SceneStore(tmp / 'data')
        session = store.create_session(reference_images=[str(ROOT / 'examples/room_demo/reference.png')])
        store.set_scene_preview(str(build(ROOT / 'examples/room_demo/scene.json', tmp / 'room.glb')))
        server = make_server(port=0, data_dir=tmp / 'data', project_dir=ROOT,
                             web_dir=ROOT / 'web', enable_codex=False)
        store = server.scene_store
        server.workspace_gateway.ensure(session['session_id'])
        store.workspace_agent(status='idle')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, args=[
                    '--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader',
                    '--enable-unsafe-swiftshader', '--disable-accelerated-2d-canvas'])
                page = browser.new_page(viewport={'width':1440,'height':900}, color_scheme='light')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                source = (ROOT / 'web/app.js').read_text() + HOOK
                page.route('**/app.js', lambda route: route.fulfill(status=200,
                    content_type='text/javascript', body=source))
                page.goto(server.browser_url(session['session_id']))
                wait_ready(page)
                if page.locator('#chat-dock').is_visible():
                    control(page, '#chat-collapse').click()
                    expect(page.locator('#chat-dock')).to_be_hidden()
                model = page.evaluate('__appearanceCheck.model()')
                pose = page.evaluate('__appearanceCheck.pose()')
                evidence = page.evaluate('__appearanceCheck.evidence()')
                expect(page.locator('#annotation-tool-panel')).to_be_hidden()
                expect(page.locator('#immersive-tools-toggle')).to_have_count(0)
                for selector in ('#task-dialog-button','#activity-dialog-button','#help-button'):
                    expect(page.locator(selector)).to_be_hidden()
                assert page.locator('.topbar button:visible, .topbar summary:visible').count() == 1
                assert page.locator('#references-dialog-button').evaluate("el => !!el.closest('.scene-pane > .pane-head')")
                control(page, '#help-button').click()
                expect(page.locator('#help-dialog')).to_be_visible()
                page.keyboard.press('Escape')
                expect(page.locator('#help-dialog')).to_be_hidden()
                expect(page.locator('#projects-dialog-button')).to_be_focused()
                control(page, '#activity-dialog-button').click()
                expect(page.locator('#activity-dialog')).to_be_visible()
                page.keyboard.press('Escape')
                expect(page.locator('#activity-dialog')).to_be_hidden()
                expect(page.locator('#projects-dialog-button')).to_be_focused()
                print('PASS single global entry, contextual annotations at rest and accessible grouped activity/help', flush=True)

                # Inspect actual open menus in both themes/layouts, including narrow screens.
                for width,height in ((1440,900),(390,844)):
                    page.set_viewport_size({'width':width,'height':height})
                    for layout in ('compare','immersive'):
                        control(page, '#comparison-layout-button' if layout == 'compare' else '#immersive-toggle').click()
                        settle(page)
                        if layout == 'immersive':
                            assert_full_scene(page)
                        for theme in ('light','dark'):
                            if page.locator('html').get_attribute('data-theme') != theme:
                                control(page, '#theme-toggle').click(); settle(page)
                            unobscured(page, '#projects-dialog-button')
                            control(page, '#theme-toggle')
                            for selector in ('#comparison-layout-button','#immersive-toggle','#theme-toggle','#help-button'):
                                unobscured(page, selector)
                            page.keyboard.press('Escape')
                            unobscured(page, '#compare-opacity')
                            unobscured(page, '#align-reference-button')
                            unobscured(page, '#ground-axis')
                            unobscured(page, '[data-selection-level="part"]')
                            page.keyboard.press('Escape')
                        scroll = page.evaluate('''() => ({
                          actual:document.documentElement.scrollWidth,expected:innerWidth,
                          offenders:[...document.querySelectorAll('body *')].filter(el => {
                            const r=el.getBoundingClientRect(),s=getComputedStyle(el);
                            return r.width>0 && r.right>innerWidth+1 && s.position!=='fixed' && s.visibility!=='hidden';
                          }).slice(0,12).map(el => [el.id || el.className, Math.round(el.getBoundingClientRect().right)])
                        })''')
                        assert scroll['actual'] <= scroll['expected'] + 1, (width,height,layout,theme,scroll)
                assert page.evaluate('__appearanceCheck.model()') == model
                actual_pose = page.evaluate('__appearanceCheck.pose()')
                assert max(abs(a-b) for key in pose for a,b in zip(pose[key],actual_pose[key])) < 1e-8, (pose,actual_pose)
                assert page.evaluate('__appearanceCheck.evidence()') == evidence
                print('PASS desktop/mobile day/night direct comparison, alignment and ground-axis controls stay reachable without changing scene or evidence', flush=True)

                page.set_viewport_size({'width':1440,'height':900})
                control(page, '#comparison-layout-button').click(); settle(page)
                original_zoom = page.evaluate('__appearanceCheck.state.referenceZoom')
                control(page, '#reference-zoom-in').click()
                assert page.evaluate('__appearanceCheck.state.referenceZoom') > original_zoom
                control(page, '#reference-zoom-out').click()
                assert abs(page.evaluate('__appearanceCheck.state.referenceZoom') - original_zoom) < 1e-8
                control(page, '#reference-zoom-in').click()
                control(page, '#reference-zoom-reset').click()
                assert page.evaluate('__appearanceCheck.state.referenceZoom') == 1
                control(page, '#drag-reference-image').click()
                expect(page.locator('#prompt-image-refs .prompt-image-chip')).to_have_count(1)
                assert '【图' in page.locator('#feedback-note').input_value()
                assert '[[' not in page.locator('#feedback-note').input_value()
                assert '[[image:' in page.evaluate('__appearanceCheck.promptText()')
                control(page, '.prompt-image-remove').click()
                expect(page.locator('#prompt-image-refs .prompt-image-chip')).to_have_count(0)
                control(page, '#chat-collapse').click()
                expect(page.locator('#chat-dock')).to_be_hidden()
                print('PASS reference corner zoom/reset and image-reference button receive real pointer clicks', flush=True)
                open_annotation_tools(page, 'reference')
                expect(page.locator('#annotation-tool-panel')).to_be_visible()
                expect(page.locator('#annotation-context-label')).to_have_text('参考')
                # The floating dock stays available when the pointer moves from
                # the reference image to its controls, then retreats on leave.
                page.locator('#annotation-tool-panel').hover()
                expect(page.locator('#annotation-tool-panel')).to_be_visible()
                choose_tool(page, 'point')
                point(page, '#reference-annotations', .52, .45)
                page.wait_for_function('__appearanceCheck.state.annotations.length === 1')
                control(page, '#capture-scene-button').click()
                expect(page.locator('#annotation-context-label')).to_have_text('截图')
                expect(page.locator('#annotation-tool-panel')).to_have_attribute('data-context','scene')
                choose_tool(page, 'point')
                point(page, '#scene-annotations', .63, .55)
                page.wait_for_function('__appearanceCheck.state.annotations.length === 2')
                frozen = page.evaluate('__appearanceCheck.evidence()')
                control(page, '#annotation-tools-close').click()
                expect(page.locator('#annotation-tool-panel')).to_be_hidden()
                expect(page.locator('#scene-snapshot-media')).to_be_visible()
                assert page.evaluate('__appearanceCheck.evidence()') == frozen
                page.evaluate('__appearanceCheck.refreshWorkspace()')
                expect(page.locator('#annotation-tool-panel')).to_be_hidden()
                open_annotation_tools(page, 'scene')
                expect(page.locator('#annotation-tool-panel')).to_be_visible()
                open_annotation_tools(page, 'reference')
                expect(page.locator('#annotation-context-label')).to_have_text('参考')
                choose_tool(page, 'point', 'scene')
                point(page, '#scene-annotations', .72, .42)
                page.wait_for_function('__appearanceCheck.state.annotations.length === 3')
                expect(page.locator('#annotation-context-label')).to_have_text('截图')
                control(page, '#more-tools > summary').click()
                unobscured(page, 'button[data-tool="arrow"]')
                unobscured(page, 'button[data-tool="text"]')
                page.keyboard.press('Escape')
                expect(page.locator('#annotation-tool-panel')).to_be_visible()
                control(page, '#scene-live-card').click()
                expect(page.locator('#annotation-tool-panel')).to_be_hidden()
                expect(page.locator('#scene-annotation-toggle')).to_have_count(0)
                unobscured(page, '#viewport canvas')
                assert page.evaluate('__appearanceCheck.model()') == model
                print('PASS reference/scene context follows real marks; closing dock preserves screenshot, polling respects collapse and live browse restores clear canvas', flush=True)

                verify_reference_window(page, store, session)
                aid = uuid.uuid4().hex
                with store.lock:
                    store.state['workspace']['approvals'] = [{
                        'approval_id':aid, 'request_id':'chrome-approval',
                        'kind':'item/commandExecution/requestApproval',
                        'details':{'reason':'检查紧凑入口提醒','command':'python build_scene.py'},
                        'prompt':'isolated browser fixture'}]
                    store._save()
                store.workspace_agent(status='awaiting_approval')
                page.evaluate('__appearanceCheck.refreshWorkspace()')
                expect(page.locator('#activity-dialog')).to_be_hidden()
                expect(page.locator('#chat-launcher')).to_contain_text('待确认')
                expect(page.locator('.chat-launcher-attention')).to_be_visible()
                control(page, '#chat-launcher').click()
                expect(page.locator(f'[data-approval-id="{aid}"]')).to_be_focused()
                expect(page.locator('#chat-approvals')).to_be_visible()
                assert not errors, errors
                print('PASS approval attention remains on collapsed chat and opens the actual pending action with no browser errors', flush=True)
                verify_live_scene_annotation(browser, server, store)
                verify_reference_entry(browser, server, store)
                browser.close()
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=3)


if __name__ == '__main__':
    main()

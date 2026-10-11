#!/usr/bin/env python3
"""Exercise real render clipping, picking and saved evidence without a model task."""
from __future__ import annotations

import argparse
import base64
import copy
from io import BytesIO
from pathlib import Path
import sys
import tempfile
import threading

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'scripts'), str(ROOT / 'tests')]
from server import make_server
from test_prompt_drag_browser import image_data
from workspace_ui_helpers import control


def ready(page):
    page.wait_for_function('window.__depth && __depth.state.workspaceReady && '
        '!__depth.state.sceneLoading && !__depth.state.seeking && '
        'document.getElementById("reference-image").naturalWidth>0')


def configure_materials(page):
    page.evaluate("""()=>{const m=__depth,colors={near_box:'#2266dd',far_box:'#dd3322',edge_box:'#22cc44'};
        for(const child of m.threeScene.children) child.visible=child===m.objectLayer;
        for(const [name,node] of m.state.objectNodes) node.traverse(mesh=>{
            if(mesh.isMesh) mesh.material=new m.THREE.MeshBasicMaterial({color:colors[name],toneMapped:false});
        });m.controls.cancelTransition();m.renderLiveScene();}""")


def rendering(page):
    raw = page.evaluate('()=>{__depth.renderLiveScene();return __depth.renderer.domElement.toDataURL("image/png");}')
    picture = Image.open(BytesIO(base64.b64decode(raw.split(',', 1)[1]))).convert('RGB')
    counts = {'blue': 0, 'red': 0, 'green': 0}
    for r, g, b in getattr(picture, 'get_flattened_data', picture.getdata)():
        counts['blue'] += b > 130 and b-r > 70 and b-g > 50
        counts['red'] += r > 130 and r-g > 70 and r-b > 70
        counts['green'] += g > 130 and g-r > 70 and g-b > 70
    return counts


def projected(page, name):
    return page.evaluate("""name=>{const m=__depth,node=m.state.objectNodes.get(name),world=new m.THREE.Vector3();
        node.getWorldPosition(world);m.camera.updateMatrixWorld();const depth=-world.clone().applyMatrix4(m.camera.matrixWorldInverse).z;
        const distance=world.distanceTo(m.camera.position),p=world.project(m.camera),r=m.renderer.domElement.getBoundingClientRect();
        const event={clientX:r.x+(p.x+1)*r.width/2,clientY:r.y+(1-p.y)*r.height/2};
        return {depth,distance,...event,selected:m.pickScene(event)?.objectId || null};}""", name)


def set_depth(page, value):
    field = control(page, '#render-depth-input')
    field.fill(str(value));field.press('Tab')


def clipping(page):
    return page.evaluate('({near:__depth.camera.near,far:__depth.camera.far})')


def assert_far(page, value):
    assert abs(clipping(page)['far']-value) < max(1e-9, value*1e-9), clipping(page)


def assert_principal(page, expected):
    actual = page.evaluate('__depth.camera.projectionMatrix.elements.slice(8,10)')
    assert all(abs(a-b) < 1e-12 for a, b in zip(actual, expected)), (actual, expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser-executable', default='/tmp/dynamic-browser-cache/chromium-1243/chrome-linux64/chrome')
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory(prefix='render-depth-browser-') as folder:
        root = Path(folder);project = root / 'project';project.mkdir()
        server = make_server(port=0, data_dir=root / 'data', project_dir=project, web_dir=ROOT / 'web',
            external_review=True, feedback_transport='mcp_events')
        worker = threading.Thread(target=server.serve_forever, daemon=True);worker.start()
        store = server.scene_store;session = server.workspace_gateway.ensure()['session_id']
        objects = [
            {'id': 'near_box', 'name': '近处', 'type': 'box', 'position': [0, 0, 1], 'size': [.3, .3, .3]},
            {'id': 'far_box', 'name': '远处', 'type': 'box', 'position': [-.8, 0, -3], 'size': [.3, .3, .3]},
            # Camera-axis depth is 4 while Euclidean/ray distance exceeds 4.2.
            {'id': 'edge_box', 'name': '画面边缘', 'type': 'box', 'position': [1.8, .65, -1], 'size': [.3, .3, .3]},
        ]
        store.replace_scene(1, objects)
        camera = {'camera_to_world': [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 3], [0, 0, 0, 1]],
            'intrinsics': {'width': 320, 'height': 240, 'fx': 300, 'fy': 300, 'cx': 120, 'cy': 95}}
        store.set_reference_clip(session, {'name': 'Depth fixture', 'fps': 2, 'frames': [
            {'name': 'frame0.png', 'data_url': image_data('navy'), 'time_sec': 0, 'camera': camera},
            {'name': 'frame1.png', 'data_url': image_data('gray'), 'time_sec': .5, 'camera': camera}]})
        original_scene = copy.deepcopy(store.scene());errors = [];feedback_posts = []
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, executable_path=args.browser_executable,
                    args=['--no-sandbox', '--no-proxy-server', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
                context = browser.new_context(viewport={'width': 1440, 'height': 1000})
                hook = '\nwindow.__depth={state,THREE,camera,renderer,threeScene,objectLayer,controls,frameAll,frameBox,resizeScene,applyCameraDepth,alignActiveReference,leaveReferenceCamera,ensureDynamicMoment,cameraData,pickScene,renderSettings,workspaceSidebar,renderLiveScene,loadScene};'
                context.route('**/app.js', lambda route: route.fulfill(status=200, content_type='application/javascript',
                    body=(ROOT / 'web/app.js').read_text()+hook))
                page = context.new_page();page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda call: feedback_posts.append(call.url)
                    if call.method == 'POST' and call.url.endswith('/feedback') else None)
                page.goto(server.browser_url(session));ready(page);configure_materials(page)

                # The numeric field controls actual GPU clipping. Picking uses
                # camera-space depth so the edge box remains selectable even
                # when its ray length is larger than the far clipping plane.
                set_depth(page, 10);assert_far(page, 10)
                counts = rendering(page);assert all(value > 30 for value in counts.values()), counts
                set_depth(page, 2.6);assert_far(page, 2.6)
                counts = rendering(page)
                assert counts['blue'] > 30 and counts['red'] == counts['green'] == 0, counts
                assert projected(page, 'near_box')['selected'] == 'near_box'
                assert projected(page, 'far_box')['selected'] is None
                assert projected(page, 'edge_box')['selected'] is None
                set_depth(page, 4.2);assert_far(page, 4.2)
                counts = rendering(page);assert counts['green'] > 30 and counts['red'] == 0, counts
                edge = projected(page, 'edge_box')
                assert edge['depth'] < 4.2 < edge['distance'] and edge['selected'] == 'edge_box', edge
                print('PASS: real numeric input clips rendered distant pixels and picking; an oblique visible edge object remains selectable by camera-axis depth', flush=True)

                # Resizing/re-aligning must preserve both the user's far plane
                # and the reference camera's noncentral principal point. Large
                # focus bounds force auto-near beyond a small manual far limit.
                principal = page.evaluate('__depth.camera.projectionMatrix.elements.slice(8,10)')
                assert max(map(abs, principal)) > .1, principal
                page.set_viewport_size({'width': 1100, 'height': 820})
                page.evaluate('__depth.resizeScene();__depth.alignActiveReference()')
                assert_far(page, 4.2)
                assert_principal(page, principal)
                set_depth(page, .1)
                page.evaluate('()=>{const m=__depth;m.frameBox(new m.THREE.Box3(new m.THREE.Vector3(-1000,-1000,-1000),new m.THREE.Vector3(1000,1000,1000)));m.resizeScene();}')
                depth = clipping(page);assert 0 < depth['near'] < depth['far'] == .1, depth
                page.evaluate('()=>__depth.loadScene({objects:__depth.state.sceneObjects,revision:__depth.state.sceneRevision+1})')
                assert_far(page, .1)
                page.evaluate('__depth.alignActiveReference();__depth.resizeScene()')
                assert_far(page, .1)
                assert_principal(page, principal)
                print('PASS: off-center calibrated alignment, resize, large-scene focus and scene reload retain manual clipping; very small far limits keep a valid near/far pair', flush=True)

                # Persistence follows real UI events; invalid edits may show a
                # validation error but cannot replace the last usable value.
                set_depth(page, 4.2)
                for invalid in (-1, 0, 100001):
                    set_depth(page, invalid);assert_far(page, 4.2)
                    assert page.evaluate('localStorage.getItem("astra-render-depth-limit")') == '4.2'
                # Incomplete exponent text is represented as an empty value
                # plus badInput by Chromium. It must not become an auto reset.
                field = control(page, '#render-depth-input');field.fill('');field.type('e')
                assert field.evaluate('el=>el.value==="" && el.validity.badInput')
                field.press('Tab');assert_far(page, 4.2)
                assert page.evaluate('localStorage.getItem("astra-render-depth-limit")') == '4.2'
                page.reload();ready(page);assert_far(page, 4.2)
                assert control(page, '#render-depth-input').input_value() == '4.2'
                control(page, '#render-depth-range').evaluate('(el)=>{el.value="300";el.dispatchEvent(new Event("input",{bubbles:true}));}')
                assert_far(page, 100)
                control(page, '#render-depth-auto').click();assert_far(page, 2000)
                assert page.evaluate('localStorage.getItem("astra-render-depth-limit")') is None
                set_depth(page, 2.6);set_depth(page, '')
                assert_far(page, 2000)
                page.reload();ready(page);assert_far(page, 2000)
                assert control(page, '#render-depth-input').input_value() == '2000'
                assert page.locator('#render-depth-auto').get_attribute('aria-pressed') == 'true'
                print('PASS: valid limits survive reload, invalid/out-of-range edits preserve the previous limit, logarithmic slider applies live, and reset/empty input restore automatic depth', flush=True)

                # Same-time evidence with distinct depth limits is distinct;
                # changing live settings cannot repaint any older frozen image.
                configure_materials(page)
                set_depth(page, 2.6)
                first = page.evaluate('()=>structuredClone(__depth.ensureDynamicMoment())')
                assert first and first['camera']['far'] == 2.6 and first['time_sec'] == 0
                set_depth(page, 4.2)
                second = page.evaluate('()=>structuredClone(__depth.ensureDynamicMoment())')
                assert second and second['id'] != first['id'] and second['camera']['far'] == 4.2
                assert second['time_sec'] == first['time_sec'] and second['data_url'] != first['data_url']
                old = page.evaluate('id=>structuredClone(__depth.state.dynamicSnapshots.find(moment=>moment.id===id))', first['id'])
                assert old == first
                reused = page.evaluate('()=>__depth.ensureDynamicMoment().id')
                assert reused == second['id'] and page.evaluate('__depth.state.dynamicSnapshots.length') == 2
                page.evaluate('id=>{const m=__depth;m.state.snapshot=m.state.dynamicSnapshots.find(moment=>moment.id===id);m.state.sceneView="snapshot";}', first['id'])
                set_depth(page, 8)
                assert page.evaluate('id=>structuredClone(__depth.state.dynamicSnapshots.find(moment=>moment.id===id))', first['id']) == first
                assert store.scene() == original_scene and feedback_posts == [] and not errors, (errors, feedback_posts)
                print('PASS: same-time different-depth captures are separate moments with exact clipping metadata; old frozen pixels/camera remain unchanged and no feedback or model turn is created', flush=True)
                browser.close()
        finally:
            server.shutdown();worker.join(5);server.server_close()
    print('ALL RENDER DEPTH BROWSER CHECKS PASSED', flush=True)


if __name__ == '__main__': main()

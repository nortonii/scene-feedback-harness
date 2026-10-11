#!/usr/bin/env python3
"""Check real GPU fog, independent clipping and frozen evidence in isolation."""
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
    page.wait_for_function('window.__fogCheck && __fogCheck.state.workspaceReady && '
        '!__fogCheck.state.sceneLoading && !__fogCheck.state.seeking && '
        'document.getElementById("reference-image").naturalWidth>0')


def configure_materials(page):
    page.evaluate("""()=>{const m=__fogCheck,colors={far_box:'#2266dd',mid_box:'#dd3322'};
        for(const child of m.threeScene.children) child.visible=child===m.objectLayer;
        for(const [name,node] of m.state.objectNodes) node.traverse(mesh=>{
            if(mesh.isMesh) mesh.material=new m.THREE.MeshBasicMaterial({color:colors[name],toneMapped:false});
        });m.controls.cancelTransition();m.renderLiveScene();}""")


def image(page):
    raw = page.evaluate('()=>{__fogCheck.renderLiveScene();return __fogCheck.renderer.domElement.toDataURL("image/png");}')
    return Image.open(BytesIO(base64.b64decode(raw.split(',', 1)[1]))).convert('RGB')


def color_distance(page, name):
    picture = image(page)
    center = page.evaluate("""name=>{const m=__fogCheck,node=m.state.objectNodes.get(name),p=new m.THREE.Vector3();
        node.getWorldPosition(p);p.project(m.camera);return {x:(p.x+1)*m.renderer.domElement.width/2,
            y:(1-p.y)*m.renderer.domElement.height/2};}""", name)
    color = picture.getpixel((round(center['x']), round(center['y'])))
    background = picture.getpixel((3, 3))
    return sum(abs(a-b) for a, b in zip(color, background))


def fog(page):
    return page.evaluate('structuredClone(__fogCheck.cameraData().fog)')


def set_fog(page, strength):
    control(page, '#render-fog-strength').evaluate('(el,value)=>{el.value=String(value);el.dispatchEvent(new Event("input",{bubbles:true}));}', strength)


def set_depth(page, value):
    field = control(page, '#render-depth-input');field.fill(str(value));field.press('Tab')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser-executable', default='/tmp/dynamic-browser-cache/chromium-1243/chrome-linux64/chrome')
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory(prefix='render-fog-browser-') as folder:
        root = Path(folder);project = root / 'project';project.mkdir()
        server = make_server(port=0, data_dir=root / 'data', project_dir=project, web_dir=ROOT / 'web',
            external_review=True, feedback_transport='mcp_events')
        worker = threading.Thread(target=server.serve_forever, daemon=True);worker.start()
        store = server.scene_store;session = server.workspace_gateway.ensure()['session_id']
        store.replace_scene(1, [
            {'id': 'far_box', 'name': '远处', 'type': 'box', 'position': [0, 0, -97], 'size': [12, 12, 12]},
            {'id': 'mid_box', 'name': '中处', 'type': 'box', 'position': [8, 0, -22], 'size': [3, 3, 3]},
        ])
        camera = {'camera_to_world': [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 3], [0, 0, 0, 1]],
            'intrinsics': {'width': 320, 'height': 240, 'fx': 300, 'fy': 300, 'cx': 160, 'cy': 120}}
        store.set_reference_clip(session, {'name': 'Fog fixture', 'fps': 2, 'frames': [
            {'name': 'frame0.png', 'data_url': image_data('navy'), 'time_sec': 0, 'camera': camera},
            {'name': 'frame1.png', 'data_url': image_data('gray'), 'time_sec': .5, 'camera': camera}]})
        original_scene = copy.deepcopy(store.scene());errors = [];feedback_posts = []
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, executable_path=args.browser_executable,
                    args=['--no-sandbox', '--no-proxy-server', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
                context = browser.new_context(viewport={'width': 1440, 'height': 1000})
                hook = '\nwindow.__fogCheck={state,THREE,camera,renderer,threeScene,objectLayer,controls,ensureDynamicMoment,cameraData,renderSettings,renderLiveScene};'
                context.route('**/app.js', lambda route: route.fulfill(status=200, content_type='application/javascript',
                    body=(ROOT / 'web/app.js').read_text()+hook))
                page = context.new_page();page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda call: feedback_posts.append(call.url)
                    if call.method == 'POST' and call.url.endswith('/feedback') else None)
                page.goto(server.browser_url(session));ready(page);configure_materials(page)

                # Default depth is sufficient for the far box. Its blue color
                # must actually reach GPU output without the former 14–36 fog.
                assert fog(page) == {'enabled': False}
                assert page.evaluate('__fogCheck.threeScene.fog===null')
                plain = color_distance(page, 'far_box');assert plain > 250, plain
                set_depth(page, 200);set_fog(page, 100)
                full = fog(page);assert full == {'enabled': True, 'near': 14, 'far': 36}, full
                assert color_distance(page, 'far_box') <= 3
                assert color_distance(page, 'mid_box') > 30
                set_fog(page, 20)
                light = fog(page);assert light['enabled'] and abs(light['near']-70) < 1e-9 and abs(light['far']-180) < 1e-9, light
                reduced = color_distance(page, 'far_box');assert plain*.65 < reduced < plain, (plain, reduced)
                set_depth(page, 500)
                assert fog(page) == light and page.evaluate('__fogCheck.camera.far') == 500
                assert abs(color_distance(page, 'far_box')-reduced) <= 3
                print('PASS: default far objects render clearly; 100% fog fades them into the background, 20% visibly weakens it, and clipping distance remains independent', flush=True)

                # Browser preferences survive reload. Theme animation must
                # update the active fog/background together and tolerate null
                # fog when it is disabled.
                page.reload();ready(page);configure_materials(page)
                assert fog(page) == light and page.evaluate('__fogCheck.camera.far') == 500
                assert control(page, '#render-fog-strength').input_value() == '20'
                assert page.locator('#render-fog-value').inner_text() == '20%'
                set_fog(page, 100)
                control(page, '#theme-toggle').click()
                page.wait_for_function('document.documentElement.dataset.theme==="dark" && !document.documentElement.classList.contains("theme-transitioning")')
                page.wait_for_function('__fogCheck.threeScene.fog.color.equals(__fogCheck.threeScene.background)')
                assert color_distance(page, 'far_box') <= 3
                set_fog(page, 0)
                assert page.locator('#render-fog-value').inner_text() == '关闭'
                page.reload();ready(page);configure_materials(page)
                assert fog(page) == {'enabled': False} and page.evaluate('__fogCheck.threeScene.fog===null')
                assert control(page, '#render-fog-strength').input_value() == '0'
                control(page, '#theme-toggle').click()
                page.wait_for_function('document.documentElement.dataset.theme==="light" && !document.documentElement.classList.contains("theme-transitioning")')
                assert page.evaluate('__fogCheck.threeScene.fog===null') and color_distance(page, 'far_box') > 250
                print('PASS: strength and disabled state survive refresh; day/night animations keep enabled fog colors matched and disabled fog remains null without rendering errors', flush=True)

                # Fog metadata identifies evidence even with the same camera
                # and timestamp. Older screenshots must retain all saved bytes.
                first = page.evaluate('()=>structuredClone(__fogCheck.ensureDynamicMoment())')
                assert first and first['camera']['fog'] == {'enabled': False}
                set_fog(page, 100)
                second = page.evaluate('()=>structuredClone(__fogCheck.ensureDynamicMoment())')
                assert second and second['id'] != first['id'] and second['camera']['fog'] == full
                assert second['time_sec'] == first['time_sec'] and second['data_url'] != first['data_url']
                assert page.evaluate('id=>structuredClone(__fogCheck.state.dynamicSnapshots.find(moment=>moment.id===id))', first['id']) == first
                assert page.evaluate('__fogCheck.ensureDynamicMoment().id') == second['id']
                set_fog(page, 20)
                assert page.evaluate('id=>structuredClone(__fogCheck.state.dynamicSnapshots.find(moment=>moment.id===id))', first['id']) == first
                assert page.evaluate('id=>structuredClone(__fogCheck.state.dynamicSnapshots.find(moment=>moment.id===id))', second['id']) == second
                assert store.scene() == original_scene and feedback_posts == [] and not errors, (errors, feedback_posts)
                print('PASS: same-time fog changes create separate evidence with fog metadata, unchanged settings reuse the moment, and old pixels/cameras remain untouched without feedback or model execution', flush=True)
                browser.close()
        finally:
            server.shutdown();worker.join(5);server.server_close()
    print('ALL RENDER FOG BROWSER CHECKS PASSED', flush=True)


if __name__ == '__main__': main()

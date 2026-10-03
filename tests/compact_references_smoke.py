"""Readable references retain exact evidence through editing, reload and submission.

Uses a temporary server with Codex disabled; all insertion/removal goes through
real rendered controls. The injected hook only reads the canonical submission.
"""
from __future__ import annotations

from pathlib import Path
import json
import re
import shutil
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'examples/room_demo')]
from core import SceneStore
from server import make_server
from build_scene import build
from playwright.sync_api import sync_playwright, expect
from immersive_theme_smoke import HOOK, settle, point, assert_inside
from workspace_ui_helpers import control, choose_tool


def wait_ready(page):
    page.wait_for_function("window.__appearanceCheck?.state.workspaceReady && !__appearanceCheck.state.sceneLoading && document.querySelector('#reference-image').naturalWidth>0")
    expect(page.locator('#submit-button')).to_be_enabled()


def main():
    results = []
    def passed(message):
        results.append(message)
        print('PASS ' + message, flush=True)

    with tempfile.TemporaryDirectory(prefix='compact-references-', dir=ROOT.parent / 'tmp') as folder:
        tmp = Path(folder)
        reference = tmp / ('参考图片来源名称非常长需要保持可读性_' * 3 + '.png')
        shutil.copyfile(ROOT / 'examples/room_demo/reference.png', reference)
        seed = SceneStore(tmp / 'data')
        session = seed.create_session(reference_images=[str(reference)])
        seed.set_scene_preview(str(build(ROOT / 'examples/room_demo/scene.json', tmp / 'room.glb')))
        server = make_server(port=0, data_dir=tmp / 'data', project_dir=ROOT,
                             web_dir=ROOT / 'web', enable_codex=False)
        store = server.scene_store
        server.workspace_gateway.ensure(session['session_id'])
        store.workspace_agent(status='idle')
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, args=[
                    '--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader',
                    '--enable-unsafe-swiftshader', '--disable-accelerated-2d-canvas'])
                page = browser.new_page(viewport={'width':1440,'height':960})
                errors, submissions = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: submissions.append(request.post_data_json)
                    if request.method == 'POST' and request.url.endswith('/feedback') else None)
                page.route('**/app.js', lambda route: route.fulfill(status=200,
                    content_type='text/javascript', body=(ROOT / 'web/app.js').read_text() + HOOK + '''
__appearanceCheck.selectMentionTarget = kind => {
  const item=state.sceneObjects[0], root=state.objectNodes.get(item.id)?.userData.gltfRoot;
  if (kind === 'object') { selectObject(item.id); return; }
  const node=root?.children.find(child=>child.name);
  if (!node) throw new Error('fixture has no named scene node');
  state.selectionLevel='part';
  selectObject(item.id,nodeReference(item.id,node,'part'),node);
};
'''))
                page.goto(server.browser_url(session['session_id']))
                wait_ready(page)
                page.wait_for_function('window.__appearanceCheck?.state.workspaceReady && !__appearanceCheck.state.sceneLoading')
                settle(page)
                note = page.locator('#feedback-note')
                canonical = lambda: page.evaluate('__appearanceCheck.promptText()')
                image_refs = lambda: page.evaluate('__appearanceCheck.citedImages()')
                out = ROOT.parent / 'inspection/compact-references'
                out.mkdir(parents=True, exist_ok=True)

                def assert_short():
                    value = note.input_value()
                    assert '[[' not in value and ']]' not in value, value
                    assert not re.search(r'[a-f\d]{24,}', value), value
                    assert reference.name not in value, value

                def mention(kind):
                    if kind in ('object','node'):
                        page.evaluate('__appearanceCheck.selectMentionTarget',kind)
                    note.focus()
                    note.press('Control+End')
                    note.press('End')
                    note.press('Space')
                    note.press('@')
                    option = page.locator(f'.prompt-mention-option[data-mention-kind="{kind}"]').first
                    expect(option).to_be_visible()
                    label = option.locator('strong').inner_text()
                    option.click()
                    expect(page.locator('#prompt-mentions')).to_be_hidden()
                    assert_short()
                    return label

                control(page, '#drag-reference-image').click()
                expect(page.locator('.prompt-image-chip')).to_have_count(1)
                assert_short()
                assert '【图1】' in note.input_value(), note.input_value()
                first_image = image_refs()[0]
                assert f'[[image:{first_image["id"]}]]' in canonical()
                assert len(note.input_value()) < 18, note.input_value()
                page.locator('.prompt-image-preview').click()
                expect(page.locator('#prompt-image-preview-dialog')).to_be_visible()
                assert page.locator('#prompt-image-preview-image').get_attribute('src') == first_image['original_data_url']
                page.keyboard.press('Escape')
                page.locator('.prompt-image-remove').click()
                expect(page.locator('.prompt-image-chip')).to_have_count(0)
                assert '[[image:' not in canonical() and '【图1】' not in note.input_value()
                control(page, '#drag-reference-image').click()
                first_image = image_refs()[-1]
                passed('long image sources become short inline names; thumbnail preview/removal preserve exact source identity')

                # Adding another image with the caret inside a short reference
                # must preserve both identities instead of splitting its label.
                value = note.input_value()
                start = value.index('【')
                note.evaluate('(el, offset)=>{el.focus();el.setSelectionRange(offset,offset)}',start+2)
                control(page, '#drag-reference-image').click()
                assert len(re.findall(r'\[\[image:', canonical())) == 2
                assert len(re.findall(r'【图\d+】', note.input_value())) == 2
                expect(page.locator('.prompt-image-chip')).to_have_count(2)
                page.locator('.prompt-image-remove').last.click()
                expect(page.locator('.prompt-image-chip')).to_have_count(1)
                note.press('Control+z')
                expect(page.locator('.prompt-image-chip')).to_have_count(2)
                page.locator('.prompt-image-remove').last.click()
                first_image = image_refs()[0]
                # IME composition should start after an atomic reference.
                note.evaluate('el=>{const i=el.value.indexOf("【");el.setSelectionRange(i+2,i+2);el.dispatchEvent(new CompositionEvent("compositionstart",{bubbles:true}));}')
                assert note.evaluate('el=>el.selectionStart') == note.input_value().index('】')+1
                note.dispatch_event('compositionend', {'data':''})
                object_label = mention('object')
                assert '[[object:' in canonical()
                node_label = mention('node')
                assert '[[node:' in canonical()
                expect(page.locator('.prompt-reference-chip')).to_have_count(2)
                assert object_label and node_label
                page.locator('.prompt-reference-preview').last.click()
                expect(page.locator('#prompt-reference-dialog')).to_be_visible()
                expect(page.locator('#prompt-reference-detail')).to_contain_text(node_label)
                page.locator('#prompt-reference-close').click()
                before = canonical()
                value = note.input_value()
                last_token = list(re.finditer(r'【[^】]+】', value))[-1]
                note.evaluate('(el, range) => {el.focus();el.setSelectionRange(...range)}', [last_token.start(),last_token.end()])
                note.press('Backspace')
                assert '[[node:' not in canonical()
                expect(page.locator('.prompt-reference-chip')).to_have_count(1)
                note.press('Control+z')
                assert canonical() == before
                expect(page.locator('.prompt-reference-chip')).to_have_count(2)
                assert_short()
                # Word deletion from the whitespace after a reference should
                # remove its whole alias and native undo must restore it.
                token = list(re.finditer(r'【[^】]+】', note.input_value()))[-1]
                note.evaluate('(el, offset)=>{el.focus();el.setSelectionRange(offset,offset)}',token.end())
                note.press('Space')
                before_word = canonical()
                note.press('Control+Backspace')
                assert '[[node:' not in canonical()
                note.press('Control+z')
                assert canonical() == before_word
                assert_short()
                passed('@ object/part references remain compact; native Backspace and undo restore the canonical scene-node link')

                control(page, '#chat-collapse').click()
                expect(page.locator('#chat-dock')).to_be_hidden()
                control(page, '#capture-scene-button').click()
                choose_tool(page, 'point', 'scene')
                point(page, '#scene-annotations', .58, .4)
                mark = page.evaluate('__appearanceCheck.state.annotations.at(-1)')
                control(page, '#annotation-tools-close').click()
                control(page, '#chat-launcher').click()
                mention('annotation')
                assert f'[[annotation:{mark["id"]}]]' in canonical()
                expect(page.locator('.prompt-reference-chip')).to_have_count(3)
                assert mark['name'] in note.input_value()
                # Removing a quote must preserve the underlying editable mark.
                mark_chip = page.locator('.prompt-reference-chip').filter(has_text=mark['name']).last
                mark_chip.locator('.prompt-reference-remove').click()
                assert f'[[annotation:{mark["id"]}]]' not in canonical()
                assert page.evaluate('__appearanceCheck.state.annotations.at(-1).id') == mark['id']
                mention('annotation')
                before, display = canonical(), note.input_value()
                page.reload()
                wait_ready(page)
                page.wait_for_function('window.__appearanceCheck?.state.workspaceReady && !__appearanceCheck.state.sceneLoading')
                expect(note).to_have_value(display)
                assert canonical() == before
                expect(page.locator('.prompt-reference-chip')).to_have_count(3)
                expect(page.locator('.prompt-image-chip')).to_have_count(1)
                assert_short()
                passed('named annotation chips remove only the quote; reload restores readable draft and stable image/object/node/mark bindings')

                # Legacy drafts have canonical text but no saved short-name map.
                # Image bytes are restored from IndexedDB on reload.
                page.evaluate(r'''() => {const key='astra-visual-draft:'+__appearanceCheck.state.sessionId;
                  const draft=JSON.parse(localStorage.getItem(key));
                  const image=__appearanceCheck.citedImages()[0];
                  draft.note=draft.note.replace(/【图\d+】 \[\[image:[^\]]+\]\]/,image.label+' [[image:'+image.id+']]');
                  delete draft.promptReferenceLabels;localStorage.setItem(key,JSON.stringify(draft));}''')
                page.reload();wait_ready(page)
                assert_short()
                assert len(image_refs()) == 1
                assert image_refs()[0]['original_data_url'] == first_image['original_data_url']
                assert '[[annotation:' in canonical() and '[[node:' in canonical()
                # Replace images repeatedly so the newest active source follows
                # more than eight retained orphan sources in IndexedDB.
                for _ in range(9):
                    control(page, '#drag-reference-image').click()
                    page.locator('.prompt-image-remove').first.click()
                latest_image = image_refs()[0]
                before, display = canonical(), note.input_value()
                page.reload();wait_ready(page)
                assert canonical() == before and note.input_value() == display
                assert len(image_refs()) == 1 and image_refs()[0]['id'] == latest_image['id']
                assert image_refs()[0]['original_data_url'] == latest_image['original_data_url']
                passed('legacy drafts migrate without raw identifiers; repeated image replacements retain the latest cited bytes across reload')

                evidence_before_edit = page.evaluate('__appearanceCheck.evidence()')
                control(page, '#theme-toggle').click()
                for width, height in [(1440,960),(390,844)]:
                    page.set_viewport_size({'width':width,'height':height})
                    settle(page)
                    note.focus()
                    expect(page.locator('#annotation-tool-panel')).to_be_hidden()
                    assert_inside(page, '#feedback-note')
                    assert_inside(page, '#submit-button')
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                    for chip in page.locator('.prompt-image-chip,.prompt-reference-chip').all():
                        box = chip.bounding_box()
                        assert box and box['x'] >= 0 and box['x'] + box['width'] <= width + 1, box
                    page.screenshot(path=str(out / f'dark-{width}.png'))
                assert page.evaluate('__appearanceCheck.evidence()') == evidence_before_edit
                passed('desktop/mobile dark composer retains legible references and reachable send control without horizontal overflow')

                page.set_viewport_size({'width':1440,'height':960})
                settle(page)
                note.focus()
                note.press('Control+End')
                note.press('End')
                note.press('Space')
                note.type('请对照这些引用调整柜子。')
                before, display = canonical(), note.input_value()
                refs = image_refs()
                nodes = page.evaluate('__appearanceCheck.state.referencedSceneNodes')
                control(page, '#submit-button').click()
                page.wait_for_function('__appearanceCheck.state.feedbackCount===1 && !__appearanceCheck.state.submitting', timeout=30000)
                assert len(submissions) == 1
                sent = submissions[0]
                assert sent['note'] == before.strip() and sent['note'] != display, (sent['note'],before)
                assert set(re.findall(r'\[\[(\w+):', sent['note'])) == {'image','object','node','annotation'}
                assert sent['image_refs'][0]['id'] == refs[-1]['id']
                assert sent['image_refs'][0]['original_data_url'] == refs[-1]['original_data_url']
                fields = {'parent_object_id','node_path','node_name','stable_id','semantic_id'}
                assert sent['referenced_scene_nodes'] == [{key:value for key,value in node.items() if key in fields} for node in nodes]
                assert sent['annotations'][0]['id'] == mark['id']
                packet = store.list_all_feedback()[0]
                assert {ref['kind'] for ref in packet['inline_references']} == {'image','object','node','annotation'}
                assert packet['image_refs'][0]['id'] == refs[-1]['id']
                expect(note).to_have_value('')
                expect(page.locator('.prompt-image-chip,.prompt-reference-chip')).to_have_count(0)
                if not page.locator('#chat-history').is_visible():
                    control(page, '#chat-history-toggle').click()
                expect(page.locator('#conversation')).to_contain_text('请对照这些引用调整柜子。')
                assert '[[' not in page.locator('#conversation').inner_text()
                assert not re.search(r'[a-f\d]{24,}', page.locator('#conversation').inner_text())
                page.reload()
                wait_ready(page)
                expect(page.locator('#conversation')).to_contain_text('请对照这些引用调整柜子。')
                assert '[[' not in page.locator('#conversation').inner_text()
                assert store.list_all_feedback()[0] == packet
                passed('real feedback preserves canonical tokens and all four evidence types; replayed chat hides wire identifiers and successful send clears the draft')
                assert not errors, errors
                print(json.dumps({'passed':len(results),'screenshots':str(out)},ensure_ascii=False),flush=True)
                browser.close()
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    main()

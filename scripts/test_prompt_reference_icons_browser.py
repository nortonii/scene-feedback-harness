#!/usr/bin/env python3
"""Check native icon-reference editing and legacy drafts in an isolated browser.

The temporary workbench uses synthetic reference pixels and a tiny local GLB.
No production service, feedback submission or model is involved.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "scripts"), str(ROOT / "tests")]
from test_prompt_drag_browser import image_data, tiny_named_glb
from workspace_ui_helpers import control


def ready(page):
    page.wait_for_function("window.__iconCheck && __iconCheck.state.workspaceReady && !__iconCheck.state.sceneLoading && document.getElementById('reference-image').naturalWidth > 0")


def canonical(page):
    return page.evaluate("__iconCheck.promptText()")


def prepare_edit(page, text, needle, start, end=None, occurrence=0):
    """Set only the native selection; the subsequent edit is a real key press.

    Offsets are intentionally calculated in JavaScript UTF-16 units, matching
    textarea selectionStart/End for emoji and variation selectors.
    """
    field = page.locator("#feedback-note")
    field.fill(text)
    field.focus()
    field.evaluate("""(field,args)=>{
      let at=-1;for(let n=0;n<=args.occurrence;n++)at=field.value.indexOf(args.needle,at+1);
      if(at<0)throw new Error('selection target is absent');
      field.setSelectionRange(at+args.start,at+args.end);
      __iconCheck.events=[];
    }""", {"needle": needle, "start": start, "end": start if end is None else end, "occurrence": occurrence})
    return field


def assert_native_edit(page, input_type):
    events = page.evaluate("__iconCheck.events")
    assert any(event["type"] == "beforeinput" and event["isTrusted"] and
               event["inputType"] == input_type for event in events), events


def source_state(page):
    return page.evaluate("JSON.stringify({nodes:__iconCheck.state.referencedSceneNodes,images:__iconCheck.state.imageRefs,camera:__iconCheck.cameraData(),time:__iconCheck.state.time,view:__iconCheck.state.activeViewId,marks:__iconCheck.state.annotations})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-executable", help="Existing Chromium binary")
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright
    from server import make_server

    errors, feedback_requests = [], []
    exact_time = "片段0.100000001s · 参考0.100000001s · 正面 #1"
    with tempfile.TemporaryDirectory(prefix="prompt-reference-icons-") as temporary:
        root = Path(temporary)
        project = root / "project"
        project.mkdir()
        server = make_server(port=0, data_dir=root / "data", project_dir=project,
                             web_dir=ROOT / "web", external_review=True,
                             feedback_transport="mcp_events")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        store = server.scene_store
        session = server.workspace_gateway.ensure()["session_id"]
        model = root / "cabinet.glb"
        tiny_named_glb(model)
        imported = store.import_model(str(model), object_id="fixture_model", name="模型柜子")
        store.update_scene(imported["scene_revision"], [{"op": "add", "object": {
            "id": f"fixture_box{number}", "name": f"独立箱子{number}", "type": "box",
            "position": [number * .2, 0, 0], "size": [.1, .1, .1], "color": "#bb8844"}}
            for number in range(1, 11)])
        camera = {"camera_to_world": [[1,0,0,0],[0,1,0,0],[0,0,1,3],[0,0,0,1]],
                  "intrinsics": {"width":320,"height":240,"fx":300,"fy":300,"cx":160,"cy":120}}
        clip = store.set_reference_clip(session, {"name": "正面", "fps": 2, "frames": [
            {"name": "source.png", "data_url": image_data("navy"), "time_sec": .100000001, "camera": camera},
            {"name": "later.png", "data_url": image_data("maroon"), "time_sec": .5, "camera": camera}]})["reference_clip"]
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True,
                    **({"executable_path": args.browser_executable} if args.browser_executable else {}),
                    args=["--no-sandbox", "--no-proxy-server", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
                context = browser.new_context(viewport={"width":1440,"height":950})
                hook = """
window.__iconCheck={state,ui,promptText,promptReferenceText,promptImageStore,cameraData,
  nodeReference,insertSceneNodeReference,capturePromptImage,addPromptImageReference,saveDraft,events:[]};
for(const type of ['beforeinput','input','compositionstart','compositionend']) ui.note.addEventListener(type,event=>{
  __iconCheck.events.push({type,isTrusted:event.isTrusted,inputType:event.inputType || '',
    start:ui.note.selectionStart,end:ui.note.selectionEnd,value:ui.note.value});
});
"""
                context.route("**/app.js", lambda route: route.fulfill(status=200,
                    content_type="application/javascript", body=(ROOT / "web/app.js").read_text() + hook))
                # Catch any accidental submit before it can reach even this
                # temporary service; workspace bootstrap remains functional.
                def block_feedback(route):
                    feedback_requests.append(route.request.url)
                    route.abort()
                context.route("**/api/sessions/*/feedback", block_feedback)
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.server_port}/")
                ready(page)
                if not page.locator("#chat-dock").is_visible():
                    control(page, "#chat-launcher").click()
                field = page.locator("#feedback-note")

                # Create real source-bound references, then restore their saved
                # records as a draft written by the former bracketed codec.
                seeded = page.evaluate("""async exact=>{
                  const m=__iconCheck;
                  for(let n=1;n<=10;n++)m.promptReferenceText.remember('独立箱子'+n,'[[object:fixture_box'+n+']]');
                  const root=m.state.objectNodes.get('fixture_model').userData.gltfRoot;
                  const node=m.nodeReference('fixture_model',root.children[0].children[0],'part');
                  if(!m.insertSceneNodeReference(node,'Door'))throw new Error('could not register source node');
                  const image=m.capturePromptImage('reference');
                  if(!m.addPromptImageReference(image))throw new Error('could not register source image');
                  const time=m.promptReferenceText.rememberTime(exact,JSON.stringify({session:m.state.sessionId,
                    clip:image.clip_id,view:image.view_id,reference:image.reference_id,time:.100000001}));
                  m.ui.note.value='🧊1 🧊10 🧩1 🖼️1 '+time.alias;m.saveDraft();
                  await m.promptImageStore.pending;
                  const key='astra-visual-draft:'+m.state.sessionId,draft=JSON.parse(localStorage.getItem(key));
                  const names={object:'物体',node:'部件',image:'图',time:'时间戳'};
                  for(const record of draft.promptReferenceLabels){
                    const number=/([1-9]\\d*)$/.exec(record.alias)[1];
                    record.alias='【'+names[record.kind]+number+'】';
                  }
                  draft.note=draft.promptReferenceLabels.filter(r=>
                    r.kind!=='object'||['[[object:fixture_box1]]','[[object:fixture_box10]]'].includes(r.token))
                    .map(r=>r.alias+' '+(r.kind==='time'?r.timeText:r.token)).join(' ');
                  localStorage.setItem(key,JSON.stringify(draft));
                  return {node,image,time:time.token,timeKey:time.timeKey};
                }""", exact_time)
                page.reload()
                ready(page)
                page.wait_for_function("__iconCheck.state.imageRefs.length===1")
                expected = "🧊1 🧊10 🧩1 🖼️1 🕒1"
                assert field.input_value() == expected, field.input_value()
                restored = canonical(page)
                assert "[[object:fixture_box1]]" in restored and "[[object:fixture_box10]]" in restored
                node_token = f"[[node:fixture_model:{'/'.join(map(str, seeded['node']['node_path']))}]]"
                assert node_token in restored and f"[[image:{seeded['image']['id']}]]" in restored
                assert exact_time in restored and "[[time:" not in restored
                image = page.evaluate("structuredClone(__iconCheck.state.imageRefs[0])")
                assert image == seeded["image"], "Restoring an icon must preserve immutable source pixels and metadata"
                assert image["reference_id"] == clip["frames"][0]["id"] and image["time_sec"] == .100000001
                assert page.evaluate("__iconCheck.state.referencedSceneNodes[0].node_path") == seeded["node"]["node_path"]
                records = page.evaluate("__iconCheck.promptReferenceText.exportRecords()")
                timestamp = next(record for record in records if record["kind"] == "time")
                assert timestamp["token"] == seeded["time"] and timestamp["timeText"] == exact_time and timestamp["timeKey"] == seeded["timeKey"]
                baseline = source_state(page)
                print("PASS: legacy bracketed drafts migrate to icons with their original numbers, node paths, image pixels and exact 0.100000001s timestamp", flush=True)

                text = "前🧊1中🧊10后"
                cases = [("🧊1", 1, None, "Backspace", "deleteContentBackward", "前中🧊10后"),
                         ("🧊10", 1, None, "Delete", "deleteContentForward", "前🧊1中后"),
                         ("🧊10", 1, 3, "Backspace", "deleteContentBackward", "前🧊1中后")]
                for needle, start, end, key, input_type, result in cases:
                    prepare_edit(page, text, needle, start, end)
                    field.press(key)
                    assert field.input_value() == result, (key, field.input_value())
                    assert_native_edit(page, input_type)
                    field.press("Control+z")
                    assert field.input_value() == text, ("native undo", field.input_value())
                    assert "[[object:fixture_box1]]" in canonical(page) and "[[object:fixture_box10]]" in canonical(page)
                assert source_state(page) == baseline
                print("PASS: trusted Backspace/Delete and partial selections remove only complete icon1/icon10 references; native Ctrl+Z restores their bindings", flush=True)

                literal_text = "🧊1 🧊10 🧊100 [🧊1] 【🧊10】 [[literal:🧊1]]"
                assert page.evaluate("text=>__iconCheck.promptReferenceText.ranges(text).length", literal_text) == 2
                for literal in ("🧊100", "[🧊1]", "【🧊10】", "[[literal:🧊1]]"):
                    icon_start = 0 if literal == "🧊100" else 1 if literal.startswith("[🧊") or literal.startswith("【") else len("[[literal:")
                    prepare_edit(page, literal_text, literal, icon_start + 2)
                    field.press("Backspace")
                    without_icon = literal.replace("🧊", "", 1)
                    assert field.input_value() == literal_text.replace(literal, without_icon, 1), (literal, field.input_value())
                    assert_native_edit(page, "deleteContentBackward")
                    field.press("Control+z")
                    assert field.input_value() == literal_text
                assert canonical(page).count("[[object:fixture_box1]]") == 1
                assert canonical(page).count("[[object:fixture_box10]]") == 1
                assert source_state(page) == baseline
                print("PASS: unregistered icon100 and bracket/token-nested literals remain ordinary native text without prefix binding or atomic deletion", flush=True)

                cdp = context.new_cdp_session(page)
                for alias in ("🧊1", "🖼️1"):
                    text = "前 " + alias + " 后"
                    prepare_edit(page, text, alias, 1)
                    cdp.send("Input.imeSetComposition", {"text":"han","selectionStart":3,"selectionEnd":3})
                    cdp.send("Input.insertText", {"text":"汉"})
                    assert field.input_value() == "前 " + alias + "汉 后", field.input_value()
                    events = page.evaluate("__iconCheck.events")
                    starts = [event for event in events if event["type"] == "compositionstart"]
                    assert starts and starts[0]["isTrusted"], events
                    # The after-app listener sees the caret moved beyond the
                    # whole UTF-16 alias before composition text is inserted.
                    assert starts[0]["start"] == page.evaluate("alias=>2+alias.length", alias), starts
                    assert any(event["type"] == "beforeinput" and event["isTrusted"] and event["inputType"] == "insertCompositionText" for event in events), events
                    assert alias in field.input_value() and "[[" not in field.input_value()
                assert source_state(page) == baseline
                print("PASS: trusted Chromium composition moves a caret out of both surrogate-pair and variation-selector icons before committing Chinese text", flush=True)

                field.fill(expected)
                field.focus()
                page.evaluate("async()=>{__iconCheck.saveDraft();await __iconCheck.promptImageStore.pending;}")
                page.reload()
                ready(page)
                page.wait_for_function("__iconCheck.state.imageRefs.length===1")
                assert field.input_value() == expected and canonical(page) == restored
                assert source_state(page) == baseline
                for token, label in (("[[object:fixture_box1]]", "物体1"), (node_token, "部件1"),
                                     (f"[[image:{image['id']}]]", "图片1"), (seeded["time"], "时间戳1")):
                    chip = page.locator('[data-reference-token="' + token + '"]')
                    button = chip.locator(".prompt-reference-preview,.prompt-image-preview")
                    assert button.get_attribute("aria-label").startswith(label + " · ")
                    assert chip.locator(".prompt-reference-alias").inner_text().startswith({"物体1":"🧊1","部件1":"🧩1","图片1":"🖼️1","时间戳1":"🕒1"}[label])
                timestamp_chip = page.locator('[data-reference-token="' + seeded["time"] + '"] .prompt-reference-preview')
                timestamp_chip.click()
                assert page.locator("#prompt-reference-detail").inner_text() == exact_time
                assert "时间戳1" in page.locator("#prompt-reference-title").inner_text()
                page.locator("#prompt-reference-close").click()
                node_chip = page.locator('[data-reference-token="' + node_token + '"] .prompt-reference-preview')
                node_chip.click()
                assert "Door" in page.locator("#prompt-reference-detail").inner_text()
                assert page.locator("#prompt-reference-status").inner_text() == ""
                page.locator("#prompt-reference-close").click()
                field.focus()
                page.locator("#chat-dock").screenshot(path="/tmp/scene_feedback_reference_icons_046.png", animations="disabled")
                assert not feedback_requests and not store.state["feedback"], feedback_requests
                assert not errors, errors
                print("PASS: reload preserves icon mappings and source-bound detail cards with readable Chinese aria labels; no feedback or browser errors", flush=True)
                browser.close()
        finally:
            server.shutdown()
            thread.join(5)
            server.server_close()
    print("ALL PROMPT REFERENCE ICON BROWSER CHECKS PASSED", flush=True)


if __name__ == "__main__":
    main()

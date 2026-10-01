#!/usr/bin/env python3
"""Exercise @ selection and delivery in isolated Chromium; no live task or GPU."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "scripts")]
from test_prompt_drag_browser import image_data, tiny_named_glb
from test_wholebody_edit_browser import result_fixture


def ready(page):
    page.wait_for_function("window.__mentionCheck && __mentionCheck.state.workspaceReady && !__mentionCheck.state.sceneLoading && document.getElementById('reference-image').naturalWidth>0")


def query(page, text, *, note=None):
    field = page.locator("#feedback-note")
    if note is not None:
        field.fill(note)
    field.focus()
    field.press_sequentially("@" + text)
    page.wait_for_function("!document.getElementById('prompt-mentions').classList.contains('hidden')")


def choose(page, kind, text=None):
    option = page.locator(f'.prompt-mention-option[data-mention-kind="{kind}"]')
    if text:
        option = option.filter(has_text=text)
    option.first.click()
    page.wait_for_function("document.getElementById('prompt-mentions').classList.contains('hidden')")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-executable")
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright
    from server import make_server
    from mcp_server import _visual_tool_result

    errors = []
    with tempfile.TemporaryDirectory(prefix="prompt-mentions-browser-") as temporary:
        root = Path(temporary); project = root / "project"; project.mkdir()
        server = make_server(port=0, data_dir=root / "data", project_dir=project,
                             web_dir=ROOT / "web", external_review=True, feedback_transport="mcp_events")
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        store = server.scene_store; gateway = server.workspace_gateway
        session = gateway.ensure()["session_id"]
        model = root / "cabinet.glb"; tiny_named_glb(model)
        imported = store.import_model(str(model), object_id="fixture_model", name="模型柜子")
        store.update_scene(imported["scene_revision"], [{"op": "add", "object": {
            "id": "standalone_box", "name": "独立箱子", "type": "box", "position": [0, 0, 0],
            "size": [.4, .4, .4], "rotation": [0, 0, 0], "color": "#bb8844"}}])
        camera = {"camera_to_world": [[1,0,0,0],[0,1,0,0],[0,0,1,3],[0,0,0,1]],
                  "intrinsics": {"width":320,"height":240,"fx":300,"fy":300,"cx":160,"cy":120}}
        first = store.set_reference_clip(session, {"name": "正面", "fps": 2, "frames": [
            {"name": f"front{i}.png", "data_url": image_data("navy"), "time_sec": i*.5, "camera": camera} for i in range(3)]})["reference_clip"]
        clip = store.set_reference_clip(session, {"append_view": True, "name": "侧面", "fps": 2, "frames": [
            {"name": f"side{i}.png", "data_url": image_data("maroon"), "time_sec": i*.5, "camera": camera} for i in range(3)]})["reference_clip"]
        second = next(view for view in clip["views"] if view["clip_id"] != first["clip_id"])
        export = gateway.pose_jobs.export_sources({"session_id":session})
        pose = result_fixture(export); gateway.pose_jobs.import_result({"job_id":export["job_id"],"result":pose})
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True,
                    **({"executable_path":args.browser_executable} if args.browser_executable else {}),
                    args=["--no-sandbox","--use-gl=angle","--use-angle=swiftshader","--enable-unsafe-swiftshader"])
                context = browser.new_context(viewport={"width":1440,"height":950})
                hook = "\nwindow.__mentionCheck={state,ui,camera,cameraData,renderAnnotations,humanCurrentFrame,getPromptMentionCandidates,insertPromptMention,promptMentions};"
                context.route("**/app.js", lambda route: route.fulfill(status=200,content_type="application/javascript",body=(ROOT/"web/app.js").read_text()+hook))
                page = context.new_page(); page.on("pageerror",lambda error:errors.append(str(error)))
                page.goto(base+"/"); ready(page)
                page.wait_for_function("__mentionCheck.humanCurrentFrame(__mentionCheck.state.humanJobs[0]) !== null")
                field = page.locator("#feedback-note")

                # Real typing in the middle of Chinese text preserves both sides.
                field.fill("把  放在这里"); field.evaluate("n=>n.setSelectionRange(1,1)")
                query(page,"standalone")
                assert page.locator('.prompt-mention-option[data-mention-kind="object"]').count() == 1
                field.press("ArrowDown"); field.press("ArrowUp"); field.press("Enter")
                assert "[[object:standalone_box]]" in field.input_value()
                assert field.input_value().startswith("把") and field.input_value().endswith("  放在这里")
                assert not store.state["feedback"], "Enter selection must not send"
                print("PASS: @ search replaces only its middle-of-sentence range; keyboard picks a real object without submitting",flush=True)

                # Named GLB children are searchable without an earlier viewport pick.
                query(page,"Cabinet"); choose(page,"node","Cabinet")
                if not page.locator('[data-selection-level="part"]').is_visible():
                    page.locator('#references-dialog-button').click()
                page.locator('[data-selection-level="part"]').click()
                if page.locator('#references-dialog').is_visible():
                    page.locator('[data-close-dialog="references-dialog"]').click()
                query(page,"Door"); choose(page,"node","Door")
                assert field.input_value().count("[[node:fixture_model:") == 2
                assert len(page.evaluate("__mentionCheck.state.referencedSceneNodes")) == 2
                print("PASS: item/part mode exposes actual named GLB nodes with registered source paths",flush=True)

                page.locator('[data-tool="rectangle"]').click()
                bounds = page.locator("#reference-annotations").bounding_box(); assert bounds
                page.mouse.move(bounds["x"]+bounds["width"]*.2,bounds["y"]+bounds["height"]*.2); page.mouse.down()
                page.mouse.move(bounds["x"]+bounds["width"]*.4,bounds["y"]+bounds["height"]*.5,steps=8); page.mouse.up()
                page.wait_for_function("__mentionCheck.state.annotations.length===1")
                mark = page.evaluate("structuredClone(__mentionCheck.state.annotations[0])")
                query(page,"框"); choose(page,"annotation")
                assert f"[[annotation:{mark['id']}]]" in field.input_value()
                query(page,mark['name']); choose(page,"annotation",mark['name'])
                assert field.input_value().count(f"[[annotation:{mark['id']}]]") == 2
                assert "正面" in field.input_value() and "帧" in field.input_value()
                query(page,"左图"); choose(page,"current-image")
                left = page.evaluate("structuredClone(__mentionCheck.state.imageRefs[0])")
                assert left["reference_id"] == first["frames"][0]["id"]
                assert left.get("annotated_data_url") != left["original_data_url"]
                query(page,"右图"); choose(page,"current-image")
                right = page.evaluate("structuredClone(__mentionCheck.state.imageRefs[1])")
                page.evaluate("__mentionCheck.camera.position.x += .2")
                assert page.evaluate("structuredClone(__mentionCheck.state.imageRefs[1])") == right
                query(page,left["id"]); choose(page,"saved-image")
                assert field.input_value().count(f"[[image:{left['id']}]]") == 2
                assert page.evaluate("__mentionCheck.state.imageRefs.length") == 2
                print("PASS: annotation/source frame and both actual images stay frozen; saved-image mentions reuse the original capture",flush=True)

                query(page,"人体"); choose(page,"pose")
                page.locator("#reference-view-select").select_option(second["clip_id"])
                page.wait_for_function("(id)=>__mentionCheck.state.activeReferenceId===id && !__mentionCheck.state.seeking && __mentionCheck.humanCurrentFrame(__mentionCheck.state.humanJobs[0])",arg=second["frames"][0]["id"])
                query(page,"人体"); choose(page,"pose")
                assert page.evaluate("__mentionCheck.state.poseRefs.length") == 2
                assert page.evaluate("(id)=>structuredClone(__mentionCheck.state.imageRefs.find(image=>image.id===id))", left["id"]) == left
                edited = page.evaluate("""()=>{const m=__mentionCheck,j=m.state.humanJobs[0],f=m.humanCurrentFrame(j);
                    const e={id:'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',job_id:j.job_id,reference_id:f.reference_id,
                     image_sha256:f.image_sha256,image_orientation:f.image_orientation,keypoint_profile:j.keypoint_profile,
                     label:'侧面手指',edits:[{name:'left_thumb4',x:.5,y:.6,visibility:'visible'}]};
                    m.state.poseEdits.push(e);return e;}""")
                query(page,"修正"); choose(page,"pose-edit")
                query(page,"修正"); choose(page,"pose-edit")
                assert field.input_value().count(f"[[pose_edit:{edited['id']}]]") == 2
                print("PASS: two camera-specific pose citations and repeated hand-edit mentions retain exact source identities",flush=True)
                draft = field.input_value()

                field.fill("foo@example.com"); field.focus()
                assert page.locator("#prompt-mentions").is_hidden()
                query(page,"does-not-exist",note="")
                assert page.locator(".prompt-mention-option").count() == 0
                field.press("Enter"); assert not store.state["feedback"]
                field.press("Escape"); assert page.locator("#prompt-mentions").is_hidden()
                field.fill(""); query(page,"standalone")
                page.dispatch_event("#feedback-note","compositionstart")
                page.dispatch_event("#feedback-note","keydown",{"key":"Enter","code":"Enter","isComposing":True,"keyCode":229,"ctrlKey":True})
                assert "[[object:" not in field.input_value() and not store.state["feedback"]
                page.dispatch_event("#feedback-note","compositionend",{"data":"箱子"})
                field.press("Escape")
                field.fill(""); query(page,"standalone"); field.press("Control+Enter")
                assert "[[object:standalone_box]]" in field.input_value() and not store.state["feedback"]
                print("PASS: literal email, no results, Escape, Chinese IME and Ctrl+Enter do not accidentally send",flush=True)

                saved_nodes = page.evaluate("structuredClone(__mentionCheck.state.referencedSceneNodes)")
                page.evaluate("__mentionCheck.state.referencedSceneNodes=__mentionCheck.state.referencedSceneNodes.filter(n=>n.node_name!=='Door')")
                original_nodes = page.evaluate("structuredClone(__mentionCheck.state.referencedSceneNodes)")
                too_long = "字"*9988+" @Door"
                field.fill(too_long); field.focus(); field.press("End")
                page.wait_for_function("!document.getElementById('prompt-mentions').classList.contains('hidden')")
                field.press("Enter")
                assert field.input_value() == too_long
                assert page.evaluate("structuredClone(__mentionCheck.state.referencedSceneNodes)") == original_nodes
                assert not store.state["feedback"]
                field.press("Escape")
                page.evaluate("nodes=>{__mentionCheck.state.referencedSceneNodes=nodes}",saved_nodes)
                page.evaluate("__mentionCheck.state.imageReferencesSupported=false;__mentionCheck.state.poseCorrectionsSupported=false")
                query(page,"",note="")
                assert page.locator('[data-mention-kind="current-image"],[data-mention-kind="saved-image"],[data-mention-kind="pose-edit"]').count() == 0
                field.press("Escape")
                page.evaluate("__mentionCheck.state.imageReferencesSupported=true;__mentionCheck.state.poseCorrectionsSupported=true")
                print("PASS: capacity failure preserves query and reference registration; older services cannot add image/manual evidence",flush=True)

                for width in (390,340):
                    page.set_viewport_size({"width":width,"height":920})
                    query(page,"standalone",note="")
                    bounds = page.locator("#prompt-mentions").bounding_box(); assert bounds
                    assert bounds["x"] >= -1 and bounds["x"]+bounds["width"] <= width+1, (width,bounds)
                    assert bounds["y"] >= -1 and bounds["y"]+bounds["height"] <= 921, (width,bounds)
                    choose(page,"object")
                page.set_viewport_size({"width":1440,"height":950})
                field.fill(draft); field.focus(); field.press("End")
                page.reload(); ready(page)
                page.wait_for_function("__mentionCheck.state.imageRefs.length===2 && __mentionCheck.state.poseEdits.length===1")
                assert field.input_value() == draft
                print("PASS: 390/340px popup stays inside the viewport; reload restores every quoted source and the one prompt",flush=True)

                failures = []
                def reject(route):
                    failures.append(route.request.post_data_json)
                    route.fulfill(status=422,content_type="application/json",body=json.dumps({"error":"isolated synthetic rejection"}))
                page.route("**/api/sessions/*/feedback",reject)
                page.locator("#submit-button").click()
                page.wait_for_function("!__mentionCheck.state.submitting")
                assert failures and field.input_value() == draft and not store.state["feedback"]
                page.unroute("**/api/sessions/*/feedback",reject)
                page.locator("#submit-button").click()
                page.wait_for_function("__mentionCheck.state.feedbackCount===1 && !__mentionCheck.state.submitting")
                packet = store.state["feedback"][0]
                assert packet["note"] == draft
                assert len(packet["image_refs"]) == 2 and len(packet["human_pose"]) == 2 and len(packet["human_pose_edits"]) == 1
                assert {p["frame"]["reference_id"] for p in packet["human_pose"]} == {first["frames"][0]["id"],second["frames"][0]["id"]}
                assert packet["human_pose_edits"][0]["document"]["frames"][0]["effective_keypoints"][95]["score"] == .15
                result = _visual_tool_result({"items":[copy.deepcopy(packet)]},store.data_dir)
                assert sum(item.type=="image" for item in result.content) >= 8
                assert field.input_value() == "" and not page.evaluate("__mentionCheck.state.poseEdits")
                assert page.locator("#prompt-mentions").is_hidden()
                print("PASS: rejected save keeps the draft; accepted feedback/MCP return real images, named nodes and source-bound pose corrections",flush=True)

                field.fill(""); query(page,"Door")
                replacement = root/"replacement.glb"; tiny_named_glb(replacement)
                replacement.write_bytes(replacement.read_bytes().replace(b'Door',b'Wall'))
                prior = store.scene()["revision"]
                store.update_scene(prior,[{"op":"delete","object_id":"fixture_model"}])
                store.import_model(str(replacement),object_id="fixture_model",name="新模型")
                page.wait_for_function("(r)=>__mentionCheck.state.sceneRevision>r && !__mentionCheck.state.sceneLoading",arg=prior)
                field.press("Enter")
                assert "[[node:" not in field.input_value()
                assert "@Door" in field.input_value()
                assert not page.evaluate("__mentionCheck.state.referencedSceneNodes")
                assert not errors,errors
                print("PASS: an open old GLB suggestion cannot insert the same path from a replacement model",flush=True)
                browser.close()
        finally:
            server.shutdown(); thread.join(5); server.server_close()
    print("ALL PROMPT MENTION BROWSER CHECKS PASSED",flush=True)


if __name__ == "__main__":
    main()

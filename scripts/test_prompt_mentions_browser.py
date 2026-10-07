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
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "scripts"), str(ROOT / "tests")]
from test_prompt_drag_browser import image_data, tiny_named_glb
from test_wholebody_edit_browser import result_fixture
from workspace_ui_helpers import control, choose_tool


def ready(page):
    page.wait_for_function("window.__mentionCheck && __mentionCheck.state.workspaceReady && !__mentionCheck.state.sceneLoading && document.getElementById('reference-image').naturalWidth>0")


def canonical(page):
    return page.evaluate("__mentionCheck.promptText()")


def query(page, text, *, note=None):
    if not page.locator("#chat-dock").is_visible():
        control(page, "#chat-launcher").click()
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


def select_model(page, level):
    return page.evaluate("""level=>{const m=__mentionCheck,root=m.state.objectNodes.get('fixture_model')?.userData.gltfRoot;
        const node=level==='part' ? root?.children[0]?.children[0] : root?.children[0];
        if(!node) throw new Error('fixture GLB is not loaded');
        m.state.selectionLevel=level;
        const ref=m.nodeReference('fixture_model',node,level);m.selectObject('fixture_model',ref,node);return ref;}""",level)


def draw_mark(page, pane, tool):
    if page.locator("#chat-dock").is_visible():
        control(page, "#chat-collapse").click()
    choose_tool(page, tool, pane)
    bounds=page.locator(f'#{pane}-annotations').bounding_box(); assert bounds
    count=page.evaluate('__mentionCheck.state.annotations.length')
    page.mouse.move(bounds['x']+bounds['width']*.2,bounds['y']+bounds['height']*.2); page.mouse.down()
    page.mouse.move(bounds['x']+bounds['width']*.4,bounds['y']+bounds['height']*.5,steps=8); page.mouse.up()
    page.wait_for_function('(n)=>__mentionCheck.state.annotations.length===n+1',arg=count)
    return page.evaluate('structuredClone(__mentionCheck.state.annotations.at(-1))')


def reject_old_candidate(page, candidate):
    return page.evaluate("""candidate=>{const m=__mentionCheck,before=m.ui.note.value,nodes=JSON.stringify(m.state.referencedSceneNodes);
        let result=false;try{result=m.insertPromptMention(candidate);}catch(error){}
        return !result && before===m.ui.note.value && nodes===JSON.stringify(m.state.referencedSceneNodes);}""",candidate)


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
                hook = "\nwindow.__mentionCheck={state,ui,camera,cameraData,renderAnnotations,renderSelection,humanCurrentFrame,getPromptMentionCandidates,insertPromptMention,promptMentions,promptText,selectObject,nodeReference,removeAnnotation,undoAnnotationEdit};"
                context.route("**/app.js", lambda route: route.fulfill(status=200,content_type="application/javascript",body=(ROOT/"web/app.js").read_text()+hook))
                page = context.new_page(); page.on("pageerror",lambda error:errors.append(str(error)))
                page.goto(base+"/"); ready(page)
                page.wait_for_function("__mentionCheck.humanCurrentFrame(__mentionCheck.state.humanJobs[0]) !== null")
                field = page.locator("#feedback-note")

                query(page,'',note='')
                assert page.locator('.prompt-mention-option').count()==0
                assert page.locator('#prompt-mention-status').inner_text().startswith('先选中物体')
                assert page.evaluate('__mentionCheck.state.sceneObjects.length')==2
                assert page.evaluate('__mentionCheck.state.humanJobs.length')==1
                field.press('Enter'); assert not store.state['feedback']
                field.press('Escape')
                print('PASS: unselected objects, GLB children and loaded human evidence never populate the empty @ menu',flush=True)

                # Real typing in the middle of Chinese text preserves both sides.
                page.evaluate("__mentionCheck.selectObject('standalone_box')")
                field.fill("把  放在这里"); field.evaluate("n=>n.setSelectionRange(1,1)")
                query(page,"standalone")
                assert page.locator('.prompt-mention-option[data-mention-kind="object"]').count() == 1
                field.press("ArrowDown"); field.press("ArrowUp"); field.press("Enter")
                assert "[[object:standalone_box]]" in canonical(page)
                assert "🧊1" in field.input_value() and "[[" not in field.input_value()
                assert field.input_value().startswith("把") and field.input_value().endswith("  放在这里")
                assert not store.state["feedback"], "Enter selection must not send"
                assert page.evaluate("__mentionCheck.getPromptMentionCandidates().map(c=>c.kind)")==['object']
                object_draft=field.input_value()
                print("PASS: only the selected object is searchable; middle-of-sentence keyboard insertion preserves surrounding text",flush=True)

                # Switching/clearing selection invalidates cached entries and an open popup.
                selected=page.evaluate('__mentionCheck.getPromptMentionCandidates()[0]')
                query(page,'standalone')
                select_model(page,'item')
                assert page.locator('.prompt-mention-option').count()==0
                old_note=field.input_value(); field.press('Enter'); assert field.input_value()==old_note
                assert reject_old_candidate(page,selected)
                item_candidate=page.evaluate('__mentionCheck.getPromptMentionCandidates()[0]')
                page.evaluate("__mentionCheck.state.selectedId=null;__mentionCheck.state.selectedSceneNode=null;__mentionCheck.renderSelection()")
                assert not page.evaluate('__mentionCheck.getPromptMentionCandidates()')
                assert reject_old_candidate(page,item_candidate)
                field.press('Escape')
                print('PASS: changing or clearing selection removes old menu entries and rejects cached targets without altering the prompt',flush=True)

                # Only the one picked GLB item/part appears, even after both have been cited.
                select_model(page,'item')
                query(page,"Cabinet",note=object_draft+' '); choose(page,"node","Cabinet")
                select_model(page,'part')
                query(page,"Door"); choose(page,"node","Door")
                assert canonical(page).count("[[node:fixture_model:") == 2
                assert "🧩1" in field.input_value() and "🧩2" in field.input_value()
                assert len(page.evaluate("__mentionCheck.state.referencedSceneNodes")) == 2
                candidates=page.evaluate('__mentionCheck.getPromptMentionCandidates()')
                assert len(candidates)==1 and candidates[0]['kind']=='node' and candidates[0]['label']=='Door'
                assert reject_old_candidate(page,{**candidates[0],'sessionId':'another-session'})
                print("PASS: a selected GLB part has one candidate; its parent, wrapper and prior references are excluded",flush=True)

                mark=draw_mark(page,'reference','rectangle')
                mark_candidate=next(c for c in page.evaluate('__mentionCheck.getPromptMentionCandidates()') if c['kind']=='annotation' and c['descriptor']['annotationId']==mark['id'])
                assert "正面" in mark_candidate['descriptor']['label'] and "帧" in mark_candidate['descriptor']['label']
                query(page,"框"); choose(page,"annotation")
                assert f"[[annotation:{mark['id']}]]" in canonical(page)
                query(page,mark['name']); choose(page,"annotation",mark['name'])
                assert canonical(page).count(f"[[annotation:{mark['id']}]]") == 2
                assert "📍1" in field.input_value()
                control(page, '#drag-reference-image').click()
                left = page.evaluate("structuredClone(__mentionCheck.state.imageRefs[0])")
                assert "🖼️1" in field.input_value()
                assert left["reference_id"] == first["frames"][0]["id"]
                assert left.get("annotated_data_url") != left["original_data_url"]
                page.locator('#reference-strip button[data-view-id="' + second["clip_id"] + '"]').first.click()
                page.wait_for_function("(id)=>__mentionCheck.state.activeReferenceId===id && !__mentionCheck.state.seeking && __mentionCheck.humanCurrentFrame(__mentionCheck.state.humanJobs[0])",arg=second["frames"][0]["id"])
                page.locator('#timeline-seek').focus(); page.locator('#timeline-seek').press('End')
                page.wait_for_function('(id)=>__mentionCheck.state.activeReferenceId===id && !__mentionCheck.state.seeking',arg=second['frames'][-1]['id'])
                side_mark=draw_mark(page,'reference','line')
                control(page, '#capture-scene-button').click()
                page.wait_for_function("__mentionCheck.state.sceneView==='snapshot' && !!__mentionCheck.state.snapshot")
                scene_mark=draw_mark(page,'scene','arrow')
                page.evaluate("__mentionCheck.state.selectedId=null;__mentionCheck.state.selectedSceneNode=null;__mentionCheck.renderSelection()")
                candidates=page.evaluate('__mentionCheck.getPromptMentionCandidates()')
                assert {c['descriptor']['annotationId'] for c in candidates if c['kind']=='annotation'}=={mark['id'],side_mark['id'],scene_mark['id']}
                assert set(c['kind'] for c in candidates)<= {'node','object','annotation'}
                for number,m in enumerate((side_mark,scene_mark),2):
                    query(page,m['name']); choose(page,'annotation',m['name'])
                    assert f"[[annotation:{m['id']}]]" in canonical(page)
                    assert f"📍{number}" in field.input_value()
                assert any('侧面' in c['descriptor']['label'] for c in candidates if c['kind']=='annotation' and c['descriptor']['annotationId']==side_mark['id'])
                print('PASS: all unsent marks across reference views, frames and a frozen scene screenshot remain selectable in this round',flush=True)

                old_mark=next(c for c in candidates if c['kind']=='annotation' and c['descriptor']['annotationId']==mark['id'])
                query(page,mark['name'])
                page.evaluate('(id)=>__mentionCheck.removeAnnotation(id)',mark['id'])
                assert page.locator('.prompt-mention-option').count()==0
                old_note=field.input_value(); field.press('Enter'); assert field.input_value()==old_note
                assert reject_old_candidate(page,old_mark)
                page.evaluate('__mentionCheck.undoAnnotationEdit()')
                assert page.locator('.prompt-mention-option').count()==1
                choose(page,'annotation',mark['name'])
                print('PASS: deleting a mark immediately removes its candidate; undo restores it and stale insertion fails safely',flush=True)

                control(page, '#drag-scene-image').click()
                right = page.evaluate("structuredClone(__mentionCheck.state.imageRefs[1])")
                page.evaluate("__mentionCheck.camera.position.x += .2")
                assert page.evaluate("structuredClone(__mentionCheck.state.imageRefs[1])") == right
                assert page.evaluate("(id)=>structuredClone(__mentionCheck.state.imageRefs.find(image=>image.id===id))", left["id"]) == left
                draft=field.input_value()
                draft_note=canonical(page)
                assert "[[" not in draft
                page.evaluate("""()=>{const m=__mentionCheck,j=m.state.humanJobs[0],f=m.humanCurrentFrame(j);
                    const e={id:'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',job_id:j.job_id,reference_id:f.reference_id,
                     image_sha256:f.image_sha256,image_orientation:f.image_orientation,keypoint_profile:j.keypoint_profile,
                     label:'侧面手指',edits:[{name:'left_thumb4',x:.5,y:.6,visibility:'visible'}]};
                    m.state.poseEdits.push(e);}""")
                query(page,'',note=field.input_value()+' ')
                assert set(page.evaluate('__mentionCheck.promptMentions.candidates.map(c=>c.kind)'))<= {'node','object','annotation','pose_edit'}
                query(page,'修正',note=draft+' ');choose(page,'pose_edit')
                assert '✏️1' in field.input_value() and '[[pose_edit:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee]]' in canonical(page)
                for term in ('左图','右图','人体结果',left['id']):
                    query(page,term,note='')
                    assert page.locator('.prompt-mention-option').count()==0
                    field.press('Escape')
                print('PASS: images keep frozen evidence and loaded human results stay out of @; this round’s correction uses a numbered reference',flush=True)

                field.fill("foo@example.com"); field.focus()
                assert page.locator("#prompt-mentions").is_hidden()
                query(page,"does-not-exist",note="")
                assert page.locator(".prompt-mention-option").count() == 0
                field.press("Enter"); assert not store.state["feedback"]
                field.press("Escape"); assert page.locator("#prompt-mentions").is_hidden()
                page.evaluate("__mentionCheck.selectObject('standalone_box')")
                field.fill(""); query(page,"standalone")
                page.dispatch_event("#feedback-note","compositionstart")
                page.dispatch_event("#feedback-note","keydown",{"key":"Enter","code":"Enter","isComposing":True,"keyCode":229,"ctrlKey":True})
                assert "[[object:" not in canonical(page) and not store.state["feedback"]
                page.dispatch_event("#feedback-note","compositionend",{"data":"箱子"})
                field.press("Escape")
                field.fill(""); query(page,"standalone"); field.press("Control+Enter")
                assert "[[object:standalone_box]]" in canonical(page) and not store.state["feedback"]
                print("PASS: literal email, no results, Escape, Chinese IME and Ctrl+Enter do not accidentally send",flush=True)

                select_model(page,'part')
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
                print("PASS: a full prompt preserves its @ query and does not register a failed part citation",flush=True)

                for width in (390,340):
                    page.set_viewport_size({"width":width,"height":920})
                    query(page,"Door",note="")
                    bounds = page.locator("#prompt-mentions").bounding_box(); assert bounds
                    assert bounds["x"] >= -1 and bounds["x"]+bounds["width"] <= width+1, (width,bounds)
                    assert bounds["y"] >= -1 and bounds["y"]+bounds["height"] <= 921, (width,bounds)
                    choose(page,"node")
                page.set_viewport_size({"width":1440,"height":950})
                field.fill(draft); field.focus(); field.press("End")
                page.reload(); ready(page)
                page.wait_for_function("__mentionCheck.state.imageRefs.length===2 && __mentionCheck.state.poseEdits.length===1")
                assert field.input_value() == draft
                assert canonical(page) == draft_note
                assert {c['descriptor']['annotationId'] for c in page.evaluate('__mentionCheck.getPromptMentionCandidates()') if c['kind']=='annotation'}=={mark['id'],side_mark['id'],scene_mark['id']}
                print("PASS: 390/340px popup stays in bounds; reload restores the prompt, selected part and this round’s cross-view marks",flush=True)

                failures = []
                def reject(route):
                    failures.append(route.request.post_data_json)
                    route.fulfill(status=422,content_type="application/json",body=json.dumps({"error":"isolated synthetic rejection"}))
                page.route("**/api/sessions/*/feedback",reject)
                page.locator("#submit-button").click()
                page.wait_for_function("!__mentionCheck.state.submitting")
                assert failures and field.input_value() == draft and canonical(page) == draft_note and not store.state["feedback"]
                assert failures[0]["note"] == draft_note.strip()
                page.unroute("**/api/sessions/*/feedback",reject)
                page.locator("#submit-button").click()
                page.wait_for_function("__mentionCheck.state.feedbackCount===1 && !__mentionCheck.state.submitting")
                packet = store.state["feedback"][0]
                assert packet["note"] == draft_note.strip()
                assert len(packet['image_refs'])==2
                assert {m['id'] for m in packet['annotations']}=={mark['id'],side_mark['id'],scene_mark['id']}
                result = _visual_tool_result({"items":[copy.deepcopy(packet)]},store.data_dir)
                assert sum(item.type=="image" for item in result.content) >= 4
                assert field.input_value() == "" and not page.evaluate("__mentionCheck.state.poseEdits")
                assert page.locator("#prompt-mentions").is_hidden()
                query(page,'',note='')
                assert page.locator('.prompt-mention-option').count()==0
                assert page.locator('#prompt-mention-status').inner_text().startswith('先选中物体')
                field.press('Escape')
                print("PASS: rejected save keeps the draft; accepted feedback/MCP deliver real evidence and clear round marks and selection",flush=True)

                select_model(page,'part')
                old_candidate=page.evaluate('__mentionCheck.getPromptMentionCandidates()[0]')
                field.fill(""); query(page,"Door")
                replacement = root/"replacement.glb"; tiny_named_glb(replacement)
                replacement.write_bytes(replacement.read_bytes().replace(b'Door',b'Wall'))
                prior = store.scene()["revision"]
                store.update_scene(prior,[{"op":"delete","object_id":"fixture_model"}])
                store.import_model(str(replacement),object_id="fixture_model",name="新模型")
                page.wait_for_function("(r)=>__mentionCheck.state.sceneRevision>r && !__mentionCheck.state.sceneLoading",arg=prior)
                field.press("Enter")
                assert "[[node:" not in canonical(page)
                assert "@Door" in field.input_value()
                assert not page.evaluate("__mentionCheck.state.referencedSceneNodes")
                assert reject_old_candidate(page,old_candidate)
                assert not errors,errors
                print("PASS: an open old GLB suggestion cannot insert the same path from a replacement model",flush=True)
                browser.close()
        finally:
            server.shutdown(); thread.join(5); server.server_close()
    print("ALL PROMPT MENTION BROWSER CHECKS PASSED",flush=True)


if __name__ == "__main__":
    main()

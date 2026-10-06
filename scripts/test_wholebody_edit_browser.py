#!/usr/bin/env python3
"""Exercise source-bound body, face, foot and hand edits in an isolated workbench.

Synthetic named 133-point observations only. No inference, GPU, real project
feedback, or Codex model requests are involved.
"""
from __future__ import annotations

import argparse
import base64
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "tests"), str(ROOT)]
from workspace_ui_helpers import choose_tool
spec = importlib.util.spec_from_file_location("wholebody_browser_profile",ROOT / "external-skills/capsule-human-tracking/scripts/wholebody_profile.py")
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


def image_data(color):
    from PIL import Image
    output=io.BytesIO(); Image.new("RGB",(320,240),color).save(output,"PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()


def result_fixture(export):
    manifest=export["manifest"]
    result={key:copy.deepcopy(manifest[key]) for key in
        ("schema_version","job_id","track_id","project_id","session_id","source_snapshot_id","view_ids")}
    result.update(profile.wholebody133_topology())
    result.update(evidence_kind="observed_2d",provenance={"kind":"image_inference","generator":"synthetic browser fixture; no inference"})
    result["frames"]=[]
    for source in manifest["frames"]:
        frame={key:copy.deepcopy(source[key]) for key in
            ("ref_id","view_id","width","height","frame_index","time_seconds","image_sha256","image_orientation")}
        points=[]
        for index,name in enumerate(result["keypoint_names"]):
            x,y=.25+(index%5)*.04,.15+(index//5%5)*.035
            if 23 <= index < 91: x,y=.45+(index%8)*.006,.15+(index//8%8)*.006
            if index >= 91:
                base=91 if index < 112 else 112; side=.3 if index < 112 else .7
                position=index-base
                x,y=(side,.68) if position == 0 else (side-.09+((position-1)//4)*.045,.64-((position-1)%4)*.055)
            # Isolate representative points so direct nearest-point gestures
            # exercise selection rather than an ambiguous dense face cluster.
            x,y={"left_shoulder":(.22,.28),"left_big_toe":(.23,.82),
                 "face-0":(.55,.13)}.get(name,(x,y))
            points.append({"name":name,"x":x,"y":y,"score":.15 if name == "left_thumb4" else .9,
                           "in_frame":name != "left_pinky_finger4"})
        frame.update(keypoints=points,bbox=[.1,.1,.8,.8],tracking_status="tracked")
        if source["frame_index"] == 1:
            frame.update(bbox=None,tracking_status="lost")
            for point in points: point.update(score=0,in_frame=False)
        result["frames"].append(frame)
    return result


def ready(page):
    page.wait_for_function("window.__wholeEdit && __wholeEdit.state.workspaceReady && !__wholeEdit.state.sceneLoading && document.getElementById('reference-image').naturalWidth>0")


def wait_frame(page,reference_id):
    page.wait_for_function("(id)=>{const w=__wholeEdit,j=w.state.humanJobs[0]; return w.state.activeReferenceId===id && !w.state.seeking && w.humanCurrentFrame(j)?.reference_id===id}",arg=reference_id)


def open_editor(page):
    open_reference_tools(page)
    if page.locator("#pose-edit-tool").get_attribute("aria-pressed") != "true":
        page.locator("#pose-edit-tool").click()
    page.wait_for_function("document.getElementById('pose-edit-panel').offsetWidth>0 && document.getElementById('human-pose-overlay').classList.contains('pose-editing')")


def pose_patches(page):
    return page.evaluate("structuredClone(__wholeEdit.state.poseEdits)")


def current_point(page,name):
    return page.evaluate("""(name)=>{const p=__wholeEdit.poseEditorContext();
        const original=p.frame.keypoints.find(point=>point.name===name);
        return {...original,...p.sample?.edits.find(point=>point.name===name)}}""",name)


def point_position(page,point):
    bounds=page.locator("#human-pose-overlay").bounding_box(); assert bounds
    return bounds["x"]+point[0]*bounds["width"],bounds["y"]+point[1]*bounds["height"]


def edit_point(page,name,region,target,drag=True,select_joint=True):
    page.locator("#pose-edit-hand").select_option(region)
    if select_joint:
        page.locator("#pose-edit-joint").select_option(name)
    if page.locator("#pose-edit-visibility").input_value() != "visible":
        page.locator("#pose-edit-visibility").select_option("visible")
    original=current_point(page,name)
    if drag and original["in_frame"]:
        page.mouse.move(*point_position(page,(original["x"],original["y"])))
        page.mouse.down()
        page.mouse.move(*point_position(page,target),steps=8)
        page.mouse.up()
    else:
        page.mouse.click(*point_position(page,target))
    page.wait_for_function("(name)=>__wholeEdit.state.poseEdits.some(e=>e.edits.some(p=>p.name===name&&p.visibility==='visible'))",arg=name)
    patch=page.evaluate("(name)=>__wholeEdit.poseEditorContext().sample.edits.find(p=>p.name===name)",name)
    assert abs(patch["x"]-target[0])<.004 and abs(patch["y"]-target[1])<.004,patch


def open_changes(page):
    if page.locator("#chat-launcher").is_visible():
        page.locator("#chat-launcher").click()
    if not page.locator("#references-dialog").is_visible():
        page.locator("#references-dialog-button").click()


def close_changes(page):
    if page.locator("#references-dialog").is_visible():
        page.locator('[data-close-dialog="references-dialog"]').click()


def history_action(page,action):
    open_reference_tools(page)
    page.locator("#"+action+"-annotation").click()


def open_reference_tools(page):
    panel=page.locator("#annotation-tool-panel")
    if not panel.is_visible() or panel.get_attribute("data-context") != "reference":
        # The inline pose editor can cover the media's center. Enter the
        # exposed image corner, just as the user can, rather than its panel.
        page.mouse.move(4,4)
        page.locator("#reference-media").hover(position={"x":20,"y":20})
        page.wait_for_function("!document.getElementById('annotation-tool-panel').hidden && document.getElementById('annotation-tool-panel').dataset.context==='reference'")
        panel.hover()


def draw_regular_arrow(page):
    choose_tool(page,"arrow","reference")
    start=point_position(page,(.12,.9)); end=point_position(page,(.35,.92))
    page.mouse.move(*start); page.mouse.down(); page.mouse.move(*end,steps=6); page.mouse.up()
    page.wait_for_function("__wholeEdit.state.annotations.some(mark=>mark.pane==='reference'&&mark.type==='arrow')")
    return page.evaluate("structuredClone(__wholeEdit.state.annotations)")


def close_tools(page):
    if page.locator("#annotation-tool-panel").is_visible():
        page.locator("#annotation-tools-close").click()


def close_chat(page):
    if page.locator("#chat-collapse").is_visible():
        page.locator("#chat-collapse").click()


def open_chat(page):
    if page.locator("#chat-launcher").is_visible():
        page.locator("#chat-launcher").click()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-executable")
    args=parser.parse_args()
    from playwright.sync_api import sync_playwright
    from server import make_server
    errors=[]
    with tempfile.TemporaryDirectory(prefix="wholebody-edit-browser-") as temporary:
        root=Path(temporary); project=root/"project"; project.mkdir()
        server=make_server(port=0,data_dir=root/"data",project_dir=project,web_dir=ROOT/"web",external_review=True,feedback_transport="mcp_events")
        worker=threading.Thread(target=server.serve_forever,daemon=True); worker.start()
        store=server.scene_store; gateway=server.workspace_gateway; workspace=gateway.ensure(); session=workspace["session_id"]
        calibration={"camera_to_world":[[1,0,0,0],[0,1,0,0],[0,0,1,3],[0,0,0,1]],
            "intrinsics":{"width":320,"height":240,"fx":300,"fy":300,"cx":160,"cy":120}}
        primary=store.set_reference_clip(session,{"name":"front","fps":2,"frames":[
            {"name":f"A{i}.png","time_sec":i*.5,"data_url":image_data((213,199+i*20,173)),"camera":calibration} for i in range(2)]})["reference_clip"]
        side_calibration=copy.deepcopy(calibration); side_calibration["camera_to_world"][0][3]=1
        clip=store.set_reference_clip(session,{"append_view":True,"name":"side","fps":2,"frames":[
            {"name":f"B{i}.png","time_sec":i*.5,"data_url":image_data((210,201,191+i*20)),"camera":side_calibration} for i in range(2)]})["reference_clip"]
        side=clip["views"][0]
        export=gateway.pose_jobs.export_sources({"session_id":session}); original=result_fixture(export)
        gateway.pose_jobs.import_result({"job_id":export["job_id"],"result":original})
        original_path=store.data_dir/"human_pose"/export["job_id"]/"output.json"; original_bytes=original_path.read_bytes()
        base=f"http://127.0.0.1:{server.server_port}/p/{workspace['project_id']}"
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(**({"executable_path":args.browser_executable} if args.browser_executable else {}),headless=True,
                    args=["--no-sandbox","--no-proxy-server","--use-gl=angle","--use-angle=swiftshader","--enable-unsafe-swiftshader"])
                context=browser.new_context(viewport={"width":1440,"height":950},accept_downloads=True)
                hook="\nwindow.__wholeEdit={state,ui,promptText,humanCurrentFrame,humanOverlayJob,drawHumanPoseOverlay,poseEditorContext,downloadPoseEdit,getPromptMentionCandidates};"
                context.route("**/app.js",lambda route:route.fulfill(status=200,content_type="application/javascript",body=(ROOT/"web/app.js").read_text()+hook))
                page=context.new_page(); page.on("pageerror",lambda error:errors.append(str(error)))
                page.goto(base+"/"); ready(page); wait_frame(page,primary["frames"][0]["id"])
                assert not errors,errors
                assert page.evaluate("__wholeEdit.state.humanJobs[0].keypoint_names.length")==133
                count=page.evaluate("""()=>{const w=__wholeEdit,c=w.ui.humanCanvas.getContext('2d'),arc=c.arc;let n=0;c.arc=function(...a){n++;return arc.apply(this,a)};try{w.drawHumanPoseOverlay()}finally{c.arc=arc}return n}""")
                assert count==131,count
                pixels=page.evaluate("""()=>{const w=__wholeEdit,m=w.ui.referenceMedia;const display=m.style.display;
                    m.style.display='none';w.state.humanOverlayChoice='hidden';w.drawHumanPoseOverlay();
                    const data=w.ui.humanCanvas.getContext('2d').getImageData(0,0,w.ui.humanCanvas.width,w.ui.humanCanvas.height).data;
                    let n=0;for(let i=3;i<data.length;i+=4)if(data[i])n++;
                    m.style.display=display;w.state.humanOverlayChoice='latest';w.drawHumanPoseOverlay();return n}""")
                assert pixels==0,pixels
                print("PASS: a hidden/resizing source pane clears historical overlay pixels before its logical size returns",flush=True)
                close_chat(page)
                marks=draw_regular_arrow(page)
                open_editor(page)
                assert page.locator("#pose-edit-panel").is_visible()
                for region,count in (("all",133),("body",17),("feet",6),("face",68),("left",21),("right",21)):
                    page.locator("#pose-edit-hand").select_option(region)
                    assert page.locator("#pose-edit-joint option").count()==count,(region,count)
                assert not pose_patches(page),"Opening and choosing regions must not create a correction"
                page.locator("#pose-edit-hand").select_option("all")
                point=current_point(page,"left_shoulder")
                page.mouse.click(*point_position(page,(point["x"],point["y"])))
                assert page.locator("#pose-edit-joint").input_value()=="left_shoulder"
                assert not pose_patches(page),"Clicking an existing point only selects it"
                edit_point(page,"left_shoulder","all",(.2,.35),select_joint=False)
                body_patch=pose_patches(page)
                assert len(body_patch)==1 and len(body_patch[0]["edits"])==1
                assert "关键点修改1" in page.locator("#feedback-note").input_value()
                assert page.evaluate("__wholeEdit.promptText()").count("[[pose_edit:")==1
                assert page.evaluate("document.activeElement.id") != "feedback-note"
                assert page.locator("#annotation-tool-panel").is_visible()
                history_action(page,"undo"); assert not pose_patches(page)
                assert "[[pose_edit:" not in page.evaluate("__wholeEdit.promptText()")
                history_action(page,"redo"); assert pose_patches(page)==body_patch
                assert page.evaluate("structuredClone(__wholeEdit.state.annotations)")==marks
                # A cancelled pointer gesture must not leave a partial move,
                # an extra citation, or a spurious history entry.
                point=current_point(page,"left_shoulder")
                page.mouse.move(*point_position(page,(point["x"],point["y"]))); page.mouse.down()
                page.mouse.move(*point_position(page,(.27,.43)),steps=5)
                page.locator("#human-pose-overlay").dispatch_event("pointercancel",{"pointerId":page.evaluate("__wholeEdit.state.poseEditDrag.pointerId"),"pointerType":"mouse"})
                page.mouse.up(); assert pose_patches(page)==body_patch
                history_action(page,"undo"); assert not pose_patches(page)
                history_action(page,"redo"); assert pose_patches(page)==body_patch
                print("PASS: all 133 declared joints are selectable; direct body drag, selection-only click, one-step undo/redo and pointercancel preserve ordinary arrows",flush=True)

                # Real transformed-image gestures must still write original
                # normalized image coordinates, not CSS pixels after zoom/pan.
                close_tools(page)
                page.locator("#reference-zoom-in").click()
                page.evaluate("document.activeElement.blur()")
                page.keyboard.down("Space")
                page.mouse.move(*point_position(page,(.5,.5))); page.mouse.down()
                pan_start=point_position(page,(.5,.5))
                page.mouse.move(pan_start[0]+24,pan_start[1]+30,steps=4)
                page.mouse.up(); page.keyboard.up("Space")
                assert page.evaluate("__wholeEdit.state.referenceZoom")>1
                assert abs(page.evaluate("__wholeEdit.state.referencePan.y"))>1
                edit_point(page,"left_shoulder","body",(.31,.38))
                history_action(page,"undo"); assert pose_patches(page)==body_patch
                history_action(page,"redo")
                close_tools(page)
                page.locator("#reference-zoom-reset").click()
                edit_point(page,"left_big_toe","feet",(.25,.79))
                edit_point(page,"face-0","face",(.57,.19))

                edit_point(page,"left_thumb4","left",(.32,.36))
                assert "低置信" in page.locator("#pose-edit-hint").inner_text()
                edit_point(page,"left_pinky_finger4","left",(.17,.59),drag=False)
                edit_point(page,"right_forefinger4","right",(.74,.39))
                page.locator("#pose-edit-joint").select_option("right_pinky_finger4"); page.locator("#pose-edit-visibility").select_option("missing")
                page.locator("#pose-edit-joint").select_option("right_ring_finger4"); page.locator("#pose-edit-visibility").select_option("occluded")
                page.locator("#pose-edit-joint").select_option("right_thumb4"); page.locator("#pose-edit-visibility").select_option("missing")
                patches=pose_patches(page)
                assert len(patches)==1 and len(patches[0]["edits"])==9,patches
                for patch in patches[0]["edits"]:
                    if patch["visibility"] == "missing": assert "x" not in patch and "y" not in patch,patch
                    if patch["visibility"] == "occluded":
                        original_joint=page.evaluate("(name)=>__wholeEdit.poseEditorContext().frame.keypoints.find(point=>point.name===name)",patch["name"])
                        assert patch["x"]==original_joint["x"] and patch["y"]==original_joint["y"],patch
                assert page.evaluate("__wholeEdit.promptText()").count("[[pose_edit:")==1
                candidate=page.evaluate("__wholeEdit.getPromptMentionCandidates().find(item=>item.label==='关键点修改1')")
                assert candidate,candidate
                assert page.evaluate("structuredClone(__wholeEdit.state.annotations)")==marks
                # Reset and remove are undoable without disturbing unrelated
                # point corrections or the original annotation.
                page.locator("#pose-edit-reset-joint").click()
                assert len(pose_patches(page)[0]["edits"])==8
                history_action(page,"undo"); assert pose_patches(page)==patches
                history_action(page,"redo"); assert len(pose_patches(page)[0]["edits"])==8
                history_action(page,"undo"); assert pose_patches(page)==patches
                open_changes(page)
                row=page.locator(".human-pose-edit-draft")
                assert row.count()==1 and "关键点修改1" in row.inner_text()
                assert page.locator(".human-pose-job:not(.human-pose-edit-draft)").count()==0
                row.get_by_role("button",name="移除",exact=True).click()
                assert not pose_patches(page) and "[[pose_edit:" not in page.evaluate("__wholeEdit.promptText()")
                close_changes(page); history_action(page,"undo"); assert pose_patches(page)==patches
                open_changes(page); page.locator(".human-pose-edit-draft").get_by_role("button",name="回看",exact=True).click()
                assert not page.locator("#references-dialog").is_visible()
                assert page.evaluate("__wholeEdit.state.activeReferenceId")==primary["frames"][0]["id"]
                page.locator("#human-pose-hide-all").click()
                assert pose_patches(page)==patches
                assert not page.locator("#human-pose-overlay").evaluate("el=>el.classList.contains('pose-editing')")
                page.locator("#human-pose-latest").click()
                open_editor(page)
                note_input=page.locator("#feedback-note")
                note_input.fill(""); note_input.press_sequentially("@关键点修改1")
                page.wait_for_function("document.getElementById('prompt-mentions').offsetWidth>0")
                assert page.locator(".prompt-mention-option").filter(has_text="关键点修改1").count()==1
                note_input.press("Enter")
                assert page.evaluate("__wholeEdit.promptText()").count("[[pose_edit:")==1
                close_chat(page)
                print("PASS: zoom/pan coordinates, feet/face/both hands, low-confidence and missing edits, compact/@ references, reset/remove/undo and overlay hiding keep one exact-frame draft",flush=True)

                with page.expect_download() as downloading: page.locator("#pose-edit-download").click()
                download=downloading.value; file=root/"correction.json"; download.save_as(file)
                correction=json.loads(file.read_text()); source=correction["frames"][0]
                assert len(source["original_keypoints"])==133 and len(source["effective_keypoints"])==133
                assert source["camera"]==calibration and source["image_sha256"]==patches[0]["image_sha256"]
                assert source["ref_id"]==primary["frames"][0]["id"] and source["view_id"]==primary["clip_id"]
                assert source["frame_index"]==0 and source["time_seconds"]==0
                assert correction["source_snapshot_id"]==export["manifest"]["source_snapshot_id"]
                index=correction["keypoint_names"].index("left_thumb4")
                assert source["effective_keypoints"][index]["score"]==.15
                assert source["effective_keypoints"][index]["manual_source"]=="manual_2d"
                assert original_path.read_bytes()==original_bytes
                print("PASS: source-bound JSON preserves original scores, parent 133 points, hashes and exact camera separately from edits",flush=True)

                viewport=page.locator("#viewport canvas").bounding_box(); assert viewport
                page.mouse.move(viewport["x"]+viewport["width"]*.5,viewport["y"]+viewport["height"]*.45); page.mouse.down()
                page.mouse.move(viewport["x"]+viewport["width"]*.65,viewport["y"]+viewport["height"]*.6,steps=8); page.mouse.up()
                page.locator('#reference-strip button[data-view-id="' + side["clip_id"] + '"]').first.click(); wait_frame(page,side["frames"][0]["id"])
                assert page.evaluate("structuredClone(__wholeEdit.state.poseEdits)")==patches
                assert not page.locator("#human-pose-overlay").evaluate("el=>el.classList.contains('pose-editing')")
                page.locator('#reference-strip button[data-view-id="' + primary["clip_id"] + '"]').first.click(); wait_frame(page,primary["frames"][0]["id"])
                page.reload(); ready(page); wait_frame(page,primary["frames"][0]["id"])
                assert page.evaluate("structuredClone(__wholeEdit.state.poseEdits)")==patches
                assert page.locator("#pose-edit-panel").is_visible()
                assert page.evaluate("structuredClone(__wholeEdit.state.annotations)")==marks
                print("PASS: orbit, camera switch and reload preserve exact source-frame corrections and original annotations",flush=True)

                note=page.locator("#feedback-note").input_value(); rejected=[]
                def reject(route):
                    rejected.append(route.request.post_data_json); route.fulfill(status=422,content_type="application/json",body='{"error":"synthetic rejection"}')
                open_chat(page)
                page.route("**/api/sessions/*/feedback",reject); page.locator("#submit-button").click()
                page.wait_for_function("!__wholeEdit.state.submitting && !__wholeEdit.state.pendingSubmission")
                assert rejected and page.locator("#feedback-note").input_value()==note
                assert page.evaluate("structuredClone(__wholeEdit.state.poseEdits)")==patches
                page.unroute("**/api/sessions/*/feedback",reject)
                print("PASS: rejected save retains manual edits and the single freeform prompt",flush=True)

                def old_server(route):
                    response=route.fetch(); body=response.json(); body.pop("pose_corrections_supported",None)
                    route.fulfill(response=response,json=body)
                blocked=[]
                def watch(request):
                    if request.method=="POST" and request.url.endswith("/feedback"): blocked.append(request.url)
                page.on("request",watch); page.route("**/api/workspace/state*",old_server); page.reload(); ready(page)
                page.locator("#submit-button").click(); page.wait_for_timeout(150)
                assert not blocked and not page.evaluate("__wholeEdit.state.poseCorrectionsSupported")
                assert page.evaluate("structuredClone(__wholeEdit.state.poseEdits)")==patches
                page.unroute("**/api/workspace/state*",old_server); page.reload(); ready(page); wait_frame(page,primary["frames"][0]["id"])
                print("PASS: old servers cannot silently drop a restored manual-correction prompt",flush=True)

                page.locator("#submit-button").click(); page.wait_for_function("!__wholeEdit.state.submitting && __wholeEdit.state.poseEdits.length===0 && document.getElementById('feedback-note').value===''")
                packet=store.feedback(session)["items"][-1]; manual=packet["human_pose_edits"][0]
                assert manual["source"]=="manual_2d" and manual["frame"]["reference_id"]==primary["frames"][0]["id"]
                assert manual["frame"]["view_id"]==primary["clip_id"] and manual["frame"]["frame_index"]==0 and manual["frame"]["time_sec"]==0
                assert manual["frame"]["image_sha256"]==source["image_sha256"] and manual["frame"]["camera"]==calibration
                assert manual["reference_original_url"]==manual["frame"]["reference_url"]
                assert len(manual["effective_keypoints"])==133 and Path(manual["corrections_path"]).is_file()
                response=context.request.get(base+manual["corrections_url"],headers={"X-Workspace-Capability":page.evaluate("__wholeEdit.state.browserCapability")}); assert response.status==200,response.text()
                assert response.json()==correction
                assert (store.media_dir/manual["pose_overlay_url"].rsplit("/",1)[1]).is_file()
                assert original_path.read_bytes()==original_bytes
                page.reload(); ready(page); assert not page.evaluate("__wholeEdit.state.poseEdits.length")
                print("PASS: accepted MCP-event feedback stores real corrected overlay and separate JSON, clears draft and preserves original parent",flush=True)

                close_chat(page)
                page.locator("#timeline-seek").focus(); page.locator("#timeline-seek").press("End"); wait_frame(page,primary["frames"][1]["id"])
                open_editor(page)
                edit_point(page,"left_shoulder","body",(.24,.3),drag=False)
                edit_point(page,"right_hand_root","right",(.66,.62),drag=False)
                with page.expect_download() as downloading: page.locator("#pose-edit-download").click()
                lost_file=root/"lost-correction.json"; downloading.value.save_as(lost_file); lost=json.loads(lost_file.read_text())
                assert len(lost["frames"][0]["edits"])==2 and lost["correction_id"]!=patches[0]["id"]
                assert lost["frames"][0]["ref_id"]==primary["frames"][1]["id"]
                assert lost["frames"][0]["frame_index"]==1 and lost["frames"][0]["time_seconds"]==.5
                assert lost["frames"][0]["image_sha256"]!=source["image_sha256"]
                fresh=gateway.pose_jobs.export_sources({"session_id":session}); merged=result_fixture(fresh)
                target=next(frame for frame in merged["frames"] if frame["ref_id"]==lost["frames"][0]["ref_id"])
                target["keypoints"]=lost["frames"][0]["effective_keypoints"]
                gateway.pose_jobs.import_result({"job_id":fresh["job_id"],"result":merged})
                imported=gateway.pose_jobs.get(fresh["job_id"],reference_id=target["ref_id"])["frames"][0]
                assert imported["bbox"] is None and imported["tracking_status"]=="lost"
                manual_point=next(point for point in imported["keypoints"] if point["name"]=="right_hand_root")
                assert manual_point["score"]==0 and manual_point["in_frame"] and manual_point["manual_source"]=="manual_2d"
                body_point=next(point for point in imported["keypoints"] if point["name"]=="left_shoulder")
                assert body_point["score"]==0 and body_point["in_frame"] and body_point["manual_source"]=="manual_2d"
                lost_patches=pose_patches(page)
                page.locator('#reference-strip button[data-view-id="' + side["clip_id"] + '"]').first.click(); wait_frame(page,side["frames"][1]["id"])
                assert pose_patches(page)==lost_patches
                assert not page.locator("#human-pose-overlay").evaluate("el=>el.classList.contains('pose-editing')")
                print("PASS: a new detector-lost frame accepts explicit body/hand placement without fabricated score/bbox or cross-view source rebinding",flush=True)
                assert not errors,errors
                browser.close()
        finally:
            server.shutdown(); worker.join(timeout=2); server.server_close()


if __name__ == "__main__": main()

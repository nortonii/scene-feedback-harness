#!/usr/bin/env python3
"""Exercise actual WholeBody hand pointer edits in an isolated workbench.

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
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]
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


def edit_point(page,name,side,target,drag=True):
    page.locator("#pose-edit-hand").select_option(side)
    page.locator("#pose-edit-joint").select_option(name)
    page.locator("#pose-edit-visibility").select_option("visible")
    bounds=page.locator("#human-pose-overlay").bounding_box(); assert bounds
    original=page.evaluate("(name)=>__wholeEdit.poseEditorContext().frame.keypoints.find(p=>p.name===name)",name)
    if drag and original["in_frame"]:
        page.mouse.move(bounds["x"]+original["x"]*bounds["width"],bounds["y"]+original["y"]*bounds["height"])
        page.mouse.down()
        page.mouse.move(bounds["x"]+target[0]*bounds["width"],bounds["y"]+target[1]*bounds["height"],steps=8)
        page.mouse.up()
    else:
        page.mouse.click(bounds["x"]+target[0]*bounds["width"],bounds["y"]+target[1]*bounds["height"])
    page.wait_for_function("(name)=>__wholeEdit.state.poseEdits.some(e=>e.edits.some(p=>p.name===name&&p.visibility==='visible'))",arg=name)
    patch=page.evaluate("(name)=>__wholeEdit.poseEditorContext().sample.edits.find(p=>p.name===name)",name)
    assert abs(patch["x"]-target[0])<.004 and abs(patch["y"]-target[1])<.004,patch


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
            {"name":f"A{i}.png","time_sec":i*.5,"data_url":image_data("#d5c7ad"),"camera":calibration} for i in range(2)]})["reference_clip"]
        clip=store.set_reference_clip(session,{"append_view":True,"name":"side","fps":2,"frames":[
            {"name":f"B{i}.png","time_sec":i*.5,"data_url":image_data("#d2c9bf"),"camera":calibration} for i in range(2)]})["reference_clip"]
        side=clip["views"][0]
        export=gateway.pose_jobs.export_sources({"session_id":session}); original=result_fixture(export)
        gateway.pose_jobs.import_result({"job_id":export["job_id"],"result":original})
        original_path=store.data_dir/"human_pose"/export["job_id"]/"output.json"; original_bytes=original_path.read_bytes()
        base=f"http://127.0.0.1:{server.server_port}/p/{workspace['project_id']}"
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(**({"executable_path":args.browser_executable} if args.browser_executable else {}),headless=True,
                    args=["--no-sandbox","--use-gl=angle","--use-angle=swiftshader","--enable-unsafe-swiftshader"])
                context=browser.new_context(viewport={"width":1440,"height":950},accept_downloads=True)
                hook="\nwindow.__wholeEdit={state,ui,humanCurrentFrame,humanOverlayJob,drawHumanPoseOverlay,poseEditorContext,downloadPoseEdit};"
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
                page.locator("#references-dialog-button").click(); page.locator(".human-pose-edit-action").click()
                assert page.locator("#pose-edit-panel").is_visible()
                assert page.locator("#pose-edit-joint option").count()==21
                page.locator("#pose-edit-hand").select_option("right"); assert page.locator("#pose-edit-joint option").count()==21
                print("PASS: actual named 133-point topology renders both 21-joint hands; lightweight editor distinguishes sides",flush=True)

                edit_point(page,"left_thumb4","left",(.32,.36))
                assert "低置信" in page.locator("#pose-edit-hint").inner_text()
                edit_point(page,"left_pinky_finger4","left",(.17,.59),drag=False)
                edit_point(page,"right_forefinger4","right",(.74,.39))
                page.locator("#pose-edit-joint").select_option("right_pinky_finger4"); page.locator("#pose-edit-visibility").select_option("missing")
                page.locator("#pose-edit-joint").select_option("right_ring_finger4"); page.locator("#pose-edit-visibility").select_option("occluded")
                page.locator("#pose-edit-joint").select_option("right_thumb4"); page.locator("#pose-edit-visibility").select_option("missing")
                patches=page.evaluate("structuredClone(__wholeEdit.state.poseEdits)")
                assert len(patches)==1 and len(patches[0]["edits"])==6,patches
                for patch in patches[0]["edits"]:
                    if patch["visibility"] != "visible": assert "x" not in patch and "y" not in patch,patch
                assert "[[pose_edit:" in page.locator("#feedback-note").input_value()
                print("PASS: real pointer drag/placement corrects low-confidence and missing joints, both sides, without inventing occluded positions",flush=True)

                with page.expect_download() as downloading: page.locator("#pose-edit-download").click()
                download=downloading.value; file=root/"correction.json"; download.save_as(file)
                correction=json.loads(file.read_text()); source=correction["frames"][0]
                assert len(source["original_keypoints"])==133 and len(source["effective_keypoints"])==133
                assert source["camera"]==calibration and source["image_sha256"]==patches[0]["image_sha256"]
                index=correction["keypoint_names"].index("left_thumb4")
                assert source["effective_keypoints"][index]["score"]==.15
                assert source["effective_keypoints"][index]["manual_source"]=="manual_2d"
                assert original_path.read_bytes()==original_bytes
                print("PASS: source-bound JSON preserves original scores, parent 133 points, hashes and exact camera separately from edits",flush=True)

                viewport=page.locator("#viewport canvas").bounding_box(); assert viewport
                page.mouse.move(viewport["x"]+viewport["width"]*.5,viewport["y"]+viewport["height"]*.45); page.mouse.down()
                page.mouse.move(viewport["x"]+viewport["width"]*.65,viewport["y"]+viewport["height"]*.6,steps=8); page.mouse.up()
                page.locator("#reference-view-select").select_option(side["clip_id"]); wait_frame(page,side["frames"][0]["id"])
                assert page.evaluate("structuredClone(__wholeEdit.state.poseEdits)")==patches
                assert not page.locator("#human-pose-overlay").evaluate("el=>el.classList.contains('pose-editing')")
                page.locator("#reference-view-select").select_option(primary["clip_id"]); wait_frame(page,primary["frames"][0]["id"])
                page.locator("#reference-zoom-in").click()
                assert page.evaluate("structuredClone(__wholeEdit.state.poseEdits)")==patches
                page.reload(); ready(page); wait_frame(page,primary["frames"][0]["id"])
                assert page.evaluate("structuredClone(__wholeEdit.state.poseEdits)")==patches
                assert page.locator("#pose-edit-panel").is_visible()
                print("PASS: orbit, camera switch, image zoom and reload preserve exact source-frame corrections",flush=True)

                note=page.locator("#feedback-note").input_value(); rejected=[]
                def reject(route):
                    rejected.append(route.request.post_data_json); route.fulfill(status=422,content_type="application/json",body='{"error":"synthetic rejection"}')
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
                assert len(manual["effective_keypoints"])==133 and Path(manual["corrections_path"]).is_file()
                response=context.request.get(base+manual["corrections_url"],headers={"X-Workspace-Capability":page.evaluate("__wholeEdit.state.browserCapability")}); assert response.status==200,response.text()
                assert response.json()==correction
                assert (store.media_dir/manual["pose_overlay_url"].rsplit("/",1)[1]).is_file()
                assert original_path.read_bytes()==original_bytes
                page.reload(); ready(page); assert not page.evaluate("__wholeEdit.state.poseEdits.length")
                print("PASS: accepted MCP-event feedback stores real corrected overlay and separate JSON, clears draft and preserves original parent",flush=True)

                page.locator("#timeline-seek").focus(); page.locator("#timeline-seek").press("End"); wait_frame(page,primary["frames"][1]["id"])
                page.locator("#references-dialog-button").click(); page.locator(".human-pose-edit-action").click()
                edit_point(page,"right_hand_root","right",(.66,.62),drag=False)
                with page.expect_download() as downloading: page.locator("#pose-edit-download").click()
                lost_file=root/"lost-correction.json"; downloading.value.save_as(lost_file); lost=json.loads(lost_file.read_text())
                fresh=gateway.pose_jobs.export_sources({"session_id":session}); merged=result_fixture(fresh)
                target=next(frame for frame in merged["frames"] if frame["ref_id"]==lost["frames"][0]["ref_id"])
                target["keypoints"]=lost["frames"][0]["effective_keypoints"]
                gateway.pose_jobs.import_result({"job_id":fresh["job_id"],"result":merged})
                imported=gateway.pose_jobs.get(fresh["job_id"],reference_id=target["ref_id"])["frames"][0]
                assert imported["bbox"] is None and imported["tracking_status"]=="lost"
                manual_point=next(point for point in imported["keypoints"] if point["name"]=="right_hand_root")
                assert manual_point["score"]==0 and manual_point["in_frame"] and manual_point["manual_source"]=="manual_2d"
                print("PASS: a detector-lost image accepts explicit hand placement and source-bound merged import without fabricated bbox or score",flush=True)
                assert not errors,errors
                browser.close()
        finally:
            server.shutdown(); worker.join(timeout=2); server.server_close()


if __name__ == "__main__": main()

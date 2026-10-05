#!/usr/bin/env python3
"""Check external pose exchange in real Chromium with a temporary workbench.

Requires the optional Playwright browser-check dependency. Uses synthetic 2D
results, starts no inference and never connects to a real Codex task.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "tests"), str(ROOT)]
from workspace_ui_helpers import choose_tool, open_annotation_tools


def image_data(color: str) -> str:
    from PIL import Image
    stream = io.BytesIO()
    Image.new("RGB", (320, 240), color).save(stream, "PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()


def result_fixture(export: dict, kind: str = "observed_2d") -> dict:
    manifest = export["manifest"]
    names = ["head", "left_shoulder", "right_shoulder", "pelvis", "left_ankle", "right_ankle"]
    view_offsets = {view: index * .02 for index, view in enumerate(manifest.get("view_ids") or [manifest["view_id"]])}
    return {
        **{key: manifest[key] for key in ("schema_version", "job_id", "track_id", "project_id", "session_id", "source_snapshot_id")},
        **({"view_ids": manifest["view_ids"]} if "view_ids" in manifest else {"view_id": manifest["view_id"]}),
        "evidence_kind": kind, "keypoint_profile": "capsule-body6-fixture",
        "keypoint_names": names, "skeleton_edges": [[0, 1], [0, 2], [1, 3], [2, 3], [3, 4], [3, 5]],
        "provenance": {"generator": "synthetic browser test; no inference", "method": "fixture"},
        "model": {"name": "synthetic capsule body points", "keypoint_format": "capsule-body6-fixture"},
        "frames": [
            {**{key: source[key] for key in ("ref_id", "view_id", "width", "height", "frame_index", "time_seconds", "image_sha256", "image_orientation")},
             "bbox": [.1, .1, .7, .8], "tracking_status": "tracked",
             "keypoints": [{"name": name, "x": .25 + i * .06 + source["frame_index"] * .01 + view_offsets[source["view_id"]],
                            "y": .2 + i * .1 + source["time_seconds"] * .03,
                            "score": .9, "in_frame": True} for i, name in enumerate(names)]}
            for source in manifest["frames"]
        ],
    }


def overlay_pixels(page) -> int:
    return page.evaluate("""() => {
      const canvas=__poseCheck.ui.humanCanvas;
      const data=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
      let painted=0; for(let i=3;i<data.length;i+=4) if(data[i]) painted++;
      return painted;
    }""")


def overlay_info(page, pending_job_id=None) -> dict:
    """Count and verify the drawn skeleton, including hidden/pending frames."""
    info = page.evaluate("""(pendingId) => {
      const p=__poseCheck, ctx=p.ui.humanCanvas.getContext('2d');
      const pending=pendingId ? p.state.humanDetails.get(pendingId) : null;
      const frames=pending?.frames;
      const editor=p.state.poseEditor;
      const originals=Object.fromEntries(['arc','moveTo','lineTo','strokeRect','fillText'].map(name=>[name,ctx[name]]));
      const points=[],bones=[];
      let from=null,rectangles=0,text=0;
      ctx.arc=function(...args) { points.push(args.slice(0,2)); return originals.arc.apply(this,args); };
      ctx.moveTo=function(...args) { from=args.slice(0,2); return originals.moveTo.apply(this,args); };
      ctx.lineTo=function(...args) { bones.push([...(from || []),...args.slice(0,2)]); return originals.lineTo.apply(this,args); };
      ctx.strokeRect=function(...args) { rectangles++; return originals.strokeRect.apply(this,args); };
      ctx.fillText=function(...args) { text++; return originals.fillText.apply(this,args); };
      try {
        if (pending) pending.frames=[];
        // Measure the automatic skeleton separately from edit-mode handles.
        p.state.poseEditor=null;
        p.drawHumanPoseOverlay();
        const job=p.humanOverlayJob(),frame=job ? p.humanCurrentFrame(job) : null;
        const width=p.ui.humanCanvas.clientWidth,height=p.ui.humanCanvas.clientHeight;
        const expectedPoints=(frame?.keypoints || []).map(point=>[point.x*width,point.y*height]);
        const expectedBones=frame ? p.state.humanDetails.get(job.job_id).skeleton_edges.map(([a,b])=>
          [...expectedPoints[a],...expectedPoints[b]]) : [];
        const same=(actual,expected)=>actual.length===expected.length && actual.every((row,index)=>
          row.length===expected[index].length && row.every((value,column)=>Math.abs(value-expected[index][column])<1e-7));
        const pixels=ctx.getImageData(0,0,p.ui.humanCanvas.width,p.ui.humanCanvas.height).data;
        let painted=0; for(let index=3;index<pixels.length;index+=4) if(pixels[index]) painted++;
        return {points:points.length,bones:bones.length,rectangles,text,painted,
          matches_frame:same(points,expectedPoints) && same(bones,expectedBones),
          job_id:job?.job_id || null,choice:p.state.humanOverlayChoice};
      } finally {
        for (const [name,original] of Object.entries(originals)) ctx[name]=original;
        if (pending) pending.frames=frames;
        p.state.poseEditor=editor;
        if (!pending) p.drawHumanPoseOverlay();
      }
    }""", pending_job_id)
    assert info.pop("rectangles") == 0, info
    assert info.pop("text") == 0, info
    assert info.pop("matches_frame"), info
    painted=info.pop("painted")
    if pending_job_id or info["choice"] == "hidden":
        assert painted == 0, {"overlay":info,"painted":painted}
    return info


def open_results(page):
    if page.locator("#chat-launcher").is_visible():
        page.locator("#chat-launcher").click()
    if not page.locator("#references-dialog").is_visible():
        page.locator("#references-dialog-button").click()
    page.wait_for_function("document.getElementById('references-dialog').open")


def close_results(page):
    if page.locator("#references-dialog").is_visible():
        page.locator('[data-close-dialog="references-dialog"]').click()
    if page.locator("#chat-dock").is_visible():
        page.locator("#chat-collapse").click()


def open_pose_editor(page):
    if page.locator("#pose-edit-panel").is_visible():
        return
    open_annotation_tools(page, "reference")
    page.locator("#pose-edit-tool").click()
    page.wait_for_function("!document.getElementById('pose-edit-panel').classList.contains('hidden')")


def drag_current_joint(page, name: str):
    """Move one visible source joint through the actual reference-image canvas."""
    page.locator("#pose-edit-joint").select_option(name)
    position = page.evaluate("""(name) => {
      const p=__poseCheck, editor=p.state.poseEditor;
      const frame=p.humanCurrentFrame(p.state.humanJobs.find(job=>job.job_id===editor.jobId));
      const joint=frame.keypoints.find(point=>point.name===name);
      const rect=p.ui.humanCanvas.getBoundingClientRect();
      return {x:rect.x+joint.x*rect.width,y:rect.y+joint.y*rect.height,width:rect.width,height:rect.height};
    }""", name)
    page.mouse.move(position["x"], position["y"])
    page.mouse.down()
    page.mouse.move(position["x"] + position["width"] * .08, position["y"] + position["height"] * .05, steps=5)
    page.mouse.up()


def import_json(context, base, headers, job_id, result):
    # Send the actual JSON file representation. Passing a dictionary through
    # Playwright's JavaScript API can round Python float values before transport.
    return context.request.post(base + "/api/workspace/pose/import",
        headers={**headers, "Content-Type": "application/json"},
        data=json.dumps({"job_id": job_id, "result": result}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-executable", help="Path to an existing Chromium binary")
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright
    from server import make_server

    errors, requests = [], []
    with tempfile.TemporaryDirectory(prefix="pose-results-browser-") as temporary:
        root = Path(temporary)
        project = root / "project"
        project.mkdir()
        server = make_server(port=0, data_dir=root / "data", project_dir=project,
                             web_dir=ROOT / "web", external_review=True, feedback_transport="mcp_events")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        store = server.scene_store
        workspace = server.workspace_gateway.ensure()
        session = workspace["session_id"]
        primary = store.set_reference_clip(session, {"name": "camera_A", "fps": 2, "frames": [
            {"name": f"A_{index}.png", "time_sec": time, "data_url": image_data("navy")}
            for index, time in enumerate((0, .5))]})["reference_clip"]
        clip = store.set_reference_clip(session, {"append_view": True, "name": "camera_B", "fps": 4,
            "frames": [{"name": f"B_{index}.png", "time_sec": time, "data_url": image_data("maroon")}
                       for index, time in enumerate((0, .25, .5))]})["reference_clip"]
        secondary = clip["views"][0]
        base = f"http://127.0.0.1:{server.server_port}/p/{workspace['project_id']}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    **({"executable_path": args.browser_executable} if args.browser_executable else {}),
                    headless=True, args=["--no-sandbox", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
                context = browser.new_context(viewport={"width": 1440, "height": 950}, accept_downloads=True)
                context.route("**/app.js", lambda route: route.fulfill(status=200,
                    content_type="application/javascript", body=(ROOT / "web/app.js").read_text() +
                    "\nwindow.__poseCheck={state,ui,promptText,humanCurrentFrame,humanJobName,humanOverlayJob,drawHumanPoseOverlay,loadHumanPoses};"))
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: requests.append((request.method, request.url)))
                page.goto(base + "/")
                page.wait_for_function("window.__poseCheck && __poseCheck.state.workspaceReady && !__poseCheck.state.sceneLoading && document.getElementById('reference-image').naturalWidth > 0")
                assert page.locator(".pane-head #human-pose-panel").count() == 0
                assert page.locator("#references-dialog #human-pose-panel").count() == 1
                open_annotation_tools(page, "reference")
                assert page.locator("#pose-edit-tool").is_disabled()
                for control in ("human-pose-run", "human-pose-runtime", "human-pose-progress", "human-pose-draw", "human-pose-fps", "human-pose-import",
                                "freeze-button", "browse-button", "snapshot-button"):
                    assert page.locator("#" + control).count() == 0, control
                assert page.locator(".view-popover").count() == 0
                page.locator("#references-dialog-button").click()
                assert not page.locator("#human-pose-panel").is_visible(), "No result controls appear before a skill imports evidence"
                close_results(page)
                capability = page.evaluate("__poseCheck.state.browserCapability")
                headers = {"X-Workspace-Capability": capability, "X-Scene-Harness-Key": store.control_token}
                response = context.request.post(base + "/api/workspace/pose/sources", headers=headers, data={"session_id": session})
                assert response.status == 201, response.text()
                export = response.json()
                observed = result_fixture(export)
                job_id = export["job_id"]
                response = context.request.post(base + "/api/workspace/pose/import",
                    headers={**headers, "Content-Type": "application/json"}, data="not json")
                assert response.status == 400, response.text()
                response = import_json(context, base, headers, job_id, observed)
                assert response.status == 200, response.text()
                page.wait_for_function("(id) => __poseCheck.state.humanJobs.some(j=>j.job_id===id && j.status==='completed')", arg=job_id)
                page.wait_for_function("(id) => __poseCheck.humanCurrentFrame(__poseCheck.state.humanJobs.find(j=>j.job_id===id)) !== null", arg=job_id)
                assert overlay_pixels(page) > 300
                assert overlay_info(page) == {"points": 6, "bones": 6, "job_id": job_id, "choice": "latest"}
                open_annotation_tools(page, "reference")
                assert page.locator("#pose-edit-tool").is_enabled()
                open_results(page)
                assert not page.locator("#human-pose-panel").is_visible(), "The references drawer only lists actual edits"
                close_results(page)
                open_pose_editor(page)
                assert page.locator("#pose-edit-result").input_value() == job_id
                assert page.locator("#pose-edit-hand option[value='body']").count() == 1
                page.locator("#human-pose-latest").click()
                page.locator("#pose-edit-finish").click()
                print("PASS: skill/API import shows custom-profile skeleton and exposes editing on the reference image without a result batch list", flush=True)

                # A second tracking run observes the same person. It must
                # replace the displayed overlay rather than add a second body.
                close_results(page)
                newer_export_response = context.request.post(base + "/api/workspace/pose/sources", headers=headers, data={"session_id": session})
                assert newer_export_response.status == 201, newer_export_response.text()
                newer = result_fixture(newer_export_response.json())
                for frame in newer["frames"]:
                    frame["bbox"] = [.2, .12, .65, .8]
                    for point in frame["keypoints"]:
                        point["x"] += .04
                response = import_json(context, base, headers, newer["job_id"], newer)
                assert response.status == 200, response.text()
                page.wait_for_function("(id)=>{const p=__poseCheck,j=p.state.humanJobs.find(j=>j.job_id===id); return j && p.humanCurrentFrame(j)}", arg=newer["job_id"])
                assert overlay_info(page) == {"points": 6, "bones": 6, "job_id": newer["job_id"], "choice": "latest"}
                open_pose_editor(page)
                page.locator("#pose-edit-result").select_option(job_id)
                assert overlay_info(page) == {"points": 6, "bones": 6, "job_id": job_id, "choice": job_id}

                projected_export_response = context.request.post(base + "/api/workspace/pose/sources", headers=headers, data={"session_id": session})
                assert projected_export_response.status == 201, projected_export_response.text()
                projected_export = projected_export_response.json()
                projected = result_fixture(projected_export, "projected_3d")
                response = import_json(context, base, headers, projected["job_id"], projected)
                assert response.status == 200, response.text()
                page.wait_for_function("(id)=>__poseCheck.state.humanJobs.some(j=>j.job_id===id && j.status==='completed')", arg=projected["job_id"], timeout=15000)
                assert not page.locator("#references-dialog").is_visible()
                page.wait_for_function("(id)=>Array.from(document.querySelectorAll('#pose-edit-result option')).some(option=>option.value===id && option.textContent.includes('三维投影参考'))", arg=projected["job_id"])
                assert overlay_info(page) == {"points": 6, "bones": 6, "job_id": job_id, "choice": job_id}, "A newly imported result must not steal an explicit historical selection"
                print("PASS: externally imported result appears while panel is closed; projections are labeled separately", flush=True)

                page.reload()
                page.wait_for_function("(id)=>{const p=window.__poseCheck,j=p?.state.humanJobs.find(j=>j.job_id===id); return p?.state.workspaceReady && j && p.humanCurrentFrame(j) && document.getElementById('reference-image').naturalWidth>0}", arg=job_id)
                assert overlay_info(page) == {"points": 6, "bones": 6, "job_id": job_id, "choice": job_id}
                page.evaluate("__poseCheck.loadHumanPoses()")
                assert overlay_info(page)["job_id"] == job_id
                open_pose_editor(page)
                page.locator("#human-pose-hide-all").click()
                page.wait_for_function("""() => {
                  const p=__poseCheck,c=p.ui.humanCanvas;
                  if(p.state.humanOverlayChoice!=='hidden') return false;
                  const pixels=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
                  for(let index=3;index<pixels.length;index+=4) if(pixels[index]) return false;
                  return true;
                }""")
                hidden_info,hidden_pixels=overlay_info(page),overlay_pixels(page)
                assert hidden_info["points"] == hidden_info["bones"] == 0 and hidden_pixels == 0, {"overlay":hidden_info,"pixels":hidden_pixels,
                    "size":page.evaluate("({width:__poseCheck.ui.humanCanvas.clientWidth,height:__poseCheck.ui.humanCanvas.clientHeight})"),
                    "after":page.evaluate("({choice:__poseCheck.state.humanOverlayChoice,editor:__poseCheck.state.poseEditor,canvas:__poseCheck.ui.humanCanvas===document.getElementById('human-pose-overlay')})")}
                assert not page.locator("#human-pose-overlay").evaluate("el=>el.classList.contains('pose-editing')"), "Hidden skeleton must release pointer events"
                page.reload()
                page.wait_for_function("window.__poseCheck && __poseCheck.state.workspaceReady && __poseCheck.state.humanJobs.length===3")
                assert overlay_info(page) == {"points": 0, "bones": 0, "job_id": None, "choice": "hidden"}
                open_pose_editor(page)
                page.locator("#human-pose-latest").click()
                page.wait_for_function("(id)=>__poseCheck.humanCurrentFrame(__poseCheck.state.humanJobs.find(j=>j.job_id===id)) !== null", arg=newer["job_id"])
                assert overlay_info(page) == {"points": 6, "bones": 6, "job_id": newer["job_id"], "choice": newer["job_id"]}, "Latest source selection prefers independent 2D evidence and pins it for editing"
                print("PASS: overlapping tracking runs paint one six-point skeleton without boxes or text; explicit history and hiding survive polling/reload; latest mode prefers 2D evidence", flush=True)

                # A newly selected frame may still be loading. A cached body
                # from another frame, or an older tracking run, must stay out.
                delayed = overlay_info(page, pending_job_id=newer["job_id"])
                assert delayed == {"points": 0, "bones": 0, "job_id": newer["job_id"], "choice": newer["job_id"]}
                # The empty-frame draw and its pixels were measured atomically
                # before the fixture restored the ready source frame.
                overlay_info(page)
                print("PASS: pending frame evidence leaves an empty overlay instead of a stale or historical body", flush=True)

                close_results(page)
                page.locator('#reference-strip button[data-view-id="' + secondary["clip_id"] + '"]').first.click()
                page.locator("#timeline-seek").focus()
                page.locator("#timeline-seek").press("End")
                expected_b = secondary["frames"][-1]["id"]
                page.wait_for_function("(x)=>{const s=__poseCheck.state,j=s.humanJobs.find(j=>j.job_id===x.job); return !s.seeking && s.activeViewId===x.view && __poseCheck.humanCurrentFrame(j)?.reference_id===x.ref}",
                    arg={"job": job_id, "view": secondary["clip_id"], "ref": expected_b})
                assert overlay_pixels(page) > 300
                assert overlay_info(page) == {"points": 6, "bones": 6, "job_id": newer["job_id"], "choice": newer["job_id"]}
                page.locator("#pose-edit-finish").click()
                open_pose_editor(page)
                page.locator("#pose-edit-result").select_option(newer["job_id"])
                page.locator("#pose-edit-hand").select_option("body")
                drag_current_joint(page, "head")
                edit = page.evaluate("""() => {
                  const p=__poseCheck, sample=p.state.poseEdits[0];
                  return {count:p.state.poseEdits.length, id:sample?.id, job_id:sample?.job_id,
                    reference_id:sample?.reference_id, edits:sample?.edits, note:p.promptText()};
                }""")
                assert edit["count"] == 1 and edit["job_id"] == newer["job_id"] and edit["reference_id"] == expected_b, edit
                assert len(edit["edits"]) == 1 and edit["edits"][0]["name"] == "head", edit
                assert f"[[pose_edit:{edit['id']}]]" in edit["note"], edit
                open_results(page)
                assert page.locator("#human-pose-jobs .human-pose-edit-draft").count() == 1
                close_results(page)
                page.locator("#pose-edit-finish").click()
                page.locator('#reference-strip button[data-view-id="' + primary["clip_id"] + '"]').first.click()
                expected_a = primary["frames"][-1]["id"]
                page.wait_for_function("(x)=>{const s=__poseCheck.state,j=s.humanJobs.find(j=>j.job_id===x.job); return !s.seeking && s.activeViewId===x.view && __poseCheck.humanCurrentFrame(j)?.reference_id===x.ref}",
                    arg={"job": job_id, "view": primary["clip_id"], "ref": expected_a})
                assert overlay_pixels(page) > 300
                assert overlay_info(page) == {"points": 6, "bones": 6, "job_id": newer["job_id"], "choice": "latest"}
                download_response = context.request.get(base + "/api/workspace/pose/" + job_id + "?download=1", headers=headers)
                assert download_response.status == 200, download_response.text()
                downloaded_path = root / "download.json"
                downloaded_path.write_text(download_response.text())
                downloaded = json.loads(downloaded_path.read_text())
                assert len(downloaded["frames"]) == 5 and downloaded["skeleton_edges"] == observed["skeleton_edges"]
                assert downloaded["keypoint_names"] == observed["keypoint_names"]
                assert downloaded == observed
                assert json.dumps(downloaded, sort_keys=True) == json.dumps(observed, sort_keys=True), "Download must preserve source JSON numeric representations for idempotent skill import"
                response = import_json(context, base, headers, job_id, downloaded)
                assert response.status == 200, response.text()
                modified = json.loads(downloaded_path.read_text())
                modified["frames"][0]["keypoints"][0]["x"] = .123
                response = import_json(context, base, headers, job_id, modified)
                assert response.status == 409, response.text()
                print("PASS: camera switching/scrubbing retain exact frames; body-point drag creates one cited source-bound draft; JSON reimports idempotently", flush=True)

                page.reload()
                page.wait_for_function("window.__poseCheck && __poseCheck.state.workspaceReady && !__poseCheck.state.sceneLoading")
                assert page.evaluate("__poseCheck.state.poseEdits.length") == 1
                assert f"[[pose_edit:{edit['id']}]]" in page.evaluate("__poseCheck.promptText()")
                assert page.evaluate("!Object.hasOwn(__poseCheck.state,'humanPendingRequest')")
                choose_tool(page, "arrow", "reference")
                bounds = page.locator("#reference-annotations").bounding_box()
                page.mouse.move(bounds["x"] + bounds["width"] * .2, bounds["y"] + bounds["height"] * .2)
                page.mouse.down()
                page.mouse.move(bounds["x"] + bounds["width"] * .4, bounds["y"] + bounds["height"] * .5, steps=5)
                page.mouse.up()
                page.wait_for_function("__poseCheck.state.annotations.length === 1")
                if page.locator("#chat-launcher").is_visible():
                    page.locator("#chat-launcher").click()
                page.locator("#submit-button").click()
                page.wait_for_function("document.getElementById('feedback-note').value === ''", timeout=20000)
                packets = store.state["feedback"]
                packet = list(packets.values())[-1] if isinstance(packets, dict) else packets[-1]
                assert len(packet["human_pose_edits"]) == 1
                correction = packet["human_pose_edits"][0]
                assert correction["frame"]["reference_id"] == expected_b
                assert correction["document"]["frames"][0]["original_keypoints"] == newer["frames"][-1]["keypoints"]
                assert correction["document"]["frames"][0]["effective_keypoints"][0]["x"] == edit["edits"][0]["x"]
                assert (store.media_dir / correction["pose_overlay_url"].rsplit("/", 1)[-1]).is_file()
                assert (store.media_dir / correction["reference_original_url"].rsplit("/", 1)[-1]).is_file()
                assert len(packet["annotations"]) == 1 and packet["dynamic_frames"]
                assert not errors, errors
                assert not any(method == "POST" and (url.endswith("/api/workspace/pose") or url.endswith("/cancel")) for method, url in requests), requests
                print("PASS: reload keeps the edit citation; feedback preserves original and moved source-frame points, PNG and annotation evidence", flush=True)
                for width in (390, 340):
                    page.set_viewport_size({"width": width, "height": 920})
                    open_pose_editor(page)
                    for selector in ("#pose-edit-panel", "#human-pose-latest", "#human-pose-hide-all"):
                        bounds = page.locator(selector).bounding_box()
                        assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width + 1, (width, selector, bounds)
                    page.locator("#human-pose-hide-all").click()
                    assert overlay_info(page)["points"] == 0
                    page.locator("#human-pose-latest").click()
                    assert overlay_info(page)["points"] == 6
                    page.locator("#pose-edit-finish").click()
                assert not errors, errors
                print("PASS: latest/hide controls remain accessible at 390 and 340 pixels", flush=True)
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
    print("ALL EXTERNAL POSE BROWSER CHECKS PASSED", flush=True)


if __name__ == "__main__":
    main()

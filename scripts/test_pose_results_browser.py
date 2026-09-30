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
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]


def image_data(color: str) -> str:
    from PIL import Image
    stream = io.BytesIO()
    Image.new("RGB", (320, 240), color).save(stream, "PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()


def result_fixture(export: dict, kind: str = "observed_2d") -> dict:
    manifest = export["manifest"]
    names = ["head", "left_shoulder", "right_shoulder", "pelvis", "left_ankle", "right_ankle"]
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
             "keypoints": [{"name": name, "x": .25 + i * .06, "y": .2 + i * .1,
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


def overlay_info(page) -> dict:
    """Count boxes actually painted, including simultaneous historic results."""
    return page.evaluate("""() => {
      const p=__poseCheck, ctx=p.ui.humanCanvas.getContext('2d');
      const strokeRect=ctx.strokeRect;
      let boxes=0;
      ctx.strokeRect=function(...args) { boxes++; return strokeRect.apply(this,args); };
      try { p.drawHumanPoseOverlay(); } finally { ctx.strokeRect=strokeRect; }
      return {boxes,job_id:p.humanOverlayJob()?.job_id || null,choice:p.state.humanOverlayChoice};
    }""")


def open_results(page):
    if page.locator("#chat-launcher").is_visible():
        page.locator("#chat-launcher").click()
    if not page.locator("#references-dialog").is_visible():
        page.locator("#references-dialog-button").click()
    page.wait_for_function("document.getElementById('references-dialog').open && !document.getElementById('human-pose-panel').classList.contains('hidden')")


def close_results(page):
    if page.locator("#references-dialog").is_visible():
        page.locator('[data-close-dialog="references-dialog"]').click()


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
                    "\nwindow.__poseCheck={state,ui,humanCurrentFrame,humanJobName,humanOverlayJob,drawHumanPoseOverlay,loadHumanPoses};"))
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: requests.append((request.method, request.url)))
                page.goto(base + "/")
                page.wait_for_function("window.__poseCheck && __poseCheck.state.workspaceReady && !__poseCheck.state.sceneLoading && document.getElementById('reference-image').naturalWidth > 0")
                assert page.locator(".pane-head #human-pose-panel").count() == 0
                assert page.locator("#references-dialog #human-pose-panel").count() == 1
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
                assert overlay_info(page)["boxes"] == 1
                open_results(page)
                assert "二维观测 · capsule-body6-fixture" in page.locator("#human-pose-jobs").inner_text()
                print("PASS: skill/API import exposes custom-profile results only inside the shared references dialog; clean headers contain no inference or upload controls", flush=True)

                # A second tracking run observes the same person. It must
                # replace the displayed overlay rather than add a second body.
                close_results(page)
                newer_export_response = context.request.post(base + "/api/workspace/pose/sources", headers=headers, data={"session_id": session})
                assert newer_export_response.status == 201, newer_export_response.text()
                newer = result_fixture(newer_export_response.json())
                for frame in newer["frames"]:
                    frame["bbox"] = [.2, .12, .65, .8]
                response = import_json(context, base, headers, newer["job_id"], newer)
                assert response.status == 200, response.text()
                page.wait_for_function("(id)=>{const p=__poseCheck,j=p.state.humanJobs.find(j=>j.job_id===id); return j && p.humanCurrentFrame(j)}", arg=newer["job_id"])
                assert overlay_info(page) == {"boxes": 1, "job_id": newer["job_id"], "choice": "latest"}
                open_results(page)
                job_name = page.evaluate("(id)=>__poseCheck.humanJobName(__poseCheck.state.humanJobs.find(j=>j.job_id===id))", job_id)
                row = page.locator(".human-pose-job").filter(has=page.locator("strong", has_text=job_name))
                row.get_by_role("button", name="显示", exact=True).click()
                assert overlay_info(page) == {"boxes": 1, "job_id": job_id, "choice": job_id}

                close_results(page)
                projected_export_response = context.request.post(base + "/api/workspace/pose/sources", headers=headers, data={"session_id": session})
                assert projected_export_response.status == 201, projected_export_response.text()
                projected_export = projected_export_response.json()
                projected = result_fixture(projected_export, "projected_3d")
                response = import_json(context, base, headers, projected["job_id"], projected)
                assert response.status == 200, response.text()
                page.wait_for_function("(id)=>__poseCheck.state.humanJobs.some(j=>j.job_id===id && j.status==='completed')", arg=projected["job_id"], timeout=15000)
                assert not page.locator("#references-dialog").is_visible()
                open_results(page)
                page.wait_for_function("document.getElementById('human-pose-jobs').textContent.includes('三维投影参考 · capsule-body6-fixture')")
                assert overlay_info(page) == {"boxes": 1, "job_id": job_id, "choice": job_id}, "A newly imported result must not steal an explicit historical selection"
                print("PASS: externally imported result appears while panel is closed; projections are labeled separately", flush=True)

                page.reload()
                page.wait_for_function("(id)=>{const p=window.__poseCheck,j=p?.state.humanJobs.find(j=>j.job_id===id); return p?.state.workspaceReady && j && p.humanCurrentFrame(j) && document.getElementById('reference-image').naturalWidth>0}", arg=job_id)
                assert overlay_info(page) == {"boxes": 1, "job_id": job_id, "choice": job_id}
                page.evaluate("__poseCheck.loadHumanPoses()")
                assert overlay_info(page)["job_id"] == job_id
                open_results(page)
                page.locator("#human-pose-panel").get_by_role("button", name="隐藏全部", exact=True).click()
                assert overlay_info(page)["boxes"] == 0 and overlay_pixels(page) == 0
                page.reload()
                page.wait_for_function("window.__poseCheck && __poseCheck.state.workspaceReady && __poseCheck.state.humanJobs.length===3")
                assert overlay_info(page) == {"boxes": 0, "job_id": None, "choice": "hidden"}
                open_results(page)
                page.locator("#human-pose-panel").get_by_role("button", name="最新结果", exact=True).click()
                page.wait_for_function("(id)=>__poseCheck.humanCurrentFrame(__poseCheck.state.humanJobs.find(j=>j.job_id===id)) !== null", arg=newer["job_id"])
                assert overlay_info(page) == {"boxes": 1, "job_id": newer["job_id"], "choice": "latest"}, "Latest mode prefers independent 2D evidence over a newer 3D projection"
                print("PASS: overlapping tracking runs paint one box/body; explicit history and hiding survive polling/reload; latest mode prefers 2D evidence", flush=True)

                # A newly selected frame may still be loading. A cached body
                # from another frame, or an older tracking run, must stay out.
                delayed = page.evaluate("""(id)=>{
                    const p=__poseCheck, detail=p.state.humanDetails.get(id), frames=detail.frames;
                    detail.frames=[];
                    const ctx=p.ui.humanCanvas.getContext('2d'), original=ctx.strokeRect;
                    let boxes=0; ctx.strokeRect=function(...args){boxes++;return original.apply(this,args);};
                    try { p.drawHumanPoseOverlay(); } finally { ctx.strokeRect=original; detail.frames=frames; }
                    return {boxes,job_id:p.humanOverlayJob()?.job_id};
                }""", newer["job_id"])
                assert delayed == {"boxes": 0, "job_id": newer["job_id"]}
                overlay_info(page)
                print("PASS: pending frame evidence leaves an empty overlay instead of a stale or historical body", flush=True)

                close_results(page)
                page.locator("#reference-view-select").select_option(secondary["clip_id"])
                page.locator("#timeline-seek").focus()
                page.locator("#timeline-seek").press("End")
                expected_b = secondary["frames"][-1]["id"]
                page.wait_for_function("(x)=>{const s=__poseCheck.state,j=s.humanJobs.find(j=>j.job_id===x.job); return !s.seeking && s.activeViewId===x.view && __poseCheck.humanCurrentFrame(j)?.reference_id===x.ref}",
                    arg={"job": job_id, "view": secondary["clip_id"], "ref": expected_b})
                assert overlay_pixels(page) > 300
                open_results(page)
                job_name = page.evaluate("(id)=>__poseCheck.humanJobName(__poseCheck.state.humanJobs.find(j=>j.job_id===id))", job_id)
                row = page.locator(".human-pose-job").filter(has=page.locator("strong", has_text=job_name))
                row.get_by_role("button", name="引用人体", exact=True).click()
                assert "camera_B · 第 3 帧 · 0.500 s" in page.locator("#feedback-note").input_value()
                page.locator("#reference-view-select").select_option(primary["clip_id"])
                expected_a = primary["frames"][-1]["id"]
                page.wait_for_function("(x)=>{const s=__poseCheck.state,j=s.humanJobs.find(j=>j.job_id===x.job); return !s.seeking && s.activeViewId===x.view && __poseCheck.humanCurrentFrame(j)?.reference_id===x.ref}",
                    arg={"job": job_id, "view": primary["clip_id"], "ref": expected_a})
                assert overlay_pixels(page) > 300
                open_results(page)
                row.get_by_role("button", name="引用人体", exact=True).click()
                assert "camera_A · 第 2 帧 · 0.500 s" in page.locator("#feedback-note").input_value()
                assert page.evaluate("__poseCheck.state.poseRefs.length") == 2
                open_results(page)
                with page.expect_download() as pending:
                    row.get_by_role("button", name="下载 JSON", exact=True).click()
                downloaded_path = root / "download.json"
                pending.value.save_as(downloaded_path)
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
                close_results(page)
                print("PASS: camera switching/scrubbing retain exact frames; complete JSON reimports idempotently and conflicting results are rejected", flush=True)

                page.reload()
                page.wait_for_function("window.__poseCheck && __poseCheck.state.workspaceReady && !__poseCheck.state.sceneLoading")
                assert page.evaluate("__poseCheck.state.poseRefs.length") == 2
                assert page.evaluate("!Object.hasOwn(__poseCheck.state,'humanPendingRequest')")
                page.locator("button[data-tool='arrow']").click()
                bounds = page.locator("#reference-annotations").bounding_box()
                page.mouse.move(bounds["x"] + bounds["width"] * .2, bounds["y"] + bounds["height"] * .2)
                page.mouse.down()
                page.mouse.move(bounds["x"] + bounds["width"] * .4, bounds["y"] + bounds["height"] * .5, steps=5)
                page.mouse.up()
                page.wait_for_function("__poseCheck.state.annotations.length === 1")
                page.locator("#submit-button").click()
                page.wait_for_function("document.getElementById('feedback-note').value === ''", timeout=20000)
                packets = store.state["feedback"]
                packet = list(packets.values())[-1] if isinstance(packets, dict) else packets[-1]
                assert len(packet["human_pose"]) == 2
                assert {sample["frame"]["reference_id"] for sample in packet["human_pose"]} == {expected_a, expected_b}
                for sample in packet["human_pose"]:
                    assert sample["skeleton_edges"] == observed["skeleton_edges"]
                    assert sample["evidence_kind"] == "observed_2d"
                    assert (store.media_dir / sample["pose_overlay_url"].rsplit("/", 1)[-1]).is_file()
                    assert (store.media_dir / sample["reference_original_url"].rsplit("/", 1)[-1]).is_file()
                assert len(packet["annotations"]) == 1 and packet["dynamic_frames"]
                assert not errors, errors
                assert not any(method == "POST" and (url.endswith("/api/workspace/pose") or url.endswith("/cancel")) for method, url in requests), requests
                print("PASS: reload keeps citations; feedback preserves two exact-frame originals, custom skeleton overlays and annotation evidence", flush=True)
                for width in (390, 340):
                    page.set_viewport_size({"width": width, "height": 920})
                    open_results(page)
                    for selector in ("#human-pose-panel", "#human-pose-latest", "#human-pose-hide-all"):
                        bounds = page.locator(selector).bounding_box()
                        assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width + 1, (width, selector, bounds)
                    page.locator("#human-pose-hide-all").click()
                    assert overlay_info(page)["boxes"] == 0
                    page.locator("#human-pose-latest").click()
                    assert overlay_info(page)["boxes"] == 1
                    close_results(page)
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

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
                    "\nwindow.__poseCheck={state,ui,humanCurrentFrame,humanJobName};"))
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: requests.append((request.method, request.url)))
                page.goto(base + "/")
                page.wait_for_function("window.__poseCheck && __poseCheck.state.workspaceReady && !__poseCheck.state.sceneLoading && document.getElementById('reference-image').naturalWidth > 0")
                assert page.locator("#human-pose-panel summary").inner_text() == "人体结果"
                for control in ("human-pose-run", "human-pose-runtime", "human-pose-progress", "human-pose-draw", "human-pose-fps"):
                    assert page.locator("#" + control).count() == 0, control
                page.locator("#human-pose-panel summary").click()
                assert "capsule" in page.locator("#human-pose-jobs").inner_text()
                capability = page.evaluate("__poseCheck.state.browserCapability")
                headers = {"X-Workspace-Capability": capability, "X-Scene-Harness-Key": store.control_token}
                response = context.request.post(base + "/api/workspace/pose/sources", headers=headers, data={"session_id": session})
                assert response.status == 201, response.text()
                export = response.json()
                observed = result_fixture(export)
                job_id = export["job_id"]
                page.locator("#human-pose-import").set_input_files({"name": "bad.json", "mimeType": "application/json", "buffer": b"not json"})
                page.wait_for_function("document.getElementById('human-pose-status').textContent.includes('无法读取 JSON')")
                with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/api/workspace/pose/import")) as pending_import:
                    page.locator("#human-pose-import").set_input_files({"name": "capsule-points.json", "mimeType": "application/json", "buffer": json.dumps(observed).encode()})
                assert pending_import.value.status == 200, pending_import.value.text()
                page.wait_for_function("(id) => __poseCheck.state.humanJobs.some(j=>j.job_id===id && j.status==='completed')", arg=job_id)
                page.wait_for_function("(id) => __poseCheck.humanCurrentFrame(__poseCheck.state.humanJobs.find(j=>j.job_id===id)) !== null", arg=job_id)
                assert overlay_pixels(page) > 300
                assert "二维观测 · capsule-body6-fixture" in page.locator("#human-pose-jobs").inner_text()
                print("PASS: lightweight result panel imports bound custom-profile JSON; no inference controls", flush=True)

                page.locator("#human-pose-panel summary").click()
                projected_export_response = context.request.post(base + "/api/workspace/pose/sources", headers=headers, data={"session_id": session})
                assert projected_export_response.status == 201, projected_export_response.text()
                projected_export = projected_export_response.json()
                projected = result_fixture(projected_export, "projected_3d")
                response = context.request.post(base + "/api/workspace/pose/import", headers=headers,
                    data={"job_id": projected["job_id"], "result": projected})
                assert response.status == 200, response.text()
                page.wait_for_function("(id)=>__poseCheck.state.humanJobs.some(j=>j.job_id===id && j.status==='completed')", arg=projected["job_id"], timeout=15000)
                assert not page.locator("#human-pose-panel").evaluate("element=>element.open")
                page.locator("#human-pose-panel summary").click()
                page.wait_for_function("document.getElementById('human-pose-jobs').textContent.includes('三维投影参考 · capsule-body6-fixture')")
                print("PASS: externally imported result appears while panel is closed; projections are labeled separately", flush=True)

                page.locator("#human-pose-panel summary").click()
                page.locator("#reference-view-select").select_option(secondary["clip_id"])
                page.locator("#timeline-seek").focus()
                page.locator("#timeline-seek").press("End")
                expected_b = secondary["frames"][-1]["id"]
                page.wait_for_function("(x)=>{const s=__poseCheck.state,j=s.humanJobs.find(j=>j.job_id===x.job); return !s.seeking && s.activeViewId===x.view && __poseCheck.humanCurrentFrame(j)?.reference_id===x.ref}",
                    arg={"job": job_id, "view": secondary["clip_id"], "ref": expected_b})
                assert overlay_pixels(page) > 300
                page.locator("#human-pose-panel summary").click()
                job_name = page.evaluate("(id)=>__poseCheck.humanJobName(__poseCheck.state.humanJobs.find(j=>j.job_id===id))", job_id)
                row = page.locator(".human-pose-job").filter(has=page.locator("strong", has_text=job_name))
                row.get_by_role("button", name="引用人体", exact=True).click()
                assert "camera_B · 第 3 帧 · 0.500 s" in page.locator("#feedback-note").input_value()
                page.locator("#reference-view-select").select_option(primary["clip_id"])
                expected_a = primary["frames"][-1]["id"]
                page.wait_for_function("(x)=>{const s=__poseCheck.state,j=s.humanJobs.find(j=>j.job_id===x.job); return !s.seeking && s.activeViewId===x.view && __poseCheck.humanCurrentFrame(j)?.reference_id===x.ref}",
                    arg={"job": job_id, "view": primary["clip_id"], "ref": expected_a})
                assert overlay_pixels(page) > 300
                page.locator("#human-pose-panel summary").click()
                row.get_by_role("button", name="引用人体", exact=True).click()
                assert "camera_A · 第 2 帧 · 0.500 s" in page.locator("#feedback-note").input_value()
                assert page.evaluate("__poseCheck.state.poseRefs.length") == 2
                page.locator("#human-pose-panel summary").click()
                with page.expect_download() as pending:
                    row.get_by_role("button", name="下载 JSON", exact=True).click()
                downloaded_path = root / "download.json"
                pending.value.save_as(downloaded_path)
                downloaded = json.loads(downloaded_path.read_text())
                assert len(downloaded["frames"]) == 5 and downloaded["skeleton_edges"] == observed["skeleton_edges"]
                assert downloaded["keypoint_names"] == observed["keypoint_names"]
                assert downloaded == observed
                with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/api/workspace/pose/import")) as pending_roundtrip:
                    page.locator("#human-pose-import").set_input_files(downloaded_path)
                assert pending_roundtrip.value.status == 200, pending_roundtrip.value.text()
                modified = json.loads(downloaded_path.read_text())
                modified["frames"][0]["keypoints"][0]["x"] = .123
                with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/api/workspace/pose/import")) as pending_conflict:
                    page.locator("#human-pose-import").set_input_files({"name": "changed.json", "mimeType": "application/json", "buffer": json.dumps(modified).encode()})
                assert pending_conflict.value.status == 409, pending_conflict.value.text()
                page.wait_for_function("document.getElementById('human-pose-status').textContent.includes('different completed result')")
                page.locator("#human-pose-panel summary").click()
                print("PASS: camera switching/scrubbing retain exact frames; complete JSON reimports idempotently and conflicting results are rejected", flush=True)

                page.reload()
                page.wait_for_function("window.__poseCheck && __poseCheck.state.workspaceReady && !__poseCheck.state.sceneLoading")
                assert page.evaluate("__poseCheck.state.poseRefs.length") == 2
                assert page.evaluate("!Object.hasOwn(__poseCheck.state,'humanPendingRequest')")
                page.locator("[data-tool='arrow']").click()
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
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
    print("ALL EXTERNAL POSE BROWSER CHECKS PASSED", flush=True)


if __name__ == "__main__":
    main()

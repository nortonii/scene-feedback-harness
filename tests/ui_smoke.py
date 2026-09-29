"""Isolated browser smoke checks; never connects to a running workbench or Codex.

Requires the optional Python playwright package and its Chromium browser:
    python tests/ui_smoke.py --artifacts-dir /tmp/scene-feedback-ui
Set PLAYWRIGHT_BROWSERS_PATH when browsers are installed in a custom directory.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "examples" / "room_demo"))
from build_scene import build  # noqa: E402
from server import make_server  # noqa: E402


def no_horizontal_overflow(page) -> None:
    sizes = page.evaluate("""() => ({viewport: innerWidth,
        body: document.body.scrollWidth, document: document.documentElement.scrollWidth})""")
    assert max(sizes["body"], sizes["document"]) <= sizes["viewport"] + 1, sizes


def drag(page, selector: str) -> None:
    node = page.locator(selector)
    node.scroll_into_view_if_needed()
    box = node.bounding_box()
    assert box and box["width"] > 40 and box["height"] > 40, (selector, box)
    page.mouse.move(box["x"] + box["width"] * 0.3, box["y"] + box["height"] * 0.3)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * 0.6, box["y"] + box["height"] * 0.6, steps=8)
    page.mouse.up()


def run_checks(page, store, session_id: str, base_url: str, artifacts: Path | None) -> list[str]:
    checked = []
    errors = []
    feedback_requests = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: feedback_requests.append(request)
            if request.method == "POST" and re.search(r"/api/sessions/[a-f0-9]+/feedback$", request.url) else None)
    page.goto(base_url, wait_until="networkidle")
    expect(page.locator("#submit-button")).to_be_enabled()
    expect(page.locator("#reference-image")).to_be_visible()
    page.wait_for_function("document.querySelector('#reference-image').naturalWidth > 0")
    expect(page.locator("#object-count")).to_have_text("1")
    expect(page.locator("#compare-opacity")).to_have_value("0")
    no_horizontal_overflow(page)
    assert not errors, errors
    if artifacts:
        page.screenshot(path=str(artifacts / "desktop-preview.png"), full_page=True)
    checked.append("desktop loading, scene, default comparison and overflow")

    tools = page.locator(".tool-button")
    assert tools.count() == 7
    for index in range(tools.count()):
        assert tools.nth(index).get_attribute("aria-pressed") in {"true", "false"}
    page.locator('.tool-button[data-tool="rectangle"]').click()
    expect(page.locator('.tool-button[data-tool="rectangle"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator('.tool-button[data-tool="select"]')).to_have_attribute("aria-pressed", "false")
    drag(page, "#reference-annotations")
    expect(page.locator("#annotation-count")).to_have_text("1")
    page.locator("#undo-annotation").click()
    expect(page.locator("#annotation-count")).to_have_text("0")
    page.locator("#redo-annotation").click()
    expect(page.locator("#annotation-count")).to_have_text("1")
    checked.append("tool accessibility, reference drawing and undo/redo")

    # The first drawing gesture must capture the live scene and retain its marks.
    page.locator('.tool-button[data-tool="arrow"]').click()
    drag(page, "#viewport canvas")
    expect(page.locator("#scene-snapshot-media")).to_be_visible()
    expect(page.locator("#annotation-count")).to_have_text("2")
    expect(page.locator("#scene-view-label")).to_contain_text("标注截图")
    original_snapshot = page.locator("#scene-snapshot-image").get_attribute("src")
    assert original_snapshot.startswith("data:image/jpeg;base64,")
    page.locator("#browse-button").click()
    expect(page.locator("#scene-snapshot-media")).to_be_hidden()
    expect(page.locator("#scene-view-label")).to_have_text("实时 3D")
    drag(page, "#viewport canvas")
    page.locator("#snapshot-button").click()
    expect(page.locator("#scene-snapshot-media")).to_be_visible()
    assert page.locator("#scene-snapshot-image").get_attribute("src") == original_snapshot
    checked.append("automatic scene capture, drawing, live orbit and original snapshot")

    # A modal's button focus must not expose the underlying annotation undo stack.
    page.locator("#references-dialog-button").click()
    expect(page.locator("#references-dialog")).to_be_visible()
    page.locator('[data-close-dialog="references-dialog"]').focus()
    page.keyboard.press("Control+z")
    expect(page.locator("#annotation-count")).to_have_text("2")
    page.keyboard.press("Escape")
    expect(page.locator("#references-dialog")).to_be_hidden()
    checked.append("modal keyboard isolation and Escape")

    note = page.locator("#feedback-note")
    original_note = "保留原有要求：柜子靠左，尺寸保持不变。"
    note.fill(original_note)
    note.evaluate("node => node.setSelectionRange(0, node.value.length)")
    example = page.locator("[data-prompt-example]").first
    example_text = example.get_attribute("data-prompt-example")
    example.click()
    expected_note = original_note + "\n" + example_text
    expect(note).to_have_value(expected_note)
    page.reload(wait_until="networkidle")
    expect(page.locator("#submit-button")).to_be_enabled()
    expect(note).to_have_value(expected_note)
    expect(page.locator("#annotation-count")).to_have_text("2")
    assert page.locator("#scene-snapshot-image").get_attribute("src") == original_snapshot
    checked.append("prompt example preserves selected text; note, marks and screenshot restore")

    page.locator("#task-dialog-button").click()
    expect(page.locator("#standalone-task-info")).to_be_visible()
    expect(page.locator("#standalone-task-title")).to_contain_text("独立工作台")
    expect(page.locator("#target-picker")).to_be_hidden()
    page.keyboard.press("Escape")
    checked.append("independent-session mode explanation")

    # Synthetic key events are intentional here: a composing/repeated Enter must
    # not create an HTTP request; the actual send below uses a real key press.
    for flags in ({"isComposing": True}, {"keyCode": 229}, {"repeat": True}):
        note.evaluate("""(node, flags) => node.dispatchEvent(new KeyboardEvent('keydown',
            {key:'Enter', code:'Enter', ctrlKey:true, bubbles:true, cancelable:true, ...flags}))""", flags)
    page.wait_for_timeout(200)
    assert len(feedback_requests) == 0
    assert store.feedback(session_id)["items"] == []
    with page.expect_response(lambda response: response.request.method == "POST"
                              and response.url.endswith(f"/api/sessions/{session_id}/feedback")) as result:
        note.press("Control+Enter")
    assert result.value.status == 201, result.value.text()
    expect(note).to_have_value("")
    page.wait_for_timeout(200)
    packets = store.feedback(session_id)["items"]
    assert len(feedback_requests) == len(packets) == 1
    packet = packets[0]
    assert packet["note"] == expected_note
    assert len(packet["annotations"]) == 2
    assert packet["scene_revision"] == store.scene()["revision"]
    assert packet["camera"]["position"]
    assert packet["reference_images"] and packet["reference_annotated_images"]
    for key in ("scene_original_url", "scene_annotated_url"):
        response = page.request.get(base_url + packet[key])
        assert response.ok and response.body().startswith(b"\xff\xd8"), (key, response.status)
    submitted = feedback_requests[0].post_data_json
    assert submitted["scene_original_data_url"] == original_snapshot
    assert submitted["idempotency_key"]
    if page.locator("#activity-dialog").is_visible():
        page.keyboard.press("Escape")
    checked.append("IME/repeat safeguards and exactly one stored Ctrl+Enter feedback with images")

    def offline(route):
        route.abort("internetdisconnected")

    page.route("**/api/**", offline)
    expect(page.locator("#session-pill")).to_contain_text("连接中断", timeout=8000)
    expect(page.locator("#workflow-status")).to_contain_text("连接中断")
    page.unroute("**/api/**", offline)
    expect(page.locator("#session-pill")).not_to_contain_text("连接中断", timeout=8000)
    expect(page.locator("#workflow-status")).not_to_contain_text("连接中断")
    checked.append("visible offline status and automatic recovery")

    # Only the disposable store is mutated to simulate terminal run outcomes.
    for status, label in (("failed", "上一轮执行失败"), ("interrupted", "上一轮执行中断"),
                          ("completed", "可以开始反馈")):
        with store.lock:
            item = store.state["workspace"]["queue"][-1]
            item.update(status=status, turn_id="smoke-turn")
            store._save()
        expect(page.locator("#workflow-status")).to_have_text(label, timeout=8000)
    checked.append("failed/interrupted run summary and completed recovery")

    # Files are posted only to the ephemeral server; no disk or Codex import.
    page.locator("#clear-annotations").click()
    expect(page.locator("#annotation-count")).to_have_text("0")
    image_bytes = (REPO / "examples" / "room_demo" / "reference.png").read_bytes()
    page.locator(".import-popover > summary").click()
    page.locator("#clip-fps").fill("1")
    with page.expect_response(lambda response: response.request.method == "POST"
                              and response.url.endswith(f"/api/sessions/{session_id}/clip")) as result:
        page.locator("#clip-input").set_input_files([
            {"name": "frame-001.png", "mimeType": "image/png", "buffer": image_bytes},
            {"name": "frame-002.png", "mimeType": "image/png", "buffer": image_bytes},
        ])
    assert result.value.ok, result.value.text()
    expect(page.locator("#timeline-panel")).to_be_visible()
    expect(page.locator("#timeline-next")).to_be_enabled()
    page.locator("#timeline-next").click()
    expect(page.locator("#timeline-time")).to_contain_text("1.000 / 2.000")
    page.locator("#save-moment").click()
    expect(page.locator(".moment-card")).to_have_count(1)
    expect(page.locator("#scene-snapshot-media")).to_be_visible()
    page.locator("#browse-button").click()
    page.locator("#timeline-prev").click()
    expect(page.locator("#timeline-time")).to_contain_text("0.000 / 2.000")
    assert len(store.get_session(session_id)["reference_clip"]["frames"]) == 2
    checked.append("two-frame image import, timeline stepping and saved moment")

    for width in (340, 390):
        page.set_viewport_size({"width": width, "height": 844})
        no_horizontal_overflow(page)
        for selector in ("#more-tools", ".timeline-options"):
            page.locator(selector + " > summary").click()
            popup = page.locator(selector + " > .popover-content")
            expect(popup).to_be_visible()
            bounds = popup.bounding_box()
            assert bounds and bounds["x"] >= -1 and bounds["x"] + bounds["width"] <= width + 1, (width, selector, bounds)
            page.keyboard.press("Escape")
    checked.append("340px and 390px tool/timeline popovers fit viewport")
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator("#reference-stage").scroll_into_view_if_needed()
    no_horizontal_overflow(page)
    assert page.locator("#reference-stage").bounding_box()["height"] > 100
    assert page.locator("#scene-stage").bounding_box()["height"] > 100
    page.locator("#feedback-note").scroll_into_view_if_needed()
    expect(page.locator("#submit-button")).to_be_visible()
    if artifacts:
        page.screenshot(path=str(artifacts / "mobile.png"), full_page=True)
    assert not errors, errors
    checked.append("mobile layout, visible composer and zero page errors")
    return checked


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-dir", type=Path)
    args = parser.parse_args()
    artifacts = args.artifacts_dir.resolve() if args.artifacts_dir else None
    if artifacts:
        artifacts.mkdir(parents=True, exist_ok=True)
    temporary_root = REPO.parent / "tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ui-smoke-", dir=temporary_root) as temporary:
        root = Path(temporary)
        server = make_server(port=0, data_dir=root / "data", project_dir=REPO,
                             web_dir=REPO / "web", enable_codex=False)
        store = server.scene_store
        session_id = store.ensure_workspace(REPO)["session_id"]
        raw = (REPO / "examples" / "room_demo" / "reference.png").read_bytes()
        store.add_reference(session_id, "reference.png", "data:image/png;base64," + base64.b64encode(raw).decode())
        glb = build(REPO / "examples" / "room_demo" / "scene.json", root / "current.glb")
        store.set_scene_preview(str(glb))
        store.workspace_agent(status="idle")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, args=["--enable-unsafe-swiftshader"])
                page = browser.new_page(viewport={"width": 1440, "height": 960}, device_scale_factor=1)
                page.set_default_timeout(10000)
                try:
                    checks = run_checks(page, store, session_id, f"http://127.0.0.1:{server.server_port}", artifacts)
                except BaseException:
                    if artifacts:
                        page.screenshot(path=str(artifacts / "failure.png"), full_page=True)
                    raise
                finally:
                    browser.close()
                print(json.dumps({"result": "passed", "checks": checks, "artifacts": str(artifacts) if artifacts else None}, ensure_ascii=False, indent=2))
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()


if __name__ == "__main__":
    main()

"""Isolated browser regressions for the floating conversation workspace.

Run with a Python environment containing Playwright and an installed Chromium:
    python tests/focus_workspace_smoke.py

The server uses an ephemeral loopback port and a temporary data directory. No
Codex process starts, and no production session, scene, or account is accessed.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
from pathlib import Path
import sys
import tempfile
import threading
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "examples" / "room_demo"))

from build_scene import build  # noqa: E402
from core import SceneStore  # noqa: E402
from server import make_server  # noqa: E402
from gateway import WorkspaceGateway  # noqa: E402
from playwright.sync_api import expect, sync_playwright  # noqa: E402


def draft(page):
    return page.evaluate("""() => {
      const session = new URL(location.href).searchParams.get('session_id');
      const key = session ? 'astra-visual-draft:' + session : Object.keys(localStorage).find(k => k.startsWith('astra-visual-draft:'));
      return JSON.parse(localStorage.getItem(key) || '{}');
    }""")


def geometry(page):
    return page.evaluate("""() => Object.fromEntries(
      ['reference-stage', 'scene-stage', 'viewport'].map(id => {
        const r = document.getElementById(id).getBoundingClientRect();
        return [id, {x:r.x, y:r.y, width:r.width, height:r.height}];
      }).concat([['canvas', (() => {
        const c = document.querySelector('#viewport canvas');
        return {width:c.width, height:c.height};
      })()]])
    )""")


def assert_launcher(page):
    launcher = page.locator("#chat-launcher")
    expect(launcher).to_be_visible()
    box = launcher.bounding_box()
    viewport = page.viewport_size
    assert box["x"] >= 0 and box["y"] >= 0, box
    assert box["x"] + box["width"] <= viewport["width"], box
    assert box["y"] + box["height"] <= viewport["height"], box
    assert box["height"] <= 64, box
    assert launcher.evaluate("el => el.scrollWidth <= el.clientWidth"), "Launcher content overflows"
    assert launcher.evaluate("""el => {
      const label = el.querySelector('span:not(#chat-unread-count)');
      const range = document.createRange();
      range.selectNodeContents(label);
      return range.getClientRects().length === 1;
    }"""), "Launcher label wraps"


def wait_ready(page):
    expect(page.locator("#submit-button")).to_be_enabled(timeout=20000)
    page.wait_for_function("""() => {
      const img = document.getElementById('reference-image');
      return img.complete && img.naturalWidth > 0 &&
        document.querySelector('#viewport canvas')?.width > 0;
    }""")
    page.wait_for_timeout(250)


def verify_chat_resize(page, store, submissions, screenshots):
    """Resize with real pointer/keyboard input while a visual draft is pending."""
    for index in range(7):
        store.workspace_event("assistant_message", {"text": f"尺寸检查 {index + 1}：" + "保留原截图和柜子标注，继续核对位置。" * 3})
    expect(page.locator("#conversation")).to_contain_text("尺寸检查 7", timeout=10000)
    handle = page.locator("#chat-resize-handle")
    expect(handle).to_be_visible()
    dock = page.locator("#chat-dock")
    conversation = page.locator("#conversation")
    initial = dock.bounding_box()
    initial_history = conversation.evaluate("el => el.clientHeight")
    original = draft(page)
    original_geometry = geometry(page)
    original_submissions = len(submissions)
    handle_box = handle.bounding_box()
    x, y = handle_box["x"] + handle_box["width"] / 2, handle_box["y"] + handle_box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, y - 160, steps=12)
    page.mouse.up()
    page.wait_for_timeout(100)
    enlarged = dock.bounding_box()
    assert enlarged["height"] >= initial["height"] + 120, (initial, enlarged)
    assert conversation.evaluate("el => el.clientHeight") >= initial_history + 100
    assert abs(enlarged["y"] + enlarged["height"] - initial["y"] - initial["height"]) < 2
    assert geometry(page) == original_geometry
    for field in ("note", "annotations", "snapshot", "camera", "selectedId", "referencedSceneNodes"):
        assert draft(page).get(field) == original.get(field), field
    assert len(submissions) == original_submissions
    if screenshots:
        page.screenshot(path=str(screenshots / "resized-expanded.png"))

    def saved_height():
        return page.evaluate("""() => {
          const session = new URL(location.href).searchParams.get('session_id');
          return JSON.parse(localStorage.getItem('astra-visual-layout:' + session) || '{}').dockHeight;
        }""")

    preferred_height = saved_height()
    page.locator("#chat-history-toggle").click()
    expect(page.locator("#chat-history")).to_be_hidden()
    expect(handle).to_be_hidden()
    assert handle.evaluate("el => el.tabIndex") == -1
    page.locator("#chat-history-toggle").press("Shift+Tab")
    expect(handle).not_to_be_focused()
    compact = dock.bounding_box()
    assert compact["height"] < enlarged["height"] - 80
    expect(page.locator("#feedback-note")).to_be_visible()
    # The former grip overlapped the dock's top edge. Drag that exact area,
    # rather than dragging the now-hidden element programmatically.
    edge_x, edge_y = compact["x"] + compact["width"] / 2, compact["y"] + 1
    page.mouse.move(edge_x, edge_y)
    page.mouse.down()
    page.mouse.move(edge_x, edge_y - 120, steps=8)
    page.mouse.up()
    # Hidden controls also ignore stale keyboard/double-click events.
    handle.dispatch_event("keydown", {"key": "Home"})
    handle.dispatch_event("dblclick")
    expect(page.locator("#chat-history")).to_be_hidden()
    assert abs(dock.bounding_box()["height"] - compact["height"]) < 2
    assert saved_height() == preferred_height
    for field in ("note", "annotations", "snapshot", "camera"):
        assert draft(page).get(field) == original.get(field), field
    assert len(submissions) == original_submissions
    page.reload()
    wait_ready(page)
    expect(page.locator("#chat-history")).to_be_hidden()
    expect(handle).to_be_hidden()
    assert saved_height() == preferred_height
    if screenshots:
        page.screenshot(path=str(screenshots / "history-collapsed-no-handle.png"))
    page.locator("#chat-history-toggle").click()
    expect(handle).to_be_visible()
    assert handle.evaluate("el => el.tabIndex") == 0
    assert abs(dock.bounding_box()["height"] - enlarged["height"]) < 2
    restored_handle = handle.bounding_box()
    restored_x = restored_handle["x"] + restored_handle["width"] / 2
    restored_y = restored_handle["y"] + restored_handle["height"] / 2
    page.mouse.move(restored_x, restored_y)
    page.mouse.down()
    page.mouse.move(restored_x, restored_y - 24, steps=4)
    page.mouse.up()
    assert dock.bounding_box()["height"] >= enlarged["height"] + 20
    enlarged = dock.bounding_box()
    page.locator("#chat-collapse").click()
    page.reload()
    wait_ready(page)
    expect(dock).to_be_hidden()
    page.locator("#chat-launcher").click()
    page.wait_for_timeout(100)
    assert abs(dock.bounding_box()["height"] - enlarged["height"]) < 2
    expect(page.locator("#feedback-note")).to_have_value(original["note"])
    assert draft(page)["annotations"] == original["annotations"]
    assert draft(page)["snapshot"] == original["snapshot"]

    handle.focus()
    before_key = dock.bounding_box()["height"]
    handle.press("ArrowUp")
    assert dock.bounding_box()["height"] > before_key
    handle.press("ArrowDown")
    assert abs(dock.bounding_box()["height"] - before_key) < 2
    handle.press("End")
    maximum = dock.bounding_box()
    assert maximum["y"] >= 0 and maximum["y"] + maximum["height"] <= 900
    assert maximum["height"] >= enlarged["height"]
    page.set_viewport_size({"width": 390, "height": 640})
    page.wait_for_timeout(150)
    constrained = dock.bounding_box()
    assert constrained["x"] >= 0 and constrained["x"] + constrained["width"] <= 390
    assert constrained["y"] >= 0 and constrained["y"] + constrained["height"] <= 640
    expect(page.locator("#feedback-note")).to_be_visible()
    expect(page.locator("#submit-button")).to_be_visible()
    if screenshots:
        page.screenshot(path=str(screenshots / "resized-mobile-clamped.png"))
    page.set_viewport_size({"width": 1440, "height": 900})
    page.wait_for_timeout(150)
    handle.press("Home")
    minimum = dock.bounding_box()
    minimum_height = float(handle.get_attribute("aria-valuemin"))
    assert minimum["height"] >= minimum_height - 2
    handle.press("ArrowDown")
    assert abs(dock.bounding_box()["height"] - minimum["height"]) < 2
    minimum_handle = handle.bounding_box()
    min_x = minimum_handle["x"] + minimum_handle["width"] / 2
    min_y = minimum_handle["y"] + minimum_handle["height"] / 2
    page.mouse.move(min_x, min_y)
    page.mouse.down()
    page.mouse.move(min_x, min(890, min_y + 120), steps=8)
    page.mouse.up()
    assert abs(dock.bounding_box()["height"] - minimum["height"]) < 2
    note_box = page.locator("#feedback-note").bounding_box()
    assert note_box["y"] >= minimum["y"] and note_box["y"] + note_box["height"] <= minimum["y"] + minimum["height"]
    handle.dblclick()
    page.wait_for_timeout(150)
    assert abs(dock.bounding_box()["height"] - initial["height"]) < 2
    for field in ("note", "annotations", "snapshot", "camera", "selectedId", "referencedSceneNodes"):
        assert draft(page).get(field) == original.get(field), field
    assert geometry(page) == original_geometry
    assert len(submissions) == original_submissions
    if screenshots:
        page.screenshot(path=str(screenshots / "resized-reset.png"))


def verify_overlay_toolbar(page, store, screenshots):
    toggle = page.locator("#compare-toggle")
    panel = page.locator("#compare-panel")
    slider = page.locator("#compare-opacity")
    overlay = page.locator("#compare-image")
    original = draft(page)
    expect(toggle).to_be_enabled()
    expect(toggle).to_have_attribute("aria-pressed", "true")
    expect(slider).to_have_value("45")
    panel.locator("summary").click()
    for opacity in (25, 45, 75):
        page.locator(f'[data-compare-opacity="{opacity}"]').click()
        expect(slider).to_have_value(str(opacity))
        expect(overlay).to_have_css("opacity", str(opacity / 100))
        assert panel.evaluate("el => el.open"), "Selecting a preset closed the opacity popover"
    slider.focus()
    slider.press("ArrowRight")
    expect(slider).to_have_value("76")
    expect(overlay).to_have_css("opacity", "0.76")
    assert panel.evaluate("el => el.open")
    page.locator('[data-compare-opacity="75"]').click()
    if screenshots:
        page.screenshot(path=str(screenshots / "overlay-popover.png"))
    panel.locator("summary").click()
    assert draft(page) == original, "Display-only opacity controls changed the visual feedback draft"
    toggle.click()
    expect(toggle).to_have_attribute("aria-pressed", "false")
    expect(overlay).to_be_hidden()
    toggle.click()
    expect(toggle).to_have_attribute("aria-pressed", "true")
    expect(overlay).to_be_visible()
    expect(overlay).to_have_css("opacity", "0.75")
    page.reload()
    wait_ready(page)
    expect(toggle).to_have_attribute("aria-pressed", "true")
    expect(slider).to_have_value("75")

    # Reopen the existing marked screenshot, preserving its original evidence.
    page.locator("#snapshot-button").click()
    expect(overlay).to_be_hidden()
    expect(toggle).to_be_disabled()
    expect(toggle).to_have_attribute("aria-pressed", "false")
    expect(slider).to_be_disabled()
    for opacity in (25, 45, 75):
        expect(page.locator(f'[data-compare-opacity="{opacity}"]')).to_be_disabled()
    preference = page.evaluate("""() => JSON.parse(localStorage.getItem(
      'astra-visual-compare:' + new URL(location.href).searchParams.get('session_id')) || '{}')""")
    assert preference["enabled"] is True and preference["opacity"] == 75
    page.locator("#browse-button").click()
    expect(toggle).to_be_enabled()
    expect(toggle).to_have_attribute("aria-pressed", "true")
    expect(overlay).to_be_visible()
    expect(overlay).to_have_css("opacity", "0.75")
    for field in ("note", "annotations", "snapshot", "camera", "referencedSceneNodes"):
        assert draft(page).get(field) == original.get(field), field

    panel.locator("summary").click()
    slider.focus()
    slider.press("Home")
    expect(slider).to_have_value("0")
    expect(toggle).to_have_attribute("aria-pressed", "false")
    expect(overlay).to_be_hidden()
    page.reload()
    wait_ready(page)
    expect(slider).to_have_value("0")
    toggle.click()
    expect(slider).to_have_value("75")
    expect(overlay).to_be_visible()
    expect(overlay).to_have_css("opacity", "0.75")
    toggle.click()
    image_url = "data:image/png;base64," + base64.b64encode(
        (ROOT / "examples/room_demo/reference.png").read_bytes()).decode()
    store.add_reference(store.workspace()["session_id"], "alternate-reference.png", image_url)
    page.reload()
    wait_ready(page)
    expect(toggle).to_have_attribute("aria-pressed", "false")
    expect(slider).to_have_value("75")
    expect(overlay).to_be_hidden()
    page.get_by_role("button", name="查看 alternate-reference.png", exact=True).click()
    expect(page.locator("#reference-title")).to_have_text("alternate-reference.png")
    expect(toggle).to_have_attribute("aria-pressed", "false")
    expect(overlay).to_be_hidden()
    page.get_by_role("button", name="查看 reference.png", exact=True).click()
    expect(toggle).to_have_attribute("aria-pressed", "false")
    expect(overlay).to_be_hidden()


def verify_compact_header(page, store, screenshots):
    if page.locator("#chat-launcher").is_visible():
        page.locator("#chat-launcher").click()
    if page.locator("#chat-history").is_visible():
        page.locator("#chat-history-toggle").click()
    for index in range(123):
        store.workspace_event("assistant_message", {"text": f"窄屏计数检查 {index + 1}"})
    expect(page.locator("#conversation")).to_contain_text("窄屏计数检查 123", timeout=10000)
    expect(page.locator("#chat-message-count")).to_contain_text("123")
    for width in (340, 390, 768, 1440):
        page.set_viewport_size({"width": width, "height": 900 if width > 640 else 844})
        page.wait_for_timeout(100)
        assert page.locator(".chat-dock-header").evaluate("el => el.scrollWidth <= el.clientWidth + 1")
        for selector in ("#chat-history-toggle", "#references-dialog-button", "#chat-collapse"):
            assert page.locator(selector).evaluate("""el => {
              const r=el.getBoundingClientRect(), h=el.closest('.chat-dock-header').getBoundingClientRect();
              const hit=document.elementFromPoint(r.x+r.width/2,r.y+2);
              return r.left>=h.left && r.right<=h.right && r.top>=h.top && r.bottom<=h.bottom && hit?.closest('button')===el;
            }"""), (width, selector)
        if width == 340:
            if screenshots:
                page.screenshot(path=str(screenshots / "mobile-long-count.png"))
            references = page.locator("#references-dialog-button")
            references.click(position={"x": references.bounding_box()["width"] / 2, "y": 2})
            expect(page.locator("#references-dialog")).to_be_visible()
            page.locator('[data-close-dialog="references-dialog"]').click()
    page.locator("#chat-history-toggle").click()


def verify_project_isolation(page, server, screenshots):
    """Use real project routing/stores while stubbing only Codex Desktop I/O."""
    original_state = WorkspaceGateway.state
    created_tasks = []

    def fixture_state(gateway, **kwargs):
        result = original_state(gateway, **kwargs)
        result["desktop_available"] = True
        return result

    def fixture_start(gateway):
        gateway.ensure()
        gateway.store.workspace_agent(status="idle")
        gateway._started = True

    def fixture_target(gateway, model, *, reasoning_effort=None, title=None, permission_mode="workspace_write"):
        thread_id = str(uuid.uuid4())
        with gateway.store.lock:
            workspace = gateway.store.state["workspace"]
            workspace["created_thread_ids"].append(thread_id)
            workspace["created_thread_specs"][thread_id] = {"model": model, "title": title,
                "reasoning_effort": reasoning_effort, "permission_mode": permission_mode}
            gateway.store._save()
        gateway.store.workspace_thread(thread_id)
        created_tasks.append(thread_id)
        return {"thread_id": thread_id, "workspace": gateway.state()}

    models = {"models": [{"model": "fixture-model", "display_name": "隔离测试模型",
                         "supported_reasoning_efforts": ["low"], "is_default": True}],
              "default_model": "fixture-model", "permission_modes_supported": True}
    root = server.project_registry.root
    root_note, child_note = "原场景的独立未发送草稿", "新场景 B 的独立未发送草稿"
    original_feedback = root.store.list_all_feedback()
    root_event_marker = "原场景项目切换前专属消息"
    root.store.workspace_event("assistant_message", {"text": root_event_marker})
    page.set_viewport_size({"width": 1440, "height": 900})
    page.evaluate("scrollTo(0,0)")
    if page.locator("#chat-launcher").is_visible():
        page.locator("#chat-launcher").click()
    page.locator("#feedback-note").fill(root_note)
    page.locator("#chat-resize-handle").press("ArrowUp")
    root_dock_height = page.locator("#chat-dock").bounding_box()["height"]

    with patch.object(WorkspaceGateway, "state", fixture_state), \
         patch.object(WorkspaceGateway, "start", fixture_start), \
         patch.object(WorkspaceGateway, "create_target", fixture_target), \
         patch.object(WorkspaceGateway, "list_models", return_value=models):
        page.reload()
        wait_ready(page)
        page.locator("#projects-dialog-button").click()
        page.locator("#create-project-panel summary").click()
        page.locator("#project-name").fill("隔离新场景 B")
        expect(page.locator("#project-model")).to_have_value("fixture-model")
        expect(page.locator("#create-project")).to_be_enabled()
        page.locator("#create-project").click()
        page.wait_for_url(re.compile(r"/p/[0-9a-f]{32}/"))
        expect(page.locator("#scene-name")).to_have_text("隔离新场景 B", timeout=10000)
        expect(page.locator("#submit-button")).to_be_enabled()
        child = server.project_registry.contexts()[-1]
        assert child.project_id != root.project_id
        assert child.store.workspace()["session_id"] != root.store.workspace()["session_id"]
        assert child.store.browser_token != root.store.browser_token
        assert child.gateway.adapter is None and len(created_tasks) == 1
        assert child.store.scene()["objects"] == []
        expect(page.locator("#reference-empty")).to_be_visible()
        expect(page.locator("#compare-toggle")).to_be_disabled()
        expect(page.locator("#compare-opacity")).to_be_disabled()
        expect(page.locator("#compare-opacity")).to_have_value("45")
        expect(page.locator("#compare-image")).to_be_hidden()
        expect(page.locator("#timeline-panel")).to_be_hidden()
        expect(page.locator("#feedback-note")).to_have_value("")
        expect(page.locator("#conversation")).not_to_contain_text(root_event_marker)
        page.locator("#feedback-note").fill(child_note)
        page.locator("#chat-collapse").click()
        if screenshots:
            page.screenshot(path=str(screenshots / "independent-project.png"))

        def switch_project(name):
            page.locator("#projects-dialog-button").click()
            choice = page.locator("#project-list .project-item").filter(has_text=name)
            expect(choice).to_be_enabled()
            choice.click()
            expect(page.locator("#scene-name")).to_have_text(name, timeout=10000)
            expect(page.locator("#submit-button")).to_be_enabled()

        switch_project(root.name)
        wait_ready(page)
        expect(page.locator("#feedback-note")).to_have_value(root_note)
        assert abs(page.locator("#chat-dock").bounding_box()["height"] - root_dock_height) < 2
        expect(page.locator("#reference-view-select")).to_be_visible()
        expect(page.locator("#compare-toggle")).to_have_attribute("aria-pressed", "false")
        expect(page.locator("#compare-opacity")).to_have_value("75")
        expect(page.locator("#compare-image")).to_be_hidden()
        expect(page.locator("#conversation")).to_contain_text(root_event_marker)
        assert root.store.list_all_feedback() == original_feedback
        assert child.store.list_all_feedback() == []
        switch_project("隔离新场景 B")
        expect(page.locator("#chat-dock")).to_be_hidden()
        page.locator("#chat-launcher").click()
        expect(page.locator("#feedback-note")).to_have_value(child_note)
        child_height = page.evaluate("""() => {
          const session = new URL(location.href).searchParams.get('session_id');
          return JSON.parse(localStorage.getItem('astra-visual-layout:' + session) || '{}').dockHeight ?? null;
        }""")
        assert child_height is None
        expect(page.locator("#conversation")).not_to_contain_text(root_event_marker)
        assert child.store.get_session(child.store.workspace()["session_id"])["reference_images"] == []
        assert len(server.project_registry.contexts()) == 2 and len(created_tasks) == 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshots", type=Path)
    parser.add_argument("--baseline", action="store_true", help="Report original geometry only.")
    args = parser.parse_args()
    screenshots = args.screenshots
    if screenshots:
        screenshots.mkdir(parents=True, exist_ok=True)
    temp_parent = ROOT.parent / "tmp"
    temp_parent.mkdir(exist_ok=True)
    results = []

    def passed(name):
        results.append(name)
        print("PASS " + name, flush=True)

    with tempfile.TemporaryDirectory(prefix="focus-browser-", dir=temp_parent) as temporary:
        temporary = Path(temporary)
        seed = SceneStore(temporary / "data")
        session = seed.create_session(reference_images=[str(ROOT / "examples/room_demo/reference.png")])
        model = build(ROOT / "examples/room_demo/scene.json", temporary / "room.glb")
        seed.set_scene_preview(str(model))
        server = make_server(port=0, data_dir=temporary / "data", web_dir=ROOT / "web",
                             project_dir=ROOT, enable_codex=False)
        store = server.scene_store
        server.workspace_gateway.ensure(session["session_id"])
        store.workspace_agent(status="idle")
        store.workspace_event("feedback_queued", {"note": "请把柜子向左移动一点。"})
        store.workspace_event("assistant_message", {"text": "已调整柜子位置，请对照参考图继续标注。"})
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = server.browser_url(session["session_id"])
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, args=[
                    "--no-sandbox", "--use-gl=angle", "--use-angle=swiftshader",
                    "--enable-unsafe-swiftshader",
                ])
                context = browser.new_context(viewport={"width": 1440, "height": 900},
                                              device_scale_factor=1, locale="zh-CN")
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                submissions = []
                pose_requests = []
                page.on("request", lambda request: pose_requests.append(request.url)
                        if request.method == "POST" and "/api/workspace/pose" in request.url else None)
                page.on("request", lambda request: submissions.append(request.post_data_json)
                        if request.method == "POST" and "/api/sessions/" in request.url
                        and request.url.endswith("/feedback") else None)
                page.goto(url)
                wait_ready(page)
                if args.baseline:
                    print(json.dumps({"baseline": geometry(page)}, ensure_ascii=False))
                    if screenshots:
                        page.screenshot(path=str(screenshots / "baseline.png"))
                    browser.close()
                    return

                expect(page.locator("#chat-dock")).to_be_visible()
                expect(page.locator("#chat-history")).to_be_visible()
                expect(page.locator("#conversation")).to_contain_text("请把柜子向左移动一点。")
                expect(page.locator("#conversation")).to_contain_text("已调整柜子位置")
                expect(page.locator(".feedback-heading")).to_be_hidden()
                references = page.locator("#references-dialog-button")
                assert references.evaluate("el => !!el.closest('.chat-dock-header')")
                reference_box = references.bounding_box()
                header_box = page.locator(".chat-dock-header").bounding_box()
                assert reference_box["y"] >= header_box["y"] and reference_box["y"] + reference_box["height"] <= header_box["y"] + header_box["height"]
                assert geometry(page)["scene-stage"]["height"] >= 650, geometry(page)
                print(json.dumps({"workspace_geometry": geometry(page)}, ensure_ascii=False), flush=True)
                if screenshots:
                    page.screenshot(path=str(screenshots / "expanded.png"))
                passed("expanded history restores real recorded user and assistant events")

                page.locator("#feedback-note").fill("保留草稿：柜子靠左，其他物体不动。")
                before = geometry(page)
                previous = draft(page)
                page.locator("#chat-history-toggle").click()
                expect(page.locator("#chat-history")).to_be_hidden()
                expect(page.locator("#feedback-note")).to_be_visible()
                assert geometry(page) == before
                if screenshots:
                    page.screenshot(path=str(screenshots / "history-hidden.png"))
                page.locator("#chat-collapse").click()
                expect(page.locator("#chat-dock")).to_be_hidden()
                assert_launcher(page)
                assert geometry(page) == before
                assert draft(page)["camera"] == previous["camera"]
                assert draft(page)["note"] == previous["note"]
                assert page.evaluate("""() => {
                  const r = document.getElementById('scene-stage').getBoundingClientRect();
                  const el = document.elementFromPoint(r.x + r.width * .3, r.bottom - 45);
                  return !!el.closest('#scene-stage');
                }"""), "Collapsed dock blocks scene pointer interaction"
                if screenshots:
                    page.screenshot(path=str(screenshots / "collapsed.png"))
                passed("independent history and dock collapse preserve canvas, camera, draft and hit testing")

                page.reload()
                wait_ready(page)
                expect(page.locator("#chat-dock")).to_be_hidden()
                page.locator("#chat-launcher").click()
                expect(page.locator("#chat-history")).to_be_hidden()
                expect(page.locator("#feedback-note")).to_have_value(previous["note"])
                page.locator("#chat-history-toggle").click()
                expect(page.locator("#conversation")).to_contain_text("已调整柜子位置")
                passed("collapsed preferences and draft survive reload independently of recorded history")

                page.locator("#chat-collapse").click()
                page.locator('[data-tool="rectangle"]').click()
                stage = page.locator("#scene-stage").bounding_box()
                page.mouse.move(stage["x"] + stage["width"] * .30, stage["y"] + stage["height"] * .35)
                page.mouse.down()
                page.mouse.move(stage["x"] + stage["width"] * .50, stage["y"] + stage["height"] * .55, steps=8)
                page.mouse.up()
                expect(page.locator("#annotation-count")).to_have_text("1")
                annotated = draft(page)
                assert annotated["snapshot"]
                page.locator("#chat-launcher").click()
                page.locator("#references-dialog-button").click()
                expect(page.locator("#references-dialog")).to_be_visible()
                page.locator(".annotation-reference-insert").first.click()
                expect(page.locator("#references-dialog")).to_be_hidden()
                expect(page.locator("#feedback-note")).to_be_focused()
                note = page.locator("#feedback-note").input_value()
                assert "保留草稿" in note and "[[annotation:" in note
                page.locator("#chat-collapse").click()
                page.locator("#chat-launcher").click()
                after = draft(page)
                assert after["snapshot"] == annotated["snapshot"]
                assert after["annotations"] == annotated["annotations"]
                passed("scene annotations and frozen evidence survive collapse; reference insertion restores composer focus")
                verify_chat_resize(page, store, submissions, screenshots)
                passed("pointer and keyboard resizing preserve draft and height; collapsed history disables resize until explicitly reopened, including after reload")
                verify_overlay_toolbar(page, store, screenshots)
                passed("overlay toolbar presets and slider apply immediately, remember opacity, pause on marked screenshots and keep saved off preference across reference changes")

                page.locator("#chat-collapse").click()
                store.workspace_event("assistant_message", {"text": "收起时的新回复：标记已收到。"})
                expect(page.locator("#conversation")).to_contain_text("收起时的新回复", timeout=10000)
                expect(page.locator("#chat-dock")).to_be_hidden()
                expect(page.locator("#chat-unread-count")).to_be_visible()
                page.locator("#chat-launcher").click()
                expect(page.locator("#chat-unread-count")).to_be_hidden()
                passed("incoming recorded messages display unread count without interrupting a collapsed workspace")

                page.locator("#chat-history-toggle").click()
                expect(page.locator("#chat-history")).to_be_hidden()
                page.locator("#feedback-note").focus()
                store.workspace_event("assistant_message", {"text": "仅收起记录时的新回复"})
                expect(page.locator("#conversation")).to_contain_text("仅收起记录时的新回复", timeout=10000)
                expect(page.locator("#chat-message-count")).to_contain_text("新")
                expect(page.locator("#chat-dock")).to_be_visible()
                expect(page.locator("#chat-history")).to_be_hidden()
                expect(page.locator("#feedback-note")).to_be_focused()
                page.locator("#chat-history-toggle").click()
                expect(page.locator("#chat-message-count")).not_to_contain_text("新")
                passed("history-only collapse indicates unread replies without moving input focus")

                for index in range(15):
                    store.workspace_event("assistant_message", {"text": f"历史进度 {index}：" + "检查几何位置并保留原图标注。" * 8})
                expect(page.locator("#conversation")).to_contain_text("历史进度 14", timeout=10000)
                scrollable = page.locator("#conversation")
                assert scrollable.evaluate("el => el.scrollHeight > el.clientHeight + 60")
                scrollable.evaluate("el => { el.scrollTop = 0; el.dispatchEvent(new Event('scroll')); }")
                store.workspace_event("assistant_message", {"text": "阅读旧消息期间到达的最新回复"})
                expect(scrollable).to_contain_text("阅读旧消息期间", timeout=10000)
                assert scrollable.evaluate("el => el.scrollTop") < 40
                if page.locator("#chat-latest").is_visible():
                    page.locator("#chat-latest").click()
                    page.wait_for_timeout(350)
                    assert scrollable.evaluate("el => el.scrollHeight - el.clientHeight - el.scrollTop") < 8
                passed("new messages preserve reading position and latest-message jump reaches bottom")

                page.reload()
                wait_ready(page)
                expect(page.locator("#conversation")).to_contain_text("阅读旧消息期间到达的最新回复")
                expect(page.locator("#conversation")).to_contain_text("请把柜子向左移动一点。")
                expect(page.locator("#feedback-note")).to_have_value(note)
                passed("conversation replays server history on reload without losing pending annotation references")

                expect(page.locator("#projects-dialog-button")).to_be_visible()
                page.locator("#human-pose-panel summary").click()
                expect(page.locator("#human-pose-panel summary")).to_have_text("人体结果")
                expect(page.locator("#human-pose-import")).to_be_enabled()
                expect(page.locator("#human-pose-jobs")).to_contain_text("capsule")
                expect(page.locator("#human-pose-run, #human-pose-draw, #human-pose-runtime")).to_have_count(0)
                expect(page.locator("#annotation-count")).to_have_text("1")
                assert "humanSeed" not in draft(page)
                assert page.locator("#human-pose-overlay").evaluate("el => getComputedStyle(el).pointerEvents") == "none"
                page.keyboard.press("Escape")
                passed("passive human result import remains separate from feedback marks with no inference or ROI controls")

                composer = page.locator("#feedback-note")
                composer.focus()
                composer.evaluate("""el => {
                  for (const modifier of ['ctrlKey', 'metaKey']) {
                    el.dispatchEvent(new KeyboardEvent('keydown', {
                      key:'Enter', [modifier]:true, isComposing:true, bubbles:true, cancelable:true
                    }));
                    el.dispatchEvent(new KeyboardEvent('keydown', {
                      key:'Enter', [modifier]:true, keyCode:229, bubbles:true, cancelable:true
                    }));
                  }
                }""")
                expect(composer).to_be_enabled()
                expect(composer).to_have_value(note)
                composer.press("End")
                composer.press("Enter")
                expect(composer).to_have_value(note + "\n")
                assert not submissions and not store.list_all_feedback()
                composer.fill(note)
                passed("IME composition and keyCode 229 block Ctrl/Cmd Enter; plain Enter only inserts a newline")

                before_submit = draft(page)
                # The app's textarea listener runs first. A second listener clicks
                # Send in the same key event, avoiding timing-dependent retries.
                composer.evaluate("""el => {
                  window.testDuplicateShortcutClick = false;
                  const duplicate = event => {
                    if (event.key !== 'Enter' || !event.ctrlKey) return;
                    el.removeEventListener('keydown', duplicate);
                    document.getElementById('submit-button').click();
                    window.testDuplicateShortcutClick = true;
                  };
                  el.addEventListener('keydown', duplicate);
                }""")
                composer.press("Control+Enter")
                assert page.evaluate("window.testDuplicateShortcutClick")
                page.wait_for_function("document.getElementById('feedback-count-label').textContent.includes('1 条')")
                assert len(submissions) == 1, len(submissions)
                sent = submissions[0]
                assert sent["note"] == note
                assert len(sent["annotations"]) == 1
                assert sent["scene_original_data_url"].startswith("data:image/")
                assert sent["scene_annotated_data_url"].startswith("data:image/")
                assert sent["camera"] == before_submit["snapshot"]["camera"]
                assert len(store.list_all_feedback()) == 1
                expect(page.locator("#feedback-note")).to_have_value("")
                passed("real Ctrl Enter plus immediate duplicate click creates one feedback with frozen camera and image evidence")

                expect(page.locator("#annotation-count")).to_have_text("0")
                expect(page.locator("#undo-annotation")).to_be_disabled()
                expect(page.locator("#snapshot-button")).to_be_hidden()
                fresh = draft(page)
                assert fresh["annotations"] == [] and fresh["snapshot"] is None
                assert fresh["selectedId"] is None and fresh["sceneView"] == "live"
                assert fresh["poseRefs"] == [] and "humanSeed" not in fresh
                assert fresh["referencedSceneNodes"] == []
                packet = store.list_all_feedback()[0]
                assert len(packet["annotations"]) == 1 and packet["scene_original_url"]
                page.reload()
                wait_ready(page)
                expect(page.locator("#annotation-count")).to_have_text("0")
                expect(page.locator("#feedback-note")).to_have_value("")
                expect(page.locator("#undo-annotation")).to_be_disabled()
                assert draft(page)["snapshot"] is None
                assert store.list_all_feedback()[0] == packet
                passed("acknowledged send clears draft, marks, snapshot, pose references and undo history across reload while saved evidence remains immutable")

                page.locator("#activity-dialog-button").click()
                expect(page.locator("#activity-dialog")).to_be_visible()
                expect(page.locator("#queue-list")).to_contain_text("针对场景版本")
                page.locator('[data-close-dialog="activity-dialog"]').click()
                page.locator("#more-tools summary").click()
                expect(page.locator('[data-tool="freehand"]')).to_be_visible()
                page.keyboard.press("Escape")
                expect(page.locator('[data-tool="freehand"]')).to_be_hidden()
                page.locator(".view-popover summary").click()
                expect(page.locator("#reset-button")).to_be_visible()
                page.keyboard.press("Escape")
                passed("activity dialog and toolbar/view popovers remain usable")

                image_url = "data:image/png;base64," + base64.b64encode(
                    (ROOT / "examples/room_demo/reference.png").read_bytes()).decode()
                store.set_reference_clip(session["session_id"], {"name": "隔离测试片段", "fps": 2,
                    "frames": [{"name": "001.png", "data_url": image_url, "time_sec": 0},
                               {"name": "002.png", "data_url": image_url, "time_sec": .5}]})
                clip = store.set_reference_clip(session["session_id"], {"name": "同步侧面机位", "fps": 2,
                    "append_view": True,
                    "frames": [{"name": "side-001.png", "data_url": image_url, "time_sec": 0},
                               {"name": "side-002.png", "data_url": image_url, "time_sec": .5}]})["reference_clip"]
                page.reload()
                wait_ready(page)
                expect(page.locator("#timeline-panel")).to_be_visible()
                page.locator("#chat-collapse").click()
                page.locator(".timeline-options summary").click()
                expect(page.locator("#feedback-scope")).to_be_visible()
                page.locator("#feedback-scope").select_option("range")
                expect(page.locator("#range-start")).to_be_visible()
                page.keyboard.press("Escape")
                page.locator("#timeline-next").click()
                expect(page.locator("#timeline-time")).not_to_have_text("0.000 s")
                passed("dynamic timeline, frame navigation and range popover remain operable")

                expect(page.locator("#reference-view-select")).to_be_visible()
                before_view_time = page.locator("#timeline-seek").input_value()
                secondary_id = clip["views"][0]["clip_id"]
                page.locator("#reference-view-select").select_option(secondary_id)
                expect(page.locator("#reference-view-select")).to_have_value(secondary_id)
                expect(page.locator("#reference-title")).to_have_text("side-002.png")
                expect(page.locator("#compare-toggle")).to_have_attribute("aria-pressed", "false")
                expect(page.locator("#compare-image")).to_be_hidden()
                assert page.locator("#timeline-seek").input_value() == before_view_time
                page.reload()
                wait_ready(page)
                expect(page.locator("#reference-view-select")).to_have_value(secondary_id)
                expect(page.locator("#compare-toggle")).to_have_attribute("aria-pressed", "false")
                expect(page.locator("#compare-opacity")).to_have_value("75")
                expect(page.locator("#compare-image")).to_be_hidden()
                assert page.locator("#timeline-seek").input_value() == before_view_time
                passed("synchronized reference views preserve active time, selected camera and disabled overlay across reload")

                for width, height in [(1280, 720), (768, 900), (390, 844), (340, 844)]:
                    page.set_viewport_size({"width": width, "height": height})
                    page.wait_for_timeout(350)
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    expect(page.locator("#projects-dialog-button")).to_be_visible()
                    expect(page.locator("#human-pose-panel summary")).to_be_visible()
                    page.locator("#human-pose-panel summary").click()
                    expect(page.locator("#human-pose-import")).to_be_enabled()
                    expect(page.locator("#human-pose-run, #human-pose-draw")).to_have_count(0)
                    pose_box = page.locator(".human-pose-controls").bounding_box()
                    assert pose_box["x"] >= -1 and pose_box["x"] + pose_box["width"] <= width + 1, pose_box
                    assert pose_box["y"] >= -1 and pose_box["y"] + pose_box["height"] <= height + 1, pose_box
                    page.keyboard.press("Escape")
                    page.locator("#projects-dialog-button").click()
                    expect(page.locator("#projects-dialog")).to_be_visible()
                    expect(page.locator("#project-list .project-item")).to_have_count(1)
                    page.locator("#close-projects").click()
                    page.locator("#chat-launcher").click()
                    expect(page.locator("#feedback-note")).to_be_visible()
                    expect(page.locator("#submit-button")).to_be_visible()
                    dock = page.locator("#chat-dock").bounding_box()
                    assert dock["x"] >= -1 and dock["x"] + dock["width"] <= width + 1, dock
                    assert dock["y"] >= -1 and dock["y"] + dock["height"] <= height + 1, dock
                    if screenshots:
                        page.screenshot(path=str(screenshots / f"viewport-{width}.png"))
                    page.locator("#chat-collapse").click()
                    assert_launcher(page)
                passed("desktop, tablet and narrow mobile retain project switch, pose popover, multiview and dock controls without overflow")
                verify_compact_header(page, store, screenshots)
                passed("long unread counts fit the shared header at narrow widths and resize grip leaves header buttons clickable")
                verify_project_isolation(page, server, screenshots)
                passed("real project creation and switching isolate scene assets, saved feedback, chat, drafts and collapsed preferences with mocked Codex I/O")
                assert not pose_requests, "Browsing the passive result panel must never POST inference, import or cancellation"
                assert not errors, errors
                assert server.workspace_gateway.adapter is None
                assert server.server_port != 18769
                passed("no browser runtime errors and no model or production server access")
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
    print(json.dumps({"passed": len(results), "checks": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()

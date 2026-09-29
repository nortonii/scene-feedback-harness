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
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "examples" / "room_demo"))

from build_scene import build  # noqa: E402
from core import SceneStore  # noqa: E402
from server import make_server  # noqa: E402
from playwright.sync_api import expect, sync_playwright  # noqa: E402


def draft(page):
    return page.evaluate("""() => {
      const key = Object.keys(localStorage).find(k => k.startsWith('astra-visual-draft:'));
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

                for width, height in [(1280, 720), (390, 844)]:
                    page.set_viewport_size({"width": width, "height": height})
                    page.wait_for_timeout(350)
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
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
                passed("compact desktop and mobile keep dock controls within viewport without horizontal overflow")
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

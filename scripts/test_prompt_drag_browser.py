#!/usr/bin/env python3
"""Exercise real prompt drags against an isolated Scene Feedback workbench.

This starts a temporary MCP-event workspace, synthetic images and a tiny local
GLB. It neither contacts a Codex model nor changes a live project.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]


def image_data(color: str) -> str:
    from PIL import Image

    stream = io.BytesIO()
    Image.new("RGB", (320, 240), color).save(stream, "PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode("ascii")


def tiny_named_glb(path: Path) -> None:
    """A real glTF node tree lets the viewer resolve an item and a part."""
    document = {"asset": {"version": "2.0"}, "scene": 0,
                "scenes": [{"nodes": [0]}],
                "nodes": [{"name": "Cabinet", "children": [1]}, {"name": "Door"}]}
    encoded = json.dumps(document, separators=(",", ":")).encode()
    encoded += b" " * (-len(encoded) % 4)
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 20 + len(encoded)) +
                     struct.pack("<I4s", len(encoded), b"JSON") + encoded)


def drag_to_note(page, source, *, note_contains: str | None = None) -> None:
    """Use Chromium mouse down/move/up; synthetic DragEvent dispatch is invalid."""
    note = page.locator("#feedback-note")
    source.scroll_into_view_if_needed()
    note.scroll_into_view_if_needed()
    start = source.bounding_box()
    end = note.bounding_box()
    assert start and end, "drag source and textarea must have visible bounds"
    before = page.evaluate("__promptDragCheck.trace.length")
    page.mouse.move(start["x"] + start["width"] / 2, start["y"] + start["height"] / 2)
    page.mouse.down()
    page.mouse.move(end["x"] + min(60, end["width"] / 3),
                    end["y"] + end["height"] / 2, steps=18)
    page.mouse.up()
    try:
        page.wait_for_function("(before) => __promptDragCheck.trace.slice(before).some(e => e.type === 'drop' && e.target === 'feedback-note')", arg=before, timeout=5000)
    except Exception as exc:
        diagnostic = page.evaluate("(before) => ({events:__promptDragCheck.trace.slice(before),note:document.getElementById('feedback-note').value})", before)
        raise AssertionError(f"native drag did not reach prompt: {diagnostic}; source={source.get_attribute('outerHTML')}; start={start}; end={end}") from exc
    trace = page.evaluate("(before) => __promptDragCheck.trace.slice(before)", before)
    assert any(event["type"] == "dragstart" for event in trace), trace
    if note_contains:
        assert note_contains in note.input_value(), note.input_value()


def image_refs(page) -> list[dict]:
    return page.evaluate("structuredClone(__promptDragCheck.state.imageRefs)")


def wait_ready(page) -> None:
    page.wait_for_function("window.__promptDragCheck && __promptDragCheck.state.workspaceReady && !__promptDragCheck.state.sceneLoading && document.getElementById('reference-image').naturalWidth > 0")


def open_references(page) -> None:
    if page.locator("#chat-launcher").is_visible():
        page.locator("#chat-launcher").click()
    if not page.locator("#references-dialog").is_visible():
        page.locator("#references-dialog-button").click()
    page.wait_for_function("document.getElementById('references-dialog').open")


def close_references(page) -> None:
    if page.locator("#references-dialog").is_visible():
        page.locator('[data-close-dialog="references-dialog"]').click()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-executable", help="Existing Chromium binary")
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright
    from server import make_server

    errors: list[str] = []
    feedback_responses: list[tuple[int, str]] = []
    with tempfile.TemporaryDirectory(prefix="prompt-drag-browser-") as temporary:
        root = Path(temporary)
        project = root / "project"
        project.mkdir()
        server = make_server(port=0, data_dir=root / "data", project_dir=project,
                             web_dir=ROOT / "web", external_review=True,
                             feedback_transport="mcp_events")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        store = server.scene_store
        try:
            model_path = root / "fixture.glb"
            tiny_named_glb(model_path)
            imported = store.import_model(str(model_path), object_id="fixture_model", name="Model cabinet")
            store.update_scene(imported["scene_revision"], [{"op": "add", "object": {
                "id": "fixture_box", "name": "Box fixture", "type": "box",
                "position": [1.5, 0, 0], "size": [.5, .5, .5], "color": "#aa6644"}}])
            workspace = server.workspace_gateway.ensure()
            session = workspace["session_id"]
            primary = store.set_reference_clip(session, {"name": "front", "fps": 2, "frames": [
                {"name": f"front-{index}.png", "time_sec": index * .5,
                 "data_url": image_data("#d76453" if index == 0 else "#cb8846")}
                for index in range(2)]})["reference_clip"]
            appended = store.set_reference_clip(session, {"append_view": True, "name": "side", "fps": 2,
                "frames": [{"name": f"side-{index}.png", "time_sec": index * .5,
                            "data_url": image_data("#638dc4" if index == 0 else "#527b9c")}
                           for index in range(2)]})["reference_clip"]
            secondary = appended["views"][0]
            base = f"http://127.0.0.1:{server.server_port}/p/{workspace['project_id']}"

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    **({"executable_path": args.browser_executable} if args.browser_executable else {}),
                    headless=True,
                    args=["--no-sandbox", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
                context = browser.new_context(viewport={"width": 1440, "height": 950})
                hook = """
window.__promptDragCheck = {state, ui, cameraData, selectObject, nodeReference, trace: []};
for (const type of ['dragstart','dragover','drop']) document.addEventListener(type, event => {
  if (type === 'dragover' && event.target?.id !== 'feedback-note') return;
  __promptDragCheck.trace.push({type, target:event.target?.id || event.target?.className || ''});
}, true);
__promptDragCheck.selectModelPart = () => {
  const root = state.objectNodes.get('fixture_model')?.userData.gltfRoot;
  const part = root?.children[0]?.children[0];
  if (!part) return null;
  const reference = nodeReference('fixture_model', part, 'part');
  if (reference) selectObject('fixture_model', reference, part);
  return reference;
};
"""
                context.route("**/app.js", lambda route: route.fulfill(
                    status=200, content_type="application/javascript",
                    body=(ROOT / "web/app.js").read_text() + "\n" + hook))
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("response", lambda response: feedback_responses.append((response.status, response.text()[:600]))
                        if "/feedback" in response.url and response.request.method == "POST" else None)
                page.goto(base + "/")
                wait_ready(page)
                assert not errors, errors

                # Capture two different camera images into one ordinary prompt.
                page.locator("#reference-view-select").select_option(primary["clip_id"])
                page.locator("#timeline-seek").focus()
                page.locator("#timeline-seek").press("Home")
                page.wait_for_function("(id) => __promptDragCheck.state.activeReferenceId === id && !__promptDragCheck.state.seeking",
                                       arg=primary["frames"][0]["id"])
                drag_to_note(page, page.locator("#drag-reference-image"), note_contains="[[image:")
                page.wait_for_function("__promptDragCheck.state.imageRefs.length === 1")
                first_image = image_refs(page)[0]
                assert first_image.get("reference_id") == primary["frames"][0]["id"], first_image

                page.locator("#reference-view-select").select_option(secondary["clip_id"])
                page.locator("#timeline-seek").focus()
                page.locator("#timeline-seek").press("End")
                page.wait_for_function("(id) => __promptDragCheck.state.activeReferenceId === id && !__promptDragCheck.state.seeking",
                                       arg=secondary["frames"][-1]["id"])
                assert image_refs(page)[0] == first_image, "first image must stay bound to its source after a camera switch and seek"
                drag_to_note(page, page.locator("#drag-reference-image"))
                page.wait_for_function("__promptDragCheck.state.imageRefs.length === 2")
                second_image = image_refs(page)[1]
                assert second_image.get("reference_id") == secondary["frames"][-1]["id"], second_image
                assert len(page.locator("#prompt-image-refs .prompt-image-chip").all()) == 2
                print("PASS: real drags add two exact-frame reference images to one prompt; first stays frozen after seek", flush=True)

                # Drag a rendered scene screenshot, then orbit the live viewer.
                drag_to_note(page, page.locator("#drag-scene-image"))
                page.wait_for_function("__promptDragCheck.state.imageRefs.length === 3")
                scene_image = image_refs(page)[-1]
                assert scene_image.get("pane") == "scene", scene_image
                camera_before = page.evaluate("__promptDragCheck.cameraData()")
                bounds = page.locator("#viewport").bounding_box()
                assert bounds
                page.mouse.move(bounds["x"] + bounds["width"] * .4, bounds["y"] + bounds["height"] * .5)
                page.mouse.down()
                page.mouse.move(bounds["x"] + bounds["width"] * .65, bounds["y"] + bounds["height"] * .65, steps=12)
                page.mouse.up()
                page.wait_for_timeout(250)
                camera_after = page.evaluate("__promptDragCheck.cameraData()")
                assert camera_after != camera_before, "test orbit gesture did not move the live camera"
                assert image_refs(page)[-1] == scene_image, "dragged scene screenshot changed after camera rotation"
                old_revision = scene_image["scene_revision"]
                store.update_scene(store.scene()["revision"], [{"op": "update", "object_id": "fixture_box",
                                                                  "fields": {"size": [.6, .5, .5]}}])
                page.wait_for_function("(revision) => __promptDragCheck.state.sceneRevision === revision + 1 && !__promptDragCheck.state.sceneLoading", arg=old_revision)
                assert image_refs(page)[-1] == scene_image, "old scene screenshot changed when a newer revision arrived"
                print("PASS: scene image drag keeps its original screenshot, camera and version after orbit and revision update", flush=True)

                # Item and selected GLB part are separate drag sources.
                open_references(page)
                item = page.locator('#object-list .object-item[data-object-id="fixture_box"]')
                assert item.get_attribute("data-prompt-drag") == "object"
                drag_to_note(page, item, note_contains="[[object:fixture_box]]")
                close_references(page)
                part = page.evaluate("__promptDragCheck.selectModelPart()")
                assert part and part["parent_object_id"] == "fixture_model" and len(part["node_path"]) >= 2, part
                drag_to_note(page, page.locator("#selected-chip"), note_contains="[[node:fixture_model:")
                print("PASS: item and actual GLB part drag into the same prompt", flush=True)

                # Draw a real reference rectangle and drag its saved list row.
                page.locator('button[data-tool="rectangle"]').click()
                canvas = page.locator("#reference-annotations")
                box = canvas.bounding_box()
                assert box
                page.mouse.move(box["x"] + box["width"] * .2, box["y"] + box["height"] * .2)
                page.mouse.down()
                page.mouse.move(box["x"] + box["width"] * .4, box["y"] + box["height"] * .55, steps=10)
                page.mouse.up()
                page.wait_for_function("__promptDragCheck.state.annotations.length === 1")
                open_references(page)
                annotation = page.locator('#annotation-list .annotation-copy[data-prompt-drag="annotation"]')
                assert annotation.count() == 1
                drag_to_note(page, annotation, note_contains="[[annotation:")
                close_references(page)
                note_before = page.locator("#feedback-note").input_value()
                images_before = image_refs(page)
                assert note_before.count("[[image:") == 3, note_before
                print("PASS: saved annotation drag joins images, object and part in one freeform prompt", flush=True)

                page.reload()
                wait_ready(page)
                page.wait_for_function("__promptDragCheck.state.imageRefs.length === 3")
                assert page.locator("#feedback-note").input_value() == note_before
                assert image_refs(page) == images_before
                assert len(page.locator("#prompt-image-refs .prompt-image-chip").all()) == 3
                print("PASS: reload restores the same prompt and original image captures", flush=True)

                for width in (390, 340):
                    page.set_viewport_size({"width": width, "height": 920})
                    page.wait_for_timeout(80)
                    metrics = page.evaluate("() => ({body:document.documentElement.scrollWidth, viewport:innerWidth, chips:document.getElementById('prompt-image-refs').scrollWidth, chipsClient:document.getElementById('prompt-image-refs').clientWidth})")
                    assert metrics["body"] <= metrics["viewport"], (width, metrics)
                    for selector in ("#drag-reference-image", "#drag-scene-image"):
                        box = page.locator(selector).bounding_box()
                        assert box and box["x"] >= -1 and box["x"] + box["width"] <= width + 1, (width, selector, box)
                        assert page.locator(selector).is_enabled(), (width, selector)
                    assert metrics["chips"] <= metrics["chipsClient"] + 1, (width, metrics)
                page.set_viewport_size({"width": 1440, "height": 950})
                print("PASS: 390/340px layouts keep both image handles and multiple chips within the viewport", flush=True)

                # Rejected save preserves the entire draft. An accepted save
                # clears it after the isolated MCP-event store acknowledges it.
                intercepted = []

                def reject_feedback(route):
                    intercepted.append(route.request.post_data_json)
                    route.fulfill(status=422, content_type="application/json",
                                  body=json.dumps({"error": "synthetic browser rejection"}))

                page.route("**/api/sessions/*/feedback", reject_feedback)
                page.locator("#submit-button").click()
                page.wait_for_function("!__promptDragCheck.state.submitting && document.getElementById('feedback-note').value.includes('[[annotation:')")
                assert intercepted, "failure route did not intercept feedback"
                assert page.locator("#feedback-note").input_value() == note_before
                assert image_refs(page) == images_before
                assert not store.state["feedback"]
                page.unroute("**/api/sessions/*/feedback", reject_feedback)
                print("PASS: failed send retains prompt and all image evidence", flush=True)

                # An older server may omit this capability. It must never
                # accept a text-only retry that silently drops image evidence.
                def no_image_support(route):
                    response = route.fetch()
                    body = response.json()
                    body.pop("image_references_supported", None)
                    route.fulfill(response=response, content_type="application/json", body=json.dumps(body))

                blocked_posts = []

                def unexpected_post(route):
                    blocked_posts.append(route.request.post_data_json)
                    route.fulfill(status=503, content_type="application/json", body='{"error":"unexpected old-server POST"}')

                page.route("**/api/workspace/state*", no_image_support)
                page.route("**/api/sessions/*/feedback", unexpected_post)
                page.reload()
                wait_ready(page)
                page.wait_for_function("__promptDragCheck.state.imageReferencesSupported === false")
                assert page.locator("#drag-reference-image").is_disabled()
                assert page.locator("#drag-scene-image").is_disabled()
                if page.locator("#submit-button").is_enabled():
                    page.locator("#submit-button").click()
                    page.wait_for_timeout(150)
                assert not blocked_posts, "old-server capability fallback posted image refs"
                assert page.locator("#feedback-note").input_value() == note_before
                assert image_refs(page) == images_before
                page.unroute("**/api/workspace/state*", no_image_support)
                page.unroute("**/api/sessions/*/feedback", unexpected_post)
                page.reload()
                wait_ready(page)
                page.wait_for_function("__promptDragCheck.state.imageReferencesSupported === true && __promptDragCheck.state.imageRefs.length === 3")
                print("PASS: absent old-server image capability disables capture and blocks a lossy POST", flush=True)

                # A network failure creates a persisted outbox packet; retry
                # must also be blocked if the next server lacks image support.
                def network_failure(route):
                    route.fulfill(status=503, content_type="application/json", body='{"error":"synthetic transport failure"}')

                page.route("**/api/sessions/*/feedback", network_failure)
                page.locator("#submit-button").click()
                page.wait_for_function("__promptDragCheck.state.pendingSubmission && !__promptDragCheck.state.submitting")
                assert image_refs(page) == images_before
                page.unroute("**/api/sessions/*/feedback", network_failure)
                blocked_posts.clear()
                page.route("**/api/workspace/state*", no_image_support)
                page.route("**/api/sessions/*/feedback", unexpected_post)
                page.reload()
                wait_ready(page)
                page.wait_for_function("__promptDragCheck.state.imageReferencesSupported === false && !!__promptDragCheck.state.pendingSubmission")
                if page.locator("#submit-button").is_enabled():
                    page.locator("#submit-button").click()
                    page.wait_for_timeout(150)
                assert not blocked_posts, "old server retried an image-bearing outbox as text-only feedback"
                page.unroute("**/api/workspace/state*", no_image_support)
                page.unroute("**/api/sessions/*/feedback", unexpected_post)
                page.reload()
                wait_ready(page)
                page.wait_for_function("__promptDragCheck.state.imageReferencesSupported === true && !!__promptDragCheck.state.pendingSubmission")
                print("PASS: old-server fallback also blocks an image-bearing outbox retry", flush=True)

                page.locator("#submit-button").click()
                try:
                    page.wait_for_function("__promptDragCheck.state.imageRefs.length === 0 && document.getElementById('feedback-note').value === ''", timeout=10000)
                except Exception as exc:
                    diagnostic = page.evaluate("() => ({toast:document.getElementById('toast').textContent, caption:document.getElementById('submit-caption').textContent, note:document.getElementById('feedback-note').value, refs:__promptDragCheck.state.imageRefs.length, pending:!!__promptDragCheck.state.pendingSubmission})")
                    sent_images = [{key: item.get(key) for key in ("pane", "reference_id", "clip_id", "view_id", "time_sec")}
                                   for item in intercepted[0].get("image_refs", [])] if intercepted else []
                    expected_clip = {"clip_id": appended["clip_id"], "views": [appended["clip_id"], secondary["clip_id"]]}
                    raise AssertionError(f"successful send did not clear: {diagnostic}; sent_images={sent_images}; expected_clip={expected_clip}; responses={feedback_responses}; errors={errors}") from exc
                packets = store.state["feedback"]
                packet = list(packets.values())[-1] if isinstance(packets, dict) else packets[-1]
                assert len(packet["image_refs"]) == 3, packet.get("image_refs")
                assert {item["pane"] for item in packet["image_refs"]} == {"reference", "scene"}
                assert packet["image_refs"][0]["reference_id"] == primary["frames"][0]["id"]
                assert packet["image_refs"][1]["reference_id"] == secondary["frames"][-1]["id"]
                assert len(packet["annotations"]) == 1
                assert "[[object:fixture_box]]" in packet["note"] and "[[node:fixture_model:" in packet["note"]
                assert not errors, errors
                print("PASS: successful MCP-event feedback saves three bound images and clears the accepted draft", flush=True)

                # The GLB can be replaced after a drag begins, before drop.
                # Its old node path must not silently bind to the new model.
                page.wait_for_function("__promptDragCheck.state.workspaceReady && !__promptDragCheck.state.submitting && !__promptDragCheck.state.sceneLoading && !__promptDragCheck.state.pendingSubmission")
                selected = page.evaluate("__promptDragCheck.selectModelPart()")
                assert selected and selected["node_path"]
                source = page.locator("#selected-chip")
                note = page.locator("#feedback-note")
                assert source.is_visible(), page.evaluate("({selected:__promptDragCheck.state.selectedId,sceneNode:__promptDragCheck.state.selectedSceneNode,status:__promptDragCheck.state.sessionStatus})")
                source.scroll_into_view_if_needed(); note.scroll_into_view_if_needed()
                start, end = source.bounding_box(), note.bounding_box()
                assert start and end
                trace_at = page.evaluate("__promptDragCheck.trace.length")
                original_url = page.evaluate("__promptDragCheck.state.sceneObjects.find(item=>item.id==='fixture_model').url")
                sx, sy = start["x"] + start["width"] / 2, start["y"] + start["height"] / 2
                page.mouse.move(sx, sy)
                page.mouse.down()
                page.mouse.move(sx + 16, sy + 9, steps=8)
                page.wait_for_function("(start) => __promptDragCheck.trace.slice(start).some(e=>e.type==='dragstart')", arg=trace_at)
                page.evaluate("__promptDragCheck.state.sceneObjects.find(item=>item.id==='fixture_model').url='/assets/replaced-model.glb'")
                page.mouse.move(end["x"] + min(60, end["width"] / 3), end["y"] + end["height"] / 2, steps=18)
                page.mouse.up()
                page.wait_for_function("(start) => __promptDragCheck.trace.slice(start).some(e=>e.type==='drop')", arg=trace_at)
                assert "[[node:fixture_model:" not in note.input_value(), note.input_value()
                assert "引用已失效" in page.locator("#toast").inner_text()
                page.evaluate("url => {__promptDragCheck.state.sceneObjects.find(item=>item.id==='fixture_model').url=url}", original_url)
                print("PASS: a model replacement during a real node drag rejects the old part reference", flush=True)

                # A right-pane arrow freezes a scene screenshot. Dragging that
                # view must carry the exact original plus its painted version.
                page.locator('button[data-tool="arrow"]').click()
                viewport = page.locator("#viewport")
                box = viewport.bounding_box()
                assert box
                page.mouse.move(box["x"] + box["width"] * .22, box["y"] + box["height"] * .28)
                page.mouse.down()
                page.mouse.move(box["x"] + box["width"] * .56, box["y"] + box["height"] * .51, steps=12)
                page.mouse.up()
                page.wait_for_function("__promptDragCheck.state.annotations.some(mark=>mark.pane==='scene' && mark.type==='arrow') && __promptDragCheck.state.snapshot?.data_url && document.getElementById('scene-snapshot-image').naturalWidth > 0")
                snapshot = page.evaluate("structuredClone(__promptDragCheck.state.snapshot)")
                drag_to_note(page, page.locator("#drag-scene-image"), note_contains="[[image:")
                page.wait_for_function("__promptDragCheck.state.imageRefs.length === 1")
                marked = image_refs(page)[0]
                assert marked["pane"] == "scene" and marked["original_data_url"] == snapshot["data_url"]
                assert marked["camera"] == snapshot["camera"] and marked["scene_revision"] == snapshot["scene_revision"]
                assert marked.get("annotated_data_url") and marked["annotated_data_url"] != marked["original_data_url"]
                page.locator('button[data-tool="select"]').click()
                view_box = viewport.bounding_box()
                assert view_box
                page.mouse.move(view_box["x"] + view_box["width"] * .4, view_box["y"] + view_box["height"] * .5)
                page.mouse.down()
                page.mouse.move(view_box["x"] + view_box["width"] * .7, view_box["y"] + view_box["height"] * .55, steps=10)
                page.mouse.up()
                assert image_refs(page)[0] == marked
                page.locator("#prompt-image-refs .prompt-image-preview").click()
                preview = page.locator("#prompt-image-preview-dialog")
                assert preview.is_visible()
                assert page.locator("#prompt-image-preview-image").get_attribute("src") == marked["annotated_data_url"]
                page.locator("#prompt-image-preview-toggle").click()
                assert page.locator("#prompt-image-preview-image").get_attribute("src") == marked["original_data_url"]
                page.locator("#prompt-image-preview-toggle").click()
                assert page.locator("#prompt-image-preview-image").get_attribute("src") == marked["annotated_data_url"]
                page.locator("#prompt-image-preview-close").click()
                print("PASS: a real scene arrow drags its exact frozen original and annotated screenshot; preview toggles both after orbit", flush=True)
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
    print("ALL PROMPT DRAG BROWSER CHECKS PASSED", flush=True)


if __name__ == "__main__":
    main()

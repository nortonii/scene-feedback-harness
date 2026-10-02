"""Browser checks for selecting, naming and quoting marks on saved images.

Called by focus_workspace_smoke.py with its isolated, editable room fixture.
All pointer interactions use the rendered canvas; no model task is submitted.
"""

from __future__ import annotations

import re

from playwright.sync_api import expect
from workspace_ui_helpers import control, open_annotation_tools, choose_tool


def verify_annotation_selection(page, screenshots):
    # Import lazily so the smoke suite can import this helper itself.
    from focus_workspace_smoke import draft, wait_ready

    scene = "#scene-annotations"
    reference = "#reference-annotations"
    note = page.locator("#feedback-note")
    browse = page.locator("#browse-button")
    dock = page.locator("#chat-dock")

    def tool(name):
        control(page, f'button[data-tool="{name}"]').click()

    def position(selector, x, y):
        box = page.locator(selector).bounding_box()
        assert box and box["width"] > 0 and box["height"] > 0, selector
        return box["x"] + box["width"] * x, box["y"] + box["height"] * y

    def click(selector, x, y):
        page.mouse.click(*position(selector, x, y))

    def marks():
        return draft(page)["annotations"]

    def selected(selector, annotation_id):
        expect(page.locator(selector)).to_have_attribute("data-selected-annotation", annotation_id)

    def count(number):
        expect(page.locator("#annotation-count")).to_have_text(str(number))
        assert len(marks()) == number

    def snapshot_is(snapshot):
        expect(page.locator("#scene-snapshot-media")).to_be_visible()
        expect(browse).to_have_attribute("aria-pressed", "false")
        assert draft(page)["snapshot"] == snapshot

    def settle_chat():
        page.wait_for_function("""() =>
          !document.getElementById('chat-dock').getAnimations({subtree:true})
            .some(animation => animation.playState === 'running')
        """)

    def collapse_chat():
        if dock.is_visible():
            control(page, "#chat-collapse").click()
        expect(dock).to_be_hidden()

    def open_chat():
        if not dock.is_visible():
            control(page, "#chat-launcher").click()
        expect(note).to_be_visible()
        settle_chat()

    def drag_to_chat(selector, point, target, *, expect_auto_open=False):
        start_x, start_y = position(selector, *point)
        page.mouse.move(start_x, start_y)
        page.mouse.down()
        # Cross the drag threshold while still over the image, so auto-open
        # cannot be satisfied by clicking the launcher or the composer.
        page.mouse.move(start_x + 18, start_y + 12, steps=3)
        if expect_auto_open:
            expect(note).to_be_visible()
        settle_chat()
        destination = position(target, .5, .5 if target == "#feedback-note" else .15)
        page.mouse.move(*destination, steps=12)
        page.mouse.up()

    def point_number(mark):
        match = re.fullmatch(r"点\s*(\d+)", mark.get("name", ""))
        assert match, mark
        return int(match[1])

    assert not marks(), "Selection checks require an initially empty visual draft"
    note.fill("")
    collapse_chat()
    control(page, "#capture-scene-button").click()
    first_snapshot = draft(page)["snapshot"]
    first_pixels = page.locator("#scene-snapshot-image").get_attribute("src")
    scene_point_at = (.52, .42)
    reference_point_at = (.5, .48)

    tool("point")
    click(scene, *scene_point_at)
    first_point = marks()[-1]
    click(reference, *reference_point_at)
    reference_point = marks()[-1]
    assert point_number(reference_point) == point_number(first_point) + 1
    tool("rectangle")
    page.mouse.move(*position(scene, .25, .55))
    page.mouse.down()
    page.mouse.move(*position(scene, .5, .72), steps=5)
    page.mouse.up()
    rectangle = marks()[-1]
    assert rectangle.get("name"), rectangle
    original_marks = marks()
    assert len({mark["name"] for mark in original_marks}) == 3
    count(3)

    # The selection arrow chooses ink on the existing screenshot. Only the
    # explicit browse control changes to live 3D.
    tool("select")
    snapshot_is(first_snapshot)
    assert page.locator("#scene-snapshot-image").get_attribute("src") == first_pixels
    click(scene, *scene_point_at)
    selected(scene, first_point["id"])
    click(scene, .375, .55)
    selected(scene, rectangle["id"])
    click(scene, .375, .635)
    selected(scene, "")
    click(reference, *reference_point_at)
    selected(reference, reference_point["id"])
    click(scene, *scene_point_at)
    selected(scene, first_point["id"])
    if screenshots:
        page.screenshot(path=str(screenshots / "selected-named-annotation.png"))

    page.keyboard.press("Backspace")
    count(2)
    assert {mark["id"] for mark in marks()} == {reference_point["id"], rectangle["id"]}
    snapshot_is(first_snapshot)
    control(page, "#undo-annotation").click()
    assert marks() == original_marks, "Undo must restore both evidence and its readable name"

    # Backspace without a selected mark must neither delete the last mark nor
    # navigate away; typing in the composer retains native editing behavior.
    click(scene, .84, .48)
    selected(scene, "")
    previous_url = page.url
    page.keyboard.press("Backspace")
    assert page.url == previous_url and marks() == original_marks
    click(scene, *scene_point_at)
    selected(scene, first_point["id"])
    open_chat()
    note.fill("edit XY")
    note.press("End")
    note.press("Backspace")
    expect(note).to_have_value("edit X")
    note.press("Home")
    note.press("Delete")
    expect(note).to_have_value("dit X")
    assert marks() == original_marks
    note.fill("")

    collapse_chat()
    drag_to_chat(scene, scene_point_at, "#feedback-note", expect_auto_open=True)
    expect(note).to_have_value(re.compile(re.escape(first_point["name"])))
    expect(note).to_have_value(re.compile(re.escape(f'[[annotation:{first_point["id"]}]]')))
    assert marks() == original_marks, "Referencing a mark must not move or mutate it"
    snapshot_is(first_snapshot)
    drag_to_chat(reference, reference_point_at, "#chat-dock")
    expect(note).to_have_value(re.compile(re.escape(reference_point["name"])))
    expect(note).to_have_value(re.compile(re.escape(f'[[annotation:{reference_point["id"]}]]')))
    assert marks() == original_marks
    if screenshots:
        page.screenshot(path=str(screenshots / "dragged-annotation-references.png"))

    # Escape cancels an in-flight drag even if the pointer is subsequently
    # released over the valid drop target.
    note.fill("保留这段文字")
    start_x, start_y = position(reference, *reference_point_at)
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    page.mouse.move(start_x + 24, start_y + 12, steps=3)
    page.keyboard.press("Escape")
    page.mouse.move(*position("#feedback-note", .5, .5), steps=8)
    page.mouse.up()
    expect(note).to_have_value("保留这段文字")
    assert marks() == original_marks
    snapshot_is(first_snapshot)
    note.fill("")

    # A mark at the same position on another screenshot gets a distinct name
    # and selection identity. Deleting it cannot affect the first screenshot.
    collapse_chat()
    browse.click()
    expect(page.locator("#scene-snapshot-media")).to_be_hidden()
    expect(browse).to_have_attribute("aria-pressed", "true")
    control(page, "#capture-scene-button").click()
    second_snapshot = draft(page)["snapshot"]
    assert second_snapshot["id"] != first_snapshot["id"]
    tool("point")
    click(scene, *scene_point_at)
    second_point = marks()[-1]
    assert point_number(second_point) == point_number(reference_point) + 1
    tool("select")
    click(scene, *scene_point_at)
    selected(scene, second_point["id"])
    page.keyboard.press("Delete")
    count(3)
    assert marks() == original_marks
    snapshot_is(second_snapshot)

    # Persist the high-water mark before creating a replacement, including
    # when the highest-numbered annotation no longer exists after a reload.
    page.reload()
    wait_ready(page)
    snapshot_is(second_snapshot)
    assert marks() == original_marks
    tool("point")
    click(scene, *scene_point_at)
    replacement = marks()[-1]
    assert point_number(replacement) == point_number(second_point) + 1
    assert replacement["name"] != second_point["name"]
    count(4)
    saved_marks = marks()
    page.locator(".snapshot-open").first.click()
    tool("select")
    click(scene, *scene_point_at)
    selected(scene, first_point["id"])
    snapshot_is(first_snapshot)
    page.reload()
    wait_ready(page)
    assert marks() == saved_marks, "Names and coordinates must survive refresh unchanged"
    tool("select")
    click(scene, *scene_point_at)
    selected(scene, first_point["id"])
    page.locator(".snapshot-open").last.click()
    click(scene, *scene_point_at)
    selected(scene, replacement["id"])
    snapshot_is(second_snapshot)

    # Leave the shared fixture clean for subsequent checks.
    control(page, "#clear-annotations").click()
    control(page, "#confirm-clear-annotations").click()
    count(0)
    browse.click()
    open_chat()
    note.fill("")
    expect(page.locator("#scene-snapshot-media")).to_be_hidden()

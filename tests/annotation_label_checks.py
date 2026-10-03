"""Browser checks for hiding names while retaining editable screenshot marks."""

from __future__ import annotations

import re

from playwright.sync_api import expect
from workspace_ui_helpers import control, open_annotation_tools, choose_tool


def verify_annotation_label_visibility(page, screenshots):
    from focus_workspace_smoke import draft, wait_ready, canonical_note

    scene = "#scene-annotations"
    reference = "#reference-annotations"
    toggle = page.locator("#scene-labels-toggle")
    note = page.locator("#feedback-note")

    def marks():
        return draft(page)["annotations"]

    def tool(name):
        open_annotation_tools(page, "scene")
        if name in ("text", "freehand") and not page.locator(f'button[data-tool="{name}"]').is_visible():
            control(page, "#more-tools summary").click()
        control(page, f'button[data-tool="{name}"]').click()

    def position(selector, x, y):
        box = page.locator(selector).bounding_box()
        assert box and box["width"] and box["height"], selector
        return box["x"] + box["width"] * x, box["y"] + box["height"] * y

    def click(selector, x, y):
        page.mouse.click(*position(selector, x, y))

    def label_box(selector, mark):
        return page.evaluate("""async ({selector, mark}) => {
          const {annotationNameBox} = await import('/annotation-labels.js');
          const canvas = document.querySelector(selector);
          return annotationNameBox(canvas.getContext('2d'),mark,canvas.clientWidth,canvas.clientHeight);
        }""", {"selector": selector, "mark": mark})

    def label_position(selector, box):
        return page.locator(selector).evaluate("""(canvas, box) => ({
          x:(box.x + box.width * .8) / canvas.clientWidth,
          y:(box.y + box.height * .5) / canvas.clientHeight,
        })""", box)

    def pixels(selector, box):
        return page.locator(selector).evaluate("""(canvas, box) => {
          const sx = canvas.width / canvas.clientWidth, sy = canvas.height / canvas.clientHeight;
          const data = canvas.getContext('2d').getImageData(
            Math.ceil(box.x*sx),Math.ceil(box.y*sy),
            Math.max(1,Math.floor(box.width*sx)),Math.max(1,Math.floor(box.height*sy))).data;
          return Array.from(data);
        }""", box)

    def center_ink(selector, point):
        return page.locator(selector).evaluate("""(canvas, point) =>
          canvas.getContext('2d').getImageData(
            Math.round(canvas.width*point[0]),Math.round(canvas.height*point[1]),1,1).data[3]
        """, point)

    assert not marks(), "Label checks require an initially empty visual draft"
    expect(toggle).to_have_attribute("aria-pressed", "true")
    expect(toggle).to_be_disabled()
    control(page, "#chat-collapse").click()
    expect(page.locator("#chat-dock")).to_be_hidden()
    control(page, "#capture-scene-button").click()
    expect(toggle).to_be_enabled()
    first_snapshot = draft(page)["snapshot"]
    point_at = (.45, .43)
    tool("point")
    click(scene, *point_at)
    scene_mark = marks()[-1]
    choose_tool(page, "point", "reference")
    click(reference, .5, .48)
    reference_mark = marks()[-1]
    tool("text")
    click(scene, .2, .7)
    page.locator("#annotation-text").fill("保留文字内容 KEEP THIS TEXT")
    control(page, "#save-text").click()
    text_mark = marks()[-1]
    original = marks()
    scene_box = label_box(scene, scene_mark)
    reference_box = label_box(reference, reference_mark)
    # This part of the user's text is well to the right of its automatic name.
    text_box = page.locator(scene).evaluate("""canvas => ({
      x:canvas.clientWidth*.2+100,y:canvas.clientHeight*.7-14,width:80,height:16
    })""")
    reference_before = pixels(reference, reference_box)
    text_before = pixels(scene, text_box)
    assert any(pixels(scene, scene_box)[3::4]), "The name must be visible before hiding it"
    assert any(reference_before[3::4]) and any(text_before[3::4])

    toggle.click()
    expect(toggle).to_have_attribute("aria-pressed", "false")
    assert not any(pixels(scene, scene_box)[3::4]), "Hidden names must leave no canvas pixels"
    assert pixels(reference, reference_box) == reference_before, "Reference names must stay visible"
    assert pixels(scene, text_box) == text_before, "User-written annotation text must stay visible"
    assert center_ink(scene, point_at) > 0, "Hiding names must preserve the point itself"
    assert marks() == original and draft(page)["snapshot"] == first_snapshot
    for mark in original:
        expect(page.locator(f'.annotation-select[data-annotation-id="{mark["id"]}"]')).to_contain_text(mark["name"])

    # An invisible name is not a selectable or erasable target. Its actual ink
    # remains selectable, and deletion/undo preserves the full named evidence.
    tag_at = label_position(scene, scene_box)
    tool("select")
    click(scene, tag_at["x"], tag_at["y"])
    expect(page.locator(scene)).to_have_attribute("data-selected-annotation", "")
    tool("erase")
    click(scene, tag_at["x"], tag_at["y"])
    assert marks() == original, "Erasing a hidden name must not delete its point"
    tool("select")
    click(scene, *point_at)
    expect(page.locator(scene)).to_have_attribute("data-selected-annotation", scene_mark["id"])
    page.keyboard.press("Backspace")
    assert len(marks()) == 2 and all(mark["id"] != scene_mark["id"] for mark in marks())
    control(page, "#undo-annotation").click()
    assert marks() == original

    # Dragging visible ink still inserts the readable name and stable token.
    start = position(scene, *point_at)
    page.mouse.move(*start)
    page.mouse.down()
    page.mouse.move(start[0]+20, start[1]+12, steps=3)
    expect(note).to_be_visible()
    page.wait_for_function("""() =>
      !document.getElementById('chat-dock').getAnimations({subtree:true})
        .some(animation => animation.playState === 'running')
    """)
    page.mouse.move(*position("#feedback-note", .5, .5), steps=12)
    page.mouse.up()
    expect(note).to_have_value(re.compile(re.escape(scene_mark["name"])))
    assert "[[" not in note.input_value()
    assert f'[[annotation:{scene_mark["id"]}]]' in canonical_note(page)
    assert marks() == original
    note.fill("")
    control(page, "#chat-collapse").click()
    expect(page.locator("#chat-dock")).to_be_hidden()

    # The preference applies across snapshots and refresh, without rewriting
    # any name, coordinate, screenshot identity or user-written text.
    control(page, "#scene-live-card").click()
    expect(toggle).to_be_disabled()
    control(page, "#capture-scene-button").click()
    tool("point")
    click(scene, *point_at)
    second_mark = marks()[-1]
    saved_marks = marks()
    expect(toggle).to_have_attribute("aria-pressed", "false")
    assert not any(pixels(scene, label_box(scene, second_mark))[3::4])
    page.locator(".snapshot-card:not([data-kind=live]) .snapshot-open").first.click()
    assert draft(page)["snapshot"] == first_snapshot
    page.reload()
    wait_ready(page)
    expect(toggle).to_have_attribute("aria-pressed", "false")
    assert marks() == saved_marks and draft(page)["snapshot"] == first_snapshot
    assert not any(pixels(scene, label_box(scene, scene_mark))[3::4])
    assert center_ink(scene, point_at) > 0
    assert text_mark["text"] == next(mark for mark in marks() if mark["id"] == text_mark["id"])["text"]
    if screenshots:
        page.screenshot(path=str(screenshots / "screenshot-annotation-names-hidden.png"))

    toggle.click()
    expect(toggle).to_have_attribute("aria-pressed", "true")
    assert any(pixels(scene, label_box(scene, scene_mark))[3::4]), "Names should reappear immediately"
    control(page, "#clear-round").click()
    control(page, "#confirm-clear-annotations").click()
    expect(page.locator("#annotation-count")).to_have_text("0")
    control(page, "#scene-live-card").click()
    control(page, "#chat-launcher").click()
    expect(note).to_be_visible()
    note.fill("")

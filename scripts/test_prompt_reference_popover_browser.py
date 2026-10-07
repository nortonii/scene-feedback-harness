#!/usr/bin/env python3
"""Exercise compact click-only reference details in an isolated browser page."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
import threading

ROOT = Path(__file__).resolve().parents[1]
HTML = r'''<!doctype html><meta charset="utf-8">
<link rel="stylesheet" href="/prompt-reference-popover.css">
<style>
:root { --paper:#faf9f5; --ink:#252520; --line:#d9d9d3; }
body { margin:40px; background:#bdc8ce; }
button { font:13px sans-serif; }
textarea { display:block; margin-top:40px; box-sizing:border-box; width:650px; height:140px;
  padding:12px 15px; border:2px solid #777; resize:none; font:20px/30px monospace; }
#chip,#image-chip { padding:6px 10px; }
#edge { position:fixed; right:8px; bottom:8px; }
#stage { position:fixed; left:30px; bottom:50px; width:230px; height:160px; background:#597389; }
</style>
<button id="chip" aria-controls="prompt-reference-dialog" aria-expanded="false">🧊1</button>
<button id="image-chip">🖼️1</button><button id="edge">边界引用</button>
<textarea id="note"></textarea><div id="stage"></div>
<div id="prompt-reference-dialog" hidden role="dialog" aria-modal="false">
  <div class="dialog-head"><h2>🧊1 · 物体</h2><button class="icon-button" id="close-text">×</button></div>
  <p id="prompt-reference-detail">固定的物体、机位和帧号来自点击时的引用。</p>
  <p id="prompt-reference-status"></p>
  <button id="prompt-reference-locate" hidden>查看这个标记</button>
</div>
<div id="prompt-image-preview-dialog" hidden role="dialog" aria-modal="false">
  <div class="dialog-head"><h2>🖼️1 · 图片引用</h2><button class="icon-button" id="close-image">×</button></div>
  <button id="prompt-image-preview-toggle">查看原图</button>
  <img id="prompt-image-preview-image" alt="固定图片"
    src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='700' height='500'%3E%3Crect width='700' height='500' fill='%238aa3b6'/%3E%3C/svg%3E">
</div>
<script type="module">
import {createPromptReferencePopover} from './prompt-reference-popover.js';
import {createPromptReferenceText} from './prompt-reference-text.js';
import {createPromptReferenceHit} from './prompt-reference-hit.js';
const note=document.querySelector('#note'),text=document.querySelector('#prompt-reference-dialog');
const image=document.querySelector('#prompt-image-preview-dialog');
const codec=createPromptReferenceText({icons:true});codec.remember('Box','[[object:box]]');
note.value='Before 🧊1 gap 🧊1 after';
let binding;
const controller=createPromptReferencePopover({getInlineHit:(x,y)=>binding.hitTest(x,y)});
const snapshots=[],closed=[];
binding=createPromptReferenceHit({textarea:note,getRanges:value=>codec.ranges(value),onHit:(entry,range,event)=>{
  const before={focus:document.activeElement.id,start:note.selectionStart,end:note.selectionEnd,value:note.value};
  controller.show(text,{anchor:note,event,range,entry});
  const after={focus:document.activeElement.id,start:note.selectionStart,end:note.selectionEnd,value:note.value};
  snapshots.push({before,after,range:{start:range.start,end:range.end}});
}});
for(const id of ['chip','edge'])document.querySelector('#'+id).addEventListener('click',event=>
  controller.show(text,{anchor:event.currentTarget,event}));
document.querySelector('#image-chip').addEventListener('click',event=>
  controller.show(image,{anchor:event.currentTarget,event}));
document.querySelector('#prompt-image-preview-toggle').addEventListener('click',event=>{
  event.currentTarget.dataset.original=event.currentTarget.dataset.original==='true'?'false':'true';
  event.currentTarget.textContent=event.currentTarget.dataset.original==='true'?'查看标注图':'查看原图';
});
for(const id of ['close-text','close-image'])document.querySelector('#'+id).addEventListener('click',controller.close);
for(const element of [text,image])element.addEventListener('close',()=>closed.push(element.id));
window.check={controller,binding,note,codec,text,image,snapshots,closed};
window.point=(occurrence=0)=>{
  const range=codec.ranges(note.value)[occurrence],style=getComputedStyle(note),rect=note.getBoundingClientRect();
  const canvas=document.createElement('canvas'),context=canvas.getContext('2d');
  context.font=[style.fontStyle,style.fontWeight,style.fontSize,style.fontFamily].join(' ');
  const start=context.measureText(note.value.slice(0,range.start)).width;
  const end=context.measureText(note.value.slice(0,range.end)).width;
  return {x:rect.left+note.clientLeft+parseFloat(style.paddingLeft)+(start+end)/2,
    y:rect.top+note.clientTop+parseFloat(style.paddingTop)+parseFloat(style.lineHeight)/2};
};
</script>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        name = self.path.split("?", 1)[0]
        if name == "/":
            payload, content_type = HTML.encode(), "text/html; charset=utf-8"
        elif name in {"/prompt-reference-popover.js", "/prompt-reference-popover.css",
                      "/prompt-reference-hit.js", "/prompt-reference-text.js", "/prompt-reference-icons.js"}:
            payload = (ROOT / "web" / name.lstrip("/")).read_bytes()
            content_type = "text/css; charset=utf-8" if name.endswith(".css") else "application/javascript; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


def shown(page, selector="#prompt-reference-dialog"):
    return page.locator(selector).is_visible()


def click_inline(page, occurrence=0):
    point = page.evaluate("occurrence=>point(occurrence)", occurrence)
    page.mouse.click(point["x"], point["y"])
    assert shown(page)
    return point


def assert_in_viewport(page, selector):
    box = page.locator(selector).bounding_box()
    viewport = page.viewport_size
    assert box and 7 <= box["x"] and 7 <= box["y"], box
    assert box["x"] + box["width"] <= viewport["width"] - 7, box
    assert box["y"] + box["height"] <= viewport["height"] - 7, box
    return box


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-executable", default="/tmp/dynamic-browser-cache/chromium-1243/chrome-linux64/chrome")
    parser.add_argument("--screenshot-dir", type=Path, help="Optionally save the three compact detail surfaces")
    args = parser.parse_args()
    if args.screenshot_dir:
        args.screenshot_dir.mkdir(parents=True, exist_ok=True)
    from PIL import Image
    from playwright.sync_api import sync_playwright

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    errors = []
    url = f"http://127.0.0.1:{server.server_port}/"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=args.browser_executable,
                args=["--no-sandbox", "--no-proxy-server"])
            page = browser.new_page(viewport={"width": 900, "height": 700})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url)
            page.wait_for_function("window.check")

            page.locator("#chip").hover()
            page.wait_for_timeout(200)
            assert not shown(page), "Hover must never open reference details"
            page.locator("#chip").click()
            box = assert_in_viewport(page, "#prompt-reference-dialog")
            assert box["width"] == 260 and box["height"] < 140, box
            assert page.locator("#chip").get_attribute("aria-expanded") == "true"
            assert page.locator("#chip").get_attribute("aria-controls") == "prompt-reference-dialog"
            assert page.evaluate("document.activeElement.id") == "chip"
            if args.screenshot_dir:
                page.screenshot(path=str(args.screenshot_dir / "source-card.png"))
            # Crossing the 7px gap is permitted; time outside both surfaces is limited.
            source = page.locator("#chip").bounding_box()
            page.mouse.move(source["x"] + 15, source["y"] + source["height"] + 3)
            page.mouse.move(box["x"] + 15, box["y"] + 12)
            page.wait_for_timeout(210)
            assert shown(page), "Crossing to the detail surface should retain it"
            page.mouse.move(850, 400)
            page.wait_for_timeout(210)
            assert not shown(page)
            assert page.locator("#chip").get_attribute("aria-expanded") == "false"
            assert page.evaluate("check.closed.length") == 1
            print("PASS: click-only compact details, source ARIA and crossing/leave close", flush=True)

            first = click_inline(page)
            snapshot = page.evaluate("check.snapshots.at(-1)")
            assert snapshot["before"] == snapshot["after"], snapshot
            assert snapshot["after"]["focus"] == "note"
            if args.screenshot_dir:
                page.screenshot(path=str(args.screenshot_dir / "inline-reference.png"))
            second = page.evaluate("point(1)")
            page.mouse.move(second["x"], second["y"])
            page.wait_for_timeout(210)
            assert not shown(page), "Another occurrence of the same token is a different source"
            click_inline(page, 1)
            page.mouse.move(600, first["y"])
            page.wait_for_timeout(210)
            assert not shown(page), "Blank space inside the same textarea must close details"
            click_inline(page)
            page.mouse.click(600, first["y"])
            assert not shown(page), "A native blank-text click closes immediately"
            print("PASS: inline range identity, blank textarea regions and preserved native focus/selection", flush=True)

            page.locator("#note").focus()
            baseline = Image.open(BytesIO(page.screenshot())).convert("RGB").crop((30, 490, 260, 650))
            page.evaluate("""()=>{
              check.controller.show(check.text,{anchor:check.note,range:check.codec.ranges(check.note.value)[0],
                event:{clientX:200,clientY:110,detail:1}});
            }""")
            changed = Image.open(BytesIO(page.screenshot())).convert("RGB").crop((30, 490, 260, 650))
            assert baseline.tobytes() == changed.tobytes(), "The canvas background must keep its brightness"
            assert page.evaluate("document.activeElement.id") == "note"
            assert page.evaluate("document.querySelectorAll(':modal').length") == 0
            assert page.evaluate("!document.querySelector('[inert]')")
            assert page.locator("#prompt-reference-dialog").get_attribute("aria-modal") == "false"
            assert page.evaluate("getComputedStyle(check.text).backdropFilter") == "none"
            page.keyboard.press("Escape")
            assert not shown(page)
            assert page.evaluate("document.activeElement.id") == "note"
            print("PASS: no modal, focus transfer, inert background, blur or brightness change", flush=True)

            page.locator("#chip").click()
            page.locator("#image-chip").click()
            assert not shown(page) and shown(page, "#prompt-image-preview-dialog")
            image_box = assert_in_viewport(page, "#prompt-image-preview-dialog")
            assert image_box["width"] == 300 and image_box["height"] < 300, image_box
            if args.screenshot_dir:
                page.screenshot(path=str(args.screenshot_dir / "image-reference.png"))
            assert page.locator("#chip").get_attribute("aria-expanded") == "false"
            page.locator("#prompt-image-preview-toggle").click()
            assert shown(page, "#prompt-image-preview-dialog"), "Moving focus into an image action must retain the surface"
            assert page.evaluate("document.activeElement.id") == "prompt-image-preview-toggle"
            assert page.locator("#prompt-image-preview-toggle").get_attribute("data-original") == "true"
            page.locator("#close-image").focus()
            assert shown(page, "#prompt-image-preview-dialog"), "Moving between preview controls must retain the surface"
            page.locator("#close-image").click()
            assert not shown(page, "#prompt-image-preview-dialog")
            assert page.locator("#image-chip").get_attribute("aria-controls") is None
            page.locator("#edge").click()
            assert_in_viewport(page, "#prompt-reference-dialog")
            page.evaluate("window.dispatchEvent(new Event('blur'))")
            assert not shown(page), "Actual browser-window blur should close the surface"
            page.locator("#edge").click()
            page.set_viewport_size({"width": 360, "height": 480})
            page.locator("#prompt-reference-dialog").wait_for(state="hidden")
            assert not shown(page)
            page.locator("#edge").click()
            assert_in_viewport(page, "#prompt-reference-dialog")
            page.set_viewport_size({"width": 900, "height": 700})
            page.locator("#prompt-reference-dialog").wait_for(state="hidden")
            print("PASS: one shared surface, compact image, close buttons, viewport edges and resize dismissal", flush=True)

            click_inline(page)
            page.keyboard.type("x")
            assert not shown(page)
            page.keyboard.press("Control+z")
            assert page.locator("#note").input_value() == "Before 🧊1 gap 🧊1 after"
            click_inline(page)
            page.evaluate("check.note.dispatchEvent(new CompositionEvent('compositionstart'))")
            assert not shown(page)
            page.evaluate("check.note.dispatchEvent(new CompositionEvent('compositionend'))")
            click_inline(page)
            page.evaluate("check.note.dispatchEvent(new Event('scroll'))")
            assert not shown(page)
            page.locator("#chip").click()
            page.evaluate("""()=>{
              document.querySelector('#prompt-reference-detail').textContent='Long source details. '.repeat(120);
              check.text.scrollTop=80;check.text.dispatchEvent(new Event('scroll'));
            }""")
            assert shown(page), "The compact surface must allow scrolling its own long details"
            page.evaluate("document.querySelector('#prompt-reference-detail').textContent='固定来源' ")
            page.keyboard.press("Escape")
            page.locator("#chip").click()
            page.evaluate("document.querySelector('#chip').hidden=true")
            page.wait_for_timeout(30)
            assert not shown(page), "A hidden source must dismiss its details"
            page.evaluate("document.querySelector('#chip').hidden=false")
            page.locator("#chip").click()
            page.evaluate("document.querySelector('#chip').remove()")
            page.wait_for_timeout(30)
            assert not shown(page)
            print("PASS: typing/undo, composition, scrolling and source disappearance dismiss safely", flush=True)

            # Touch has no hover exit; outside taps and the close control remain usable.
            context = browser.new_context(viewport={"width": 390, "height": 650}, has_touch=True, is_mobile=True)
            touch = context.new_page()
            touch.on("pageerror", lambda error: errors.append(str(error)))
            touch.goto(url)
            touch.wait_for_function("window.check")
            touch.locator("#chip").tap()
            touch.wait_for_timeout(250)
            assert shown(touch), "Touch pointer exit must not auto-close details"
            touch.locator("#close-text").tap()
            assert not shown(touch)
            touch.locator("#chip").tap()
            touch.locator("#stage").tap()
            assert not shown(touch)
            context.close()
            print("PASS: touch details stay open until an outside tap or close action", flush=True)

            page.locator("#edge").focus()
            page.keyboard.press("Enter")
            assert shown(page) and page.evaluate("document.activeElement.id") == "edge"
            before = page.evaluate("check.closed.length")
            page.evaluate("check.controller.destroy()")
            assert not shown(page)
            assert page.locator("#edge").get_attribute("aria-expanded") == "false"
            assert page.locator("#edge").get_attribute("aria-controls") is None
            assert page.evaluate("check.closed.length") == before + 1
            page.keyboard.press("Enter")
            page.mouse.move(500, 400)
            page.wait_for_timeout(210)
            assert not shown(page)
            assert page.evaluate("check.closed.length") == before + 1
            assert not errors, errors
            print("PASS: keyboard activation, close notification and controller cleanup", flush=True)
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    main()

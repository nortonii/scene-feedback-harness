#!/usr/bin/env python3
"""Exercise reference clicks in an isolated native textarea, without a workbench."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
import threading

ROOT = Path(__file__).resolve().parents[1]
HTML = r"""<!doctype html><meta charset="utf-8">
<style>
body { margin: 30px; }
textarea { display:block; resize:none; box-sizing:border-box; width:540px; height:160px;
  border:3px solid #555; padding:13px 17px; font:20px/28px monospace; }
</style>
<textarea id="feedback-note"></textarea>
<script type="module">
import {createPromptReferenceText} from './prompt-reference-text.js';
import {createPromptReferenceHit,hitPromptReference} from './prompt-reference-hit.js';
const field=document.querySelector('textarea');
const codec=createPromptReferenceText({icons:true});
for(let n=1;n<=10;n++)codec.remember('Box '+n,'[[object:box'+n+']]');
codec.remember('Picture','[[image:picture]]');
const calls=[];
const binding=createPromptReferenceHit({textarea:field,getRanges:value=>codec.ranges(value),
  onHit:(entry,range,event)=>calls.push({alias:entry.alias,start:range.start,end:range.end,
    trusted:event.isTrusted,value:field.value,selection:[field.selectionStart,field.selectionEnd]})});
window.check={field,codec,calls,binding,hitPromptReference};
// A canvas supplies independently measured single-line coordinates. Browser
// clicks then verify the native caret against the source alias's UTF-16 range.
window.point=(alias,offset=.5)=>{
  const style=getComputedStyle(field),rect=field.getBoundingClientRect();
  const canvas=document.createElement('canvas'),context=canvas.getContext('2d');
  context.font=[style.fontStyle,style.fontWeight,style.fontSize,style.fontFamily].join(' ');
  context.letterSpacing=style.letterSpacing;context.wordSpacing=style.wordSpacing;
  const start=field.value.indexOf(alias);
  const before=context.measureText(field.value.slice(0,start)).width;
  const after=context.measureText(field.value.slice(0,start)+alias).width;
  return {x:rect.left+parseFloat(style.borderLeftWidth)+parseFloat(style.paddingLeft)+
      before+(after-before)*offset-field.scrollLeft,
    y:rect.top+parseFloat(style.borderTopWidth)+parseFloat(style.paddingTop)+
      parseFloat(style.lineHeight)/2-field.scrollTop};
};
</script>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        name = self.path.split("?", 1)[0]
        if name == "/":
            payload, content_type = HTML.encode(), "text/html; charset=utf-8"
        elif name in {"/prompt-reference-hit.js", "/prompt-reference-text.js", "/prompt-reference-icons.js"}:
            payload = (ROOT / "web" / name.lstrip("/")).read_bytes()
            content_type = "application/javascript; charset=utf-8"
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


def setup(page, value, css="", wrap="soft"):
    page.evaluate("""({value,css,wrap})=>{
      check.field.style.cssText=css;check.field.wrap=wrap;check.field.value=value;
      check.field.scrollTop=0;check.field.scrollLeft=0;check.calls.length=0;
    }""", {"value": value, "css": css, "wrap": wrap})


def click_alias(page, alias, offset=.5):
    point = page.evaluate("([alias,offset])=>point(alias,offset)", [alias, offset])
    page.mouse.click(point["x"], point["y"])
    result = page.evaluate("check.calls.at(-1)")
    assert result and result["alias"] == alias and result["trusted"], (point, result)
    assert result["selection"][0] == result["selection"][1]
    assert result["start"] <= result["selection"][0] <= result["end"], result
    return point


def native_inside_points(page, alias):
    """Use real native caret positions as an independent wrapped-text oracle.

    Strictly internal caret offsets avoid the ambiguity at an alias's boundary,
    where native textarea clicks also map adjacent whitespace to the same caret.
    """
    data = page.evaluate("""alias=>{
      const field=check.field,rect=field.getBoundingClientRect(),style=getComputedStyle(field);
      const start=field.value.indexOf(alias),range=check.codec.ranges(field.value).find(r=>r.start===start);
      return {start:range.start,end:range.end,left:rect.left+field.clientLeft,
        top:rect.top+field.clientTop,width:field.clientWidth,height:field.clientHeight,
        paddingTop:parseFloat(style.paddingTop),lineHeight:parseFloat(style.lineHeight),
        scrollTop:field.scrollTop};
    }""", alias)
    # A wrapped line's blank tail can map to an internal alias caret offset.
    # Retain only columns containing actual rendered ink as well as an internal
    # native caret, so this oracle never treats line-tail whitespace as a glyph.
    from PIL import Image
    page.evaluate("check.field.blur()")
    pixels = Image.open(BytesIO(page.screenshot())).convert("RGB")

    def has_ink(x, y):
        half = int(data["lineHeight"] / 2) - 2
        return any(min(pixels.getpixel((round(x), yy))) < 230
                   for yy in range(max(0, round(y) - half), min(pixels.height, round(y) + half)))

    points = []
    first_y = data["top"] + data["paddingTop"] + data["lineHeight"] / 2 - data["scrollTop"]
    while first_y < data["top"]:
        first_y += data["lineHeight"]
    for row in range(int(data["height"] / data["lineHeight"]) + 1):
        y = first_y + row * data["lineHeight"]
        if y >= data["top"] + data["height"]:
            break
        for x_offset in range(2, int(data["width"]) - 2, 5):
            x = data["left"] + x_offset
            page.mouse.click(x, y)
            caret = page.evaluate("check.field.selectionStart")
            if data["start"] < caret < data["end"] and has_ink(x, y):
                actual = page.evaluate("([x,y])=>check.binding.hitTest(x,y)?.entry.alias ?? null", [x, y])
                assert actual == alias, ("native caret inside reference", caret, alias, x, y, actual)
                points.append((x, y))
    assert points, (alias, data)
    return points


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-executable", default="/tmp/dynamic-browser-cache/chromium-1243/chrome-linux64/chrome")
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    errors = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=args.browser_executable,
                args=["--no-sandbox", "--no-proxy-server"])
            page = browser.new_page(viewport={"width": 1100, "height": 850})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            page.wait_for_function("window.check")

            for alias in ("🧊1", "🧊10", "🖼️1"):
                setup(page, "前文 " + alias + " 后文")
                click_alias(page, alias)
            print("PASS: real clicks identify object1/object10 and image VS16 aliases in UTF-16", flush=True)

            for css in ("font:29px/41px serif;letter-spacing:2.25px;word-spacing:3px;padding:19px 23px;",
                        "font:14px/22px sans-serif;letter-spacing:-.25px;border-width:1px;padding:7px 9px;width:333.5px;"):
                setup(page, "Prefix 🧊10 suffix", css)
                click_alias(page, "🧊10")
            print("PASS: font, line height, letter/word spacing, padding, border and fractional width", flush=True)

            setup(page, "🧊1")
            rect = page.locator("textarea").bounding_box()
            point = page.evaluate("point('🧊1')")
            page.mouse.click(rect["x"] + rect["width"] - 35, point["y"])
            assert page.evaluate("check.field.selectionStart===check.field.value.length")
            assert page.evaluate("check.calls.length") == 0, "Blank line tail must not open the final reference"
            page.mouse.click(rect["x"] + 6, rect["y"] + 6)
            assert page.evaluate("check.calls.length") == 0, "Padding must not open a reference"
            setup(page, "Plain text 🧊99 🧊1")
            for unknown in ("Plain", "🧊99"):
                point = page.evaluate("alias=>point(alias)", unknown)
                page.mouse.click(point["x"], point["y"])
            assert page.evaluate("check.calls.length") == 0
            print("PASS: prose, padding, unregistered icons and line-tail whitespace stay editable without opening details", flush=True)

            setup(page, "abc🧊10 trailing", "width:60px;height:280px;font:24px/34px monospace;padding:9px 7px;overflow-wrap:anywhere;")
            points = native_inside_points(page, "🧊10")
            assert len({round(y, 2) for _, y in points}) >= 2, ("fixture must split reference across wrapped lines", points)
            print("PASS: a reference split over two wrapped lines has clickable geometry on both lines", flush=True)

            setup(page, "\n".join(["Line " + str(n) for n in range(14)]) + "\nVisible 🖼️1 end\nTail",
                  "height:126px;font:20px/28px monospace;padding:13px 17px;")
            page.evaluate("check.field.scrollTop=check.field.scrollHeight-check.field.clientHeight")
            native_inside_points(page, "🖼️1")
            setup(page, "x" * 65 + " 🧊10 suffix", "width:260px;height:120px;font:20px/28px monospace;", "off")
            page.evaluate("check.field.scrollLeft=check.field.scrollWidth-check.field.clientWidth")
            click_alias(page, "🧊10")
            print("PASS: vertical and horizontal native textarea scroll offsets", flush=True)

            setup(page, "<img src=x onerror=injected=true> 🧊1")
            click_alias(page, "🧊1")
            assert page.evaluate("!window.injected && !document.querySelector('img')")
            initial_nodes = page.evaluate("document.body.children.length")
            page.evaluate("""()=>{
              const original=Range.prototype.getClientRects;
              Range.prototype.getClientRects=()=>{throw new Error('fixture measurement failure')};
              try {const p=point('🧊1');check.binding.hitTest(p.x,p.y)} catch(error) {
                if(error.message!=='fixture measurement failure')throw error;
              } finally {Range.prototype.getClientRects=original;}
            }""")
            assert page.evaluate("document.body.children.length") == initial_nodes
            print("PASS: note markup stays literal and temporary mirrors are removed even after a measurement exception", flush=True)

            setup(page, "🧊1")
            page.evaluate("check.field.dispatchEvent(new CompositionEvent('compositionstart'))")
            point = page.evaluate("point('🧊1')")
            page.mouse.click(point["x"], point["y"])
            assert page.evaluate("check.calls.length") == 0
            page.evaluate("check.field.dispatchEvent(new CompositionEvent('compositionend'))")
            click_alias(page, "🧊1")
            page.locator("textarea").press("End")
            page.keyboard.type(" native")
            page.locator("textarea").press("Control+z")
            assert page.locator("textarea").input_value() == "🧊1"
            count = page.evaluate("check.calls.length")
            page.evaluate("check.binding.destroy()")
            page.mouse.click(point["x"], point["y"])
            assert page.evaluate("check.calls.length") == count
            assert page.evaluate("document.body.children.length") == initial_nodes
            assert not errors, errors
            print("PASS: composition guard, native typing/undo and listener cleanup", flush=True)
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    main()

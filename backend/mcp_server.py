"""Official MCP SDK stdio tools for the local 3D scene feedback harness."""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations

from server import DEFAULT_DATA_DIR


PORT = int(os.environ.get("SCENE_FEEDBACK_PORT", "18765"))
DATA_DIR = Path(os.environ.get("SCENE_FEEDBACK_DATA_DIR", str(DEFAULT_DATA_DIR))).expanduser().resolve()
BASE_URL = f"http://127.0.0.1:{PORT}"
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
IMAGE_URL_RE = re.compile(r"^/(media|screenshots)/([0-9a-f]{32}\.(?:png|jpg))$")


def _http(method: str, path: str, payload: dict[str, Any] | None = None, *, private: bool = False, timeout: float = 10) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if private:
        headers["X-Scene-Harness-Key"] = (DATA_DIR / "control_token").read_text(encoding="ascii").strip()
    request = urllib.request.Request(BASE_URL + path, data=body, headers=headers, method=method)
    try:
        with _opener.open(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        with exc:
            try:
                detail = json.load(exc)["error"]
            except Exception:
                detail = exc.reason
        raise ValueError(f"HTTP {exc.code}: {detail}") from exc


def ensure_http_server() -> None:
    """Require the project Gateway started by the local workbench process."""
    try:
        health = _http("GET", "/api/health", timeout=2)
    except (ValueError, urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise RuntimeError(f"Workspace Gateway unavailable at {BASE_URL}; start backend/server.py") from exc
    if health.get("service") != "scene-feedback-harness" or not health.get("workspace_gateway"):
        raise RuntimeError(f"port {PORT} is not running the current Workspace Gateway")


def _image_path(url: str) -> Path:
    match = IMAGE_URL_RE.fullmatch(url)
    if match is None:
        raise ValueError("feedback contains an invalid image URL")
    directory = DATA_DIR / match.group(1)
    return directory / match.group(2)


def _feedback_with_local_paths(result: dict[str, Any]) -> dict[str, Any]:
    for item in result.get("items", []):
        for reference in item.get("reference_images", []):
            reference["path"] = str(_image_path(reference["url"]))
        for reference in item.get("reference_annotated_images", []):
            reference["path"] = str(_image_path(reference["url"]))
        for crop in item.get("crops", []):
            crop["path"] = str(_image_path(crop["url"]))
        for name in ("scene_original", "scene_annotated", "screenshot"):
            url = item.get(f"{name}_url")
            if url:
                item[f"{name}_path"] = str(_image_path(url))
    return result


def _preview_image(path: Path) -> ImageContent:
    """Send a bounded MCP preview while retaining the original file on disk."""
    from PIL import Image, ImageOps

    with Image.open(path) as opened:
        opened.load()
        image = ImageOps.exif_transpose(opened)
        image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
        if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, "white")
            background.paste(rgba, mask=rgba.getchannel("A"))
            image = background
        else:
            image = image.convert("RGB")
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=85, optimize=True)
    return ImageContent(type="image", data=base64.b64encode(output.getvalue()).decode("ascii"), mime_type="image/jpeg")


def _visual_tool_result(result: dict[str, Any]) -> CallToolResult:
    enriched = _feedback_with_local_paths(result)
    content: list[TextContent | ImageContent] = [TextContent(type="text", text=json.dumps(enriched, ensure_ascii=False))]
    for item in enriched.get("items", []):
        for index, reference in enumerate(item.get("reference_images", []), 1):
            content.append(TextContent(type="text", text=f"Reference {index} original ({reference['id']}): {reference['path']}"))
            content.append(_preview_image(Path(reference["path"])))
        for reference in item.get("reference_annotated_images", []):
            content.append(TextContent(type="text", text=f"Annotated reference ({reference['reference_id']}): {reference['path']}"))
            content.append(_preview_image(Path(reference["path"])))
        for label in ("scene_original", "scene_annotated"):
            path = item.get(f"{label}_path")
            if path:
                content.append(TextContent(type="text", text=f"{label.replace('_', ' ').title()}: {path}"))
                content.append(_preview_image(Path(path)))
        for crop in item.get("crops", []):
            content.append(TextContent(type="text", text=f"{crop['source'].title()} detail crop: {crop['path']}"))
            content.append(_preview_image(Path(crop["path"])))
    return CallToolResult(content=content, structured_content=enriched)


mcp = MCPServer("scene-feedback-harness")


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False, idempotent_hint=True))
def workspace_open(open_browser: bool = True) -> dict[str, Any]:
    """Open or locate this project's persistent visual reconstruction workbench."""
    ensure_http_server()
    result = _http("GET", "/api/workspace/state")
    result.pop("browser_capability", None)
    result["url"] = f"{BASE_URL}/?session_id={result['session_id']}"
    result["browser_opened"] = False
    if open_browser and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        try:
            result["browser_opened"] = bool(webbrowser.open(result["url"], 1, True))
        except Exception:
            logging.exception("Could not open the visual workbench")
    return result


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False, idempotent_hint=True))
def workspace_get_context() -> CallToolResult:
    """Read this project's current scene revision, reference photos and Codex thread identity."""
    ensure_http_server()
    context = _http("GET", "/api/workspace/context")
    blocks: list[TextContent | ImageContent] = []
    for reference in context["reference_images"]:
        reference["path"] = str(_image_path(reference["url"]))
    for obj in context["scene"]["objects"]:
        if obj.get("type") == "model" and obj.get("url", "").startswith("/assets/"):
            obj["local_path"] = str(DATA_DIR / "assets" / obj["url"].rsplit("/", 1)[-1])
    blocks.append(TextContent(type="text", text=json.dumps(context, ensure_ascii=False)))
    for reference in context["reference_images"]:
        blocks.append(TextContent(type="text", text=f"Reference original: {reference['path']}"))
        blocks.append(_preview_image(Path(reference["path"])))
    return CallToolResult(content=blocks, structured_content=context)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False, idempotent_hint=True))
def workspace_get_feedback(feedback_id: str) -> CallToolResult:
    """Read one already submitted immutable visual feedback packet with real image blocks."""
    ensure_http_server()
    packet = _http("GET", f"/api/workspace/feedback/{feedback_id}")
    return _visual_tool_result({"items": [packet], "next_cursor": 1, "session_id": packet["session_id"]})


@mcp.tool()
def workspace_publish_scene(local_path: str, expected_revision: int) -> dict[str, Any]:
    """Publish a self-contained GLB after editing its source; refresh the same workbench."""
    ensure_http_server()
    return _http("POST", "/api/workspace/publish", {"local_path": local_path, "expected_revision": expected_revision}, private=True)


@mcp.tool()
def workspace_request_feedback(message: str, object_ids: list[str] | None = None) -> dict[str, Any]:
    """Ask the human to inspect a result in the workbench, then return immediately."""
    ensure_http_server()
    return _http("POST", "/api/workspace/request-feedback", {"message": message, "object_ids": object_ids}, private=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    mcp.run()

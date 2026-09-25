"""Official MCP SDK stdio tools for the local 3D scene feedback harness."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import re
import threading
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations

from server import DEFAULT_DATA_DIR, DEFAULT_WEB_DIR, make_server


PORT = int(os.environ.get("SCENE_FEEDBACK_PORT", "18765"))
DATA_DIR = Path(os.environ.get("SCENE_FEEDBACK_DATA_DIR", str(DEFAULT_DATA_DIR))).expanduser().resolve()
WEB_DIR = Path(os.environ.get("SCENE_FEEDBACK_WEB_DIR", str(DEFAULT_WEB_DIR))).expanduser().resolve()
BASE_URL = f"http://127.0.0.1:{PORT}"
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_server_lock = threading.Lock()
_server = None
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
    """Reuse a running harness or start one in this MCP process."""
    global _server
    with _server_lock:
        try:
            health = _http("GET", "/api/health", timeout=1)
        except ValueError as exc:
            raise RuntimeError(f"port {PORT} is occupied by a service without the harness health endpoint") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            health = None
        if health is not None:
            if health.get("service") != "scene-feedback-harness":
                raise RuntimeError(f"port {PORT} is occupied by another HTTP service")
            return
        if _server is None:
            try:
                _server = make_server(port=PORT, data_dir=DATA_DIR, web_dir=WEB_DIR)
            except OSError as exc:
                # Another harness may have won the startup race.
                try:
                    health = _http("GET", "/api/health", timeout=1)
                except Exception:
                    raise RuntimeError(f"could not bind loopback port {PORT}: {exc}") from exc
                if health.get("service") != "scene-feedback-harness":
                    raise RuntimeError(f"port {PORT} is occupied by another HTTP service") from exc
                return
            threading.Thread(target=_server.serve_forever, name="scene-feedback-http", daemon=True).start()
        health = _http("GET", "/api/health")
        if health.get("service") != "scene-feedback-harness":
            raise RuntimeError("HTTP harness failed to start")


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


async def _wait_for_feedback(session_id: str, timeout_sec: int, cursor: int = 0) -> dict[str, Any]:
    if not 1 <= timeout_sec <= 3600:
        raise ValueError("timeout_sec must be between 1 and 3600")
    if type(cursor) is not int or cursor < 0:
        raise ValueError("cursor must be a nonnegative integer")
    deadline = asyncio.get_running_loop().time() + timeout_sec
    while True:
        result = await asyncio.to_thread(_http, "GET", f"/api/sessions/{session_id}/feedback?cursor={cursor}")
        if result["items"] or result["status"] != "open":
            result["url"] = f"{BASE_URL}/?session_id={session_id}"
            return _feedback_with_local_paths(result)
        if asyncio.get_running_loop().time() >= deadline:
            return {"session_id": session_id, "status": "timeout", "items": [], "next_cursor": cursor, "url": f"{BASE_URL}/?session_id={session_id}", "message": "Session remains open; call wait_visual_feedback later with this cursor."}
        await asyncio.sleep(0.35)


mcp = MCPServer("scene-feedback-harness")


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False, idempotent_hint=True))
def get_scene() -> dict[str, Any]:
    """Read the current editable scene, its object IDs, transforms, and revision."""
    ensure_http_server()
    return _http("GET", "/api/scene")


@mcp.tool()
async def request_visual_feedback(
    reference_images: list[str] | None = None,
    current_scene: dict[str, Any] | None = None,
    scene_glb_path: str | None = None,
    session_id: str | None = None,
    cursor: int | None = None,
    wait_for_submit: bool = True,
    timeout_sec: int = 600,
    open_browser: bool = True,
) -> CallToolResult:
    """Open the visual workbench for photos and the current 3D result, then return the next human feedback packet.

    reference_images are local PNG/JPEG paths. current_scene may provide an
    {objects:[...]} snapshot, or scene_glb_path may point to a local GLB preview.
    Omit both to review the existing scene. Reuse session_id for later rounds;
    cursor is the previous result's next_cursor. Without a cursor, a reused
    session waits for submissions after the current feedback count.
    """
    ensure_http_server()
    if current_scene is not None and scene_glb_path is not None:
        raise ValueError("provide current_scene or scene_glb_path, not both")
    if current_scene is not None and (not isinstance(current_scene, dict) or not isinstance(current_scene.get("objects"), list)):
        raise ValueError("current_scene must contain an objects array")
    if type(timeout_sec) is not int or not 1 <= timeout_sec <= 3600:
        raise ValueError("timeout_sec must be between 1 and 3600")
    # Validate a reused session before any operation that changes the scene.
    if session_id is None:
        if cursor not in (None, 0) or type(cursor) is bool:
            raise ValueError("a new session must start at cursor 0")
        cursor = 0
        session = None
    else:
        if reference_images is not None:
            raise ValueError("reference_images can be supplied only when creating a session")
        session = await asyncio.to_thread(_http, "GET", f"/api/sessions/{session_id}")
        if session["status"] != "open":
            raise ValueError("session is closed")
        if cursor is None:
            cursor = session["feedback_count"]
    if type(cursor) is not int or cursor < 0:
        raise ValueError("cursor must be a nonnegative integer")
    if scene_glb_path is not None:
        await asyncio.to_thread(_http, "POST", "/api/scene/preview", {"local_path": scene_glb_path}, private=True)
    elif current_scene is not None:
        scene = await asyncio.to_thread(_http, "GET", "/api/scene")
        await asyncio.to_thread(_http, "PUT", "/api/scene", {"expected_revision": scene["revision"], "objects": current_scene["objects"]}, private=True)
    if session_id is None:
        session = await asyncio.to_thread(_http, "POST", "/api/sessions", {"reference_images": reference_images or []}, private=True)
    else:
        session["url"] = f"{BASE_URL}/?session_id={session_id}"
    opened = False
    if open_browser and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        try:
            opened = bool(await asyncio.wait_for(asyncio.to_thread(webbrowser.open, session["url"], 1, True), timeout=5))
        except Exception:
            logging.exception("Could not open a local browser")
    session["browser_opened"] = opened
    session["next_cursor"] = cursor
    if wait_for_submit and opened:
        result = await _wait_for_feedback(session["session_id"], timeout_sec, cursor)
        result["browser_opened"] = opened
        return _visual_tool_result(result)
    if wait_for_submit:
        session["message"] = "Open the URL, submit feedback, then call wait_visual_feedback with this session_id and next_cursor."
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(session, ensure_ascii=False))], structured_content=session)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False, idempotent_hint=True))
async def wait_visual_feedback(session_id: str, cursor: int = 0, timeout_sec: int = 600) -> CallToolResult:
    """Wait for visual feedback after cursor in the same persistent review session; return images and metadata."""
    ensure_http_server()
    return _visual_tool_result(await _wait_for_feedback(session_id, timeout_sec, cursor))


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False, idempotent_hint=True))
def get_visual_feedback(session_id: str, cursor: int = 0) -> CallToolResult:
    """Read available visual feedback now, including reference and annotated image blocks."""
    ensure_http_server()
    result = _http("GET", f"/api/sessions/{session_id}/feedback?cursor={cursor}")
    result["url"] = f"{BASE_URL}/?session_id={session_id}"
    return _visual_tool_result(result)


@mcp.tool()
async def open_scene_feedback(wait_for_submit: bool = True, timeout_sec: int = 600, open_browser: bool = True) -> dict[str, Any]:
    """Create a 3D review session, open its browser UI, and optionally wait for the user's submitted spatial feedback.

    The default waits only if a local browser actually launches. Otherwise the tool
    returns the URL immediately so the agent can share it, then call wait_scene_feedback.
    Set wait_for_submit=false for a reliable two-call workflow in headless clients.
    """
    ensure_http_server()
    session = await asyncio.to_thread(_http, "POST", "/api/sessions", {})
    opened = False
    if open_browser and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        try:
            opened = bool(await asyncio.wait_for(asyncio.to_thread(webbrowser.open, session["url"], 1, True), timeout=5))
        except Exception:
            logging.exception("Could not open a local browser")
    session["browser_opened"] = opened
    if wait_for_submit and opened:
        result = await _wait_for_feedback(session["session_id"], timeout_sec)
        result["browser_opened"] = opened
        return result
    if wait_for_submit and not opened:
        session["message"] = "Open the URL in a browser, then call wait_scene_feedback with this session_id."
    return session


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False, idempotent_hint=True))
async def wait_scene_feedback(session_id: str, timeout_sec: int = 600, cursor: int = 0) -> dict[str, Any]:
    """Wait for spatial feedback in a review session; pass next_cursor for later rounds."""
    ensure_http_server()
    return await _wait_for_feedback(session_id, timeout_sec, cursor)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False, idempotent_hint=True))
def get_scene_feedback(session_id: str, cursor: int = 0) -> dict[str, Any]:
    """Poll submitted structured annotations and screenshots for a review session."""
    ensure_http_server()
    result = _http("GET", f"/api/sessions/{session_id}/feedback?cursor={cursor}")
    return _feedback_with_local_paths(result)


@mcp.tool()
def update_scene(expected_revision: int, changes: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply scene changes. Each change is add {object}, update {object_id, fields}, or delete {object_id}."""
    ensure_http_server()
    return _http("POST", "/api/scene/update", {"expected_revision": expected_revision, "changes": changes}, private=True)


@mcp.tool()
def replace_scene(expected_revision: int, objects: list[dict[str, Any]]) -> dict[str, Any]:
    """Replace all scene objects at the expected revision, preserving revision conflict checks."""
    ensure_http_server()
    return _http("PUT", "/api/scene", {"expected_revision": expected_revision, "objects": objects}, private=True)


@mcp.tool()
def import_scene_model(local_path: str, object_id: str | None = None, name: str | None = None, position: list[float] | None = None, size: list[float] | None = None) -> dict[str, Any]:
    """Import a local GLB 2.0 file into the scene and expose it read-only to the 3D viewer.

    For a model object, size is a three-axis scale multiplier, not its measured bounds.
    """
    ensure_http_server()
    return _http("POST", "/api/models/import", {"local_path": local_path, "object_id": object_id, "name": name, "position": position, "size": size}, private=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    mcp.run()

"""Official MCP SDK stdio tools for the local 3D scene feedback harness."""

from __future__ import annotations

import asyncio
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
from urllib.parse import urlencode

from mcp.server import MCPServer
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations

from server import DEFAULT_DATA_DIR


PORT = int(os.environ.get("SCENE_FEEDBACK_PORT", "18765"))
DATA_DIR = Path(os.environ.get("SCENE_FEEDBACK_DATA_DIR", str(DEFAULT_DATA_DIR))).expanduser().resolve()
PROJECT_DIR = Path(os.environ.get("SCENE_FEEDBACK_PROJECT_DIR", str(Path(__file__).resolve().parent.parent))).expanduser().resolve()
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
    if health.get("data_dir") != str(DATA_DIR) or health.get("project_dir") != str(PROJECT_DIR):
        raise RuntimeError(f"Workspace Gateway at {BASE_URL} belongs to another project or data directory; check SCENE_FEEDBACK_* settings")


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


mcp = MCPServer(
    "scene-feedback-harness",
    instructions=(
        "For 3D/image reconstruction in an existing Codex task, if the user says '进入人工调试模式' or '进入人工参与调试模式', "
        "call request_visual_feedback with project-local reference images and current GLB if available. "
        "In external mode, annotated images, scene screenshot, selected object, and note return to the "
        "invoking Codex task. Continue editing in that task; publish the updated GLB with "
        "workspace_publish_scene. If no browser opens, share the returned URL and call "
        "wait_visual_feedback with session_id and next_cursor."
    ),
)


def _external_state() -> dict[str, Any]:
    ensure_http_server()
    state = _http("GET", "/api/workspace/state")
    if state.get("delivery_mode") != "external":
        raise ValueError("this workbench uses its own Codex App Server task; start it with --external-review for an existing Codex task")
    return state


async def _wait_for_external_feedback(session_id: str, timeout_sec: int, cursor: int) -> dict[str, Any]:
    if type(timeout_sec) is not int or not 1 <= timeout_sec <= 3600:
        raise ValueError("timeout_sec must be between 1 and 3600")
    if type(cursor) is not int or cursor < 0:
        raise ValueError("cursor must be a nonnegative integer")
    deadline = asyncio.get_running_loop().time() + timeout_sec
    query = urlencode({"session_id": session_id, "cursor": cursor})
    while True:
        result = await asyncio.to_thread(_http, "GET", f"/api/workspace/external/feedback?{query}", private=True)
        if result["items"] or result.get("status") != "open":
            result["url"] = f"{BASE_URL}/?session_id={session_id}"
            return result
        if asyncio.get_running_loop().time() >= deadline:
            return {"session_id": session_id, "status": "timeout", "items": [], "next_cursor": cursor, "url": f"{BASE_URL}/?session_id={session_id}", "message": "The workbench remains open. Call wait_visual_feedback later with this session_id and next_cursor."}
        await asyncio.sleep(0.35)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False, idempotent_hint=True))
def workspace_open(open_browser: bool = True) -> dict[str, Any]:
    """Open or locate this project's persistent visual reconstruction workbench."""
    ensure_http_server()
    result = _http("GET", "/api/workspace/state")
    if result.get("delivery_mode") == "external":
        _http("POST", "/api/workspace/external/request", {"session_id": result["session_id"]}, private=True)
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
    if _http("GET", "/api/workspace/state").get("delivery_mode") == "external":
        return _http("POST", "/api/workspace/external/request", {"message": message}, private=True)
    return _http("POST", "/api/workspace/request-feedback", {"message": message, "object_ids": object_ids}, private=True)


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
    """Enter human visual debugging in the invoking Codex task and return the annotated feedback.

    Use this for '进入人工调试模式' during 3D reconstruction. Pass reference_images
    as project-local PNG/JPEG paths and scene_glb_path as a project-local GLB.
    The persistent workbench session is bound to the configured project.
    """
    state = _external_state()
    if session_id is not None and session_id != state["session_id"]:
        raise ValueError("session_id belongs to a different workspace")
    if current_scene is not None and scene_glb_path is not None:
        raise ValueError("provide current_scene or scene_glb_path, not both")
    if current_scene is not None and (not isinstance(current_scene, dict) or not isinstance(current_scene.get("objects"), list)):
        raise ValueError("current_scene must contain an objects array")
    if type(timeout_sec) is not int or not 1 <= timeout_sec <= 3600:
        raise ValueError("timeout_sec must be between 1 and 3600")
    if cursor is not None and (type(cursor) is not int or cursor < 0):
        raise ValueError("cursor must be a nonnegative integer")
    if reference_images is not None:
        await asyncio.to_thread(_http, "POST", "/api/workspace/references", {"reference_images": reference_images}, private=True)
    if scene_glb_path is not None:
        scene = await asyncio.to_thread(_http, "GET", "/api/scene")
        await asyncio.to_thread(_http, "POST", "/api/workspace/publish", {"local_path": scene_glb_path, "expected_revision": scene["revision"]}, private=True)
    elif current_scene is not None:
        scene = await asyncio.to_thread(_http, "GET", "/api/scene")
        await asyncio.to_thread(_http, "PUT", "/api/scene", {"expected_revision": scene["revision"], "objects": current_scene["objects"]}, private=True)
    request = await asyncio.to_thread(_http, "POST", "/api/workspace/external/request", {"session_id": state["session_id"], "cursor": cursor, "message": "请在参考图和当前场景上标出要调整的位置，然后发送给当前 Codex 任务。"}, private=True)
    effective_cursor = request["next_cursor"]
    url = f"{BASE_URL}/?session_id={state['session_id']}"
    opened = False
    if open_browser and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        try:
            opened = bool(await asyncio.wait_for(asyncio.to_thread(webbrowser.open, url, 1, True), timeout=5))
        except Exception:
            logging.exception("Could not open the visual workbench")
    if wait_for_submit and opened:
        result = await _wait_for_external_feedback(state["session_id"], timeout_sec, effective_cursor)
        result["browser_opened"] = opened
        return _visual_tool_result(result)
    response = {**request, "url": url, "browser_opened": opened, "reference_images": _http("GET", "/api/workspace/context")["reference_images"]}
    if wait_for_submit:
        response["message"] = "Open the URL, submit your marks, then call wait_visual_feedback with session_id and next_cursor."
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(response, ensure_ascii=False))], structured_content=response)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False, idempotent_hint=True))
async def wait_visual_feedback(session_id: str, cursor: int = 0, timeout_sec: int = 600) -> CallToolResult:
    """Wait for the next visual feedback packet and return images to this same Codex task."""
    state = _external_state()
    if session_id != state["session_id"]:
        raise ValueError("session_id belongs to a different workspace")
    await asyncio.to_thread(_http, "POST", "/api/workspace/external/request", {"session_id": session_id, "cursor": cursor}, private=True)
    return _visual_tool_result(await _wait_for_external_feedback(session_id, timeout_sec, cursor))


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False, idempotent_hint=True))
def get_visual_feedback(session_id: str, cursor: int = 0) -> CallToolResult:
    """Read submitted visual feedback now, including the original and annotated images."""
    state = _external_state()
    if session_id != state["session_id"]:
        raise ValueError("session_id belongs to a different workspace")
    if type(cursor) is not int or cursor < 0:
        raise ValueError("cursor must be a nonnegative integer")
    query = urlencode({"session_id": session_id, "cursor": cursor})
    result = _http("GET", f"/api/workspace/external/feedback?{query}", private=True)
    result["url"] = f"{BASE_URL}/?session_id={session_id}"
    return _visual_tool_result(result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    mcp.run()

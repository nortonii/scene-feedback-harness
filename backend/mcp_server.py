"""Official MCP SDK stdio tools for the local 3D scene feedback harness."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from server import DEFAULT_DATA_DIR, DEFAULT_WEB_DIR, make_server


PORT = int(os.environ.get("SCENE_FEEDBACK_PORT", "18765"))
DATA_DIR = Path(os.environ.get("SCENE_FEEDBACK_DATA_DIR", str(DEFAULT_DATA_DIR))).expanduser().resolve()
WEB_DIR = Path(os.environ.get("SCENE_FEEDBACK_WEB_DIR", str(DEFAULT_WEB_DIR))).expanduser().resolve()
BASE_URL = f"http://127.0.0.1:{PORT}"
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_server_lock = threading.Lock()
_server = None


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


def _feedback_with_local_paths(result: dict[str, Any]) -> dict[str, Any]:
    for item in result.get("items", []):
        screenshot_url = item.get("screenshot_url")
        if screenshot_url:
            item["screenshot_path"] = str(DATA_DIR / "screenshots" / screenshot_url.rsplit("/", 1)[-1])
    return result


async def _wait_for_feedback(session_id: str, timeout_sec: int) -> dict[str, Any]:
    if not 1 <= timeout_sec <= 3600:
        raise ValueError("timeout_sec must be between 1 and 3600")
    deadline = asyncio.get_running_loop().time() + timeout_sec
    while True:
        session = await asyncio.to_thread(_http, "GET", f"/api/sessions/{session_id}")
        if session["status"] != "open":
            result = await asyncio.to_thread(_http, "GET", f"/api/sessions/{session_id}/feedback")
            result["url"] = f"{BASE_URL}/?session_id={session_id}"
            return _feedback_with_local_paths(result)
        if asyncio.get_running_loop().time() >= deadline:
            return {"session_id": session_id, "status": "timeout", "items": [], "url": f"{BASE_URL}/?session_id={session_id}", "message": "Session remains open; call wait_scene_feedback or get_scene_feedback later."}
        await asyncio.sleep(0.35)


mcp = MCPServer("scene-feedback-harness")


@mcp.tool()
def get_scene() -> dict[str, Any]:
    """Read the current editable scene, its object IDs, transforms, and revision."""
    ensure_http_server()
    return _http("GET", "/api/scene")


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


@mcp.tool()
async def wait_scene_feedback(session_id: str, timeout_sec: int = 600) -> dict[str, Any]:
    """Wait for a previously opened 3D review session to be submitted or cancelled."""
    ensure_http_server()
    return await _wait_for_feedback(session_id, timeout_sec)


@mcp.tool()
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

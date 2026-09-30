"""HTTP API and static 3D review page. No web framework is required."""

from __future__ import annotations

import argparse
import errno
import hmac
import hashlib
from http.cookies import CookieError, SimpleCookie
import ipaddress
import json
import logging
import mimetypes
import os
import re
import signal
import sys
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

try:
    import fcntl
except ImportError:  # pragma: no cover - this server requires Unix file locking
    fcntl = None

from core import APIError, SceneStore
from gateway import WorkspaceGateway
from projects import ProjectContext, ProjectRegistry
from human_pose import HumanPoseJobs


HERE = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = HERE / "data"
DEFAULT_WEB_DIR = HERE.parent / "web"
MAX_REQUEST_BYTES = 64 * 1024 * 1024
SESSION_ROUTE = re.compile(r"^/api/sessions/([0-9a-f]{32})(?:/(feedback|cancel|references|clip))?$")
MEDIA_ROUTE = re.compile(r"^/(assets|screenshots|media)/([0-9a-f]{32}\.(?:glb|png|jpg))$")
WORKSPACE_QUEUE_ROUTE = re.compile(r"^/api/workspace/queue/([0-9a-f]{32})/confirm$")
WORKSPACE_APPROVAL_ROUTE = re.compile(r"^/api/workspace/approvals/([0-9a-f]{32})/respond$")
WORKSPACE_FEEDBACK_ROUTE = re.compile(r"^/api/workspace/feedback/([0-9a-f]{32})$")
PROJECT_ROUTE = re.compile(r"^/p/([0-9a-f]{32})(/.*)?$")
POSE_ROUTE = re.compile(r"^/api/workspace/pose/([0-9a-f]{32})(/cancel)?$")


class _DataDirLock:
    """Keep a second server from overwriting this data directory's state."""

    def __init__(self, data_dir: str | Path):
        if fcntl is None:
            raise RuntimeError("scene-feedback server requires Unix fcntl file locking")
        directory = Path(data_dir).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / ".scene-feedback-server.lock"
        self._fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self._fd)
            self._fd = None
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise RuntimeError(f"another scene-feedback server is already using data directory {directory}") from exc
            raise

    def close(self) -> None:
        if self._fd is not None:
            fd, self._fd = self._fd, None
            os.close(fd)


def _make_server_unlocked(
    *,
    port: int = 18765,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    web_dir: str | Path = DEFAULT_WEB_DIR,
    project_dir: str | Path | None = None,
    model: str | None = None,
    enable_codex: bool = False,
    external_review: bool = False,
    feedback_transport: str = "legacy",
    shared_thread_id: str | None = None,
    adapter: object | None = None,
    listen_host: str = "127.0.0.1",
    public_base_url: str | None = None,
) -> ThreadingHTTPServer:
    if feedback_transport == "mcp_events":
        if enable_codex or shared_thread_id or adapter is not None:
            raise ValueError("MCP events cannot use a Codex App Server or shared desktop adapter")
        external_review = True
    if external_review and enable_codex:
        raise ValueError("external review cannot start a separate Codex App Server thread")
    if shared_thread_id and (not external_review or adapter is not None):
        raise ValueError("--shared-thread-id requires --external-review without another adapter")
    if listen_host not in {"127.0.0.1", "localhost"} and not public_base_url:
        raise ValueError("--public-base-url is required when listening beyond loopback")
    if public_base_url:
        public_url = urlsplit(public_base_url)
        if (public_url.scheme not in {"http", "https"} or not public_url.hostname
                or public_url.username or public_url.password or public_url.path not in {"", "/"}
                or public_url.query or public_url.fragment):
            raise ValueError("--public-base-url must be an http(s) origin without a path or query")
        try:
            configured_port = public_url.port
        except ValueError as exc:
            raise ValueError("--public-base-url has an invalid port") from exc
        if configured_port != port:
            raise ValueError("--public-base-url must use the listening port")
        public_base_url = public_base_url.rstrip("/")
    lan_mode = public_base_url is not None
    store = SceneStore(data_dir)
    web_root = Path(web_dir).expanduser().resolve()
    project_root = Path(project_dir or os.environ.get("SCENE_FEEDBACK_PROJECT_DIR", HERE.parent)).expanduser().resolve()
    gateway = WorkspaceGateway(store, project_root, adapter=adapter, external_review=external_review, feedback_transport=feedback_transport)
    if shared_thread_id:
        from shared_thread_adapter import SharedDesktopAdapter
        # The CLI ID bootstraps the first binding. A later user-selected task
        # is durable and must survive service restarts with the same unit.
        persisted_id = (store.state.get("workspace") or {}).get("thread_id")
        target_id = persisted_id or shared_thread_id
        owned_ids = (store.state.get("workspace") or {}).get("created_thread_ids", [])
        kwargs = {"allow_owned_resume": True} if target_id in owned_ids else {}
        gateway.adapter = SharedDesktopAdapter(target_id, on_event=gateway.scoped_adapter_callback(), **kwargs)
    if enable_codex and adapter is None:
        from appserver_adapter import CodexAppServerAdapter
        gateway.adapter = CodexAppServerAdapter(
            project_root,
            state_path=store.data_dir / "codex_app_server_thread.json",
            on_event=gateway.on_adapter_event,
            model=model or os.environ.get("SCENE_FEEDBACK_MODEL"),
            env_overrides={
                "SCENE_FEEDBACK_PORT": str(port),
                "SCENE_FEEDBACK_DATA_DIR": str(store.data_dir),
                "SCENE_FEEDBACK_PROJECT_DIR": str(project_root),
            },
        )

    workspace = store.state.get("workspace", {})
    registry_path = store.data_dir / "project_registry.json"
    root_project_id = workspace.get("project_id")
    if root_project_id is None and registry_path.exists():
        root_project_id = json.loads(registry_path.read_text(encoding="utf-8")).get("default_project_id")
    root_project_id = root_project_id or uuid.uuid4().hex
    root_context = ProjectContext(root_project_id, store.scene().get("name") or project_root.name, store, gateway)

    def browser_url(session_id: str, port_number: int, context: ProjectContext = root_context, *, prefixed: bool = False) -> str:
        base = public_base_url or f"http://127.0.0.1:{port_number}"
        prefix = f"/p/{context.project_id}" if prefixed or context is not root_context else ""
        query = {"session_id": session_id}
        if lan_mode:
            query["access_token"] = store.browser_token
        return f"{base}{prefix}/?{urlencode(query)}"

    class Handler(BaseHTTPRequestHandler):
        server_version = "SceneFeedbackHarness/0.1"

        def _check_request_origin(self) -> None:
            port_number = self.server.server_port
            allowed = {f"127.0.0.1:{port_number}", f"localhost:{port_number}"}
            origins = {f"http://{host}" for host in allowed}
            if public_base_url:
                public_url = urlsplit(public_base_url)
                allowed.add(public_url.netloc)
                origins.add(public_base_url)
            if self.headers.get("Host") not in allowed:
                raise APIError(403, "request host is not configured for this workbench")
            origin = self.headers.get("Origin")
            if origin and origin not in origins:
                raise APIError(403, "cross-origin request refused")

        def _browser_url(self, session_id: str) -> str:
            return browser_url(session_id, self.server.server_port, self.context, prefixed=bool(self.project_prefix))

        def _select_context(self, path: str) -> str:
            match = PROJECT_ROUTE.fullmatch(path)
            context = registry.get(match.group(1) if match else root_context.project_id)
            if path.startswith("/p/") and match is None:
                raise APIError(404, "reconstruction project not found")
            key = self.headers.get("X-Scene-Harness-Key")
            if key is not None:
                token_context = registry.by_control_token(key)
                if match and token_context is not context:
                    raise APIError(403, "MCP control key belongs to another reconstruction project")
                context = token_context
            self.context = context
            self.project_prefix = f"/p/{match.group(1)}" if match else ""
            context.gateway.desktop_seed_thread_id = (
                (store.state.get("workspace") or {}).get("thread_id") or getattr(gateway.adapter, "thread_id", None)
            )
            return (match.group(2) or "/") if match else path

        def _has_lan_access(self) -> bool:
            if hmac.compare_digest(self.headers.get("X-Scene-Harness-Key", ""), self.context.store.control_token):
                return True
            try:
                cookies = SimpleCookie()
                cookies.load(self.headers.get("Cookie", ""))
                cookie = cookies.get(f"scene_feedback_{self.server.server_port}_access")
            except CookieError:
                return False
            return cookie is not None and hmac.compare_digest(cookie.value, store.browser_token)

        def _needs_lan_access(self) -> bool:
            if not lan_mode:
                return False
            try:
                loopback_peer = ipaddress.ip_address(self.client_address[0]).is_loopback
            except ValueError:
                loopback_peer = False
            # Existing local MCP processes use the loopback API address. A remote
            # peer cannot bypass access control by forging a loopback Host header.
            return not loopback_peer or self.headers.get("Host") == urlsplit(public_base_url).netloc

        def _bootstrap_lan_access(self, path: str, query: dict[str, list[str]]) -> bool:
            if not lan_mode or self.command != "GET" or path != "/" or "access_token" not in query:
                return False
            submitted = query.pop("access_token")
            if len(submitted) != 1 or not registry.accepts_browser_token(submitted[0]):
                raise APIError(403, "invalid workbench access link")
            location = self.project_prefix + "/" + (f"?{urlencode(query, doseq=True)}" if query else "")
            cookie = f"scene_feedback_{self.server.server_port}_access={store.browser_token}; HttpOnly; SameSite=Lax; Path=/"
            if urlsplit(public_base_url).scheme == "https":
                cookie += "; Secure"
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Set-Cookie", cookie)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True

        def _require_control_key(self) -> None:
            key = self.headers.get("X-Scene-Harness-Key", "")
            if not hmac.compare_digest(key, self.context.store.control_token):
                raise APIError(403, "this operation requires the local MCP control key")

        def _require_browser_capability(self) -> None:
            key = self.headers.get("X-Workspace-Capability", "")
            if not hmac.compare_digest(key, self.context.store.browser_token):
                raise APIError(403, "this operation requires the workspace browser capability")

        def _require_pose_access(self) -> None:
            key = self.headers.get("X-Scene-Harness-Key", "")
            if hmac.compare_digest(key, self.context.store.control_token):
                self._require_control_key()
            else:
                self._require_browser_capability()

        def _read_json(self) -> dict:
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise APIError(415, "request body must be application/json")
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError as exc:
                raise APIError(411, "Content-Length is required") from exc
            if not 0 <= length <= MAX_REQUEST_BYTES:
                raise APIError(413, "request is too large")
            try:
                payload = json.loads(self.rfile.read(length))
            except (ValueError, UnicodeDecodeError) as exc:
                raise APIError(400, "invalid JSON body") from exc
            if not isinstance(payload, dict):
                raise APIError(400, "JSON body must be an object")
            return payload

        def _send_json(self, status: int, value: dict) -> None:
            data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(data)

        def _send_file(self, file_path: Path) -> None:
            if not file_path.is_file():
                raise APIError(404, "file not found")
            content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(file_path.stat().st_size))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if lan_mode:
                self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with file_path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    self.wfile.write(chunk)

        def _dispatch(self) -> None:
            self._check_request_origin()
            parsed = urlsplit(self.path)
            path = self._select_context(unquote(parsed.path))
            query = parse_qs(parsed.query)
            if path == "/mcp":
                supplied = self.headers.get("Authorization", "")
                if not hmac.compare_digest(supplied, "Bearer " + self.context.store.control_token):
                    raise APIError(401, "MCP endpoint requires this project's bearer credential")
                if self.command != "POST":
                    raise APIError(405, "this stateless MCP endpoint accepts POST")
                try:
                    rpc_length = int(self.headers.get("Content-Length", "0"))
                except ValueError as exc:
                    raise APIError(411, "valid Content-Length is required") from exc
                if rpc_length > 1024 * 1024:
                    raise APIError(413, "MCP request exceeds 1 MiB")
                from plugin_rpc import PluginRPC
                result = PluginRPC(self.context).handle(self._read_json())
                if result is None:
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                return self._send_json(200, result)
            if self._bootstrap_lan_access(path, query):
                return
            if self._needs_lan_access() and not self._has_lan_access():
                raise APIError(403, "open a workbench access link first")

            store = self.context.store
            gateway = self.context.gateway
            project_root = self.context.project_dir
            if self.command == "GET" and path == "/api/projects":
                return self._send_json(200, registry.list(self.context.project_id))
            if self.command == "POST" and path == "/api/projects":
                if self.context.gateway.feedback_transport == "mcp_events":
                    raise APIError(409, "create a separate event workspace instead of a Codex-bound project")
                self._require_browser_capability()
                return self._send_json(201, registry.create(self._read_json()))

            if self.command == "GET" and path == "/api/health":
                return self._send_json(200, {"service": "scene-feedback-harness", "status": "ok", "scene_revision": store.scene()["revision"], "workspace_gateway": True, "reference_multiview": True, "projects_supported": True, "project_id": self.context.project_id, "project_dir": str(project_root), "data_dir": str(store.data_dir), "delivery_mode": "external" if gateway.external_review else "appserver", "feedback_transport": gateway.feedback_transport})
            if self.command == "GET" and path == "/api/workspace/state":
                state = gateway.state(include_capability=True, preferred_session_id=query.get("session_id", [None])[0])
                state["browser_url"] = self._browser_url(state["session_id"])
                return self._send_json(200, state)
            if self.command == "GET" and path == "/api/workspace/events":
                gateway.ensure()
                return self._send_json(200, store.workspace_events(self._event_cursor(query)))
            if self.command == "GET" and path == "/api/workspace/context":
                workspace = gateway.state()
                return self._send_json(200, {"project_id": workspace["project_id"], "project_dir": workspace["project_dir"], "session_id": workspace["session_id"], "thread_id": workspace["thread_id"], "delivery_mode": workspace["delivery_mode"], "scene": store.scene(), "reference_images": store.get_session(workspace["session_id"])["reference_images"], "reference_clip": workspace["reference_clip"], "request_feedback": workspace["request_feedback"]})
            if self.command == "GET" and path == "/api/workspace/targets":
                return self._send_json(200, gateway.list_targets())
            if self.command == "GET" and path == "/api/workspace/models":
                return self._send_json(200, gateway.list_models())
            if self.command == "GET" and path == "/api/workspace/pose":
                session_id = query.get("session_id", [None])[0] or gateway.state()["session_id"]
                return self._send_json(200, gateway.pose_jobs.list(session_id))
            if self.command == "POST" and path == "/api/workspace/pose":
                self._require_pose_access()
                return self._send_json(202, gateway.pose_jobs.start(self._read_json()))
            pose_match = POSE_ROUTE.fullmatch(path)
            if pose_match:
                job_id, cancel = pose_match.groups()
                if self.command == "GET" and not cancel:
                    offset_text = query.get("frame_offset", ["0"])
                    limit_text = query.get("max_frames", [None])
                    reference_ids = query.get("reference_id", [None])
                    view_ids = query.get("view_id", [None])
                    download = query.get("download", ["0"])
                    if any(len(values) != 1 for values in (offset_text, limit_text, reference_ids, view_ids, download)):
                        raise APIError(400, "pose query parameters must occur once")
                    try:
                        offset = int(offset_text[0])
                        limit = int(limit_text[0]) if limit_text[0] is not None else None
                    except ValueError as exc:
                        raise APIError(400, "pose frame_offset and max_frames must be integers") from exc
                    if offset < 0 or limit is not None and not 1 <= limit <= 32:
                        raise APIError(400, "pose frame_offset must be nonnegative and max_frames must be between 1 and 32")
                    reference_id, view_id = reference_ids[0], view_ids[0]
                    if reference_id is not None and (not re.fullmatch(r"[0-9a-f]{32}", reference_id) or view_id is not None):
                        raise APIError(400, "pose reference_id must be a valid ID without view_id")
                    if view_id is not None and not re.fullmatch(r"[0-9a-f]{32}", view_id):
                        raise APIError(400, "pose view_id must be a valid ID")
                    if download[0] not in {"0", "1"}:
                        raise APIError(400, "pose download must be 0 or 1")
                    if download[0] == "1":
                        if limit is not None or offset or reference_id is not None or view_id is not None:
                            raise APIError(400, "pose download cannot be combined with frame filters")
                        limit = "all"
                    return self._send_json(200, gateway.pose_jobs.get(job_id, frame_offset=offset, max_frames=limit,
                                                                       reference_id=reference_id, view_id=view_id))
                if self.command == "POST" and cancel:
                    self._require_pose_access()
                    self._read_json()
                    return self._send_json(200, gateway.pose_jobs.cancel(job_id))
            if self.command == "POST" and path == "/api/workspace/targets":
                self._require_browser_capability()
                payload = self._read_json()
                return self._send_json(201, gateway.create_target(
                    payload.get("model"),
                    reasoning_effort=payload.get("reasoning_effort"),
                    title=payload.get("title"),
                    permission_mode=payload.get("permission_mode", "workspace_write"),
                ))
            if self.command == "POST" and path == "/api/workspace/target":
                self._require_browser_capability()
                payload = self._read_json()
                return self._send_json(200, gateway.switch_target(payload.get("thread_id")))
            if self.command == "POST" and path == "/api/workspace/references":
                self._require_control_key()
                payload = self._read_json()
                return self._send_json(200, gateway.add_reference_paths(payload.get("reference_images")))
            if self.command == "POST" and path == "/api/workspace/clip":
                self._require_control_key()
                return self._send_json(200, gateway.set_reference_clip_paths(self._read_json()))
            if self.command == "POST" and path == "/api/workspace/reference-cameras":
                self._require_control_key()
                payload = self._read_json()
                if payload.get("apply_manifest") is True and "cameras" not in payload:
                    return self._send_json(200, gateway.apply_reference_camera_manifest())
                if "cameras" not in payload or "apply_manifest" in payload:
                    raise APIError(400, "provide cameras or apply_manifest")
                return self._send_json(200, gateway.set_reference_cameras(payload["cameras"]))
            if self.command == "POST" and path == "/api/workspace/external/request":
                self._require_control_key()
                payload = self._read_json()
                return self._send_json(200, gateway.begin_external_request(session_id=payload.get("session_id"), message=payload.get("message"), cursor=payload.get("cursor")))
            if self.command == "GET" and path == "/api/workspace/external/feedback":
                self._require_control_key()
                return self._send_json(200, gateway.take_external_feedback(query.get("session_id", [""])[0], self._cursor(query)))
            feedback_match = WORKSPACE_FEEDBACK_ROUTE.fullmatch(path)
            if self.command == "GET" and feedback_match:
                return self._send_json(200, store.feedback_by_id(feedback_match.group(1)))
            queue_match = WORKSPACE_QUEUE_ROUTE.fullmatch(path)
            if self.command == "POST" and queue_match:
                self._require_browser_capability()
                return self._send_json(200, gateway.confirm_queue(queue_match.group(1), self._read_json()))
            approval_match = WORKSPACE_APPROVAL_ROUTE.fullmatch(path)
            if self.command == "POST" and approval_match:
                self._require_browser_capability()
                return self._send_json(200, gateway.respond_to_approval(approval_match.group(1), self._read_json()))
            if self.command == "POST" and path == "/api/workspace/interrupt":
                self._require_browser_capability()
                self._read_json()
                return self._send_json(200, gateway.interrupt())
            if self.command == "POST" and path == "/api/workspace/request-feedback":
                self._require_control_key()
                payload = self._read_json()
                gateway.ensure()
                return self._send_json(200, store.workspace_request_feedback(payload.get("message"), object_ids=payload.get("object_ids")))
            if self.command == "POST" and path == "/api/workspace/publish":
                self._require_control_key()
                payload = self._read_json()
                gateway.ensure()
                if "expected_revision" not in payload:
                    raise APIError(400, "expected_revision is required")
                if gateway.external_review:
                    gateway._project_file(payload.get("local_path"))
                scene = store.set_scene_preview(payload.get("local_path"), expected_revision=payload["expected_revision"])
                return self._send_json(200, scene)
            if self.command == "GET" and path == "/api/scene":
                return self._send_json(200, store.scene())
            if self.command == "PUT" and path == "/api/scene":
                self._require_control_key()
                payload = self._read_json()
                return self._send_json(200, store.replace_scene(payload.get("expected_revision"), payload.get("objects")))
            if self.command == "POST" and path == "/api/scene/update":
                self._require_control_key()
                payload = self._read_json()
                return self._send_json(200, store.update_scene(payload.get("expected_revision"), payload.get("changes")))
            if self.command == "POST" and path == "/api/scene/preview":
                self._require_control_key()
                payload = self._read_json()
                return self._send_json(200, store.set_scene_preview(payload.get("local_path")))
            if self.command == "POST" and path == "/api/models/import":
                self._require_control_key()
                payload = self._read_json()
                return self._send_json(201, store.import_model(payload.get("local_path"), object_id=payload.get("object_id"), name=payload.get("name"), position=payload.get("position"), size=payload.get("size")))
            if self.command == "POST" and path == "/api/sessions":
                payload = self._read_json()
                if "reference_images" in payload:
                    self._require_control_key()
                session = store.create_session(payload.get("reference_images"), reference_session_id=payload.get("reference_session_id"))
                session["url"] = self._browser_url(session["session_id"])
                return self._send_json(201, session)
            if self.command == "GET" and path == "/api/sessions":
                return self._send_json(200, {"items": store.list_sessions()})
            if self.command == "GET" and path == "/api/feedback":
                session_id = query.get("session_id", [None])[0]
                if session_id:
                    cursor = self._cursor(query)
                    return self._send_json(200, store.feedback(session_id, cursor))
                self._require_control_key()
                return self._send_json(200, {"items": store.list_all_feedback()})
            session_match = SESSION_ROUTE.fullmatch(path)
            if session_match:
                session_id, suffix = session_match.groups()
                if self.command == "GET" and suffix is None:
                    return self._send_json(200, store.get_session(session_id))
                if self.command == "GET" and suffix == "feedback":
                    return self._send_json(200, store.feedback(session_id, self._cursor(query)))
                if self.command == "POST" and suffix == "feedback":
                    self._require_browser_capability()
                    return self._send_json(201, gateway.submit(session_id, self._read_json()))
                if self.command == "POST" and suffix == "references":
                    self._require_browser_capability()
                    payload = self._read_json()
                    return self._send_json(201, gateway.add_reference_data_url(session_id, payload.get("name"), payload.get("data_url")))
                if self.command == "POST" and suffix == "clip":
                    self._require_browser_capability()
                    return self._send_json(201, store.set_reference_clip(session_id, self._read_json()))
                if self.command == "POST" and suffix == "cancel":
                    self._require_browser_capability()
                    self._read_json()
                    return self._send_json(200, store.cancel_session(session_id))
            if self.command == "GET":
                media_match = MEDIA_ROUTE.fullmatch(path)
                if media_match:
                    directory, name = media_match.groups()
                    if directory == "assets" and name.endswith(".glb"):
                        return self._send_file(store.assets_dir / name)
                    if directory == "screenshots" and name.endswith((".png", ".jpg")):
                        return self._send_file(store.screenshots_dir / name)
                    if directory == "media" and name.endswith((".png", ".jpg")):
                        return self._send_file(store.media_dir / name)
                    raise APIError(404, "file not found")
                if path == "/":
                    return self._send_file(web_root / "index.html")
                # Static assets are restricted to the web directory.
                relative = Path(path.lstrip("/"))
                candidate = (web_root / relative).resolve()
                if web_root in candidate.parents and candidate.is_file():
                    return self._send_file(candidate)
            raise APIError(404, "endpoint not found")

        @staticmethod
        def _cursor(query: dict) -> int:
            try:
                return int(query.get("cursor", ["0"])[0])
            except ValueError as exc:
                raise APIError(400, "cursor must be a nonnegative integer") from exc

        @staticmethod
        def _event_cursor(query: dict) -> int:
            try:
                return int(query.get("after", ["0"])[0])
            except ValueError as exc:
                raise APIError(400, "after must be a nonnegative integer") from exc

        def _handle(self) -> None:
            try:
                self._dispatch()
            except APIError as exc:
                result = {"error": exc.message}
                if exc.detail is not None:
                    result.update(exc.detail)
                self._send_json(exc.status, result)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                logging.error("Unhandled request error:\n%s", traceback.format_exc())
                self._send_json(500, {"error": "internal server error"})

        def do_GET(self) -> None:
            self._handle()

        def do_POST(self) -> None:
            self._handle()

        def do_PUT(self) -> None:
            self._handle()

        def log_message(self, format: str, *args: object) -> None:
            message = format % args
            if "?" in self.path:
                message = message.replace(self.path, urlsplit(self.path).path + "?[redacted]")
            logging.info("%s - %s", self.address_string(), message)

    server = ThreadingHTTPServer((listen_host, port), Handler)
    server.daemon_threads = False
    server.workspace_gateway = gateway
    server.scene_store = store
    server.browser_url = lambda session_id, project_id=None: browser_url(
        session_id, server.server_port, registry.get(project_id) if project_id else root_context
    )

    def configure_context(context: ProjectContext) -> None:
        if context.gateway.pose_jobs is None:
            context.gateway.pose_jobs = HumanPoseJobs(context.store)
        context.gateway.registry_project_id = context.project_id
        if feedback_transport == "mcp_events" and context.gateway.mcp_events is None:
            from mcp_events import MCPEvents
            context.gateway.ensure()
            context.gateway.mcp_events = MCPEvents(
                context.store, context.project_id,
                principal_validator=lambda principal: hmac.compare_digest(
                    principal, hashlib.sha256(context.store.control_token.encode()).hexdigest()),
            )
        context.gateway.project_name = context.name
        context.gateway.desktop_seed_thread_id = (
            (store.state.get("workspace") or {}).get("thread_id") or getattr(gateway.adapter, "thread_id", None)
        )
        context.gateway.thread_config = {"mcp_servers": {"scene_feedback": {
            "command": sys.executable, "args": [str(HERE / "mcp_server.py")],
            "env": {"SCENE_FEEDBACK_PORT": str(server.server_port),
                    "SCENE_FEEDBACK_DATA_DIR": str(context.store.data_dir),
                    "SCENE_FEEDBACK_PROJECT_DIR": str(context.project_dir),
                    "SCENE_FEEDBACK_WEB_DIR": str(web_root),
                    "SCENE_FEEDBACK_TOOLS_VERSION": "2026-09-30-vitpose-one-click"},
            "tool_timeout_sec": 120, "required": True,
        }}}
        context.gateway.browser_url = lambda session_id: browser_url(session_id, server.server_port, context, prefixed=True)
        if hasattr(context.gateway.adapter, "thread_config"):
            context.gateway.adapter.thread_config = context.gateway.thread_config
        if context is root_context and enable_codex and hasattr(context.gateway.adapter, "env_overrides"):
            context.gateway.adapter.env_overrides["SCENE_FEEDBACK_PORT"] = str(server.server_port)
            context.gateway.adapter._thread_config = context.gateway.adapter._project_mcp_config()

    def create_context(record: dict, newly_created: bool) -> ProjectContext:
        child_project = Path(record["project_dir"])
        child_data = Path(record["data_dir"])
        established = record.get("creation_status") == "ready" or any(
            record.get(key) for key in ("session_id", "thread_id", "created_thread_id")
        )
        if not newly_created and established and (
            not child_project.is_dir() or not (child_data / "state.json").is_file()
        ):
            raise RuntimeError("saved reconstruction project is missing its workspace or state; restore its files")
        child_project.parent.parent.mkdir(mode=0o700, exist_ok=True)
        child_project.parent.mkdir(mode=0o700, exist_ok=True)
        child_project.mkdir(mode=0o700, exist_ok=True)
        child_lock = _DataDirLock(child_data)
        child_gateway = None
        try:
            child_store = SceneStore(child_data)
            if newly_created:
                with child_store.lock:
                    child_store.state["scene"]["name"] = record["name"]
                    child_store._save()
            child_gateway = WorkspaceGateway(child_store, child_project, external_review=external_review, feedback_transport=feedback_transport)
            context = ProjectContext(record["project_id"], record["name"], child_store, child_gateway, child_lock)
            configure_context(context)
            child_workspace = child_gateway.ensure()
            if child_workspace["project_id"] != context.project_id:
                raise RuntimeError("managed project ID does not match its stored workspace")
            target_id = child_workspace.get("thread_id")
            if external_review and target_id and feedback_transport == "legacy":
                from shared_thread_adapter import SharedDesktopAdapter
                kwargs = {"allow_owned_resume": True, "thread_config": child_gateway.thread_config} if target_id in child_workspace.get("created_thread_ids", []) else {}
                child_gateway.adapter = SharedDesktopAdapter(target_id, on_event=child_gateway.scoped_adapter_callback(), **kwargs)
            return context
        except BaseException:
            try:
                if child_gateway is not None:
                    child_gateway.close()
            finally:
                child_lock.close()
            raise

    registry = None
    try:
        registry = ProjectRegistry(root_context, context_factory=create_context, configure_context=configure_context)
        server.project_registry = registry
        if enable_codex or external_review:
            gateway.start()
    except BaseException:
        if registry is not None:
            registry.close()
        else:
            gateway.close()
        server.server_close()
        raise
    return server


def make_server(
    *,
    port: int = 18765,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    web_dir: str | Path = DEFAULT_WEB_DIR,
    project_dir: str | Path | None = None,
    model: str | None = None,
    enable_codex: bool = False,
    external_review: bool = False,
    feedback_transport: str = "legacy",
    shared_thread_id: str | None = None,
    adapter: object | None = None,
    listen_host: str = "127.0.0.1",
    public_base_url: str | None = None,
) -> ThreadingHTTPServer:
    """Create one server for a data directory, releasing its lock on close."""
    data_lock = _DataDirLock(data_dir)
    try:
        server = _make_server_unlocked(
            port=port,
            data_dir=data_dir,
            web_dir=web_dir,
            project_dir=project_dir,
            model=model,
            enable_codex=enable_codex,
            external_review=external_review,
            feedback_transport=feedback_transport,
            shared_thread_id=shared_thread_id,
            adapter=adapter,
            listen_host=listen_host,
            public_base_url=public_base_url,
        )
    except BaseException:
        data_lock.close()
        raise

    original_close = server.server_close

    def close_with_data_lock() -> None:
        try:
            original_close()
        finally:
            try:
                server.project_registry.close()
            finally:
                data_lock.close()

    server.server_close = close_with_data_lock
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="3D scene review HTTP server")
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--listen-host", default="127.0.0.1", help="HTTP bind address; use 0.0.0.0 for LAN access")
    parser.add_argument("--public-base-url", help="Browser URL on the LAN, e.g. http://192.168.1.10:18765; enables access-link authentication")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--web-dir", type=Path, default=DEFAULT_WEB_DIR)
    parser.add_argument("--project-dir", type=Path, default=Path(os.environ.get("SCENE_FEEDBACK_PROJECT_DIR", HERE.parent)))
    parser.add_argument("--model", default=os.environ.get("SCENE_FEEDBACK_MODEL"), help="Codex model ID for this project thread")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--no-codex", action="store_true", help="serve the workbench without starting Codex App Server")
    mode.add_argument("--mcp-events", action="store_true", help="opt in to MCP 2026-07-28 webhook feedback; no Codex adapter")
    mode.add_argument("--external-review", action="store_true", help="review in an existing Codex task")
    parser.add_argument("--shared-thread-id", help="deliver submitted feedback into this already-open Codex Desktop task")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    server = make_server(
        port=args.port,
        data_dir=args.data_dir,
        web_dir=args.web_dir,
        project_dir=args.project_dir,
        model=args.model,
        enable_codex=not args.no_codex and not args.external_review and not args.mcp_events,
        external_review=args.external_review or args.mcp_events,
        feedback_transport="mcp_events" if args.mcp_events else "legacy",
        shared_thread_id=args.shared_thread_id,
        listen_host=args.listen_host,
        public_base_url=args.public_base_url,
    )
    session_id = server.workspace_gateway.state()["session_id"]
    print(f"Scene feedback UI: {server.browser_url(session_id)}", flush=True)
    def terminate_server(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate_server)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

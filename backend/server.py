"""Loopback HTTP API and static 3D review page. No web framework is required."""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import mimetypes
import os
import re
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from core import APIError, SceneStore
from gateway import WorkspaceGateway


HERE = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = HERE / "data"
DEFAULT_WEB_DIR = HERE.parent / "web"
MAX_REQUEST_BYTES = 64 * 1024 * 1024
SESSION_ROUTE = re.compile(r"^/api/sessions/([0-9a-f]{32})(?:/(feedback|cancel|references))?$")
MEDIA_ROUTE = re.compile(r"^/(assets|screenshots|media)/([0-9a-f]{32}\.(?:glb|png|jpg))$")
WORKSPACE_QUEUE_ROUTE = re.compile(r"^/api/workspace/queue/([0-9a-f]{32})/confirm$")
WORKSPACE_APPROVAL_ROUTE = re.compile(r"^/api/workspace/approvals/([0-9a-f]{32})/respond$")
WORKSPACE_FEEDBACK_ROUTE = re.compile(r"^/api/workspace/feedback/([0-9a-f]{32})$")


def make_server(*, port: int = 18765, data_dir: str | Path = DEFAULT_DATA_DIR, web_dir: str | Path = DEFAULT_WEB_DIR, project_dir: str | Path | None = None, model: str | None = None, enable_codex: bool = False, external_review: bool = False, adapter: object | None = None) -> ThreadingHTTPServer:
    if external_review and (enable_codex or adapter is not None):
        raise ValueError("external review cannot start a separate Codex App Server thread")
    store = SceneStore(data_dir)
    web_root = Path(web_dir).expanduser().resolve()
    project_root = Path(project_dir or os.environ.get("SCENE_FEEDBACK_PROJECT_DIR", HERE.parent)).expanduser().resolve()
    gateway = WorkspaceGateway(store, project_root, adapter=adapter, external_review=external_review)
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

    class Handler(BaseHTTPRequestHandler):
        server_version = "SceneFeedbackHarness/0.1"

        def _check_request_origin(self) -> None:
            port_number = self.server.server_port
            allowed = {f"127.0.0.1:{port_number}", f"localhost:{port_number}"}
            if self.headers.get("Host") not in allowed:
                raise APIError(403, "request must use the loopback host")
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://{host}" for host in allowed}:
                raise APIError(403, "cross-origin request refused")

        def _require_control_key(self) -> None:
            key = self.headers.get("X-Scene-Harness-Key", "")
            if not hmac.compare_digest(key, store.control_token):
                raise APIError(403, "this operation requires the local MCP control key")

        def _require_browser_capability(self) -> None:
            key = self.headers.get("X-Workspace-Capability", "")
            if not hmac.compare_digest(key, store.browser_token):
                raise APIError(403, "this operation requires the workspace browser capability")

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
            self.end_headers()
            with file_path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    self.wfile.write(chunk)

        def _dispatch(self) -> None:
            self._check_request_origin()
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query)

            if self.command == "GET" and path == "/api/health":
                return self._send_json(200, {"service": "scene-feedback-harness", "status": "ok", "scene_revision": store.scene()["revision"], "workspace_gateway": True, "project_dir": str(project_root), "data_dir": str(store.data_dir), "delivery_mode": "external" if external_review else "appserver"})
            if self.command == "GET" and path == "/api/workspace/state":
                return self._send_json(200, gateway.state(include_capability=True, preferred_session_id=query.get("session_id", [None])[0]))
            if self.command == "GET" and path == "/api/workspace/events":
                gateway.ensure()
                return self._send_json(200, store.workspace_events(self._event_cursor(query)))
            if self.command == "GET" and path == "/api/workspace/context":
                workspace = gateway.state()
                return self._send_json(200, {"project_id": workspace["project_id"], "project_dir": workspace["project_dir"], "session_id": workspace["session_id"], "thread_id": workspace["thread_id"], "delivery_mode": workspace["delivery_mode"], "scene": store.scene(), "reference_images": store.get_session(workspace["session_id"])["reference_images"], "request_feedback": workspace["request_feedback"]})
            if self.command == "POST" and path == "/api/workspace/references":
                self._require_control_key()
                payload = self._read_json()
                return self._send_json(200, gateway.add_reference_paths(payload.get("reference_images")))
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
                if external_review:
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
                session["url"] = f"http://127.0.0.1:{self.server.server_port}/?session_id={session['session_id']}"
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
                    return self._send_json(201, store.add_reference(session_id, payload.get("name"), payload.get("data_url")))
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
                self._send_json(exc.status, {"error": exc.message})
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
            logging.info("%s - %s", self.address_string(), format % args)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.workspace_gateway = gateway
    server.scene_store = store
    if enable_codex or external_review:
        gateway.start()
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Local 3D scene review HTTP server")
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--web-dir", type=Path, default=DEFAULT_WEB_DIR)
    parser.add_argument("--project-dir", type=Path, default=Path(os.environ.get("SCENE_FEEDBACK_PROJECT_DIR", HERE.parent)))
    parser.add_argument("--model", default=os.environ.get("SCENE_FEEDBACK_MODEL"), help="Codex model ID for this project thread")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--no-codex", action="store_true", help="serve the workbench without starting Codex App Server")
    mode.add_argument("--external-review", action="store_true", help="wait for an existing Codex task to receive feedback through an MCP tool call")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    server = make_server(port=args.port, data_dir=args.data_dir, web_dir=args.web_dir, project_dir=args.project_dir, model=args.model, enable_codex=not args.no_codex and not args.external_review, external_review=args.external_review)
    print(f"Scene feedback UI: http://127.0.0.1:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.workspace_gateway.close()
        server.server_close()


if __name__ == "__main__":
    main()

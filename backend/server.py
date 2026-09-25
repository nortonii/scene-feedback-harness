"""Loopback HTTP API and static 3D review page. No web framework is required."""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import mimetypes
import re
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from core import APIError, SceneStore


HERE = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = HERE / "data"
DEFAULT_WEB_DIR = HERE.parent / "web"
MAX_REQUEST_BYTES = 8 * 1024 * 1024
SESSION_ROUTE = re.compile(r"^/api/sessions/([0-9a-f]{32})(?:/(feedback|cancel))?$")
MEDIA_ROUTE = re.compile(r"^/(assets|screenshots)/([0-9a-f]{32}\.(?:glb|png|jpg))$")


def make_server(*, port: int = 18765, data_dir: str | Path = DEFAULT_DATA_DIR, web_dir: str | Path = DEFAULT_WEB_DIR) -> ThreadingHTTPServer:
    store = SceneStore(data_dir)
    web_root = Path(web_dir).expanduser().resolve()

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
                return self._send_json(200, {"service": "scene-feedback-harness", "status": "ok", "scene_revision": store.scene()["revision"]})
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
            if self.command == "POST" and path == "/api/models/import":
                self._require_control_key()
                payload = self._read_json()
                return self._send_json(201, store.import_model(payload.get("local_path"), object_id=payload.get("object_id"), name=payload.get("name"), position=payload.get("position"), size=payload.get("size")))
            if self.command == "POST" and path == "/api/sessions":
                self._read_json()
                session = store.create_session()
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
                    return self._send_json(201, store.submit_feedback(session_id, self._read_json()))
                if self.command == "POST" and suffix == "cancel":
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
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Local 3D scene review HTTP server")
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--web-dir", type=Path, default=DEFAULT_WEB_DIR)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    server = make_server(port=args.port, data_dir=args.data_dir, web_dir=args.web_dir)
    print(f"Scene feedback UI: http://127.0.0.1:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

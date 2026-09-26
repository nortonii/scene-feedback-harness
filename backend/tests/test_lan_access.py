"""LAN page access keeps workspace data behind an explicit browser link."""

from __future__ import annotations

import base64
import io
import json
import socket
import struct
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from PIL import Image


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import make_server  # noqa: E402


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _tiny_glb(path: Path) -> None:
    scene = json.dumps({"asset": {"version": "2.0"}, "scenes": [{}], "scene": 0}).encode()
    scene += b" " * (-len(scene) % 4)
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(scene)) + struct.pack("<I4s", len(scene), b"JSON") + scene)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class LANAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        web_dir = self.root / "web"
        web_dir.mkdir()
        (web_dir / "index.html").write_text("<html>LAN viewer</html>", encoding="utf-8")
        (web_dir / "app.js").write_text("// LAN asset", encoding="utf-8")
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.server = make_server(
            port=self.port,
            data_dir=self.root / "data",
            web_dir=web_dir,
            project_dir=self.project,
            external_review=True,
            listen_host="0.0.0.0",
            public_base_url=self.base,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.control_key = (self.root / "data" / "control_token").read_text(encoding="ascii").strip()
        self.browser_token = (self.root / "data" / "browser_token").read_text(encoding="ascii").strip()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temporary.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None, *, cookie: str = "", control: bool = False, capability: bool = False, origin: str = "", host: str = ""):
        headers = {"Accept": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        if control:
            headers["X-Scene-Harness-Key"] = self.control_key
        if capability:
            headers["X-Workspace-Capability"] = self.browser_token
        if origin:
            headers["Origin"] = origin
        if host:
            headers["Host"] = host
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        try:
            response = self.opener.open(request, timeout=10)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            data = response.read()
            if response.headers.get_content_type() == "application/json" and data:
                data = json.loads(data)
            return response.code, response.headers, data

    def _access_cookie(self) -> str:
        session = self.server.scene_store.create_session()
        path = f"/?session_id={session['session_id']}&access_token={self.browser_token}"
        status, headers, body = self.request("GET", path)
        self.assertEqual(status, 303)
        self.assertEqual(body, b"")
        location = headers.get("Location", "")
        self.assertIn(f"session_id={session['session_id']}", location)
        self.assertNotIn("access_token", location)
        set_cookie = headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=Lax", set_cookie)
        self.assertIn("Path=/", set_cookie)
        return set_cookie.split(";", 1)[0]

    def test_default_listener_is_loopback(self) -> None:
        other = make_server(port=0, data_dir=self.root / "other-data", web_dir=self.root / "web", project_dir=self.project)
        try:
            self.assertEqual(other.server_address[0], "127.0.0.1")
        finally:
            other.server_close()

    def test_lan_link_bootstraps_authenticated_page_and_media(self) -> None:
        self.assertEqual(self.server.server_address[0], "0.0.0.0")
        model = self.project / "model.glb"
        _tiny_glb(model)
        imported = self.server.scene_store.import_model(str(model), object_id="model")
        asset_path = imported["object"]["url"]
        for path in ("/", "/app.js", "/api/health", "/api/workspace/state", "/api/scene", asset_path):
            with self.subTest(path=path):
                status, _, _ = self.request("GET", path)
                self.assertEqual(status, 403)

        status, _, _ = self.request("GET", f"/?access_token=wrong-{self.browser_token}")
        self.assertEqual(status, 403)
        cookie = self._access_cookie()
        for path in ("/", "/app.js", "/api/workspace/state", "/api/scene", asset_path):
            with self.subTest(path=path):
                status, _, _ = self.request("GET", path, cookie=cookie)
                self.assertEqual(status, 200)
        status, _, state = self.request("GET", "/api/workspace/state", cookie=cookie)
        self.assertEqual(state["browser_capability"], self.browser_token)
        self.assertEqual(parse_qs(urlsplit(state["browser_url"]).query)["access_token"], [self.browser_token])
        status, _, asset = self.request("GET", asset_path, cookie=cookie)
        self.assertEqual(asset, model.read_bytes())

    def test_lan_writes_still_need_capability_and_origin(self) -> None:
        cookie = self._access_cookie()
        session = self.server.scene_store.create_session()
        reference_path = f"/api/sessions/{session['session_id']}/references"
        image = io.BytesIO()
        Image.new("RGB", (4, 4), "#d86643").save(image, format="PNG")
        payload = {"name": "mark.png", "data_url": "data:image/png;base64," + base64.b64encode(image.getvalue()).decode("ascii")}
        status, _, _ = self.request("POST", reference_path, payload, cookie=cookie)
        self.assertEqual(status, 403)
        status, _, _ = self.request("POST", reference_path, payload, cookie=cookie, capability=True, origin="https://evil.example")
        self.assertEqual(status, 403)
        status, _, _ = self.request("POST", reference_path, payload, cookie=cookie, capability=True, host="evil.example")
        self.assertEqual(status, 403)
        status, _, _ = self.request("POST", reference_path, payload, cookie=cookie, capability=True, origin=self.base)
        self.assertEqual(status, 201)

    def test_lan_session_urls_include_public_address_and_access_token(self) -> None:
        cookie = self._access_cookie()
        status, _, created = self.request("POST", "/api/sessions", {}, cookie=cookie)
        self.assertEqual(status, 201)
        self.assertEqual(urlsplit(created["url"]).netloc, urlsplit(self.base).netloc)
        self.assertEqual(parse_qs(urlsplit(created["url"]).query)["access_token"], [self.browser_token])
        status, _, state = self.request("GET", "/api/workspace/state", control=True)
        self.assertEqual(status, 200)
        self.assertIn("browser_url", state)

    def test_existing_local_mcp_host_remains_available_when_public_host_differs(self) -> None:
        port = _free_port()
        public_host = f"lan.test:{port}"
        server = make_server(
            port=port,
            data_dir=self.root / "other-lan-data",
            web_dir=self.root / "web",
            project_dir=self.project,
            external_review=True,
            listen_host="0.0.0.0",
            public_base_url=f"http://{public_host}",
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{port}"

            def get(path: str, host: str, cookie: str = ""):
                headers = {"Host": host, "X-Forwarded-For": "127.0.0.1"}
                if cookie:
                    headers["Cookie"] = cookie
                request = urllib.request.Request(base + path, headers=headers)
                try:
                    response = self.opener.open(request, timeout=10)
                except urllib.error.HTTPError as exc:
                    response = exc
                with response:
                    return response.code, response.headers

            self.assertEqual(get("/api/health", f"127.0.0.1:{port}")[0], 200)
            self.assertEqual(get("/api/health", public_host)[0], 403)
            token = (self.root / "other-lan-data" / "browser_token").read_text(encoding="ascii").strip()
            status, headers = get(f"/?access_token={token}", public_host)
            self.assertEqual(status, 303)
            cookie = headers["Set-Cookie"].split(";", 1)[0]
            self.assertEqual(get("/api/health", public_host, cookie)[0], 200)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_bootstrap_request_log_redacts_access_token(self) -> None:
        with self.assertLogs(level="INFO") as captured:
            self._access_cookie()
            status, _, _ = self.request("GET", f"/?%61ccess_token={self.browser_token}")
            self.assertEqual(status, 303)
        messages = "\n".join(captured.output)
        self.assertNotIn(self.browser_token, messages)
        self.assertIn("?[redacted]", messages)


if __name__ == "__main__":
    unittest.main()

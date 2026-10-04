"""LAN page access keeps workspace data behind an explicit browser link."""

from __future__ import annotations

import base64
from http.cookies import SimpleCookie
import io
import json
import socket
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from PIL import Image


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server as server_module  # noqa: E402
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

    def request_at(self, base: str, method: str, path: str, payload: dict | None = None, *, headers: dict | None = None):
        request_headers = {"Accept": "application/json", **(headers or {})}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(base + path, data=body, headers=request_headers, method=method)
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
        cookies = SimpleCookie()
        cookies.load(set_cookie)
        access = cookies[f"scene_feedback_{self.port}_access"]
        self.assertEqual(access.value, self.browser_token)
        self.assertEqual(access["max-age"], str(server_module.LAN_ACCESS_COOKIE_MAX_AGE))
        self.assertTrue(access["httponly"])
        self.assertEqual(access["samesite"], "Lax")
        self.assertEqual(access["path"], "/")
        self.assertFalse(access["secure"])
        return set_cookie.split(";", 1)[0]

    def test_default_listener_is_loopback(self) -> None:
        other = make_server(port=0, data_dir=self.root / "other-data", web_dir=self.root / "web", project_dir=self.project)
        thread = threading.Thread(target=other.serve_forever, daemon=True)
        thread.start()
        try:
            self.assertEqual(other.server_address[0], "127.0.0.1")
            base = f"http://127.0.0.1:{other.server_port}"
            self.assertEqual(self.request_at(base, "GET", "/api/health")[2]["lan_access"], "local")
            self.assertEqual(self.request_at(base, "GET", "/api/workspace/state")[2]["lan_access"], "local")
        finally:
            other.shutdown()
            other.server_close()
            thread.join(timeout=3)

    def test_invalid_lan_access_mode_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "lan_access"):
            make_server(port=0, data_dir=self.root / "invalid-mode-data", web_dir=self.root / "web",
                        project_dir=self.project, lan_access="unexpected")

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
        self.assertEqual(state["lan_access"], "link")
        self.assertEqual(self.request("GET", "/api/health", cookie=cookie)[2]["lan_access"], "link")
        self.assertEqual(state["browser_capability"], self.browser_token)
        self.assertEqual(parse_qs(urlsplit(state["browser_url"]).query)["access_token"], [self.browser_token])
        status, _, asset = self.request("GET", asset_path, cookie=cookie)
        self.assertEqual(asset, model.read_bytes())

    def test_open_lan_needs_no_cookie_and_keeps_control_boundary(self) -> None:
        port = _free_port()
        base = f"http://127.0.0.1:{port}"
        server = make_server(
            port=port,
            data_dir=self.root / "open-lan-data",
            web_dir=self.root / "web",
            project_dir=self.project,
            external_review=True,
            listen_host="0.0.0.0",
            public_base_url=base,
            lan_access="open",
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for path in ("/", "/app.js", "/api/scene", "/api/health", "/api/workspace/state"):
                with self.subTest(path=path):
                    status, headers, _ = self.request_at(base, "GET", path)
                    self.assertEqual(status, 200)
                    self.assertIsNone(headers.get("Set-Cookie"))

            status, _, health = self.request_at(base, "GET", "/api/health")
            self.assertEqual((status, health["lan_access"]), (200, "open"))
            status, _, state = self.request_at(base, "GET", "/api/workspace/state")
            self.assertEqual((status, state["lan_access"]), (200, "open"))
            self.assertEqual(parse_qs(urlsplit(state["browser_url"]).query), {"session_id": [state["session_id"]]})
            self.assertEqual(parse_qs(urlsplit(server.browser_url(state["session_id"])).query),
                             {"session_id": [state["session_id"]]})

            status, headers, _ = self.request_at(base, "GET", "/?access_token=obsolete&session_id=" + state["session_id"])
            self.assertEqual(status, 303)
            self.assertEqual(headers["Location"], "/?session_id=" + state["session_id"])
            self.assertIsNone(headers.get("Set-Cookie"))
            self.assertEqual(self.request_at(base, "GET", headers["Location"])[0], 200)

            scene = self.request_at(base, "GET", "/api/scene")[2]
            feedback_path = f"/api/sessions/{state['session_id']}/feedback"
            feedback = {"idempotency_key": "open-lan-feedback-0001", "scene_revision": scene["revision"],
                        "note": "Move the marked edge"}
            self.assertEqual(self.request_at(base, "POST", feedback_path, feedback)[0], 403)
            browser_headers = {"X-Workspace-Capability": state["browser_capability"]}
            status, _, submitted = self.request_at(base, "POST", feedback_path, feedback, headers=browser_headers)
            self.assertEqual(status, 201, submitted)

            image = io.BytesIO()
            Image.new("RGB", (4, 4), "#d86643").save(image, format="PNG")
            reference = {"name": "mark.png", "data_url": "data:image/png;base64," + base64.b64encode(image.getvalue()).decode("ascii")}
            reference_path = f"/api/sessions/{state['session_id']}/references"
            self.assertEqual(self.request_at(base, "POST", reference_path, reference)[0], 403)
            self.assertEqual(self.request_at(base, "POST", reference_path, reference, headers=browser_headers)[0], 201)

            self.assertEqual(self.request_at(base, "POST", "/api/workspace/publish", {})[0], 403)
            self.assertEqual(self.request_at(base, "POST", "/api/workspace/publish", {}, headers=browser_headers)[0], 403)
            self.assertEqual(self.request_at(base, "POST", "/mcp", {})[0], 401)
            self.assertEqual(self.request_at(base, "GET", "/api/health", headers={"Host": "evil.test"})[0], 403)
            self.assertEqual(self.request_at(base, "GET", "/api/health", headers={"Origin": "https://evil.test"})[0], 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_authenticated_home_renews_cookie_without_renewing_api_or_assets(self) -> None:
        cookie = self._access_cookie()
        invalid_cookie = f"scene_feedback_{self.port}_access=invalid"
        for path, supplied, expected in (("/", "", 403), ("/", invalid_cookie, 403),
                                         ("/api/workspace/state", "", 403)):
            with self.subTest(path=path, supplied=supplied):
                status, headers, _ = self.request("GET", path, cookie=supplied)
                self.assertEqual(status, expected)
                self.assertIsNone(headers.get("Set-Cookie"))

        status, headers, _ = self.request("GET", "/", cookie=cookie)
        self.assertEqual(status, 200)
        renewed = SimpleCookie()
        renewed.load(headers.get("Set-Cookie", ""))
        access = renewed[f"scene_feedback_{self.port}_access"]
        self.assertEqual(access.value, self.browser_token)
        self.assertEqual(access["max-age"], str(server_module.LAN_ACCESS_COOKIE_MAX_AGE))
        self.assertTrue(access["httponly"])
        self.assertEqual(access["samesite"], "Lax")
        self.assertEqual(access["path"], "/")

        for path in ("/app.js", "/api/workspace/state"):
            with self.subTest(path=path):
                status, headers, _ = self.request("GET", path, cookie=cookie)
                self.assertEqual(status, 200)
                self.assertIsNone(headers.get("Set-Cookie"))

    def test_local_opener_uses_saved_session_then_bootstraps_clean_page(self) -> None:
        session_id = self.server.scene_store.workspace()["session_id"]
        status, headers, body = self.request("GET", f"/open?session_id={session_id}")
        self.assertEqual((status, body), (303, b""))
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")
        self.assertIsNone(headers.get("Set-Cookie"))
        access_url = urlsplit(headers["Location"])
        self.assertEqual(access_url.netloc, urlsplit(self.base).netloc)
        self.assertEqual(access_url.path, "/")
        access_query = parse_qs(access_url.query)
        self.assertEqual(access_query["session_id"], [session_id])
        self.assertEqual(access_query["access_token"], [self.browser_token])

        status, headers, body = self.request("GET", access_url.path + "?" + access_url.query)
        self.assertEqual((status, body), (303, b""))
        self.assertEqual(headers["Location"], f"/?session_id={session_id}")
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, _, page = self.request("GET", headers["Location"], cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn(b"LAN viewer", page)

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

    def test_local_opener_rejects_public_host_remote_peer_and_cross_origin(self) -> None:
        port = _free_port()
        public_host = f"lan.test:{port}"
        server = make_server(
            port=port,
            data_dir=self.root / "opener-data",
            web_dir=self.root / "web",
            project_dir=self.project,
            external_review=True,
            listen_host="0.0.0.0",
            public_base_url=f"http://{public_host}",
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            token = (self.root / "opener-data" / "browser_token").read_text(encoding="ascii").strip()
            local_host = f"127.0.0.1:{port}"

            def get(path: str, *, host: str, origin: str = "", cookie: str = ""):
                headers = {"Host": host}
                if origin:
                    headers["Origin"] = origin
                if cookie:
                    headers["Cookie"] = cookie
                request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)
                try:
                    response = self.opener.open(request, timeout=10)
                except urllib.error.HTTPError as exc:
                    response = exc
                with response:
                    return response.code, response.headers, response.read()

            status, headers, _ = get(f"/?access_token={token}", host=public_host)
            self.assertEqual(status, 303)
            cookie = headers["Set-Cookie"].split(";", 1)[0]

            def assert_denied(status, headers, body):
                self.assertEqual(status, 403)
                self.assertIsNone(headers.get("Location"))
                self.assertIsNone(headers.get("Set-Cookie"))
                self.assertFalse(token in str(headers) + body.decode("utf-8", errors="replace"))

            assert_denied(*get("/open", host=public_host, cookie=cookie))
            assert_denied(*get("/open", host=local_host, origin="https://other.test", cookie=cookie))

            original_get_request = server.get_request

            def remote_get_request():
                connection, (_, peer_port) = original_get_request()
                return connection, ("198.51.100.25", peer_port)

            # Simulate a non-loopback TCP peer even when the test runner has no LAN interface.
            with patch.object(server, "get_request", side_effect=remote_get_request):
                assert_denied(*get("/open", host=local_host, cookie=cookie))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_https_public_url_marks_persistent_cookie_secure(self) -> None:
        port = _free_port()
        server = make_server(
            port=port,
            data_dir=self.root / "https-data",
            web_dir=self.root / "web",
            project_dir=self.project,
            external_review=True,
            listen_host="0.0.0.0",
            public_base_url=f"https://127.0.0.1:{port}",
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            token = (self.root / "https-data" / "browser_token").read_text(encoding="ascii").strip()
            request = urllib.request.Request(f"http://127.0.0.1:{port}/?access_token={token}")
            try:
                response = self.opener.open(request, timeout=10)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                self.assertEqual(response.code, 303)
                parsed = SimpleCookie()
                parsed.load(response.headers["Set-Cookie"])
                access = parsed[f"scene_feedback_{port}_access"]
                self.assertTrue(access["secure"])
                self.assertEqual(access["max-age"], str(server_module.LAN_ACCESS_COOKIE_MAX_AGE))
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

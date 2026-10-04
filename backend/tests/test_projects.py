"""Project routing isolates stores, task ownership, credentials and retries."""

from __future__ import annotations

import http.client
from http.cookies import SimpleCookie
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import APIError  # noqa: E402
from gateway import WorkspaceGateway  # noqa: E402
import server as server_module  # noqa: E402


def _quiet_start(gateway):
    gateway.ensure()
    gateway._started = True


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "assembly"
        self.project.mkdir()
        self.web = self.root / "web"
        self.web.mkdir()
        (self.web / "index.html").write_text("<html>viewer</html>", encoding="utf-8")
        (self.web / "app.js").write_text("// viewer", encoding="utf-8")
        self.data = self.root / "data"
        self.created = []
        self.start_patch = patch.object(WorkspaceGateway, "start", _quiet_start)
        self.start_patch.start()
        self.target_patch = patch.object(WorkspaceGateway, "create_target", self.create_target)
        self.target_patch.start()
        self.start_server()

    def create_target(self, model, *, reasoning_effort=None, title=None, permission_mode="workspace_write"):
        # Patched on the instance by Mock's callable substitution: get the
        # newest gateway from the registry, where project creation installed it.
        context = self.server.project_registry.contexts()[-1]
        gateway = context.gateway
        thread_id = str(uuid.uuid4())
        self.created.append((context.project_id, thread_id, model, title, gateway.thread_config))
        with gateway.store.lock:
            workspace = gateway.store.state["workspace"]
            workspace["created_thread_ids"].append(thread_id)
            workspace["created_thread_specs"][thread_id] = {"model": model, "title": title, "permission_mode": permission_mode}
        gateway.store.workspace_thread(thread_id)
        return {"thread_id": thread_id, "workspace": gateway.state()}

    def start_server(self, *, lan=False, lan_access="link"):
        options = {}
        port = 0
        if lan:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            options = {"listen_host": "0.0.0.0", "public_base_url": f"http://127.0.0.1:{port}"}
        self.server = server_module.make_server(port=port, data_dir=self.data, web_dir=self.web,
                                               project_dir=self.project, external_review=True,
                                               lan_access=lan_access, **options)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.root_context = self.server.project_registry.root

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def tearDown(self):
        self.stop_server()
        self.target_patch.stop()
        self.start_patch.stop()
        self.temporary.cleanup()

    def request(self, method, path, payload=None, *, capability=None, control=None, cookie=None, origin=None):
        headers = {}
        for name, value in (("X-Workspace-Capability", capability), ("X-Scene-Harness-Key", control),
                            ("Cookie", cookie), ("Origin", origin)):
            if value is not None:
                headers[name] = value
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read()
        result = json.loads(data) if response.getheader("Content-Type", "").startswith("application/json") else data
        response_headers = dict(response.getheaders())
        status = response.status
        connection.close()
        return status, result, response_headers

    def create(self, *, request_id=None, name="新场景", path="/api/projects", capability=None):
        payload = {"name": name, "model": "gpt-6-astra", "reasoning_effort": "high",
                   "permission_mode": "workspace_write", "request_id": request_id or uuid.uuid4().hex}
        status, result, _ = self.request("POST", path, payload,
                                      capability=capability or self.root_context.store.browser_token)
        return status, result, payload

    def test_create_preserves_default_store_and_starts_empty_managed_scene(self):
        store = self.root_context.store
        store.state["scene"] = {"revision": 8, "name": "Assembly101", "objects": [{"id": "existing"}]}
        workspace = store.state["workspace"]
        workspace["thread_id"] = str(uuid.uuid4())
        workspace["queue"] = [{"feedback_id": uuid.uuid4().hex, "status": "completed", "target_thread_id": workspace["thread_id"]}]
        store.state["sessions"][workspace["session_id"]]["reference_clip"] = {"clip_id": uuid.uuid4().hex, "views": []}
        store._save()
        before = store.state_path.read_bytes()
        media = store.media_dir / (uuid.uuid4().hex + ".jpg")
        media.write_bytes(b"original reference evidence")

        status, result, payload = self.create()
        self.assertEqual(status, 201, result)
        context = self.server.project_registry.get(result["project"]["project_id"])
        self.assertEqual(context.store.scene()["objects"], [])
        self.assertEqual(context.store.scene()["name"], "新场景")
        self.assertEqual(context.project_dir, self.data / "projects" / context.project_id / "workspace")
        self.assertEqual(context.store.data_dir, self.data / "projects" / context.project_id / "data")
        self.assertNotEqual(context.store.browser_token, store.browser_token)
        self.assertNotEqual(result["workspace"]["session_id"], workspace["session_id"])
        self.assertEqual(result["project"]["request_id"], payload["request_id"])
        self.assertEqual(store.state_path.read_bytes(), before)
        self.assertEqual(media.read_bytes(), b"original reference evidence")
        self.assertFalse(store.state["workspace"]["thread_id"] == result["workspace"]["thread_id"])
        config = context.gateway.thread_config["mcp_servers"]["scene_feedback"]
        self.assertEqual(config["env"]["SCENE_FEEDBACK_PORT"], str(self.server.server_port))
        self.assertEqual(config["env"]["SCENE_FEEDBACK_DATA_DIR"], str(context.store.data_dir))
        self.assertEqual(config["env"]["SCENE_FEEDBACK_PROJECT_DIR"], str(context.project_dir))
        self.assertIn("/p/" + context.project_id + "/", result["workspace"]["browser_url"])

    def test_request_id_survives_restart_and_rejects_changed_settings(self):
        _, first, payload = self.create()
        status, repeat, _ = self.request("POST", "/api/projects", payload, capability=self.root_context.store.browser_token)
        self.assertEqual(status, 201)
        self.assertEqual(repeat["project"], first["project"])
        self.assertEqual(len(self.created), 1)
        changed = dict(payload, name="不同的场景")
        self.assertEqual(self.request("POST", "/api/projects", changed, capability=self.root_context.store.browser_token)[0], 409)
        original_id = self.root_context.project_id
        self.stop_server()
        self.start_server()
        status, restored, _ = self.request("POST", "/api/projects", payload, capability=self.root_context.store.browser_token)
        self.assertEqual(status, 201)
        self.assertEqual(restored["project"], first["project"])
        self.assertEqual(self.root_context.project_id, original_id)
        self.assertEqual(len(self.created), 1)

    def test_child_restart_preserves_owned_permission_and_mcp_config(self):
        class RestoredAdapter:
            def __init__(self, thread_id, on_event, **kwargs):
                self.thread_id = thread_id
                self.kwargs = kwargs
                self.thread_config = kwargs.get("thread_config")

            def close(self):
                pass

        _, first, _ = self.create()
        project_id = first["project"]["project_id"]
        context = self.server.project_registry.get(project_id)
        target_id = context.store.workspace()["thread_id"]
        for owned, mode in ((True, "full_access"), (True, "workspace_write"), (True, "read_only"), (False, "full_access"), (True, None)):
            with self.subTest(owned=owned, mode=mode):
                with context.store.lock:
                    workspace = context.store.state["workspace"]
                    workspace["created_thread_ids"] = [target_id] if owned else []
                    workspace["created_thread_specs"][target_id] = {"permission_mode": mode} if mode else {}
                    context.store._save()
                self.stop_server()
                with patch("shared_thread_adapter.SharedDesktopAdapter", RestoredAdapter):
                    self.start_server()
                context = self.server.project_registry.get(project_id)
                adapter = context.gateway.adapter
                self.assertEqual(adapter.thread_id, target_id)
                self.assertEqual(adapter.kwargs.get("allow_owned_resume", False), owned)
                self.assertEqual(adapter.kwargs.get("permission_mode"), mode if owned else None)
                env = adapter.thread_config["mcp_servers"]["scene_feedback"]["env"]
                self.assertEqual(env["SCENE_FEEDBACK_PROJECT_DIR"], str(context.project_dir))
                self.assertEqual(env["SCENE_FEEDBACK_DATA_DIR"], str(context.store.data_dir))

    def test_prefixes_and_mcp_tokens_select_their_own_resources(self):
        _, first, _ = self.create(name="A")
        _, second, _ = self.create(name="B")
        a = self.server.project_registry.get(first["project"]["project_id"])
        b = self.server.project_registry.get(second["project"]["project_id"])
        filename = uuid.uuid4().hex + ".jpg"
        a.store.media_dir.joinpath(filename).write_bytes(b"camera A")
        b.store.media_dir.joinpath(filename).write_bytes(b"camera B")
        for context, contents in ((a, b"camera A"), (b, b"camera B")):
            prefix = "/p/" + context.project_id
            self.assertEqual(self.request("GET", prefix + "/media/" + filename)[:2], (200, contents))
            status, health, _ = self.request("GET", "/api/health", control=context.store.control_token)
            self.assertEqual((status, health["data_dir"]), (200, str(context.store.data_dir)))
            status, catalog, _ = self.request("GET", prefix + "/api/projects")
            self.assertEqual((status, catalog["current_project_id"]), (200, context.project_id))
        self.assertEqual(self.request("GET", "/api/health")[1]["project_id"], self.root_context.project_id)
        self.assertEqual(self.request("GET", "/api/health", control="invalid")[0], 403)
        self.assertEqual(self.request("GET", "/p/" + a.project_id + "/api/scene", control=b.store.control_token)[0], 403)
        b_session = b.store.workspace()["session_id"]
        self.assertEqual(self.request("GET", "/p/" + a.project_id + "/api/sessions/" + b_session)[0], 404)
        self.assertEqual(self.request("GET", "/p/" + a.project_id + "/app.js")[:2], (200, b"// viewer"))

    def test_capabilities_and_task_ownership_do_not_cross_projects(self):
        _, first, _ = self.create()
        context = self.server.project_registry.get(first["project"]["project_id"])
        prefix = "/p/" + context.project_id
        status, _, _ = self.create(path=prefix + "/api/projects", capability=self.root_context.store.browser_token)
        self.assertEqual(status, 403)
        with self.assertRaisesRegex(APIError, "another reconstruction project"):
            self.root_context.gateway.target_validator(context.store.workspace()["thread_id"])
        self.assertIs(self.root_context.gateway.target_binding_lock, context.gateway.target_binding_lock)
        self.assertEqual(self.request("POST", prefix + "/api/projects", {"name": "escape", "model": "gpt-6-astra",
                         "project_dir": "/tmp/host", "request_id": uuid.uuid4().hex}, capability=context.store.browser_token)[0], 400)

    def test_failed_task_creation_is_visible_and_is_never_repeated(self):
        created_id = str(uuid.uuid4())
        calls = []

        def fail(*args, **kwargs):
            calls.append(True)
            raise APIError(503, "task creation result is uncertain", detail={"thread_id": created_id})

        with patch.object(WorkspaceGateway, "create_target", fail):
            status, failed, payload = self.create()
            self.assertEqual(status, 503)
            self.assertEqual(failed["project"]["created_thread_id"], created_id)
            project_id = failed["project"]["project_id"]
            self.assertEqual(self.request("POST", "/api/projects", payload, capability=self.root_context.store.browser_token)[0], 503)
            self.stop_server()
            self.start_server()
            self.assertEqual(self.request("POST", "/api/projects", payload, capability=self.root_context.store.browser_token)[0], 503)
        self.assertEqual(len(calls), 1)
        _, catalog, _ = self.request("GET", "/api/projects")
        self.assertEqual(next(item for item in catalog["projects"] if item["project_id"] == project_id)["creation_status"], "uncertain")
        context = self.server.project_registry.get(project_id)
        context.store.workspace_thread(created_id)
        _, catalog, _ = self.request("GET", "/api/projects")
        self.assertEqual(next(item for item in catalog["projects"] if item["project_id"] == project_id)["creation_status"], "ready")
        self.assertEqual(self.request("POST", "/api/projects", payload, capability=self.root_context.store.browser_token)[0], 201)

    def test_factory_failure_keeps_catalog_and_idempotent_error(self):
        with patch.object(server_module, "SceneStore", side_effect=RuntimeError("cannot initialize child store")):
            status, failed, payload = self.create()
        self.assertEqual(status, 503)
        project_id = failed["project"]["project_id"]
        status, repeat, _ = self.request("POST", "/api/projects", payload, capability=self.root_context.store.browser_token)
        self.assertEqual((status, repeat["project"]["project_id"]), (503, project_id))
        catalog = self.request("GET", "/api/projects")[1]
        self.assertIn(project_id, [item["project_id"] for item in catalog["projects"]])
        self.assertEqual(len(self.created), 0)

    def test_all_child_locks_are_released_even_when_one_gateway_close_fails(self):
        _, first, _ = self.create()
        _, second, _ = self.create()
        contexts = self.server.project_registry.contexts()
        for context in contexts[1:]:
            with self.assertRaisesRegex(RuntimeError, "already using data directory"):
                server_module._DataDirLock(context.store.data_dir)
        with patch.object(contexts[-1].gateway, "close", side_effect=RuntimeError("close failure")):
            with self.assertRaisesRegex(RuntimeError, "close failure"):
                self.server.server_close()
        for context in contexts:
            lock = server_module._DataDirLock(context.store.data_dir)
            lock.close()

    def test_child_access_link_bootstraps_root_hub_cookie(self):
        _, first, _ = self.create()
        context = self.server.project_registry.get(first["project"]["project_id"])
        self.stop_server()
        self.start_server(lan=True)
        context = self.server.project_registry.get(context.project_id)
        prefix = "/p/" + context.project_id
        self.assertEqual(self.request("GET", prefix + "/")[0], 403)
        self.assertEqual(self.request("GET", prefix + "/api/workspace/state")[0], 403)
        status, _, headers = self.request("GET", prefix + "/?access_token=" + context.store.browser_token)
        self.assertEqual(status, 303)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.assertEqual(cookie.split("=", 1)[1], self.root_context.store.browser_token)
        parsed = SimpleCookie()
        parsed.load(headers["Set-Cookie"])
        self.assertEqual(parsed[f"scene_feedback_{self.server.server_port}_access"]["max-age"],
                         str(server_module.LAN_ACCESS_COOKIE_MAX_AGE))
        self.assertEqual(headers["Location"], prefix + "/")
        status, _, page_headers = self.request("GET", prefix + "/", cookie=cookie)
        self.assertEqual(status, 200)
        renewed = SimpleCookie()
        renewed.load(page_headers.get("Set-Cookie", ""))
        self.assertEqual(renewed[f"scene_feedback_{self.server.server_port}_access"].value,
                         self.root_context.store.browser_token)
        self.assertEqual(renewed[f"scene_feedback_{self.server.server_port}_access"]["max-age"],
                         str(server_module.LAN_ACCESS_COOKIE_MAX_AGE))
        status, _, api_headers = self.request("GET", prefix + "/api/workspace/state", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertNotIn("Set-Cookie", api_headers)
        self.assertEqual(self.request("GET", "/api/projects", cookie=cookie)[0], 200)
        self.assertEqual(self.request("POST", prefix + "/api/projects", {"request_id": uuid.uuid4().hex},
                                     cookie=cookie, capability=self.root_context.store.browser_token)[0], 403)
        self.assertEqual(self.request("GET", prefix + "/api/projects", cookie=cookie, origin="https://other.test")[0], 403)

    def test_child_local_opener_preserves_project_and_current_session(self):
        _, created, _ = self.create(name="Water Tanker")
        project_id = created["project"]["project_id"]
        self.stop_server()
        self.start_server(lan=True)
        context = self.server.project_registry.get(project_id)
        session_id = context.store.workspace()["session_id"]
        prefix = "/p/" + project_id

        status, _, headers = self.request("GET", prefix + f"/open?session_id={session_id}")
        self.assertEqual(status, 303)
        self.assertNotIn("Set-Cookie", headers)
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")
        access_url = urlsplit(headers["Location"])
        self.assertEqual(access_url.path, prefix + "/")
        query = parse_qs(access_url.query)
        self.assertEqual(query["session_id"], [session_id])
        self.assertTrue(self.server.project_registry.accepts_browser_token(query["access_token"][0]))

        status, _, bootstrap = self.request("GET", access_url.path + "?" + access_url.query)
        self.assertEqual(status, 303)
        self.assertEqual(bootstrap["Location"], prefix + f"/?session_id={session_id}")
        cookie = bootstrap["Set-Cookie"].split(";", 1)[0]
        status, page, _ = self.request("GET", bootstrap["Location"], cookie=cookie)
        self.assertEqual((status, page), (200, b"<html>viewer</html>"))
        self.assertEqual(self.request("GET", prefix + "/api/workspace/state", cookie=cookie)[1]["session_id"], session_id)

    def test_open_lan_root_and_child_require_no_cookie_but_keep_project_capabilities(self):
        _, created, _ = self.create(name="Water Tanker")
        project_id = created["project"]["project_id"]
        self.stop_server()
        self.start_server(lan=True, lan_access="open")
        child = self.server.project_registry.get(project_id)
        self.assertNotEqual(self.root_context.store.browser_token, child.store.browser_token)
        prefix = "/p/" + project_id

        for route, context in (("", self.root_context), (prefix, child)):
            with self.subTest(route=route):
                status, _, page_headers = self.request("GET", route + "/")
                self.assertEqual(status, 200)
                self.assertNotIn("Set-Cookie", page_headers)
                status, health, _ = self.request("GET", route + "/api/health")
                self.assertEqual((status, health["lan_access"]), (200, "open"))
                status, state, _ = self.request("GET", route + "/api/workspace/state")
                self.assertEqual((status, state["lan_access"]), (200, "open"))
                self.assertEqual(state["project_id"], context.project_id)
                self.assertEqual(state["browser_capability"], context.store.browser_token)
                generated = urlsplit(state["browser_url"])
                self.assertEqual(generated.path, route + "/")
                self.assertEqual(parse_qs(generated.query), {"session_id": [state["session_id"]]})
                self.assertEqual(parse_qs(urlsplit(self.server.browser_url(state["session_id"], context.project_id)).query),
                                 {"session_id": [state["session_id"]]})

        child_session = child.store.workspace()["session_id"]
        status, _, headers = self.request("GET", prefix + f"/?access_token=obsolete&session_id={child_session}")
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], prefix + f"/?session_id={child_session}")
        self.assertNotIn("Set-Cookie", headers)
        self.assertEqual(self.request("GET", headers["Location"])[0], 200)

        self.assertEqual(self.request("POST", prefix + "/api/projects", {"request_id": uuid.uuid4().hex},
                                      capability=self.root_context.store.browser_token)[0], 403)
        self.assertEqual(self.request("POST", prefix + "/api/workspace/publish", {},
                                      capability=child.store.browser_token)[0], 403)
        self.assertEqual(self.request("POST", prefix + "/mcp", {})[0], 401)
        self.assertEqual(self.request("GET", prefix + "/api/health", origin="https://other.test")[0], 403)

    def test_closed_registry_rejects_new_creations(self):
        self.server.project_registry.close()
        self.assertEqual(self.request("GET", "/api/projects")[0], 503)
        self.assertEqual(self.request("POST", "/api/projects", {"request_id": uuid.uuid4().hex},
                                     capability=self.root_context.store.browser_token)[0], 503)

    def test_locked_child_does_not_block_default_or_other_project_restore(self):
        _, first, _ = self.create(name="locked")
        _, second, _ = self.create(name="working")
        locked_id = first["project"]["project_id"]
        other_id = second["project"]["project_id"]
        locked = self.server.project_registry.get(locked_id)
        owned_thread = first["workspace"]["thread_id"]
        self.stop_server()
        held_lock = server_module._DataDirLock(locked.store.data_dir)
        try:
            with self.assertLogs(level="ERROR"):
                self.start_server()
            self.assertEqual(self.request("GET", "/api/health")[0], 200)
            self.assertEqual(self.request("GET", "/p/" + other_id + "/api/health")[0], 200)
            catalog = self.request("GET", "/api/projects")[1]
            unavailable = next(item for item in catalog["projects"] if item["project_id"] == locked_id)
            self.assertEqual(unavailable["creation_status"], "unavailable")
            self.assertEqual(unavailable["thread_id"], owned_thread)
            self.assertEqual(self.request("GET", "/p/" + locked_id + "/api/workspace/state")[0], 503)
            with self.assertRaisesRegex(APIError, "another reconstruction project"):
                self.root_context.gateway.target_validator(owned_thread)
        finally:
            held_lock.close()

    def test_missing_established_state_is_not_recreated_on_later_restarts(self):
        _, created, _ = self.create()
        project_id = created["project"]["project_id"]
        context = self.server.project_registry.get(project_id)
        self.stop_server()
        context.store.state_path.unlink()
        for _ in range(2):
            with self.assertLogs(level="ERROR"):
                self.start_server()
            self.assertFalse(context.store.state_path.exists())
            catalog = self.request("GET", "/api/projects")[1]
            unavailable = next(item for item in catalog["projects"] if item["project_id"] == project_id)
            self.assertEqual(unavailable["creation_status"], "unavailable")
            self.assertEqual(unavailable["created_thread_id"], created["workspace"]["thread_id"])
            if _ == 0:
                self.stop_server()

    def test_server_close_waits_for_inflight_publish_before_releasing_locks(self):
        entered = threading.Event()
        release = threading.Event()
        closed = threading.Event()
        results = []
        source = self.project / "preview.glb"
        source.write_bytes(b"stand-in preview")

        def publish(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("publish was not released")
            return self.root_context.store.scene()

        def close():
            self.server.shutdown()
            self.server.server_close()
            closed.set()

        with patch.object(self.root_context.store, "set_scene_preview", publish):
            writer = threading.Thread(target=lambda: results.append(self.request(
                "POST", "/api/workspace/publish", {"local_path": str(source), "expected_revision": 1},
                control=self.root_context.store.control_token
            )))
            writer.start()
            self.assertTrue(entered.wait(2))
            closer = threading.Thread(target=close)
            closer.start()
            try:
                self.assertFalse(closed.wait(0.8))
                with self.assertRaisesRegex(RuntimeError, "already using data directory"):
                    server_module._DataDirLock(self.data)
            finally:
                release.set()
                writer.join(timeout=3)
                closer.join(timeout=3)
        self.assertTrue(closed.is_set())
        self.assertEqual(results[0][0], 200)
        lock = server_module._DataDirLock(self.data)
        lock.close()


if __name__ == "__main__":
    unittest.main()

"""Creating a Desktop task preserves the scene and makes routing recoverable."""

from __future__ import annotations

import sys
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import APIError, SceneStore  # noqa: E402
from gateway import WorkspaceGateway  # noqa: E402
from server import make_server  # noqa: E402
from shared_thread_bridge import OwnedEmptyThreadMissing  # noqa: E402
from shared_thread_bridge import SharedThreadRPCRejected  # noqa: E402


OLD = "01a0d906-146e-7762-a1f9-49baeda8e270"
NEW = "01a0de73-9763-7432-8ca4-5892c0904234"
MISSING = "21a0de73-9763-7432-8ca4-5892c0904234"


class FakeBridge:
    def __init__(self):
        self.closed = False
        self.created = []

    def list_models(self):
        return [
            {"model": "gpt-6-astra", "displayName": "Astra", "inputModalities": ["text", "image"],
             "defaultReasoningEffort": "high", "supportedReasoningEfforts": [{"reasoningEffort": "high"}, {"reasoningEffort": "ultra"}], "isDefault": True},
            {"model": "text-only", "inputModalities": ["text"], "supportedReasoningEfforts": None},
            {"model": "hidden", "hidden": True, "supportedReasoningEfforts": None},
        ]

    def create_thread(self, model, cwd, *, reasoning_effort=None, title=None, permission_mode="workspace_write"):
        self.created.append((model, cwd, reasoning_effort, title, permission_mode))
        return NEW

    def close(self):
        self.closed = True

    def read_thread(self):
        return {"id": NEW, "status": {"type": "idle"}}

    def read_loaded_thread(self, _thread_id):
        raise SharedThreadRPCRejected("thread/read", {"message": "no rollout found"})


class FakeAdapter:
    def __init__(self, thread_id, on_event=None, *, allow_owned_resume=False, initial_bridge=None):
        self.thread_id = thread_id
        self.on_event = on_event
        self.allow_owned_resume = allow_owned_resume
        self.initial_bridge = initial_bridge
        self.closed = False
        self.fail_start = False

    def start(self):
        if self.fail_start:
            raise RuntimeError("temporary daemon failure")
        return self.thread_id

    def inspect_thread_status(self):
        return "idle"

    def status(self):
        return {"connected": True, "turn_state": "idle"}

    def close(self):
        self.closed = True
        if self.initial_bridge is not None:
            self.initial_bridge.close()


class CreateTargetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.store = SceneStore(self.root / "data")
        self.original = FakeAdapter(OLD)
        self.gateway = WorkspaceGateway(self.store, self.project, adapter=self.original, external_review=True)
        self.gateway.ensure()
        self.store.workspace_thread(OLD)
        self.bridge = FakeBridge()

    def tearDown(self):
        self.gateway.close()
        self.temp.cleanup()

    def test_new_task_binds_future_feedback_and_preserves_older_packet(self):
        session = self.gateway.state()["session_id"]
        old = self.gateway.submit(session, {"idempotency_key": "older-one", "scene_revision": 1, "note": "older"})
        scene = self.store.scene()
        with patch("gateway.SharedThreadBridge.connect_to_desktop", return_value=self.bridge), patch("gateway.SharedDesktopAdapter", FakeAdapter), patch.object(self.gateway, "wake"):
            created = self.gateway.create_target("gpt-6-astra", reasoning_effort="ultra", title="New Astra", permission_mode="full_access")
        self.assertEqual(created["thread_id"], NEW)
        self.assertEqual(created["workspace"]["thread_id"], NEW)
        self.assertEqual(created["workspace"]["created_thread_ids"], [NEW])
        self.assertEqual(created["workspace"]["queue"][0]["target_thread_id"], OLD)
        self.assertEqual(self.store.scene(), scene)
        self.assertTrue(self.gateway.adapter.allow_owned_resume)
        self.assertTrue(self.original.closed)
        self.assertEqual(self.bridge.created, [("gpt-6-astra", self.project, "ultra", "New Astra", "full_access")])
        self.assertEqual(created["workspace"]["created_thread_specs"][NEW]["permission_mode"], "full_access")
        self.assertFalse(self.bridge.closed)
        self.assertEqual(self.gateway.submit(session, {"idempotency_key": "newer-one", "scene_revision": 1, "note": "later"})["delivery"]["target_thread_id"], NEW)
        self.assertEqual(old["delivery"]["target_thread_id"], OLD)

    def test_invalid_selection_and_active_delivery_do_not_create_task(self):
        with patch("gateway.SharedThreadBridge.connect_to_desktop", return_value=self.bridge), patch("gateway.SharedDesktopAdapter", FakeAdapter):
            with self.assertRaisesRegex(APIError, "image input"):
                self.gateway.create_target("text-only")
            with self.assertRaisesRegex(APIError, "not supported"):
                self.gateway.create_target("gpt-6-astra", reasoning_effort="none")
            with self.assertRaisesRegex(APIError, "permission_mode"):
                self.gateway.create_target("gpt-6-astra", permission_mode="unrestricted")
            with self.assertRaisesRegex(APIError, "permission_mode"):
                self.gateway.create_target("gpt-6-astra", permission_mode=None)
            with self.store.lock:
                self.store.state["workspace"]["active_feedback_id"] = "pending"
            with self.assertRaisesRegex(APIError, "active feedback"):
                self.gateway.create_target("gpt-6-astra")
        self.assertEqual(self.bridge.created, [])
        self.assertEqual(self.gateway.state()["thread_id"], OLD)

    def test_task_id_is_persisted_and_returned_if_binding_fails(self):
        class BrokenAdapter(FakeAdapter):
            def start(self):
                raise RuntimeError("daemon disconnected")

        with patch("gateway.SharedThreadBridge.connect_to_desktop", return_value=self.bridge), patch("gateway.SharedDesktopAdapter", BrokenAdapter):
            with self.assertRaises(APIError) as raised:
                self.gateway.create_target("gpt-6-astra")
        self.assertEqual(raised.exception.detail, {"thread_id": NEW})
        self.assertEqual(self.gateway.state()["created_thread_ids"], [NEW])
        self.assertEqual(self.gateway.state()["created_thread_specs"][NEW]["permission_mode"], "workspace_write")
        self.assertEqual(self.gateway.state()["thread_id"], OLD)

    def test_model_catalog_only_offers_visible_image_models(self):
        with patch("gateway.SharedThreadBridge.connect_to_desktop", return_value=self.bridge):
            catalog = self.gateway.list_models()
        self.assertEqual(catalog["default_model"], "gpt-6-astra")
        self.assertIs(catalog["permission_modes_supported"], True)
        self.assertEqual([item["model"] for item in catalog["models"]], ["gpt-6-astra"])
        self.assertEqual(catalog["models"][0]["supported_reasoning_efforts"], ["high", "ultra"])

    def test_restart_recreates_only_a_owned_task_with_no_delivery_attempt(self):
        with self.store.lock:
            workspace = self.store.state["workspace"]
            workspace["created_thread_ids"] = [OLD]
            workspace["created_thread_specs"] = {OLD: {"model": "gpt-6-astra", "reasoning_effort": "high", "title": "Astra feedback", "permission_mode": "full_access"}}
            workspace["queue"] = [{"feedback_id": "feedback-1", "target_thread_id": OLD, "status": "queued", "turn_id": None}]
            self.store._save()

        class MissingAdapter(FakeAdapter):
            def start(self):
                raise OwnedEmptyThreadMissing("no rollout found")

        self.gateway.adapter = MissingAdapter(OLD, allow_owned_resume=True)
        self.gateway._started = True
        with patch("gateway.SharedThreadBridge.connect_to_desktop", return_value=self.bridge), patch("gateway.SharedDesktopAdapter", FakeAdapter), patch.object(self.gateway, "wake"):
            self.gateway._supervise_once()
        state = self.gateway.state()
        self.assertEqual(state["thread_id"], NEW)
        self.assertEqual(state["queue"][0]["target_thread_id"], NEW)
        self.assertEqual(state["created_thread_specs"][NEW]["model"], "gpt-6-astra")
        self.assertEqual(state["created_thread_specs"][NEW]["permission_mode"], "full_access")
        self.assertEqual(self.bridge.created[0][-1], "full_access")
        self.assertTrue(self.gateway.adapter.allow_owned_resume)

    def test_uncertain_delivery_disables_empty_task_recreation(self):
        with self.store.lock:
            workspace = self.store.state["workspace"]
            workspace["created_thread_ids"] = [OLD]
            workspace["created_thread_specs"] = {OLD: {"model": "gpt-6-astra", "reasoning_effort": "high", "title": "Astra feedback"}}
            workspace["queue"] = [{"feedback_id": "feedback-1", "target_thread_id": OLD, "status": "delivery_uncertain", "turn_id": None}]
            self.store._save()

        class MissingAdapter(FakeAdapter):
            def start(self):
                raise OwnedEmptyThreadMissing("no rollout found")

        self.gateway.adapter = MissingAdapter(OLD, allow_owned_resume=True)
        self.gateway._started = True
        with patch("gateway.SharedThreadBridge.connect_to_desktop", return_value=self.bridge) as connect:
            self.gateway._supervise_once()
        connect.assert_not_called()
        self.assertEqual(self.gateway.state()["thread_id"], OLD)
        self.assertEqual(self.gateway.state()["queue"][0]["target_thread_id"], OLD)

    def test_switching_back_to_unloaded_zero_turn_owned_task_recreates_it(self):
        with self.store.lock:
            workspace = self.store.state["workspace"]
            workspace["created_thread_ids"] = [MISSING]
            workspace["created_thread_specs"] = {MISSING: {"model": "gpt-6-astra", "reasoning_effort": "high", "title": "Old Astra"}}
            workspace["queue"] = [{"feedback_id": "feedback-1", "target_thread_id": MISSING, "status": "queued", "turn_id": None}]
            self.store._save()
        self.bridge.socket_path = Path("/tmp/private-desktop-a")
        discovered = [(self.bridge.socket_path, {"id": OLD, "cwd": str(self.project), "status": {"type": "idle"}})]
        with patch("gateway.SharedThreadBridge.discover_loaded_threads", return_value=discovered), patch("gateway.SharedThreadBridge.connect_to_desktop", return_value=self.bridge), patch("gateway.SharedDesktopAdapter", FakeAdapter), patch.object(self.gateway, "wake"):
            targets = self.gateway.list_targets()["targets"]
            self.assertEqual(next(item for item in targets if item["thread_id"] == MISSING)["status"], "recoverable")
            state = self.gateway.switch_target(MISSING)
        self.assertEqual(state["thread_id"], NEW)
        self.assertEqual(state["queue"][0]["target_thread_id"], NEW)
        self.assertNotIn(MISSING, state["created_thread_ids"])
        self.assertIn(NEW, state["created_thread_ids"])
        self.assertEqual(self.bridge.created[0][-1], "read_only")
        self.assertEqual(state["created_thread_specs"][NEW]["permission_mode"], "read_only")

    def test_http_create_requires_browser_capability(self):
        self.gateway.close()
        with patch("shared_thread_adapter.SharedDesktopAdapter", FakeAdapter), patch("gateway.SharedDesktopAdapter", FakeAdapter), patch("gateway.SharedThreadBridge.connect_to_desktop", return_value=self.bridge):
            server = make_server(port=0, data_dir=self.root / "data", project_dir=self.project, external_review=True, shared_thread_id=OLD)
            serving = threading.Thread(target=server.serve_forever, daemon=True)
            serving.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
                connection.request("GET", "/api/workspace/models")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                catalog = json.loads(response.read())
                self.assertEqual(catalog["default_model"], "gpt-6-astra")
                self.assertIs(catalog["permission_modes_supported"], True)
                body = json.dumps({"model": "gpt-6-astra", "reasoning_effort": "high", "permission_mode": "read_only"})
                connection.request("POST", "/api/workspace/targets", body=body, headers={"Content-Type": "application/json"})
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                self.assertEqual(self.bridge.created, [])
                connection.request("POST", "/api/workspace/targets", body=body, headers={"Content-Type": "application/json", "X-Workspace-Capability": server.scene_store.browser_token})
                response = connection.getresponse()
                self.assertEqual(response.status, 201)
                self.assertEqual(json.loads(response.read())["thread_id"], NEW)
                self.assertEqual(self.bridge.created[0][-1], "read_only")
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                serving.join(timeout=2)

    def test_service_restart_marks_persisted_created_task_as_owned(self):
        with self.store.lock:
            workspace = self.store.state["workspace"]
            workspace["created_thread_ids"] = [OLD]
            workspace["created_thread_specs"] = {OLD: {"model": "gpt-6-astra", "reasoning_effort": "high", "title": "Astra feedback"}}
            self.store._save()
        self.gateway.close()
        with patch("shared_thread_adapter.SharedDesktopAdapter", FakeAdapter):
            server = make_server(port=0, data_dir=self.root / "data", project_dir=self.project, external_review=True, shared_thread_id=OLD)
        try:
            self.assertTrue(server.workspace_gateway.adapter.allow_owned_resume)
        finally:
            server.workspace_gateway.close()
            server.server_close()

    def test_http_creation_error_returns_created_task_id_for_manual_recovery(self):
        class FailNewAdapter(FakeAdapter):
            def start(self):
                if self.thread_id == NEW:
                    raise RuntimeError("daemon disconnected")
                return super().start()

        self.gateway.close()
        with patch("shared_thread_adapter.SharedDesktopAdapter", FailNewAdapter), patch("gateway.SharedDesktopAdapter", FailNewAdapter), patch("gateway.SharedThreadBridge.connect_to_desktop", return_value=self.bridge):
            server = make_server(port=0, data_dir=self.root / "data", project_dir=self.project, external_review=True, shared_thread_id=OLD)
            serving = threading.Thread(target=server.serve_forever, daemon=True)
            serving.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
                connection.request("POST", "/api/workspace/targets", body=json.dumps({"model": "gpt-6-astra"}), headers={"Content-Type": "application/json", "X-Workspace-Capability": server.scene_store.browser_token})
                response = connection.getresponse()
                body = json.loads(response.read())
                self.assertEqual(response.status, 503)
                self.assertEqual(body["thread_id"], NEW)
                self.assertEqual(server.workspace_gateway.state()["thread_id"], OLD)
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                serving.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

"""A browser can rebind one persistent scene to another Desktop task safely."""

from __future__ import annotations

import sys
import tempfile
import http.client
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import APIError, SceneStore  # noqa: E402
from gateway import WorkspaceGateway  # noqa: E402
from server import make_server  # noqa: E402


OLD = "01a0d906-146e-7762-a1f9-49baeda8e270"
NEW = "01a0de73-9763-7432-8ca4-5892c0904234"


class TargetAdapter:
    def __init__(self, thread_id: str, on_event=None):
        self.thread_id = thread_id
        self.on_event = on_event
        self.connected = False
        self.sent: list[str] = []
        self.lookups: list[str] = []
        self.runtime = "idle"
        self.closed = False

    def start(self) -> str:
        self.connected = True
        return self.thread_id

    def status(self) -> dict:
        return {"connected": self.connected, "thread_id": self.thread_id, "turn_state": self.runtime}

    def inspect_thread_status(self) -> str:
        return self.runtime

    def start_turn(self, _text: str, _images: list[str], message_id: str) -> dict:
        self.sent.append(message_id)
        self.runtime = "active"
        return {"thread_id": self.thread_id, "turn_id": f"turn-{message_id}", "status": "inProgress"}

    def lookup_feedback(self, feedback_id: str) -> list[dict]:
        self.lookups.append(feedback_id)
        return []

    def release_uncertain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True
        self.connected = False


class TargetSwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project" / "segment"
        self.project.mkdir(parents=True)
        self.store = SceneStore(self.root / "data")
        self.old_adapter = TargetAdapter(OLD)
        self.gateway = WorkspaceGateway(self.store, self.project, adapter=self.old_adapter, external_review=True)
        self.old_adapter.on_event = self.gateway.scoped_adapter_callback()
        self.created: list[TargetAdapter] = []

    def tearDown(self) -> None:
        self.gateway.close()
        self.temporary.cleanup()

    def catalog(self, *, target_cwd: str | None = None, target_status: str = "idle", same_socket: bool = True) -> list[tuple[Path, dict]]:
        old_socket = Path("/tmp/private-desktop-a")
        new_socket = old_socket if same_socket else Path("/tmp/private-desktop-b")
        target = {"id": NEW, "cwd": str(self.project) if target_cwd is None else target_cwd, "status": {"type": target_status}, "name": "New Astra", "model": "gpt-6-astra", "reasoningEffort": "ultra"}
        old = {"id": OLD, "cwd": str(self.root), "status": {"type": "idle"}, "name": "Previous Astra", "model": "gpt-6-sol"}
        return [(old_socket, old), (new_socket, target)]

    def replacement(self, thread_id: str, on_event) -> TargetAdapter:
        adapter = TargetAdapter(thread_id, on_event)
        self.created.append(adapter)
        return adapter

    def test_switch_keeps_older_queue_on_original_task_and_new_feedback_on_new_task(self) -> None:
        with patch("gateway.SharedThreadBridge.discover_loaded_threads", return_value=self.catalog()), patch("gateway.SharedDesktopAdapter", side_effect=self.replacement), patch.object(self.gateway, "wake"):
            self.gateway.start()
            session_id = self.gateway.state()["session_id"]
            old_packet = self.gateway.submit(session_id, {"idempotency_key": "before-switch", "scene_revision": 1, "note": "old"})
            self.assertEqual(old_packet["delivery"]["target_thread_id"], OLD)
            state = self.gateway.switch_target(NEW)
            self.assertEqual(state["thread_id"], NEW)
            self.assertEqual(state["queue"][0]["target_thread_id"], OLD)
            new_packet = self.gateway.submit(session_id, {"idempotency_key": "after-switch", "scene_revision": 1, "note": "new"})
            self.assertEqual(new_packet["delivery"]["target_thread_id"], NEW)
            self.gateway._dispatch()
            self.assertEqual(self.created[0].sent, [new_packet["feedback_id"]])
            self.assertEqual(self.gateway.state()["queue"][0]["status"], "queued")
            self.assertEqual(self.gateway.state()["queue"][1]["status"], "running")

            self.created[0].runtime = "idle"
            self.gateway.on_adapter_event({"method": "turn/completed", "params": {"turn": {"id": f"turn-{new_packet['feedback_id']}", "status": "completed"}}})
            self.gateway.switch_target(OLD)
            self.gateway._dispatch()
            self.assertEqual(self.created[1].sent, [old_packet["feedback_id"]])

    def test_list_exposes_loaded_compatible_tasks_and_rejects_unsafe_targets(self) -> None:
        self.gateway.start()
        good = self.catalog()
        outside = (Path("/tmp/private-desktop-a"), {"id": "11a0de73-9763-7432-8ca4-5892c0904234", "cwd": "/outside", "status": {"type": "idle"}, "name": "Other project"})
        missing_cwd = (Path("/tmp/private-desktop-a"), {"id": "21a0de73-9763-7432-8ca4-5892c0904234", "status": {"type": "idle"}, "name": "Unknown cwd"})
        with patch("gateway.SharedThreadBridge.discover_loaded_threads", return_value=[*good, outside, missing_cwd]):
            result = self.gateway.list_targets()
        self.assertEqual(result["thread_id"], OLD)
        self.assertEqual({entry["thread_id"] for entry in result["targets"]}, {OLD, NEW})
        self.assertEqual(next(entry for entry in result["targets"] if entry["thread_id"] == NEW)["reasoning_effort"], "ultra")

        for catalog in (self.catalog(target_cwd="/outside"), self.catalog(target_status="active"), self.catalog(same_socket=False)):
            with patch("gateway.SharedThreadBridge.discover_loaded_threads", return_value=catalog):
                with self.assertRaises(APIError):
                    self.gateway.switch_target(NEW)
        ambiguous = [self.catalog()[1], (Path("/tmp/private-desktop-b"), {"id": "11a0de73-9763-7432-8ca4-5892c0904234", "cwd": str(self.project), "status": {"type": "idle"}})]
        with patch("gateway.SharedThreadBridge.discover_loaded_threads", return_value=ambiguous):
            with self.assertRaisesRegex(APIError, "single Codex Desktop daemon"):
                self.gateway.switch_target(NEW)
        self.assertEqual(self.gateway.state()["thread_id"], OLD)

    def test_unloaded_old_task_can_be_replaced_but_active_delivery_cannot(self) -> None:
        with patch("gateway.SharedThreadBridge.discover_loaded_threads", return_value=[self.catalog()[1]]), patch("gateway.SharedDesktopAdapter", side_effect=self.replacement), patch.object(self.gateway, "wake"):
            self.gateway.start()
            packet = self.gateway.submit(self.gateway.state()["session_id"], {"idempotency_key": "pending-old", "scene_revision": 1, "note": "test"})
            with self.store.lock:
                item = self.store.state["workspace"]["queue"][0]
                item["status"] = "delivery_uncertain"
                self.store.state["workspace"]["active_feedback_id"] = packet["feedback_id"]
                self.store._save()
            with self.assertRaisesRegex(APIError, "active feedback"):
                self.gateway.switch_target(NEW)
            self.assertEqual(self.gateway.state()["thread_id"], OLD)
            with self.store.lock:
                item["quarantined_at"] = "2026-01-01T00:00:00+00:00"
                self.store.state["workspace"]["active_feedback_id"] = None
                self.store._save()
            self.gateway.switch_target(NEW)
            self.assertEqual(self.gateway.state()["queue"][0]["target_thread_id"], OLD)
            self.gateway._supervise_once()
            self.assertEqual(self.created[-1].lookups, [])
            with self.assertRaisesRegex(APIError, "switch back"):
                self.gateway.confirm_queue(packet["feedback_id"], {"retry_uncertain": True})

    def test_failed_persistence_rolls_back_adapter_and_target(self) -> None:
        with patch("gateway.SharedThreadBridge.discover_loaded_threads", return_value=self.catalog()), patch("gateway.SharedDesktopAdapter", side_effect=self.replacement), patch.object(self.gateway, "wake"):
            self.gateway.start()
            original_save = self.store._save
            with patch.object(self.store, "_save", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    self.gateway.switch_target(NEW)
            self.assertIs(self.gateway.adapter, self.old_adapter)
            self.assertEqual(self.gateway.state()["thread_id"], OLD)
            self.assertTrue(self.created[-1].closed)
            original_save()

    def test_stale_no_thread_id_event_is_ignored_after_switching_away_and_back(self) -> None:
        with patch("gateway.SharedThreadBridge.discover_loaded_threads", return_value=self.catalog()), patch("gateway.SharedDesktopAdapter", side_effect=self.replacement), patch.object(self.gateway, "wake"):
            self.gateway.start()
            self.gateway.switch_target(NEW)
            self.gateway.switch_target(OLD)
            late_request = {"method": "adapter/request_pending", "params": {"request_id": 71, "method": "item/tool/requestUserInput", "params": {"question": "late request"}}}
            self.old_adapter.on_event(late_request)
            self.created[0].on_event(late_request)
            self.assertEqual(self.gateway.state()["approvals"], [])
            self.created[1].on_event(late_request)
            self.assertEqual(len(self.gateway.state()["approvals"]), 1)

    def test_restart_prefers_persisted_selection_over_original_cli_id(self) -> None:
        self.gateway.ensure()
        self.store.workspace_thread(NEW)
        self.gateway.close()
        with patch("shared_thread_adapter.SharedDesktopAdapter", TargetAdapter):
            server = make_server(port=0, data_dir=self.root / "data", project_dir=self.project, external_review=True, shared_thread_id=OLD)
        try:
            self.assertEqual(server.workspace_gateway.adapter.thread_id, NEW)
            self.assertEqual(server.workspace_gateway.state()["thread_id"], NEW)
        finally:
            server.server_close()

    def test_http_switch_requires_browser_capability(self) -> None:
        self.gateway.close()
        with patch("shared_thread_adapter.SharedDesktopAdapter", TargetAdapter), patch("gateway.SharedDesktopAdapter", side_effect=self.replacement), patch("gateway.SharedThreadBridge.discover_loaded_threads", return_value=self.catalog()):
            server = make_server(port=0, data_dir=self.root / "data", project_dir=self.project, external_review=True, shared_thread_id=OLD)
            serving = threading.Thread(target=server.serve_forever, daemon=True)
            serving.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
                connection.request("GET", "/api/workspace/targets")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(len(json.loads(response.read())["targets"]), 2)
                body = json.dumps({"thread_id": NEW})
                connection.request("POST", "/api/workspace/target", body=body, headers={"Content-Type": "application/json"})
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                connection.request("POST", "/api/workspace/target", body=body, headers={"Content-Type": "application/json", "X-Workspace-Capability": server.scene_store.browser_token})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read())["thread_id"], NEW)
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                serving.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

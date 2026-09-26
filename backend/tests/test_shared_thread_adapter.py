"""Existing desktop task delivery checks using fake Codex connections."""

from __future__ import annotations

import queue
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from appserver_adapter import UncertainDeliveryError  # noqa: E402
from core import APIError, SceneStore  # noqa: E402
from gateway import WorkspaceGateway  # noqa: E402
from shared_thread_adapter import SharedDesktopAdapter  # noqa: E402
from shared_thread_bridge import SharedThreadTimeout, UncertainTurnDelivery  # noqa: E402


THREAD_ID = "01a0d906-146e-7762-a1f9-49baeda8e270"


def wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for fake Codex event")


class FakeBridge:
    """Only the bridge surface that SharedDesktopAdapter uses."""

    def __init__(self, *, uncertain: bool = False, daemon: dict | None = None) -> None:
        self.ws = self
        self.uncertain = uncertain
        self.daemon = daemon
        self.incoming: queue.Queue[dict] = queue.Queue()
        self.started: list[tuple[str, list[str], str]] = []
        self.responses: list[tuple[int | str, dict]] = []
        self.active_turn_id: str | None = None
        self.closed = False

    def settimeout(self, _seconds: float) -> None:
        pass

    def read_thread(self, include_turns: bool = False) -> dict:
        if self.daemon is not None:
            return {
                "id": THREAD_ID,
                "status": {"type": self.daemon["status"]},
                "turns": list(self.daemon["turns"]) if include_turns else [],
            }
        return {"id": THREAD_ID, "status": {"type": "idle"}, "turns": []}

    def start_turn(self, text: str, image_paths, *, client_user_message_id: str) -> str:
        self.started.append((text, list(image_paths), client_user_message_id))
        if self.uncertain:
            raise UncertainTurnDelivery("socket closed after turn/start")
        if self.daemon is not None:
            self.active_turn_id = f"turn-{len(self.daemon['turns']) + 1}"
            self.daemon["turns"].append({"id": self.active_turn_id, "status": "inProgress"})
            self.daemon["status"] = "active"
        else:
            self.active_turn_id = "turn-1"
        return self.active_turn_id

    def receive_message(self) -> dict:
        try:
            message = self.incoming.get(timeout=0.05)
        except queue.Empty as exc:
            raise SharedThreadTimeout("no event yet") from exc
        if message.get("method") == "turn/completed":
            self.active_turn_id = None
        return message

    def respond_to_request(self, request_id: int | str, result: dict) -> None:
        self.responses.append((request_id, result))

    def close(self) -> None:
        self.closed = True


class FakeBoundAdapter:
    def __init__(self, *, uncertain: bool = False) -> None:
        self.uncertain = uncertain
        self.calls: list[dict] = []
        self.responses: list[tuple[int | str, dict]] = []
        self.connected = True
        self.starts = 0

    def start(self) -> str:
        self.starts += 1
        return THREAD_ID

    def refresh(self) -> dict:
        return self.status()

    def status(self) -> dict:
        return {"connected": self.connected, "thread_id": THREAD_ID, "turn_state": "idle", "pending_requests": []}

    def start_turn(self, text: str, image_paths, message_id: str | None = None) -> dict:
        self.calls.append({"text": text, "image_paths": list(image_paths), "message_id": message_id})
        if self.uncertain:
            raise UncertainDeliveryError("turn/start outcome is unknown")
        return {"thread_id": THREAD_ID, "turn_id": f"turn-{len(self.calls)}", "status": "inProgress"}

    def respond_to_request(self, request_id: int | str, result: dict) -> None:
        self.responses.append((request_id, result))

    def close(self) -> None:
        pass


class SharedDesktopAdapterTests(unittest.TestCase):
    def test_silent_socket_reconciles_only_the_exact_completed_turn(self) -> None:
        daemon = {"status": "idle", "turns": []}
        bridges: list[FakeBridge] = []
        events: list[dict] = []

        def connect(_thread_id: str, **_kwargs) -> FakeBridge:
            bridge = FakeBridge(daemon=daemon)
            bridges.append(bridge)
            return bridge

        with patch("shared_thread_adapter.SharedThreadBridge.connect_for_thread", side_effect=connect):
            adapter = SharedDesktopAdapter(THREAD_ID, events.append)
            self.assertEqual(adapter.start_turn("Fix the shape", [], message_id="feedback-1")["turn_id"], "turn-1")
            # The socket never receives turn/completed. A different completed
            # turn must not satisfy this active feedback's completion check.
            daemon["turns"].append({"id": "another-turn", "status": "completed"})
            self.assertIsNone(adapter.lookup_turn("missing-turn"))
            self.assertEqual(adapter.lookup_turn("turn-1")["status"], "inProgress")
            daemon["turns"][0]["status"] = "completed"
            daemon["status"] = "idle"
            wait_for(lambda: adapter.status()["turn_state"] == "idle", timeout=8.0)
            completed = [event for event in events if event.get("method") == "turn/completed"]
            self.assertEqual(len(completed), 1)
            self.assertEqual(completed[0]["params"]["turn"]["id"], "turn-1")
            self.assertEqual(completed[0]["params"]["turn"]["status"], "completed")
            self.assertTrue(bridges[0].closed)
            adapter.close()

    def test_preserves_connection_for_pending_approval_and_completion(self) -> None:
        bridges: list[FakeBridge] = []
        events: list[dict] = []

        def connect(_thread_id: str, **_kwargs) -> FakeBridge:
            bridge = FakeBridge()
            bridges.append(bridge)
            return bridge

        with patch("shared_thread_adapter.SharedThreadBridge.connect_for_thread", side_effect=connect):
            adapter = SharedDesktopAdapter(THREAD_ID, events.append)
            self.assertEqual(adapter.start(), THREAD_ID)
            self.assertTrue(bridges[0].closed)
            response = adapter.start_turn("Fix the cabinet", [], message_id="feedback-1")
            self.assertEqual(response["turn_id"], "turn-1")
            active = bridges[-1]
            self.assertFalse(active.closed)
            self.assertEqual(active.started[0][2], "feedback-1")
            active.incoming.put({"id": 99, "method": "item/commandExecution/requestApproval", "params": {"threadId": THREAD_ID, "turnId": "turn-1", "command": "echo inspect"}})
            wait_for(lambda: len(adapter.status()["pending_requests"]) == 1)
            self.assertEqual(events[-1]["method"], "adapter/request_pending")
            adapter.respond_to_request(99, {"decision": "decline"})
            self.assertEqual(active.responses, [(99, {"decision": "decline"})])
            active.incoming.put({"method": "turn/completed", "params": {"threadId": THREAD_ID, "turn": {"id": "turn-1", "status": "completed"}}})
            wait_for(lambda: adapter.status()["turn_state"] == "idle" and active.closed)
            self.assertTrue(any(event["method"] == "turn/completed" for event in events))
            self.assertEqual(adapter.status()["pending_requests"], [])
            adapter.close()

    def test_uncertain_turn_is_reported_and_not_retried(self) -> None:
        bridges: list[FakeBridge] = []

        def connect(_thread_id: str, **_kwargs) -> FakeBridge:
            bridge = FakeBridge(uncertain=True)
            bridges.append(bridge)
            return bridge

        with patch("shared_thread_adapter.SharedThreadBridge.connect_for_thread", side_effect=connect):
            adapter = SharedDesktopAdapter(THREAD_ID, lambda _event: None)
            with self.assertRaises(UncertainDeliveryError):
                adapter.start_turn("Fix the cabinet", [], message_id="feedback-uncertain")
            self.assertEqual(len(bridges), 1)
            self.assertEqual(len(bridges[0].started), 1)
            self.assertTrue(bridges[0].closed)
            self.assertEqual(adapter.status()["turn_state"], "unknown")
            adapter.close()


class BoundGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.store = SceneStore(self.root / "data")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_startup_recovers_completed_active_feedback_before_dispatching_next(self) -> None:
        daemon = {"status": "idle", "turns": [{"id": "turn-1", "status": "completed"}]}
        bridges: list[FakeBridge] = []

        def connect(_thread_id: str, **_kwargs) -> FakeBridge:
            bridge = FakeBridge(daemon=daemon)
            bridges.append(bridge)
            return bridge

        adapter = SharedDesktopAdapter(THREAD_ID, lambda _event: None)
        gateway = WorkspaceGateway(self.store, self.project, adapter=adapter, external_review=True)
        adapter.on_event = gateway.on_adapter_event
        workspace = gateway.ensure()
        first = self.store.submit_feedback(workspace["session_id"], {"idempotency_key": "persisted-first", "scene_revision": 1, "note": "first"})
        second = self.store.submit_feedback(workspace["session_id"], {"idempotency_key": "persisted-second", "scene_revision": 1, "note": "second"})
        with self.store.lock:
            stored = self.store.state["workspace"]
            first_item = next(item for item in stored["queue"] if item["feedback_id"] == first["feedback_id"])
            first_item.update(status="running", turn_id="turn-1")
            stored["active_feedback_id"] = first["feedback_id"]
            stored["agent"] = {"status": "running", "turn_id": "turn-1", "error": None}
            self.store._save()

        with patch("shared_thread_adapter.SharedThreadBridge.connect_for_thread", side_effect=connect):
            gateway.start()
            wait_for(lambda: next(item for item in gateway.state()["queue"] if item["feedback_id"] == second["feedback_id"])["status"] == "running")
            state = gateway.state()
            self.assertEqual(state["queue"][0]["status"], "completed")
            self.assertEqual(state["queue"][1]["status"], "running")
            self.assertEqual(state["active_feedback_id"], second["feedback_id"])
            started = [entry for bridge in bridges for entry in bridge.started]
            self.assertEqual([entry[2] for entry in started], [second["feedback_id"]])
            gateway.close()

    def test_submit_dispatches_to_bound_task_once_then_completes(self) -> None:
        adapter = FakeBoundAdapter()
        gateway = WorkspaceGateway(self.store, self.project, adapter=adapter, external_review=True)
        gateway.start()
        session_id = gateway.state()["session_id"]
        payload = {"idempotency_key": "bound-feedback-1", "scene_revision": 1, "note": "柜子形状不对应"}
        packet = gateway.submit(session_id, payload)
        wait_for(lambda: gateway.state()["queue"][0]["status"] == "running")
        state = gateway.state()
        self.assertEqual(state["thread_id"], THREAD_ID)
        self.assertEqual(state["queue"][0]["feedback_id"], packet["feedback_id"])
        self.assertEqual(state["queue"][0]["turn_id"], "turn-1")
        self.assertEqual(adapter.calls[0]["message_id"], packet["feedback_id"])
        self.assertIn("柜子形状不对应", adapter.calls[0]["text"])
        same = gateway.submit(session_id, payload)
        self.assertEqual(same["feedback_id"], packet["feedback_id"])
        self.assertEqual(len(adapter.calls), 1)
        with self.assertRaisesRegex(APIError, "delivered directly"):
            gateway.take_external_feedback(session_id, 0)
        gateway.on_adapter_event({"method": "adapter/request_pending", "params": {"request_id": 99, "method": "item/commandExecution/requestApproval", "params": {"command": "echo inspect"}}})
        approval = gateway.state()["approvals"][0]
        self.assertEqual(gateway.state()["agent"]["status"], "awaiting_approval")
        gateway.respond_to_approval(approval["approval_id"], {"decision": "decline"})
        self.assertEqual(adapter.responses, [(99, {"decision": "decline"})])
        gateway.on_adapter_event({"method": "turn/completed", "params": {"threadId": THREAD_ID, "turn": {"id": "turn-1", "status": "completed"}}})
        state = gateway.state()
        self.assertEqual(state["queue"][0]["status"], "completed")
        self.assertIsNone(state["active_feedback_id"])
        self.assertEqual(state["agent"]["status"], "idle")
        self.assertEqual(len(adapter.calls), 1)
        gateway.close()

    def test_durable_queue_moves_from_queued_to_running_to_completed(self) -> None:
        adapter = FakeBoundAdapter()
        gateway = WorkspaceGateway(self.store, self.project, adapter=adapter, external_review=True)
        gateway.start()
        session_id = gateway.state()["session_id"]
        with patch.object(gateway, "wake"):
            packet = gateway.submit(session_id, {"idempotency_key": "bound-queue-1", "scene_revision": 1, "note": "Fix the shape"})
        self.assertEqual(gateway.state()["queue"][0]["status"], "queued")
        self.assertEqual(SceneStore(self.store.data_dir).workspace()["queue"][0]["status"], "queued")
        gateway.wake()
        wait_for(lambda: gateway.state()["queue"][0]["status"] == "running")
        self.assertEqual(adapter.calls[0]["message_id"], packet["feedback_id"])
        gateway.on_adapter_event({"method": "turn/completed", "params": {"threadId": THREAD_ID, "turn": {"id": "turn-1", "status": "completed"}}})
        self.assertEqual(SceneStore(self.store.data_dir).workspace()["queue"][0]["status"], "completed")
        gateway.close()

    def test_uncertain_bound_delivery_stays_queued_for_explicit_resolution(self) -> None:
        adapter = FakeBoundAdapter(uncertain=True)
        gateway = WorkspaceGateway(self.store, self.project, adapter=adapter, external_review=True)
        gateway.start()
        session_id = gateway.state()["session_id"]
        with patch("gateway.logging.exception"):
            packet = gateway.submit(session_id, {"idempotency_key": "bound-uncertain-1", "scene_revision": 1, "note": "形状不对应"})
            wait_for(lambda: gateway.state()["queue"][0]["status"] == "delivery_uncertain")
        self.assertEqual(gateway.state()["active_feedback_id"], packet["feedback_id"])
        gateway.wake()
        time.sleep(0.05)
        self.assertEqual(len(adapter.calls), 1)
        persisted = SceneStore(self.store.data_dir).workspace()
        self.assertEqual(persisted["queue"][0]["status"], "delivery_uncertain")
        gateway.close()


if __name__ == "__main__":
    unittest.main()

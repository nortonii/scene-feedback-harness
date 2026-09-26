"""Failure and restart checks for delivery into an existing Codex task."""

from __future__ import annotations

import copy
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from appserver_adapter import UncertainDeliveryError  # noqa: E402
from core import APIError, SceneStore  # noqa: E402
from gateway import WorkspaceGateway  # noqa: E402
from shared_thread_adapter import DeliveryNotReadyError, DeliveryRejectedError  # noqa: E402


THREAD_ID = "01a0d906-146e-7762-a1f9-49baeda8e270"


def wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for delivery")


class RecordingAdapter:
    def __init__(self) -> None:
        self.connected = False
        self.runtime = "idle"
        self.turns: list[dict] = []
        self.calls: list[str] = []
        self.starts = 0
        self.uncertain_next = False
        self.save_uncertain_turn = False
        self.reject_next = False
        self.start_fail_once = False
        self.releases = 0

    def start(self) -> str:
        if self.start_fail_once:
            self.start_fail_once = False
            raise ConnectionError("desktop daemon is temporarily unavailable")
        self.connected = True
        self.starts += 1
        return THREAD_ID

    def status(self) -> dict:
        return {"connected": self.connected, "thread_id": THREAD_ID, "turn_state": "idle"}

    def refresh(self) -> dict:
        return self.status()

    def inspect_thread_status(self) -> str:
        return self.runtime

    def start_turn(self, _text: str, _images: list[str], message_id: str) -> dict:
        if self.runtime != "idle":
            raise DeliveryNotReadyError("the original Codex task is busy")
        self.calls.append(message_id)
        if self.reject_next:
            self.reject_next = False
            raise DeliveryRejectedError("turn/start was rejected")
        turn = {"id": f"turn-{len(self.calls)}", "status": "inProgress", "items": [{"type": "userMessage", "clientId": message_id}]}
        if self.uncertain_next:
            self.uncertain_next = False
            if self.save_uncertain_turn:
                self.turns.append(turn)
                self.runtime = "active"
            raise UncertainDeliveryError("socket closed after turn/start")
        self.turns.append(turn)
        self.runtime = "active"
        return {"thread_id": THREAD_ID, "turn_id": turn["id"], "status": "inProgress"}

    def lookup_feedback(self, feedback_id: str) -> list[dict]:
        return [copy.deepcopy(turn) for turn in self.turns if any(item.get("clientId") == feedback_id for item in turn.get("items", []))]

    def lookup_turn(self, turn_id: str) -> dict | None:
        return next((copy.deepcopy(turn) for turn in self.turns if turn["id"] == turn_id), None)

    def release_uncertain(self) -> None:
        self.releases += 1

    def close(self) -> None:
        self.connected = False


class GatewayReliabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.project = root / "project"
        self.project.mkdir()
        self.store = SceneStore(root / "data")
        self.adapter = RecordingAdapter()
        self.gateway = WorkspaceGateway(self.store, self.project, adapter=self.adapter, external_review=True)

    def tearDown(self) -> None:
        self.gateway.close()
        self.temporary.cleanup()

    def submit(self, key: str) -> dict:
        return self.gateway.submit(self.gateway.state()["session_id"], {"idempotency_key": key, "scene_revision": 1, "note": key})

    def test_busy_task_keeps_feedback_queued_then_sends_without_new_http_request(self) -> None:
        self.adapter.runtime = "active"
        self.gateway.start()
        packet = self.submit("busy-before-send")
        wait_for(lambda: self.gateway.state()["queue"][0].get("error"))
        self.assertEqual(self.gateway.state()["queue"][0]["status"], "queued")
        self.assertEqual(self.adapter.calls, [])
        self.adapter.runtime = "idle"
        self.gateway._supervise_once()
        wait_for(lambda: self.gateway.state()["queue"][0]["status"] == "running")
        self.assertEqual(self.adapter.calls, [packet["feedback_id"]])

    def test_daemon_outage_reconnects_and_drains_saved_feedback(self) -> None:
        self.adapter.start_fail_once = True
        self.gateway.start()
        self.assertEqual(self.gateway.state()["agent"]["status"], "disconnected")
        packet = self.submit("saved-during-outage")
        self.assertEqual(self.gateway.state()["queue"][0]["status"], "queued")
        self.gateway._supervise_once()
        wait_for(lambda: self.gateway.state()["queue"][0]["status"] == "running")
        self.assertEqual(self.adapter.calls, [packet["feedback_id"]])

    def test_explicit_rejection_requires_manual_retry(self) -> None:
        self.adapter.reject_next = True
        self.gateway.start()
        with patch("gateway.logging.exception"):
            packet = self.submit("rejected-before-acceptance")
            wait_for(lambda: self.gateway.state()["queue"][0]["status"] == "failed")
        self.assertIsNone(self.gateway.state()["queue"][0]["turn_id"])
        self.assertEqual(self.adapter.calls, [packet["feedback_id"]])
        self.gateway.confirm_queue(packet["feedback_id"], {"retry_failed": True})
        wait_for(lambda: self.gateway.state()["queue"][0]["status"] == "running")
        self.assertEqual(self.adapter.calls, [packet["feedback_id"], packet["feedback_id"]])

    def test_uncertain_send_recovers_exact_turn_and_never_replays(self) -> None:
        self.adapter.uncertain_next = True
        self.adapter.save_uncertain_turn = True
        self.gateway.start()
        with patch("gateway.logging.exception"):
            packet = self.submit("uncertain-but-saved")
            wait_for(lambda: self.gateway.state()["queue"][0]["status"] == "delivery_uncertain")
        with self.assertRaisesRegex(APIError, "already reached"):
            self.gateway.confirm_queue(packet["feedback_id"], {"retry_uncertain": True})
        self.assertEqual(self.gateway.state()["queue"][0]["status"], "running")
        self.assertEqual(self.adapter.calls, [packet["feedback_id"]])
        self.adapter.turns[0]["status"] = "completed"
        self.adapter.runtime = "idle"
        self.gateway._supervise_once()
        self.assertEqual(self.gateway.state()["queue"][0]["status"], "completed")
        self.assertEqual(self.adapter.calls, [packet["feedback_id"]])

    def test_unknown_delivery_is_quarantined_then_new_feedback_proceeds(self) -> None:
        self.adapter.uncertain_next = True
        self.gateway.start()
        with patch("gateway.logging.exception"):
            first = self.submit("lost-response-no-turn")
            wait_for(lambda: self.gateway.state()["queue"][0]["status"] == "delivery_uncertain")
        with self.store.lock:
            self.store.state["workspace"]["queue"][0]["uncertain_since"] = (datetime.now(timezone.utc) - timedelta(seconds=40)).isoformat(timespec="seconds")
            self.store._save()
        self.gateway._supervise_once()
        state = self.gateway.state()
        self.assertIsNone(state["active_feedback_id"])
        self.assertEqual(state["queue"][0]["status"], "delivery_uncertain")
        self.assertIn("quarantined_at", state["queue"][0])
        second = self.submit("new-after-quarantine")
        wait_for(lambda: self.gateway.state()["queue"][1]["status"] == "running")
        self.assertEqual(self.adapter.calls, [first["feedback_id"], second["feedback_id"]])

    def test_restart_tracks_existing_turn_to_completion(self) -> None:
        workspace = self.gateway.ensure()
        packet = self.store.submit_feedback(workspace["session_id"], {"idempotency_key": "restart-existing", "scene_revision": 1, "note": "continue"})
        with self.store.lock:
            item = self.store.state["workspace"]["queue"][0]
            item.update(status="running", turn_id="turn-existing")
            self.store.state["workspace"]["active_feedback_id"] = packet["feedback_id"]
            self.store._save()
        self.adapter.turns.append({"id": "turn-existing", "status": "inProgress", "items": [{"type": "userMessage", "clientId": packet["feedback_id"]}]})
        self.adapter.runtime = "active"
        self.gateway.start()
        self.assertEqual(self.gateway.state()["queue"][0]["status"], "running")
        self.adapter.turns[0]["status"] = "completed"
        self.adapter.runtime = "idle"
        self.gateway._supervise_once()
        self.assertEqual(self.gateway.state()["queue"][0]["status"], "completed")
        self.assertIsNone(self.gateway.state()["active_feedback_id"])
        self.assertEqual(self.adapter.calls, [])

    def test_unrelated_completion_cannot_clear_unknown_active_delivery(self) -> None:
        self.gateway.start()
        with patch.object(self.gateway, "wake"):
            packet = self.submit("unrelated-completion")
        with self.store.lock:
            item = self.store.state["workspace"]["queue"][0]
            item["status"] = "dispatching"
            self.store.state["workspace"]["active_feedback_id"] = packet["feedback_id"]
            self.store._save()
        self.gateway.on_adapter_event({"method": "turn/completed", "params": {"turn": {"id": "another-turn", "status": "completed"}}})
        self.assertEqual(self.gateway.state()["active_feedback_id"], packet["feedback_id"])
        self.assertEqual(self.gateway.state()["queue"][0]["status"], "dispatching")

    def test_legacy_mcp_packet_becomes_queued_even_if_task_is_busy(self) -> None:
        workspace = self.gateway.ensure()
        self.store.submit_feedback(workspace["session_id"], {"idempotency_key": "legacy-mcp-packet", "scene_revision": 1, "note": "continue"})
        with self.store.lock:
            self.store.state["workspace"]["queue"][0]["status"] = "awaiting_mcp"
            self.store._save()
        self.adapter.runtime = "active"
        self.gateway.start()
        wait_for(lambda: self.gateway.state()["queue"][0]["status"] == "queued")
        self.assertEqual(self.adapter.calls, [])


if __name__ == "__main__":
    unittest.main()

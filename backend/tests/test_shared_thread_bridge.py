"""Shared desktop daemon bridge checks without touching a real Codex task."""

from __future__ import annotations

import json
import socket
import sys
import tempfile
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared_thread_bridge import (  # noqa: E402
    SharedThreadBridge,
    SharedThreadBridgeError,
    SharedThreadNotIdle,
    SharedThreadRPCRejected,
    SharedThreadTimeout,
    UncertainTurnDelivery,
    _private_socket_candidates,
    _connect,
)


THREAD_ID = "01a0d906-146e-7762-a1f9-49baeda8e270"


class FakeWebSocket:
    def __init__(self, status: str = "idle", *, fail_turn_receive: bool = False, reject_turn: bool = False, reject_error: str = "invalid input") -> None:
        self.status = status
        self.fail_turn_receive = fail_turn_receive
        self.reject_turn = reject_turn
        self.reject_error = reject_error
        self.turns: list[dict] = []
        self.sent: list[dict] = []
        self.responses: deque[dict] = deque()
        self.closed = False

    def send(self, data: str) -> None:
        message = json.loads(data)
        self.sent.append(message)
        method = message.get("method")
        request_id = message.get("id")
        if method == "initialize":
            self.responses.append({"id": request_id, "result": {"userAgent": "fake"}})
            self.responses.append({"method": "account/updated", "params": {}})
        elif method == "thread/read":
            thread = {"id": THREAD_ID, "status": {"type": self.status}}
            if message.get("params", {}).get("includeTurns"):
                thread["turns"] = list(self.turns)
            self.responses.append({"id": request_id, "result": {"thread": thread}})
        elif method == "thread/resume":
            self.responses.append({"id": request_id, "result": {"thread": {"id": THREAD_ID, "status": {"type": self.status}}}})
        elif method == "turn/start" and self.reject_turn:
            self.responses.append({"id": request_id, "error": {"code": -32602, "message": self.reject_error}})
        elif method == "turn/start" and not self.fail_turn_receive:
            self.responses.append({"id": 99, "method": "item/commandExecution/requestApproval", "params": {"threadId": THREAD_ID, "turnId": "turn-1"}})
            self.responses.append({"id": request_id, "result": {"turn": {"id": "turn-1", "status": "inProgress"}}})
            self.responses.append({"method": "turn/completed", "params": {"threadId": THREAD_ID, "turn": {"id": "turn-1", "status": "completed"}}})
        elif method == "turn/interrupt":
            self.responses.append({"id": request_id, "result": {}})

    def recv(self) -> str:
        if not self.responses:
            raise TimeoutError("fake daemon stopped responding")
        return json.dumps(self.responses.popleft())

    def close(self) -> None:
        self.closed = True


class SharedThreadBridgeTests(unittest.TestCase):
    def test_reads_exact_turn_history_without_inferring_from_idle(self) -> None:
        ws = FakeWebSocket("idle")
        ws.turns = [
            {"id": "turn-1", "status": "inProgress"},
            {"id": "unrelated", "status": "completed"},
        ]
        bridge = SharedThreadBridge(ws, THREAD_ID)
        self.assertNotIn("turns", bridge.read_thread())
        turns = bridge.read_thread(include_turns=True)["turns"]
        self.assertEqual(turns[0], {"id": "turn-1", "status": "inProgress"})
        self.assertEqual(turns[1], {"id": "unrelated", "status": "completed"})
        self.assertEqual([item["params"]["includeTurns"] for item in ws.sent if item.get("method") == "thread/read"], [False, True])

    def test_subscribes_only_to_selected_loaded_daemon(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            loaded_path = root / ("a" * 64)
            other_path = root / ("b" * 64)
            connections = {loaded_path: FakeWebSocket(), other_path: FakeWebSocket("notLoaded")}
            with socket.socket(socket.AF_UNIX) as first, socket.socket(socket.AF_UNIX) as second:
                first.bind(str(loaded_path))
                second.bind(str(other_path))
                loaded_path.chmod(0o600)
                other_path.chmod(0o600)
                bridge = SharedThreadBridge.connect_for_thread(
                    THREAD_ID, socket_dir=root, connector=lambda path, _timeout: connections[path], subscribe=True
                )
                try:
                    self.assertEqual(bridge.socket_path, loaded_path)
                    loaded_subscriptions = [item for item in connections[loaded_path].sent if item.get("method") == "thread/resume"]
                    other_subscriptions = [item for item in connections[other_path].sent if item.get("method") == "thread/resume"]
                    self.assertEqual(len(loaded_subscriptions), 1)
                    self.assertEqual(loaded_subscriptions[0]["params"]["threadId"], THREAD_ID)
                    self.assertEqual(other_subscriptions, [])
                finally:
                    bridge.close()

    def test_connect_performs_unix_websocket_upgrade(self) -> None:
        with patch("shared_thread_bridge.socket.socket") as socket_factory, patch("shared_thread_bridge._checked_peer") as peer_check, patch("websocket.create_connection") as upgrade:
            raw = socket_factory.return_value
            result = _connect(Path("/tmp/private-codex-socket"), 7.0)
            self.assertIs(result, upgrade.return_value)
            socket_factory.assert_called_once_with(socket.AF_UNIX, socket.SOCK_STREAM)
            raw.connect.assert_called_once_with("/tmp/private-codex-socket")
            peer_check.assert_called_once_with(raw)
            upgrade.assert_called_once_with("ws://localhost/", socket=raw, timeout=7.0, suppress_origin=True)

    def test_discovers_only_private_same_user_socket(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            good = root / ("a" * 64)
            public = root / ("b" * 64)
            with socket.socket(socket.AF_UNIX) as first, socket.socket(socket.AF_UNIX) as second:
                first.bind(str(good))
                second.bind(str(public))
                good.chmod(0o600)
                public.chmod(0o666)
                (root / ("c" * 64)).write_text("not a socket")
                self.assertEqual(_private_socket_candidates(root), [good])
                root.chmod(0o755)
                with self.assertRaisesRegex(SharedThreadBridgeError, "private"):
                    _private_socket_candidates(root)

    def test_selects_loaded_idle_desktop_daemon_and_builds_visual_turn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            loaded_path = root / ("a" * 64)
            other_path = root / ("b" * 64)
            image = root / "mark.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\n")
            connections = {loaded_path: FakeWebSocket(), other_path: FakeWebSocket("notLoaded")}
            with socket.socket(socket.AF_UNIX) as first, socket.socket(socket.AF_UNIX) as second:
                first.bind(str(loaded_path))
                second.bind(str(other_path))
                loaded_path.chmod(0o600)
                other_path.chmod(0o600)
                bridge = SharedThreadBridge.connect_for_thread(THREAD_ID, socket_dir=root, connector=lambda path, _timeout: connections[path])
                try:
                    self.assertEqual(bridge.socket_path, loaded_path)
                    self.assertTrue(connections[other_path].closed)
                    self.assertEqual(bridge.receive_message()["method"], "account/updated")
                    self.assertEqual(bridge.start_turn("请处理这条反馈", [image], client_user_message_id="feedback-1", cwd=root), "turn-1")
                    turn = next(item for item in connections[loaded_path].sent if item.get("method") == "turn/start")
                    self.assertEqual(turn["params"], {
                        "threadId": THREAD_ID,
                        "input": [{"type": "text", "text": "请处理这条反馈"}, {"type": "localImage", "path": str(image)}],
                        "clientUserMessageId": "feedback-1",
                        "cwd": str(root),
                    })
                    request = bridge.receive_message()
                    self.assertEqual(request["method"], "item/commandExecution/requestApproval")
                    bridge.respond_to_request(request["id"], {"decision": "decline"})
                    self.assertEqual(connections[loaded_path].sent[-1], {"id": 99, "result": {"decision": "decline"}})
                    self.assertEqual(bridge.receive_message()["method"], "turn/completed")
                    self.assertIsNone(bridge.active_turn_id)
                finally:
                    bridge.close()
                self.assertTrue(connections[loaded_path].closed)

    def test_rejects_busy_task_before_turn_start(self) -> None:
        ws = FakeWebSocket("active")
        bridge = SharedThreadBridge(ws, THREAD_ID)
        with self.assertRaises(SharedThreadNotIdle):
            bridge.start_turn("feedback", client_user_message_id="feedback-1")
        self.assertFalse(any(item.get("method") == "turn/start" for item in ws.sent))

    def test_can_bind_busy_desktop_task_without_starting_turn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            path = root / ("a" * 64)
            with socket.socket(socket.AF_UNIX) as listener:
                listener.bind(str(path))
                path.chmod(0o600)
                ws = FakeWebSocket("active")
                bridge = SharedThreadBridge.connect_for_thread(THREAD_ID, socket_dir=root, require_idle=False, connector=lambda _path, _timeout: ws)
                try:
                    self.assertEqual(bridge.read_thread()["status"], {"type": "active"})
                    with self.assertRaises(SharedThreadNotIdle):
                        bridge.start_turn("feedback", client_user_message_id="feedback-1")
                finally:
                    bridge.close()

    def test_interrupt_requires_active_matching_turn(self) -> None:
        ws = FakeWebSocket()
        bridge = SharedThreadBridge(ws, THREAD_ID)
        with self.assertRaises(SharedThreadBridgeError):
            bridge.interrupt_turn()
        self.assertEqual(bridge.start_turn("feedback", client_user_message_id="feedback-1"), "turn-1")
        with self.assertRaises(SharedThreadBridgeError):
            bridge.interrupt_turn("some-other-turn")
        bridge.interrupt_turn("turn-1")
        interrupt = next(item for item in ws.sent if item.get("method") == "turn/interrupt")
        self.assertEqual(interrupt["params"], {"threadId": THREAD_ID, "turnId": "turn-1"})

    def test_uncertain_delivery_is_never_retried(self) -> None:
        ws = FakeWebSocket(fail_turn_receive=True)
        bridge = SharedThreadBridge(ws, THREAD_ID)
        with self.assertRaisesRegex(UncertainTurnDelivery, "inspect task history"):
            bridge.start_turn("feedback", client_user_message_id="feedback-1")
        self.assertEqual(len([item for item in ws.sent if item.get("method") == "turn/start"]), 1)

    def test_explicit_turn_rejection_is_not_uncertain(self) -> None:
        ws = FakeWebSocket(reject_turn=True)
        bridge = SharedThreadBridge(ws, THREAD_ID)
        with self.assertRaisesRegex(SharedThreadRPCRejected, "invalid input"):
            bridge.start_turn("feedback", client_user_message_id="feedback-1")
        self.assertEqual(len([item for item in ws.sent if item.get("method") == "turn/start"]), 1)
        self.assertIsNone(bridge.active_turn_id)

    def test_competing_turn_after_idle_check_is_retryable(self) -> None:
        ws = FakeWebSocket(reject_turn=True)
        original_send = ws.send

        def send(data: str) -> None:
            if json.loads(data).get("method") == "turn/start":
                ws.status = "active"
            original_send(data)

        ws.send = send
        bridge = SharedThreadBridge(ws, THREAD_ID)
        with self.assertRaises(SharedThreadNotIdle):
            bridge.start_turn("feedback", client_user_message_id="feedback-1")

    def test_busy_rpc_rejection_is_retryable_even_when_competing_turn_finishes(self) -> None:
        bridge = SharedThreadBridge(FakeWebSocket(reject_turn=True, reject_error="turn already in progress"), THREAD_ID)
        with self.assertRaises(SharedThreadNotIdle):
            bridge.start_turn("feedback", client_user_message_id="feedback-1")

    def test_idle_event_read_timeout_is_distinct_from_disconnection(self) -> None:
        bridge = SharedThreadBridge(FakeWebSocket(), THREAD_ID)
        with self.assertRaises(SharedThreadTimeout):
            bridge.receive_message()

    def test_validates_task_id_message_and_image(self) -> None:
        with self.assertRaises(ValueError):
            SharedThreadBridge(FakeWebSocket(), "not-an-id")
        bridge = SharedThreadBridge(FakeWebSocket(), THREAD_ID)
        with self.assertRaises(ValueError):
            bridge.start_turn("", client_user_message_id="feedback-1")
        with self.assertRaises(FileNotFoundError):
            bridge.start_turn("feedback", ["/missing-scene-feedback.png"], client_user_message_id="feedback-1")


if __name__ == "__main__":
    unittest.main()

"""Shared desktop daemon bridge checks without touching a real Codex task."""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared_thread_bridge import (  # noqa: E402
    ExternalSettingsUnavailable,
    SharedThreadBridge,
    SharedThreadBridgeError,
    SharedThreadNotIdle,
    SharedThreadRPCRejected,
    SharedThreadTimeout,
    SharedThreadPermissionMismatch,
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
        self.subscribed = False
        self.effective_settings = {
            "approvalPolicy": "on-request", "approvalsReviewer": "user",
            "sandbox": {"type": "readOnly"}, "activePermissionProfile": {"id": ":read-only"},
        }

    def permission_settings(self, params: dict) -> dict:
        profile = params.get("permissions")
        policy = params.get("sandboxPolicy") or {"type": {
            ":danger-full-access": "dangerFullAccess", ":workspace": "workspaceWrite", ":read-only": "readOnly",
        }[profile]}
        return {"sandbox": policy, "approvalPolicy": params["approvalPolicy"],
                "approvalsReviewer": "user", "activePermissionProfile": {"id": profile} if profile else None}

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
            self.subscribed = True
            if self.status == "notLoaded" and "permissions" in message["params"]:
                self.effective_settings = self.permission_settings(message["params"])
            self.responses.append({"id": request_id, "result": {
                "thread": {"id": THREAD_ID, "status": {"type": self.status}}, **self.effective_settings,
            }})
        elif method == "permissionProfile/list":
            self.responses.append({"id": request_id, "result": {"data": [
                {"id": profile, "allowed": True} for profile in (":read-only", ":workspace", ":danger-full-access")
            ], "nextCursor": None}})
        elif method == "thread/settings/update":
            params = message["params"]
            settings = self.permission_settings(params)
            changed = settings != self.effective_settings
            self.effective_settings = settings
            self.responses.append({"id": request_id, "result": {}})
            if self.subscribed and changed:
                self.responses.append({"method": "thread/settings/updated", "params": {
                    "threadId": THREAD_ID, "threadSettings": {
                        "sandboxPolicy": settings["sandbox"],
                        **{key: value for key, value in settings.items() if key != "sandbox"},
                    },
                }})
        elif method == "thread/loaded/list":
            self.responses.append({"id": request_id, "result": {"data": [THREAD_ID], "nextCursor": None}})
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


BOUND_SETTINGS = {
    "model": "gpt-6-astra", "reasoningEffort": "xhigh", "approvalPolicy": "never",
    "approvalsReviewer": "user", "sandbox": {"type": "dangerFullAccess"},
    "activePermissionProfile": {"id": ":danger-full-access"},
}


def write_bound_rollout(home: Path, *, settings: dict | None = None) -> Path:
    folder = home / "sessions" / "2026" / "10" / "05"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"rollout-2026-10-05T21-09-51-{THREAD_ID}.jsonl"
    payload = settings if settings is not None else {
        "model": "gpt-6-astra", "effort": "xhigh", "approval_policy": "never",
        "approvals_reviewer": "user", "sandbox_policy": {"type": "danger-full-access"},
        "active_permission_profile": {"id": ":danger-full-access"},
    }
    path.write_text("\n".join((json.dumps({"type": "session_meta", "payload": {"id": THREAD_ID}}),
                               json.dumps({"type": "turn_context", "payload": payload}))) + "\n", encoding="utf-8")
    return path


class BoundWebSocket(FakeWebSocket):
    def __init__(self, path: Path, *, status: str = "notLoaded", resume_status: str = "idle",
                 resume_settings: dict | None = None, wrong_resume_id: bool = False,
                 reject_resume: bool = False, thread_fields: dict | None = None) -> None:
        super().__init__(status)
        self.path = path
        self.resume_status = resume_status
        self.resume_settings = resume_settings or BOUND_SETTINGS
        self.wrong_resume_id = wrong_resume_id
        self.reject_resume = reject_resume
        self.thread_fields = thread_fields or {}

    def send(self, data: str) -> None:
        message = json.loads(data)
        method = message.get("method")
        if method == "thread/resume" and self.reject_resume:
            self.sent.append(message)
            self.responses.append({"id": message["id"], "error": {"code": -32600, "message": "already has an active writer"}})
            return
        if method == "thread/resume":
            self.status = self.resume_status
        super().send(data)
        if method == "thread/read":
            self.responses[-1]["result"]["thread"].update({
                "path": str(self.path), "source": "vscode", "parentThreadId": None, "ephemeral": False,
                **self.thread_fields,
            })
        elif method == "thread/resume":
            result = self.responses[-1]["result"]
            result.update(self.resume_settings)
            if self.wrong_resume_id:
                result["thread"]["id"] = "01a0d906-146e-7762-a1f9-49baeda8e271"


class SharedThreadBridgeTests(unittest.TestCase):
    def test_saved_owned_permissions_survive_loaded_and_unloaded_resume_and_turns(self) -> None:
        class ResumableWebSocket(FakeWebSocket):
            def send(self, data: str) -> None:
                super().send(data)
                if json.loads(data).get("method") == "thread/resume":
                    self.status = "idle"

        config = {"mcp_servers": {"scene_feedback": {"env": {"SCENE_FEEDBACK_DATA_DIR": "/tmp/scoped-scene"}}}}
        for mode, approval, profile in (
            ("full_access", "never", ":danger-full-access"),
            ("workspace_write", "on-request", ":workspace"),
            ("read_only", "on-request", ":read-only"),
        ):
            for status in ("idle", "notLoaded"):
                with self.subTest(mode=mode, status=status), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    root.chmod(0o700)
                    path = root / ("a" * 64)
                    with socket.socket(socket.AF_UNIX) as listener:
                        listener.bind(str(path))
                        path.chmod(0o600)
                        ws = ResumableWebSocket(status)
                        bridge = SharedThreadBridge.connect_for_thread(
                            THREAD_ID, socket_dir=root, connector=lambda _path, _timeout: ws,
                            subscribe=True, allow_owned_resume=True, thread_config=config, permission_mode=mode,
                        )
                        try:
                            resumes = [item["params"] for item in ws.sent if item.get("method") == "thread/resume"]
                            self.assertEqual(resumes[0], {"threadId": THREAD_ID, "config": config,
                                                        "approvalPolicy": approval, "permissions": profile, "approvalsReviewer": "user"})
                            updates = [item["params"] for item in ws.sent if item.get("method") == "thread/settings/update"]
                            self.assertEqual(updates, [{"threadId": THREAD_ID, "approvalPolicy": approval, "permissions": profile, "approvalsReviewer": "user"}] if status == "idle" and mode != "read_only" else [])
                            self.assertEqual(resumes[1:], [{"threadId": THREAD_ID}] if updates else [])
                            for number in (1, 2):
                                bridge.start_turn("feedback", client_user_message_id=f"feedback-{number}", permission_mode=mode)
                                while bridge.active_turn_id is not None:
                                    bridge.receive_message()
                            turns = [item["params"] for item in ws.sent if item.get("method") == "turn/start"]
                            self.assertEqual(len(turns), 2)
                            for params in turns:
                                self.assertEqual(params["permissions"], profile)
                                self.assertEqual(params["approvalPolicy"], approval)
                                self.assertEqual(params["approvalsReviewer"], "user")
                                self.assertNotIn("sandboxPolicy", params)
                                self.assertNotIn("sandbox", params)
                            self.assertEqual(sum(item.get("method") == "permissionProfile/list" for item in ws.sent), 1)
                            initialize = next(item for item in ws.sent if item.get("method") == "initialize")
                            self.assertIs(initialize["params"]["capabilities"]["experimentalApi"], True)
                        finally:
                            bridge.close()

    def test_unowned_task_cannot_receive_permission_override(self) -> None:
        with patch("shared_thread_bridge._private_socket_candidates") as candidates:
            with self.assertRaisesRegex(ValueError, "workbench-owned"):
                SharedThreadBridge.connect_for_thread(THREAD_ID, permission_mode="full_access")
            with self.assertRaisesRegex(ValueError, "permission_mode"):
                SharedThreadBridge.connect_for_thread(THREAD_ID, allow_owned_resume=True, permission_mode="invalid")
            candidates.assert_not_called()

    def test_named_profiles_override_persisted_read_only_on_next_loaded_turn(self) -> None:
        class PersistedReadOnlyWebSocket(FakeWebSocket):
            def send(self, data: str) -> None:
                message = json.loads(data)
                if message.get("method") == "thread/resume":
                    self.sent.append(message)
                    self.subscribed = True
                    # Native loaded resume returns current settings even with
                    # a requested override. Never substitute legacy sandbox.
                    self.responses.append({"id": message["id"], "result": {
                        "thread": {"id": THREAD_ID, "status": {"type": "idle"}},
                        **self.effective_settings,
                    }})
                    return
                super().send(data)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            path = root / ("a" * 64)
            with socket.socket(socket.AF_UNIX) as listener:
                listener.bind(str(path))
                path.chmod(0o600)
                ws = PersistedReadOnlyWebSocket()
                bridge = SharedThreadBridge.connect_for_thread(
                    THREAD_ID, socket_dir=root, connector=lambda _path, _timeout: ws,
                    subscribe=True, allow_owned_resume=True, permission_mode="full_access",
                )
                try:
                    bridge.start_turn("feedback", client_user_message_id="feedback-1", permission_mode="full_access")
                    params = next(item["params"] for item in ws.sent if item.get("method") == "turn/start")
                    self.assertEqual(params["permissions"], ":danger-full-access")
                    self.assertEqual(params["approvalPolicy"], "never")
                    self.assertNotIn("sandboxPolicy", params)
                finally:
                    bridge.close()

    def test_profile_denial_or_catalog_errors_never_fall_back_to_legacy(self) -> None:
        class CatalogWebSocket(FakeWebSocket):
            def __init__(self, code=None, allowed=True):
                super().__init__()
                self.code = code
                self.allowed = allowed

            def send(self, data: str) -> None:
                message = json.loads(data)
                if message.get("method") == "permissionProfile/list":
                    self.sent.append(message)
                    if self.code is not None:
                        self.responses.append({"id": message["id"], "error": {"code": self.code, "message": "catalog failed"}})
                    else:
                        self.responses.append({"id": message["id"], "result": {"data": [{"id": ":danger-full-access", "allowed": self.allowed}]}})
                    return
                super().send(data)

        for code, allowed in ((None, False), (-32602, True), (-32600, True)):
            with self.subTest(code=code, allowed=allowed):
                ws = CatalogWebSocket(code, allowed)
                bridge = SharedThreadBridge(ws, THREAD_ID)
                with self.assertRaises(SharedThreadBridgeError):
                    bridge.start_turn("feedback", client_user_message_id="feedback-1", permission_mode="full_access")
                self.assertFalse(any(item.get("method") == "turn/start" for item in ws.sent))

        ws = CatalogWebSocket(-32601)
        bridge = SharedThreadBridge(ws, THREAD_ID)
        bridge.start_turn("feedback", client_user_message_id="feedback-1", permission_mode="full_access")
        params = next(item["params"] for item in ws.sent if item.get("method") == "turn/start")
        self.assertEqual(params["sandboxPolicy"], {"type": "dangerFullAccess"})
        self.assertNotIn("permissions", params)

    def test_creation_effective_permission_mismatch_retains_created_id(self) -> None:
        created = "01a0de73-9763-7432-8ca4-5892c0904234"

        class WrongCreationWebSocket(FakeWebSocket):
            def send(self, data: str) -> None:
                super().send(data)
                message = json.loads(data)
                if message.get("method") == "thread/start":
                    self.responses.append({"id": message["id"], "result": {
                        "thread": {"id": created, "ephemeral": False},
                        "approvalPolicy": "never", "sandbox": {"type": "readOnly"},
                        "activePermissionProfile": {"id": ":read-only"},
                    }})

        bridge = SharedThreadBridge(WrongCreationWebSocket(), THREAD_ID)
        with self.assertRaises(SharedThreadPermissionMismatch) as raised:
            bridge.create_thread("gpt-6-astra", Path("/tmp/project"), permission_mode="full_access")
        self.assertEqual(raised.exception.created_thread_id, created)
        self.assertEqual(bridge.thread_id, created)

    def test_unloaded_resume_effective_permission_mismatch_blocks_delivery(self) -> None:
        class WrongResumeWebSocket(FakeWebSocket):
            def send(self, data: str) -> None:
                message = json.loads(data)
                if message.get("method") == "thread/resume":
                    self.sent.append(message)
                    self.responses.append({"id": message["id"], "result": {
                        "thread": {"id": THREAD_ID, "status": {"type": "idle"}},
                        "sandbox": {"type": "readOnly"}, "activePermissionProfile": {"id": ":read-only"},
                    }})
                    return
                super().send(data)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            path = root / ("a" * 64)
            with socket.socket(socket.AF_UNIX) as listener:
                listener.bind(str(path))
                path.chmod(0o600)
                ws = WrongResumeWebSocket("notLoaded")
                with self.assertRaises(SharedThreadPermissionMismatch):
                    SharedThreadBridge.connect_for_thread(THREAD_ID, socket_dir=root, connector=lambda _path, _timeout: ws,
                                                          allow_owned_resume=True, permission_mode="full_access")
                self.assertTrue(ws.closed)
                self.assertFalse(any(item.get("method") == "turn/start" for item in ws.sent))

    def test_idle_sync_is_requested_only_for_owned_startup_and_feedback_connections(self) -> None:
        for status, mode, sync, expected in (
            ("idle", "full_access", False, False),  # monitor
            ("active", "full_access", True, False),
            ("idle", None, True, False),  # external/unknown mode
            ("idle", "full_access", True, True),
        ):
            with self.subTest(status=status, mode=mode, sync=sync), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                root.chmod(0o700)
                path = root / ("a" * 64)
                with socket.socket(socket.AF_UNIX) as listener:
                    listener.bind(str(path))
                    path.chmod(0o600)
                    ws = FakeWebSocket(status)
                    bridge = SharedThreadBridge.connect_for_thread(
                        THREAD_ID, socket_dir=root, connector=lambda _path, _timeout: ws,
                        require_idle=False, allow_owned_resume=mode is not None,
                        permission_mode=mode, sync_owned_permissions=sync,
                    )
                    bridge.close()
                    self.assertEqual(any(item.get("method") == "thread/settings/update" for item in ws.sent), expected)
                    self.assertFalse(any(item.get("method") == "turn/start" for item in ws.sent))

    def test_settings_update_requires_verified_metadata_or_explicit_unsupported_method(self) -> None:
        class SettingsWebSocket(FakeWebSocket):
            def __init__(self, code=None, bad_policy=False):
                super().__init__()
                self.code = code
                self.bad_policy = bad_policy

            def send(self, data: str) -> None:
                message = json.loads(data)
                if message.get("method") == "thread/settings/update":
                    self.sent.append(message)
                    if self.code is not None:
                        self.responses.append({"id": message["id"], "error": {"code": self.code, "message": "settings failed"}})
                    else:
                        self.effective_settings = {
                            "sandbox": {"type": "externalSandbox" if self.bad_policy else "dangerFullAccess"},
                            "activePermissionProfile": {"id": ":danger-full-access"}, "approvalPolicy": "never", "approvalsReviewer": "user",
                        }
                        self.responses.append({"method": "thread/settings/updated", "params": {
                            "threadId": THREAD_ID, "threadSettings": {
                                "sandboxPolicy": {"type": "externalSandbox" if self.bad_policy else "dangerFullAccess"},
                                "activePermissionProfile": {"id": ":danger-full-access"}, "approvalPolicy": "never",
                                "approvalsReviewer": "user",
                            },
                        }})
                        self.responses.append({"id": message["id"], "result": {}})
                    return
                super().send(data)

        for code, bad_policy in ((-32602, False), (-32600, False), (None, True)):
            with self.subTest(code=code, bad_policy=bad_policy):
                bridge = SharedThreadBridge(SettingsWebSocket(code, bad_policy), THREAD_ID)
                with self.assertRaises(SharedThreadBridgeError):
                    bridge.sync_saved_permissions("full_access")
        bridge = SharedThreadBridge(SettingsWebSocket(-32601), THREAD_ID)
        self.assertIsNone(bridge.sync_saved_permissions("full_access"))
        bridge.start_turn("feedback", client_user_message_id="feedback-1", permission_mode="full_access")
        params = next(item["params"] for item in bridge.ws.sent if item.get("method") == "turn/start")
        self.assertEqual(params["permissions"], ":danger-full-access")

        # A previous resume notification must not be mistaken for the update.
        bridge = SharedThreadBridge(SettingsWebSocket(), THREAD_ID)
        bridge._pending.append({"method": "thread/settings/updated", "params": {
            "threadId": THREAD_ID, "threadSettings": {"sandboxPolicy": {"type": "readOnly"}},
        }})
        self.assertEqual(bridge.sync_saved_permissions("full_access")["sandbox"], {"type": "dangerFullAccess"})

    def test_changed_and_unchanged_settings_do_not_require_notifications(self) -> None:
        class NoNotificationWebSocket(FakeWebSocket):
            def send(self, data: str) -> None:
                super().send(data)
                if json.loads(data).get("method") == "thread/settings/update":
                    self.responses = deque(item for item in self.responses if item.get("method") != "thread/settings/updated")

        ws = NoNotificationWebSocket()
        bridge = SharedThreadBridge(ws, THREAD_ID)
        first = bridge.sync_saved_permissions("full_access")
        self.assertEqual(first["activePermissionProfile"]["id"], ":danger-full-access")
        second = bridge.sync_saved_permissions("full_access")
        self.assertEqual(second["sandbox"], {"type": "dangerFullAccess"})
        self.assertEqual(sum(item.get("method") == "thread/settings/update" for item in ws.sent), 1)
        self.assertFalse(any(item.get("method") == "turn/start" for item in ws.sent))

    def test_cached_permission_settings_must_belong_to_the_bound_task(self) -> None:
        for thread in (None, {"id": "01a0de73-9763-7432-8ca4-5892c0904234"}):
            with self.subTest(thread=thread):
                ws = FakeWebSocket()
                bridge = SharedThreadBridge(ws, THREAD_ID)
                settings = {"thread": thread, "approvalPolicy": "never", "approvalsReviewer": "user",
                            "sandbox": {"type": "dangerFullAccess"}, "activePermissionProfile": {"id": ":danger-full-access"}}
                with self.assertRaisesRegex(SharedThreadBridgeError, "wrong task"):
                    bridge.sync_saved_permissions("full_access", current_settings=settings)
                self.assertFalse(any(item.get("method") in {"thread/settings/update", "turn/start"} for item in ws.sent))

    def test_discovers_loaded_tasks_without_requiring_old_task_to_remain_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            path = root / ("a" * 64)
            with socket.socket(socket.AF_UNIX) as listener:
                listener.bind(str(path))
                path.chmod(0o600)
                ws = FakeWebSocket()
                records = SharedThreadBridge.discover_loaded_threads(socket_dir=root, connector=lambda _path, _timeout: ws)
                self.assertEqual([(entry_path, thread["id"]) for entry_path, thread in records], [(path, THREAD_ID)])
                self.assertTrue(ws.closed)

    def test_owned_unloaded_task_resumes_only_on_unique_desktop_daemon(self) -> None:
        class ResumableWebSocket(FakeWebSocket):
            def send(self, data: str) -> None:
                super().send(data)
                if json.loads(data).get("method") == "thread/resume":
                    self.status = "idle"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            path = root / ("a" * 64)
            with socket.socket(socket.AF_UNIX) as listener:
                listener.bind(str(path))
                path.chmod(0o600)
                ws = ResumableWebSocket("notLoaded")
                bridge = SharedThreadBridge.connect_for_thread(
                    THREAD_ID, socket_dir=root, connector=lambda _path, _timeout: ws,
                    allow_owned_resume=True, subscribe=True,
                    thread_config={"mcp_servers": {"scene_feedback": {"env": {"SCENE_FEEDBACK_DATA_DIR": "/tmp/new-data"}}}},
                )
                try:
                    self.assertEqual(bridge.read_thread()["status"]["type"], "idle")
                    self.assertEqual([item["method"] for item in ws.sent].count("thread/resume"), 1)
                    resume = next(item for item in ws.sent if item.get("method") == "thread/resume")
                    self.assertEqual(resume["params"]["config"]["mcp_servers"]["scene_feedback"]["env"]["SCENE_FEEDBACK_DATA_DIR"], "/tmp/new-data")
                finally:
                    bridge.close()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            first = root / ("a" * 64)
            second = root / ("b" * 64)
            connections = {first: ResumableWebSocket("notLoaded"), second: ResumableWebSocket("notLoaded")}
            with socket.socket(socket.AF_UNIX) as left, socket.socket(socket.AF_UNIX) as right:
                left.bind(str(first))
                right.bind(str(second))
                first.chmod(0o600)
                second.chmod(0o600)
                with self.assertRaisesRegex(SharedThreadBridgeError, "not loaded"):
                    SharedThreadBridge.connect_for_thread(
                        THREAD_ID, socket_dir=root, connector=lambda path, _timeout: connections[path], allow_owned_resume=True,
                    )
                self.assertFalse(any(item["method"] == "thread/resume" for ws in connections.values() for item in ws.sent))

    def test_bound_cold_resume_preserves_settings_and_only_adds_project_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".codex"
            rollout = write_bound_rollout(home)
            ws = BoundWebSocket(rollout)
            config = {"mcp_servers": {"scene_feedback": {"command": "python3"}}}
            with patch.dict(os.environ, {"CODEX_HOME": str(home)}), patch(
                "shared_thread_bridge._private_socket_candidates", return_value=[Path("a" * 64)]
            ):
                bridge = SharedThreadBridge.connect_for_thread(
                    THREAD_ID, allow_bound_resume=True, subscribe=True, thread_config=config,
                    connector=lambda _path, _timeout: ws,
                )
                try:
                    self.assertEqual(bridge.read_thread()["status"]["type"], "idle")
                    resumes = [item["params"] for item in ws.sent if item.get("method") == "thread/resume"]
                    self.assertEqual(resumes, [{"threadId": THREAD_ID}, {"threadId": THREAD_ID, "config": config}])
                    for params in resumes:
                        self.assertFalse({"model", "approvalPolicy", "approvalsReviewer", "sandbox", "permissions"} & params.keys())
                finally:
                    bridge.close()

    def test_bound_failed_cold_verification_cannot_escape_on_loaded_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".codex"
            rollout = write_bound_rollout(home)
            changed = {**BOUND_SETTINGS, "sandbox": {"type": "workspaceWrite"}}
            ws = BoundWebSocket(rollout, resume_settings=changed)
            with patch.dict(os.environ, {"CODEX_HOME": str(home)}), patch(
                "shared_thread_bridge._private_socket_candidates", return_value=[Path("a" * 64)]
            ):
                for status in ("notLoaded", "idle"):
                    self.assertEqual(ws.status, status)
                    with self.assertRaisesRegex(SharedThreadBridgeError, "sandbox changed"):
                        SharedThreadBridge.connect_for_thread(
                            THREAD_ID, allow_bound_resume=True, connector=lambda _path, _timeout: ws,
                        )
                self.assertEqual([item["method"] for item in ws.sent].count("thread/resume"), 2)
                self.assertFalse(any(item["method"] == "turn/start" for item in ws.sent))

    def test_bound_resume_rejects_ambiguous_or_unverified_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".codex"
            rollout = write_bound_rollout(home)
            first = BoundWebSocket(rollout)
            second = BoundWebSocket(rollout)
            with patch.dict(os.environ, {"CODEX_HOME": str(home)}), patch(
                "shared_thread_bridge._private_socket_candidates", return_value=[Path("a" * 64), Path("b" * 64)]
            ):
                with self.assertRaisesRegex(SharedThreadBridgeError, "not loaded"):
                    SharedThreadBridge.connect_for_thread(
                        THREAD_ID, allow_bound_resume=True,
                        connector=lambda path, _timeout: first if path.name.startswith("a") else second,
                    )
            self.assertFalse(any(item["method"] == "thread/resume" for ws in (first, second) for item in ws.sent))
            with patch.dict(os.environ, {"CODEX_HOME": str(home)}), patch(
                "shared_thread_bridge._private_socket_candidates", return_value=[Path("a" * 64)]
            ):
                with self.assertRaisesRegex(SharedThreadBridgeError, "not loaded"):
                    SharedThreadBridge.connect_for_thread(THREAD_ID, connector=lambda _path, _timeout: first)
            self.assertFalse(any(item["method"] == "thread/resume" for item in first.sent))

    def test_bound_resume_rejects_missing_baseline_bad_path_and_native_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".codex"
            rollout = write_bound_rollout(home)
            socket_path = Path("a" * 64)
            with patch.dict(os.environ, {"CODEX_HOME": str(home)}), patch(
                "shared_thread_bridge._private_socket_candidates", return_value=[socket_path]
            ):
                missing = {"model": "gpt-6-astra", "effort": "xhigh"}
                write_bound_rollout(home, settings=missing)
                ws = BoundWebSocket(rollout)
                with self.assertRaisesRegex(SharedThreadBridgeError, "complete settings baseline"):
                    SharedThreadBridge.connect_for_thread(
                        THREAD_ID, allow_bound_resume=True, connector=lambda _path, _timeout: ws,
                    )
                self.assertFalse(any(item["method"] == "thread/resume" for item in ws.sent))
                write_bound_rollout(home)
                for options, message in (
                    ({"wrong_resume_id": True}, "wrong bound task"),
                    ({"resume_status": "notLoaded"}, "did not safely load"),
                    ({"reject_resume": True}, "active writer"),
                    ({"thread_fields": {"parentThreadId": THREAD_ID}}, "persisted user task"),
                    ({"thread_fields": {"ephemeral": True}}, "persisted user task"),
                ):
                    with self.subTest(options=options):
                        ws = BoundWebSocket(rollout, **options)
                        with self.assertRaisesRegex(SharedThreadBridgeError, message):
                            SharedThreadBridge.connect_for_thread(
                                THREAD_ID, allow_bound_resume=True, connector=lambda _path, _timeout: ws,
                            )
                other = Path(directory) / f"rollout-2026-10-05T21-09-51-{THREAD_ID}.jsonl"
                other.write_text(rollout.read_text(encoding="utf-8"), encoding="utf-8")
                ws = BoundWebSocket(other)
                with self.assertRaisesRegex(SharedThreadBridgeError, "outside this user's sessions"):
                    SharedThreadBridge.connect_for_thread(
                        THREAD_ID, allow_bound_resume=True, connector=lambda _path, _timeout: ws,
                    )
                self.assertFalse(any(item["method"] == "thread/resume" for item in ws.sent))
                real_rollout = rollout.with_name("saved-rollout.jsonl")
                rollout.rename(real_rollout)
                rollout.symlink_to(real_rollout)
                ws = BoundWebSocket(rollout)
                with self.assertRaisesRegex(SharedThreadBridgeError, "cannot safely read"):
                    SharedThreadBridge.connect_for_thread(
                        THREAD_ID, allow_bound_resume=True, connector=lambda _path, _timeout: ws,
                    )
                self.assertFalse(any(item["method"] == "thread/resume" for item in ws.sent))

    def test_loaded_zero_turn_bound_task_keeps_existing_subscription_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".codex"
            rollout = write_bound_rollout(home)
            rollout.write_text(json.dumps({"type": "session_meta", "payload": {"id": THREAD_ID}}) + "\n", encoding="utf-8")
            ws = BoundWebSocket(rollout, status="idle")
            with patch.dict(os.environ, {"CODEX_HOME": str(home)}), patch(
                "shared_thread_bridge._private_socket_candidates", return_value=[Path("a" * 64)]
            ):
                bridge = SharedThreadBridge.connect_for_thread(
                    THREAD_ID, allow_bound_resume=True, subscribe=True,
                    thread_config={"mcp_servers": {"scene_feedback": {"command": "python3"}}},
                    connector=lambda _path, _timeout: ws,
                )
                bridge.close()
                self.assertEqual([item["method"] for item in ws.sent].count("thread/resume"), 1)
                ws.status = "notLoaded"
                with self.assertRaisesRegex(SharedThreadBridgeError, "no recent turn settings"):
                    SharedThreadBridge.connect_for_thread(
                        THREAD_ID, allow_bound_resume=True, connector=lambda _path, _timeout: ws,
                    )
                rollout.write_text(json.dumps({"type": "session_meta", "payload": {"id": THREAD_ID}})
                                   + '\n{"type":"turn_context","payload":\n', encoding="utf-8")
                ws = BoundWebSocket(rollout, status="idle")
                with self.assertRaisesRegex(SharedThreadBridgeError, "malformed settings"):
                    SharedThreadBridge.connect_for_thread(
                        THREAD_ID, allow_bound_resume=True, connector=lambda _path, _timeout: ws,
                    )

    def test_loaded_empty_bound_task_can_have_no_native_path_or_rollout_yet(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".codex"
            rollout = write_bound_rollout(home)
            with patch.dict(os.environ, {"CODEX_HOME": str(home)}), patch(
                "shared_thread_bridge._private_socket_candidates", return_value=[Path("a" * 64)]
            ):
                for fields in ({"path": None}, {}):
                    with self.subTest(fields=fields):
                        if not fields:
                            rollout.unlink()
                        ws = BoundWebSocket(rollout, status="idle", thread_fields=fields)
                        bridge = SharedThreadBridge.connect_for_thread(
                            THREAD_ID, allow_bound_resume=True, subscribe=True,
                            thread_config={"mcp_servers": {"scene_feedback": {"command": "python3"}}},
                            connector=lambda _path, _timeout: ws,
                        )
                        bridge.close()
                        resumes = [item["params"] for item in ws.sent if item.get("method") == "thread/resume"]
                        self.assertEqual(len(resumes), 1)
                        self.assertEqual(set(resumes[0]), {"threadId", "config"})
                        self.assertTrue(any(item.get("method") == "thread/read" and item["params"]["includeTurns"]
                                            for item in ws.sent))
                        cold = BoundWebSocket(rollout, status="notLoaded", thread_fields=fields)
                        with self.assertRaises(ExternalSettingsUnavailable):
                            SharedThreadBridge.connect_for_thread(
                                THREAD_ID, allow_bound_resume=True, connector=lambda _path, _timeout: cold,
                            )
                        self.assertFalse(any(item["method"] == "thread/resume" for item in cold.sent))

    def test_loaded_missing_baseline_requires_proven_empty_user_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".codex"
            rollout = write_bound_rollout(home)
            rollout.unlink()
            with patch.dict(os.environ, {"CODEX_HOME": str(home)}), patch(
                "shared_thread_bridge._private_socket_candidates", return_value=[Path("a" * 64)]
            ):
                for fields in (
                    {"path": None, "turns": [{"id": "turn-1", "status": "completed"}]},
                    {"source": {"subAgent": {"threadSpawn": {}}}},
                    {"parentThreadId": THREAD_ID},
                    {"ephemeral": True},
                ):
                    with self.subTest(fields=fields):
                        ws = BoundWebSocket(rollout, status="idle", thread_fields=fields)
                        with self.assertRaises(SharedThreadBridgeError):
                            SharedThreadBridge.connect_for_thread(
                                THREAD_ID, allow_bound_resume=True, connector=lambda _path, _timeout: ws,
                            )
                        self.assertFalse(any(item["method"] == "thread/resume" for item in ws.sent))
                rollout.write_text(json.dumps({"type": "session_meta", "payload": {"id": "wrong-task"}}) + "\n", encoding="utf-8")
                ws = BoundWebSocket(rollout, status="idle")
                with self.assertRaisesRegex(SharedThreadBridgeError, "different task"):
                    SharedThreadBridge.connect_for_thread(
                        THREAD_ID, allow_bound_resume=True, connector=lambda _path, _timeout: ws,
                    )
                self.assertFalse(any(item["method"] == "thread/resume" for item in ws.sent))

    def test_model_catalog_and_task_creation_use_same_connection(self) -> None:
        created = "01a0de73-9763-7432-8ca4-5892c0904234"

        class CreationWebSocket(FakeWebSocket):
            created = False

            def send(self, data: str) -> None:
                message = json.loads(data)
                if message.get("method") == "thread/read" and self.created:
                    self.sent.append(message)
                    self.responses.append({"id": message["id"], "result": {"thread": {"id": created, "status": {"type": "idle"}}}})
                    return
                super().send(data)
                if message.get("method") == "model/list":
                    self.responses.append({"id": message["id"], "result": {"data": [{"model": "gpt-6-astra"}], "nextCursor": None}})
                elif message.get("method") == "thread/start":
                    self.created = True
                    self.responses.append({"id": message["id"], "result": {"thread": {"id": created, "ephemeral": False}}})
                elif message.get("method") == "thread/name/set":
                    self.responses.append({"id": message["id"], "result": {}})

        ws = CreationWebSocket()
        bridge = SharedThreadBridge(ws, THREAD_ID)
        self.assertEqual(bridge.list_models(), [{"model": "gpt-6-astra"}])
        config = {"mcp_servers": {"scene_feedback": {"env": {"SCENE_FEEDBACK_DATA_DIR": "/tmp/project-data"}}}}
        self.assertEqual(bridge.create_thread("gpt-6-astra", Path("/tmp/project"), reasoning_effort="ultra", title="Astra feedback", config=config), created)
        start = next(item for item in ws.sent if item.get("method") == "thread/start")
        self.assertEqual(bridge.thread_id, created)
        self.assertEqual(bridge.read_thread()["id"], created)
        self.assertEqual(start["params"], {
            "model": "gpt-6-astra", "cwd": "/tmp/project", "ephemeral": False,
            "serviceName": "scene_feedback_workspace", "config": {**config, "model_reasoning_effort": "ultra"},
            "approvalPolicy": "on-request", "permissions": ":workspace", "approvalsReviewer": "user",
        })
        self.assertNotIn("model_reasoning_effort", config)
        self.assertTrue(any(item.get("method") == "thread/name/set" for item in ws.sent))

    def test_new_task_permission_modes_are_sent_explicitly(self) -> None:
        created = "01a0de73-9763-7432-8ca4-5892c0904234"

        class CreationWebSocket(FakeWebSocket):
            def send(self, data: str) -> None:
                super().send(data)
                message = json.loads(data)
                if message.get("method") == "thread/start":
                    self.responses.append({"id": message["id"], "result": {"thread": {"id": created, "ephemeral": False}}})

        for mode, policy, profile in (
            ("full_access", "never", ":danger-full-access"),
            ("workspace_write", "on-request", ":workspace"),
            ("read_only", "on-request", ":read-only"),
        ):
            with self.subTest(mode=mode):
                ws = CreationWebSocket()
                bridge = SharedThreadBridge(ws, THREAD_ID)
                self.assertEqual(bridge.create_thread("gpt-6-astra", Path("/tmp/project"), permission_mode=mode), created)
                params = next(item["params"] for item in ws.sent if item.get("method") == "thread/start")
                self.assertEqual((params["approvalPolicy"], params["permissions"], params["approvalsReviewer"]), (policy, profile, "user"))
                self.assertNotIn("sandbox", params)

        ws = CreationWebSocket()
        bridge = SharedThreadBridge(ws, THREAD_ID)
        with self.assertRaisesRegex(ValueError, "permission_mode"):
            bridge.create_thread("gpt-6-astra", Path("/tmp/project"), permission_mode="invalid")
        self.assertFalse(ws.sent)

    def test_child_request_verification_uses_separate_connection_and_parent_chain(self) -> None:
        session_id = "01a0e1e0-1e1f-7010-8324-0906fa533d7a"
        child = "01a0e1e3-a8d6-7a10-a33b-ac3bc245fd24"
        grandchild = "01a0e1e3-d089-7d60-aff6-12210b9d8e54"
        sibling = "01a0e1ee-718e-70f2-b5b4-61d9634e89e9"
        unrelated = "01a0e1ee-8d79-7151-b3dc-08556654a41d"
        records = {
            THREAD_ID: {"id": THREAD_ID, "sessionId": session_id, "parentThreadId": session_id},
            child: {"id": child, "sessionId": session_id, "parentThreadId": THREAD_ID},
            grandchild: {"id": grandchild, "sessionId": session_id, "parentThreadId": child},
            sibling: {"id": sibling, "sessionId": session_id, "parentThreadId": session_id},
            session_id: {"id": session_id, "sessionId": session_id, "parentThreadId": None},
            unrelated: {"id": unrelated, "sessionId": unrelated, "parentThreadId": None},
        }

        class MetadataWebSocket(FakeWebSocket):
            def send(self, data: str) -> None:
                message = json.loads(data)
                if message.get("method") == "thread/read":
                    self.sent.append(message)
                    self.responses.append({"id": message["id"], "result": {"thread": records[message["params"]["threadId"]]}})
                    return
                super().send(data)

        main_ws = FakeWebSocket()
        bridge = SharedThreadBridge(main_ws, THREAD_ID, socket_path=Path("/tmp/fake-codex-socket"))
        readers = []

        def connector(_path, _timeout):
            reader = MetadataWebSocket()
            readers.append(reader)
            return reader

        self.assertTrue(bridge.is_descendant_thread(child, connector=connector))
        self.assertTrue(bridge.is_descendant_thread(grandchild, connector=connector))
        self.assertFalse(bridge.is_descendant_thread(sibling, connector=connector))
        self.assertFalse(bridge.is_descendant_thread(unrelated, connector=connector))
        self.assertEqual(main_ws.sent, [])
        self.assertTrue(all(reader.closed for reader in readers))
        self.assertEqual(len(readers), 4)

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
                    THREAD_ID, socket_dir=root, connector=lambda path, _timeout: connections[path], subscribe=True,
                    thread_config={"mcp_servers": {"scene_feedback": {"env": {"SCENE_FEEDBACK_DATA_DIR": "/tmp/selected-data"}}}},
                )
                try:
                    self.assertEqual(bridge.socket_path, loaded_path)
                    loaded_subscriptions = [item for item in connections[loaded_path].sent if item.get("method") == "thread/resume"]
                    other_subscriptions = [item for item in connections[other_path].sent if item.get("method") == "thread/resume"]
                    self.assertEqual(len(loaded_subscriptions), 1)
                    self.assertEqual(loaded_subscriptions[0]["params"]["threadId"], THREAD_ID)
                    self.assertEqual(loaded_subscriptions[0]["params"]["config"]["mcp_servers"]["scene_feedback"]["env"]["SCENE_FEEDBACK_DATA_DIR"], "/tmp/selected-data")
                    self.assertNotIn("approvalPolicy", loaded_subscriptions[0]["params"])
                    self.assertNotIn("sandbox", loaded_subscriptions[0]["params"])
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

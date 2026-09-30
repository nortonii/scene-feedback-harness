"""Connect a visual-feedback gateway to a task already loaded by Codex Desktop.

This module speaks App Server JSON-RPC over the desktop daemon's private Unix
WebSocket.  Existing Desktop tasks are only used while loaded; workbench-owned
tasks may be resumed on the same daemon after they unload.  A caller must keep
the returned bridge connected while its turn runs and handle server requests
(approvals, elicitation, user input) rather than approving them automatically.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import stat
import struct
import tempfile
from collections import deque
from pathlib import Path
from typing import Any, Callable, Iterable


_SOCKET_NAME = re.compile(r"[0-9a-f]{64}\Z")
_THREAD_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_CREATE_PERMISSION_POLICIES = {
    "full_access": ("never", "danger-full-access"),
    "workspace_write": ("on-request", "workspace-write"),
    "read_only": ("on-request", "read-only"),
}
_TURN_SANDBOX_TYPES = {
    "full_access": "dangerFullAccess",
    "workspace_write": "workspaceWrite",
    "read_only": "readOnly",
}
_PERMISSION_PROFILE_IDS = {
    "full_access": ":danger-full-access",
    "workspace_write": ":workspace",
    "read_only": ":read-only",
}


def _permission_overrides(permission_mode: str | None, *, for_turn: bool = False) -> dict[str, Any]:
    """Translate an explicitly saved mode without changing daemon defaults."""
    if permission_mode is None:
        return {}
    if not isinstance(permission_mode, str) or permission_mode not in _CREATE_PERMISSION_POLICIES:
        raise ValueError("permission_mode must be full_access, workspace_write, or read_only")
    approval, sandbox = _CREATE_PERMISSION_POLICIES[permission_mode]
    result: dict[str, Any] = {"approvalPolicy": approval, "approvalsReviewer": "user"}
    if for_turn:
        result["sandboxPolicy"] = {"type": _TURN_SANDBOX_TYPES[permission_mode]}
    else:
        result["sandbox"] = sandbox
    return result


class SharedThreadBridgeError(RuntimeError):
    """The shared daemon could not safely serve this task."""


class SharedThreadNotIdle(SharedThreadBridgeError):
    """The target task has an active turn; starting another is unsafe."""


class SharedThreadTimeout(SharedThreadBridgeError):
    """No daemon message arrived before the socket timeout; keep listening."""


class UncertainTurnDelivery(SharedThreadBridgeError):
    """turn/start may have reached Codex; inspect history before any retry."""


class SharedThreadRPCRejected(SharedThreadBridgeError):
    """App Server answered a JSON-RPC request with an explicit error."""

    def __init__(self, method: str, error: Any) -> None:
        self.method = method
        self.error = error
        super().__init__(f"{method} failed: {error}")


class SharedThreadPermissionMismatch(SharedThreadBridgeError):
    """The daemon did not apply the explicitly saved task permission mode."""

    def __init__(self, message: str, *, created_thread_id: str | None = None) -> None:
        self.created_thread_id = created_thread_id
        super().__init__(message)


class OwnedEmptyThreadMissing(SharedThreadBridgeError):
    """A workbench-created zero-turn task has no persisted rollout to resume."""


def _private_socket_candidates(socket_dir: Path | None = None) -> list[Path]:
    """Find sockets in the current user's private Codex daemon directory."""
    if not hasattr(os, "getuid"):
        raise SharedThreadBridgeError("shared Codex daemon discovery requires Unix")
    uid = os.getuid()
    directory = socket_dir or Path(tempfile.gettempdir()) / f"codex-daemon-{uid}"
    try:
        mode = directory.lstat()
    except FileNotFoundError as exc:
        raise SharedThreadBridgeError("Codex Desktop daemon directory was not found") from exc
    if not stat.S_ISDIR(mode.st_mode) or mode.st_uid != uid or stat.S_IMODE(mode.st_mode) & 0o077:
        raise SharedThreadBridgeError("Codex daemon directory must be owned by this user and private")
    result = []
    for entry in directory.iterdir():
        if not _SOCKET_NAME.fullmatch(entry.name):
            continue
        info = entry.lstat()
        if stat.S_ISSOCK(info.st_mode) and info.st_uid == uid and not stat.S_IMODE(info.st_mode) & 0o077:
            result.append(entry)
    return sorted(result)


def _checked_peer(unix_socket: socket.socket) -> None:
    """Require a same-user Codex app-server behind the socket."""
    if not hasattr(socket, "SO_PEERCRED"):
        raise SharedThreadBridgeError("SO_PEERCRED is required for the shared Codex daemon")
    pid, uid, _gid = struct.unpack("3i", unix_socket.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if uid != os.getuid():
        raise SharedThreadBridgeError("Codex daemon socket belongs to another user")
    try:
        args = [part.decode("utf-8", "replace") for part in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if part]
    except OSError as exc:
        raise SharedThreadBridgeError("cannot verify Codex daemon process") from exc
    if not args or Path(args[0]).name != "codex" or "app-server" not in args or "unix://" not in args:
        raise SharedThreadBridgeError("socket peer is not a Codex Unix App Server")


def _connect(path: Path, timeout: float) -> Any:
    """Perform the Unix socket WebSocket Upgrade using websocket-client."""
    try:
        import websocket  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SharedThreadBridgeError("install websocket-client to use the shared Codex daemon") from exc
    raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        raw.settimeout(timeout)
        raw.connect(str(path))
        _checked_peer(raw)
        return websocket.create_connection("ws://localhost/", socket=raw, timeout=timeout, suppress_origin=True)
    except Exception:
        raw.close()
        raise


class SharedThreadBridge:
    """One connection to a task on the current Desktop App Server daemon.

    Use :meth:`connect_for_thread` for an existing task and
    :meth:`connect_to_desktop` when creating a new one. Keep this connection
    open until a started turn finishes, and process server-initiated requests.
    """

    def __init__(self, websocket_connection: Any, thread_id: str, *, timeout: float = 10.0, socket_path: Path | None = None) -> None:
        if not isinstance(thread_id, str) or not _THREAD_ID.fullmatch(thread_id):
            raise ValueError("thread_id must be a Codex task UUID")
        self.ws = websocket_connection
        self.thread_id = thread_id
        self.socket_path = socket_path
        self.timeout = timeout
        self._next_id = 1
        self._pending: deque[dict[str, Any]] = deque()
        self._closed = False
        self._active_turn_id: str | None = None
        self._permission_cwd: str | None = None
        self._permission_catalogs: dict[str | None, dict[str, bool] | None] = {}

    def _saved_permission_overrides(self, mode: str | None, *, cwd: str | None = None, for_turn: bool = False) -> dict[str, Any]:
        legacy = _permission_overrides(mode, for_turn=for_turn)
        if mode is None:
            return legacy
        directory = cwd if cwd is not None else self._permission_cwd
        if directory not in self._permission_catalogs:
            profiles: dict[str, bool] = {}
            cursor: str | None = None
            for _ in range(10):
                params: dict[str, Any] = {"limit": 100}
                if directory is not None:
                    params["cwd"] = directory
                if cursor is not None:
                    params["cursor"] = cursor
                try:
                    response = self._rpc("permissionProfile/list", params)
                except SharedThreadRPCRejected as exc:
                    if isinstance(exc.error, dict) and exc.error.get("code") == -32601 and cursor is None:
                        self._permission_catalogs[directory] = None
                        break
                    raise
                page = response.get("data")
                if not isinstance(page, list):
                    raise SharedThreadBridgeError("permissionProfile/list returned invalid profiles")
                for item in page:
                    if (not isinstance(item, dict) or not isinstance(item.get("id"), str)
                            or not isinstance(item.get("allowed"), bool) or item["id"] in profiles):
                        raise SharedThreadBridgeError("permissionProfile/list returned invalid profiles")
                    profiles[item["id"]] = item["allowed"]
                next_cursor = response.get("nextCursor")
                if next_cursor is None:
                    self._permission_catalogs[directory] = profiles
                    break
                if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
                    raise SharedThreadBridgeError("permissionProfile/list returned an invalid cursor")
                cursor = next_cursor
            else:
                raise SharedThreadBridgeError("too many permission profiles to list safely")
        catalog = self._permission_catalogs[directory]
        if catalog is None:
            # Older daemons explicitly report method-not-found. Other errors,
            # missing profiles and requirements denials must never fall back.
            return legacy
        profile_id = _PERMISSION_PROFILE_IDS[mode]
        if catalog.get(profile_id) is not True:
            raise SharedThreadBridgeError(f"saved permission profile {profile_id} is unavailable or disallowed")
        return {"approvalPolicy": legacy["approvalPolicy"], "approvalsReviewer": "user", "permissions": profile_id}

    @staticmethod
    def _verify_saved_permissions(result: dict[str, Any], mode: str | None, *, created_thread_id: str | None = None) -> None:
        if mode is None:
            return
        expected_approval, _sandbox = _CREATE_PERMISSION_POLICIES[mode]
        profile = result.get("activePermissionProfile")
        sandbox_key = "sandbox" if "sandbox" in result else "sandboxPolicy"
        actual_sandbox = result.get(sandbox_key)
        mismatch = False
        if profile is not None:
            mismatch = not isinstance(profile, dict) or profile.get("id") != _PERMISSION_PROFILE_IDS[mode]
        if sandbox_key in result:
            mismatch = mismatch or not isinstance(actual_sandbox, dict) or actual_sandbox.get("type") != _TURN_SANDBOX_TYPES[mode]
        if "approvalPolicy" in result:
            mismatch = mismatch or result["approvalPolicy"] != expected_approval
        if "approvalsReviewer" in result:
            mismatch = mismatch or result["approvalsReviewer"] != "user"
        if mismatch:
            raise SharedThreadPermissionMismatch(f"Codex did not apply saved permission mode {mode}; task was not bound", created_thread_id=created_thread_id)

    def sync_saved_permissions(self, mode: str, *, current_settings: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Apply a saved mode to an idle owned task without starting a turn.

        Callers enforce workbench ownership and subscribe the connection.
        Monitor connections never call
        this method. A daemon without settings/update keeps the explicit
        override for the next legitimate turn.
        """
        thread = self.read_thread()
        if thread.get("status", {}).get("type") != "idle":
            return None
        if thread.get("id") != self.thread_id:
            raise SharedThreadBridgeError("daemon returned the wrong task")
        overrides = self._saved_permission_overrides(mode, cwd=thread.get("cwd"), for_turn=True)
        if current_settings is None:
            current_settings = self._rpc("thread/resume", {"threadId": self.thread_id})
        current_thread = current_settings.get("thread")
        if not isinstance(current_thread, dict) or current_thread.get("id") != self.thread_id:
            raise SharedThreadBridgeError("thread/resume returned the wrong task settings")

        def verify_metadata(settings: dict[str, Any]) -> None:
            if not {"sandbox", "approvalPolicy", "approvalsReviewer"}.issubset(settings):
                raise SharedThreadBridgeError("thread/resume returned incomplete permission settings")
            if "permissions" in overrides and not isinstance(settings.get("activePermissionProfile"), dict):
                raise SharedThreadBridgeError("thread/resume did not confirm the named permission profile")
            self._verify_saved_permissions(settings, mode)

        try:
            verify_metadata(current_settings)
        except SharedThreadBridgeError:
            pass  # Incomplete or different current settings require an update.
        else:
            return current_settings
        try:
            self._rpc("thread/settings/update", {"threadId": self.thread_id, **overrides})
        except SharedThreadRPCRejected as exc:
            if isinstance(exc.error, dict) and exc.error.get("code") == -32601:
                return None
            raise
        # No-op updates do not emit a notification. Reading the subscribed
        # task's current settings verifies both no-op and changed settings.
        settings = self._rpc("thread/resume", {"threadId": self.thread_id})
        resumed = settings.get("thread")
        if not isinstance(resumed, dict) or resumed.get("id") != self.thread_id:
            raise SharedThreadBridgeError("thread/resume returned the wrong task after settings update")
        verify_metadata(settings)
        return settings

    @classmethod
    def connect_for_thread(
        cls,
        thread_id: str,
        *,
        socket_dir: Path | None = None,
        timeout: float = 10.0,
        require_idle: bool = True,
        subscribe: bool = False,
        allow_owned_resume: bool = False,
        thread_config: dict[str, Any] | None = None,
        permission_mode: str | None = None,
        sync_owned_permissions: bool = False,
        connector: Callable[[Path, float], Any] = _connect,
    ) -> "SharedThreadBridge":
        """Select the single daemon where ``thread_id`` is already loaded.

        A fresh daemon can read the rollout but reports ``notLoaded``; it is
        never selected.  A busy loaded task is rejected by default; set
        ``require_idle=False`` only to observe or bind it without starting a
        turn.  :meth:`start_turn` always verifies idle status again.
        """
        if not isinstance(thread_id, str) or not _THREAD_ID.fullmatch(thread_id):
            raise ValueError("thread_id must be a Codex task UUID")
        permission_overrides = _permission_overrides(permission_mode)
        if permission_overrides and not allow_owned_resume:
            raise ValueError("permission overrides require a workbench-owned task")
        loaded: list[tuple[SharedThreadBridge, dict[str, Any]]] = []
        unloaded: list[SharedThreadBridge] = []
        errors: list[str] = []
        missing_empty_rollout = False
        already_subscribed = False
        current_permission_settings = None
        candidates = _private_socket_candidates(socket_dir)
        for path in candidates:
            bridge: SharedThreadBridge | None = None
            try:
                ws = connector(path, timeout)
                bridge = cls(ws, thread_id, timeout=timeout, socket_path=path)
                bridge._initialize()
                thread = bridge.read_thread()
                if isinstance(thread.get("cwd"), str):
                    bridge._permission_cwd = thread["cwd"]
                if thread.get("id") != thread_id:
                    raise SharedThreadBridgeError("daemon returned the wrong task")
                if thread.get("status", {}).get("type") != "notLoaded":
                    loaded.append((bridge, thread))
                else:
                    unloaded.append(bridge)
            except Exception as exc:
                if bridge is not None:
                    bridge.close()
                errors.append(f"{path.name}: {exc}")
                if isinstance(exc, SharedThreadRPCRejected) and "no rollout found" in str(exc).lower():
                    missing_empty_rollout = True
        if not loaded and allow_owned_resume and len(unloaded) == 1 and len(candidates) == 1:
            bridge = unloaded.pop()
            try:
                params = {"threadId": thread_id, **bridge._saved_permission_overrides(permission_mode)}
                if thread_config is not None:
                    params["config"] = thread_config
                result = bridge._rpc("thread/resume", params)
                current_permission_settings = result
                bridge._verify_saved_permissions(result, permission_mode)
                resumed = result.get("thread")
                if not isinstance(resumed, dict) or resumed.get("id") != thread_id:
                    raise SharedThreadBridgeError("thread/resume returned the wrong owned task")
                thread = bridge.read_thread()
                loaded.append((bridge, thread))
                subscribe = False  # thread/resume already subscribed this connection
                already_subscribed = True
            except SharedThreadRPCRejected as exc:
                bridge.close()
                if "no rollout found" in str(exc).lower():
                    raise OwnedEmptyThreadMissing(f"workbench-owned task {thread_id} has no persisted rollout") from exc
                raise
            except Exception:
                bridge.close()
                raise
        for bridge in unloaded:
            bridge.close()
        if len(loaded) != 1:
            for bridge, _thread in loaded:
                bridge.close()
            if allow_owned_resume and len(candidates) == 1 and missing_empty_rollout:
                raise OwnedEmptyThreadMissing(f"workbench-owned task {thread_id} has no persisted rollout")
            detail = "multiple daemons have this task loaded" if loaded else "desktop task is not loaded by a shared daemon"
            if errors:
                detail += f" ({'; '.join(errors)})"
            raise SharedThreadBridgeError(detail)
        bridge, thread = loaded[0]
        if require_idle and thread.get("status", {}).get("type") != "idle":
            bridge.close()
            raise SharedThreadNotIdle(f"Codex task status is {thread.get('status')!r}")
        if (sync_owned_permissions and permission_mode is not None
                and thread.get("status", {}).get("type") == "idle" and not already_subscribed):
            subscribe = True
        if subscribe:
            try:
                resume_mode = permission_mode if thread.get("status", {}).get("type") == "idle" else None
                params = {"threadId": thread_id, **bridge._saved_permission_overrides(resume_mode)}
                if thread_config is not None:
                    params["config"] = thread_config
                result = bridge._rpc("thread/resume", params)
                current_permission_settings = result
                # Loaded daemon sessions can return their existing profile
                # even when resume carries an override. The saved mode must
                # also be sent with the next legitimate turn/start.
                resumed = result.get("thread")
                if not isinstance(resumed, dict) or resumed.get("id") != thread_id:
                    raise SharedThreadBridgeError("thread/resume returned the wrong task")
            except Exception:
                bridge.close()
                raise
        if permission_mode is not None and (subscribe or sync_owned_permissions):
            try:
                bridge.sync_saved_permissions(permission_mode, current_settings=current_permission_settings)
            except Exception:
                bridge.close()
                raise
        return bridge

    @classmethod
    def connect_to_desktop(
        cls,
        current_thread_id: str,
        *,
        socket_dir: Path | None = None,
        timeout: float = 10.0,
        connector: Callable[[Path, float], Any] = _connect,
    ) -> "SharedThreadBridge":
        """Open the current task's daemon, or the sole Desktop daemon if unloaded."""
        if not isinstance(current_thread_id, str) or not _THREAD_ID.fullmatch(current_thread_id):
            raise ValueError("current_thread_id must be a Codex task UUID")
        paths = _private_socket_candidates(socket_dir)
        if len(paths) != 1:
            records = cls.discover_loaded_threads(socket_dir=socket_dir, timeout=timeout, connector=connector)
            matches = {path for path, thread in records if thread.get("id") == current_thread_id}
            if len(matches) != 1:
                raise SharedThreadBridgeError("cannot identify a single Codex Desktop daemon for this workspace")
            path = matches.pop()
        else:
            path = paths[0]
        bridge: SharedThreadBridge | None = None
        try:
            bridge = cls(connector(path, timeout), current_thread_id, timeout=timeout, socket_path=path)
            bridge._initialize()
            return bridge
        except Exception:
            if bridge is not None:
                bridge.close()
            raise

    def list_models(self) -> list[dict[str, Any]]:
        """Return all visible model catalog entries from this Desktop daemon."""
        entries: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(10):
            params: dict[str, Any] = {"limit": 100, "includeHidden": False}
            if cursor is not None:
                params["cursor"] = cursor
            response = self._rpc("model/list", params)
            page = response.get("data")
            if not isinstance(page, list) or any(not isinstance(item, dict) for item in page):
                raise SharedThreadBridgeError("model/list returned invalid models")
            entries.extend(page)
            next_cursor = response.get("nextCursor")
            if next_cursor is None:
                return entries
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
                raise SharedThreadBridgeError("model/list returned an invalid cursor")
            cursor = next_cursor
        raise SharedThreadBridgeError("too many Codex models to list safely")

    def create_thread(
        self,
        model: str,
        cwd: Path,
        *,
        reasoning_effort: str | None = None,
        title: str | None = None,
        permission_mode: str = "workspace_write",
        config: dict[str, Any] | None = None,
    ) -> str:
        """Create one persistent blank task on this exact Desktop daemon."""
        if permission_mode is None:
            raise ValueError("invalid permission_mode")
        overrides = self._saved_permission_overrides(permission_mode, cwd=str(cwd))
        params: dict[str, Any] = {
            "model": model,
            "cwd": str(cwd),
            "ephemeral": False,
            "serviceName": "scene_feedback_workspace",
            **overrides,
        }
        if config is not None:
            params["config"] = dict(config)
        if reasoning_effort is not None:
            params.setdefault("config", {})["model_reasoning_effort"] = reasoning_effort
        result = self._rpc("thread/start", params)
        thread = result.get("thread")
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(thread_id, str) or not _THREAD_ID.fullmatch(thread_id) or thread.get("ephemeral") is True:
            raise SharedThreadBridgeError("thread/start returned no persistent task ID")
        self.thread_id = thread_id
        self._permission_cwd = str(cwd)
        self._verify_saved_permissions(result, permission_mode, created_thread_id=thread_id)
        if title:
            try:
                self._rpc("thread/name/set", {"threadId": thread_id, "name": title})
            except SharedThreadBridgeError as exc:
                # The task exists even if its optional title fails.  The
                # caller must still record and bind it.
                logging.warning("Could not name new Codex task %s: %s", thread_id, exc)
        self.thread_id = thread_id
        return thread_id

    @classmethod
    def discover_loaded_threads(
        cls,
        *,
        socket_dir: Path | None = None,
        timeout: float = 10.0,
        connector: Callable[[Path, float], Any] = _connect,
    ) -> list[tuple[Path, dict[str, Any]]]:
        """Inspect loaded tasks on private same-user Desktop daemon sockets.

        This does not depend on the workspace's previous task still being
        loaded, so a user can rebind after Desktop unloads an idle task.
        """
        records: list[tuple[Path, dict[str, Any]]] = []
        connected = False
        errors: list[str] = []
        for path in _private_socket_candidates(socket_dir):
            bridge: SharedThreadBridge | None = None
            try:
                bridge = cls(connector(path, timeout), "00000000-0000-0000-0000-000000000000", timeout=timeout, socket_path=path)
                bridge._initialize()
                loaded_ids = bridge.loaded_thread_ids()
                connected = True
                for thread_id in loaded_ids:
                    try:
                        thread = bridge.read_loaded_thread(thread_id)
                    except Exception as exc:
                        errors.append(f"{path.name}: cannot read {thread_id}: {exc}")
                        continue
                    if thread.get("status", {}).get("type") != "notLoaded":
                        records.append((path, thread))
            except Exception as exc:
                errors.append(f"{path.name}: {exc}")
            finally:
                if bridge is not None:
                    bridge.close()
        if not connected:
            raise SharedThreadBridgeError("no private Codex Desktop daemon is reachable" + (f" ({'; '.join(errors)})" if errors else ""))
        return records

    def _send(self, message: dict[str, Any]) -> None:
        if self._closed:
            raise SharedThreadBridgeError("connection is closed")
        try:
            self.ws.send(json.dumps(message, ensure_ascii=False, separators=(",", ":")))
        except Exception as exc:
            raise SharedThreadBridgeError(f"Codex daemon send failed: {exc}") from exc

    def _receive(self) -> dict[str, Any]:
        if self._closed:
            raise SharedThreadBridgeError("connection is closed")
        try:
            message = json.loads(self.ws.recv())
        except Exception as exc:
            if isinstance(exc, TimeoutError) or type(exc).__name__ == "WebSocketTimeoutException":
                raise SharedThreadTimeout("waiting for Codex daemon message timed out") from exc
            raise SharedThreadBridgeError(f"Codex daemon receive failed: {exc}") from exc
        if not isinstance(message, dict):
            raise SharedThreadBridgeError("invalid App Server message")
        return message

    def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._send({"id": request_id, "method": method, "params": params})
        while True:
            message = self._receive()
            if message.get("id") == request_id:
                if "error" in message:
                    raise SharedThreadRPCRejected(method, message["error"])
                result = message.get("result")
                if not isinstance(result, dict):
                    raise SharedThreadBridgeError(f"{method} returned an invalid response")
                return result
            self._pending.append(message)

    def _initialize(self) -> None:
        self._rpc("initialize", {"clientInfo": {"name": "scene_feedback_shared_bridge", "title": "Scene Feedback Shared Bridge", "version": "0.1.0"}, "capabilities": {"experimentalApi": True}})
        self._send({"method": "initialized", "params": {}})

    def read_thread(self, *, include_turns: bool = False) -> dict[str, Any]:
        """Read task state; optionally include its persisted turn outcomes."""
        thread = self._rpc("thread/read", {"threadId": self.thread_id, "includeTurns": include_turns}).get("thread")
        if not isinstance(thread, dict) or thread.get("id") != self.thread_id:
            raise SharedThreadBridgeError("thread/read returned the wrong task")
        return thread

    def loaded_thread_ids(self) -> list[str]:
        """List task IDs loaded by this exact desktop daemon connection."""
        found: list[str] = []
        seen: set[str] = set()
        cursor: str | None = None
        for _ in range(10):
            params: dict[str, Any] = {"limit": 100}
            if cursor is not None:
                params["cursor"] = cursor
            response = self._rpc("thread/loaded/list", params)
            page = response.get("data")
            if not isinstance(page, list) or any(not isinstance(item, str) or not _THREAD_ID.fullmatch(item) for item in page):
                raise SharedThreadBridgeError("thread/loaded/list returned invalid task IDs")
            for item in page:
                if item not in seen:
                    found.append(item)
                    seen.add(item)
            next_cursor = response.get("nextCursor")
            if next_cursor is None:
                return found
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
                raise SharedThreadBridgeError("thread/loaded/list returned an invalid cursor")
            cursor = next_cursor
        raise SharedThreadBridgeError("too many loaded Codex tasks to list safely")

    def read_loaded_thread(self, thread_id: str) -> dict[str, Any]:
        """Read another task through the same daemon; caller checks membership."""
        if not isinstance(thread_id, str) or not _THREAD_ID.fullmatch(thread_id):
            raise ValueError("thread_id must be a Codex task UUID")
        thread = self._rpc("thread/read", {"threadId": thread_id, "includeTurns": False}).get("thread")
        if not isinstance(thread, dict) or thread.get("id") != thread_id:
            raise SharedThreadBridgeError("thread/read returned the wrong task")
        return thread

    def is_descendant_thread(
        self,
        thread_id: str,
        *,
        timeout: float = 3.0,
        connector: Callable[[Path, float], Any] = _connect,
    ) -> bool:
        """Verify a child request belongs to this task without reading this connection.

        A request may originate in a spawned subagent and carry that child's
        threadId.  Read the parent chain on a fresh connection to the same
        daemon; never call _rpc on the subscribed event connection while its
        reader is running.
        """
        if not isinstance(thread_id, str) or not _THREAD_ID.fullmatch(thread_id):
            return False
        if thread_id == self.thread_id:
            return True
        if self.socket_path is None:
            raise SharedThreadBridgeError("cannot verify child task without the original daemon socket")
        reader: SharedThreadBridge | None = None
        try:
            reader = type(self)(connector(self.socket_path, timeout), self.thread_id, timeout=timeout, socket_path=self.socket_path)
            reader._initialize()
            root = reader.read_thread()
            session_id = root.get("sessionId")
            if not isinstance(session_id, str) or not session_id:
                return False
            current_id = thread_id
            visited = {self.thread_id}
            for _ in range(32):
                if current_id in visited:
                    return False
                visited.add(current_id)
                current = reader.read_loaded_thread(current_id)
                if current.get("sessionId") != session_id:
                    return False
                parent_id = current.get("parentThreadId")
                if parent_id == self.thread_id:
                    return True
                if not isinstance(parent_id, str) or not _THREAD_ID.fullmatch(parent_id):
                    return False
                current_id = parent_id
            return False
        finally:
            if reader is not None:
                reader.close()

    def start_turn(
        self,
        text: str,
        image_paths: Iterable[str | os.PathLike[str]] = (),
        *,
        client_user_message_id: str,
        cwd: str | os.PathLike[str] | None = None,
        permission_mode: str | None = None,
    ) -> str:
        """Start exactly one turn in the bound idle task; never retry blindly.

        The caller must keep this connection alive and handle subsequent
        ``receive_message`` values until ``turn/completed``.  This method does
        not grant approvals or answer user-input requests.
        """
        if self._active_turn_id is not None:
            raise SharedThreadNotIdle("this bridge already has an active turn")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be nonempty")
        if not isinstance(client_user_message_id, str) or not 1 <= len(client_user_message_id) <= 200:
            raise ValueError("client_user_message_id must be a stable nonempty id")
        paths: list[str] = []
        for image in image_paths:
            path = Path(image).expanduser().resolve(strict=True)
            if not path.is_file():
                raise ValueError(f"image is not a regular file: {path}")
            paths.append(str(path))
        params: dict[str, Any] = {
            "threadId": self.thread_id,
            "input": [{"type": "text", "text": text}, *({"type": "localImage", "path": path} for path in paths)],
            "clientUserMessageId": client_user_message_id,
        }
        if cwd is not None:
            directory = Path(cwd).expanduser().resolve(strict=True)
            if not directory.is_dir():
                raise ValueError("cwd must be a directory")
            params["cwd"] = str(directory)
        thread = self.read_thread()
        status = thread.get("status", {})
        if status.get("type") != "idle":
            raise SharedThreadNotIdle(f"Codex task status is {status!r}")
        permission_cwd = params.get("cwd") or thread.get("cwd") or self._permission_cwd
        params.update(self._saved_permission_overrides(permission_mode, cwd=permission_cwd, for_turn=True))
        try:
            result = self._rpc("turn/start", params)
        except SharedThreadRPCRejected as exc:
            # An explicit JSON-RPC rejection means this request was not
            # accepted.  A competing client may have started a turn after
            # our idle check; that case can be retried when it becomes idle.
            error_text = str(exc.error).lower()
            busy_error = any(phrase in error_text for phrase in (
                "turn already", "already in progress", "already active", "thread busy", "turn busy",
            ))
            try:
                status_after = self.read_thread().get("status", {})
            except SharedThreadBridgeError:
                status_after = {}
            if busy_error or status_after.get("type") == "active":
                raise SharedThreadNotIdle("another Codex turn started before feedback delivery") from exc
            raise
        except SharedThreadBridgeError as exc:
            # The socket may have dropped after the request was sent.  There
            # is no server-side deduplication guarantee for turn/start.
            raise UncertainTurnDelivery(f"inspect task history for {client_user_message_id} before retry: {exc}") from exc
        turn = result.get("turn")
        if not isinstance(turn, dict) or not isinstance(turn.get("id"), str):
            raise UncertainTurnDelivery(f"turn/start returned no turn id for {client_user_message_id}")
        self._active_turn_id = turn["id"]
        return turn["id"]

    def receive_message(self) -> dict[str, Any]:
        """Get the next event or server request; requests need human handling."""
        message = self._pending.popleft() if self._pending else self._receive()
        if message.get("method") == "turn/completed":
            params = message.get("params") or {}
            if params.get("threadId") in {None, self.thread_id} and (params.get("turn") or {}).get("id") == self._active_turn_id:
                self._active_turn_id = None
        return message

    def respond_to_request(self, request_id: int | str, result: dict[str, Any]) -> None:
        """Send a human-provided answer to a server-initiated request."""
        if not isinstance(request_id, (int, str)) or not isinstance(result, dict):
            raise ValueError("request_id and result are required")
        self._send({"id": request_id, "result": result})

    def interrupt_turn(self, turn_id: str | None = None) -> None:
        """Request cancellation of this bridge's turn; await ``turn/completed``."""
        active = self._active_turn_id
        if active is None or (turn_id is not None and turn_id != active):
            raise SharedThreadBridgeError("there is no matching active turn on this bridge")
        self._rpc("turn/interrupt", {"threadId": self.thread_id, "turnId": active})

    @property
    def active_turn_id(self) -> str | None:
        return self._active_turn_id

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self.ws.close()

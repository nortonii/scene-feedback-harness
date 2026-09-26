"""Connect a visual-feedback gateway to a task already loaded by Codex Desktop.

This module speaks App Server JSON-RPC over the desktop daemon's private Unix
WebSocket.  It deliberately never starts or resumes a thread: another App
Server process cannot own the desktop task's writer lock.  A caller must keep
the returned bridge connected while its turn runs and handle server requests
(approvals, elicitation, user input) rather than approving them automatically.
"""

from __future__ import annotations

import json
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


class SharedThreadBridgeError(RuntimeError):
    """The shared daemon could not safely serve this task."""


class SharedThreadNotIdle(SharedThreadBridgeError):
    """The target task has an active turn; starting another is unsafe."""


class SharedThreadTimeout(SharedThreadBridgeError):
    """No daemon message arrived before the socket timeout; keep listening."""


class UncertainTurnDelivery(SharedThreadBridgeError):
    """turn/start may have reached Codex; inspect history before any retry."""


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
    """One connection to an existing, idle desktop task.

    Use :meth:`connect_for_thread` to find the daemon that already loaded the
    task.  Do not create a fresh App Server or call ``thread/resume``.  Keep
    this object open until a started turn finishes, and process messages from
    :meth:`receive_message`, including server-initiated requests.
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

    @classmethod
    def connect_for_thread(
        cls,
        thread_id: str,
        *,
        socket_dir: Path | None = None,
        timeout: float = 10.0,
        require_idle: bool = True,
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
        loaded: list[tuple[SharedThreadBridge, dict[str, Any]]] = []
        errors: list[str] = []
        for path in _private_socket_candidates(socket_dir):
            bridge: SharedThreadBridge | None = None
            try:
                ws = connector(path, timeout)
                bridge = cls(ws, thread_id, timeout=timeout, socket_path=path)
                bridge._initialize()
                thread = bridge.read_thread()
                if thread.get("id") != thread_id:
                    raise SharedThreadBridgeError("daemon returned the wrong task")
                if thread.get("status", {}).get("type") != "notLoaded":
                    loaded.append((bridge, thread))
                else:
                    bridge.close()
            except Exception as exc:
                if bridge is not None:
                    bridge.close()
                errors.append(f"{path.name}: {exc}")
        if len(loaded) != 1:
            for bridge, _thread in loaded:
                bridge.close()
            detail = "multiple daemons have this task loaded" if loaded else "desktop task is not loaded by a shared daemon"
            if errors:
                detail += f" ({'; '.join(errors)})"
            raise SharedThreadBridgeError(detail)
        bridge, thread = loaded[0]
        if require_idle and thread.get("status", {}).get("type") != "idle":
            bridge.close()
            raise SharedThreadNotIdle(f"Codex task status is {thread.get('status')!r}")
        return bridge

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
                    raise SharedThreadBridgeError(f"{method} failed: {message['error']}")
                result = message.get("result")
                if not isinstance(result, dict):
                    raise SharedThreadBridgeError(f"{method} returned an invalid response")
                return result
            self._pending.append(message)

    def _initialize(self) -> None:
        self._rpc("initialize", {"clientInfo": {"name": "scene_feedback_shared_bridge", "title": "Scene Feedback Shared Bridge", "version": "0.1.0"}})
        self._send({"method": "initialized", "params": {}})

    def read_thread(self) -> dict[str, Any]:
        """Read task state without loading or subscribing to it."""
        thread = self._rpc("thread/read", {"threadId": self.thread_id, "includeTurns": False}).get("thread")
        if not isinstance(thread, dict) or thread.get("id") != self.thread_id:
            raise SharedThreadBridgeError("thread/read returned the wrong task")
        return thread

    def start_turn(
        self,
        text: str,
        image_paths: Iterable[str | os.PathLike[str]] = (),
        *,
        client_user_message_id: str,
        cwd: str | os.PathLike[str] | None = None,
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
        status = self.read_thread().get("status", {})
        if status.get("type") != "idle":
            raise SharedThreadNotIdle(f"Codex task status is {status!r}")
        try:
            result = self._rpc("turn/start", params)
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
            if params.get("threadId") == self.thread_id and (params.get("turn") or {}).get("id") == self._active_turn_id:
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

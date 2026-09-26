"""Deliver workbench feedback to a task loaded in the current Codex Desktop daemon."""

from __future__ import annotations

import threading
import time
import logging
from pathlib import Path
from typing import Any, Callable, Sequence

from appserver_adapter import TurnBusyError, UncertainDeliveryError
from shared_thread_bridge import SharedThreadBridge, SharedThreadNotIdle, SharedThreadTimeout, UncertainTurnDelivery


_RECONCILE_INTERVAL_SEC = 5.0


class SharedDesktopAdapter:
    """The WorkspaceGateway adapter interface for an existing desktop task.

    The daemon and task must already exist.  A separate Codex process cannot
    safely resume a desktop-owned rollout.  Each submitted turn keeps its own
    connection open until the terminal notification arrives, including any
    human approval requests.
    """

    def __init__(self, thread_id: str, on_event: Callable[[dict[str, Any]], None]):
        self.thread_id = thread_id
        self.on_event = on_event
        self._lock = threading.RLock()
        self._turn_lock = threading.Lock()
        self._bridge: SharedThreadBridge | None = None
        self._reader: threading.Thread | None = None
        self._pending_requests: dict[str, dict[str, Any]] = {}
        self._pending_rpc: dict[str, tuple[threading.Event, dict[str, Any]]] = {}
        self._next_id = 10_000
        self._connected = False
        self._closed = False
        self._turn_state = "idle"
        self._active_turn_id: str | None = None

    def start(self) -> str:
        with self._lock:
            if self._closed:
                raise RuntimeError("shared desktop adapter is closed")
        bridge = SharedThreadBridge.connect_for_thread(self.thread_id, require_idle=False)
        bridge.close()
        with self._lock:
            self._connected = True
        return self.thread_id

    def start_turn(self, text: str, image_paths: Sequence[str] = (), message_id: str | None = None) -> dict[str, Any]:
        if message_id is None:
            raise ValueError("a stable feedback message ID is required")
        with self._turn_lock:
            with self._lock:
                if self._closed:
                    raise RuntimeError("shared desktop adapter is closed")
                if self._turn_state != "idle":
                    raise TurnBusyError(f"shared desktop turn state is {self._turn_state}")
                self._turn_state = "waiting_for_idle"
            bridge: SharedThreadBridge | None = None
            try:
                while True:
                    with self._lock:
                        if self._closed:
                            raise RuntimeError("shared desktop adapter is closed")
                    try:
                        bridge = SharedThreadBridge.connect_for_thread(self.thread_id, subscribe=True)
                    except SharedThreadNotIdle:
                        time.sleep(1)
                        continue
                    # Another client can start a turn between the subscription
                    # and this second idle check. No turn/start was sent yet.
                    try:
                        turn_id = bridge.start_turn(text, image_paths, client_user_message_id=message_id)
                        break
                    except SharedThreadNotIdle:
                        bridge.close()
                        bridge = None
                        time.sleep(1)
                with self._lock:
                    if self._closed:
                        raise UncertainDeliveryError("adapter closed after Codex accepted the turn")
                    self._bridge = bridge
                    self._connected = True
                    self._active_turn_id = turn_id
                    self._turn_state = "active"
                    self._reader = threading.Thread(target=self._read_loop, args=(bridge,), daemon=True, name="shared-codex-events")
                    self._reader.start()
                return {"thread_id": self.thread_id, "turn_id": turn_id, "status": "inProgress"}
            except Exception as exc:
                if bridge is not None and bridge is not self._bridge:
                    bridge.close()
                with self._lock:
                    self._turn_state = "unknown" if isinstance(exc, (UncertainTurnDelivery, UncertainDeliveryError)) else "idle"
                if isinstance(exc, UncertainTurnDelivery):
                    raise UncertainDeliveryError(str(exc)) from exc
                raise

    def _read_loop(self, bridge: SharedThreadBridge) -> None:
        try:
            bridge.ws.settimeout(1.0)
            next_reconcile = time.monotonic() + _RECONCILE_INTERVAL_SEC
            while True:
                with self._lock:
                    if self._closed or self._bridge is not bridge:
                        return
                try:
                    message = bridge.receive_message()
                except SharedThreadTimeout:
                    if time.monotonic() >= next_reconcile:
                        next_reconcile = time.monotonic() + _RECONCILE_INTERVAL_SEC
                        with self._lock:
                            active_id = self._active_turn_id
                        if active_id:
                            try:
                                saved_turn = self.lookup_turn(active_id)
                            except Exception:
                                logging.exception("Could not reconcile shared Codex turn")
                            else:
                                if saved_turn and saved_turn.get("status") in {"completed", "failed", "interrupted"}:
                                    with self._lock:
                                        self._active_turn_id = None
                                        self._turn_state = "idle"
                                    self.on_event({"method": "turn/completed", "params": {"threadId": self.thread_id, "turn": saved_turn}})
                                    return
                    continue
                if "id" in message and "method" in message:
                    with self._lock:
                        self._pending_requests[str(message["id"])] = message
                    self.on_event({"method": "adapter/request_pending", "params": {"request_id": message["id"], "method": message["method"], "params": message.get("params", {})}})
                elif "id" in message:
                    with self._lock:
                        pending = self._pending_rpc.pop(str(message["id"]), None)
                    if pending:
                        event, result = pending
                        result.update(message)
                        event.set()
                elif "method" in message:
                    params = message.get("params") or {}
                    if isinstance(params, dict) and params.get("threadId") not in {None, self.thread_id}:
                        continue
                    if message["method"] == "turn/completed":
                        if bridge.active_turn_id:
                            continue
                        with self._lock:
                            self._active_turn_id = None
                            self._turn_state = "idle"
                        self.on_event(message)
                        return
                    self.on_event(message)
        except Exception as exc:
            with self._lock:
                if not self._closed:
                    self._turn_state = "unknown"
                    self._connected = False
                    self.on_event({"method": "adapter/disconnected", "params": {"error": str(exc)[:500]}})
        finally:
            with self._lock:
                if self._bridge is bridge:
                    self._bridge = None
                    self._pending_requests.clear()
                    for event, result in self._pending_rpc.values():
                        result["error"] = "shared Codex connection closed"
                        event.set()
                    self._pending_rpc.clear()
            bridge.close()

    def lookup_turn(self, turn_id: str) -> dict[str, Any] | None:
        """Read the authoritative saved status for one exact turn ID."""
        bridge = SharedThreadBridge.connect_for_thread(self.thread_id, require_idle=False)
        try:
            thread = bridge.read_thread(include_turns=True)
            for turn in thread.get("turns") or []:
                if isinstance(turn, dict) and turn.get("id") == turn_id:
                    return turn
            return None
        finally:
            bridge.close()

    def respond_to_request(self, request_id: int | str, result: dict[str, Any]) -> None:
        with self._lock:
            request = self._pending_requests.get(str(request_id))
            bridge = self._bridge
            if request is None or bridge is None:
                raise ValueError("Codex request is no longer pending")
            bridge.respond_to_request(request["id"], result)
            del self._pending_requests[str(request_id)]

    def interrupt(self, turn_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            bridge = self._bridge
            target = turn_id or self._active_turn_id
            if bridge is None or target is None:
                raise TurnBusyError("there is no active shared Codex turn")
            rpc_id = self._next_id
            self._next_id += 1
            done = threading.Event()
            result: dict[str, Any] = {}
            self._pending_rpc[str(rpc_id)] = (done, result)
            bridge._send({"id": rpc_id, "method": "turn/interrupt", "params": {"threadId": self.thread_id, "turnId": target}})
        if not done.wait(10):
            raise UncertainDeliveryError("turn/interrupt response timed out")
        if "error" in result:
            raise RuntimeError(f"turn/interrupt failed: {result['error']}")
        return {"thread_id": self.thread_id, "turn_id": target, "result": result.get("result")}

    def refresh(self) -> dict[str, Any]:
        # An uncertain turn must be inspected in the original task before
        # retrying.  A now-idle task alone does not prove it never received it.
        with self._lock:
            if self._turn_state == "unknown":
                return self.status()
        bridge = SharedThreadBridge.connect_for_thread(self.thread_id, require_idle=False)
        try:
            thread = bridge.read_thread()
        finally:
            bridge.close()
        with self._lock:
            self._connected = True
            if self._active_turn_id is None:
                self._turn_state = "idle" if thread.get("status", {}).get("type") == "idle" else "active"
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {"connected": self._connected, "thread_id": self.thread_id, "turn_id": self._active_turn_id, "turn_state": self._turn_state, "pending_requests": list(self._pending_requests.values())}

    def close(self) -> None:
        with self._lock:
            self._closed = True
            bridge = self._bridge
        if bridge:
            bridge.close()

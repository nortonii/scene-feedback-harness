"""Deliver workbench feedback to a task loaded in the current Codex Desktop daemon."""

from __future__ import annotations

import threading
import time
import logging
from pathlib import Path
from typing import Any, Callable, Sequence

from appserver_adapter import TurnBusyError, UncertainDeliveryError
from shared_thread_bridge import (
    SharedThreadBridge,
    SharedThreadBridgeError,
    SharedThreadNotIdle,
    SharedThreadRPCRejected,
    SharedThreadTimeout,
    UncertainTurnDelivery,
)


_RECONCILE_INTERVAL_SEC = 5.0


class DeliveryNotReadyError(TurnBusyError):
    """No turn/start was sent; the desktop task can be tried later."""


class DeliveryRejectedError(RuntimeError):
    """App Server explicitly rejected turn/start; no turn was created."""


class SharedDesktopAdapter:
    """The WorkspaceGateway adapter interface for an existing desktop task.

    The daemon and task must already exist.  A separate Codex process cannot
    safely resume a desktop-owned rollout.  Each submitted turn keeps its own
    connection open until the terminal notification arrives, including any
    human approval requests.
    """

    def __init__(self, thread_id: str, on_event: Callable[[dict[str, Any]], None], *, allow_owned_resume: bool = False, initial_bridge: SharedThreadBridge | None = None):
        self.thread_id = thread_id
        self.on_event = on_event
        self.allow_owned_resume = allow_owned_resume
        self._initial_bridge = initial_bridge
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
        self._uncertain_feedback_id: str | None = None

    def _connect(self, *, require_idle: bool = True, subscribe: bool = False) -> SharedThreadBridge:
        return SharedThreadBridge.connect_for_thread(
            self.thread_id,
            require_idle=require_idle,
            subscribe=subscribe,
            allow_owned_resume=self.allow_owned_resume,
        )

    def start(self) -> str:
        with self._lock:
            if self._closed:
                raise RuntimeError("shared desktop adapter is closed")
            bridge = self._initial_bridge
        if bridge is not None:
            try:
                thread = bridge.read_thread()
                if thread.get("id") != self.thread_id:
                    raise SharedThreadBridgeError("new Codex task ID changed")
            except SharedThreadBridgeError:
                with self._lock:
                    if self._initial_bridge is bridge:
                        self._initial_bridge = None
                bridge.close()
                bridge = None
        if bridge is None:
            bridge = self._connect(require_idle=False)
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
                    raise DeliveryNotReadyError("shared desktop adapter is closed")
                if self._turn_state != "idle":
                    raise DeliveryNotReadyError(f"shared desktop turn state is {self._turn_state}")
                self._turn_state = "starting"
            bridge: SharedThreadBridge | None = None
            try:
                try:
                    with self._lock:
                        bridge = self._initial_bridge
                    if bridge is None:
                        bridge = self._connect(subscribe=True)
                    # A second idle check in start_turn catches a competing
                    # desktop turn.  The dispatcher should schedule a later
                    # attempt instead of keeping its queue worker blocked.
                    turn_id = bridge.start_turn(text, image_paths, client_user_message_id=message_id)
                except SharedThreadNotIdle as exc:
                    raise DeliveryNotReadyError(str(exc)) from exc
                except SharedThreadRPCRejected as exc:
                    if exc.method == "turn/start":
                        raise DeliveryRejectedError(str(exc)) from exc
                    raise DeliveryNotReadyError(str(exc)) from exc
                except UncertainTurnDelivery:
                    raise
                except SharedThreadBridgeError as exc:
                    # Discovery, subscription and the pre-send thread/read
                    # are safe to retry: bridge.start_turn wraps any error
                    # after sending as UncertainTurnDelivery.
                    with self._lock:
                        if bridge is not None and self._initial_bridge is bridge:
                            self._initial_bridge = None
                            bridge.close()
                    raise DeliveryNotReadyError(str(exc)) from exc
                with self._lock:
                    if self._closed:
                        raise UncertainDeliveryError("adapter closed after Codex accepted the turn")
                    self._bridge = bridge
                    if self._initial_bridge is bridge:
                        self._initial_bridge = None
                    self._connected = True
                    self._active_turn_id = turn_id
                    self._turn_state = "active"
                    self._uncertain_feedback_id = None
                    self._reader = threading.Thread(target=self._read_loop, args=(bridge,), daemon=True, name="shared-codex-events")
                    self._reader.start()
                return {"thread_id": self.thread_id, "turn_id": turn_id, "status": "inProgress"}
            except Exception as exc:
                if bridge is not None and bridge is not self._bridge and bridge is not self._initial_bridge:
                    bridge.close()
                with self._lock:
                    uncertain = isinstance(exc, (UncertainTurnDelivery, UncertainDeliveryError))
                    self._turn_state = "unknown" if uncertain else "idle"
                    if uncertain:
                        self._uncertain_feedback_id = message_id
                    elif isinstance(exc, DeliveryNotReadyError):
                        self._connected = False
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
                                        self._pending_requests.clear()
                                    self.on_event({"method": "turn/completed", "params": {"threadId": self.thread_id, "turn": saved_turn}})
                                    return
                    continue
                if "id" in message and "method" in message:
                    request_params = message.get("params")
                    request_thread_id = None
                    if isinstance(request_params, dict):
                        request_thread_id = request_params.get("threadId")
                        if request_thread_id is not None and request_thread_id != self.thread_id:
                            # Subagents use their own threadId. Verify the
                            # parent chain on a separate daemon connection;
                            # an unrelated task must never enter this panel.
                            try:
                                descendant = bridge.is_descendant_thread(request_thread_id)
                            except Exception:
                                logging.exception("Could not verify Codex child request %s", request_thread_id)
                                continue
                            if not descendant:
                                continue
                    with self._lock:
                        self._pending_requests[str(message["id"])] = message
                    self.on_event({"method": "adapter/request_pending", "params": {"thread_id": self.thread_id, "source_thread_id": request_thread_id or self.thread_id, "request_id": message["id"], "method": message["method"], "params": request_params if isinstance(request_params, dict) else {}}})
                elif "id" in message:
                    with self._lock:
                        pending = self._pending_rpc.pop(str(message["id"]), None)
                    if pending:
                        event, result = pending
                        result.update(message)
                        event.set()
                elif "method" in message:
                    params = message.get("params") or {}
                    if message["method"] == "serverRequest/resolved" and isinstance(params, dict):
                        request_id = params.get("requestId")
                        if isinstance(request_id, (int, str)) and not isinstance(request_id, bool):
                            resolved_source: str | None = None
                            with self._lock:
                                pending = self._pending_requests.get(str(request_id))
                                pending_params = pending.get("params") if isinstance(pending, dict) else None
                                pending_thread_id = pending_params.get("threadId") if isinstance(pending_params, dict) else None
                                pending_source = pending_thread_id or self.thread_id
                                notice_source = params.get("threadId")
                                same_id = (pending is not None and type(pending.get("id")) is type(request_id)
                                           and pending["id"] == request_id)
                                # A child request must name that same child in
                                # the resolution. Parent requests may omit the
                                # thread ID on older App Server notifications.
                                same_source = notice_source == pending_source or (notice_source is None and pending_source == self.thread_id)
                                if same_id and same_source:
                                    self._pending_requests.pop(str(request_id), None)
                                    resolved_source = pending_source
                            if resolved_source is not None:
                                self.on_event({"method": "adapter/request_resolved", "params": {
                                    "thread_id": self.thread_id,
                                    "source_thread_id": resolved_source,
                                    "request_id": request_id,
                                }})
                        continue
                    if isinstance(params, dict) and params.get("threadId") not in {None, self.thread_id}:
                        continue
                    if message["method"] == "turn/completed":
                        if bridge.active_turn_id:
                            continue
                        with self._lock:
                            self._active_turn_id = None
                            self._turn_state = "idle"
                            self._pending_requests.clear()
                        self.on_event(message)
                        return
                    self.on_event(message)
        except Exception as exc:
            with self._lock:
                if not self._closed:
                    self._turn_state = "unknown"
                    self._connected = False
                    self.on_event({"method": "adapter/disconnected", "params": {"thread_id": self.thread_id, "error": str(exc)[:500]}})
        finally:
            with self._lock:
                if self._bridge is bridge:
                    self._bridge = None
                    self._connected = False
                    self._pending_requests.clear()
                    for event, result in self._pending_rpc.values():
                        result["error"] = "shared Codex connection closed"
                        event.set()
                    self._pending_rpc.clear()
            bridge.close()

    def lookup_turn(self, turn_id: str) -> dict[str, Any] | None:
        """Read the authoritative saved status for one exact turn ID."""
        bridge = self._connect(require_idle=False)
        try:
            thread = bridge.read_thread(include_turns=True)
            for turn in thread.get("turns") or []:
                if isinstance(turn, dict) and turn.get("id") == turn_id:
                    return turn
            return None
        finally:
            bridge.close()

    def lookup_feedback(self, feedback_id: str) -> list[dict[str, Any]]:
        """Find exactly the turns whose user message carries this client ID.

        A duplicate ID can have multiple turns: clientUserMessageId records an
        ID but is not a server-side deduplication promise.
        """
        if not isinstance(feedback_id, str) or not feedback_id:
            raise ValueError("feedback_id must be nonempty text")
        bridge = self._connect(require_idle=False)
        try:
            thread = bridge.read_thread(include_turns=True)
            matches: list[dict[str, Any]] = []
            for turn in thread.get("turns") or []:
                if not isinstance(turn, dict):
                    continue
                items = turn.get("items") or []
                if any(isinstance(item, dict) and item.get("type") == "userMessage" and item.get("clientId") == feedback_id for item in items):
                    matches.append(turn)
            return matches
        finally:
            bridge.close()

    def inspect_thread_status(self) -> str:
        """Read the daemon's runtime task status, independent of local state."""
        with self._lock:
            held_bridge = self._initial_bridge
        bridge = held_bridge or self._connect(require_idle=False)
        try:
            try:
                status = bridge.read_thread().get("status") or {}
            except SharedThreadBridgeError:
                if bridge is not held_bridge:
                    raise
                with self._lock:
                    if self._initial_bridge is held_bridge:
                        self._initial_bridge = None
                held_bridge.close()
                bridge = self._connect(require_idle=False)
                status = bridge.read_thread().get("status") or {}
            kind = status.get("type") if isinstance(status, dict) else None
            if not isinstance(kind, str) or not kind:
                raise SharedThreadBridgeError("thread/read returned no runtime status")
            with self._lock:
                self._connected = True
            return kind
        finally:
            if bridge is not held_bridge:
                bridge.close()

    def release_uncertain(self) -> None:
        """Allow a new attempt after the gateway has resolved an old one."""
        with self._lock:
            if self._bridge is not None:
                raise TurnBusyError("cannot release uncertainty while a turn connection is active")
            self._active_turn_id = None
            self._uncertain_feedback_id = None
            self._turn_state = "idle"

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
            if self._turn_state == "unknown" and (self._uncertain_feedback_id or self._active_turn_id):
                return self.status()
        bridge = self._connect(require_idle=False)
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
            initial_bridge = self._initial_bridge
            self._initial_bridge = None
        if bridge:
            bridge.close()
        if initial_bridge and initial_bridge is not bridge:
            initial_bridge.close()

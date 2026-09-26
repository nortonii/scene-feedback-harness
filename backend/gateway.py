"""Durable browser-to-Codex delivery for one local reconstruction workspace."""

from __future__ import annotations

import copy
import base64
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from appserver_adapter import TurnBusyError
from core import APIError, MAX_REFERENCE_BYTES, MAX_REFERENCES, SceneStore, _now
from shared_thread_adapter import DeliveryNotReadyError, DeliveryRejectedError
from shared_thread_bridge import SharedThreadNotIdle


class WorkspaceGateway:
    def __init__(self, store: SceneStore, project_dir: str | Path, *, adapter: Any = None, external_review: bool = False):
        self.store = store
        self.project_dir = Path(project_dir).expanduser().resolve()
        self.adapter = adapter
        self.external_review = external_review
        self._worker_lock = threading.Lock()
        self._worker_running = False
        self._worker_thread: threading.Thread | None = None
        self._started = False
        self._supervisor_lock = threading.Lock()
        self._supervisor_stop = threading.Event()
        self._supervisor_thread: threading.Thread | None = None
        self._adapter_initialized = False

    _RECONCILE_INTERVAL_SEC = 5.0
    _UNCERTAIN_GRACE_SEC = 30.0

    def ensure(self, preferred_session_id: str | None = None) -> dict[str, Any]:
        workspace = self.store.ensure_workspace(self.project_dir, preferred_session_id=preferred_session_id)
        mode = "external" if self.external_review else "appserver"
        with self.store.lock:
            stored = self.store.state["workspace"]
            existing = stored.get("delivery_mode")
            if existing is not None and existing != mode:
                raise APIError(409, f"workspace is already bound to {existing} delivery")
            if existing is None:
                if self.external_review and (stored.get("thread_id") or stored.get("queue")):
                    raise APIError(409, "workspace already contains Codex App Server activity")
                stored["delivery_mode"] = mode
                self.store._save()
            return copy.deepcopy(stored)

    def state(self, *, include_capability: bool = False, preferred_session_id: str | None = None) -> dict[str, Any]:
        self.ensure(preferred_session_id)
        result = self.store.workspace()
        result.pop("events", None)
        result["events_cursor"] = result.pop("event_seq")
        for approval in result.get("approvals", []):
            approval.pop("request_id", None)
        if include_capability:
            result["browser_capability"] = self.store.browser_token
        return result

    def start(self) -> None:
        self.ensure()
        self._started = True
        if self.external_review and self.adapter is None:
            self.store.workspace_agent(status="external_idle")
            return
        if self.adapter is None:
            self.store.workspace_agent(status="disconnected", error="Codex App Server is not configured")
            return
        if self.external_review:
            # A request that was waiting for an MCP call is still a durable
            # browser submission when this service becomes directly bound.
            with self.store.lock:
                workspace = self.store.state["workspace"]
                for item in workspace["queue"]:
                    if item["status"] == "awaiting_mcp":
                        item["status"] = "queued"
                self.store._save()
            self._supervise_once()
            self._supervisor_thread = threading.Thread(target=self._supervisor_loop, daemon=True, name="workspace-supervisor")
            self._supervisor_thread.start()
            return
        try:
            thread_id = self.adapter.start()
            self.store.workspace_thread(thread_id)
            bound_task_idle = True
            if self.external_review:
                bound_task_idle = self.adapter.refresh().get("turn_state") == "idle"
            with self.store.lock:
                saved_workspace = self.store.state["workspace"]
                saved_active = saved_workspace.get("active_feedback_id")
                saved_item = next((entry for entry in saved_workspace["queue"] if entry["feedback_id"] == saved_active), None)
                saved_turn_id = saved_item.get("turn_id") if saved_item else None
            recovered_turn = None
            if self.external_review and saved_turn_id and hasattr(self.adapter, "lookup_turn"):
                try:
                    recovered_turn = self.adapter.lookup_turn(saved_turn_id)
                except Exception:
                    logging.exception("Could not reconcile the previous shared Codex turn")
            with self.store.lock:
                workspace = self.store.state["workspace"]
                active_id = workspace.get("active_feedback_id")
                workspace["approvals"] = []
                if self.external_review and bound_task_idle:
                    for queued in workspace["queue"]:
                        if queued["status"] == "awaiting_mcp":
                            queued["status"] = "queued"
                if active_id:
                    item = next((entry for entry in workspace["queue"] if entry["feedback_id"] == active_id), None)
                    if item and item["status"] in {"dispatching", "running"}:
                        if recovered_turn and recovered_turn.get("id") == item.get("turn_id") and recovered_turn.get("status") in {"completed", "failed", "interrupted"}:
                            item["status"] = recovered_turn["status"]
                            item["error"] = None
                            workspace["active_feedback_id"] = None
                            workspace["agent"] = {"status": "idle", "turn_id": None, "error": None}
                            self.store.workspace_event("turn_reconciled", {"feedback_id": active_id, "turn_id": item["turn_id"], "status": item["status"]})
                        else:
                            item["status"] = "delivery_uncertain"
                            item["error"] = "Gateway restarted during a turn; inspect the thread before retrying."
                            workspace["agent"] = {"status": "delivery_uncertain", "turn_id": item.get("turn_id"), "error": item["error"]}
                            self.store.workspace_event("delivery_uncertain", {"feedback_id": active_id, "message": item["error"]})
                        self.store._save()
                else:
                    workspace["agent"] = {"status": "idle", "turn_id": None, "error": None}
                    self.store._save()
            self.wake()
        except Exception as exc:
            logging.exception("Could not connect local Codex App Server")
            self.store.workspace_agent(status="disconnected", error=str(exc)[:500])
            self.store.workspace_event("disconnected", {"message": str(exc)[:500]})

    def close(self) -> None:
        with self._worker_lock:
            self._started = False
            worker = self._worker_thread
        self._supervisor_stop.set()
        if self._supervisor_thread is not None and self._supervisor_thread is not threading.current_thread():
            self._supervisor_thread.join(timeout=30)
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=30)
        if self.adapter is not None:
            self.adapter.close()

    def _supervisor_loop(self) -> None:
        while not self._supervisor_stop.wait(self._RECONCILE_INTERVAL_SEC):
            try:
                self._supervise_once()
            except Exception:
                logging.exception("Workspace supervisor failed; retrying")

    def _supervise_once(self) -> None:
        if not self._started or self.adapter is None or not self.external_review:
            return
        if not self._supervisor_lock.acquire(blocking=False):
            return
        try:
            try:
                if not self._adapter_initialized or not self.adapter.status().get("connected"):
                    self.store.workspace_thread(self.adapter.start())
                    self._adapter_initialized = True
            except Exception as exc:
                error = str(exc)[:500]
                with self.store.lock:
                    workspace = self.store.state["workspace"]
                    active_id = workspace.get("active_feedback_id")
                    if not active_id and workspace["agent"].get("status") != "disconnected":
                        workspace["agent"] = {"status": "disconnected", "turn_id": None, "error": error}
                        self.store._save()
                        self.store.workspace_event("disconnected", {"message": error})
                if active_id:
                    self._mark_uncertain(active_id, "Cannot inspect the Codex task while disconnected; checking again.")
                return
            with self.store.lock:
                workspace = self.store.state["workspace"]
                active_id = workspace.get("active_feedback_id")
                active = next((item for item in workspace["queue"] if item["feedback_id"] == active_id), None)
                active = copy.deepcopy(active) if active else None
                quarantined = [item["feedback_id"] for item in workspace["queue"] if item["status"] == "delivery_uncertain" and item.get("quarantined_at") and item["feedback_id"] != active_id]
            if active:
                self._reconcile_active(active)
            elif active_id:
                with self.store.lock:
                    if self.store.state["workspace"].get("active_feedback_id") == active_id:
                        self.store.state["workspace"]["active_feedback_id"] = None
                        self.store._save()
            for feedback_id in quarantined:
                self._reconcile_quarantined(feedback_id)
            with self.store.lock:
                workspace = self.store.state["workspace"]
                if not workspace.get("active_feedback_id"):
                    pending = any(item["status"] == "queued" for item in workspace["queue"])
                    if workspace["agent"].get("status") == "disconnected" or (workspace["agent"].get("status") == "waiting" and not pending):
                        workspace["agent"] = {"status": "idle", "turn_id": None, "error": None}
                        self.store._save()
                else:
                    pending = False
            if pending:
                self.wake()
        finally:
            self._supervisor_lock.release()

    def _matching_turn(self, feedback_id: str, turn_id: str | None) -> tuple[dict[str, Any] | None, bool]:
        """Return an exact saved turn, and whether multiple deliveries exist."""
        if hasattr(self.adapter, "lookup_feedback"):
            matches = self.adapter.lookup_feedback(feedback_id)
            if len(matches) > 1:
                return None, True
            if matches:
                return matches[0], False
        if turn_id and hasattr(self.adapter, "lookup_turn"):
            return self.adapter.lookup_turn(turn_id), False
        return None, False

    def _apply_reconciled_turn(self, feedback_id: str, turn: dict[str, Any]) -> None:
        turn_id = turn.get("id")
        outcome = turn.get("status")
        if not isinstance(turn_id, str) or outcome not in {"inProgress", "completed", "failed", "interrupted"}:
            return
        terminal = outcome != "inProgress"
        with self.store.lock:
            workspace = self.store.state["workspace"]
            item = next((entry for entry in workspace["queue"] if entry["feedback_id"] == feedback_id), None)
            if item is None or item["status"] == "discarded":
                return
            if not terminal and workspace.get("active_feedback_id") not in {None, feedback_id}:
                return
            target_status = "running" if not terminal else outcome
            if item["status"] == target_status and item.get("turn_id") == turn_id:
                return
            item["status"] = target_status
            item["turn_id"] = turn_id
            item["error"] = None if outcome == "completed" else item.get("error")
            item.pop("quarantined_at", None)
            item.pop("uncertain_since", None)
            if terminal:
                if workspace.get("active_feedback_id") == feedback_id:
                    workspace["active_feedback_id"] = None
                    workspace["approvals"] = []
                    workspace["agent"] = {"status": "idle", "turn_id": None, "error": None}
            else:
                workspace["active_feedback_id"] = feedback_id
                workspace["agent"] = {"status": "running", "turn_id": turn_id, "error": None}
            self.store._save()
            self.store.workspace_event("turn_reconciled", {"feedback_id": feedback_id, "turn_id": turn_id, "status": item["status"]})
        if terminal and hasattr(self.adapter, "release_uncertain"):
            try:
                self.adapter.release_uncertain()
            except Exception:
                logging.exception("Could not reset reconciled adapter state")

    def _reconcile_active(self, item: dict[str, Any]) -> None:
        feedback_id = item["feedback_id"]
        if item["status"] == "dispatching":
            with self._worker_lock:
                if self._worker_running:
                    return
        try:
            turn, duplicate = self._matching_turn(feedback_id, item.get("turn_id"))
            if turn and not duplicate:
                self._apply_reconciled_turn(feedback_id, turn)
                return
            if not duplicate and item["status"] == "running" and self.adapter.status().get("turn_state") in {"active", "running"}:
                return
            runtime_status = self.adapter.inspect_thread_status() if hasattr(self.adapter, "inspect_thread_status") else self.adapter.refresh().get("turn_state")
        except Exception:
            logging.exception("Could not reconcile feedback %s", feedback_id)
            return
        self._mark_uncertain(feedback_id, "Multiple Codex turns carry this feedback ID; inspect the task before retrying." if duplicate else "Delivery could not be confirmed in the Codex task; checking again.")
        with self.store.lock:
            current = next((entry for entry in self.store.state["workspace"]["queue"] if entry["feedback_id"] == feedback_id), None)
            since = current.get("uncertain_since") if current else None
        if runtime_status != "idle" or not since:
            return
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(since)).total_seconds()
        except (TypeError, ValueError):
            age = 0
        if age < self._UNCERTAIN_GRACE_SEC:
            return
        with self.store.lock:
            workspace = self.store.state["workspace"]
            current = next((entry for entry in workspace["queue"] if entry["feedback_id"] == feedback_id), None)
            if workspace.get("active_feedback_id") != feedback_id or current is None or current["status"] != "delivery_uncertain":
                return
            current["quarantined_at"] = _now()
            workspace["active_feedback_id"] = None
            workspace["agent"] = {"status": "idle", "turn_id": None, "error": None}
            self.store._save()
            self.store.workspace_event("uncertain_feedback_quarantined", {"feedback_id": feedback_id})
        if hasattr(self.adapter, "release_uncertain"):
            try:
                self.adapter.release_uncertain()
            except Exception:
                logging.exception("Could not release quarantined delivery")

    def _reconcile_quarantined(self, feedback_id: str) -> None:
        if not hasattr(self.adapter, "lookup_feedback"):
            return
        try:
            turn, duplicate = self._matching_turn(feedback_id, None)
        except Exception:
            logging.exception("Could not inspect quarantined feedback %s", feedback_id)
            return
        if duplicate:
            self._mark_uncertain(feedback_id, "Multiple Codex turns carry this feedback ID; inspect the task before retrying.")
        elif turn:
            self._apply_reconciled_turn(feedback_id, turn)

    def _mark_uncertain(self, feedback_id: str, message: str) -> None:
        with self.store.lock:
            workspace = self.store.state["workspace"]
            item = next((entry for entry in workspace["queue"] if entry["feedback_id"] == feedback_id), None)
            if item is None or item["status"] in {"completed", "failed", "interrupted", "discarded"}:
                return
            changed = item["status"] != "delivery_uncertain"
            item["status"] = "delivery_uncertain"
            item["error"] = message
            item.setdefault("uncertain_since", _now())
            if workspace.get("active_feedback_id") == feedback_id:
                workspace["agent"] = {"status": "delivery_uncertain", "turn_id": item.get("turn_id"), "error": message}
            self.store._save()
            if changed:
                self.store.workspace_event("delivery_uncertain", {"feedback_id": feedback_id, "message": message})

    def submit(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        workspace = self.ensure(preferred_session_id=session_id)
        if session_id != workspace["session_id"]:
            raise APIError(409, "feedback belongs to another session; use the workspace session")
        if not payload.get("idempotency_key"):
            raise APIError(400, "idempotency_key is required for direct Codex delivery")
        feedback = self.store.submit_feedback(session_id, payload)
        if self.external_review and self.adapter is None:
            with self.store.lock:
                item = next(entry for entry in self.store.state["workspace"]["queue"] if entry["feedback_id"] == feedback["feedback_id"])
                if item["status"] == "queued":
                    item["status"] = "awaiting_mcp"
                    self.store._save()
                result = copy.deepcopy(feedback)
                result["delivery"] = copy.deepcopy(item)
                return result
        self.wake()
        with self.store.lock:
            item = next(entry for entry in self.store.state["workspace"]["queue"] if entry["feedback_id"] == feedback["feedback_id"])
            result = copy.deepcopy(feedback)
            result["delivery"] = copy.deepcopy(item)
            return result

    def _project_file(self, local_path: Any) -> Path:
        if not isinstance(local_path, str) or not local_path:
            raise APIError(400, "local path must be a file path")
        try:
            path = Path(local_path).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise APIError(400, "local file does not exist") from exc
        if not path.is_file() or not path.is_relative_to(self.project_dir):
            raise APIError(400, "local file must be inside the workspace project directory")
        return path

    def add_reference_paths(self, paths: Any) -> dict[str, Any]:
        """Import local images for the bound workspace, avoiding duplicate uploads."""
        workspace = self.ensure()
        if not isinstance(paths, list) or len(paths) > MAX_REFERENCES:
            raise APIError(400, "reference_images must be an array of at most 8 paths")
        manifest = self._reference_camera_manifest()
        prepared = []
        for path in paths:
            source = self._project_file(path)
            _, data = self.store._read_reference_path(str(source))
            # Camera exports often share names such as 000480.jpg. Keep the
            # view name visible in the browser's reference strip.
            label = f"{source.parent.name}_{source.name}"[-180:]
            prepared.append((label, data, self._manifest_camera(label, data, manifest)))
        with self.store.lock:
            session_id = workspace["session_id"]
            current = self.store.state["sessions"][session_id]["reference_images"]
            existing = {(entry["name"], (self.store.media_dir / entry["url"].rsplit("/", 1)[-1]).read_bytes()): entry for entry in current}
            novel = [(name, data, camera) for name, data, camera in prepared if (name, data) not in existing]
            if len(current) + len({(name, data) for name, data, _ in novel}) > MAX_REFERENCES:
                raise APIError(400, "workspace may contain at most 8 reference images")
            for name, data, _ in novel:
                if (name, data) in existing:
                    continue
                mime = self.store._image_kind(data)[1]
                data_url = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
                existing[(name, data)] = self.store.add_reference(session_id, name, data_url)
            camera_updates = list({existing[(name, data)]["id"]: {"reference_id": existing[(name, data)]["id"], "camera": camera}
                                   for name, data, camera in prepared if camera is not None and existing[(name, data)].get("camera") != camera}.values())
            if camera_updates:
                self.store.set_reference_cameras(session_id, camera_updates)
            return {"session_id": session_id, "reference_images": copy.deepcopy(self.store.state["sessions"][session_id]["reference_images"])}

    def _reference_camera_manifest(self) -> list[tuple[str, dict[str, Any]]]:
        """Load optional filename-prefix camera mapping from the private data dir.

        Each entry describes a fixed calibrated camera; it intentionally does
        not contain a frame-specific alignment image. A new frame must receive
        its own undistorted image through set_reference_cameras.
        """
        path = self.store.data_dir / "reference_cameras.json"
        if not path.exists():
            return []
        try:
            if path.stat().st_size > 1_000_000:
                raise APIError(400, "reference camera manifest exceeds 1 MB")
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise APIError(400, "reference camera manifest cannot be read") from exc
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or not isinstance(manifest.get("entries"), list):
            raise APIError(400, "reference camera manifest must have schema_version 1 and entries")
        entries = []
        seen = set()
        for entry in manifest["entries"]:
            if not isinstance(entry, dict):
                raise APIError(400, "reference camera entry must be an object")
            prefix = entry.get("name_prefix")
            if not isinstance(prefix, str) or not 1 <= len(prefix) <= 180 or any(ord(char) < 32 for char in prefix) or "/" in prefix or "\\" in prefix or prefix in seen:
                raise APIError(400, "reference camera name_prefix must be unique short file text")
            seen.add(prefix)
            entries.append((prefix, self.store._normalize_reference_camera(entry.get("camera"))))
        return entries

    def _manifest_camera(self, name: str, image_bytes: bytes, entries: list[tuple[str, dict[str, Any]]]) -> dict[str, Any] | None:
        matches = [(len(prefix), camera) for prefix, camera in entries if name.startswith(prefix)]
        if not matches:
            return None
        camera = max(matches, key=lambda item: item[0])[1]
        intrinsics = camera["intrinsics"]
        if self.store._image_dimensions(image_bytes) != (intrinsics["width"], intrinsics["height"]):
            raise APIError(400, "reference camera manifest image dimensions do not match")
        return camera

    def apply_reference_camera_manifest(self) -> dict[str, Any]:
        """Attach a project-specific camera manifest to references already imported."""
        workspace = self.ensure()
        entries = self._reference_camera_manifest()
        if not entries:
            raise APIError(404, "reference camera manifest is not installed")
        session_id = workspace["session_id"]
        session = self.store.get_session(session_id)
        updates = []
        for reference in session["reference_images"]:
            data = (self.store.media_dir / reference["url"].rsplit("/", 1)[-1]).read_bytes()
            camera = self._manifest_camera(reference["name"], data, entries)
            if camera is not None and reference.get("camera") != camera:
                updates.append({"reference_id": reference["id"], "camera": camera})
        if updates:
            return self.store.set_reference_cameras(session_id, updates)
        return {"session_id": session_id, "reference_images": session["reference_images"]}

    def set_reference_cameras(self, cameras: Any) -> dict[str, Any]:
        workspace = self.ensure()
        return self.store.set_reference_cameras(workspace["session_id"], cameras)

    def add_reference_data_url(self, session_id: str, name: Any, data_url: Any) -> dict[str, Any]:
        """Browser uploads also receive camera metadata when their names match."""
        camera = None
        entries = self._reference_camera_manifest()
        if entries:
            if not isinstance(name, str):
                raise APIError(400, "name must be a file name")
            data = self.store._decode_image_data_url(data_url, max_bytes=MAX_REFERENCE_BYTES)
            camera = self._manifest_camera(name, data, entries)
        reference = self.store.add_reference(session_id, name, data_url)
        if camera is not None:
            result = self.store.set_reference_cameras(session_id, [{"reference_id": reference["id"], "camera": camera}])
            return next(item for item in result["reference_images"] if item["id"] == reference["id"])
        return reference

    def begin_external_request(self, *, session_id: Any = None, message: Any = None, cursor: Any = None) -> dict[str, Any]:
        if not self.external_review:
            raise APIError(409, "this workspace uses direct Codex delivery")
        workspace = self.ensure()
        if session_id is not None and session_id != workspace["session_id"]:
            raise APIError(409, "review session belongs to another workspace")
        if message is not None and (not isinstance(message, str) or not 1 <= len(message) <= 2000):
            raise APIError(400, "request message must contain 1 to 2000 characters")
        with self.store.lock:
            session = self.store.state["sessions"][workspace["session_id"]]
            count = session.get("feedback_count", 0)
            if cursor is None:
                cursor = count
            if type(cursor) is not int or not 0 <= cursor <= count:
                raise APIError(400, "cursor must refer to an existing feedback position")
            current = self.store.state["workspace"]
            if self.adapter is None:
                current["agent"] = {"status": "external_idle", "turn_id": None, "error": None}
            if message is not None:
                current["request_feedback"] = {"message": message, "object_ids": [], "at": _now(), "scene_revision": self.store.state["scene"]["revision"]}
            self.store._save()
            self.store.workspace_event("external_feedback_requested", {"session_id": workspace["session_id"], "cursor": cursor, "message": message or ""})
            return {"session_id": workspace["session_id"], "next_cursor": cursor, "scene_revision": self.store.state["scene"]["revision"], "delivery_mode": "external", "thread_id": current.get("thread_id")}

    def take_external_feedback(self, session_id: str, cursor: int) -> dict[str, Any]:
        if not self.external_review:
            raise APIError(409, "this workspace uses direct Codex delivery")
        if self.adapter is not None:
            raise APIError(409, "feedback is delivered directly to the bound Codex task")
        workspace = self.ensure()
        if session_id != workspace["session_id"]:
            raise APIError(409, "review session belongs to another workspace")
        with self.store.lock:
            result = self.store.feedback(session_id, cursor)
            if result["items"]:
                ids = {item["feedback_id"] for item in result["items"]}
                for item in self.store.state["workspace"]["queue"]:
                    if item["feedback_id"] in ids and item["status"] == "awaiting_mcp":
                        item["status"] = "returned_to_mcp"
                self.store.state["workspace"]["agent"] = {"status": "external_idle", "turn_id": None, "error": None}
                self.store._save()
                self.store.workspace_event("feedback_returned_to_mcp", {"feedback_ids": sorted(ids)})
            return result

    def confirm_queue(self, feedback_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        retry_requested = payload.get("retry_uncertain") is True or payload.get("retry_failed") is True
        if retry_requested:
            if self.adapter is None:
                raise APIError(503, "Codex App Server is unavailable")
            with self.store.lock:
                workspace = self.store.state["workspace"]
                item = next((entry for entry in workspace["queue"] if entry["feedback_id"] == feedback_id), None)
                if item is None:
                    raise APIError(404, "queued feedback not found")
                if item["status"] not in {"delivery_uncertain", "failed"}:
                    raise APIError(409, "feedback is not awaiting retry")
                if workspace.get("active_feedback_id") not in {None, feedback_id}:
                    raise APIError(409, "another feedback is active")
            try:
                turn, duplicate = self._matching_turn(feedback_id, item.get("turn_id"))
                if duplicate:
                    raise APIError(409, "multiple Codex turns already contain this feedback ID")
                if turn:
                    self._apply_reconciled_turn(feedback_id, turn)
                    raise APIError(409, "feedback already reached the Codex task; its status was reconciled")
                runtime_status = self.adapter.inspect_thread_status() if hasattr(self.adapter, "inspect_thread_status") else self.adapter.refresh().get("turn_state")
                if runtime_status != "idle":
                    raise APIError(409, "Codex task is active; retry after it becomes idle")
                if hasattr(self.adapter, "release_uncertain"):
                    self.adapter.release_uncertain()
            except APIError:
                raise
            except Exception as exc:
                raise APIError(503, f"cannot reconcile Codex thread before retry: {exc}") from exc
        with self.store.lock:
            workspace = self.store.state["workspace"]
            item = next((entry for entry in workspace["queue"] if entry["feedback_id"] == feedback_id), None)
            if item is None:
                raise APIError(404, "queued feedback not found")
            if item["status"] == "blocked_stale":
                if payload.get("confirm") is True:
                    item["status"] = "queued"
                    item["confirmed_against_revision"] = self.store.state["scene"]["revision"]
                    kind = "stale_feedback_confirmed"
                elif payload.get("confirm") is False:
                    item["status"] = "discarded"
                    kind = "queued_feedback_discarded"
                else:
                    raise APIError(400, "confirm must be true or false")
            elif item["status"] == "delivery_uncertain":
                if payload.get("retry_uncertain") is True:
                    item["status"] = "queued"
                    item["error"] = None
                    item.pop("quarantined_at", None)
                    item.pop("uncertain_since", None)
                    if workspace.get("active_feedback_id") == feedback_id:
                        workspace["active_feedback_id"] = None
                        workspace["agent"] = {"status": "idle", "turn_id": None, "error": None}
                    kind = "uncertain_feedback_retry_requested"
                elif payload.get("retry_uncertain") is False:
                    item["status"] = "discarded"
                    if workspace.get("active_feedback_id") == feedback_id:
                        workspace["active_feedback_id"] = None
                        workspace["agent"] = {"status": "idle", "turn_id": None, "error": None}
                    kind = "uncertain_feedback_discarded"
                else:
                    raise APIError(400, "retry_uncertain must be true or false")
            elif item["status"] == "failed" and payload.get("retry_failed") is True:
                item["status"] = "queued"
                item["error"] = None
                item.pop("uncertain_since", None)
                item.pop("quarantined_at", None)
                kind = "failed_feedback_retry_requested"
            else:
                raise APIError(409, "feedback is not awaiting confirmation")
            self.store._save()
            self.store.workspace_event(kind, {"feedback_id": feedback_id})
            result = copy.deepcopy(item)
        self.wake()
        return result

    def wake(self) -> None:
        if not self._started or self.adapter is None:
            return
        with self._worker_lock:
            if self._worker_running:
                return
            self._worker_running = True
            self._worker_thread = threading.Thread(target=self._dispatch, daemon=True, name="workspace-dispatch")
            self._worker_thread.start()

    def _dispatch(self) -> None:
        send_attempted = False
        defer_retry = False
        try:
            if not self.adapter.status().get("connected"):
                if self.external_review:
                    # The supervisor reconnects periodically. Do not claim a
                    # queued packet while the transport is unavailable.
                    return
                try:
                    thread_id = self.adapter.start()
                    self.store.workspace_thread(thread_id)
                    with self.store.lock:
                        if not self.store.state["workspace"].get("active_feedback_id"):
                            self.store.state["workspace"]["agent"] = {"status": "idle", "turn_id": None, "error": None}
                            self.store._save()
                except Exception as exc:
                    self.store.workspace_agent(status="disconnected", error=str(exc)[:500])
                    self.store.workspace_event("disconnected", {"message": str(exc)[:500]})
                    return
            with self.store.lock:
                workspace = self.store.state["workspace"]
                if workspace.get("active_feedback_id"):
                    return
                # A feedback captured against an older scene needs a human
                # decision, but must not hold up newer feedback that already
                # targets the current revision.
                item = next((entry for entry in workspace["queue"] if entry["status"] == "queued"), None)
                if item is None:
                    return
                revision = self.store.state["scene"]["revision"]
                if item["scene_revision"] != revision and item.get("confirmed_against_revision") != revision:
                    item["status"] = "blocked_stale"
                    self.store._save()
                    self.store.workspace_event("stale_feedback_confirmation_required", {"feedback_id": item["feedback_id"], "captured_revision": item["scene_revision"], "current_revision": revision})
                    return
                item["status"] = "dispatching"
                item["error"] = None
                workspace["active_feedback_id"] = item["feedback_id"]
                workspace["agent"] = {"status": "running", "turn_id": None, "error": None}
                self.store._save()
                feedback = next(entry for entry in self.store.state["feedback"] if entry["feedback_id"] == item["feedback_id"])
                feedback = copy.deepcopy(feedback)
                if item["scene_revision"] != revision:
                    feedback["submitted_from_stale_snapshot"] = True
            text, image_paths = self._turn_input(feedback)
            send_attempted = True
            response = self.adapter.start_turn(text, image_paths, message_id=feedback["feedback_id"])
            with self.store.lock:
                workspace = self.store.state["workspace"]
                current = next(entry for entry in workspace["queue"] if entry["feedback_id"] == feedback["feedback_id"])
                if current["status"] == "dispatching":
                    current["status"] = "running"
                    current["turn_id"] = response.get("turn_id")
                    current["error"] = None
                    current.pop("uncertain_since", None)
                    workspace["agent"] = {"status": "running", "turn_id": response.get("turn_id"), "error": None}
                    self.store._save()
                    self.store.workspace_event("turn_started", {"feedback_id": feedback["feedback_id"], "turn_id": response.get("turn_id")})
        except Exception as exc:
            error = str(exc)[:500]
            not_ready = isinstance(exc, (DeliveryNotReadyError, TurnBusyError, SharedThreadNotIdle))
            rejected = isinstance(exc, DeliveryRejectedError)
            uncertain = not not_ready and send_attempted and not rejected
            defer_retry = not_ready
            with self.store.lock:
                workspace = self.store.state["workspace"]
                active_id = workspace.get("active_feedback_id")
                item = next((entry for entry in workspace["queue"] if entry["feedback_id"] == active_id), None)
                if item:
                    item["status"] = "queued" if not_ready else "delivery_uncertain" if uncertain else "failed"
                    item["error"] = error
                    if uncertain:
                        item.setdefault("uncertain_since", _now())
                workspace["active_feedback_id"] = active_id if uncertain else None
                workspace["agent"] = {"status": "delivery_uncertain" if uncertain else "waiting" if not_ready else "error", "turn_id": None, "error": error}
                self.store._save()
                self.store.workspace_event("delivery_uncertain" if uncertain else "delivery_waiting" if not_ready else "turn_failed", {"feedback_id": active_id, "message": error})
            if not not_ready:
                logging.exception("Could not deliver workspace feedback")
        finally:
            with self._worker_lock:
                self._worker_running = False
            with self.store.lock:
                workspace = self.store.state.get("workspace", {})
                pending = not workspace.get("active_feedback_id") and any(item["status"] == "queued" for item in workspace.get("queue", []))
            if pending and not defer_retry and self.adapter.status().get("connected"):
                self.wake()

    def _turn_input(self, feedback: dict[str, Any]) -> tuple[str, list[str]]:
        fallback = "（仅提供参考图，请据图开始或继续重建。）" if feedback.get("reference_images") and not feedback.get("annotations") else "（仅有视觉标记）"
        lines = ["用户通过 Visual Reconstruction Workspace 发送视觉反馈。", "项目根目录：" + str(self.project_dir), "用户原话：", feedback.get("note", "") or fallback, "", f"场景版本：{feedback['scene_revision']}", f"反馈 ID：{feedback['feedback_id']}"]
        if feedback.get("submitted_from_stale_snapshot"):
            lines.append("这份反馈针对较早的冻结场景截图。用户已确认继续发送；请依据截图和版本判断，不要把旧标记当成当前视角坐标。")
        if feedback.get("selected_object_ids"):
            lines.append("选中对象 ID：" + ", ".join(feedback["selected_object_ids"]))
        if feedback.get("selected_scene_nodes"):
            lines.append("选中 GLB 节点：" + json.dumps(feedback["selected_scene_nodes"], ensure_ascii=False))
        if feedback.get("camera"):
            lines.append("冻结视角：" + json.dumps(feedback["camera"], ensure_ascii=False))
        reference_names = {item["id"]: item["name"] for item in feedback.get("reference_images", [])}
        active_id = feedback.get("active_reference_id")
        aligned_id = feedback.get("aligned_reference_id")
        if active_id in reference_names:
            lines.append(f"当前查看的参考图：{reference_names[active_id]} (ID {active_id})")
        if aligned_id in reference_names:
            lines.append(f"场景截图已按这张参考图的标定相机视角对齐：{reference_names[aligned_id]} (ID {aligned_id})。请把两张图作为同一视角比较；镜头畸变和标定误差仍可能造成少量像素偏差。")
        if feedback.get("annotations"):
            lines.append("标记数据：" + json.dumps(feedback["annotations"], ensure_ascii=False))
        lines += ["", "附件顺序："]
        image_paths: list[str] = []

        def add(label: str, url: str) -> None:
            name = url.rsplit("/", 1)[-1]
            if not url.startswith("/media/") or not name or "/" in name:
                raise APIError(500, "stored image URL is invalid")
            path = (self.store.media_dir / name).resolve()
            if path.parent != self.store.media_dir or not path.is_file():
                raise APIError(500, "stored image is missing")
            image_paths.append(str(path))
            lines.append(f"{len(image_paths)}. {label}")

        annotated = {entry["reference_id"]: entry["url"] for entry in feedback.get("reference_annotated_images", [])}
        for reference in feedback.get("reference_images", []):
            add(f"参考原图 {reference['name']} (ID {reference['id']})", reference["url"])
            if reference["id"] == aligned_id and reference.get("alignment_image_url"):
                add(f"去畸变对齐图 {reference['name']} (ID {reference['id']})", reference["alignment_image_url"])
            if reference["id"] in annotated:
                add(f"带用户标记的参考图 {reference['name']} (ID {reference['id']})", annotated[reference["id"]])
        for key, label in (("scene_original_url", "冻结视角的干净场景截图"), ("scene_annotated_url", "带用户标记和高亮的场景截图")):
            if feedback.get(key):
                add(label, feedback[key])
        for crop in feedback.get("crops", []):
            add(f"{crop['source']} 局部放大图", crop["url"])
        lines += ["", "红线、箭头、编号、框和画笔痕迹是用户后画的提示，不是参考图中的真实几何。请结合图像和原话继续当前重建任务；修改完成后调用 workspace_publish_scene 发布新的 GLB。"]
        return "\n".join(lines), image_paths

    def on_adapter_event(self, event: dict[str, Any]) -> None:
        method = event.get("method", "")
        params = event.get("params") or {}
        try:
            if method == "adapter/request_pending":
                approval_id = __import__("uuid").uuid4().hex
                details = params.get("params", {})
                if not isinstance(details, dict):
                    details = {}
                encoded = json.dumps(details, ensure_ascii=False, allow_nan=False)
                too_large = len(encoded.encode("utf-8")) > 64 * 1024
                questions = details.get("questions")
                question_ids = None
                if isinstance(questions, list) and len(questions) <= 100 and all(isinstance(question, dict) and isinstance(question.get("id"), str) and question["id"] for question in questions):
                    candidate_ids = [question["id"] for question in questions]
                    if len(json.dumps(candidate_ids, ensure_ascii=False).encode("utf-8")) <= 4096:
                        question_ids = candidate_ids
                request = {
                    "approval_id": approval_id,
                    "request_id": params.get("request_id"),
                    "kind": params.get("method"),
                    "prompt": encoded[:4000],
                    "details": None if too_large else copy.deepcopy(details),
                    "details_truncated": too_large,
                    "question_ids": question_ids,
                    "at": _now(),
                }
                with self.store.lock:
                    workspace = self.store.state["workspace"]
                    workspace["approvals"].append(request)
                    workspace["agent"]["status"] = "awaiting_approval"
                    self.store._save()
                self.store.workspace_event("approval_requested", {"approval_id": approval_id, "kind": request["kind"], "prompt": request["prompt"], "details_truncated": too_large})
            elif method == "turn/completed":
                turn = params.get("turn") or {}
                turn_id = turn.get("id")
                outcome = turn.get("status", "completed")
                with self.store.lock:
                    workspace = self.store.state["workspace"]
                    active_id = workspace.get("active_feedback_id")
                    item = next((entry for entry in workspace["queue"] if entry["feedback_id"] == active_id), None)
                    known_turn_id = item.get("turn_id") if item else None
                    if not known_turn_id or not turn_id or known_turn_id != turn_id:
                        self.store.workspace_event("codex_event", {"method": method, "turn_id": turn_id, "status": outcome, "message": "completion did not match a known active turn"})
                        return
                    if item:
                        item["status"] = outcome if outcome in {"completed", "failed", "interrupted"} else "failed"
                        item["turn_id"] = turn_id or item.get("turn_id")
                    workspace["active_feedback_id"] = None
                    workspace["approvals"] = []
                    workspace["agent"] = {"status": "idle", "turn_id": None, "error": None}
                    self.store._save()
                self.store.workspace_event("turn_completed" if outcome == "completed" else "turn_failed", {"feedback_id": active_id, "turn_id": turn_id, "status": outcome})
                self.wake()
            elif method == "turn/started":
                turn = params.get("turn") or {}
                with self.store.lock:
                    workspace = self.store.state["workspace"]
                    active_id = workspace.get("active_feedback_id")
                    item = next((entry for entry in workspace["queue"] if entry["feedback_id"] == active_id), None)
                    if item and item["status"] == "dispatching" and turn.get("id"):
                        item["turn_id"] = turn["id"]
                        workspace["agent"]["turn_id"] = turn["id"]
                        self.store._save()
                self.store.workspace_event("codex_event", {"method": method, "turn_id": turn.get("id")})
            elif method == "adapter/disconnected":
                with self.store.lock:
                    workspace = self.store.state["workspace"]
                    active_id = workspace.get("active_feedback_id")
                    if active_id:
                        item = next((entry for entry in workspace["queue"] if entry["feedback_id"] == active_id), None)
                        if item and item["status"] in {"dispatching", "running"}:
                            item["status"] = "delivery_uncertain"
                            item["error"] = "Codex App Server disconnected during a turn; inspect the thread before retrying."
                            item.setdefault("uncertain_since", _now())
                        workspace["agent"] = {"status": "delivery_uncertain", "turn_id": item.get("turn_id") if item else None, "error": item.get("error") if item else None}
                    else:
                        workspace["agent"] = {"status": "disconnected", "turn_id": None, "error": "Codex App Server disconnected"}
                    workspace["approvals"] = []
                    self.store._save()
                self.store.workspace_event("delivery_uncertain" if active_id else "disconnected", {"feedback_id": active_id, "message": "Codex App Server disconnected"})
            elif method == "adapter/reconnected":
                thread_id = params.get("thread_id")
                if thread_id:
                    self.store.workspace_thread(thread_id)
                with self.store.lock:
                    active_id = self.store.state["workspace"].get("active_feedback_id")
                if not active_id:
                    self.store.workspace_agent(status="idle")
                self.store.workspace_event("reconnected", {})
                if not active_id:
                    self.wake()
            elif method == "adapter/empty_thread_recreated":
                thread_id = params.get("thread_id")
                if thread_id:
                    self.store.workspace_thread(thread_id)
            elif method == "item/completed":
                item = params.get("item") if isinstance(params, dict) else None
                if isinstance(item, dict) and item.get("type") == "agentMessage":
                    message = item.get("text")
                    if isinstance(message, str) and message:
                        self.store.workspace_event("assistant_message", {"text": message[:30_000]})
                else:
                    summary = {"method": method, "item_type": item.get("type") if isinstance(item, dict) else None}
                    if isinstance(item, dict) and isinstance(item.get("command"), str):
                        summary["command"] = item["command"][:1000]
                    self.store.workspace_event("codex_event", summary)
            elif method == "error":
                self.store.workspace_event("codex_event", {"method": method, "preview": json.dumps(params, ensure_ascii=False)[:4000]})
        except Exception:
            logging.exception("Could not process Codex App Server event")

    def respond_to_approval(self, approval_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.adapter is None:
            raise APIError(503, "Codex App Server is unavailable")
        if not isinstance(payload, dict):
            raise APIError(400, "approval response must be a JSON object")
        with self.store.lock:
            request = next((entry for entry in self.store.state["workspace"]["approvals"] if entry["approval_id"] == approval_id), None)
            if request is None:
                raise APIError(404, "approval request not found")
            request = copy.deepcopy(request)
        decision = payload.get("decision")
        kind = request["kind"]
        details = request.get("details")
        if details is None and not request.get("details_truncated"):
            try:
                details = json.loads(request.get("prompt", ""))
            except (TypeError, ValueError):
                details = {}
        if not isinstance(details, dict):
            details = {}
        supported_kinds = {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
            "mcpServer/elicitation/request",
            "item/permissions/requestApproval",
            "item/tool/requestUserInput",
        }
        if request.get("details_truncated") and kind not in supported_kinds:
            raise APIError(422, "unsupported request has oversized details; interrupt this turn from the workbench")
        if request.get("details_truncated") and decision not in {"decline", "cancel"}:
            raise APIError(413, "approval details are too large to inspect in the workbench; accepting is unavailable")
        if kind in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
            if decision not in {"accept", "acceptForSession", "decline", "cancel"}:
                raise APIError(400, "decision must be accept, acceptForSession, decline or cancel")
            result = {"decision": decision}
        elif kind == "mcpServer/elicitation/request":
            if decision not in {"accept", "decline", "cancel"}:
                raise APIError(400, "decision must be accept, decline or cancel")
            result = {"action": decision}
            if decision == "accept":
                mode = details.get("mode")
                if mode in {"url", "openai/userVerification"}:
                    if mode == "url" and (not isinstance(details.get("url"), str) or not details["url"]):
                        raise APIError(400, "URL elicitation has no usable URL")
                    if "content" in payload:
                        raise APIError(400, "URL elicitation accept must not include form content")
                elif mode in {"form", "openai/form", "openaiForm"}:
                    schema = details.get("requestedSchema")
                    if not isinstance(schema, dict):
                        raise APIError(400, "form elicitation has no requested schema")
                    content = payload.get("content")
                    if content is None and schema.get("type") == "object" and not schema.get("properties") and not schema.get("required"):
                        content = {}
                    if not isinstance(content, dict):
                        raise APIError(400, "accepted elicitation needs form content")
                    self._validate_form_content(schema, content, strict=mode == "form")
                    result["content"] = copy.deepcopy(content)
                else:
                    raise APIError(400, "unsupported elicitation mode")
        elif kind == "item/permissions/requestApproval":
            if decision not in {"accept", "decline", "cancel"}:
                raise APIError(400, "permission decision must be accept, decline or cancel")
            if decision == "accept":
                requested = details.get("permissions")
                if not isinstance(requested, dict):
                    raise APIError(400, "permission request has no profile to inspect")
                submitted = payload.get("permissions", requested)
                if submitted != requested:
                    raise APIError(400, "granted permissions must exactly match the inspected request")
                scope = payload.get("scope", "turn")
                if scope not in {"turn", "session"}:
                    raise APIError(400, "permission scope must be turn or session")
                result = {"permissions": copy.deepcopy(requested), "scope": scope}
            else:
                # The 0.156.1 response has no decline flag. An empty granted
                # profile grants none of the requested extra permissions.
                result = {"permissions": {}, "scope": "turn"}
        elif kind == "item/tool/requestUserInput":
            if decision not in {"accept", "decline", "cancel"}:
                raise APIError(400, "user-input decision must be accept, decline or cancel")
            questions = details.get("questions")
            if isinstance(questions, list) and all(isinstance(question, dict) and isinstance(question.get("id"), str) and question["id"] for question in questions):
                question_ids = [question["id"] for question in questions]
            elif request.get("details_truncated") and decision in {"decline", "cancel"}:
                question_ids = request.get("question_ids")
                if not isinstance(question_ids, list):
                    raise APIError(422, "cannot safely answer oversized user-input request; interrupt this turn from the workbench")
            else:
                raise APIError(400, "user-input request has no valid questions")
            if len(question_ids) != len(set(question_ids)):
                raise APIError(400, "user-input request has duplicate question IDs")
            if decision == "accept":
                answers = payload.get("answers")
                if not isinstance(answers, dict) or set(answers) != set(question_ids):
                    raise APIError(400, "answers must match every requested question ID")
                for question_id, answer in answers.items():
                    if not isinstance(answer, dict) or set(answer) != {"answers"} or not isinstance(answer["answers"], list) or not answer["answers"]:
                        raise APIError(400, f"question {question_id!r} needs answers")
                    if len(answer["answers"]) > 10 or any(not isinstance(value, str) or not 1 <= len(value) <= 4000 for value in answer["answers"]):
                        raise APIError(400, "user-input answers must be short text")
                result = {"answers": copy.deepcopy(answers)}
            else:
                result = {"answers": {question_id: {"answers": []} for question_id in question_ids}}
        else:
            result = payload.get("result")
            if not isinstance(result, dict):
                raise APIError(422, "unsupported request needs a structured result; interrupt this turn from the workbench")
        try:
            encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise APIError(400, "approval response contains invalid JSON values") from exc
        if len(encoded.encode("utf-8")) > 64 * 1024:
            raise APIError(413, "approval response is too large")
        try:
            self.adapter.respond_to_request(request["request_id"], result)
        except ValueError as exc:
            raise APIError(400, str(exc)) from exc
        with self.store.lock:
            workspace = self.store.state["workspace"]
            workspace["approvals"] = [entry for entry in workspace["approvals"] if entry["approval_id"] != approval_id]
            workspace["agent"]["status"] = "running"
            self.store._save()
        self.store.workspace_event("approval_resolved", {"approval_id": approval_id, "decision": decision or "structured"})
        return {"approval_id": approval_id, "status": "responded"}

    @staticmethod
    def _validate_form_content(schema: dict[str, Any], content: dict[str, Any], *, strict: bool) -> None:
        required = schema.get("required") or []
        if not isinstance(required, list) or any(not isinstance(key, str) for key in required):
            raise APIError(400, "elicitation schema has invalid required fields")
        if any(key not in content for key in required):
            raise APIError(400, "form content is missing a required field")
        if not strict:
            return
        properties = schema.get("properties") or {}
        if not isinstance(properties, dict) or any(key not in properties for key in content):
            raise APIError(400, "form content contains an unrequested field")
        for key, value in content.items():
            field = properties[key]
            if not isinstance(field, dict):
                raise APIError(400, "elicitation schema has an invalid field")
            kind = field.get("type")
            if kind == "string" and not isinstance(value, str):
                raise APIError(400, f"form field {key!r} must be text")
            if kind == "boolean" and not isinstance(value, bool):
                raise APIError(400, f"form field {key!r} must be true or false")
            if kind in {"integer", "number"} and (isinstance(value, bool) or not isinstance(value, (int, float)) or (kind == "integer" and not isinstance(value, int))):
                raise APIError(400, f"form field {key!r} must be a {kind}")
            if kind == "array" and (not isinstance(value, list) or any(not isinstance(item, str) for item in value)):
                raise APIError(400, f"form field {key!r} must be a list of text values")
            if "enum" in field and value not in field["enum"]:
                raise APIError(400, f"form field {key!r} is not an offered option")
            if kind == "string" and isinstance(value, str):
                if isinstance(field.get("minLength"), int) and len(value) < field["minLength"]:
                    raise APIError(400, f"form field {key!r} is too short")
                if isinstance(field.get("maxLength"), int) and len(value) > field["maxLength"]:
                    raise APIError(400, f"form field {key!r} is too long")
                choices = field.get("oneOf")
                if isinstance(choices, list) and value not in [option.get("const") for option in choices if isinstance(option, dict)]:
                    raise APIError(400, f"form field {key!r} is not an offered option")
            if kind in {"integer", "number"} and isinstance(value, (int, float)) and not isinstance(value, bool):
                minimum = field.get("minimum")
                maximum = field.get("maximum")
                if isinstance(minimum, (int, float)) and not isinstance(minimum, bool) and value < minimum:
                    raise APIError(400, f"form field {key!r} violates minimum")
                if isinstance(maximum, (int, float)) and not isinstance(maximum, bool) and value > maximum:
                    raise APIError(400, f"form field {key!r} violates maximum")
            if kind == "array" and isinstance(value, list):
                if isinstance(field.get("minItems"), int) and len(value) < field["minItems"]:
                    raise APIError(400, f"form field {key!r} has too few choices")
                if isinstance(field.get("maxItems"), int) and len(value) > field["maxItems"]:
                    raise APIError(400, f"form field {key!r} has too many choices")
                item_schema = field.get("items")
                if isinstance(item_schema, dict):
                    choices = item_schema.get("enum")
                    if not isinstance(choices, list) and isinstance(item_schema.get("anyOf"), list):
                        choices = [option.get("const") for option in item_schema["anyOf"] if isinstance(option, dict)]
                    if isinstance(choices, list) and any(item not in choices for item in value):
                        raise APIError(400, f"form field {key!r} contains an unoffered choice")

    def interrupt(self) -> dict[str, Any]:
        if self.adapter is None:
            raise APIError(503, "Codex App Server is unavailable")
        with self.store.lock:
            active_id = self.store.state["workspace"].get("active_feedback_id")
            turn_id = self.store.state["workspace"]["agent"].get("turn_id")
        if not active_id:
            raise APIError(409, "there is no active Codex turn")
        response = self.adapter.interrupt(turn_id)
        self.store.workspace_event("interrupt_requested", {"feedback_id": active_id, "turn_id": turn_id})
        return response

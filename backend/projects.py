"""Persistent project catalog; each project keeps its own store and gateway."""

from __future__ import annotations

from dataclasses import dataclass
import hmac
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any, Callable
from urllib.parse import urlencode
import uuid

from core import APIError, SceneStore
from gateway import WorkspaceGateway


PROJECT_ID = re.compile(r"^[0-9a-f]{32}$")


@dataclass
class ProjectContext:
    project_id: str
    name: str
    store: SceneStore
    gateway: WorkspaceGateway
    data_lock: Any = None

    @property
    def project_dir(self) -> Path:
        return self.gateway.project_dir


class ProjectRegistry:
    def __init__(
        self,
        root: ProjectContext,
        *,
        context_factory: Callable[[dict[str, Any], bool], ProjectContext],
        configure_context: Callable[[ProjectContext], None],
    ):
        self.root = root
        self.path = root.store.data_dir / "project_registry.json"
        self.managed_dir = root.store.data_dir / "projects"
        self._lock = threading.RLock()
        self.target_binding_lock = threading.RLock()
        self._factory = context_factory
        self._configure = configure_context
        self._contexts = {root.project_id: root}
        self._closed = False
        root_record = {
            "project_id": root.project_id, "name": root.name,
            "project_dir": str(root.project_dir), "data_dir": str(root.store.data_dir),
            "creation_status": "ready",
        }
        if self.path.exists():
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if document.get("schema_version") != 1 or document.get("default_project_id") != root.project_id:
                raise RuntimeError("project registry does not match the default workspace")
            records = document.get("projects")
            if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
                raise RuntimeError("invalid project registry")
            self._records: dict[str, dict[str, Any]] = {}
            requests: set[str] = set()
            for record in records:
                project_id = record.get("project_id")
                if not isinstance(project_id, str) or not PROJECT_ID.fullmatch(project_id) or project_id in self._records:
                    raise RuntimeError("invalid or duplicate project ID in registry")
                request_id = record.get("request_id")
                if request_id is not None:
                    if not isinstance(request_id, str) or not PROJECT_ID.fullmatch(request_id) or request_id in requests:
                        raise RuntimeError("invalid or duplicate creation request in registry")
                    requests.add(request_id)
                self._records[project_id] = dict(record)
            saved_root = self._records.get(root.project_id)
            if saved_root is None or any(saved_root.get(key) != root_record[key] for key in ("project_dir", "data_dir")):
                raise RuntimeError("default project directories changed in registry")
            root.name = saved_root["name"]
        else:
            self._records = {root.project_id: root_record}
            self._save()
        self._configure(root)
        self._install_binding_guard(root)
        try:
            for project_id, record in self._records.items():
                if project_id == root.project_id:
                    continue
                self._validate_managed_paths(record)
                context = None
                try:
                    context = self._factory(record, False)
                    self._contexts[project_id] = context
                    self._configure(context)
                    self._install_binding_guard(context)
                    context.gateway.start()
                except Exception as exc:
                    # A partially created or unavailable child must stay visible
                    # without replacing the existing default workspace.
                    record["creation_status"] = "unavailable"
                    record["creation_error"] = str(exc)[:500]
                    record["creation_http_status"] = 503
                    logging.exception("Could not restore reconstruction project %s", project_id)
                    self._contexts.pop(project_id, None)
                    if context is not None:
                        try:
                            context.gateway.close()
                        except Exception:
                            logging.exception("Could not close unavailable reconstruction project %s", project_id)
                        finally:
                            if context.data_lock is not None:
                                try:
                                    context.data_lock.close()
                                except Exception:
                                    logging.exception("Could not release unavailable project lock %s", project_id)
                    continue
                if record.get("creation_status") == "creating":
                    record["creation_status"] = "uncertain"
                    record["creation_error"] = "project creation was interrupted; inspect its Codex task before creating another"
                    record["creation_http_status"] = 503
                self._refresh_creation(context)
            self._save()
        except BaseException:
            self.close()
            raise

    def _save(self) -> None:
        document = {"schema_version": 1, "default_project_id": self.root.project_id,
                    "projects": list(self._records.values())}
        descriptor, temporary = tempfile.mkstemp(prefix="projects-", suffix=".json", dir=self.root.store.data_dir)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(document, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _validate_managed_paths(self, record: dict[str, Any]) -> None:
        base = self.managed_dir / record["project_id"]
        for key, leaf in (("project_dir", "workspace"), ("data_dir", "data")):
            expected = base / leaf
            if record.get(key) != str(expected) or expected.resolve() != expected:
                raise RuntimeError("managed project directories must remain inside the registry data directory")
        if not isinstance(record.get("name"), str) or not record["name"].strip():
            raise RuntimeError("managed project has no name")

    def _install_binding_guard(self, context: ProjectContext) -> None:
        context.gateway.target_binding_lock = self.target_binding_lock
        context.gateway.target_validator = lambda thread_id: self.validate_target(context.project_id, thread_id)

    def validate_target(self, project_id: str, thread_id: str) -> None:
        # Called while the shared binding lock is held. Do not take the catalog
        # lock: creation holds it while waiting for a task binding to complete.
        for record in tuple(self._records.values()):
            if record["project_id"] != project_id and (
                thread_id in (record.get("thread_id"), record.get("created_thread_id"))
                or thread_id in record.get("created_thread_ids", [])
            ):
                raise APIError(409, "Codex task belongs to another reconstruction project")
        for context in tuple(self._contexts.values()):
            if context.project_id == project_id:
                continue
            with context.store.lock:
                workspace = context.store.state.get("workspace", {})
                claimed = workspace.get("thread_id") == thread_id or thread_id in workspace.get("created_thread_ids", [])
            if claimed or self._records[context.project_id].get("created_thread_id") == thread_id:
                raise APIError(409, "Codex task belongs to another reconstruction project")

    def contexts(self) -> tuple[ProjectContext, ...]:
        with self._lock:
            return tuple(self._contexts.values())

    def get(self, project_id: str) -> ProjectContext:
        with self._lock:
            if self._closed:
                raise APIError(503, "reconstruction service is closing")
            context = self._contexts.get(project_id)
        if context is None:
            record = self._records.get(project_id)
            if record is not None:
                raise APIError(503, record.get("creation_error", "reconstruction project is unavailable"),
                               detail={"project": self._unavailable_metadata(record)})
            raise APIError(404, "reconstruction project not found")
        return context

    def by_control_token(self, token: str) -> ProjectContext:
        for context in self.contexts():
            if hmac.compare_digest(token, context.store.control_token):
                return context
        raise APIError(403, "invalid local MCP control key")

    def accepts_browser_token(self, token: str) -> bool:
        return any(hmac.compare_digest(token, context.store.browser_token) for context in self.contexts())

    def _refresh_creation(self, context: ProjectContext) -> None:
        record = self._records[context.project_id]
        if "workspace" not in context.store.state:
            return
        workspace = context.store.workspace()
        record.update({key: workspace.get(key) for key in ("session_id", "thread_id", "scene_revision")})
        record["created_thread_ids"] = list(workspace.get("created_thread_ids", []))
        if workspace.get("thread_id") and record.get("creation_status") != "ready":
            record["creation_status"] = "ready"
            record["created_thread_id"] = workspace["thread_id"]
            record.pop("creation_error", None)
            record.pop("creation_http_status", None)

    def metadata(self, context: ProjectContext) -> dict[str, Any]:
        context.gateway.ensure()
        workspace = context.store.workspace()
        record = self._records[context.project_id]
        result = {
            "project_id": context.project_id, "name": context.name,
            "project_dir": str(context.project_dir), "session_id": workspace["session_id"],
            "thread_id": workspace.get("thread_id"), "scene_revision": workspace["scene_revision"],
            "creation_status": record.get("creation_status", "ready"),
            "url": f"/p/{context.project_id}/?{urlencode({'session_id': workspace['session_id']})}",
        }
        for key in ("creation_error", "created_thread_id", "request_id"):
            if record.get(key):
                result[key] = record[key]
        return result

    def _unavailable_metadata(self, record: dict[str, Any]) -> dict[str, Any]:
        result = {key: record.get(key) for key in (
            "project_id", "name", "project_dir", "session_id", "thread_id", "scene_revision", "creation_status"
        )}
        query = urlencode({"session_id": record["session_id"]}) if record.get("session_id") else ""
        result["url"] = f"/p/{record['project_id']}/" + ("?" + query if query else "")
        for key in ("creation_error", "created_thread_id", "request_id"):
            if record.get(key):
                result[key] = record[key]
        return result

    def list(self, current_project_id: str) -> dict[str, Any]:
        with self._lock:
            before = json.dumps(self._records, sort_keys=True)
            for context in self._contexts.values():
                self._refresh_creation(context)
            if before != json.dumps(self._records, sort_keys=True):
                self._save()
            return {"projects": [self.metadata(self._contexts[project_id]) if project_id in self._contexts
                                 else self._unavailable_metadata(record) for project_id, record in self._records.items()],
                    "current_project_id": current_project_id}

    @staticmethod
    def _creation_spec(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if set(payload) - {"name", "model", "reasoning_effort", "permission_mode", "request_id"}:
            raise APIError(400, "project creation accepts name and Codex settings, not host directory paths")
        request_id = payload.get("request_id")
        if not isinstance(request_id, str) or not PROJECT_ID.fullmatch(request_id):
            raise APIError(400, "request_id must be a 32-character lowercase UUID hex value")
        name, model = payload.get("name"), payload.get("model")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120 or any(ord(ch) < 32 for ch in name):
            raise APIError(400, "scene name must contain 1 to 120 printable characters")
        if not isinstance(model, str) or not model.strip():
            raise APIError(400, "select a Codex model")
        permission = payload.get("permission_mode", "workspace_write")
        if not isinstance(permission, str) or permission not in {"full_access", "workspace_write", "read_only"}:
            raise APIError(400, "permission_mode must be full_access, workspace_write, or read_only")
        effort = payload.get("reasoning_effort")
        if effort is not None and not isinstance(effort, str):
            raise APIError(400, "reasoning_effort must be a supported value")
        return request_id, {"name": name.strip(), "model": model.strip(), "reasoning_effort": effort,
                            "permission_mode": permission}

    def _creation_result(self, context: ProjectContext) -> dict[str, Any]:
        record = self._records[context.project_id]
        workspace = context.gateway.state(include_capability=True)
        if hasattr(context.gateway, "browser_url"):
            workspace["browser_url"] = context.gateway.browser_url(workspace["session_id"])
        result = {"project": self.metadata(context), "workspace": workspace}
        if record.get("creation_status") in {"failed", "uncertain", "creating", "unavailable"}:
            raise APIError(record.get("creation_http_status", 503), record.get("creation_error", "project creation is not confirmed"), detail=result)
        return result

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_id, spec = self._creation_spec(payload)
        with self._lock:
            if self._closed:
                raise APIError(503, "reconstruction service is closing")
            for record in self._records.values():
                if record.get("request_id") == request_id:
                    if record.get("creation_spec") != spec:
                        raise APIError(409, "request_id was already used with different project settings")
                    context = self._contexts.get(record["project_id"])
                    if context is None:
                        raise APIError(record.get("creation_http_status", 503), record.get("creation_error", "project creation is not confirmed"),
                                       detail={"project": self._unavailable_metadata(record)})
                    self._refresh_creation(context)
                    self._save()
                    return self._creation_result(context)
            project_id = uuid.uuid4().hex
            base = self.managed_dir / project_id
            record = {"project_id": project_id, "name": spec["name"],
                      "project_dir": str(base / "workspace"), "data_dir": str(base / "data"),
                      "creation_status": "creating", "request_id": request_id, "creation_spec": spec}
            self._validate_managed_paths(record)
            self._records[project_id] = record
            self._save()  # Persist intent before any task/start request.
            context: ProjectContext | None = None
            try:
                context = self._factory(record, True)
                self._contexts[project_id] = context
                self._configure(context)
                self._install_binding_guard(context)
                self._refresh_creation(context)
                self._save()
                context.gateway.start()
                result = context.gateway.create_target(spec["model"], reasoning_effort=spec["reasoning_effort"],
                                                       permission_mode=spec["permission_mode"], title=spec["name"])
                record["created_thread_id"] = result["thread_id"]
                record["creation_status"] = "ready"
                self._refresh_creation(context)
                if context.gateway._supervisor_thread is None:
                    context.gateway.start()
                self._save()
                return self._creation_result(context)
            except Exception as exc:
                record["creation_status"] = "uncertain" if "uncertain" in str(exc).lower() else "failed"
                record["creation_error"] = exc.message if isinstance(exc, APIError) else str(exc)[:500]
                record["creation_http_status"] = exc.status if isinstance(exc, APIError) else 503
                if isinstance(exc, APIError) and isinstance(exc.detail, dict) and exc.detail.get("thread_id"):
                    record["created_thread_id"] = exc.detail["thread_id"]
                if context is not None:
                    workspace = context.store.workspace()
                    record.update({key: workspace.get(key) for key in ("session_id", "thread_id", "scene_revision")})
                    record["created_thread_ids"] = list(workspace.get("created_thread_ids", []))
                    if workspace.get("created_thread_ids") and not record.get("created_thread_id"):
                        record["created_thread_id"] = workspace["created_thread_ids"][-1]
                self._save()
                if context is not None:
                    return self._creation_result(context)
                raise APIError(record["creation_http_status"], record["creation_error"],
                               detail={"project": self._unavailable_metadata(record)}) from exc

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            contexts = tuple(self._contexts.values())
        first_error: BaseException | None = None
        for context in reversed(contexts):
            try:
                context.gateway.close()
            except BaseException as exc:
                first_error = first_error or exc
            finally:
                if context.data_lock is not None:
                    try:
                        context.data_lock.close()
                    except BaseException as exc:
                        first_error = first_error or exc
        if first_error is not None:
            raise first_error

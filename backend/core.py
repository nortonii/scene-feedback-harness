"""Persistent scene and human feedback state for the local 3D review harness."""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import math
import os
import re
import shutil
import struct
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
ASSET_RE = re.compile(r"^/assets/[0-9a-f]{32}\.glb$")
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
MAX_MODEL_BYTES = 100 * 1024 * 1024
MAX_SCREENSHOT_BYTES = 4 * 1024 * 1024


class APIError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _vector(value: Any, name: str, *, positive: bool = False) -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        raise APIError(400, f"{name} must be a three-number array")
    result = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
            raise APIError(400, f"{name} must contain finite numbers")
        if abs(item) > 1_000_000 or (positive and item <= 0):
            raise APIError(400, f"{name} has an out-of-range value")
        result.append(float(item))
    return result


def _safe_json(value: Any, *, depth: int = 0) -> None:
    if depth > 12:
        raise APIError(400, "feedback is nested too deeply")
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise APIError(400, "numbers must be finite")
        return
    if isinstance(value, str):
        if len(value) > 32_000:
            raise APIError(400, "text field is too long")
        return
    if isinstance(value, list):
        if len(value) > 1000:
            raise APIError(400, "array is too long")
        for item in value:
            _safe_json(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 100:
            raise APIError(400, "object has too many fields")
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 100:
                raise APIError(400, "invalid field name")
            _safe_json(item, depth=depth + 1)
        return
    raise APIError(400, "value is not valid JSON")


def _default_objects() -> list[dict[str, Any]]:
    """A small editable chair makes the harness useful immediately."""
    return [
        {"id": "chair_seat", "name": "Seat", "type": "box", "position": [0, 0, 0.91], "size": [1.25, 1.15, 0.18], "rotation": [0, 0, 0], "color": "#d68b5b"},
        {"id": "chair_back", "name": "Backrest", "type": "box", "position": [0, 0.51, 1.54], "size": [1.25, 0.16, 1.42], "rotation": [0, 0, 0], "color": "#cd785a"},
        *[
            {"id": f"chair_leg_{index}", "name": f"Leg {index}", "type": "box", "position": [x, y, 0.45], "size": [0.13, 0.13, 0.9], "rotation": [0, 0, 0], "color": "#6d5143"}
            for index, (x, y) in enumerate(((-0.49, -0.43), (0.49, -0.43), (-0.49, 0.43), (0.49, 0.43)), 1)
        ],
    ]


class SceneStore:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.assets_dir = self.data_dir / "assets"
        self.screenshots_dir = self.data_dir / "screenshots"
        self.state_path = self.data_dir / "state.json"
        self.token_path = self.data_dir / "control_token"
        self.lock = threading.RLock()
        for directory in (self.data_dir, self.assets_dir, self.screenshots_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.control_token = self._control_token()
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        else:
            self.state = {"schema_version": 1, "scene": {"revision": 1, "objects": _default_objects()}, "sessions": {}, "feedback": []}
            self._save()

    def _control_token(self) -> str:
        if self.token_path.exists():
            return self.token_path.read_text(encoding="ascii").strip()
        token = uuid.uuid4().hex + uuid.uuid4().hex
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(self.token_path, flags, 0o600)
            with os.fdopen(descriptor, "w", encoding="ascii") as handle:
                handle.write(token + "\n")
        except FileExistsError:
            return self.token_path.read_text(encoding="ascii").strip()
        return token

    def _save(self) -> None:
        fd, temporary = tempfile.mkstemp(prefix="state-", suffix=".json", dir=self.data_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.state, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.state_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def scene(self) -> dict[str, Any]:
        with self.lock:
            return copy.deepcopy(self.state["scene"])

    def _validate_object(self, obj: Any) -> dict[str, Any]:
        if not isinstance(obj, dict):
            raise APIError(400, "each scene object must be an object")
        object_id = obj.get("id")
        if not isinstance(object_id, str) or not ID_RE.fullmatch(object_id):
            raise APIError(400, "object id must start with a letter and use letters, numbers, '_' or '-'")
        kind = obj.get("type")
        if kind not in {"box", "sphere", "cylinder", "model"}:
            raise APIError(400, "object type must be box, sphere, cylinder or model")
        name = obj.get("name", object_id)
        if not isinstance(name, str) or not 1 <= len(name) <= 120:
            raise APIError(400, "object name must contain 1 to 120 characters")
        position = _vector(obj.get("position"), "position")
        size = _vector(obj.get("size"), "size", positive=True)
        rotation = _vector(obj.get("rotation", [0, 0, 0]), "rotation")
        color = obj.get("color", "#aaaaaa")
        if not isinstance(color, str) or not COLOR_RE.fullmatch(color):
            raise APIError(400, "color must be a #rrggbb value")
        result = {"id": object_id, "name": name, "type": kind, "position": position, "size": size, "rotation": rotation, "color": color}
        if kind == "model":
            url = obj.get("url")
            if not isinstance(url, str) or not ASSET_RE.fullmatch(url):
                raise APIError(400, "model url must be an imported /assets/*.glb file")
            if not (self.assets_dir / url.rsplit("/", 1)[-1]).is_file():
                raise APIError(400, "model asset does not exist")
            result["url"] = url
        if "metadata" in obj:
            metadata = obj["metadata"]
            if not isinstance(metadata, dict):
                raise APIError(400, "metadata must be an object")
            _safe_json(metadata)
            if len(json.dumps(metadata, ensure_ascii=False)) > 16_000:
                raise APIError(400, "metadata is too large")
            result["metadata"] = metadata
        return result

    def _validate_objects(self, objects: Any) -> list[dict[str, Any]]:
        if not isinstance(objects, list) or len(objects) > 500:
            raise APIError(400, "objects must be an array with at most 500 items")
        validated = [self._validate_object(obj) for obj in objects]
        ids = [obj["id"] for obj in validated]
        if len(ids) != len(set(ids)):
            raise APIError(400, "scene object ids must be unique")
        return validated

    def replace_scene(self, expected_revision: int, objects: Any) -> dict[str, Any]:
        with self.lock:
            self._check_revision(expected_revision)
            validated = self._validate_objects(objects)
            self.state["scene"] = {"revision": expected_revision + 1, "objects": validated}
            self._save()
            return self.scene()

    def _check_revision(self, revision: Any) -> None:
        if type(revision) is not int or revision < 1:
            raise APIError(400, "expected_revision must be a positive integer")
        if revision != self.state["scene"]["revision"]:
            raise APIError(409, f"scene revision changed to {self.state['scene']['revision']}; reload and retry")

    def update_scene(self, expected_revision: int, changes: Any) -> dict[str, Any]:
        with self.lock:
            self._check_revision(expected_revision)
            if not isinstance(changes, list) or not 1 <= len(changes) <= 500:
                raise APIError(400, "changes must be a nonempty array with at most 500 items")
            objects = copy.deepcopy(self.state["scene"]["objects"])
            for change in changes:
                if not isinstance(change, dict):
                    raise APIError(400, "each change must be an object")
                op = change.get("op", "update")
                if op == "add":
                    objects.append(change.get("object"))
                elif op in {"update", "delete"}:
                    object_id = change.get("object_id")
                    index = next((i for i, obj in enumerate(objects) if obj["id"] == object_id), None)
                    if index is None:
                        raise APIError(404, f"scene object {object_id!r} not found")
                    if op == "delete":
                        objects.pop(index)
                    else:
                        fields = change.get("fields")
                        if not isinstance(fields, dict) or not fields:
                            raise APIError(400, "update change needs nonempty fields")
                        if "id" in fields or "type" in fields or "url" in fields:
                            raise APIError(400, "object id, type and model url cannot be changed in place")
                        if set(fields) - {"name", "position", "size", "rotation", "color", "metadata"}:
                            raise APIError(400, "update contains unsupported object fields")
                        objects[index].update(fields)
                else:
                    raise APIError(400, "change op must be add, update or delete")
            return self.replace_scene(expected_revision, objects)

    def create_session(self) -> dict[str, Any]:
        with self.lock:
            session_id = uuid.uuid4().hex
            session = {"session_id": session_id, "scene_revision": self.state["scene"]["revision"], "status": "open", "created_at": _now(), "feedback_count": 0}
            self.state["sessions"][session_id] = session
            self._save()
            return copy.deepcopy(session)

    def get_session(self, session_id: str) -> dict[str, Any]:
        with self.lock:
            session = self.state["sessions"].get(session_id)
            if session is None:
                raise APIError(404, "session not found")
            return copy.deepcopy(session)

    def list_sessions(self) -> list[dict[str, Any]]:
        with self.lock:
            return [copy.deepcopy(item) for item in self.state["sessions"].values()]

    def list_all_feedback(self) -> list[dict[str, Any]]:
        with self.lock:
            return copy.deepcopy(self.state["feedback"])

    def cancel_session(self, session_id: str) -> dict[str, Any]:
        with self.lock:
            session = self.state["sessions"].get(session_id)
            if session is None:
                raise APIError(404, "session not found")
            if session["status"] != "open":
                raise APIError(409, "session is already closed")
            session["status"] = "cancelled"
            session["closed_at"] = _now()
            self._save()
            return copy.deepcopy(session)

    def _store_screenshot(self, data_url: Any) -> str:
        if not isinstance(data_url, str):
            raise APIError(400, "screenshot_data_url must be a data URL")
        match = re.fullmatch(r"data:image/(png|jpeg);base64,([A-Za-z0-9+/=]+)", data_url)
        if not match or len(data_url) > MAX_SCREENSHOT_BYTES * 2:
            raise APIError(400, "screenshot must be a PNG or JPEG data URL up to 4 MB")
        try:
            payload = base64.b64decode(match.group(2), validate=True)
        except binascii.Error as exc:
            raise APIError(400, "invalid screenshot encoding") from exc
        if len(payload) > MAX_SCREENSHOT_BYTES:
            raise APIError(400, "screenshot is too large")
        extension = "png" if match.group(1) == "png" else "jpg"
        if extension == "png" and not payload.startswith(b"\x89PNG\r\n\x1a\n"):
            raise APIError(400, "invalid PNG screenshot")
        if extension == "jpg" and not payload.startswith(b"\xff\xd8\xff"):
            raise APIError(400, "invalid JPEG screenshot")
        name = f"{uuid.uuid4().hex}.{extension}"
        (self.screenshots_dir / name).write_bytes(payload)
        return f"/screenshots/{name}"

    def submit_feedback(self, session_id: str, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise APIError(400, "feedback must be a JSON object")
        with self.lock:
            session = self.state["sessions"].get(session_id)
            if session is None:
                raise APIError(404, "session not found")
            if session["status"] != "open":
                raise APIError(409, "session is already closed")
            revision = payload.get("scene_revision")
            if type(revision) is not int or revision < 1:
                raise APIError(400, "scene_revision must be a positive integer")
            if revision != self.state["scene"]["revision"]:
                raise APIError(409, f"scene revision changed to {self.state['scene']['revision']}; reload and resubmit")
            annotations = payload.get("annotations", [])
            if not isinstance(annotations, list) or len(annotations) > 100:
                raise APIError(400, "annotations must be an array with at most 100 items")
            object_ids = {obj["id"] for obj in self.state["scene"]["objects"]}
            normalized_annotations = []
            for annotation in annotations:
                if not isinstance(annotation, dict):
                    raise APIError(400, "each annotation must be an object")
                kind = annotation.get("type", annotation.get("operation"))
                if kind not in {"target_box", "guide_line", "move", "resize", "note"}:
                    raise APIError(400, "annotation type must be target_box, guide_line, move, resize or note")
                if "type" in annotation and "operation" in annotation and annotation["type"] != annotation["operation"]:
                    raise APIError(400, "annotation type and operation disagree")
                object_id = annotation.get("object_id")
                if kind != "note" and (not isinstance(object_id, str) or object_id not in object_ids):
                    raise APIError(400, "annotation refers to an unknown object id")
                if kind == "note" and object_id is not None and (not isinstance(object_id, str) or object_id not in object_ids):
                    raise APIError(400, "annotation refers to an unknown object id")
                if annotation.get("coordinate_frame") not in (None, "world", "object_local"):
                    raise APIError(400, "coordinate_frame must be world or object_local")
                required_vectors = {"target_box": ("center", "size"), "guide_line": ("start", "end"), "move": ("target_position",), "resize": ("target_size",), "note": ()}[kind]
                for vector_name in required_vectors:
                    _vector(annotation.get(vector_name), vector_name, positive=vector_name in {"size", "target_size"})
                if kind == "target_box" and annotation.get("anchor", "center") not in {"bottom", "center"}:
                    raise APIError(400, "target_box anchor must be bottom or center")
                if kind == "target_box" and "source_box" in annotation:
                    source_box = annotation["source_box"]
                    if not isinstance(source_box, dict):
                        raise APIError(400, "source_box must contain center and size")
                    _vector(source_box.get("center"), "source_box.center")
                    if any(value < 0 for value in _vector(source_box.get("size"), "source_box.size")):
                        raise APIError(400, "source_box.size cannot be negative")
                _safe_json(annotation)
                normalized = copy.deepcopy(annotation)
                normalized.pop("operation", None)
                normalized["type"] = kind
                normalized_annotations.append(normalized)
            note = payload.get("note", "")
            if not isinstance(note, str) or len(note) > 10_000:
                raise APIError(400, "note must be text up to 10000 characters")
            if not annotations and not note.strip():
                raise APIError(400, "add an annotation or a note before submitting")
            camera = payload.get("camera")
            if camera is not None:
                _safe_json(camera)
            screenshot_url = None
            if payload.get("screenshot_data_url"):
                screenshot_url = self._store_screenshot(payload["screenshot_data_url"])
            feedback = {"feedback_id": uuid.uuid4().hex, "session_id": session_id, "scene_revision": revision, "submitted_at": _now(), "annotations": normalized_annotations, "note": note}
            if camera is not None:
                feedback["camera"] = camera
            if screenshot_url:
                feedback["screenshot_url"] = screenshot_url
            self.state["feedback"].append(feedback)
            session["status"] = "submitted"
            session["closed_at"] = _now()
            session["feedback_count"] = 1
            self._save()
            return copy.deepcopy(feedback)

    def feedback(self, session_id: str, cursor: int = 0) -> dict[str, Any]:
        with self.lock:
            session = self.get_session(session_id)
            if type(cursor) is not int or cursor < 0:
                raise APIError(400, "cursor must be a nonnegative integer")
            items = [copy.deepcopy(item) for item in self.state["feedback"] if item["session_id"] == session_id]
            return {"session_id": session_id, "status": session["status"], "items": items[cursor:], "next_cursor": len(items)}

    def import_model(self, local_path: str, *, object_id: str | None = None, name: str | None = None, position: list[float] | None = None, size: list[float] | None = None) -> dict[str, Any]:
        if not isinstance(local_path, str) or not local_path:
            raise APIError(400, "local_path must be a file path")
        try:
            source = Path(local_path).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise APIError(400, "model file does not exist") from exc
        if not source.is_file() or source.suffix.lower() != ".glb":
            raise APIError(400, "model must be a .glb file")
        try:
            file_size = source.stat().st_size
        except OSError as exc:
            raise APIError(400, "cannot read model file") from exc
        if not 20 <= file_size <= MAX_MODEL_BYTES:
            raise APIError(400, "GLB must be between 20 bytes and 100 MB")
        try:
            with source.open("rb") as handle:
                header = handle.read(12)
                magic, version, declared_size = struct.unpack("<4sII", header)
                if magic != b"glTF" or version != 2 or declared_size != file_size:
                    raise APIError(400, "file is not a valid GLB 2.0 container")
                chunk_header = handle.read(8)
                if len(chunk_header) != 8:
                    raise APIError(400, "GLB is missing its JSON chunk")
                chunk_size, chunk_type = struct.unpack("<I4s", chunk_header)
                if chunk_type != b"JSON" or chunk_size > 16 * 1024 * 1024 or chunk_size % 4 or chunk_size > file_size - 20:
                    raise APIError(400, "GLB has an invalid JSON chunk")
                document = json.loads(handle.read(chunk_size))
                if not isinstance(document, dict) or document.get("asset", {}).get("version") != "2.0":
                    raise APIError(400, "GLB asset version must be 2.0")
        except (OSError, struct.error, ValueError, AttributeError) as exc:
            raise APIError(400, "file is not a valid GLB 2.0 container") from exc
        object_id = object_id or f"model_{uuid.uuid4().hex[:10]}"
        name = name or source.stem[:120]
        if not isinstance(object_id, str) or not ID_RE.fullmatch(object_id):
            raise APIError(400, "object id must start with a letter and use letters, numbers, '_' or '-'")
        if not isinstance(name, str) or not 1 <= len(name) <= 120:
            raise APIError(400, "object name must contain 1 to 120 characters")
        position = _vector(position if position is not None else [0, 0, 0], "position")
        size = _vector(size if size is not None else [1, 1, 1], "size", positive=True)
        with self.lock:
            if any(obj["id"] == object_id for obj in self.state["scene"]["objects"]):
                raise APIError(409, "object id already exists")
            file_name = f"{uuid.uuid4().hex}.glb"
            destination = self.assets_dir / file_name
            hasher = hashlib.sha256()
            with source.open("rb") as src, destination.open("xb") as dst:
                while chunk := src.read(1024 * 1024):
                    hasher.update(chunk)
                    dst.write(chunk)
            obj = self._validate_object({"id": object_id, "name": name, "type": "model", "url": f"/assets/{file_name}", "position": position or [0, 0, 0], "size": size or [1, 1, 1], "rotation": [0, 0, 0], "color": "#ffffff", "metadata": {"source_name": source.name, "sha256": hasher.hexdigest()}})
            scene = self.state["scene"]
            scene["objects"].append(obj)
            scene["revision"] += 1
            self._save()
            return {"object": copy.deepcopy(obj), "scene_revision": scene["revision"]}

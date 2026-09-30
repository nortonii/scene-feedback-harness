"""Lightweight exchange of externally generated 2D human-pose evidence.

This module exports exact reference snapshots, validates imported named joint
results and serves overlays. Inference belongs to the reconstruction skill.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any
import uuid

from core import APIError, _now, _safe_json
from dynamic import MAX_CLIP_FRAMES, number, reference_views

JOINT_NAMES = ["nose", "left_eye", "right_eye", "left_ear", "right_ear", "left_shoulder", "right_shoulder",
               "left_elbow", "right_elbow", "left_wrist", "right_wrist", "left_hip", "right_hip",
               "left_knee", "right_knee", "left_ankle", "right_ankle"]
SKELETON_EDGES = [[0, 1], [0, 2], [1, 3], [2, 4], [5, 6], [5, 7], [7, 9], [6, 8], [8, 10],
                  [5, 11], [6, 12], [11, 12], [11, 13], [13, 15], [12, 14], [14, 16]]
ID = re.compile(r"^[0-9a-f]{32}$")
MAX_VIEWS = 8
MAX_RESULT_FRAMES = MAX_VIEWS * MAX_CLIP_FRAMES
MAX_RESULT_BYTES = 256 * 1024 * 1024

def _atomic_json(path: Path, document: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix="pose-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _load_job(store: Any, job_id: Any) -> dict:
    if not isinstance(job_id, str) or not ID.fullmatch(job_id):
        raise APIError(400, "invalid pose job ID")
    path = store.data_dir / "human_pose" / job_id / "job.json"
    if not path.is_file():
        raise APIError(404, "human pose job not found in this project")
    return json.loads(path.read_text(encoding="utf-8"))


def _summary(job: dict) -> dict:
    return copy.deepcopy({key: value for key, value in job.items() if key not in {"sources", "frames", "request_digest"}})


def _sources(store: Any, job: dict) -> list[dict]:
    if job.get("automatic"):
        return json.loads((store.data_dir / "human_pose" / job["job_id"] / "sources.json").read_text(encoding="utf-8"))["sources"]
    return job["sources"]


def _frame_index(store: Any, job_id: str) -> dict:
    return json.loads((store.data_dir / "human_pose" / job_id / "frames_index.json").read_text(encoding="utf-8"))


def _read_archive_frames(store: Any, job_id: str, index: dict, first: int, count: int) -> list[dict]:
    offsets = index["offsets"]
    if count <= 0 or first >= len(offsets):
        return []
    result = []
    path = store.data_dir / "human_pose" / job_id / "frames.jsonl"
    with path.open("rb") as handle:
        handle.seek(offsets[first])
        for _ in range(min(count, len(offsets) - first)):
            result.append(json.loads(handle.readline()))
    return result


def _store_frame_archive(directory: Path, frames: list[dict]) -> None:
    """Write once, then seek by frame/reference/view without loading all results."""
    path = directory / "frames.jsonl"
    descriptor, temporary = tempfile.mkstemp(prefix="pose-frames-", suffix=".tmp", dir=directory)
    offsets: list[int] = []
    references: dict[str, int] = {}
    views: dict[str, dict[str, int]] = {}
    try:
        with os.fdopen(descriptor, "wb") as handle:
            for position, frame in enumerate(frames):
                offsets.append(handle.tell())
                references[frame["reference_id"]] = position
                view_id = frame.get("view_id")
                if view_id is not None:
                    view = views.setdefault(view_id, {"start": position, "count": 0})
                    if view["start"] + view["count"] != position:
                        raise ValueError("pose result frames for each view must be contiguous")
                    view["count"] += 1
                handle.write((json.dumps(frame, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _atomic_json(directory / "frames_index.json", {"offsets": offsets, "references": references, "views": views})
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _bbox(value: Any) -> list[float]:
    if not isinstance(value, list) or len(value) != 4:
        raise APIError(400, "bbox must be normalized [x,y,width,height]")
    result = [number(item, "bbox coordinate", maximum=1) for item in value]
    x, y, width, height = result
    if width < .01 or height < .01 or x + width > 1.000001 or y + height > 1.000001:
        raise APIError(400, "person bbox must stay within the image and have width/height at least 0.01")
    return result


def _image_path(store: Any, url: str) -> Path:
    if not isinstance(url, str) or not re.fullmatch(r"/media/[0-9a-f]{32}\.(?:jpg|png)", url):
        raise APIError(500, "pose source has an invalid stored image URL")
    path = store.media_dir / url.rsplit("/", 1)[1]
    if not path.is_file():
        raise APIError(409, "pose source image is missing")
    return path


def _display_dimensions(path: Path) -> tuple[int, int]:
    from PIL import Image
    with Image.open(path) as image:
        width, height = image.size
        if image.getexif().get(274) in {5, 6, 7, 8}:
            width, height = height, width
        return width, height


def _joint_profile(result: dict) -> tuple[str, list[str], list[list[int]]]:
    profile = result.get("keypoint_profile", "coco17")
    if not isinstance(profile, str) or not profile.strip() or len(profile) > 96:
        raise APIError(400, "keypoint_profile must be a short profile name")
    names = result.get("keypoint_names", JOINT_NAMES if profile == "coco17" else None)
    if (not isinstance(names, list) or not 1 <= len(names) <= 256
            or any(not isinstance(name, str) or not name.strip() or len(name) > 96 or any(ord(char) < 32 for char in name) for name in names)
            or len(set(names)) != len(names)):
        raise APIError(400, "keypoint_names must contain 1 to 256 unique named joints")
    if profile == "coco17" and names != JOINT_NAMES:
        raise APIError(400, "coco17 profile must preserve the standard named joint order")
    edges = result.get("skeleton_edges", SKELETON_EDGES if names == JOINT_NAMES else None)
    if (not isinstance(edges, list) or len(edges) > 768
            or any(not isinstance(edge, list) or len(edge) != 2 or any(type(index) is not int or not 0 <= index < len(names) for index in edge)
                   or edge[0] == edge[1] for edge in edges)
            or len({tuple(sorted(edge)) for edge in edges}) != len(edges)):
        raise APIError(400, "skeleton_edges must contain distinct valid pairs of declared joint indices")
    return profile, copy.deepcopy(names), copy.deepcopy(edges)


class HumanPoseJobs:
    """Passive result store; the historical name keeps old persisted jobs readable."""

    def __init__(self, store: Any):
        self.store = store
        self.directory = store.data_dir / "human_pose"
        self.directory.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.closed = False
        for path in self.directory.glob("*/job.json"):
            document = json.loads(path.read_text(encoding="utf-8"))
            if document.get("status") in {"queued", "running"}:
                document.update(status="interrupted", error="内置追踪已移至 capsule-human-tracking skill。请由 skill 重新运行并导入结果。", finished_at=_now())
                _atomic_json(path, document)

    def list(self, session_id: str) -> dict:
        self.store.get_session(session_id)
        with self.lock:
            jobs = [_summary(json.loads(path.read_text(encoding="utf-8"))) for path in self.directory.glob("*/job.json")]
        jobs = sorted((job for job in jobs if job["session_id"] == session_id), key=lambda job: job["created_at"], reverse=True)
        return {"session_id": session_id, "jobs": jobs, "mode": "external_results", "inference_supported": False,
                "source_export_supported": True, "result_import_supported": True}

    def get(self, job_id: str, frame_offset: int = 0, max_frames: int | str | None = None,
            reference_id: str | None = None, view_id: str | None = None) -> dict:
        with self.lock:
            job = _load_job(self.store, job_id)
            if type(frame_offset) is not int or frame_offset < 0:
                raise APIError(400, "frame_offset must be nonnegative")
            if reference_id is not None and (not isinstance(reference_id, str) or not ID.fullmatch(reference_id)):
                raise APIError(400, "reference_id must be an exact stored pose frame ID")
            if view_id is not None and (not isinstance(view_id, str) or not ID.fullmatch(view_id)):
                raise APIError(400, "view_id must identify a pose camera")
            known_views = job.get("view_ids", [job.get("view_id")])
            if view_id is not None and view_id not in known_views:
                raise APIError(404, "view_id is not a camera in this pose job")
            if max_frames not in (None, "all") and (type(max_frames) is not int or not 1 <= max_frames <= 32):
                raise APIError(400, "max_frames must be between 1 and 32")
            summary = _summary(job)
            if not job.get("automatic"):
                frames = copy.deepcopy(job.get("frames", []))
                all_count = len(frames)
                if view_id is not None:
                    frames = [frame for frame in frames if frame.get("view_id") == view_id]
                if reference_id is not None:
                    frames = [frame for frame in frames if frame["reference_id"] == reference_id]
                    if not frames:
                        raise APIError(404, "pose reference not found in this job")
                total = len(frames)
                count = total if max_frames in (None, "all") else max_frames
                page = frames[frame_offset:frame_offset + count]
                next_offset = frame_offset + count if frame_offset + count < total else None
            elif job["status"] != "completed":
                page, total, all_count, next_offset = [], 0, 0, None
            else:
                index = _frame_index(self.store, job_id)
                total = len(index["offsets"])
                all_count = total
                if reference_id is not None:
                    position = index["references"].get(reference_id)
                    if position is None:
                        raise APIError(404, "pose reference not found in this job")
                    if view_id is not None and index["views"].get(view_id, {}).get("start", -1) > position:
                        raise APIError(404, "pose reference does not belong to that camera")
                    page = _read_archive_frames(self.store, job_id, index, position, 1)
                    if view_id is not None and page[0].get("view_id") != view_id:
                        raise APIError(404, "pose reference does not belong to that camera")
                    total, next_offset = 1, None
                else:
                    view = index["views"].get(view_id) if view_id is not None else None
                    if view_id is not None and view is None:
                        raise APIError(404, "pose camera not found in this job")
                    start, total = (view["start"], view["count"]) if view else (0, total)
                    count = total if max_frames == "all" else max_frames if max_frames is not None else 8
                    page = _read_archive_frames(self.store, job_id, index, start + frame_offset,
                                                min(count, max(0, total - frame_offset)))
                    next_offset = frame_offset + len(page) if frame_offset + len(page) < total else None
            return {**summary, "frames": page, "result_frame_count": total, "total_frame_count": all_count,
                    "frame_offset": frame_offset,
                    "next_frame_offset": next_offset,
                    "result_json_path": str(self.directory / job_id / ("output.json" if job.get("automatic") else "job.json"))
                        if job["status"] == "completed" else None,
                    "keypoint_profile": job.get("keypoint_profile", "coco17"),
                    "keypoint_names": job.get("keypoint_names", JOINT_NAMES),
                    "skeleton_edges": job.get("skeleton_edges", SKELETON_EDGES)}

    def download(self, job_id: str) -> dict:
        """Keep exported external JSON byte semantics for safe idempotent reimport."""
        with self.lock:
            job = _load_job(self.store, job_id)
            if job.get("imported_external") and job["status"] == "completed":
                return json.loads((self.directory / job_id / "output.json").read_text(encoding="utf-8"))
            return self.get(job_id, max_frames="all")

    def _current_sources(self, session_id: str) -> tuple[list[dict], list[dict], str]:
        session = self.store.get_session(session_id)
        clip = session.get("reference_clip")
        sources, views = [], []
        if clip:
            for view in reference_views(clip):
                frames = view.get("frames", [])
                if not frames:
                    continue
                views.append({"view_id": view["clip_id"], "view_name": view["name"],
                              "reference_id": frames[0]["id"], "sampled_frames": len(frames)})
                sources.extend({"reference_id": frame["id"], "reference_name": frame["name"],
                                "reference_url": frame["url"], "frame_index": frame["frame_index"],
                                "time_sec": frame["time_sec"], "view_id": view["clip_id"],
                                "view_name": view["name"], "clip_id": clip["clip_id"],
                                **({"camera": copy.deepcopy(frame["camera"])} if frame.get("camera") else {})}
                               for frame in frames)
        else:
            for reference in session.get("reference_images", []):
                views.append({"view_id": reference["id"], "view_name": reference["name"],
                              "reference_id": reference["id"], "sampled_frames": 1})
                sources.append({"reference_id": reference["id"], "reference_name": reference["name"],
                                "reference_url": reference["url"], "frame_index": 0, "time_sec": 0,
                                "view_id": reference["id"], "view_name": reference["name"],
                                **({"camera": copy.deepcopy(reference["camera"])} if reference.get("camera") else {})})
        if not sources:
            raise APIError(400, "no reference frames are available to export")
        if len(views) > MAX_VIEWS or len(sources) > MAX_RESULT_FRAMES:
            raise APIError(400, f"pose exchange supports at most {MAX_VIEWS} views and {MAX_RESULT_FRAMES} frames")
        images = {}
        for source in sources:
            path = _image_path(self.store, source["reference_url"])
            if path not in images:
                images[path] = (*_display_dimensions(path), hashlib.sha256(path.read_bytes()).hexdigest())
            source["width"], source["height"], source["image_sha256"] = images[path]
            source["image_orientation"] = "exif_oriented_display"
        return sources, views, "dynamic_clip" if clip else "static_references"

    @staticmethod
    def _snapshot_id(project_id: str, session_id: str, sources: list[dict]) -> str:
        document = {"project_id": project_id, "session_id": session_id, "sources": sources}
        return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()

    def export_sources(self, payload: Any) -> dict:
        if not isinstance(payload, dict) or set(payload) - {"session_id", "request_id"}:
            raise APIError(400, "pose source export accepts only session_id and request_id")
        workspace = self.store.workspace()
        session_id = payload.get("session_id", workspace["session_id"])
        if session_id != workspace["session_id"]:
            raise APIError(409, "pose source export must use this project's current session")
        session = self.store.get_session(session_id)
        if session["status"] != "open":
            raise APIError(409, "session is closed")
        request_id = payload.get("request_id", uuid.uuid4().hex)
        if not isinstance(request_id, str) or not ID.fullmatch(request_id):
            raise APIError(400, "request_id must be a 32-character hexadecimal ID")
        with self.lock, self.store.lock:
            if self.closed:
                raise APIError(503, "pose result store is shutting down")
            for path in self.directory.glob("*/job.json"):
                existing = json.loads(path.read_text(encoding="utf-8"))
                if existing.get("request_id") == request_id:
                    if not existing.get("external_results") or existing["session_id"] != session_id:
                        raise APIError(409, "pose request_id already belongs to another export")
                    return self._export_response(existing)
            sources, views, kind = self._current_sources(session_id)
            snapshot = self._snapshot_id(workspace["project_id"], session_id, sources)
            job_id = uuid.uuid4().hex
            multi = len(views) > 1
            job = {"schema_version": 2 if multi else 1, "job_id": job_id, "request_id": request_id,
                   "project_id": workspace["project_id"], "session_id": session_id, "source_snapshot_id": snapshot,
                   "track_id": "human_" + job_id[:8], "status": "awaiting_import", "external_results": True,
                   "automatic": True, "all_views": True, "multi_view": multi, "source_kind": kind,
                   "sampling": "all_imported_frames", "sample_fps": None, "confidence_threshold": .3,
                   "completed_frames": 0, "total_frames": len(sources), "views": views,
                   "view_ids": [view["view_id"] for view in views], "created_at": _now(),
                   **({"view_id": views[0]["view_id"], "view_name": views[0]["view_name"]} if not multi else {})}
            directory = self.directory / job_id
            directory.mkdir()
            frames = [{"image_path": str(_image_path(self.store, source["reference_url"])),
                       "ref_id": source["reference_id"], "view_id": source["view_id"],
                       "frame_index": source["frame_index"], "time_seconds": source["time_sec"],
                       "width": source["width"], "height": source["height"],
                       "image_sha256": source["image_sha256"], "image_orientation": source["image_orientation"],
                       **({"camera": copy.deepcopy(source["camera"])} if source.get("camera") else {})} for source in sources]
            manifest = {key: job[key] for key in ("schema_version", "job_id", "track_id", "project_id", "session_id", "source_snapshot_id")}
            manifest.update(**({"view_ids": job["view_ids"]} if multi else {"view_id": job["view_id"]}),
                            project_dir=workspace["project_dir"], coordinate_frame="reference_image_normalized", frames=frames)
            _atomic_json(directory / "sources.json", {"sources": sources})
            _atomic_json(directory / "input.json", manifest)
            _atomic_json(directory / "job.json", job)
            return self._export_response(job)

    def _export_response(self, job: dict) -> dict:
        path = self.directory / job["job_id"] / "input.json"
        return {**_summary(job), "project_dir": self.store.workspace()["project_dir"],
                "manifest_json_path": str(path), "manifest": json.loads(path.read_text(encoding="utf-8"))}

    def import_result(self, payload: Any) -> dict:
        if (not isinstance(payload, dict) or set(payload) - {"job_id", "session_id", "result", "result_path"}
                or ("result" in payload) == ("result_path" in payload)):
            raise APIError(400, "pose import needs job_id and exactly one result or result_path")
        with self.lock, self.store.lock:
            if self.closed:
                raise APIError(503, "pose result store is shutting down")
            job = _load_job(self.store, payload.get("job_id"))
            workspace = self.store.workspace()
            if (not job.get("external_results") or job["session_id"] != workspace["session_id"]
                    or job["project_id"] != workspace["project_id"]
                    or payload.get("session_id", job["session_id"]) != job["session_id"]):
                raise APIError(409, "pose import does not belong to this project's current exported sources")
            if "result_path" in payload:
                value = payload["result_path"]
                if not isinstance(value, str) or not value:
                    raise APIError(400, "result_path must be a project-local JSON path")
                project = Path(workspace["project_dir"]).resolve()
                path = Path(value).expanduser()
                path = (project / path if not path.is_absolute() else path).resolve()
                if not path.is_relative_to(project) or not path.is_file():
                    raise APIError(400, "result_path must identify a file within this project")
                if path.stat().st_size > MAX_RESULT_BYTES:
                    raise APIError(413, "pose result file is too large")
                try:
                    result = json.loads(path.read_text(encoding="utf-8"))
                except (ValueError, UnicodeError) as exc:
                    raise APIError(400, "pose result file must contain valid JSON") from exc
            else:
                result = payload["result"]
            try:
                encoded = json.dumps(result, sort_keys=True, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise APIError(400, "pose result must contain finite JSON values") from exc
            if len(encoded.encode("utf-8")) > MAX_RESULT_BYTES:
                raise APIError(413, "pose result is too large")
            digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            if job["status"] == "completed":
                if job.get("result_digest") != digest:
                    raise APIError(409, "this pose export already has a different completed result")
                return _summary(job)
            if job["status"] != "awaiting_import":
                raise APIError(409, "pose export is not awaiting an external result")
            sources, _, _ = self._current_sources(job["session_id"])
            if self._snapshot_id(workspace["project_id"], job["session_id"], sources) != job["source_snapshot_id"]:
                raise APIError(409, "pose reference sources changed after export; export current sources again")
            normalized = self._validate_result(job, result)
            profile, names, edges = _joint_profile(result)
            provenance = result.get("provenance", {})
            if not isinstance(provenance, dict):
                raise APIError(400, "pose provenance must be an object")
            _safe_json(provenance)
            model, tracking = result.get("model", {}), result.get("tracking", {})
            if not isinstance(model, dict) or not isinstance(tracking, dict):
                raise APIError(400, "pose model and tracking metadata must be objects")
            _safe_json(model)
            _safe_json(tracking)
            if "identity_guaranteed" in tracking and type(tracking["identity_guaranteed"]) is not bool:
                raise APIError(400, "tracking identity_guaranteed must be a boolean")
            for key in ("scope", "cross_view_identity_source"):
                if key in tracking and (not isinstance(tracking[key], str) or not 1 <= len(tracking[key]) <= 200):
                    raise APIError(400, f"tracking {key} must be a short string")
            tracking = copy.deepcopy(tracking)
            tracking.setdefault("scope", "per_view_independent")
            tracking.setdefault("identity_guaranteed", False)
            tracking.setdefault("cross_view_identity_source", "independent_external_tracks" if result["evidence_kind"] == "observed_2d" else "projected_3d_geometry")
            tracking["workbench_identity_verified"] = False
            threshold = number(result.get("confidence_threshold", .3), "pose display confidence threshold", maximum=1)
            directory = self.directory / job["job_id"]
            _atomic_json(directory / "output.json", result)
            _store_frame_archive(directory, normalized)
            job.update(status="completed", imported_external=True, completed_frames=len(normalized),
                       result_frame_count=len(normalized), model=model, tracking=tracking,
                       evidence_kind=result["evidence_kind"], confidence_threshold=threshold, keypoint_profile=profile,
                       keypoint_names=names, skeleton_edges=edges, provenance=provenance,
                       result_digest=digest, finished_at=_now(), error=None)
            _atomic_json(directory / "job.json", job)
            return _summary(job)

    def _validate_result(self, job: dict, result: Any) -> list[dict]:
        bindings = ("job_id", "track_id", "project_id", "session_id", "source_snapshot_id")
        if not isinstance(result, dict) or any(result.get(key) != job[key] for key in bindings):
            raise APIError(400, "pose result belongs to another exported project/session/source snapshot")
        if type(result.get("schema_version")) is not int or result["schema_version"] != job["schema_version"]:
            raise APIError(400, "pose result schema_version must match the exported manifest")
        if (job["multi_view"] and result.get("view_ids") != job["view_ids"]
                or not job["multi_view"] and result.get("view_id") != job["view_id"]):
            raise APIError(400, "pose result cameras do not match the exported manifest")
        if result.get("evidence_kind") not in {"observed_2d", "projected_3d"}:
            raise APIError(400, "pose evidence_kind must declare observed_2d or projected_3d")
        _, joint_names, _ = _joint_profile(result)
        predictions, sources = result.get("frames"), _sources(self.store, job)
        if not isinstance(predictions, list) or len(predictions) != len(sources):
            raise APIError(400, "pose result frame count must match all exported images")
        frames = []
        for source, prediction in zip(sources, predictions):
            if not isinstance(prediction, dict):
                raise APIError(400, "pose frame must be an object")
            exact = {"ref_id": source["reference_id"], "view_id": source["view_id"],
                     "width": source["width"], "height": source["height"],
                     "frame_index": source["frame_index"], "time_seconds": source["time_sec"],
                     "image_sha256": source["image_sha256"], "image_orientation": source["image_orientation"]}
            if (any(prediction.get(key) != value for key, value in exact.items())
                    or any(type(prediction.get(key)) is not int for key in ("width", "height", "frame_index"))
                    or type(prediction.get("time_seconds")) not in (int, float)):
                raise APIError(400, "pose result frame IDs, camera, dimensions and timestamps must exactly match export")
            points = prediction.get("keypoints")
            if not isinstance(points, list) or len(points) != len(joint_names):
                raise APIError(400, "pose result points must match the declared named joint profile")
            normalized = []
            for name, point in zip(joint_names, points):
                if not isinstance(point, dict) or point.get("name") != name:
                    raise APIError(400, "pose result has unexpected joint names/order")
                values = [point.get(key) for key in ("x", "y", "score")]
                if any(type(value) not in (int, float) or not 0 <= value <= 1 or not math.isfinite(value) for value in values):
                    raise APIError(400, "pose result requires finite normalized coordinates and scores")
                if type(point.get("in_frame")) is not bool:
                    raise APIError(400, "pose result requires a boolean in_frame for each joint")
                normalized.append({"name": name, "x": values[0], "y": values[1], "score": values[2],
                                   "in_frame": point["in_frame"],
                                   **({"raw_score": point["raw_score"]} if type(point.get("raw_score")) in (int, float) and math.isfinite(point["raw_score"]) else {})})
            status = prediction.get("tracking_status")
            if status not in {"tracked", "lost"}:
                raise APIError(400, "pose tracking_status must be tracked or lost")
            roi = prediction.get("bbox")
            if isinstance(roi, dict):
                roi = [roi.get(key) for key in ("x", "y", "width", "height")]
            if roi is None and (status != "lost" or any(point["score"] > 0 or point["in_frame"] for point in normalized)):
                raise APIError(400, "no-person frame must be lost with no visible or confident keypoints")
            roi_status = prediction.get("roi_status")
            if roi_status is not None and (not isinstance(roi_status, str) or len(roi_status) > 120):
                raise APIError(400, "roi_status must be a short string")
            frames.append({**copy.deepcopy(source), "keypoints": normalized,
                           "bbox": _bbox(roi) if roi is not None else None, "tracking_status": status,
                           "roi_status": roi_status,
                           **({"detector_score": prediction["detector_score"]} if type(prediction.get("detector_score")) in (int, float) and math.isfinite(prediction["detector_score"]) else {})})
        return frames

    def close(self) -> None:
        with self.lock:
            self.closed = True


def prepare_pose_feedback(store: Any, session_id: str, references: Any, note: str = "") -> list[dict]:
    """Resolve only stored, completed predictions; never trust client keypoints."""
    if references is None:
        references = []
    if not isinstance(references, list) or len(references) > 8:
        raise APIError(400, "pose_refs must be an array with at most 8 samples")
    pattern = re.compile(r"\[\[pose:([0-9a-f]{32}):([0-9a-f]{32})\]\]")
    tokens = list(pattern.finditer(note))
    if any(match.start() not in {token.start() for token in tokens} for match in re.finditer(r"\[\[pose:", note)):
        raise APIError(400, "malformed inline human pose reference")
    cited = {(token.group(1), token.group(2)) for token in tokens}
    supplied = {(ref.get("job_id"), ref.get("reference_id")) for ref in references if isinstance(ref, dict)
                and isinstance(ref.get("job_id"), str) and isinstance(ref.get("reference_id"), str)}
    if not cited <= supplied:
        raise APIError(400, "inline human pose reference has no matching pose_refs entry")
    prepared = []
    seen = set()
    for reference in references:
        if not isinstance(reference, dict):
            raise APIError(400, "pose reference must contain job_id and reference_id")
        job = _load_job(store, reference.get("job_id"))
        if job.get("session_id") != session_id:
            raise APIError(400, "pose reference belongs to another session")
        if job.get("status") != "completed":
            raise APIError(409, "human pose job has not completed")
        reference_id = reference.get("reference_id")
        if job.get("automatic"):
            if not isinstance(reference_id, str) or not ID.fullmatch(reference_id):
                raise APIError(400, "pose reference is not an inferred frame of this job")
            index = _frame_index(store, job["job_id"])
            position = index["references"].get(reference_id)
            frame = _read_archive_frames(store, job["job_id"], index, position, 1)[0] if position is not None else None
        else:
            frame = next((frame for frame in job.get("frames", []) if frame["reference_id"] == reference_id), None)
        if frame is None:
            raise APIError(400, "pose reference is not an inferred frame of this job")
        key = (job["job_id"], reference_id)
        if key in seen:
            raise APIError(400, "duplicate pose reference")
        seen.add(key)
        image = _image_path(store, frame["reference_url"])
        # Preserve actual inference-frame evidence even if the current clip changed.
        prepared.append({"job_id": job["job_id"], "track_id": job["track_id"], "source": ("projected_3d_geometry" if job.get("evidence_kind") == "projected_3d" else "external_pose_estimate") if job.get("imported_external") else "vitpose_estimate",
                         "evidence_kind": job.get("evidence_kind", "observed_2d"),
                         "keypoint_profile": job.get("keypoint_profile", "coco17"),
                         "keypoint_names": copy.deepcopy(job.get("keypoint_names", JOINT_NAMES)),
                         "provenance": copy.deepcopy(job.get("provenance", {})),
                         **({"multi_view": True, "view_ids": copy.deepcopy(job["view_ids"]),
                             "cross_view_identity_source": job.get("tracking", {}).get("cross_view_identity_source", "independent_external_tracks") if job.get("imported_external") else "automatic_dominant_person_per_view" if job.get("automatic") else "user_designated_boxes"} if job.get("multi_view") else {}),
                         "coordinate_frame": "reference_image_normalized", "confidence_threshold": job["confidence_threshold"],
                         "model": copy.deepcopy(job.get("model", {})), "tracking": copy.deepcopy(job.get("tracking", {})), "frame": copy.deepcopy(frame),
                         "reference_original_url": frame["reference_url"],
                         "_overlay_data": _render_overlay(image, frame, job["confidence_threshold"], job.get("skeleton_edges", SKELETON_EDGES),
                                                          job.get("evidence_kind", "observed_2d")),
                         "skeleton_edges": copy.deepcopy(job.get("skeleton_edges", SKELETON_EDGES))})
    return prepared


def _render_overlay(path: Path, frame: dict, threshold: float, edges: list[list[int]] | None = None,
                    evidence_kind: str = "observed_2d") -> bytes:
    from PIL import Image, ImageDraw, ImageOps
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    draw = ImageDraw.Draw(image)
    points = frame["keypoints"]
    width, height = image.size
    line_width = max(2, round(min(width, height) / 200))
    def visible(point: dict) -> bool:
        return frame.get("tracking_status") != "lost" and point["score"] >= threshold and point.get("in_frame", True)
    for first, second in edges if edges is not None else SKELETON_EDGES:
        if visible(points[first]) and visible(points[second]):
            draw.line([(points[index]["x"] * width, points[index]["y"] * height) for index in (first, second)],
                      fill="#00b7b0", width=line_width)
    radius = line_width + 1
    for point in points:
        if visible(point):
            x, y = point["x"] * width, point["y"] * height
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill="#00b7b0", outline="white")
    draw.rectangle((4, 4, 255, 25), fill="white")
    draw.text((8, 8), "Projected 3D joints" if evidence_kind == "projected_3d" else "Estimated 2D joints", fill="#005f5b")
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()

"""Project-local asynchronous ViTPose jobs and immutable visual evidence.

Automatic jobs cover every imported camera/frame and detect one dominant
person independently per view. Neither mode infers cross-camera identity.
"""

from __future__ import annotations

import copy
import io
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
from typing import Any
import uuid

from core import APIError, _now
from dynamic import MAX_CLIP_FRAMES, number, reference_views
from pose_worker import command_for_job, runtime_status


JOINT_NAMES = ["nose", "left_eye", "right_eye", "left_ear", "right_ear", "left_shoulder", "right_shoulder",
               "left_elbow", "right_elbow", "left_wrist", "right_wrist", "left_hip", "right_hip",
               "left_knee", "right_knee", "left_ankle", "right_ankle"]
SKELETON_EDGES = [[0, 1], [0, 2], [1, 3], [2, 4], [5, 6], [5, 7], [7, 9], [6, 8], [8, 10],
                  [5, 11], [6, 12], [11, 12], [11, 13], [13, 15], [12, 14], [14, 16]]
ID = re.compile(r"^[0-9a-f]{32}$")
ACTIVE = {"queued", "running"}
# All projects on this server share the runner, so they cannot overcommit its GPU.
_RUNNER_SLOT = threading.Semaphore(1)
MAX_FRAMES = 600
MAX_VIEWS = 8
MAX_AUTO_FRAMES = MAX_VIEWS * MAX_CLIP_FRAMES


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


class HumanPoseJobs:
    def __init__(self, store: Any):
        self.store = store
        self.directory = store.data_dir / "human_pose"
        self.directory.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.closed = False
        self.threads: dict[str, threading.Thread] = {}
        self.stops: dict[str, threading.Event] = {}
        self.processes: dict[str, subprocess.Popen] = {}
        # A restart must not pretend that an in-flight worker completed.
        for path in self.directory.glob("*/job.json"):
            document = json.loads(path.read_text(encoding="utf-8"))
            if document.get("status") in ACTIVE:
                document.update(status="interrupted", error="服务已重启，请重新框选或重新开始追踪。", finished_at=_now())
                _atomic_json(path, document)

    def list(self, session_id: str) -> dict:
        self.store.get_session(session_id)
        with self.lock:
            jobs = [_summary(json.loads(path.read_text(encoding="utf-8"))) for path in self.directory.glob("*/job.json")]
        jobs = sorted((job for job in jobs if job["session_id"] == session_id), key=lambda job: job["created_at"], reverse=True)
        return {**runtime_status(), "session_id": session_id, "jobs": jobs}

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
                    "keypoint_names": JOINT_NAMES, "skeleton_edges": SKELETON_EDGES}

    def start(self, payload: Any) -> dict:
        if not isinstance(payload, dict):
            raise APIError(400, "pose request must be an object")
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not ID.fullmatch(session_id):
            raise APIError(400, "pose request must identify an existing session")
        session = self.store.get_session(session_id)
        if session["status"] != "open":
            raise APIError(409, "session is closed")
        request_id = payload.get("request_id")
        if not isinstance(request_id, str) or not ID.fullmatch(request_id):
            raise APIError(400, "request_id must be a 32-character hexadecimal ID")
        try:
            digest = json.dumps(payload, sort_keys=True, allow_nan=False, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise APIError(400, "pose request must contain finite JSON values") from exc
        # Retry the exact request even after a worker becomes unavailable.
        with self.lock:
            for path in self.directory.glob("*/job.json"):
                existing = json.loads(path.read_text(encoding="utf-8"))
                if existing.get("request_id") == request_id:
                    if existing.get("request_digest") != digest:
                        raise APIError(409, "pose request_id was already used for a different request")
                    return _summary(existing)
            if self.closed:
                raise APIError(503, "pose runner is shutting down")
            capability = runtime_status()
            if not capability["configured"]:
                raise APIError(503, capability.get("message", "ViTPose runner is not configured"))
            if any(json.loads(path.read_text(encoding="utf-8")).get("status") in ACTIVE for path in self.directory.glob("*/job.json")):
                raise APIError(409, "this project already has an active human pose job")
            if "all_views" in payload:
                if payload["all_views"] is not True:
                    raise APIError(400, "all_views must be true for automatic whole-reference tracking")
                if not capability.get("automatic_detection_configured", False):
                    raise APIError(503, capability.get("automatic_detection_message", "automatic person detector is unavailable"))
                return self._start_automatic_locked(payload, session, request_id, digest)
            reference_id, view_id = payload.get("reference_id"), payload.get("view_id")
            multi_requested = "views" in payload
            multi_views = payload.get("views")
            if multi_requested:
                if reference_id is not None or view_id is not None or "bbox" in payload:
                    raise APIError(400, "multi-view pose requests use views instead of top-level reference_id, view_id or bbox")
                if not isinstance(multi_views, list) or not 2 <= len(multi_views) <= MAX_VIEWS:
                    raise APIError(400, "multi-view pose requests need 2 to 8 camera views")
                selected_views = []
                seen = set()
                for item in multi_views:
                    if not isinstance(item, dict) or not isinstance(item.get("view_id"), str) or not ID.fullmatch(item["view_id"]):
                        raise APIError(400, "each pose view needs a valid view_id")
                    if item["view_id"] in seen:
                        raise APIError(400, "pose camera views cannot be repeated")
                    seen.add(item["view_id"])
                    if "reference_id" in item and (not isinstance(item["reference_id"], str) or not ID.fullmatch(item["reference_id"])):
                        raise APIError(400, "pose view reference_id must identify its first sampled frame")
                    selected_views.append({"view_id": item["view_id"], "bbox": _bbox(item.get("bbox")),
                                           **({"reference_id": item["reference_id"]} if "reference_id" in item else {})})
                bbox = None
            else:
                if (reference_id is None) == (view_id is None):
                    raise APIError(400, "choose exactly one static reference_id or dynamic view_id")
                if view_id is not None and (not isinstance(view_id, str) or not ID.fullmatch(view_id)):
                    raise APIError(400, "pose view_id must identify a reference camera")
                bbox = _bbox(payload.get("bbox"))
                selected_views = []
            fps = number(payload.get("sample_fps", 5), "pose sample_fps", minimum=.1, maximum=60)
            threshold = number(payload.get("confidence_threshold", .3), "pose confidence threshold", maximum=1)
            provenance: dict = {}
            if reference_id is not None:
                reference = next((ref for ref in session.get("reference_images", []) if ref["id"] == reference_id), None)
                if reference is None:
                    raise APIError(400, "static pose reference is not in this session")
                if any(key in payload for key in ("start_time_sec", "end_time_sec")):
                    raise APIError(400, "static pose requests cannot have a time range")
                sources = [{"reference_id": reference["id"], "reference_name": reference["name"], "reference_url": reference["url"]}]
                if reference.get("camera"):
                    sources[0]["camera"] = copy.deepcopy(reference["camera"])
                provenance.update(reference_id=reference_id, reference_name=reference["name"])
            else:
                clip = session.get("reference_clip")
                available = {view["clip_id"]: view for view in reference_views(clip)}
                requested = selected_views if multi_requested else [{"view_id": view_id, "bbox": bbox}]
                if any(item["view_id"] not in available for item in requested):
                    raise APIError(400, "pose view_id is not a current reference camera")
                # Every requested camera must cover the same explicit interval.
                duration = min(available[item["view_id"]]["duration_sec"] for item in requested)
                start = number(payload.get("start_time_sec", 0), "pose range start", maximum=duration)
                end = number(payload.get("end_time_sec", duration), "pose range end", minimum=start, maximum=duration)
                sources = []
                view_metadata = []
                for item in requested:
                    current_view_id = item["view_id"]
                    view = available[current_view_id]
                    # First sample is the first real frame at/after start; no
                    # synthesized frame or cross-camera timestamp is implied.
                    candidates = [frame for frame in view["frames"] if start - 1e-6 <= frame["time_sec"] <= end + 1e-6]
                    if not candidates:
                        raise APIError(400, "pose range contains no reference frames in view " + view["name"])
                    selected = []
                    next_time = candidates[0]["time_sec"]
                    for frame in candidates:
                        if frame["time_sec"] + 1e-6 >= next_time:
                            selected.append(frame)
                            next_time = frame["time_sec"] + 1 / fps
                    if item.get("reference_id") is not None and item["reference_id"] != selected[0]["id"]:
                        raise APIError(400, "pose view reference_id is not its first sampled frame; refresh the selected box")
                    if len(sources) + len(selected) > MAX_FRAMES:
                        raise APIError(400, f"pose tracking exceeds {MAX_FRAMES} samples across all views; shorten the range or lower sample_fps")
                    view_metadata.append({"view_id": current_view_id, "view_name": view["name"],
                                          "reference_id": selected[0]["id"], "bbox": item["bbox"],
                                          "sampled_frames": len(selected)})
                    sources.extend({"reference_id": frame["id"], "reference_name": frame["name"], "reference_url": frame["url"],
                                    "frame_index": frame["frame_index"], "time_sec": frame["time_sec"], "view_id": current_view_id,
                                    "view_name": view["name"], "clip_id": clip["clip_id"],
                                    **({"seed_bbox": item["bbox"]} if multi_requested else {}),
                                    **({"camera": copy.deepcopy(frame["camera"])} if frame.get("camera") else {})} for frame in selected)
                if multi_requested:
                    provenance.update(multi_view=True, views=view_metadata, view_ids=[item["view_id"] for item in view_metadata],
                                      clip_id=clip["clip_id"], start_time_sec=start, end_time_sec=end)
                else:
                    provenance.update(view_id=view_id, view_name=view["name"], clip_id=clip["clip_id"], start_time_sec=start, end_time_sec=end)
            for source in sources:
                source["width"], source["height"] = _display_dimensions(_image_path(self.store, source["reference_url"]))
                source["image_orientation"] = "exif_oriented_display"
            job_id = uuid.uuid4().hex
            job = {"schema_version": 2 if multi_requested else 1, "job_id": job_id, "request_id": request_id, "request_digest": digest,
                   "session_id": session_id, "track_id": "human_" + job_id[:8], "status": "queued",
                   **({"bbox": bbox} if not multi_requested else {}),
                   "sample_fps": fps, "confidence_threshold": threshold, "completed_frames": 0, "total_frames": len(sources),
                   "created_at": _now(), "sources": sources, **provenance}
            directory = self.directory / job_id
            directory.mkdir()
            _atomic_json(directory / "job.json", job)
            self.stops[job_id] = threading.Event()
            thread = threading.Thread(target=self._run, args=(job_id,), daemon=True, name="vitpose-" + job_id[:8])
            self.threads[job_id] = thread
            thread.start()
            return _summary(job)

    def _start_automatic_locked(self, payload: dict, session: dict, request_id: str, digest: str) -> dict:
        if set(payload) - {"session_id", "request_id", "all_views"}:
            raise APIError(400, "automatic pose tracking uses all current references without box, range or sampling options")
        clip = session.get("reference_clip")
        sources: list[dict] = []
        view_metadata: list[dict] = []
        if clip:
            for view in reference_views(clip):
                frames = view.get("frames", [])
                if not frames:
                    continue
                view_metadata.append({"view_id": view["clip_id"], "view_name": view["name"],
                                      "reference_id": frames[0]["id"], "sampled_frames": len(frames)})
                sources.extend({"reference_id": frame["id"], "reference_name": frame["name"],
                                "reference_url": frame["url"], "frame_index": frame["frame_index"],
                                "time_sec": frame["time_sec"], "view_id": view["clip_id"],
                                "view_name": view["name"], "clip_id": clip["clip_id"],
                                **({"camera": copy.deepcopy(frame["camera"])} if frame.get("camera") else {})}
                               for frame in frames)
            source_kind = "dynamic_clip"
        else:
            for reference in session.get("reference_images", []):
                view_metadata.append({"view_id": reference["id"], "view_name": reference["name"],
                                      "reference_id": reference["id"], "sampled_frames": 1})
                sources.append({"reference_id": reference["id"], "reference_name": reference["name"],
                                "reference_url": reference["url"], "frame_index": 0, "time_sec": 0,
                                "view_id": reference["id"], "view_name": reference["name"],
                                **({"camera": copy.deepcopy(reference["camera"])} if reference.get("camera") else {})})
            source_kind = "static_references"
        if not sources:
            raise APIError(400, "no reference frames are available for automatic human tracking")
        if len(view_metadata) > MAX_VIEWS or len(sources) > MAX_AUTO_FRAMES:
            raise APIError(400, f"automatic pose tracking supports at most {MAX_VIEWS} views and {MAX_AUTO_FRAMES} imported frames")
        for source in sources:
            source["width"], source["height"] = _display_dimensions(_image_path(self.store, source["reference_url"]))
            source["image_orientation"] = "exif_oriented_display"
        multi_view = len(view_metadata) > 1
        job_id = uuid.uuid4().hex
        job = {"schema_version": 2 if multi_view else 1, "job_id": job_id, "request_id": request_id,
               "request_digest": digest, "session_id": session["session_id"], "track_id": "human_" + job_id[:8],
               "status": "queued", "automatic": True, "all_views": True, "multi_view": multi_view,
               "source_kind": source_kind, "sampling": "all_imported_frames", "sample_fps": None,
               "confidence_threshold": .3, "completed_frames": 0, "total_frames": len(sources),
               "views": view_metadata, "view_ids": [view["view_id"] for view in view_metadata],
               **({"view_id": view_metadata[0]["view_id"], "view_name": view_metadata[0]["view_name"]} if not multi_view else {}),
               **({"clip_id": clip["clip_id"]} if clip else {}), "created_at": _now()}
        directory = self.directory / job_id
        directory.mkdir()
        _atomic_json(directory / "sources.json", {"sources": sources})
        _atomic_json(directory / "job.json", job)
        self.stops[job_id] = threading.Event()
        thread = threading.Thread(target=self._run, args=(job_id,), daemon=True, name="vitpose-" + job_id[:8])
        self.threads[job_id] = thread
        thread.start()
        return _summary(job)

    def _update(self, job_id: str, **changes: Any) -> dict:
        with self.lock:
            job = _load_job(self.store, job_id)
            if job["status"] in ACTIVE:
                job.update(changes)
                _atomic_json(self.directory / job_id / "job.json", job)
            return job

    @staticmethod
    def _terminate(process: subprocess.Popen) -> None:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)

    def _run(self, job_id: str) -> None:
        stop = self.stops[job_id]
        acquired = False
        process = None
        watchdog = None
        try:
            while not stop.is_set():
                if _RUNNER_SLOT.acquire(timeout=.2):
                    acquired = True
                    break
            if stop.is_set():
                return
            job = self._update(job_id, status="running", started_at=_now())
            directory = self.directory / job_id
            frames = []
            for source in _sources(self.store, job):
                frame = {"image_path": str(_image_path(self.store, source["reference_url"])), "ref_id": source["reference_id"],
                         "width": source["width"], "height": source["height"],
                         "frame_index": source.get("frame_index", 0), "time_seconds": source.get("time_sec", 0),
                         **({"view_id": source["view_id"]} if job.get("multi_view") else {})}
                if not job.get("automatic"):
                    x, y, width, height = source.get("seed_bbox", job.get("bbox"))
                    frame["bbox_xywh"] = [x * source["width"], y * source["height"], width * source["width"], height * source["height"]]
                frames.append(frame)
            manifest = {"schema_version": 2 if job.get("multi_view") else 1, "job_id": job_id, "track_id": job["track_id"],
                        **({"view_ids": job["view_ids"]} if job.get("multi_view") else
                           {"view_id": job["view_id"] if job.get("automatic") else job.get("view_id", "reference:" + frames[0]["ref_id"])}),
                        "options": {"device": "auto", "update_roi": True, "auto_detect": bool(job.get("automatic")),
                                    "score_threshold": job["confidence_threshold"]}, "frames": frames}
            _atomic_json(directory / "input.json", manifest)
            output = directory / "output.json"
            with (directory / "worker.log").open("wb") as log:
                with self.lock:
                    if stop.is_set():
                        return
                    command = [sys.executable, str(Path(__file__).with_name("pose_launcher.py")), str(os.getpid()), "--",
                               *command_for_job(directory / "input.json", output)]
                    process = subprocess.Popen(command, stdout=subprocess.PIPE,
                                               stderr=log, text=True, start_new_session=True)
                    self.processes[job_id] = process
                def timed_out() -> None:
                    self._update(job_id, status="failed", error="ViTPose 追踪超时，请检查 worker 日志后重试。", finished_at=_now())
                    stop.set()
                    self._terminate(process)
                watchdog = threading.Timer(14400 if job.get("automatic") else 1800, timed_out)
                watchdog.daemon = True
                watchdog.start()
                for line in process.stdout:
                    if stop.is_set():
                        break
                    if len(line) > 8192:
                        continue
                    try:
                        progress = json.loads(line)
                    except ValueError:
                        continue
                    count = progress.get("completed_frames") if isinstance(progress, dict) else None
                    if type(count) is int and job["completed_frames"] <= count <= job["total_frames"]:
                        job = self._update(job_id, completed_frames=count)
                if stop.is_set():
                    self._terminate(process)
                    return
                code = process.wait(timeout=30)
                if code:
                    detail = (directory / "worker.log").read_text(encoding="utf-8", errors="replace")[-1400:]
                    raise RuntimeError(f"ViTPose worker exited {code}: {detail}")
            result = json.loads(output.read_text(encoding="utf-8"))
            normalized = self._validate_result(job, result)
            tracking = result.get("tracking", {})
            if not isinstance(tracking, dict):
                raise ValueError("pose output tracking metadata must be an object")
            if job.get("multi_view"):
                tracking = {**tracking, "scope": "per_view_independent",
                            "cross_view_identity_source": "automatic_dominant_person_per_view" if job.get("automatic") else "user_designated_boxes",
                            "identity_guaranteed": False}
            if job.get("automatic"):
                _store_frame_archive(directory, normalized)
            self._update(job_id, status="completed", completed_frames=len(normalized),
                         **({"result_frame_count": len(normalized)} if job.get("automatic") else {"frames": normalized}),
                         model=result.get("model", {}), tracking=tracking, finished_at=_now(), error=None)
        except Exception as exc:
            self._update(job_id, status="failed", error=str(exc)[:1800], finished_at=_now())
        finally:
            if watchdog is not None:
                watchdog.cancel()
            if process is not None:
                self._terminate(process)
                if process.stdout:
                    process.stdout.close()
            with self.lock:
                self.processes.pop(job_id, None)
            if acquired:
                _RUNNER_SLOT.release()

    def _validate_result(self, job: dict, result: Any) -> list[dict]:
        if not isinstance(result, dict) or result.get("job_id") != job["job_id"] or result.get("track_id") != job["track_id"]:
            raise ValueError("pose output belongs to another tracking job")
        if job.get("multi_view") and result.get("view_ids") not in (None, job["view_ids"]):
            raise ValueError("pose output camera view list does not match the requested views")
        sources = _sources(self.store, job)
        predictions = result.get("frames")
        if not isinstance(predictions, list) or len(predictions) != len(sources):
            raise ValueError("pose output frame count does not match requested images")
        frames = []
        for source, prediction in zip(sources, predictions):
            if (not isinstance(prediction, dict) or prediction.get("ref_id") != source["reference_id"]
                    or prediction.get("width") != source["width"] or prediction.get("height") != source["height"]):
                raise ValueError("pose output source or image dimensions do not match")
            if job.get("multi_view") and prediction.get("view_id") != source["view_id"]:
                raise ValueError("pose output camera view does not match the requested source")
            if ("frame_index" in prediction and prediction["frame_index"] != source.get("frame_index", 0) or
                    "time_seconds" in prediction and prediction["time_seconds"] != source.get("time_sec", 0)):
                raise ValueError("pose output frame time/index does not match the requested source")
            points = prediction.get("keypoints")
            if not isinstance(points, list) or len(points) != len(JOINT_NAMES):
                raise ValueError("ViTPose output must contain 17 COCO keypoints")
            normalized = []
            for name, point in zip(JOINT_NAMES, points):
                if not isinstance(point, dict) or point.get("name") != name:
                    raise ValueError("pose output has unexpected joint names/order")
                values = [point.get(key) for key in ("x", "y", "score")]
                if any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1 for value in values):
                    raise ValueError("pose output has non-finite or unnormalized keypoints")
                normalized.append({"name": name, "x": values[0], "y": values[1], "score": values[2],
                                   "in_frame": point.get("in_frame", True) is True,
                                   **({"raw_score": point["raw_score"]} if type(point.get("raw_score")) in (int, float) and math.isfinite(point["raw_score"]) else {})})
            roi = prediction.get("bbox")
            if isinstance(roi, dict):
                roi = [roi.get(key) for key in ("x", "y", "width", "height")]
            if roi is None and not job.get("automatic"):
                roi = source.get("seed_bbox", job.get("bbox"))
            valid_count = sum(point["score"] >= job["confidence_threshold"] and point["in_frame"] for point in normalized)
            if roi is None and prediction.get("tracking_status") != "lost":
                raise ValueError("tracked automatic frame needs an actual person ROI")
            if roi is None and any(point["score"] > 0 or point["in_frame"] for point in normalized):
                raise ValueError("no-person frame cannot contain inferred visible keypoints")
            frames.append({**copy.deepcopy(source), "keypoints": normalized, "bbox": _bbox(roi) if roi is not None else None,
                           "tracking_status": prediction.get("tracking_status", "tracked" if valid_count >= 5 else "lost"),
                           "roi_status": prediction.get("roi_status"),
                           **({"detector_score": prediction["detector_score"]} if type(prediction.get("detector_score")) in (int, float) and math.isfinite(prediction["detector_score"]) else {})})
        return frames

    def cancel(self, job_id: str, *, interrupted: bool = False) -> dict:
        with self.lock:
            job = _load_job(self.store, job_id)
            if job["status"] not in ACTIVE:
                return _summary(job)
            status = "interrupted" if interrupted else "cancelled"
            job = self._update(job_id, status=status, error="服务重启中，追踪已中断。" if interrupted else None, finished_at=_now())
            self.stops[job_id].set()
            process = self.processes.get(job_id)
        if process is not None:
            self._terminate(process)
        return _summary(job)

    def close(self) -> None:
        with self.lock:
            self.closed = True
            ids = list(self.stops)
        for job_id in ids:
            self.cancel(job_id, interrupted=True)
        for thread in self.threads.values():
            thread.join(timeout=10)


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
        prepared.append({"job_id": job["job_id"], "track_id": job["track_id"], "source": "vitpose_estimate",
                         **({"multi_view": True, "view_ids": copy.deepcopy(job["view_ids"]),
                             "cross_view_identity_source": "automatic_dominant_person_per_view" if job.get("automatic") else "user_designated_boxes"} if job.get("multi_view") else {}),
                         "coordinate_frame": "reference_image_normalized", "confidence_threshold": job["confidence_threshold"],
                         "model": copy.deepcopy(job.get("model", {})), "tracking": copy.deepcopy(job.get("tracking", {})), "frame": copy.deepcopy(frame),
                         "reference_original_url": frame["reference_url"],
                         "_overlay_data": _render_overlay(image, frame, job["confidence_threshold"]),
                         "skeleton_edges": SKELETON_EDGES})
    return prepared


def _render_overlay(path: Path, frame: dict, threshold: float) -> bytes:
    from PIL import Image, ImageDraw, ImageOps
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    draw = ImageDraw.Draw(image)
    points = frame["keypoints"]
    width, height = image.size
    line_width = max(2, round(min(width, height) / 200))
    def visible(point: dict) -> bool:
        return frame.get("tracking_status") != "lost" and point["score"] >= threshold and point.get("in_frame", True)
    for first, second in SKELETON_EDGES:
        if visible(points[first]) and visible(points[second]):
            draw.line([(points[index]["x"] * width, points[index]["y"] * height) for index in (first, second)],
                      fill="#00b7b0", width=line_width)
    radius = line_width + 1
    for point in points:
        if visible(point):
            x, y = point["x"] * width, point["y"] * height
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill="#00b7b0", outline="white")
    draw.rectangle((4, 4, 255, 25), fill="white")
    draw.text((8, 8), "ViTPose / estimated COCO17 keypoints", fill="#005f5b")
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()

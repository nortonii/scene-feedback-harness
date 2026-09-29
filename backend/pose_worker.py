"""ViTPose runtime adapter and a single-person, single-view inference worker.

The HTTP process imports this module without loading Torch or a checkpoint.
Inference happens in a separately configured Python process or runner.
"""

from __future__ import annotations

import importlib.util
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = Path.home() / "data/arctic-official/models/vitpose-plus-base"
COCO_NAMES = (
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_hip", "right_hip",
    "left_knee", "right_knee", "left_ankle", "right_ankle",
)
COCO_EDGES = ((15, 13), (13, 11), (16, 14), (14, 12), (11, 12), (5, 11),
              (6, 12), (5, 6), (5, 7), (6, 8), (7, 9), (8, 10),
              (1, 2), (0, 1), (0, 2), (1, 3), (2, 4), (3, 5), (4, 6))
MODEL_FILES = ("config.json", "preprocessor_config.json", "model.safetensors")


def _runner_template() -> list[str] | None:
    value = os.environ.get("SCENE_FEEDBACK_POSE_RUNNER")
    if not value:
        return None
    try:
        command = json.loads(value)
    except ValueError as exc:
        raise ValueError("SCENE_FEEDBACK_POSE_RUNNER must be a JSON argv array") from exc
    if not isinstance(command, list) or not command or any(not isinstance(item, str) or not item or "\0" in item for item in command):
        raise ValueError("SCENE_FEEDBACK_POSE_RUNNER must be a nonempty JSON argv array of strings")
    if not any("{manifest}" in item for item in command) or not any("{output}" in item for item in command):
        raise ValueError("pose runner argv must include both {manifest} and {output}")
    return command


def _executable_exists(value: str) -> bool:
    return bool(shutil.which(os.path.expanduser(value)))


def _model_path() -> Path:
    return Path(os.environ.get("SCENE_FEEDBACK_POSE_MODEL", str(DEFAULT_MODEL))).expanduser()


def _missing_dependencies(python: str) -> list[str]:
    dependencies = ("torch", "transformers", "PIL", "numpy", "scipy", "cv2")
    if os.path.abspath(python) == os.path.abspath(sys.executable):
        return [name for name in dependencies if importlib.util.find_spec(name) is None]
    # Inspect an explicitly selected environment's packages, without importing
    # Torch, starting its interpreter or touching CUDA during an HTTP request.
    executable = Path(shutil.which(os.path.expanduser(python)) or python)
    prefix = executable.parent.parent
    sites = list(prefix.glob("lib/python*/site-packages")) + list(prefix.glob("lib/python*/dist-packages"))
    configuration = prefix / "pyvenv.cfg"
    if configuration.is_file():
        values = dict(line.split("=", 1) for line in configuration.read_text(encoding="utf-8").splitlines() if "=" in line)
        values = {key.strip(): value.strip() for key, value in values.items()}
        if values.get("include-system-site-packages", "").lower() == "true" and values.get("home"):
            base = Path(values["home"]).parent
            sites += list(base.glob("lib/python*/site-packages")) + list(base.glob("lib/python*/dist-packages"))
    if prefix == Path("/usr"):
        sites += list(Path("/usr/local/lib").glob("python*/dist-packages"))
        sites += [Path("/usr/lib/python3/dist-packages")]
    return [name for name in dependencies if not any((site / name).exists() or list(site.glob(name + ".*.so")) for site in sites)]


def runtime_status() -> dict[str, Any]:
    """Check configured paths/argv only; never warm up a model in the server."""
    try:
        runner = _runner_template()
        if runner is not None:
            if not _executable_exists(runner[0]):
                return {"configured": False, "runtime_label": "configured runner", "message": "pose runner executable is unavailable"}
            for argument in runner[1:]:
                if argument.endswith(".py") and "{" not in argument and not Path(argument).expanduser().is_file():
                    return {"configured": False, "runtime_label": "configured runner", "message": "pose runner script is unavailable"}
            return {"configured": True, "runtime_label": "configured runner",
                    "message": "runner configured; checkpoint and device are checked when a job starts"}
        python = os.environ.get("SCENE_FEEDBACK_POSE_PYTHON", sys.executable)
        if not _executable_exists(python):
            return {"configured": False, "runtime_label": "local ViTPose", "message": "configured pose Python is unavailable"}
        missing = _missing_dependencies(python)
        if missing:
            return {"configured": False, "runtime_label": "local ViTPose", "message": "pose Python is missing: " + ", ".join(missing)}
        model = _model_path()
        missing_files = [name for name in MODEL_FILES if not (model / name).is_file()]
        if missing_files:
            return {"configured": False, "runtime_label": "local ViTPose", "message": "ViTPose checkpoint is missing: " + ", ".join(missing_files)}
        return {"configured": True, "runtime_label": "local ViTPose+ Base",
                "message": "dependencies and checkpoint files found; device checked when a job starts"}
    except (ValueError, OSError) as exc:
        return {"configured": False, "runtime_label": "ViTPose", "message": str(exc)}


def command_for_job(manifest_path: Path, output_path: Path) -> list[str]:
    manifest = str(Path(manifest_path).expanduser().resolve())
    output = str(Path(output_path).expanduser().resolve())
    runner = _runner_template()
    if runner is not None:
        return [argument.replace("{manifest}", manifest).replace("{output}", output) for argument in runner]
    return [os.environ.get("SCENE_FEEDBACK_POSE_PYTHON", sys.executable),
            str(ROOT / "scripts/run_pose_worker.py"), "--manifest", manifest, "--output", output,
            "--model-path", str(_model_path())]


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="pose-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(label + " must be finite")
    return float(value)


def clip_bbox(box: Any, width: int, height: int) -> list[float]:
    """Intersect a user COCO xywh box with the actual image; never invent one."""
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise ValueError("each frame needs an explicit human bbox_xywh in pixels")
    x, y, w, h = [_number(value, "bbox coordinate") for value in box]
    if w <= 0 or h <= 0:
        raise ValueError("human bbox width and height must be positive")
    left, top = max(0.0, x), max(0.0, y)
    right, bottom = min(float(width), x + w), min(float(height), y + h)
    if right - left < 2 or bottom - top < 2:
        raise ValueError("human bbox must intersect the image by at least 2 pixels per side")
    # Keep normalized boxes valid for the job manager, including a crop that
    # moves against an image boundary. Expansion stays inside the same ROI
    # neighborhood and never falls back to a whole-image person box.
    minimum_w, minimum_h = max(2.0, width * 0.0100000001), max(2.0, height * 0.0100000001)
    if right - left < minimum_w:
        left = min(float(width) - minimum_w, max(0.0, (left + right - minimum_w) / 2))
        right = left + minimum_w
    if bottom - top < minimum_h:
        top = min(float(height) - minimum_h, max(0.0, (top + bottom - minimum_h) / 2))
        bottom = top + minimum_h
    return [left, top, right - left, bottom - top]


def validate_manifest(manifest_path: Path) -> dict[str, Any]:
    path = Path(manifest_path).resolve(strict=True)
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("pose manifest exceeds 8 MiB")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version", 1) != 1:
        raise ValueError("pose manifest must be a schema_version 1 object")
    for name in ("job_id", "track_id", "view_id"):
        if not isinstance(manifest.get(name), str) or not manifest[name] or len(manifest[name]) > 200:
            raise ValueError(name + " must identify this single-view job")
    frames = manifest.get("frames")
    if not isinstance(frames, list) or not 1 <= len(frames) <= 600:
        raise ValueError("pose job must contain 1 to 600 frames")
    options = manifest.get("options", {})
    if not isinstance(options, dict):
        raise ValueError("pose options must be an object")
    threshold = _number(options.get("score_threshold", 0.3), "score_threshold")
    if not 0 <= threshold <= 1:
        raise ValueError("score_threshold must be between 0 and 1")
    if not isinstance(options.get("update_roi", True), bool):
        raise ValueError("update_roi must be boolean")
    previous_index = -1
    previous_time = -1.0
    for frame in frames:
        if not isinstance(frame, dict):
            raise ValueError("pose frames must be objects")
        if frame.get("view_id", manifest["view_id"]) != manifest["view_id"]:
            raise ValueError("a pose track cannot mix camera views")
        index = frame.get("frame_index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0 or index <= previous_index:
            raise ValueError("frame_index must be nonnegative and strictly increasing")
        time_seconds = _number(frame.get("time_seconds"), "time_seconds")
        if time_seconds < 0 or time_seconds < previous_time:
            raise ValueError("frame times must be nonnegative and nondecreasing")
        previous_index, previous_time = index, time_seconds
        image_path = frame.get("image_path")
        if not isinstance(image_path, str) or not image_path:
            raise ValueError("each frame needs an image_path")
        source = Path(image_path).expanduser()
        if not source.is_absolute():
            source = path.parent / source
        source = source.resolve(strict=True)
        if not source.is_file():
            raise ValueError("pose image must be a file")
        frame["image_path"] = str(source)
        # Dimensions are checked after the image is decoded. Check presence and
        # finite xywh now, before loading a costly checkpoint.
        box = frame.get("bbox_xywh")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            raise ValueError("each frame needs an explicit human bbox_xywh in pixels")
        for value in box:
            _number(value, "bbox coordinate")
        if box[2] <= 0 or box[3] <= 0:
            raise ValueError("human bbox width and height must be positive")
        if not isinstance(frame.get("reset_roi", False), bool):
            raise ValueError("reset_roi must be boolean")
        from PIL import Image
        with Image.open(source) as image:
            width, height = image.size
            if image.getexif().get(274) in (5, 6, 7, 8):
                width, height = height, width
        for key, actual in (("width", width), ("height", height)):
            if key in frame and frame[key] != actual:
                raise ValueError("manifest dimensions do not match the EXIF-oriented reference image")
        clip_bbox(box, width, height)
    return manifest


def normalized_keypoints(points: Any, scores: Any, width: int, height: int, threshold: float) -> list[dict[str, Any]]:
    if len(points) != 17 or len(scores) != 17:
        raise ValueError("ViTPose model must return exactly 17 COCO joints")
    result = []
    for index, (point, score) in enumerate(zip(points, scores)):
        px, py = _number(float(point[0]), "predicted x"), _number(float(point[1]), "predicted y")
        raw_score = _number(float(score), "predicted score")
        in_frame = 0 <= px < width and 0 <= py < height
        result.append({"id": index, "name": COCO_NAMES[index],
                       "x": min(1.0, max(0.0, px / width)), "y": min(1.0, max(0.0, py / height)),
                       "score": min(1.0, max(0.0, raw_score)), "raw_score": raw_score,
                       "in_frame": in_frame, "visible": in_frame and raw_score >= threshold,
                       "pixel_x": px, "pixel_y": py})
    return result


def tracking_quality(keypoints: list[dict[str, Any]]) -> bool:
    confident = [point for point in keypoints if point["visible"]]
    torso = [keypoints[index] for index in (5, 6, 11, 12) if keypoints[index]["visible"]]
    # Require upper and lower torso support, so visible feet alone cannot pull
    # the next crop toward the floor when the person is occluded.
    return len(confident) >= 6 and len(torso) >= 3 and any(keypoints[index]["visible"] for index in (5, 6)) and any(keypoints[index]["visible"] for index in (11, 12))


def update_bbox(box: list[float], points: list[dict[str, Any]], width: int, height: int) -> list[float]:
    confident = [point for point in points if point["visible"]]
    xs, ys = [point["pixel_x"] for point in confident], [point["pixel_y"] for point in confident]
    old_x, old_y, old_w, old_h = box
    span_w, span_h = max(xs) - min(xs), max(ys) - min(ys)
    # Padding retains limbs that currently have low confidence. Size changes
    # remain gradual; this is an ROI heuristic, not an identity tracker.
    target_w = min(old_w * 1.2, max(old_w * 0.9, span_w * 1.5))
    target_h = min(old_h * 1.2, max(old_h * 0.9, span_h * 1.4))
    target_x = (min(xs) + max(xs)) / 2
    target_y = (min(ys) + max(ys)) / 2
    center_x, center_y = old_x + old_w / 2, old_y + old_h / 2
    delta_x = min(old_w * 0.15, max(-old_w * 0.15, target_x - center_x))
    delta_y = min(old_h * 0.15, max(-old_h * 0.15, target_y - center_y))
    new_w, new_h = old_w * 0.7 + target_w * 0.3, old_h * 0.7 + target_h * 0.3
    new_w, new_h = min(float(width), new_w), min(float(height), new_h)
    # Shift an edge-bound crop inside the image instead of repeatedly cutting
    # off its size when the person's head is outside the camera frame.
    left = min(float(width) - new_w, max(0.0, center_x + delta_x * 0.5 - new_w / 2))
    top = min(float(height) - new_h, max(0.0, center_y + delta_y * 0.5 - new_h / 2))
    return clip_bbox([left, top, new_w, new_h], width, height)


def run_inference(manifest_path: Path, output_path: Path, *, model_path: Path | None = None,
                  progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Load a real checkpoint once and infer sequentially within supplied ROIs."""
    manifest = validate_manifest(manifest_path)
    options = manifest.get("options", {})
    threshold = float(options.get("score_threshold", 0.3))
    update_roi = options.get("update_roi", True)
    model_directory = Path(model_path or manifest.get("model_path") or _model_path()).expanduser().resolve(strict=True)
    if not all((model_directory / name).is_file() for name in MODEL_FILES):
        raise ValueError("ViTPose+ Base checkpoint must contain config, processor config and safetensors weights")
    import torch
    import transformers
    from PIL import Image, ImageOps
    from transformers import VitPoseForPoseEstimation, VitPoseImageProcessor

    requested_device = options.get("device", "auto")
    if not isinstance(requested_device, str) or requested_device not in {"auto", "cpu", "cuda", "cuda:0"}:
        raise ValueError("device must be auto, cpu or cuda")
    device = "cuda:0" if requested_device == "auto" and torch.cuda.is_available() else "cpu" if requested_device == "auto" else requested_device
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is unavailable in the configured pose Python")
    torch.set_num_threads(4)
    model = VitPoseForPoseEstimation.from_pretrained(str(model_directory), local_files_only=True).to(device).eval()
    processor = VitPoseImageProcessor.from_pretrained(str(model_directory), local_files_only=True)
    if (len(model.config.id2label) != 17 or getattr(model.config.backbone_config, "num_experts", 0) != 6
            or getattr(model.config.backbone_config, "hidden_size", 0) != 768):
        raise ValueError("configured checkpoint is not the 17-joint ViTPose+ Base model")
    digest = hashlib.sha256()
    with (model_directory / "model.safetensors").open("rb") as checkpoint:
        for chunk in iter(lambda: checkpoint.read(2 * 1024 * 1024), b""):
            digest.update(chunk)
    emit = progress or (lambda value: None)
    emit({"type": "progress", "completed_frames": 0, "total_frames": len(manifest["frames"]), "stage": "model_loaded"})
    results = []
    previous_box = None
    previous_dimensions = None
    for completed, frame in enumerate(manifest["frames"], 1):
        with Image.open(frame["image_path"]) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
        width, height = image.size
        manual_box = clip_bbox(frame["bbox_xywh"], width, height)
        dimensions = (width, height)
        if previous_box is not None and update_roi and not frame.get("reset_roi"):
            if previous_dimensions != dimensions:
                raise ValueError("ROI tracking requires consistent image dimensions within a camera view")
            box = clip_bbox(previous_box, width, height)
        else:
            box = manual_box
        boxes = [[box]]
        inputs = processor(images=image, boxes=boxes, return_tensors="pt")
        inputs = {name: value.to(device) for name, value in inputs.items()}
        inputs["dataset_index"] = torch.zeros(len(inputs["pixel_values"]), dtype=torch.long, device=device)
        with torch.inference_mode():
            prediction = model(**inputs)
        person = processor.post_process_pose_estimation(prediction, boxes=boxes)[0][0]
        keypoints = normalized_keypoints(person["keypoints"].detach().cpu().tolist(),
                                         person["scores"].detach().cpu().tolist(), width, height, threshold)
        tracked = tracking_quality(keypoints)
        roi_status = "updated" if tracked and update_roi else "held_low_confidence" if not tracked else "fixed"
        result = {key: frame[key] for key in ("image_path", "frame_index", "time_seconds", "ref_id", "image_id") if key in frame}
        result.update({"view_id": manifest["view_id"], "track_id": manifest["track_id"],
                       "width": width, "height": height, "bbox_xywh": box,
                       "bbox": {"x": box[0] / width, "y": box[1] / height, "width": box[2] / width, "height": box[3] / height},
                       "keypoints": keypoints, "tracking_status": "tracked" if tracked else "lost", "roi_status": roi_status})
        results.append(result)
        previous_box = update_bbox(box, keypoints, width, height) if tracked and update_roi else box
        previous_dimensions = dimensions
        emit({"type": "progress", "completed_frames": completed, "total_frames": len(manifest["frames"])})
    result = {"schema_version": 1, "job_id": manifest["job_id"], "track_id": manifest["track_id"], "view_id": manifest["view_id"],
              "model": {"name": "ViTPose+ Base", "path": str(model_directory), "dataset": "COCO", "dataset_index": 0,
                        "checkpoint_sha256": digest.hexdigest(), "source": "local_checkpoint",
                        "joints": 17, "joint_names": list(COCO_NAMES), "edges": [list(edge) for edge in COCO_EDGES],
                        "device": device, "torch_version": str(torch.__version__), "transformers_version": transformers.__version__,
                        "gpu_name": torch.cuda.get_device_name(0) if device.startswith("cuda") else None},
              "tracking": {"method": "single_person_confidence_gated_roi" if update_roi else "fixed_manual_roi",
                           "scope": "single_view", "identity_guaranteed": False, "score_threshold": threshold,
                           "coordinate_space": "exif_oriented_display",
                           "visibility_method": "confidence_and_image_bounds_not_occlusion_segmentation",
                           "raw_score_kind": "heatmap_peak_not_calibrated_probability"}, "frames": results}
    atomic_json(Path(output_path), result)
    return result

"""Standalone, per-camera ViTPose observer for exported workbench sources.

Source validation runs before importing Torch or loading any checkpoint.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import struct
import tempfile
from typing import Any, Callable

from wholebody_profile import (COCO17_NAMES, COCO17_PROFILE, WHOLEBODY133_NAMES,
                               WHOLEBODY133_PROFILE, WHOLEBODY133_EDGES,
                               WHOLEBODY133_GROUPS)

COCO_NAMES = COCO17_NAMES
COCO_EDGES = ((15, 13), (13, 11), (16, 14), (14, 12), (11, 12), (5, 11),
              (6, 12), (5, 6), (5, 7), (6, 8), (7, 9), (8, 10),
              (1, 2), (0, 1), (0, 2), (1, 3), (2, 4), (3, 5), (4, 6))
MODEL_FILES = ("config.json", "preprocessor_config.json", "model.safetensors")
DETECTOR_FILENAME = "fasterrcnn_mobilenet_v3_large_320_fpn-907ea3f9.pth"
DETECTOR_SHA256 = "907ea3f91ff92242bc1baea8049276a3e76bca48ce7560bd268cc029f37977b5"
MAX_SOURCE_FRAMES = 8 * 10000
SUPPORTED_PROFILES = (WHOLEBODY133_PROFILE, COCO17_PROFILE)


def pose_topology(profile: str) -> tuple[tuple[str, ...], tuple[tuple[int, int], ...], int]:
    if profile == WHOLEBODY133_PROFILE:
        return WHOLEBODY133_NAMES, WHOLEBODY133_EDGES, 5
    if profile == COCO17_PROFILE:
        return COCO17_NAMES, COCO_EDGES, 0
    raise ValueError("unsupported pose profile: " + str(profile))


def validate_model_head(model_directory: Path, profile: str) -> None:
    """Reject mislabeled or incomplete checkpoints before Torch/GPU loads.

    HF's ViTPose+ Base has six backbone experts but its public COCO head has
    only 17 channels. Expert 5 alone cannot make WholeBody133 predictions.
    """
    model_directory = Path(model_directory)
    names, _, _ = pose_topology(profile)
    config = json.loads((model_directory / "config.json").read_text(encoding="utf-8"))
    labels = config.get("id2label")
    backbone = config.get("backbone_config") or {}
    if not isinstance(labels, dict) or len(labels) != len(names) or backbone.get("num_experts") != 6:
        raise ValueError(f"{profile} needs a genuine {len(names)}-joint ViTPose+ checkpoint; dataset_index alone does not change the head")
    weights = model_directory / "model.safetensors"
    with weights.open("rb") as stream:
        size_raw = stream.read(8)
        if len(size_raw) != 8:
            raise ValueError("ViTPose safetensors header is missing")
        header_size = struct.unpack("<Q", size_raw)[0]
        if not 0 < header_size <= 16 * 1024 * 1024:
            raise ValueError("ViTPose safetensors header is invalid")
        header = json.loads(stream.read(header_size))
    weight = header.get("head.conv.weight")
    bias = header.get("head.conv.bias")
    if (not isinstance(weight, dict) or not isinstance(bias, dict)
            or weight.get("shape") != [len(names), 256, 1, 1]
            or bias.get("shape") != [len(names)]):
        raise ValueError(f"{profile} needs a trained {len(names)}-channel pose head in model.safetensors")


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
    # Keep normalized boxes valid for workbench import, including a crop that
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


def validate_manifest(manifest_path: Path, *, auto_detect: bool | None = None) -> dict[str, Any]:
    path = Path(manifest_path).resolve(strict=True)
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("pose manifest exceeds 64 MiB")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version", 1) not in (1, 2):
        raise ValueError("pose manifest must be a schema_version 1 or 2 object")
    for binding in ("project_id", "session_id", "source_snapshot_id"):
        value = manifest.get(binding)
        if not isinstance(value, str) or not value or len(value) > 200:
            raise ValueError("exported workbench manifest requires " + binding)
    version = manifest.get("schema_version", 1)
    for name in ("job_id", "track_id"):
        if not isinstance(manifest.get(name), str) or not manifest[name] or len(manifest[name]) > 200:
            raise ValueError(name + " must identify this pose job")
    if version == 1:
        view_id = manifest.get("view_id")
        if not isinstance(view_id, str) or not view_id or len(view_id) > 200:
            raise ValueError("view_id must identify this single-view job")
        view_ids = [view_id]
    else:
        view_ids = manifest.get("view_ids")
        if (not isinstance(view_ids, list) or not 2 <= len(view_ids) <= 8
                or any(not isinstance(view_id, str) or not view_id or len(view_id) > 200 for view_id in view_ids)
                or len(set(view_ids)) != len(view_ids)):
            raise ValueError("multi-view pose manifest needs 2 to 8 distinct view_ids")
    options = manifest.get("options", {})
    if not isinstance(options, dict):
        raise ValueError("pose options must be an object")
    if not isinstance(options.get("auto_detect", False), bool):
        raise ValueError("auto_detect must be boolean")
    frames = manifest.get("frames")
    if auto_detect is not None and type(auto_detect) is not bool:
        raise ValueError("auto_detect override must be boolean")
    # A workbench export names immutable source frames, not an algorithm. A
    # caller choosing a detector can infer its ROIs without editing that export.
    automatic = auto_detect if auto_detect is not None else options.get("auto_detect", False) or (
        isinstance(frames, list) and bool(frames) and all("bbox_xywh" not in frame for frame in frames if isinstance(frame, dict)))
    frame_limit = MAX_SOURCE_FRAMES
    if not isinstance(frames, list) or not 1 <= len(frames) <= frame_limit:
        raise ValueError(f"pose job must contain 1 to {frame_limit} frames")
    threshold = _number(options.get("score_threshold", 0.3), "score_threshold")
    if not 0 <= threshold <= 1:
        raise ValueError("score_threshold must be between 0 and 1")
    if not isinstance(options.get("update_roi", True), bool):
        raise ValueError("update_roi must be boolean")
    previous_by_view: dict[str, tuple[int, float]] = {}
    for frame in frames:
        if not isinstance(frame, dict):
            raise ValueError("pose frames must be objects")
        if not isinstance(frame.get("ref_id"), str) or not frame["ref_id"]:
            raise ValueError("each frame needs its exact exported ref_id")
        source_digest = frame.get("image_sha256")
        if not isinstance(source_digest, str) or len(source_digest) != 64 or any(char not in "0123456789abcdef" for char in source_digest):
            raise ValueError("each frame needs its exported image_sha256")
        if frame.get("image_orientation") != "exif_oriented_display":
            raise ValueError("source frame must declare exif_oriented_display orientation")
        current_view = frame.get("view_id", view_ids[0] if version == 1 else None)
        if not isinstance(current_view, str) or not current_view or len(current_view) > 200:
            raise ValueError("pose frame needs a valid view_id")
        if version == 1 and current_view != view_ids[0]:
            raise ValueError("a pose track cannot mix camera views")
        if current_view not in view_ids:
            raise ValueError("pose frame references an undeclared camera view")
        previous_index, previous_time = previous_by_view.get(current_view, (-1, -1.0))
        index = frame.get("frame_index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0 or index <= previous_index:
            raise ValueError("frame_index must be nonnegative and strictly increasing")
        time_seconds = _number(frame.get("time_seconds"), "time_seconds")
        if time_seconds < 0 or time_seconds < previous_time:
            raise ValueError("frame times must be nonnegative and nondecreasing")
        previous_by_view[current_view] = (index, time_seconds)
        image_path = frame.get("image_path")
        if not isinstance(image_path, str) or not image_path:
            raise ValueError("each frame needs an image_path")
        source = Path(image_path).expanduser()
        if not source.is_absolute():
            source = path.parent / source
        source = source.resolve(strict=True)
        if not source.is_file():
            raise ValueError("pose image must be a file")
        actual_digest = hashlib.sha256()
        with source.open("rb") as image_stream:
            for chunk in iter(lambda: image_stream.read(1024 * 1024), b""):
                actual_digest.update(chunk)
        if actual_digest.hexdigest() != source_digest:
            raise ValueError("exported source image_sha256 changed: " + frame["ref_id"])
        frame["image_path"] = str(source)
        # Dimensions are checked after the image is decoded. Check presence and
        # finite xywh now, before loading a costly checkpoint.
        box = frame.get("bbox_xywh")
        if automatic:
            if box is not None:
                raise ValueError("automatic pose frames must not supply a manual person bbox")
        else:
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
        if not automatic:
            clip_bbox(box, width, height)
    if version == 2 and set(previous_by_view) != set(view_ids):
        raise ValueError("each declared pose camera view needs at least one frame")
    return manifest


def normalized_keypoints(points: Any, scores: Any, width: int, height: int, threshold: float,
                         names: tuple[str, ...] = COCO_NAMES) -> list[dict[str, Any]]:
    if len(points) != len(names) or len(scores) != len(names):
        raise ValueError(f"ViTPose model must return exactly {len(names)} declared joints")
    result = []
    for index, (point, score) in enumerate(zip(points, scores)):
        px, py = _number(float(point[0]), "predicted x"), _number(float(point[1]), "predicted y")
        raw_score = _number(float(score), "predicted score")
        in_frame = 0 <= px < width and 0 <= py < height
        result.append({"id": index, "name": names[index],
                       "x": min(1.0, max(0.0, px / width)), "y": min(1.0, max(0.0, py / height)),
                       "score": min(1.0, max(0.0, raw_score)), "raw_score": raw_score,
                       "in_frame": in_frame, "visible": in_frame and raw_score >= threshold,
                       "pixel_x": px, "pixel_y": py})
    return result


def tracking_quality(keypoints: list[dict[str, Any]]) -> bool:
    # Hands and face can remain confidently predicted while the torso is lost.
    # They must not preserve or steer the person ROI on their own.
    confident = [point for point in keypoints[:17] if point["visible"]]
    torso = [keypoints[index] for index in (5, 6, 11, 12) if keypoints[index]["visible"]]
    # Require upper and lower torso support, so visible feet alone cannot pull
    # the next crop toward the floor when the person is occluded.
    return len(confident) >= 6 and len(torso) >= 3 and any(keypoints[index]["visible"] for index in (5, 6)) and any(keypoints[index]["visible"] for index in (11, 12))


def update_bbox(box: list[float], points: list[dict[str, Any]], width: int, height: int) -> list[float]:
    confident = [point for point in points[:17] if point["visible"]]
    if not confident:
        return box
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


def select_person_detection(prediction: dict[str, Any], width: int, height: int,
                            previous_box: list[float] | None = None, threshold: float = .3) -> tuple[list[float], float] | None:
    """Choose one detector-backed person, favoring ROI continuity when available."""
    labels = prediction["labels"]
    scores = prediction["scores"]
    boxes = prediction["boxes"]
    if hasattr(labels, "detach"):
        labels, scores, boxes = (value.detach().cpu().tolist() for value in (labels, scores, boxes))
    candidates = []
    for label, score, raw in zip(labels, scores, boxes):
        if label != 1 or not math.isfinite(float(score)) or score < threshold or len(raw) != 4:
            continue
        left, top, right, bottom = [float(value) for value in raw]
        if not all(math.isfinite(value) for value in (left, top, right, bottom)):
            continue
        left, top = max(0, left), max(0, top)
        right, bottom = min(width, right), min(height, bottom)
        if right - left < max(2, width * .01) or bottom - top < max(2, height * .01):
            continue
        box = [left, top, right - left, bottom - top]
        area = box[2] * box[3] / (width * height)
        rating = float(score) * math.sqrt(area)
        if previous_box is not None:
            px, py, pw, ph = previous_box
            overlap = max(0, min(right, px + pw) - max(left, px)) * max(0, min(bottom, py + ph) - max(top, py))
            union = box[2] * box[3] + pw * ph - overlap
            iou = overlap / union if union > 0 else 0
            rating *= 1 + 2 * iou
        candidates.append((rating, box, float(score)))
    if not candidates:
        return None
    _, box, score = max(candidates, key=lambda item: item[0])
    x, y, w, h = box
    return clip_bbox([x - w * .08, y - h * .08, w * 1.16, h * 1.16], width, height), score


def _empty_keypoints(names: tuple[str, ...] = COCO_NAMES) -> list[dict[str, Any]]:
    return [{"id": index, "name": name, "x": 0., "y": 0., "score": 0., "raw_score": 0.,
             "in_frame": False, "visible": False, "pixel_x": 0., "pixel_y": 0.}
            for index, name in enumerate(names)]


def run_inference(manifest_path: Path, output_path: Path, *, model_path: Path | None = None,
                  detector_path: Path | None = None,
                  auto_detect: bool | None = None,
                  profile: str = WHOLEBODY133_PROFILE,
                  progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Load checkpoints once and infer every source frame with independent ROIs."""
    if auto_detect is None:
        auto_detect = detector_path is not None
    names, edges, dataset_index = pose_topology(profile)
    manifest = validate_manifest(manifest_path, auto_detect=auto_detect)
    options = manifest.get("options", {})
    threshold = float(options.get("score_threshold", 0.3))
    update_roi = options.get("update_roi", True)
    automatic = auto_detect
    if model_path is None:
        raise ValueError("provide --model-path for a local ViTPose+ Base checkpoint")
    if automatic and detector_path is None:
        raise ValueError("provide --detector-path for the local person detector checkpoint")
    model_directory = Path(model_path).expanduser().resolve(strict=True)
    if not all((model_directory / name).is_file() for name in MODEL_FILES):
        raise ValueError("ViTPose+ checkpoint must contain config, processor config and safetensors weights")
    validate_model_head(model_directory, profile)
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
    if (len(model.config.id2label) != len(names) or model.head.conv.out_channels != len(names)
            or getattr(model.config.backbone_config, "num_experts", 0) != 6):
        raise ValueError("loaded checkpoint does not match the declared ViTPose+ joint profile")
    digest = hashlib.sha256()
    with (model_directory / "model.safetensors").open("rb") as checkpoint:
        for chunk in iter(lambda: checkpoint.read(2 * 1024 * 1024), b""):
            digest.update(chunk)
    detector = None
    detector_transform = None
    if automatic:
        from torchvision.models.detection import (FasterRCNN_MobileNet_V3_Large_320_FPN_Weights,
                                                   fasterrcnn_mobilenet_v3_large_320_fpn)
        detector_path = Path(detector_path).expanduser().resolve(strict=True)
        detector_digest = hashlib.sha256()
        with detector_path.open("rb") as checkpoint:
            for chunk in iter(lambda: checkpoint.read(2 * 1024 * 1024), b""):
                detector_digest.update(chunk)
        if detector_digest.hexdigest() != DETECTOR_SHA256:
            raise ValueError("person detector checkpoint SHA-256 does not match the official COCO weights")
        detector = fasterrcnn_mobilenet_v3_large_320_fpn(weights=None, weights_backbone=None)
        detector.load_state_dict(torch.load(detector_path, map_location="cpu", weights_only=True))
        detector = detector.to(device).eval()
        detector_transform = FasterRCNN_MobileNet_V3_Large_320_FPN_Weights.DEFAULT.transforms()
    emit = progress or (lambda value: None)
    emit({"type": "progress", "completed_frames": 0, "total_frames": len(manifest["frames"]), "stage": "model_loaded"})
    results = []
    previous_by_view: dict[str, tuple[list[float] | None, tuple[int, int], bool, int]] = {}
    for completed, frame in enumerate(manifest["frames"], 1):
        with Image.open(frame["image_path"]) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
        width, height = image.size
        dimensions = (width, height)
        current_view = frame.get("view_id", manifest.get("view_id"))
        previous = previous_by_view.get(current_view)
        previous_box, previous_dimensions, previous_tracked, since_detection = previous if previous is not None else (None, None, False, 0)
        if previous_dimensions is not None and previous_dimensions != dimensions:
            raise ValueError("ROI tracking requires consistent image dimensions within a camera view")
        detector_score = None
        detected = False
        if automatic:
            if previous_box is None or not previous_tracked or since_detection >= 30 or frame.get("reset_roi"):
                with torch.inference_mode():
                    prediction = detector([detector_transform(image).to(device)])[0]
                selected = select_person_detection(prediction, width, height, previous_box)
                if selected is not None:
                    box, detector_score = selected
                    detected = True
                else:
                    box = previous_box
            else:
                box = previous_box
            if box is None:
                keypoints = _empty_keypoints(names)
                result = {key: frame[key] for key in ("image_path", "frame_index", "time_seconds", "ref_id", "image_id",
                                                       "image_sha256", "image_orientation", "camera", "clip_id", "view_name") if key in frame}
                result.update({"view_id": current_view, "track_id": manifest["track_id"],
                               "width": width, "height": height, "bbox_xywh": None, "bbox": None,
                               "keypoints": keypoints, "tracking_status": "lost", "roi_status": "no_person_detected"})
                results.append(result)
                previous_by_view[current_view] = (None, dimensions, False, 0)
                emit({"type": "progress", "completed_frames": completed, "total_frames": len(manifest["frames"])})
                continue
        else:
            manual_box = clip_bbox(frame["bbox_xywh"], width, height)
            if previous_box is not None and update_roi and not frame.get("reset_roi"):
                box = clip_bbox(previous_box, width, height)
            else:
                box = manual_box
        boxes = [[box]]
        inputs = processor(images=image, boxes=boxes, return_tensors="pt")
        inputs = {name: value.to(device) for name, value in inputs.items()}
        inputs["dataset_index"] = torch.full((len(inputs["pixel_values"]),), dataset_index,
                                              dtype=torch.long, device=device)
        with torch.inference_mode():
            prediction = model(**inputs)
        person = processor.post_process_pose_estimation(prediction, boxes=boxes)[0][0]
        keypoints = normalized_keypoints(person["keypoints"].detach().cpu().tolist(),
                                         person["scores"].detach().cpu().tolist(), width, height, threshold, names)
        tracked = tracking_quality(keypoints)
        roi_status = ("detected" if previous is None else "re_detected") if detected else (
            "updated" if tracked and update_roi else "held_low_confidence" if not tracked else "fixed")
        result = {key: frame[key] for key in ("image_path", "frame_index", "time_seconds", "ref_id", "image_id",
                                               "image_sha256", "image_orientation", "camera", "clip_id", "view_name") if key in frame}
        result.update({"view_id": current_view, "track_id": manifest["track_id"],
                       "width": width, "height": height, "bbox_xywh": box,
                       "bbox": {"x": box[0] / width, "y": box[1] / height, "width": box[2] / width, "height": box[3] / height},
                       "keypoints": keypoints,
                       "tracking_status": "tracked" if tracked or detected or not automatic else "lost",
                       "roi_body_quality": tracked, "roi_status": roi_status,
                       **({"detector_score": detector_score} if detector_score is not None else {})})
        results.append(result)
        previous_by_view[current_view] = (update_bbox(box, keypoints, width, height) if tracked and update_roi else box,
                                          dimensions, tracked, 0 if detected else since_detection + 1)
        emit({"type": "progress", "completed_frames": completed, "total_frames": len(manifest["frames"])})
    multi_view = manifest.get("schema_version", 1) == 2
    result = {"schema_version": 2 if multi_view else 1, "job_id": manifest["job_id"], "track_id": manifest["track_id"],
              "project_id": manifest["project_id"], "session_id": manifest["session_id"],
              "source_snapshot_id": manifest["source_snapshot_id"], "evidence_kind": "observed_2d",
              "provenance": {"kind": "image_inference", "method": f"Faster R-CNN person detection and ViTPose+ {profile}" if automatic else f"ViTPose+ {profile} on supplied ROI",
                             "source_artifact": str(model_directory / "model.safetensors"),
                             **({"detector_artifact": str(detector_path)} if automatic else {})},
              "keypoint_profile": profile, "keypoint_names": list(names),
              "skeleton_edges": [list(edge) for edge in edges],
              **({"keypoint_groups": WHOLEBODY133_GROUPS} if profile == WHOLEBODY133_PROFILE else {}),
              **({"view_ids": manifest["view_ids"]} if multi_view else {"view_id": manifest["view_id"]}),
              "model": {"name": "ViTPose+", "path": str(model_directory), "dataset": "COCO-WholeBody" if dataset_index == 5 else "COCO", "dataset_index": dataset_index,
                        "checkpoint_sha256": digest.hexdigest(), "source": "local_checkpoint",
                        "joints": len(names), "joint_names": list(names), "edges": [list(edge) for edge in edges],
                        "device": device, "torch_version": str(torch.__version__), "transformers_version": transformers.__version__,
                        "gpu_name": torch.cuda.get_device_name(0) if device.startswith("cuda") else None},
              "tracking": {"method": "automatic_person_detection_and_confidence_gated_roi" if automatic else
                                      "single_person_confidence_gated_roi" if update_roi else "fixed_manual_roi",
                           "scope": "per_view_independent" if multi_view else "single_view",
                           **({"cross_view_identity_source": "automatic_dominant_person_per_view" if automatic else "user_designated_boxes"} if multi_view else {}),
                           "identity_guaranteed": False, "score_threshold": threshold,
                           "coordinate_space": "exif_oriented_display",
                           "visibility_method": "confidence_and_image_bounds_not_occlusion_segmentation",
                           "raw_score_kind": "heatmap_peak_not_calibrated_probability",
                           **({"person_detector": "torchvision_fasterrcnn_mobilenet_v3_large_320_fpn_coco_v1",
                               "person_detector_sha256": DETECTOR_SHA256,
                               "selection": "confidence_area_with_per_view_roi_continuity",
                               "redetection_interval_frames": 30} if automatic else {})}, "frames": results}
    atomic_json(Path(output_path), result)
    return result

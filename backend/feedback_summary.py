"""Compact model-facing feedback; immutable packets remain in the store."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


def _fields(value: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    return {name: copy.deepcopy(value[name]) for name in names if name in value}


SOURCE_FIELDS = (
    "id", "name", "label", "pane", "reference_id", "reference_name", "reference_frame_id",
    "reference_image_id", "frame_id", "clip_id",
    "view_id", "view_name", "frame_index", "time_sec", "reference_time_sec",
    "scene_revision", "from_stale_snapshot", "width", "height", "image_width",
    "image_height", "source_image_width", "source_image_height", "image_sha256",
    "image_orientation", "source_snapshot_id", "tracking_status",
    "selected_object_ids", "selected_scene_nodes", "animation_clips",
)
POSE_FIELDS = (
    "job_id", "parent_job_id", "track_id", "source", "evidence_kind",
    "parent_evidence_kind", "coordinate_frame", "keypoint_profile",
    "confidence_threshold", "multi_view", "view_ids", "cross_view_identity_source",
    "corrections_path", "parent_result_path", "source_manifest_path",
)


def feedback_manifest(feedback: dict[str, Any], data_dir: Path) -> dict[str, Any]:
    """Describe each source once, without embedding whole pose archives.

    Short source keys bind image captions to this manifest. Times stay exact
    numeric values and frame_index retains the stored zero-based convention.
    Camera references share one description; full camera matrices, topology,
    predictions and correction documents can be read from the immutable packet.
    """
    result = _fields(feedback, (
        "feedback_id", "session_id", "scene_revision", "delivery_scene_revision",
        "submitted_from_stale_snapshot", "note", "object_prompts",
        "selected_object_ids", "selected_scene_nodes", "active_reference_id",
        "aligned_reference_id", "timeline",
    ))
    result["details"] = {"state_path": str((data_dir / "state.json").resolve()),
                         "feedback_id": feedback["feedback_id"]}
    cameras: dict[str, dict[str, Any]] = {}
    camera_ids: dict[str, str] = {}

    def camera(value: Any) -> str | None:
        if not isinstance(value, dict) or not value:
            return None
        signature = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if signature not in camera_ids:
            key = "cam" + str(len(cameras) + 1)
            camera_ids[signature] = key
            summary = _fields(value, (
                "position", "target", "up", "fov", "aspect", "near", "far",
                "reference_image_id", "alignment_exact", "calibration_status",
                "image_undistorted", "coordinate_frame", "intrinsics", "projection",
            ))
            if "camera_to_world" in value:
                summary["calibrated"] = True
            cameras[key] = summary
        return camera_ids[signature]

    def comparison(value: dict[str, Any]) -> dict[str, Any]:
        summary = _fields(value, ("reference_id", "reference_name", "source", "enabled", "opacity",
                                  "rect", "alignment_exact", "coordinate_space"))
        camera_key = camera(value.get("reference_camera"))
        if camera_key:
            summary["reference_camera"] = camera_key
        return summary

    def source(value: dict[str, Any], key: str) -> dict[str, Any]:
        summary = {"key": key, **_fields(value, SOURCE_FIELDS)}
        camera_key = camera(value.get("camera"))
        if camera_key:
            summary["camera"] = camera_key
        reference_camera = camera(value.get("reference_camera"))
        if reference_camera:
            summary["reference_camera"] = reference_camera
        if value.get("comparison"):
            summary["comparison"] = comparison(value["comparison"])
        return summary

    camera_key = camera(feedback.get("camera"))
    if camera_key:
        result["camera"] = camera_key
    if feedback.get("comparison"):
        result["comparison"] = comparison(feedback["comparison"])
    for field, prefix in (("reference_images", "R"), ("image_refs", "I"),
                          ("scene_snapshots", "S"), ("dynamic_frames", "F")):
        if feedback.get(field):
            result[field] = [source(item, f"{prefix}{index}")
                             for index, item in enumerate(feedback[field], 1)]
            if field == "image_refs":
                for item in result[field]:
                    item["token"] = f"[[image:{item['id']}]]"
    if feedback.get("reference_annotated_images"):
        reference_keys = {item["id"]: item["key"] for item in result.get("reference_images", [])}
        result["reference_annotated_images"] = [
            {"key": reference_keys.get(item["reference_id"], f"RA{index}"), "reference_id": item["reference_id"]}
            for index, item in enumerate(feedback["reference_annotated_images"], 1)
        ]
    if feedback.get("crops"):
        result["crops"] = [{"key": f"crop{index}", **_fields(item, ("source", "reference_id"))}
                           for index, item in enumerate(feedback["crops"], 1)]
    if feedback.get("inline_references"):
        references = []
        for item in feedback["inline_references"]:
            reference = _fields(item, ("token", "kind", "id", "from_stale_snapshot"))
            if item.get("kind") == "object":
                reference["object"] = _fields(item.get("object", {}), ("id", "name", "type"))
            elif item.get("kind") == "node":
                reference["scene_node"] = copy.deepcopy(item.get("scene_node"))
            elif item.get("kind") == "annotation":
                reference["annotation"] = _fields(item.get("annotation", {}), ("id", "name", "type", "pane"))
            elif item.get("kind") == "image":
                reference["image"] = {"id": item["id"]}
            # Annotation/image details live in their own lists, once per source.
            references.append(reference)
        result["inline_references"] = references
    if feedback.get("annotations"):
        result["annotations"] = []
        for item in feedback["annotations"]:
            annotation = {name: copy.deepcopy(value) for name, value in item.items()
                          if name != "camera" and not name.endswith("_url")}
            camera_key = camera(item.get("camera"))
            if camera_key:
                annotation["camera"] = camera_key
            result["annotations"].append(annotation)
    if feedback.get("human_pose"):
        result["human_pose"] = []
        for index, pose in enumerate(feedback["human_pose"], 1):
            summary = {"key": f"P{index}", **_fields(pose, POSE_FIELDS)}
            summary["frame"] = source(pose["frame"], f"P{index}")
            job_id = pose.get("job_id")
            if job_id:
                directory = data_dir / "human_pose" / job_id
                manifest_path = directory / "input.json"
                if "source_manifest_path" not in summary and manifest_path.is_file():
                    summary["source_manifest_path"] = str(manifest_path)
                if "parent_result_path" not in summary:
                    result_path = next((directory / name for name in ("output.json", "job.json")
                                        if (directory / name).is_file()), None)
                    if result_path:
                        summary["parent_result_path"] = str(result_path)
            result["human_pose"].append(summary)
    if feedback.get("human_pose_edits"):
        result["human_pose_edits"] = []
        for index, pose in enumerate(feedback["human_pose_edits"], 1):
            summary = {"key": f"E{index}", "id": pose["id"],
                       "token": f"[[pose_edit:{pose['id']}]]", **_fields(pose, POSE_FIELDS)}
            summary["frame"] = source(pose["frame"], f"E{index}")
            document = pose.get("document", {})
            if "source_snapshot_id" in document:
                summary["source_snapshot_id"] = document["source_snapshot_id"]
            frames = document.get("frames", [])
            originals = frames[0].get("original_keypoints", []) if frames else pose["frame"].get("keypoints", [])
            names = document.get("keypoint_names", pose.get("keypoint_names", []))
            original_by_name = {point.get("name", names[index] if index < len(names) else ""): point
                                for index, point in enumerate(originals)}
            summary["edits"] = []
            for edit in pose.get("edits", []):
                summary["edits"].append({
                    "name": edit["name"],
                    "before": _fields(original_by_name.get(edit["name"], {}),
                                      ("x", "y", "score", "in_frame", "manual_visibility")),
                    "after": _fields(edit, ("x", "y", "visibility")),
                })
            result["human_pose_edits"].append(summary)
    if cameras:
        result["cameras"] = cameras
    return result


def feedback_result_manifest(result: dict[str, Any], data_dir: Path) -> dict[str, Any]:
    summary = {key: copy.deepcopy(value) for key, value in result.items() if key != "items"}
    summary["format"] = "feedback_summary"
    summary["read_details"] = "Full immutable packets and camera matrices: state_path → feedback by feedback_id; MCP structuredContent retains all fields. Prompt #frame is one-based; JSON frame_index is zero-based; times are clip-relative seconds."
    summary["items"] = [feedback_manifest(item, data_dir) for item in result.get("items", [])]
    return summary


def caption(source_key: str, role: str) -> str:
    return f"{source_key} · {role}"

#!/usr/bin/env python3
"""Validate workbench pose sources/results and prepare observed 2D handoffs.

Inference is an explicit command. Export/import themselves are MCP calls; this
script validates their local files and prints the exact import arguments.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import shutil
from typing import Any

from pose_worker import COCO_NAMES, run_inference, validate_manifest
from wholebody_profile import (COCO17_PROFILE, WHOLEBODY133_PROFILE,
                               WHOLEBODY133_NAMES, WHOLEBODY133_EDGES,
                               WHOLEBODY133_GROUPS)


def _finite(value: Any, label: str, *, minimum: float | None = None,
            maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(label + " must be a finite number")
    result = float(value)
    if minimum is not None and result < minimum or maximum is not None and result > maximum:
        raise ValueError(label + " is out of range")
    return result


def _json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(str(path) + " must contain a JSON object")
    return document


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_view(manifest: dict, source: dict) -> str:
    return source.get("view_id", manifest.get("view_id"))


def source_summary(path: Path, manifest: dict) -> dict:
    counts: dict[str, int] = {}
    for source in manifest["frames"]:
        view = _source_view(manifest, source)
        counts[view] = counts.get(view, 0) + 1
    return {"manifest_path": str(path.resolve()), "manifest_sha256": _digest(path),
            **{key: manifest[key] for key in ("project_id", "session_id", "source_snapshot_id", "job_id", "track_id")},
            "project_dir": manifest.get("project_dir"),
            "source_frames": len(manifest["frames"]), "views": counts,
            "calibrated_frames": sum(bool(frame.get("camera")) for frame in manifest["frames"]),
            "source_kind": "reference_snapshot", "inference_method": "chosen by the skill, not prescribed by the source export"}


def _project_result_path(manifest: dict, path: Path) -> Path:
    value = manifest.get("project_dir")
    if not isinstance(value, str) or not value:
        raise ValueError("source export needs its actual project_dir for project-local result_path")
    project = Path(value).expanduser().resolve(strict=True)
    output = path.expanduser().resolve()
    if not output.is_relative_to(project):
        raise ValueError("pose result_path must remain inside the exported project_dir")
    return output


def _topology(result: dict) -> tuple[list[str], list[list[int]]]:
    names = result.get("keypoint_names")
    if not isinstance(names, list) or not 1 <= len(names) <= 256 or any(
            not isinstance(name, str) or not name.strip() or len(name) > 96 or
            any(ord(char) < 32 for char in name) for name in names) or len(set(names)) != len(names):
        raise ValueError("result must declare 1..256 unique keypoint_names")
    edges = result.get("skeleton_edges")
    if not isinstance(edges, list) or len(edges) > 768 or any(not isinstance(edge, list) or len(edge) != 2 or
                                            any(type(index) is not int or not 0 <= index < len(names) for index in edge)
                                            or edge[0] == edge[1] for edge in edges) or len({tuple(sorted(edge)) for edge in edges}) != len(edges):
        raise ValueError("result skeleton_edges must use declared joint indices")
    if result.get("keypoint_profile") == "coco17" and names != list(COCO_NAMES):
        raise ValueError("coco17 profile requires canonical COCO17 joint names and order")
    if result.get("keypoint_profile") == WHOLEBODY133_PROFILE and (
            names != list(WHOLEBODY133_NAMES) or edges != [list(edge) for edge in WHOLEBODY133_EDGES]):
        raise ValueError("coco-wholebody133 profile requires canonical 133 joints and edges")
    if (not isinstance(result.get("keypoint_profile"), str) or not result["keypoint_profile"].strip()
            or len(result["keypoint_profile"]) > 96):
        raise ValueError("result must declare a keypoint_profile")
    return names, edges


def _bbox(value: Any) -> None:
    if isinstance(value, dict) and set(value) == {"x", "y", "width", "height"}:
        coordinates = [value[key] for key in ("x", "y", "width", "height")]
    elif isinstance(value, list) and len(value) == 4:
        # The workbench's archived importer uses normalized xywh arrays;
        # direct standalone results use the named-object form.
        coordinates = value
    else:
        raise ValueError("tracked result bbox must be normalized xywh")
    x, y, w, h = (_finite(item, "bbox coordinate", minimum=0, maximum=1) for item in coordinates)
    if w < .01 or h < .01 or x + w > 1.000001 or y + h > 1.000001:
        raise ValueError("tracked bbox must have positive area inside its source image")


def verify_result(manifest: dict, result: dict, *, result_path: Path | None = None) -> dict:
    for key in ("schema_version", "project_id", "session_id", "source_snapshot_id", "job_id", "track_id"):
        if result.get(key) != manifest.get(key):
            raise ValueError("result does not match exported source " + key)
    if type(result.get("schema_version")) is not int:
        raise ValueError("result schema_version must be an integer")
    if manifest["schema_version"] == 1:
        if result.get("view_id") != manifest.get("view_id"):
            raise ValueError("result single-view identity differs from export")
    elif result.get("view_ids") != manifest.get("view_ids"):
        raise ValueError("result view_ids/order differ from export")
    evidence_kind = result.get("evidence_kind")
    if evidence_kind not in ("observed_2d", "projected_3d"):
        raise ValueError("result must declare observed_2d or projected_3d evidence_kind")
    names, _ = _topology(result)
    frames = result.get("frames")
    sources = manifest["frames"]
    if not isinstance(frames, list) or len(frames) != len(sources):
        raise ValueError("result must contain exactly one prediction per exported source frame")
    visible_total = 0
    lost_total = 0
    for position, (source, frame) in enumerate(zip(sources, frames)):
        if not isinstance(frame, dict):
            raise ValueError("result frame is not an object at position " + str(position))
        for key in ("ref_id", "frame_index", "width", "height", "image_sha256", "image_orientation"):
            if frame.get(key) != source.get(key):
                raise ValueError(f"result frame {position} differs from source {key}")
        if any(type(frame.get(key)) is not int for key in ("frame_index", "width", "height")):
            raise ValueError(f"result frame {position} requires integer frame index and dimensions")
        if frame.get("view_id") != _source_view(manifest, source):
            raise ValueError(f"result frame {position} differs from source view_id")
        if type(frame.get("time_seconds")) not in (int, float) or frame.get("time_seconds") != source.get("time_seconds"):
            raise ValueError(f"result frame {position} differs from source time_seconds")
        points = frame.get("keypoints")
        if not isinstance(points, list) or len(points) != len(names):
            raise ValueError(f"result frame {position} has wrong joint count")
        for index, point in enumerate(points):
            if not isinstance(point, dict) or point.get("name") != names[index]:
                raise ValueError(f"result frame {position} has wrong joint name/order")
            if "id" in point and point["id"] != index:
                raise ValueError(f"result frame {position} has wrong joint ID")
            for key in ("x", "y", "score"):
                _finite(point.get(key), f"frame {position} joint {index} {key}", minimum=0, maximum=1)
            if type(point.get("in_frame")) is not bool:
                raise ValueError(f"frame {position} joint {index} needs in_frame boolean")
            visible_total += bool(point["in_frame"] and point["score"] > 0)
        if frame.get("tracking_status") not in ("tracked", "lost"):
            raise ValueError(f"result frame {position} requires tracked or lost tracking_status")
        if frame.get("bbox") is None:
            lost_total += 1
            if frame["tracking_status"] != "lost":
                raise ValueError("null bbox requires lost tracking_status")
            if any(point["score"] or (point["in_frame"] and not (
                    point.get("manual_source") == "manual_2d"
                    and point.get("manual_visibility") == "visible"
                    and point.get("manual_position") is True)) for point in points):
                raise ValueError("null bbox allows only explicitly positioned manual visible joints with zero model scores")
        else:
            _bbox(frame["bbox"])
    report = {"job_id": result["job_id"], "source_snapshot_id": result["source_snapshot_id"],
              "evidence_kind": evidence_kind, "keypoint_profile": result["keypoint_profile"],
              "joint_count": len(names), "frames": len(frames), "lost_frames": lost_total,
              "scored_in_frame_joints": visible_total}
    if result_path is not None:
        report.update(result_path=str(result_path.resolve()), result_sha256=_digest(result_path))
    return report


def adapt_observations(manifest: dict, observations: dict) -> dict:
    """Convert explicitly sourced pixel observations; never project 3D here."""
    if observations.get("evidence_kind") != "observed_2d":
        raise ValueError("adapter accepts only independently observed_2d coordinates")
    provenance = observations.get("provenance")
    if not isinstance(provenance, dict) or not isinstance(provenance.get("method"), str) or not provenance["method"] or not isinstance(provenance.get("source_artifact"), str) or not provenance["source_artifact"]:
        raise ValueError("observations need method and source_artifact provenance")
    for key in ("project_id", "session_id", "source_snapshot_id"):
        if observations.get(key) != manifest[key]:
            raise ValueError("observations differ from source " + key)
    profile = observations.get("keypoint_profile")
    names = observations.get("keypoint_names")
    edges = observations.get("skeleton_edges")
    _topology({"keypoint_profile": profile, "keypoint_names": names, "skeleton_edges": edges})
    inputs = observations.get("frames")
    if not isinstance(inputs, list) or len(inputs) != len(manifest["frames"]):
        raise ValueError("observations need one frame per exported source")
    output_frames = []
    for position, (source, item) in enumerate(zip(manifest["frames"], inputs)):
        if not isinstance(item, dict):
            raise ValueError("observation frame is not an object")
        for key in ("ref_id", "frame_index", "width", "height", "time_seconds", "image_sha256", "image_orientation"):
            if item.get(key) != source.get(key):
                raise ValueError(f"observation frame {position} differs from source {key}")
        if any(type(item.get(key)) is not int for key in ("frame_index", "width", "height")):
            raise ValueError(f"observation frame {position} needs integer frame index and dimensions")
        if type(item.get("time_seconds")) not in (int, float):
            raise ValueError(f"observation frame {position} needs numeric time_seconds")
        if item.get("view_id") != _source_view(manifest, source):
            raise ValueError(f"observation frame {position} differs from source view_id")
        points = item.get("keypoints")
        if not isinstance(points, list) or len(points) != len(names):
            raise ValueError("observation frame needs all declared keypoints")
        normalized = []
        width, height = source["width"], source["height"]
        for index, point in enumerate(points):
            if not isinstance(point, dict) or point.get("name") != names[index] or not isinstance(point.get("xy_px"), list) or len(point["xy_px"]) != 2:
                raise ValueError("observation keypoint order or pixel coordinate is invalid")
            x = _finite(point["xy_px"][0], "observed pixel x")
            y = _finite(point["xy_px"][1], "observed pixel y")
            score = _finite(point.get("score"), "observation score", minimum=0, maximum=1)
            in_frame = 0 <= x < width and 0 <= y < height
            if "in_frame" in point and point["in_frame"] != in_frame:
                raise ValueError("observation in_frame disagrees with its actual pixel coordinate")
            normalized.append({"id": index, "name": names[index], "x": min(1,max(0,x/width)),
                               "y": min(1,max(0,y/height)), "score": score, "in_frame": in_frame,
                               "pixel_x": x, "pixel_y": y})
        raw_box = item.get("bbox_xywh")
        if raw_box is None:
            if any(point["score"] or point["in_frame"] for point in normalized):
                raise ValueError("observation without person bbox must mark all joints absent")
            bbox = None
        else:
            if not isinstance(raw_box, list) or len(raw_box) != 4:
                raise ValueError("observation bbox_xywh must contain four pixel numbers")
            bx, by, bw, bh = (_finite(value, "observed bbox") for value in raw_box)
            bbox = {"x": bx/width, "y": by/height, "width": bw/width, "height": bh/height}
            _bbox(bbox)
        output_frames.append({"ref_id": source["ref_id"], "view_id": _source_view(manifest, source),
                              "frame_index": source["frame_index"], "time_seconds": source["time_seconds"],
                              "width": width, "height": height,
                              "image_sha256": source["image_sha256"],
                              "image_orientation": source["image_orientation"], "bbox": bbox,
                              "tracking_status": "tracked" if bbox is not None else "lost",
                              "keypoints": normalized})
    return {"schema_version": manifest["schema_version"],
            **{key: manifest[key] for key in ("project_id", "session_id", "source_snapshot_id", "job_id", "track_id")},
            **({"view_id": manifest["view_id"]} if manifest["schema_version"] == 1 else {"view_ids": manifest["view_ids"]}),
            "evidence_kind": "observed_2d", "keypoint_profile": profile,
            "keypoint_names": names, "skeleton_edges": edges,
            **({"keypoint_groups": WHOLEBODY133_GROUPS} if profile == WHOLEBODY133_PROFILE else {}),
            "model": {"name": "external observed 2D source", "source": provenance["source_artifact"]},
            "provenance": provenance, "tracking": {"method": provenance["method"], "identity_guaranteed": False},
            "frames": output_frames}


def apply_corrections(manifest: dict, result: dict, corrections: dict,
                      *, corrections_path: Path | None = None) -> dict:
    """Merge source-bound human joint edits without changing model confidence.

    The original result stays intact. Every changed joint retains its model
    prediction for audit and gets an explicit manual origin and visibility.
    """
    verify_result(manifest, result)
    if result["keypoint_profile"] != WHOLEBODY133_PROFILE:
        raise ValueError("manual correction merge requires COCO-WholeBody133 parent evidence")
    if not isinstance(corrections, dict) or type(corrections.get("schema_version")) is not int or corrections["schema_version"] != 1 or corrections.get("kind") != "scene_feedback_pose_corrections":
        raise ValueError("corrections need scene_feedback_pose_corrections schema_version 1")
    for key in ("project_id", "session_id", "source_snapshot_id"):
        if corrections.get(key) != manifest[key]:
            raise ValueError("corrections differ from source " + key)
    if corrections.get("parent_job_id") != result["job_id"]:
        raise ValueError("corrections parent_job_id differs from result job_id")
    for key in ("keypoint_profile", "keypoint_names", "skeleton_edges"):
        if corrections.get(key) != result[key]:
            raise ValueError("corrections differ from parent " + key)
    if corrections.get("parent_evidence_kind") != result["evidence_kind"]:
        raise ValueError("corrections differ from parent evidence_kind")
    if corrections.get("coordinate_frame") != "reference_image_normalized":
        raise ValueError("corrections must use reference_image_normalized coordinates")
    items = corrections.get("frames")
    if not isinstance(items, list) or not items or len(items) > len(manifest["frames"]):
        raise ValueError("corrections need one or more edited source frames")
    merged = copy.deepcopy(result)
    by_ref = {source["ref_id"]: (source, original, amended)
              for source, original, amended in zip(manifest["frames"], result["frames"], merged["frames"])}
    if len(by_ref) != len(manifest["frames"]):
        raise ValueError("source export has duplicate ref_id values")
    seen_refs: set[str] = set()
    edit_count = 0
    names = result["keypoint_names"]
    name_to_index = {name: index for index, name in enumerate(names)}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("correction frame must be an object")
        ref_id = item.get("ref_id")
        if not isinstance(ref_id, str) or ref_id not in by_ref or ref_id in seen_refs:
            raise ValueError("correction ref_id is unknown or repeated")
        seen_refs.add(ref_id)
        source, original, amended = by_ref[ref_id]
        for key in ("view_id", "frame_index", "time_seconds", "width", "height", "image_sha256", "image_orientation"):
            expected = original[key]
            if item.get(key) != expected or (key in ("frame_index", "width", "height") and type(item.get(key)) is not int):
                raise ValueError(f"correction {ref_id} differs from source {key}")
        if type(item.get("time_seconds")) not in (int, float):
            raise ValueError("correction time_seconds must be numeric")
        parent_points = item.get("original_keypoints")
        if not isinstance(parent_points, list) or len(parent_points) != len(names):
            raise ValueError("correction must echo all original parent keypoints")
        for index, (echo, point) in enumerate(zip(parent_points, original["keypoints"])):
            if not isinstance(echo, dict) or any(echo.get(key) != point.get(key) for key in ("name", "x", "y", "score", "in_frame")):
                raise ValueError(f"correction parent joint {index} differs from actual result")
            for key in ("raw_score", "manual_source", "manual_visibility", "manual_position"):
                if (key in echo or key in point) and echo.get(key) != point.get(key):
                    raise ValueError(f"correction parent joint {index} differs from actual result {key}")
        edits = item.get("edits")
        if not isinstance(edits, list) or not 1 <= len(edits) <= len(names):
            raise ValueError("correction frame needs one or more named edits")
        seen_names: set[str] = set()
        for edit in edits:
            if not isinstance(edit, dict) or edit.get("name") not in name_to_index or edit["name"] in seen_names:
                raise ValueError("correction joint name is unknown or repeated")
            name = edit["name"]
            seen_names.add(name)
            visibility = edit.get("visibility")
            if visibility not in ("visible", "occluded", "missing"):
                raise ValueError("correction visibility must be visible, occluded or missing")
            if visibility == "missing":
                if "x" in edit or "y" in edit:
                    raise ValueError("missing joint must not have coordinates")
                parent_point = original["keypoints"][name_to_index[name]]
                x, y = parent_point["x"], parent_point["y"]
                positioned = False
            elif visibility == "occluded" and "x" not in edit and "y" not in edit:
                # The absence of a human location is distinct from a measured
                # image point; keep the original estimate only for display.
                point = original["keypoints"][name_to_index[name]]
                x, y = point["x"], point["y"]
                positioned = False
            else:
                x = _finite(edit.get("x"), "manual x", minimum=0, maximum=1)
                y = _finite(edit.get("y"), "manual y", minimum=0, maximum=1)
                positioned = True
                if visibility == "visible" and (x >= 1 or y >= 1):
                    raise ValueError("visible correction must lie inside its source image")
            point = amended["keypoints"][name_to_index[name]]
            point["model_prediction"] = {key: original["keypoints"][name_to_index[name]].get(key)
                                         for key in ("x", "y", "score", "in_frame", "visible", "raw_score")
                                         if key in original["keypoints"][name_to_index[name]]}
            point.update({"x": x, "y": y, "in_frame": visibility == "visible",
                          "visible": visibility == "visible", "origin": "manual",
                          "visibility": visibility, "manual_source": "manual_2d",
                          "manual_visibility": visibility, "manual_position": positioned,
                          "pixel_x": x * original["width"], "pixel_y": y * original["height"]})
            edit_count += 1
    provenance = merged.get("provenance")
    if not isinstance(provenance, dict):
        provenance = {}
        merged["provenance"] = provenance
    prior_manual = copy.deepcopy(provenance.get("manual"))
    provenance["manual"] = {"kind": "human_annotation", "source": "scene_feedback_pose_corrections",
                            "edited_frames": len(seen_refs), "edited_joints": edit_count,
                            "confidence_policy": "model score retained; manual_source, manual_visibility and manual_position carry the human evidence class",
                            **({"previous_amendment": prior_manual} if prior_manual is not None else {}),
                            **({"source_artifact": str(corrections_path.resolve()),
                                "source_sha256": _digest(corrections_path)} if corrections_path else {})}
    verify_result(manifest, merged)
    return merged


def _joint_map(path: Path, result: dict) -> dict:
    mapping = _json(path)
    if mapping.get("source_profile") != result["keypoint_profile"] or mapping.get("target_profile") != "mhr127-hot3d21":
        raise ValueError("joint map must explicitly connect result profile to mhr127-hot3d21")
    pairs = mapping.get("pairs")
    if not isinstance(pairs, dict) or not pairs or any(name not in result["keypoint_names"] or type(index) is not int or
                                                       not 0 <= index < 127 for name, index in pairs.items()):
        raise ValueError("joint map pairs must use declared source names and MHR127 indices")
    if len(set(pairs.values())) != len(pairs):
        raise ValueError("joint map cannot assign two source joints to the same MHR127 index")
    if not isinstance(mapping.get("validation_note"), str) or not mapping["validation_note"].strip():
        raise ValueError("joint map requires a validation_note explaining its actual source")
    return mapping


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("source-export", "track", "adapt-observations", "apply-corrections", "check-result", "import-payload", "handoff"):
        command = sub.add_parser(name)
        command.add_argument("--manifest", type=Path, required=True, help="manifest_json_path from workspace_export_pose_sources")
        if name in ("apply-corrections", "check-result", "import-payload", "handoff"):
            command.add_argument("--result", type=Path, required=True)
        if name in ("track", "adapt-observations", "apply-corrections", "handoff"):
            command.add_argument("--output", type=Path, required=True)
        if name == "track":
            command.add_argument("--model-path", type=Path, required=True)
            command.add_argument("--detector-path", type=Path)
            command.add_argument("--profile", choices=(WHOLEBODY133_PROFILE, COCO17_PROFILE),
                                 default=WHOLEBODY133_PROFILE)
        if name == "adapt-observations":
            command.add_argument("--observations", type=Path, required=True)
        if name == "apply-corrections":
            command.add_argument("--corrections", type=Path, required=True)
        if name == "handoff":
            command.add_argument("--joint-map", type=Path)
    args = parser.parse_args()
    manifest_path = args.manifest.expanduser().resolve(strict=True)
    manifest = validate_manifest(manifest_path)
    if args.action == "source-export":
        print(json.dumps(source_summary(manifest_path, manifest), ensure_ascii=False))
        return
    if args.action == "track":
        output = _project_result_path(manifest, args.output)
        if output.exists():
            parser.error("output already exists; choose a new result path")
        if manifest.get("options", {}).get("auto_detect") and args.detector_path is None:
            parser.error("automatic tracking requires --detector-path")
        result = run_inference(manifest_path, output, model_path=args.model_path,
                               detector_path=args.detector_path,
                               profile=args.profile,
                               progress=lambda value: print(json.dumps(value, ensure_ascii=False), flush=True))
        report = verify_result(manifest, result, result_path=output)
        print(json.dumps({"type": "complete", **report}, ensure_ascii=False), flush=True)
        return
    if args.action == "adapt-observations":
        output = _project_result_path(manifest, args.output)
        if output.exists():
            parser.error("output already exists; choose a new result path")
        adapted = adapt_observations(manifest, _json(args.observations))
        verify_result(manifest, adapted)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(adapted, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"type": "adapted", **verify_result(manifest, adapted, result_path=output)}, ensure_ascii=False))
        return
    if args.action == "apply-corrections":
        output = _project_result_path(manifest, args.output)
        if output.exists():
            parser.error("output already exists; choose a new merged result path")
        parent = _json(args.result.expanduser().resolve(strict=True))
        corrections_path = args.corrections.expanduser().resolve(strict=True)
        merged = apply_corrections(manifest, parent, _json(corrections_path),
                                   corrections_path=corrections_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(merged, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"type": "corrected", **verify_result(manifest, merged, result_path=output)}, ensure_ascii=False))
        return
    result_path = args.result.expanduser().resolve(strict=True)
    result = _json(result_path)
    report = verify_result(manifest, result, result_path=result_path)
    if args.action == "check-result":
        print(json.dumps(report, ensure_ascii=False))
    elif args.action == "import-payload":
        print(json.dumps({"job_id": manifest["job_id"],
                          "result_path": str(_project_result_path(manifest, result_path))}, ensure_ascii=False))
    elif args.action == "handoff":
        destination = args.output.expanduser().resolve()
        mapping = _joint_map(args.joint_map, result) if args.joint_map else None
        if destination.exists():
            parser.error("handoff directory already exists; choose a new path")
        destination.mkdir(parents=True)
        shutil.copy2(manifest_path, destination / "source_manifest.json")
        shutil.copy2(result_path, destination / "pose_result.json")
        if mapping is not None:
            shutil.copy2(args.joint_map, destination / "joint_map.json")
        handoff = {**report, "manifest_sha256": _digest(manifest_path),
                   "source_manifest": "source_manifest.json", "pose_result": "pose_result.json",
                   "source_images": "referenced at their actual absolute image_path; not copied",
                   "source_cameras": "per-frame camera fields in source_manifest.json when supplied",
                   "joint_map": "joint_map.json" if mapping else None,
                   "capsule_use": (
                       "Compare observed 2D joints with projections of the existing fixed-geometry motion using the actual cameras and an explicitly validated joint map. Do not feed these joints into the mask-only leg refiner automatically."
                       if result["evidence_kind"] == "observed_2d" else
                       "These joints are projections of a current 3D prediction, not independent image observations. Inspect them against the actual reference images; do not fit 3D motion to this same projection as if it were measured evidence."),
                   "limits": "No cross-view identity proof, 3D triangulation, metric scale, or automatic MHR127/HOT3D21 motion initialization."}
        (destination / "handoff.json").write_text(json.dumps(handoff, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"handoff_path": str(destination), "report": handoff}, ensure_ascii=False))


if __name__ == "__main__":
    main()

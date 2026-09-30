#!/usr/bin/env python3
"""Source-bound, calibrated WholeBody133 reconstruction with fixed capsule bones.

This is a separate profile from MHR127/HOT3D21. It never initializes unseen
anatomy from an average skeleton, and it never treats independent view tracks
as proof of person identity. NumPy and Pillow are the only runtime dependencies.
"""
from __future__ import annotations

import argparse
import copy
import itertools
import json
import math
from pathlib import Path
import subprocess
import sys

import numpy as np

from common import fresh, sha, write_json
from pose_evidence import verify_result
from wholebody_profile import KEYPOINT_NAMES, HAND_EDGES, WHOLEBODY133_PROFILE

NAMES = tuple(KEYPOINT_NAMES) + ("virtual_pelvis_center", "virtual_shoulder_center")
BODY_EDGES = ((133, 11), (133, 12), (133, 134), (134, 5), (134, 6),
              (5, 7), (7, 9), (6, 8), (8, 10), (11, 13), (13, 15), (12, 14), (14, 16))
PARTS = {"body": (133, BODY_EDGES), "head": (134, ((134, 0),)),
         "left_foot": (15, ((15, 17), (15, 18), (15, 19))),
         "right_foot": (16, ((16, 20), (16, 21), (16, 22))),
         "left_hand": (91, tuple((91 + a, 91 + b) for a, b in HAND_EDGES)),
         "right_hand": (112, tuple((112 + a, 112 + b) for a, b in HAND_EDGES))}


def finite(value, name, minimum=None, maximum=None):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(name + " must be a finite number")
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise ValueError(name + " is out of range")
    return float(value)


def rigid(value, name):
    matrix = np.asarray(value, dtype=float)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all() or not np.allclose(matrix[3], [0, 0, 0, 1], atol=1e-8):
        raise ValueError(name + " must be a finite homogeneous 4x4 transform")
    rotation = matrix[:3, :3]
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-5) or abs(np.linalg.det(rotation) - 1) > 1e-5:
        raise ValueError(name + " must contain a proper rigid rotation, without scale or reflection")
    return matrix


def calibrated_camera(source, convention):
    camera = source.get("camera")
    if not isinstance(camera, dict):
        raise ValueError("Exported frame " + source["ref_id"] + " has no calibrated camera")
    transform = rigid(camera.get("camera_to_world"), "camera_to_world")
    intr = camera.get("intrinsics", {})
    if (intr.get("width"), intr.get("height")) != (source["width"], source["height"]):
        raise ValueError("Camera intrinsics dimensions differ from the exact observed source pixels")
    fx, fy = (finite(intr.get(key), "camera " + key, .00001) for key in ("fx", "fy"))
    cx, cy = (finite(intr.get(key), "camera " + key) for key in ("cx", "cy"))
    if not 0 <= cx <= source["width"] or not 0 <= cy <= source["height"]:
        raise ValueError("Camera principal point must be in its source image")
    distortion = camera.get("distortion", [0] * 5)
    if not isinstance(distortion, list) or len(distortion) != 5 or any(finite(x, "distortion") != 0 for x in distortion):
        raise ValueError("Nonzero distortion requires explicitly undistorted images, observations and intrinsics")
    extrinsic = np.linalg.inv(transform)[:3]
    if convention == "threejs":
        extrinsic = np.diag([1, -1, -1]) @ extrinsic
    elif convention != "opencv":
        raise ValueError("camera_convention must explicitly be threejs or opencv; axes are never guessed")
    intrinsic = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]])
    return {"P": intrinsic @ extrinsic, "extrinsic": extrinsic, "K": intrinsic,
            "center": transform[:3, 3], "ref_id": source["ref_id"], "view_id": source["view_id"]}


def project(point, camera):
    q = camera["P"] @ np.r_[point, 1]
    depth = (camera["extrinsic"] @ np.r_[point, 1])[2]
    return (q[:2] / q[2] if abs(q[2]) > 1e-12 else np.array([np.inf, np.inf])), depth


def triangulate(observations, *, reprojection_px=4, min_ray_angle_deg=.5):
    """Pair consensus followed by weighted DLT; reject poor baseline and depth."""
    if len(observations) < 2:
        return None, {"reason": "fewer_than_two_confident_views"}

    def solve(items):
        rows = []
        for camera, xy, score in items:
            matrix = camera["P"]
            rows += [(xy[0] * matrix[2] - matrix[0]) * math.sqrt(score),
                     (xy[1] * matrix[2] - matrix[1]) * math.sqrt(score)]
        _, singular, vectors = np.linalg.svd(rows)
        homogeneous = vectors[-1]
        if abs(homogeneous[3]) < 1e-12 or singular[-2] < 1e-10:
            return None
        return homogeneous[:3] / homogeneous[3]

    candidates = []
    for pair in itertools.combinations(observations, 2):
        if np.linalg.norm(pair[0][0]["center"] - pair[1][0]["center"]) < 1e-8:
            continue
        point = solve(pair)
        if point is None:
            continue
        rays = [point - camera["center"] for camera, _, _ in pair]
        lengths = [np.linalg.norm(ray) for ray in rays]
        if min(lengths) < 1e-10:
            continue
        angle = math.degrees(math.acos(np.clip(abs(np.dot(*rays) / np.prod(lengths)), 0, 1)))
        if angle < min_ray_angle_deg:
            continue
        errors = [np.linalg.norm(project(point, camera)[0] - xy) if project(point, camera)[1] > 1e-8 else np.inf
                  for camera, xy, _ in observations]
        inliers = [item for item, error in zip(observations, errors) if error <= reprojection_px]
        if len(inliers) >= 2:
            candidates.append((len(inliers), -float(np.mean([x for x in errors if x <= reprojection_px])), inliers))
    if not candidates:
        return None, {"reason": "poor_parallax_depth_or_reprojection"}
    inliers = max(candidates, key=lambda item: item[:2])[2]
    point = solve(inliers)
    if point is None:
        return None, {"reason": "degenerate_dlt"}
    errors = [np.linalg.norm(project(point, camera)[0] - xy) for camera, xy, _ in inliers]
    if max(errors) > reprojection_px or any(project(point, camera)[1] <= 1e-8 for camera, _, _ in inliers):
        return None, {"reason": "refined_reprojection_or_depth_failed"}
    return point, {"reason": "accepted", "views": [camera["view_id"] for camera, _, _ in inliers],
                   "reprojection_px": [float(error) for error in errors],
                   "mean_observation_weight": float(np.mean([score for _, _, score in inliers]))}


def observation_weight(point, threshold, manual_weight):
    # Occluded manual guesses stay in the evidence packet but cannot establish depth.
    visibility = point.get("manual_visibility", point.get("visibility", "visible"))
    if visibility not in ("visible", "occluded", "missing"):
        raise ValueError("Unknown manual observation visibility")
    if visibility != "visible" or not point["in_frame"]:
        return 0.
    if (point.get("origin") == "manual" or point.get("manual_source") == "manual_2d") and point.get("manual_position") is True:
        return manual_weight  # explicit fitting weight, never a fabricated detector score
    return point["score"] if point["score"] >= threshold else 0.


def validate_inputs(config, manifest, result, *, check_images=True):
    if config.get("schema_version") != 1 or type(config.get("schema_version")) is not int:
        raise ValueError("Expected reconstruction config schema_version=1")
    verify_result(manifest, result)
    if result.get("evidence_kind") != "observed_2d":
        raise ValueError("Reconstruction needs independent observed_2d evidence, not projected_3d")
    if result.get("keypoint_profile") != WHOLEBODY133_PROFILE or result.get("keypoint_names") != list(KEYPOINT_NAMES):
        raise ValueError("Expected canonical coco-wholebody133; COCO17 cannot initialize missing hands")
    views = manifest.get("view_ids", [manifest.get("view_id")])
    association = config.get("association", {})
    if (association.get("identity_verified") is not True or not isinstance(association.get("actor_id"), str)
            or not association["actor_id"] or not isinstance(association.get("verification"), str) or not association["verification"].strip()):
        raise ValueError("Explicit verified actor_id and cross-view association provenance are required")
    tracks = association.get("view_track_ids")
    if not isinstance(tracks, dict) or set(tracks) != set(views) or any(value != result["track_id"] for value in tracks.values()):
        raise ValueError("Every exported view needs an explicitly confirmed track_id for this same actor")
    if config.get("camera_convention") not in ("threejs", "opencv"):
        raise ValueError("Declare the actual camera_convention; never infer axes from an image")
    cameras = []
    for source, frame in zip(manifest["frames"], result["frames"]):
        source = {**source, "view_id": source.get("view_id", manifest.get("view_id"))}
        if frame.get("actor_id", association["actor_id"]) != association["actor_id"]:
            raise ValueError("Observation actor differs from explicitly verified association")
        if check_images:
            from PIL import Image, ImageOps
            path = Path(source["image_path"])
            if not path.is_file() or sha(path) != source["image_sha256"]:
                raise ValueError("Exported source image is missing or its actual SHA256 changed")
            with Image.open(path) as opened:
                if ImageOps.exif_transpose(opened).size != (source["width"], source["height"]):
                    raise ValueError("Observed source dimensions disagree with oriented source image")
        cameras.append(calibrated_camera(source, config["camera_convention"]))
    return views, cameras


def synchronized_groups(manifest, views, tolerance):
    by_view = {view: [] for view in views}
    for index, source in enumerate(manifest["frames"]):
        by_view[source.get("view_id", manifest.get("view_id"))].append(index)
    for indices in by_view.values():
        times = [manifest["frames"][index]["time_seconds"] for index in indices]
        if any(b <= a for a, b in zip(times, times[1:])):
            raise ValueError("Each view must have strictly increasing source times")
    used = set()
    groups = []
    # Deterministic union chronology; never interpolate a person's pixels between images.
    for index in sorted(range(len(manifest["frames"])), key=lambda i: (manifest["frames"][i]["time_seconds"], i)):
        if index in used:
            continue
        time = manifest["frames"][index]["time_seconds"]
        group = [index]
        view = manifest["frames"][index].get("view_id", manifest.get("view_id"))
        for other in views:
            if other == view:
                continue
            choices = [i for i in by_view[other] if i not in used and abs(manifest["frames"][i]["time_seconds"] - time) <= tolerance]
            if choices:
                group.append(min(choices, key=lambda i: abs(manifest["frames"][i]["time_seconds"] - time)))
        used.update(group)
        groups.append((time, group))
    return groups


def hand_input(document, manifest, actor_id, allowed_times=None):
    if document is None:
        return {}
    for key in ("project_id", "session_id", "source_snapshot_id"):
        if document.get(key) != manifest.get(key):
            raise ValueError("Existing 3D hand input differs from exported " + key)
    if (type(document.get("schema_version")) is not int or document.get("schema_version") != 1 or document.get("actor_id") != actor_id
            or document.get("coordinate_frame") != "camera_world" or document.get("joint_order") != "wholebody_hand21"):
        raise ValueError("3D hand input needs explicit actor, camera_world frame and canonical wrist+five 4-joint fingers")
    provenance = document.get("provenance", {})
    if not provenance.get("method") or not provenance.get("source_artifact"):
        raise ValueError("Existing 3D hands need their actual source provenance")
    output = {}
    for frame in document.get("frames", []):
        time = finite(frame.get("time_seconds"), "3D hand time", 0)
        if allowed_times is not None and time not in allowed_times:
            raise ValueError("Existing 3D hand time does not exactly match a synchronized source group")
        if time in output:
            raise ValueError("Duplicate existing hand timestamp")
        parts = {}
        for side in ("left", "right"):
            if side not in frame:
                continue
            value = frame[side]
            joints = np.asarray(value.get("joints"), dtype=float)
            scores = np.asarray(value.get("scores"), dtype=float)
            if joints.shape != (21, 3) or scores.shape != (21,) or not np.isfinite(joints).all() or not np.isfinite(scores).all() or (scores < 0).any() or (scores > 1).any():
                raise ValueError("Existing hands need exactly 21 finite canonical 3D points and confidence scores")
            parts[side] = (joints, scores)
        output[time] = parts
    return output


def reconstruct(config, manifest, result, *, hands_3d=None, limit=None, check_images=True):
    views, cameras = validate_inputs(config, manifest, result, check_images=check_images)
    threshold = finite(config.get("confidence_threshold", .3), "confidence_threshold", 0, 1)
    gain = finite(config.get("update_gain", .7), "update_gain", .0001, 1)
    manual_weight = finite(config.get("manual_visible_weight", .8), "manual_visible_weight", .0001, 1)
    error_limit = finite(config.get("max_reprojection_px", 4), "max_reprojection_px", .0001, 100)
    angle = finite(config.get("min_ray_angle_deg", .5), "min_ray_angle_deg", .0001, 45)
    tolerance = finite(config.get("sync_tolerance_sec", 1e-6), "sync_tolerance_sec", 0, .02)
    wrist_fraction = finite(config.get("wrist_merge_fraction", .35), "wrist_merge_fraction", .001, 1)
    unit = config.get("source_unit_in_meters")
    if unit is not None:
        finite(unit, "source_unit_in_meters", .000000001)
        if not isinstance(config.get("metric_scale_provenance"), str) or not config["metric_scale_provenance"].strip():
            raise ValueError("Metric units need independent scale provenance")
    groups = synchronized_groups(manifest, views, tolerance)
    existing_hands = hand_input(hands_3d, manifest, config["association"]["actor_id"], {time for time, _ in groups})
    if limit is not None:
        if type(limit) is not int or limit < 1:
            raise ValueError("--frames must be a positive prefix length")
        groups = groups[:limit]
    state = np.full((135, 3), np.nan)
    template = {}
    initialized = {}
    positions, availability, raw_points, raw_valid, times, reports = [], [], [], [], [], []
    for ordinal, (time, indices) in enumerate(groups):
        raw = np.full((135, 3), np.nan)
        valid = np.zeros(135, dtype=bool)
        qualities = np.zeros(135)
        joint_reports = []
        for joint in range(133):
            observations = []
            origins = []
            manual_observations = []
            for index in indices:
                point = result["frames"][index]["keypoints"][joint]
                weight = observation_weight(point, threshold, manual_weight)
                if weight:
                    source = manifest["frames"][index]
                    observations.append((cameras[index], np.array([point["x"] * source["width"], point["y"] * source["height"]]), weight))
                    origins.append("manual_2d" if point.get("manual_source") == "manual_2d" and point.get("manual_position") is True else point.get("origin", "inference"))
                if point.get("manual_source") == "manual_2d" or point.get("origin") == "manual":
                    manual_observations.append({"view_id": cameras[index]["view_id"], "ref_id": cameras[index]["ref_id"],
                        "visibility": point.get("manual_visibility", point.get("visibility", "visible")),
                        "manual_position": point.get("manual_position", False), "model_score": point["score"], "fit_weight": weight})
            point, report = triangulate(observations, reprojection_px=error_limit, min_ray_angle_deg=angle)
            report.update(joint=KEYPOINT_NAMES[joint], origins=origins)
            if manual_observations:
                report["manual_observations"] = manual_observations
            if point is not None:
                raw[joint], valid[joint], qualities[joint] = point, True, report["mean_observation_weight"]
            joint_reports.append(report)
        for side, base in (("left", 91), ("right", 112)):
            if side in existing_hands.get(time, {}):
                joints, scores = existing_hands[time][side]
                keep = scores >= threshold
                raw[base:base + 21][keep] = joints[keep]
                valid[base:base + 21][keep] = True
                qualities[base:base + 21][keep] = scores[keep]
                for local, accepted in enumerate(keep):
                    if accepted:
                        joint_reports[base + local] = {"joint": KEYPOINT_NAMES[base + local], "reason": "existing_3d",
                            "origins": ["existing_3d"], "provenance": hands_3d["provenance"]}
        wrist_report = []
        for wrist, root, elbow in ((9, 91, 7), (10, 112, 8)):
            if valid[wrist] and valid[root]:
                scale = np.linalg.norm(raw[wrist] - raw[elbow]) if valid[elbow] else max(np.linalg.norm(raw[root + i] - raw[root]) for i in (1, 5, 9, 13, 17) if valid[root + i]) if any(valid[root + i] for i in (1, 5, 9, 13, 17)) else 0
                gap = float(np.linalg.norm(raw[root] - raw[wrist]))
                if scale <= 1e-8 or gap > wrist_fraction * scale:
                    valid[root:root + 21] = False
                    wrist_report.append({"hand_root": root, "gap": gap, "status": "rejected_inconsistent_same_wrist"})
                else:
                    fused = (raw[root] * qualities[root] + raw[wrist] * qualities[wrist]) / (qualities[root] + qualities[wrist])
                    raw[root] = raw[wrist] = fused
                    wrist_report.append({"hand_root": root, "gap": gap, "status": "explicit_same_side_wrist_fusion"})
        for center, pair in ((133, (11, 12)), (134, (5, 6))):
            if valid[list(pair)].all():
                raw[center], valid[center] = np.mean(raw[list(pair)], axis=0), True
        part_updates = {}
        for part, (root, edges) in PARTS.items():
            nodes = sorted({root, *(child for _, child in edges)})
            dependency = part in ("head", "left_foot", "right_foot") and "body" not in initialized
            if part not in initialized:
                if dependency or not valid[nodes].all():
                    part_updates[part] = "uninitialized_insufficient_reliable_3d"
                    continue
                lengths = [float(np.linalg.norm(raw[child] - raw[parent])) for parent, child in edges]
                if min(lengths) < 1e-6:
                    part_updates[part] = "uninitialized_degenerate_bone"
                    continue
                initialized[part] = ordinal
                template[part] = {"root": root, "edges": [list(edge) for edge in edges], "lengths": lengths,
                    "initialized_frame": ordinal, "initialized_time_seconds": time,
                    "radii": [max(length * (.07 if "hand" in part else .12), min(lengths) * .03) for length in lengths]}
                prior_root = state[root].copy()
                state[nodes] = raw[nodes]
                if part in ("head", "left_foot", "right_foot"):
                    state[root] = prior_root  # preserve the already accepted body bone endpoint
            anchored = None
            if part in ("left_hand", "right_hand") and "body" in initialized:
                anchored = state[9 if part == "left_hand" else 10]
            elif part in ("head", "left_foot", "right_foot"):
                anchored = state[root].copy()
            prior = state.copy()
            if anchored is not None:
                state[root] = anchored
            elif valid[root]:
                state[root] = prior[root] + gain * (raw[root] - prior[root])
            updated = held = 0
            for (parent, child), length in zip(edges, template[part]["lengths"]):
                previous_direction = prior[child] - prior[parent]
                previous_direction /= np.linalg.norm(previous_direction)
                if valid[parent] and valid[child]:
                    observed_direction = raw[child] - raw[parent]
                    norm = np.linalg.norm(observed_direction)
                    if norm > 1e-10:
                        direction = previous_direction * (1 - gain) + observed_direction / norm * gain
                        norm = np.linalg.norm(direction)
                        direction = direction / norm if norm > 1e-10 else previous_direction
                        updated += 1
                    else:
                        direction = previous_direction
                        held += 1
                else:
                    direction = previous_direction
                    held += 1
                state[child] = state[parent] + length * direction
            part_updates[part] = {"updated_directions": updated, "held_directions": held,
                "root_anchor": "body_wrist" if part in ("left_hand", "right_hand") and anchored is not None else "observed_or_previous_root"}
        modeled_errors = []
        for index in indices:
            source = manifest["frames"][index]
            for joint, point in enumerate(result["frames"][index]["keypoints"]):
                if np.isfinite(state[joint]).all() and observation_weight(point, threshold, manual_weight):
                    xy, depth = project(state[joint], cameras[index])
                    if depth > 0:
                        modeled_errors.append(float(np.linalg.norm(xy - [point["x"] * source["width"], point["y"] * source["height"]])))
        positions.append(state.copy())
        availability.append(np.isfinite(state).all(axis=1))
        raw_points.append(raw[:133].copy())
        raw_valid.append(valid[:133].copy())
        times.append(time)
        reports.append({"time_seconds": time, "source_ref_ids": [manifest["frames"][i]["ref_id"] for i in indices],
            "joints": joint_reports, "wrist_fusion": wrist_report, "parts": part_updates,
            "model_reprojection_px": {"count": len(modeled_errors), "mean": float(np.mean(modeled_errors)) if modeled_errors else None,
                                       "max": max(modeled_errors) if modeled_errors else None}})
    if not groups:
        raise ValueError("No exported source frames")
    positions = np.asarray(positions)
    available = np.asarray(availability)
    increments = np.zeros_like(positions)
    increment_valid = np.zeros_like(available)
    if len(positions) > 1:
        increment_valid[1:] = available[1:] & available[:-1]
        delta = positions[1:] - positions[:-1]
        increments[1:][increment_valid[1:]] = delta[increment_valid[1:]]
    status = "complete_body_and_hands" if all(part in initialized for part in ("body", "left_hand", "right_hand")) else "partial" if initialized else "unsupported"
    model = {"schema_version": 1, "profile": "wholebody133-fixed-capsules", "source_profile": WHOLEBODY133_PROFILE,
             "node_names": list(NAMES), "virtual_nodes": {"133": "mean(left_hip,right_hip)", "134": "mean(left_shoulder,right_shoulder)"},
             "parts": template, "face_model": "not_built; independent face68 observations remain in evidence",
             "coordinate_frame": "camera_world", "source_unit_in_meters": unit,
             "metric_scale_provenance": config.get("metric_scale_provenance"), "status": status,
             "association": copy.deepcopy(config["association"])}
    report = {"status": status, "frames": len(times), "source_views": views,
              "initialized_parts": initialized, "unsupported_parts": [part for part in PARTS if part not in initialized],
              "scale": "metric calibration explicitly declared" if unit is not None else "camera world units; metric scale not established",
              "occlusion": "previous root and bone direction held without velocity extrapolation",
              "identity": config["association"], "frames_report": reports,
              "source_provenance": result.get("provenance", {}),
              "source_binding": {key: manifest[key] for key in ("project_id", "session_id", "source_snapshot_id", "job_id", "track_id")}}
    motion = {"positions": positions, "available": available, "motion_increment": increments,
              "increment_valid": increment_valid, "time_seconds": np.asarray(times),
              "triangulated_positions": np.asarray(raw_points), "triangulated_available": np.asarray(raw_valid)}
    validate_motion(model, motion)
    return model, motion, report


def validate_motion(model, motion, prefix=None):
    positions, available = motion["positions"], motion["available"]
    if positions.shape != (*available.shape, 3) or available.shape[1] != 135 or len(positions) < 1:
        raise ValueError("Invalid fixed WholeBody motion shapes")
    if not np.isfinite(positions[available]).all() or np.isfinite(positions[~available]).any():
        raise ValueError("Available joints must be finite; unsupported joints must remain absent (NaN)")
    max_length_error = 0.
    for part in model["parts"].values():
        for (parent, child), length in zip(part["edges"], part["lengths"]):
            mask = available[:, parent] & available[:, child]
            error = float(np.max(abs(np.linalg.norm(positions[mask, child] - positions[mask, parent], axis=1) - length)))
            max_length_error = max(max_length_error, error)
    if max_length_error > 1e-7:
        raise ValueError("Capsule bone lengths changed through time")
    mask = motion["increment_valid"][1:]
    recurrence = float(np.max(abs((positions[1:] - positions[:-1] - motion["motion_increment"][1:])[mask]))) if mask.any() else 0.
    if recurrence > 1e-10:
        raise ValueError("Motion does not recur from the previously accepted state")
    if "body" in model["parts"]:
        for wrist, root in ((9, 91), (10, 112)):
            mask = available[:, wrist] & available[:, root]
            if mask.any() and not np.allclose(positions[mask, wrist], positions[mask, root], atol=1e-9):
                raise ValueError("Same-side hand root is detached from body wrist")
    if prefix is not None:
        n = len(prefix["positions"])
        for key in ("positions", "available", "motion_increment", "triangulated_positions", "triangulated_available", "time_seconds"):
            if not np.allclose(motion[key][:n], prefix[key], equal_nan=True, atol=1e-10):
                raise ValueError("Independent prefix replay differs: " + key)
    return {"frames": len(positions), "fixed_bone_max_error": max_length_error, "recurrence_max_error": recurrence,
            "prefix_replay_frames": len(prefix["positions"]) if prefix is not None else None}


def reconstruct_files(config_path, output, limit=None):
    config_path = Path(config_path).resolve()
    config = json.loads(config_path.read_text())
    def load(key):
        path = (config_path.parent / config[key]).resolve()
        return path, json.loads(path.read_text())
    manifest_path, manifest = load("manifest_path")
    result_path, result = load("observations_path")
    hands_path, hands = load("hands_3d_path") if config.get("hands_3d_path") else (None, None)
    model, motion, report = reconstruct(config, manifest, result, hands_3d=hands, limit=limit)
    out = fresh(output)
    np.savez_compressed(out / "wholebody_motion.npz", **motion)
    write_json(out / "wholebody_template.json", model)
    report["validation"] = validate_motion(model, motion)
    report["inputs"] = {str(path): sha(path) for path in (config_path, manifest_path, result_path, hands_path) if path is not None}
    write_json(out / "report.json", report)
    write_json(out / "reconstruction_config.json", config)
    return {"output": str(out), "status": report["status"], "frames": report["frames"],
            "initialized_parts": report["initialized_parts"], "unsupported_parts": report["unsupported_parts"]}


def build_files(run, output, *, blender=None, scene=None, world_to_blender=None, preview_glb=False, preview_frame=None):
    from capsule import blender_binary
    run = Path(run).resolve()
    model = json.loads((run / "wholebody_template.json").read_text())
    with np.load(run / "wholebody_motion.npz", allow_pickle=False) as data:
        motion = {key: data[key] for key in data.files}
    validate_motion(model, motion)
    if not model["parts"]:
        raise ValueError("Unsupported reconstruction has no reliable geometry to build")
    if scene and world_to_blender is None:
        raise ValueError("Scene integration requires explicit world_to_blender calibration; never guess scene alignment")
    transform = rigid(world_to_blender if world_to_blender is not None else np.eye(4), "world_to_blender")
    if preview_frame is None:
        preview_frame = len(motion["positions"]) - 1
    if type(preview_frame) is not int or not 0 <= preview_frame < len(motion["positions"]):
        raise ValueError("GLB preview frame is outside reconstructed sequence")
    out = fresh(output)
    job = {"run": str(run), "output": str(out), "scene": str(Path(scene).resolve()) if scene else None,
           "world_to_blender": transform.tolist(), "preview_glb": preview_glb, "preview_frame": preview_frame}
    write_json(out / "build_job.json", job)
    log = out / "blender.log"
    with log.open("w") as handle:
        process = subprocess.run([blender_binary(blender), "-b", "-t", "2", "--python", str(Path(__file__).with_name("wholebody_blender.py")), "--", str(out / "build_job.json")], stdout=handle, stderr=subprocess.STDOUT)
    if process.returncode or "WHOLEBODY_BUILD_COMPLETE" not in log.read_text():
        raise RuntimeError("WholeBody Blender build failed; inspect " + str(log))
    return json.loads((out / "build_report.json").read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--frames", type=int, help="independent causal prefix length")
    args = parser.parse_args()
    print(json.dumps(reconstruct_files(args.config, args.output, args.frames), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, FileNotFoundError, RuntimeError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)

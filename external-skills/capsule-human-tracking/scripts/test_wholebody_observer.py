#!/usr/bin/env python3
"""Focused offline WholeBody profile, source binding and manual edit checks."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pose_evidence import apply_corrections, verify_result  # noqa: E402
from pose_worker import (normalized_keypoints, pose_topology, tracking_quality,
                         update_bbox, validate_manifest, validate_model_head)  # noqa: E402
from wholebody_profile import (COCO17_PROFILE, HAND_EDGES, WHOLEBODY133_EDGES,
                               WHOLEBODY133_NAMES, WHOLEBODY133_PROFILE,
                               WHOLEBODY133_GROUPS)  # noqa: E402


class WholeBodyObserverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="wholebody-observer-test-")
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        frames = []
        for view in ("left", "right"):
            path = self.project / f"{view}.png"
            Image.new("RGB", (100, 80), "#eaeaea").save(path)
            frames.append({"ref_id": f"source-{view}", "view_id": view,
                           "frame_index": 0, "time_seconds": 0.0,
                           "width": 100, "height": 80,
                           "image_path": str(path),
                           "image_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                           "image_orientation": "exif_oriented_display"})
        self.manifest = {"schema_version": 2, "project_id": "project", "session_id": "session",
                         "source_snapshot_id": "snapshot", "job_id": "job", "track_id": "track",
                         "project_dir": str(self.project), "view_ids": ["left", "right"], "frames": frames}
        self.manifest_path = self.project / "manifest.json"
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")

    def result(self, *, lost: bool = False, projected: bool = False) -> dict:
        frames = []
        for source in self.manifest["frames"]:
            points = [{"id": i, "name": name, "x": .4, "y": .5,
                       "score": 0.0 if lost else .7, "in_frame": not lost,
                       "raw_score": 0.0 if lost else .7}
                      for i, name in enumerate(WHOLEBODY133_NAMES)]
            frames.append({key: source[key] for key in ("ref_id", "view_id", "frame_index", "time_seconds",
                                                         "width", "height", "image_sha256", "image_orientation")}
                          | {"bbox": None if lost else {"x": .2, "y": .2, "width": .5, "height": .6},
                             "tracking_status": "lost" if lost else "tracked", "keypoints": points})
        return ({key: self.manifest[key] for key in ("schema_version", "project_id", "session_id",
                                                       "source_snapshot_id", "job_id", "track_id", "view_ids")}
        | {"evidence_kind": "projected_3d" if projected else "observed_2d",
           "keypoint_profile": WHOLEBODY133_PROFILE,
           "keypoint_names": list(WHOLEBODY133_NAMES),
           "skeleton_edges": [list(edge) for edge in WHOLEBODY133_EDGES],
           "provenance": {"kind": "image_inference"}, "frames": frames})

    def corrections(self, result: dict, *, edit: dict | None = None) -> dict:
        frame = result["frames"][0]
        return {"schema_version": 1, "kind": "scene_feedback_pose_corrections",
                "project_id": "project", "session_id": "session", "source_snapshot_id": "snapshot",
                "parent_job_id": "job", "keypoint_profile": WHOLEBODY133_PROFILE,
                "keypoint_names": list(WHOLEBODY133_NAMES),
                "skeleton_edges": [list(edge) for edge in WHOLEBODY133_EDGES],
                "parent_evidence_kind": result["evidence_kind"],
                "coordinate_frame": "reference_image_normalized",
                "frames": [{key: frame[key] for key in ("ref_id", "view_id", "frame_index", "time_seconds",
                                                          "width", "height", "image_sha256", "image_orientation")}
                           | {"original_keypoints": [{key: p[key] for key in ("name", "x", "y", "score", "in_frame", "raw_score")}
                                                    for p in frame["keypoints"]],
                              "edits": [edit or {"name": "left_thumb4", "x": .61, "y": .73,
                                                 "visibility": "visible"}]}]}

    def test_official_profile_order_and_hand_groups(self) -> None:
        self.assertEqual(len(WHOLEBODY133_NAMES), 133)
        self.assertEqual(len(WHOLEBODY133_EDGES), 65)
        self.assertEqual(len(HAND_EDGES), 20)
        self.assertEqual(WHOLEBODY133_NAMES[91], "left_hand_root")
        self.assertEqual(WHOLEBODY133_NAMES[111], "left_pinky_finger4")
        self.assertEqual(WHOLEBODY133_NAMES[112], "right_hand_root")
        self.assertEqual(WHOLEBODY133_GROUPS["left_hand"], list(range(91, 112)))
        self.assertEqual(WHOLEBODY133_GROUPS["right_hand"], list(range(112, 133)))
        self.assertEqual(pose_topology(WHOLEBODY133_PROFILE)[2], 5)
        self.assertEqual(pose_topology(COCO17_PROFILE)[2], 0)

    def test_normalization_requires_real_133_channels(self) -> None:
        with self.assertRaisesRegex(ValueError, "133"):
            normalized_keypoints([[20, 20]] * 17, [.8] * 17, 100, 80, .3, WHOLEBODY133_NAMES)
        points = normalized_keypoints([[20, 20]] * 133, [.8] * 133, 100, 80, .3, WHOLEBODY133_NAMES)
        self.assertEqual(len(points), 133)
        self.assertEqual(points[132]["name"], "right_pinky_finger4")

    def test_body_only_roi_gate_ignores_high_confidence_hands(self) -> None:
        points = [{"visible": i >= 17, "pixel_x": 98 if i >= 17 else 40,
                   "pixel_y": 70 if i >= 17 else 30} for i in range(133)]
        self.assertFalse(tracking_quality(points))
        self.assertEqual(update_bbox([20, 10, 50, 60], points, 100, 80), [20, 10, 50, 60])
        for index in (5, 6, 11, 12, 7, 8):
            points[index]["visible"] = True
        self.assertTrue(tracking_quality(points))
        moved = update_bbox([20, 10, 50, 60], points, 100, 80)
        self.assertLess(moved[0], 50)

    def test_manifest_binding_checked_before_torch(self) -> None:
        self.assertEqual(len(validate_manifest(self.manifest_path)["frames"]), 2)
        self.assertNotIn("torch", sys.modules)
        (self.project / "left.png").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "image_sha256 changed"):
            validate_manifest(self.manifest_path)
        self.assertNotIn("torch", sys.modules)

    def test_17_head_rejected_for_wholebody_before_torch(self) -> None:
        model = self.project / "fake-legacy-head"
        model.mkdir()
        (model / "config.json").write_text(json.dumps({"id2label": {str(i): str(i) for i in range(17)},
                                                        "backbone_config": {"num_experts": 6}}), encoding="utf-8")
        header = json.dumps({"head.conv.weight": {"shape": [17, 256, 1, 1]},
                             "head.conv.bias": {"shape": [17]}}).encode("utf-8")
        (model / "model.safetensors").write_bytes(struct.pack("<Q", len(header)) + header)
        with self.assertRaisesRegex(ValueError, "genuine 133-joint"):
            validate_model_head(model, WHOLEBODY133_PROFILE)
        validate_model_head(model, COCO17_PROFILE)
        (model / "config.json").write_text(json.dumps({"id2label": {str(i): str(i) for i in range(133)},
                                                        "backbone_config": {"num_experts": 6}}), encoding="utf-8")
        header = json.dumps({"head.conv.weight": {"shape": [133, 256, 1, 1]},
                             "head.conv.bias": {"shape": [133]}}).encode("utf-8")
        (model / "model.safetensors").write_bytes(struct.pack("<Q", len(header)) + header)
        validate_model_head(model, WHOLEBODY133_PROFILE)
        self.assertNotIn("torch", sys.modules)

    def test_visible_manual_edit_preserves_model_score_and_parent(self) -> None:
        parent = self.result()
        original = copy.deepcopy(parent)
        merged = apply_corrections(self.manifest, parent, self.corrections(parent))
        self.assertEqual(parent, original)
        point = merged["frames"][0]["keypoints"][95]
        self.assertEqual(point["name"], "left_thumb4")
        self.assertEqual((point["x"], point["y"]), (.61, .73))
        self.assertEqual(point["score"], .7)
        self.assertEqual(point["manual_source"], "manual_2d")
        self.assertEqual(point["manual_visibility"], "visible")
        self.assertTrue(point["manual_position"])
        self.assertEqual(point["model_prediction"]["x"], .4)
        self.assertEqual(merged["frames"][0]["keypoints"][94], parent["frames"][0]["keypoints"][94])
        self.assertEqual(verify_result(self.manifest, merged)["joint_count"], 133)

    def test_archived_workbench_xywh_array_works_with_corrections(self) -> None:
        parent = self.result()
        for frame in parent["frames"]:
            frame["bbox"] = [.2, .2, .5, .6]
        merged = apply_corrections(self.manifest, parent, self.corrections(parent))
        self.assertEqual(merged["frames"][0]["bbox"], [.2, .2, .5, .6])
        self.assertEqual(verify_result(self.manifest, merged)["frames"], 2)

    def test_lost_frame_accepts_only_explicit_manual_visible(self) -> None:
        parent = self.result(lost=True)
        merged = apply_corrections(self.manifest, parent, self.corrections(parent))
        self.assertIsNone(merged["frames"][0]["bbox"])
        self.assertEqual(merged["frames"][0]["tracking_status"], "lost")
        self.assertTrue(merged["frames"][0]["keypoints"][95]["in_frame"])
        self.assertEqual(verify_result(self.manifest, merged)["lost_frames"], 2)
        forged = copy.deepcopy(merged)
        forged["frames"][0]["keypoints"][95].pop("manual_source")
        with self.assertRaisesRegex(ValueError, "null bbox"):
            verify_result(self.manifest, forged)

    def test_occluded_missing_and_projected_provenance(self) -> None:
        parent = self.result(projected=True)
        occluded = apply_corrections(self.manifest, parent,
                                     self.corrections(parent, edit={"name": "right_forefinger1", "visibility": "occluded"}))
        point = occluded["frames"][0]["keypoints"][117]
        self.assertEqual(occluded["evidence_kind"], "projected_3d")
        self.assertEqual(point["score"], .7)
        self.assertFalse(point["in_frame"])
        self.assertFalse(point["manual_position"])
        self.assertEqual(point["manual_visibility"], "occluded")
        missing = apply_corrections(self.manifest, parent,
                                    self.corrections(parent, edit={"name": "left_thumb4", "visibility": "missing"}))
        point = missing["frames"][0]["keypoints"][95]
        self.assertFalse(point["in_frame"])
        self.assertEqual(point["score"], .7)
        self.assertFalse(point["manual_position"])
        self.assertEqual(point["manual_visibility"], "missing")
        with self.assertRaises(ValueError):
            apply_corrections(self.manifest, parent,
                              self.corrections(parent, edit={"name": "left_thumb4", "x": float("nan"), "y": .3,
                                                             "visibility": "visible"}))

    def test_corrections_reject_stale_or_invalid_bindings(self) -> None:
        parent = self.result()
        cases = [("source_snapshot_id", "other"), ("parent_job_id", "other"),
                 ("keypoint_profile", COCO17_PROFILE), ("coordinate_frame", "pixels")]
        for key, value in cases:
            with self.subTest(key=key):
                doc = self.corrections(parent)
                doc[key] = value
                with self.assertRaises(ValueError):
                    apply_corrections(self.manifest, parent, doc)
        for key, value in (("view_id", "wrong"), ("time_seconds", 1.0),
                           ("image_sha256", "0" * 64), ("image_orientation", "wrong")):
            with self.subTest(frame_key=key):
                doc = self.corrections(parent)
                doc["frames"][0][key] = value
                with self.assertRaises(ValueError):
                    apply_corrections(self.manifest, parent, doc)
        doc = self.corrections(parent)
        doc["frames"][0]["original_keypoints"][95]["score"] = .9
        with self.assertRaisesRegex(ValueError, "parent joint"):
            apply_corrections(self.manifest, parent, doc)

    def test_apply_corrections_cli_preserves_original_file(self) -> None:
        parent = self.result()
        result_path = self.project / "parent.json"
        result_path.write_text(json.dumps(parent), encoding="utf-8")
        corrections_path = self.project / "corrections.json"
        corrections_path.write_text(json.dumps(self.corrections(parent)), encoding="utf-8")
        output = self.project / "merged.json"
        script = Path(__file__).with_name("pose_evidence.py")
        run = subprocess.run([sys.executable, str(script), "apply-corrections",
                              "--manifest", str(self.manifest_path), "--result", str(result_path),
                              "--corrections", str(corrections_path), "--output", str(output)],
                             text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(run.stdout)["type"], "corrected")
        self.assertEqual(json.loads(result_path.read_text()), parent)
        self.assertEqual(json.loads(output.read_text())["provenance"]["manual"]["edited_joints"], 1)
        with self.assertRaises(subprocess.CalledProcessError):
            subprocess.run([sys.executable, str(script), "apply-corrections",
                            "--manifest", str(self.manifest_path), "--result", str(result_path),
                            "--corrections", str(corrections_path), "--output", str(output)],
                           text=True, capture_output=True, check=True)


if __name__ == "__main__":
    unittest.main()

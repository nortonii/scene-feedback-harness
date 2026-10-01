#!/usr/bin/env python3
"""Isolated calibrated133 reconstruction and actual Blender save/reopen gates."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "external-skills/capsule-human-tracking/scripts"
sys.path.insert(0, str(TOOLS))
from wholebody_profile import KEYPOINT_NAMES, SKELETON_EDGES, WHOLEBODY133_PROFILE
from wholebody_reconstruction import (reconstruct, reconstruct_files, validate_motion, build_files,
                                     calibrated_camera, project, observation_weight)


def synthetic(directory, frames=4, views=3):
    points = np.zeros((133, 3))
    points[:, 2] = 4
    points[:17, :2] = [[0, -.65], [-.04, -.68], [.04, -.68], [-.09, -.65], [.09, -.65],
        [-.25, -.4], [.25, -.4], [-.47, -.3], [.47, -.3], [-.65, -.25], [.65, -.25],
        [-.15, .2], [.15, .2], [-.15, .65], [.15, .65], [-.15, 1.1], [.15, 1.1]]
    points[17:23, :2] = [[-.12, 1.18], [-.2, 1.18], [-.15, 1.06], [.12, 1.18], [.2, 1.18], [.15, 1.06]]
    points[23:91, :2] = np.c_[np.linspace(-.06, .06, 68), np.full(68, -.64)]
    for base, wrist in ((91, 9), (112, 10)):
        points[base] = points[wrist]
        for finger, first in enumerate((1, 5, 9, 13, 17)):
            for joint in range(4):
                points[base + first + joint] = points[wrist] + [(finger - 2) * .025, -.055 - .03 * joint, .004 * finger]
    world = np.asarray([points + [frame * .02, 0, 0] for frame in range(frames)])
    image = directory / "source.png"
    Image.new("RGB", (640, 480), "white").save(image)
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    manifest = {"schema_version": 2 if views > 1 else 1, "project_id": "test_project", "session_id": "test_session",
                "source_snapshot_id": "synthetic-calibration", "job_id": "test_job", "track_id": "person1",
                "frames": []}
    view_ids = ["view" + str(index) for index in range(views)]
    if views > 1:
        manifest["view_ids"] = view_ids
    else:
        manifest["view_id"] = view_ids[0]
    result = {key: copy.deepcopy(value) for key, value in manifest.items() if key != "frames"}
    result.update(evidence_kind="observed_2d", keypoint_profile=WHOLEBODY133_PROFILE,
                  keypoint_names=list(KEYPOINT_NAMES), skeleton_edges=[list(edge) for edge in SKELETON_EDGES],
                  provenance={"method": "known calibrated synthetic points", "source_artifact": "isolated test"}, frames=[])
    for view, center in zip(view_ids, ([0, 0, 0], [.8, 0, 0], [0, .6, 0])):
        c2w = np.eye(4)
        c2w[:3, 3] = center
        for frame, actual in enumerate(world):
            source = {"ref_id": f"{view}_{frame}", "view_id": view, "frame_index": frame, "time_seconds": frame / 30,
                "width": 640, "height": 480, "image_path": str(image), "image_sha256": digest, "image_orientation": 1,
                "camera": {"camera_to_world": c2w.tolist(), "intrinsics": {"width": 640, "height": 480,
                    "fx": 500, "fy": 500, "cx": 320, "cy": 240}}}
            camera = calibrated_camera(source, "opencv")
            prediction = {key: copy.deepcopy(value) for key, value in source.items() if key not in ("image_path", "camera")}
            prediction.update(bbox={"x": .2, "y": .2, "width": .6, "height": .7}, tracking_status="tracked", keypoints=[])
            for index, point in enumerate(actual):
                xy, depth = project(point, camera)
                assert depth > 0 and 0 <= xy[0] < 640 and 0 <= xy[1] < 480
                prediction["keypoints"].append({"id": index, "name": KEYPOINT_NAMES[index], "x": xy[0] / 640,
                    "y": xy[1] / 480, "score": 1, "in_frame": True})
            manifest["frames"].append(source)
            result["frames"].append(prediction)
    config = {"schema_version": 1, "camera_convention": "opencv", "confidence_threshold": .3, "update_gain": 1,
              "association": {"actor_id": "person1", "identity_verified": True,
                "verification": "synthetic known same actor in every synchronized camera",
                "view_track_ids": {view: "person1" for view in view_ids}}}
    return config, manifest, result, world


class ReconstructionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.config, self.manifest, self.result, self.world = synthetic(self.directory)

    def tearDown(self):
        self.tmp.cleanup()

    def test_known_calibrated_body_two_hands_and_fixed_finger_lengths(self):
        model, motion, report = reconstruct(self.config, self.manifest, self.result)
        self.assertEqual(report["status"], "complete_body_and_hands")
        self.assertEqual(len(model["parts"]["left_hand"]["edges"]), 20)
        self.assertEqual(len(model["parts"]["right_hand"]["edges"]), 20)
        np.testing.assert_allclose(motion["triangulated_positions"], self.world, atol=1e-10)
        np.testing.assert_allclose(motion["positions"][:, 5:23], self.world[:, 5:23], atol=1e-10)
        for base in (91, 112):
            np.testing.assert_allclose(motion["positions"][:, base:base + 21], self.world[:, base:base + 21], atol=1e-10)
        self.assertLess(validate_motion(model, motion)["fixed_bone_max_error"], 1e-10)
        self.assertIsNone(model["source_unit_in_meters"])
        self.assertIn("metric scale not established", report["scale"])
        self.assertFalse(motion["available"][:, 23:91].any())

    def test_occlusion_holds_all_previous_state_without_velocity_extrapolation(self):
        for frame in self.result["frames"]:
            if frame["frame_index"] == 2:
                for point in frame["keypoints"]:
                    point.update(score=0, in_frame=False)
        model, motion, report = reconstruct(self.config, self.manifest, self.result)
        np.testing.assert_allclose(motion["positions"][2], motion["positions"][1], equal_nan=True, atol=0)
        self.assertEqual(report["frames_report"][2]["parts"]["left_hand"]["held_directions"], 20)
        self.assertGreater(np.nanmax(abs(motion["positions"][3] - motion["positions"][2])), 0)

    def test_prefix_replay_and_missing_hand_do_not_use_future_initialization(self):
        for frame in self.result["frames"]:
            if frame["frame_index"] < 2:
                frame["keypoints"][111].update(score=0, in_frame=False)
        model, full, report = reconstruct(self.config, self.manifest, self.result)
        short_model, prefix, short_report = reconstruct(self.config, self.manifest, self.result, limit=2)
        self.assertEqual(model["parts"]["left_hand"]["initialized_frame"], 2)
        self.assertNotIn("left_hand", short_model["parts"])
        self.assertFalse(full["available"][:2, 91:112].any())
        self.assertEqual(validate_motion(model, full, prefix)["prefix_replay_frames"], 2)
        self.assertEqual(short_report["status"], "partial")

    def test_per_frame_hand_shape_noise_cannot_resize_the_template(self):
        for frame in self.result["frames"]:
            if frame["frame_index"] > 0:
                for point in frame["keypoints"][91:112]:
                    point["y"] -= .001 * frame["frame_index"] * (point["id"] - 91)
        model, motion, report = reconstruct(self.config, self.manifest, self.result)
        self.assertLess(validate_motion(model, motion)["fixed_bone_max_error"], 1e-10)
        self.assertGreater(report["frames_report"][3]["model_reprojection_px"]["max"], .1)

    def test_inconsistent_body_and_hand_wrist_is_not_silently_detached(self):
        for frame in self.result["frames"]:
            frame["keypoints"][91]["x"] += .1
        model, motion, report = reconstruct(self.config, self.manifest, self.result)
        self.assertNotIn("left_hand", model["parts"])
        self.assertIn("left_hand", report["unsupported_parts"])
        self.assertEqual(report["frames_report"][0]["wrist_fusion"][0]["status"], "rejected_inconsistent_same_wrist")

    def test_threejs_axes_are_explicit_and_wrong_camera_shapes_are_rejected(self):
        changed = copy.deepcopy(self.manifest)
        for source in changed["frames"]:
            matrix = np.asarray(source["camera"]["camera_to_world"])
            matrix[:3, :3] = np.diag([1, -1, -1])
            source["camera"]["camera_to_world"] = matrix.tolist()
        model, motion, _ = reconstruct({**self.config, "camera_convention": "threejs"}, changed, self.result)
        np.testing.assert_allclose(motion["triangulated_positions"], self.world, atol=1e-10)
        for mutate in (lambda m: m["frames"][0].pop("camera"),
                       lambda m: m["frames"][0]["camera"]["intrinsics"].update(width=641),
                       lambda m: m["frames"][0]["camera"].update(distortion=[.1, 0, 0, 0, 0]),
                       lambda m: m["frames"][0]["camera"]["camera_to_world"][0].__setitem__(0, 2)):
            bad = copy.deepcopy(self.manifest)
            mutate(bad)
            with self.assertRaises(ValueError):
                reconstruct(self.config, bad, self.result)

    def test_bad_third_view_is_excluded_by_reprojection_consensus(self):
        for frame in self.result["frames"]:
            if frame["view_id"] == "view2":
                frame["keypoints"][100]["x"] += .08
        _, motion, report = reconstruct(self.config, self.manifest, self.result)
        np.testing.assert_allclose(motion["triangulated_positions"][:, 100], self.world[:, 100], atol=1e-10)
        self.assertEqual(report["frames_report"][0]["joints"][100]["views"], ["view0", "view1"])

    def test_coincident_cameras_cannot_establish_depth(self):
        for source in self.manifest["frames"]:
            source["camera"]["camera_to_world"] = np.eye(4).tolist()
        _, motion, report = reconstruct(self.config, self.manifest, self.result)
        self.assertEqual(report["status"], "unsupported")
        self.assertFalse(motion["triangulated_available"].any())

    def test_identity_provenance_source_mismatch_and_projections_are_rejected(self):
        bad_configs = [{**self.config, "association": {**self.config["association"], "identity_verified": False}},
                       {**self.config, "association": {**self.config["association"], "view_track_ids": {"view0": "person1"}}},
                       {**self.config, "camera_convention": "guess"},
                       {**self.config, "source_unit_in_meters": 1}]
        for config in bad_configs:
            with self.assertRaises(ValueError):
                reconstruct(config, self.manifest, self.result)
        for key, value in (("source_snapshot_id", "foreign"), ("evidence_kind", "projected_3d")):
            with self.assertRaises(ValueError):
                reconstruct(self.config, self.manifest, {**self.result, key: value})
        Image.new("RGB", (640, 480), "red").save(self.directory / "source.png")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            reconstruct(self.config, self.manifest, self.result)

    def test_single_view_without_existing_depth_reports_unsupported(self):
        config, manifest, result, _ = synthetic(self.directory, views=1)
        model, motion, report = reconstruct(config, manifest, result)
        self.assertEqual(report["status"], "unsupported")
        self.assertEqual(model["parts"], {})
        self.assertFalse(motion["available"].any())
        self.assertTrue(np.isnan(motion["positions"]).all())

    def test_existing_3d_hand_is_explicit_partial_and_not_a_fake_body(self):
        config, manifest, result, world = synthetic(self.directory, views=1)
        hands = {"schema_version": 1, "project_id": manifest["project_id"], "session_id": manifest["session_id"],
                 "source_snapshot_id": manifest["source_snapshot_id"], "actor_id": "person1", "coordinate_frame": "camera_world",
                 "joint_order": "wholebody_hand21", "provenance": {"method": "known synthetic 3D", "source_artifact": "test"},
                 "frames": [{"time_seconds": index / 30, "left": {"joints": frame[91:112].tolist(), "scores": [1] * 21}}
                            for index, frame in enumerate(world)]}
        model, motion, report = reconstruct(config, manifest, result, hands_3d=hands)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(set(model["parts"]), {"left_hand"})
        self.assertFalse(motion["available"][:, :91].any())
        np.testing.assert_allclose(motion["positions"][:, 91:112], world[:, 91:112], atol=1e-10)
        wrong_time = copy.deepcopy(hands)
        wrong_time["frames"][0]["time_seconds"] = .001
        with self.assertRaisesRegex(ValueError, "synchronized source group"):
            reconstruct(config, manifest, result, hands_3d=wrong_time)
        # Missing/weak existing 3D does not erase genuine, confident multi-view 2D.
        multiview = copy.deepcopy(hands)
        for frame in multiview["frames"]:
            frame["left"]["scores"] = [0] * 21
        full_model, full_motion, _ = reconstruct(self.config, self.manifest, self.result, hands_3d=multiview)
        self.assertIn("left_hand", full_model["parts"])
        np.testing.assert_allclose(full_motion["positions"][:, 91:112], self.world[:, 91:112], atol=1e-10)

    def test_manual_visible_weight_is_separate_from_model_confidence(self):
        point = {"score": .1, "in_frame": True, "manual_source": "manual_2d", "manual_position": True,
                 "manual_visibility": "visible"}
        self.assertEqual(observation_weight(point, .3, .8), .8)
        self.assertEqual(point["score"], .1)
        for visibility in ("occluded", "missing"):
            self.assertEqual(observation_weight({**point, "manual_visibility": visibility}, .3, .8), 0)
        self.assertEqual(observation_weight({**point, "manual_position": False}, .3, .8), 0)
        for frame in self.result["frames"]:
            frame["keypoints"][111].update(point)
        model, motion, _ = reconstruct(self.config, self.manifest, self.result)
        self.assertIn("left_hand", model["parts"])


class DeliveryTests(unittest.TestCase):
    def test_actual_blender_scene_preservation_fixed_meshes_and_static_glb(self):
        binary = os.environ.get("BLENDER_BIN", "/home/nortonii/.local/opt/blender-4.5.3-linux-x64/blender")
        if not Path(binary).is_file():
            self.skipTest("Blender binary unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, manifest, result, world = synthetic(root, frames=3)
            # Rotating a complete arm and hand makes a detached interpolation
            # observable; one initially missing fingertip also checks late visibility.
            for index, frame in enumerate(world):
                angle = .3 * index
                rotation = np.array([[np.cos(angle), -np.sin(angle), 0], [np.sin(angle), np.cos(angle), 0], [0, 0, 1]])
                shoulder = frame[5].copy()
                nodes = [7, 9, *range(91, 112)]
                frame[nodes] = (frame[nodes] - shoulder) @ rotation.T + shoulder
            for source, prediction in zip(manifest["frames"], result["frames"]):
                camera = calibrated_camera(source, "opencv")
                for index, point in enumerate(world[source["frame_index"]]):
                    xy, _ = project(point, camera)
                    prediction["keypoints"][index].update(x=xy[0] / 640, y=xy[1] / 480)
                if source["frame_index"] == 0:
                    prediction["keypoints"][111].update(score=0, in_frame=False)
            for name, document in (("sources.json", manifest), ("observations.json", result)):
                (root / name).write_text(json.dumps(document))
            config.update(manifest_path="sources.json", observations_path="observations.json")
            (root / "config.json").write_text(json.dumps(config))
            command = [sys.executable, str(TOOLS / "capsule.py"), "wholebody-reconstruct", "--config", str(root / "config.json"), "--output", str(root / "run")]
            subprocess.run(command, check=True, capture_output=True, text=True)
            scene = root / "source.blend"
            script = root / "create_scene.py"
            script.write_text("import bpy\nbpy.ops.wm.read_factory_settings(use_empty=True)\nbpy.ops.mesh.primitive_cube_add(location=(3,0,0))\nbpy.context.object.name='Preserved_Cube'\nbpy.ops.wm.save_as_mainfile(filepath=" + repr(str(scene)) + ")\n")
            subprocess.run([binary, "-b", "-t", "2", "--python", str(script)], check=True, capture_output=True)
            original_hash = hashlib.sha256(scene.read_bytes()).hexdigest()
            transform_path = root / "world_to_blender.json"
            transform_path.write_text(json.dumps(np.eye(4).tolist()))
            command = [sys.executable, str(TOOLS / "capsule.py"), "wholebody-build", "--run", str(root / "run"),
                       "--output", str(root / "build"), "--scene", str(scene), "--world-to-blender-json", str(transform_path),
                       "--preview-glb", "--blender", binary]
            process = subprocess.run(command, capture_output=True, text=True)
            if process.returncode:
                log = root / "build/blender.log"
                self.fail(process.stderr + (log.read_text()[-8000:] if log.exists() else process.stdout))
            report = json.loads((root / "build/build_report.json").read_text())
            self.assertTrue(report["reopened_standalone"] and report["reopened_integrated"])
            self.assertEqual(report["original_objects_preserved"], 1)
            self.assertEqual(report["finger_segments_per_hand"], {"left": 20, "right": 20})
            self.assertLess(report["max_saved_endpoint_error"], 1e-5)
            self.assertTrue(report["parented_fixed_endpoint_fk"])
            self.assertLess(report["midframe_connection_max_error"], 1e-6)
            self.assertEqual(hashlib.sha256(scene.read_bytes()).hexdigest(), original_hash)
            self.assertEqual(Path(report["glb_preview"]).read_bytes()[:4], b"glTF")
            with self.assertRaisesRegex(ValueError, "explicit world_to_blender"):
                build_files(root / "run", root / "forbidden", scene=scene)


if __name__ == "__main__":
    unittest.main()

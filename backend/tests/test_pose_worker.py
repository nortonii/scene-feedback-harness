"""Worker guards that prevent plausible-looking poses from using bad inputs."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pose_worker import (clip_bbox, command_for_job, normalized_keypoints, runtime_status,
                         tracking_quality, update_bbox, validate_manifest)  # noqa: E402


class PoseWorkerTests(unittest.TestCase):
    def test_missing_or_outside_human_box_never_becomes_an_entire_image(self):
        for box in (None, [0, 0, 0, 10], [1000, 1000, 20, 20], [0, math.nan, 50, 50]):
            with self.subTest(box=box), self.assertRaises(ValueError):
                clip_bbox(box, 960, 540)
        self.assertEqual(clip_bbox([-10, -20, 30, 50], 960, 540), [0, 0, 20, 30])

    def test_confident_feet_and_low_torso_cannot_drive_a_person_track(self):
        points = normalized_keypoints([[20, 30]] * 17, [.1] * 17, 100, 100, .3)
        for index in (9, 10, 13, 14, 15, 16):
            points[index]["visible"] = True
        self.assertFalse(tracking_quality(points))
        for index in (5, 6, 11):
            points[index]["visible"] = True
        self.assertTrue(tracking_quality(points))

    def test_outside_predictions_keep_raw_score_but_are_not_display_visible(self):
        points = normalized_keypoints([[110, -5]] * 17, [1.2] * 17, 100, 100, .3)
        for point in points:
            self.assertEqual((point["x"], point["y"], point["score"]), (1, 0, 1))
            self.assertEqual(point["raw_score"], 1.2)
            self.assertFalse(point["in_frame"])
            self.assertFalse(point["visible"])
        with self.assertRaises(ValueError):
            normalized_keypoints([[10, 5]] * 17, [math.nan] * 17, 100, 100, .3)

    def test_edge_tracking_moves_roi_without_cutting_away_its_height(self):
        points = normalized_keypoints([[520 + (i % 2) * 140, 30 + i * 20] for i in range(17)],
                                      [.8] * 17, 960, 540, .3)
        box = [440., 0., 400., 540.]
        moved = update_bbox(box, points, 960, 540)
        self.assertGreaterEqual(moved[3], box[3] * .97 - 1e-6)
        self.assertGreaterEqual(moved[0], 0)
        self.assertGreaterEqual(moved[1], 0)
        self.assertLessEqual(moved[0] + moved[2], 960 + 1e-6)
        self.assertLessEqual(moved[1] + moved[3], 540 + 1e-6)
        clipped = clip_bbox([958, 0, 5, 100], 960, 540)
        self.assertGreaterEqual(clipped[2] / 960, .01)

    def test_manifest_uses_browser_orientation_and_rejects_raw_dimension_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = Image.new("RGB", (40, 60))
            exif = Image.Exif()
            exif[274] = 6
            image.save(root / "rotated.jpg", exif=exif)
            frame = {"image_path": "rotated.jpg", "frame_index": 0, "time_seconds": 0,
                     "ref_id": "reference", "bbox_xywh": [10, 5, 40, 30], "width": 60, "height": 40}
            manifest = {"schema_version": 1, "job_id": "job", "track_id": "person", "view_id": "reference:one",
                        "frames": [frame]}
            path = root / "input.json"
            path.write_text(json.dumps(manifest))
            self.assertEqual(validate_manifest(path)["frames"][0]["width"], 60)
            frame.update(width=40, height=60)
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "EXIF-oriented"):
                validate_manifest(path)
            frame.update(width=60, height=40, view_id="another-camera")
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "mix camera views"):
                validate_manifest(path)

    def test_runner_argv_does_not_evaluate_shell_text_in_paths(self):
        with patch.dict(os.environ, {"SCENE_FEEDBACK_POSE_RUNNER": json.dumps([
            sys.executable, "--input", "{manifest}", "--output", "{output}"
        ])}):
            command = command_for_job(Path("/tmp/person $(touch BAD).json"), Path("/tmp/pose result.json"))
            self.assertEqual(command[2], "/tmp/person $(touch BAD).json")
            self.assertEqual(command[4], "/tmp/pose result.json")
            self.assertTrue(runtime_status()["configured"])
        with patch.dict(os.environ, {"SCENE_FEEDBACK_POSE_RUNNER": "echo {manifest}"}):
            self.assertFalse(runtime_status()["configured"])
            with self.assertRaises(ValueError):
                command_for_job(Path("input.json"), Path("output.json"))


if __name__ == "__main__":
    unittest.main()

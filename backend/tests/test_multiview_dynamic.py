"""Multi-view imports and immutable evidence keep each camera's time and identity."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import APIError, SceneStore
from dynamic import MAX_CLIP_MANIFEST_BYTES, reference_frames, reference_views, shared_duration
from gateway import WorkspaceGateway
import mcp_server
from backend.tests.test_dynamic_scenes import calibrated_camera, image_data


class MultiViewDynamicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.store = SceneStore(self.root / "data")
        self.gateway = WorkspaceGateway(self.store, self.project, external_review=True)
        self.session = self.gateway.ensure()["session_id"]
        self.data, self.data_url = image_data()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def two_views(self) -> dict:
        first = self.store.set_reference_clip(self.session, {"name": "camera_A", "fps": 2, "frames": [
            {"name": "camera_A_0.png", "time_sec": 0, "data_url": self.data_url},
            {"name": "camera_A_1.png", "time_sec": 0.5, "data_url": self.data_url},
        ]})["reference_clip"]
        clip = self.store.set_reference_clip(self.session, {"name": "camera_B", "fps": 3, "duration_sec": 2.5,
            "append_view": True, "frames": [{"name": f"camera_B_{i}.png", "time_sec": t, "data_url": self.data_url}
                for i, t in enumerate((0, 0.34, 0.8, 2.0))]})["reference_clip"]
        self.assertEqual(clip["clip_id"], first["clip_id"])
        self.assertEqual(clip["frames"], first["frames"])
        return clip

    def payload(self, clip: dict) -> dict:
        primary, second = reference_views(clip)
        return {"scene_revision": 1, "note": "Compare both cameras at the same scene time.",
            "timeline": {"clip_id": clip["clip_id"], "view_id": second["clip_id"], "fps": primary["fps"],
                         "duration_sec": shared_duration(clip), "time_sec": 0.4, "scope": {"kind": "frame"}},
            "dynamic_frames": [{"id": f"evidence{i}", "view_id": view["clip_id"], "time_sec": 0.4, "scene_revision": 1,
                "reference_frame_id": view["frames"][1]["id"], "camera": {}, "scene_original_data_url": self.data_url,
                "scene_annotated_data_url": self.data_url, "reference_annotated_data_url": self.data_url,
                "view_name": "spoofed", "reference_time_sec": 999, "frame_index": 999}
                for i, view in enumerate((primary, second))],
            "annotations": [{"id": f"mark{i}", "type": "point", "pane": "reference", "coordinates": {"x": 0.2, "y": 0.3},
                "frame_id": f"evidence{i}", "view_id": view["clip_id"], "clip_id": clip["clip_id"], "time_sec": 0.4,
                "scene_revision": 1, "reference_image_id": view["frames"][1]["id"], "view_name": "spoofed", "frame_index": 999}
                for i, view in enumerate((primary, second))]}

    def test_append_preserves_primary_and_replacement_and_limit_are_persistent(self) -> None:
        clip = self.two_views()
        primary_id, second_id = [view["clip_id"] for view in reference_views(clip)]
        replacement = self.store.set_reference_clip(self.session, {"replace_view_id": second_id, "name": "B revised", "fps": 4,
            "frames": [{"data_url": self.data_url}]})["reference_clip"]
        self.assertEqual(replacement["clip_id"], primary_id)
        self.assertEqual(replacement["frames"], clip["frames"])
        self.assertEqual(replacement["views"][0]["clip_id"], second_id)
        self.assertGreater(replacement["views_revision"], clip["views_revision"])
        for i in range(6):
            replacement = self.store.set_reference_clip(self.session, {"append_view": True, "name": f"extra{i}",
                "frames": [{"data_url": self.data_url}]})["reference_clip"]
        self.assertEqual(len(reference_views(replacement)), 8)
        with self.assertRaisesRegex(APIError, "8 views"):
            self.store.set_reference_clip(self.session, {"append_view": True, "frames": [{"data_url": self.data_url}]})
        self.assertEqual(SceneStore(self.store.data_dir).get_session(self.session)["reference_clip"], replacement)
        fresh = self.store.set_reference_clip(self.session, {"frames": [{"data_url": self.data_url}]})["reference_clip"]
        self.assertEqual(len(reference_views(fresh)), 1)
        self.assertNotEqual(fresh["clip_id"], primary_id)

    def test_same_scene_time_keeps_per_view_sampling_and_all_model_images(self) -> None:
        clip = self.two_views()
        payload = self.payload(clip)
        untouched = copy.deepcopy(payload)
        packet = self.store.submit_feedback(self.session, payload)
        self.assertEqual(payload, untouched)
        self.assertEqual([frame["time_sec"] for frame in packet["dynamic_frames"]], [0.4, 0.4])
        self.assertEqual([frame["reference_time_sec"] for frame in packet["dynamic_frames"]], [0.5, 0.34])
        self.assertEqual([frame["view_name"] for frame in packet["dynamic_frames"]], ["camera_A", "camera_B"])
        self.assertEqual([frame["frame_index"] for frame in packet["dynamic_frames"]], [1, 1])
        self.assertEqual([mark["view_name"] for mark in packet["annotations"]], ["camera_A", "camera_B"])
        self.assertEqual(packet["timeline"]["duration_sec"], 2.5)
        self.assertEqual(packet["timeline"]["fps"], 2)
        self.assertNotIn("frames", packet["reference_clip"])
        self.assertNotIn("frames", packet["reference_clip"]["views"][0])
        message, paths = self.gateway._turn_input(packet)
        self.assertEqual(len(paths), 8)
        self.assertIn("机位 camera_B", message)
        self.assertIn("参考采样时间 0.340000 秒", message)
        with patch.object(mcp_server, "DATA_DIR", self.store.data_dir):
            result = mcp_server._visual_tool_result({"items": [copy.deepcopy(packet)]})
        self.assertEqual(sum(item.type == "image" for item in result.content), 8)
        self.assertTrue(any("view camera_B" in item.text and "reference sample time 0.340000s" in item.text
                            for item in result.content if item.type == "text"))
        self.assertEqual(SceneStore(self.store.data_dir).feedback_by_id(packet["feedback_id"]), packet)

    def test_cross_view_ids_and_missing_secondary_annotation_identity_are_rejected(self) -> None:
        clip = self.two_views()
        primary, secondary = reference_views(clip)
        mutations = [
            lambda p: p["timeline"].update(view_id="unknown"),
            lambda p: p["dynamic_frames"][1].update(view_id="unknown"),
            lambda p: p["dynamic_frames"][1].update(view_id=primary["clip_id"]),
            lambda p: p["dynamic_frames"][1].pop("view_id"),
            lambda p: p["annotations"][1].pop("view_id"),
            lambda p: p["annotations"][1].update(view_id=primary["clip_id"]),
            lambda p: p["timeline"].update(duration_sec=primary["duration_sec"]),
            lambda p: p["timeline"].update(fps=secondary["fps"]),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                payload = self.payload(clip)
                mutate(payload)
                with self.assertRaises(APIError):
                    self.store.submit_feedback(self.session, payload)
        self.assertEqual(self.store.list_all_feedback(), [])

    def test_legacy_primary_and_static_frames_need_no_view_id(self) -> None:
        clip = self.two_views()
        payload = self.payload(clip)
        payload["dynamic_frames"] = payload["dynamic_frames"][:1]
        payload["annotations"] = payload["annotations"][:1]
        payload["timeline"].pop("view_id")
        payload["dynamic_frames"][0].pop("view_id")
        payload["annotations"][0].pop("view_id")
        packet = self.store.submit_feedback(self.session, payload)
        self.assertEqual(packet["dynamic_frames"][0]["view_id"], clip["clip_id"])
        self.assertEqual(packet["annotations"][0]["view_id"], clip["clip_id"])
        reference = self.store.add_reference(self.session, "static.png", self.data_url)
        payload["dynamic_frames"][0].update(reference_frame_id=None, static_reference_id=reference["id"])
        payload["annotations"][0]["reference_image_id"] = reference["id"]
        packet = self.store.submit_feedback(self.session, payload)
        self.assertNotIn("view_id", packet["dynamic_frames"][0])
        self.assertNotIn("view_id", packet["annotations"][0])
        self.assertNotIn("frame_index", packet["dynamic_frames"][0])

    def test_batch_project_manifest_validates_all_views_before_publishing(self) -> None:
        self.two_views()
        directory = self.project / "clips"
        directory.mkdir()
        (directory / "image.png").write_bytes(self.data)
        single = {"name": "nested camera", "fps": 3, "frames": [{"path": "image.png", "time_sec": 0}]}
        (directory / "single.json").write_text(json.dumps(single))
        legacy = self.gateway.set_reference_clip_paths({"manifest_path": str(directory / "single.json"), "fps": 10})["reference_clip"]
        self.assertEqual(legacy["fps"], 3)  # The tool fps parameter historically applies only to videos.
        manifest = directory / "views.json"
        valid = {"name": "motion", "views": [
            {"name": "A", "fps": 2, "duration_sec": 1, "frames": [{"path": "image.png", "time_sec": 0}]},
            {"manifest_path": "single.json", "name": "B", "fps": 4},
        ]}
        manifest.write_text(json.dumps(valid))
        result = self.gateway.set_reference_clip_paths({"manifest_path": str(manifest)})["reference_clip"]
        self.assertEqual(result["group_name"], "motion")
        self.assertEqual([view["name"] for view in reference_views(result)], ["A", "B"])
        self.assertEqual(result["views"][0]["fps"], 4)
        before_media = set(self.store.media_dir.iterdir())
        invalid = copy.deepcopy(valid)
        invalid["views"][1] = {"frames": [{"path": "missing.png"}]}
        manifest.write_text(json.dumps(invalid))
        with self.assertRaises(APIError):
            self.gateway.set_reference_clip_paths({"manifest_path": str(manifest), "append_view": True})
        self.assertEqual(self.store.get_session(self.session)["reference_clip"], result)
        self.assertEqual(set(self.store.media_dir.iterdir()), before_media)
        outside = self.root / "outside.png"
        outside.write_bytes(self.data)
        invalid["views"][1] = {"frames": [{"path": str(outside)}]}
        manifest.write_text(json.dumps(invalid))
        with self.assertRaisesRegex(APIError, "inside the workspace"):
            self.gateway.set_reference_clip_paths({"manifest_path": str(manifest)})

    def test_camera_updates_and_manifest_cover_additional_views(self) -> None:
        clip = self.two_views()
        second = clip["views"][0]
        camera = calibrated_camera()
        result = self.store.set_reference_cameras(self.session, [
            {"reference_id": clip["frames"][0]["id"], "camera": camera},
            {"reference_id": second["frames"][0]["id"], "camera": camera},
        ])["reference_clip"]
        self.assertEqual(result["views"][0]["frames"][0]["camera"], camera)
        self.assertGreater(result["views_revision"], clip["views_revision"])
        mapping = {"schema_version": 1, "entries": [
            {"name_prefix": "camera_A_", "camera": camera}, {"name_prefix": "camera_B_", "camera": camera}]}
        (self.store.data_dir / "reference_cameras.json").write_text(json.dumps(mapping))
        # Different cameras often export identical frame filenames. The view
        # label qualifies those names for the fixed-camera manifest fallback.
        for frame in reference_frames(self.store.state["sessions"][self.session]["reference_clip"]):
            frame["name"] = "0001.png"
        self.store._save()
        updated = self.gateway.apply_reference_camera_manifest()["reference_clip"]
        self.assertTrue(all(frame["camera"] == camera for frame in reference_frames(updated)))
        self.assertEqual(SceneStore(self.store.data_dir).get_session(self.session)["reference_clip"], updated)
        payload = self.payload(updated)
        packet = self.store.submit_feedback(self.session, payload)
        self.assertEqual(packet["dynamic_frames"][1]["reference_camera"], camera)

    def test_full_calibrated_batch_exceeds_single_view_limit_and_invalid_times_are_400(self) -> None:
        image = self.project / "image.png"
        image.write_bytes(self.data)
        camera = {**calibrated_camera(), "calibration_status": "c" * 200}
        frames = [{"path": "image.png", "time_sec": index / 10, "camera": camera} for index in range(600)]
        document = {"views": [{"name": f"camera_{index}", "fps": 10, "frames": frames} for index in range(8)]}
        encoded = json.dumps(document).encode()
        self.assertGreater(len(encoded), MAX_CLIP_MANIFEST_BYTES)
        manifest = self.project / "batch.json"
        manifest.write_bytes(encoded)
        clip = self.gateway.set_reference_clip_paths({"manifest_path": str(manifest)})["reference_clip"]
        self.assertEqual([len(view["frames"]) for view in reference_views(clip)], [600] * 8)
        oversized_view = {"views": [{"frames": [{"path": "image.png"}], "unused": "x" * MAX_CLIP_MANIFEST_BYTES}]}
        manifest.write_text(json.dumps(oversized_view))
        with self.assertRaisesRegex(APIError, "view manifest exceeds 2 MB"):
            self.gateway.set_reference_clip_paths({"manifest_path": str(manifest)})
        manifest.write_text(json.dumps({"frames": [{"path": "image.png", "time_sec": "invalid"}]}))
        with self.assertRaises(APIError) as rejected:
            self.gateway.set_reference_clip_paths({"manifest_path": str(manifest)})
        self.assertEqual(rejected.exception.status, 400)
        self.assertEqual(self.store.get_session(self.session)["reference_clip"], clip)

    def test_mcp_append_name_and_context_paths_include_all_views(self) -> None:
        clip = self.two_views()
        with patch.object(mcp_server, "ensure_http_server"), patch.object(mcp_server, "_http", return_value={}) as http:
            mcp_server.workspace_set_reference_clip(manifest_path="cam.json", append_view=True, view_name="camera B")
        self.assertEqual(http.call_args.args[2]["append_view"], True)
        self.assertEqual(http.call_args.args[2]["view_name"], "camera B")
        context = {"reference_clip": copy.deepcopy(clip), "reference_images": [], "scene": {"objects": []}}
        with patch.object(mcp_server, "ensure_http_server"), patch.object(mcp_server, "_http", return_value=context), patch.object(mcp_server, "DATA_DIR", self.store.data_dir):
            result = mcp_server.workspace_get_context()
        self.assertTrue(all(frame["path"] for frame in reference_frames(result.structured_content["reference_clip"])))


if __name__ == "__main__":
    unittest.main()

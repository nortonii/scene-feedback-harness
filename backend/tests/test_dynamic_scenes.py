"""Dynamic reference imports and immutable timed feedback delivery."""

from __future__ import annotations

import base64
import copy
import io
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import APIError, SceneStore
from dynamic import sample_video
from gateway import WorkspaceGateway
from server import make_server
import mcp_server


def image_data(color: str = "red") -> tuple[bytes, str]:
    output = io.BytesIO()
    Image.new("RGB", (24, 24), color).save(output, format="PNG")
    data = output.getvalue()
    return data, "data:image/png;base64," + base64.b64encode(data).decode()


def calibrated_camera() -> dict:
    return {"camera_to_world": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 3], [0, 0, 0, 1]],
            "intrinsics": {"width": 24, "height": 24, "fx": 20, "fy": 20, "cx": 12, "cy": 12}}


class DynamicSceneTests(unittest.TestCase):
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

    def import_clip(self) -> dict:
        return self.store.set_reference_clip(self.session, {"name": "hand motion", "fps": 2, "frames": [
            {"name": "0001.png", "data_url": self.data_url, "time_sec": 0, "camera": calibrated_camera()},
            {"name": "0002.png", "data_url": self.data_url, "time_sec": 0.5},
        ]})["reference_clip"]

    def payload(self, clip: dict | None = None) -> dict:
        duration = clip["duration_sec"] if clip else 2
        fps = clip["fps"] if clip else 2
        return {"scene_revision": 1, "note": "Move this earlier.", "timeline": {"clip_id": clip["clip_id"] if clip else None,
                "time_sec": 0, "duration_sec": duration, "fps": fps, "scope": {"kind": "range", "start_sec": 0, "end_sec": 0.5}},
                "dynamic_frames": [{"id": "moment1", "time_sec": 0, "scene_revision": 1,
                "reference_frame_id": clip["frames"][0]["id"] if clip else None, "camera": {"position": [0, 0, 3]},
                "selected_object_ids": [], "selected_scene_nodes": [], "scene_original_data_url": self.data_url,
                "scene_annotated_data_url": self.data_url, **({"reference_annotated_data_url": self.data_url} if clip else {})}]}

    def test_sequence_import_camera_clone_clear_and_no_revision_change(self) -> None:
        clip = self.import_clip()
        self.assertEqual(clip["duration_sec"], 1)
        self.assertEqual(clip["source_type"], "sequence")
        self.assertEqual(clip["frames"][0]["camera"], calibrated_camera())
        self.assertEqual(self.store.scene()["revision"], 1)
        self.assertEqual(self.gateway.state()["reference_clip"], clip)
        cloned = self.store.create_session(reference_session_id=self.session)
        self.assertEqual(cloned["reference_clip"], clip)
        self.store.set_reference_clip(self.session, {"clear": True})
        self.assertIsNone(self.store.get_session(self.session)["reference_clip"])
        self.assertEqual(self.store.get_session(cloned["session_id"])["reference_clip"], clip)

    def test_manifest_relative_paths_and_project_boundary(self) -> None:
        directory = self.project / "clip"
        directory.mkdir()
        (directory / "frame.png").write_bytes(self.data)
        manifest = directory / "clip.json"
        manifest.write_text(json.dumps({"name": "motion", "fps": 5, "frames": [{"path": "frame.png", "time_sec": 0, "camera": calibrated_camera()}]}))
        clip = self.gateway.set_reference_clip_paths({"manifest_path": str(manifest)})["reference_clip"]
        self.assertEqual(clip["frames"][0]["name"], "frame.png")
        outside = self.root / "outside.png"
        outside.write_bytes(self.data)
        manifest.write_text(json.dumps({"frames": [{"path": str(outside)}]}))
        with self.assertRaisesRegex(APIError, "inside the workspace"):
            self.gateway.set_reference_clip_paths({"manifest_path": str(manifest)})
        self.assertEqual(self.store.get_session(self.session)["reference_clip"], clip)

    def test_bad_timestamps_camera_and_limits_leave_previous_clip_intact(self) -> None:
        clip = self.import_clip()
        base = {"fps": 2, "frames": [{"data_url": self.data_url, "time_sec": 0}, {"data_url": self.data_url, "time_sec": 0}]}
        with self.assertRaisesRegex(APIError, "strictly increasing"):
            self.store.set_reference_clip(self.session, base)
        camera = calibrated_camera()
        camera["intrinsics"]["width"] = 25
        with self.assertRaisesRegex(APIError, "dimensions"):
            self.store.set_reference_clip(self.session, {"frames": [{"data_url": self.data_url, "camera": camera}]})
        with self.assertRaisesRegex(APIError, "600"):
            self.store.set_reference_clip(self.session, {"frames": [{"data_url": self.data_url}] * 601})
        with self.assertRaisesRegex(APIError, "exceed"):
            self.store.set_reference_clip(self.session, {"frames": [{"data_url": self.data_url}]}, byte_limit=1)
        self.assertEqual(self.store.get_session(self.session)["reference_clip"], clip)

    def test_dynamic_evidence_is_immutable_and_reaches_gateway_and_mcp_as_images(self) -> None:
        clip = self.import_clip()
        payload = self.payload(clip)
        payload["idempotency_key"] = "dynamic-test-1"
        payload["annotations"] = [{"id": "mark1", "type": "rectangle", "pane": "reference",
            "reference_image_id": clip["frames"][0]["id"], "frame_id": "moment1", "time_sec": 0,
            "clip_id": clip["clip_id"], "scene_revision": 1, "coordinates": {"x": 0.1, "y": 0.2, "x2": 0.4, "y2": 0.5}}]
        payload["note"] = "Move to [[annotation:mark1]] at this time."
        feedback = self.store.submit_feedback(self.session, payload)
        evidence = feedback["dynamic_frames"][0]
        self.assertEqual(evidence["reference_original_url"], clip["frames"][0]["url"])
        self.assertEqual(evidence["reference_camera"], calibrated_camera())
        self.assertFalse(any(key.endswith("_data_url") for key in evidence))
        message, paths = self.gateway._turn_input(feedback)
        self.assertEqual(len(paths), 4)
        self.assertIn("0.000000 秒", message)
        self.assertIn("start_sec", message)
        with patch.object(mcp_server, "DATA_DIR", self.store.data_dir):
            tool_result = mcp_server._visual_tool_result({"items": [copy.deepcopy(feedback)]})
        self.assertEqual(sum(item.type == "image" for item in tool_result.content), 4)
        self.assertIn("camera", tool_result.structured_content["items"][0]["dynamic_frames"][0])
        # Replays are resolved before clip validation and remain exactly-once after replacement.
        self.store.set_reference_clip(self.session, {"clear": True})
        replay = self.store.submit_feedback(self.session, payload)
        self.assertEqual(replay["feedback_id"], feedback["feedback_id"])
        self.assertEqual(self.store.get_session(self.session)["feedback_count"], 1)
        self.assertEqual(SceneStore(self.store.data_dir).feedback_by_id(feedback["feedback_id"])["dynamic_frames"], feedback["dynamic_frames"])

    def test_clip_scope_frame_timestamp_and_annotation_associations_validate(self) -> None:
        clip = self.import_clip()
        mutations = [
            lambda p: p["timeline"].update(clip_id="unknown"),
            lambda p: p["timeline"].update(duration_sec=8),
            lambda p: p["timeline"].update(time_sec=99),
            lambda p: p["timeline"]["scope"].update(start_sec=0.7, end_sec=0.5),
            lambda p: p["dynamic_frames"][0].update(reference_frame_id="unknown"),
            lambda p: p["dynamic_frames"][0].update(reference_frame_id=clip["frames"][1]["id"]),
            lambda p: p["dynamic_frames"][0].update(time_sec=float("nan")),
            lambda p: p["dynamic_frames"][0].pop("scene_original_data_url"),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                payload = self.payload(clip)
                mutate(payload)
                with self.assertRaises(APIError):
                    self.store.submit_feedback(self.session, payload)
        payload = self.payload(clip)
        payload["annotations"] = [{"type": "point", "pane": "scene", "frame_id": "moment1", "time_sec": 0.1,
                "clip_id": clip["clip_id"], "scene_revision": 1, "coordinates": {"x": 0.5, "y": 0.5}}]
        with self.assertRaisesRegex(APIError, "timestamp"):
            self.store.submit_feedback(self.session, payload)

    def test_mixed_revision_evidence_requires_confirmation_and_oldest_top_revision(self) -> None:
        payload = self.payload()
        self.store.replace_scene(1, [])
        payload["dynamic_frames"].append({**payload["dynamic_frames"][0], "id": "moment2", "time_sec": 0.5, "scene_revision": 2})
        with self.assertRaisesRegex(APIError, "scene revision changed"):
            self.store.submit_feedback(self.session, payload)
        payload["confirm_stale"] = True
        packet = self.store.submit_feedback(self.session, payload)
        self.assertEqual([frame["scene_revision"] for frame in packet["dynamic_frames"]], [1, 2])
        self.assertTrue(packet["submitted_from_stale_snapshot"])
        payload["scene_revision"] = 2
        with self.assertRaisesRegex(APIError, "revision"):
            self.store.submit_feedback(self.session, payload)
        payload = self.payload()
        payload["confirm_stale"] = True
        payload["dynamic_frames"][0]["scene_revision"] = 99
        with self.assertRaisesRegex(APIError, "revision"):
            self.store.submit_feedback(self.session, payload)

    def test_animation_only_and_stable_node_metadata(self) -> None:
        packet = self.store.submit_feedback(self.session, self.payload())
        self.assertIsNone(packet["timeline"]["clip_id"])
        self.assertEqual(len(packet["dynamic_frames"]), 1)
        node = self.store._scene_node({"parent_object_id": "model", "node_path": [2], "stable_id": "chair/back", "semantic_id": "chair_back"}, {"model"})
        self.assertEqual(node["stable_id"], "chair/back")
        with self.assertRaisesRegex(APIError, "stable_id"):
            self.store._scene_node({**node, "stable_id": "bad\n"}, {"model"})

    def test_static_reference_can_be_marked_at_multiple_times_with_animation_identity(self) -> None:
        document = json.dumps({"asset": {"version": "2.0"}, "scenes": [{}], "scene": 0}).encode()
        document += b" " * (-len(document) % 4)
        path = self.project / "scene.glb"
        path.write_bytes(struct.pack("<4sII", b"glTF", 2, 20 + len(document)) + struct.pack("<I4s", len(document), b"JSON") + document)
        self.store.import_model(str(path), object_id="motion")
        clip = self.import_clip()
        reference = self.store.add_reference(self.session, "still.png", self.data_url)
        payload = self.payload(clip)
        revision = self.store.scene()["revision"]
        payload["scene_revision"] = revision
        frame = payload["dynamic_frames"][0]
        frame.update(scene_revision=revision, reference_frame_id=None, static_reference_id=reference["id"],
                     animation_clips=[{"object_id": "motion", "name": "Walk", "index": 0}])
        payload["annotations"] = [{"id": "still-mark", "type": "point", "pane": "reference", "reference_image_id": reference["id"],
                                    "frame_id": frame["id"], "time_sec": 0, "clip_id": clip["clip_id"], "scene_revision": revision,
                                    "coordinates": {"x": 0.2, "y": 0.3}}]
        packet = self.store.submit_feedback(self.session, payload)
        saved = packet["dynamic_frames"][0]
        self.assertEqual(saved["reference_original_url"], reference["url"])
        self.assertEqual(saved["static_reference_id"], reference["id"])
        self.assertEqual(saved["animation_clips"], [{"object_id": "motion", "name": "Walk", "index": 0}])
        frame["animation_clips"][0]["name"] = "x" * 161
        with self.assertRaisesRegex(APIError, "clip name"):
            self.store.submit_feedback(self.session, payload)

    def test_video_missing_decoder_and_invalid_input_are_bounded(self) -> None:
        video = self.project / "broken.mp4"
        video.write_bytes(b"not a video")
        with patch("dynamic.shutil.which", return_value=None), self.assertRaisesRegex(APIError, "requires ffmpeg"):
            sample_video(video, 10)
        with self.assertRaisesRegex(APIError, "fully decoded"):
            sample_video(video, 10)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg unavailable")
    def test_real_video_sampling_and_overlong_rejection(self) -> None:
        video = self.project / "motion.mp4"
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=24x24:r=10:d=1", "-pix_fmt", "yuv420p", str(video)], check=True, timeout=15)
        result = self.gateway.set_reference_clip_paths({"video_path": str(video)})["reference_clip"]
        self.assertEqual(result["fps"], 10)
        self.assertEqual(result["source_type"], "video")
        self.assertTrue(result["sampled"])
        self.assertEqual(len(result["frames"]), 10)
        probe = {"format": {"format_name": "mp4", "duration": "61"}, "streams": [{"width": 24, "height": 24}]}
        with patch("dynamic.subprocess.run", return_value=subprocess.CompletedProcess([], 0, json.dumps(probe).encode(), b"")) as run:
            with self.assertRaisesRegex(APIError, "exceed 600"):
                sample_video(video, 10)
            self.assertEqual(run.call_count, 1)


class DynamicHTTPTests(unittest.TestCase):
    def test_clip_routes_capabilities_and_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "project").mkdir()
            (root / "web").mkdir()
            server = make_server(port=0, data_dir=root / "data", web_dir=root / "web", project_dir=root / "project", external_review=True)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            base = f"http://127.0.0.1:{server.server_port}"
            store = server.scene_store
            session = store.workspace()["session_id"]
            data, data_url = image_data()
            try:
                def post(path: str, payload: dict, key: str | None = None) -> dict:
                    headers = {"Content-Type": "application/json"}
                    if key is not None:
                        headers["X-Workspace-Capability" if key == store.browser_token else "X-Scene-Harness-Key"] = key
                    request = urllib.request.Request(base + path, data=json.dumps(payload).encode(), headers=headers, method="POST")
                    with opener.open(request) as response:
                        return json.load(response)
                # Browser writes accept the separately issued browser capability.
                payload = {"fps": 2, "frames": [{"data_url": data_url, "time_sec": 0}]}
                with self.assertRaises(urllib.error.HTTPError) as rejected:
                    post(f"/api/sessions/{session}/clip", payload)
                self.assertEqual(rejected.exception.code, 403)
                rejected.exception.close()
                result = post(f"/api/sessions/{session}/clip", payload, store.browser_token)
                with opener.open(base + "/api/workspace/context") as response:
                    self.assertEqual(json.load(response)["reference_clip"], result["reference_clip"])
                with self.assertRaises(urllib.error.HTTPError) as rejected:
                    post("/api/workspace/clip", {"clear": True}, store.browser_token)
                self.assertEqual(rejected.exception.code, 403)
                rejected.exception.close()
                self.assertIsNone(post("/api/workspace/clip", {"clear": True}, store.control_token)["reference_clip"])
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()

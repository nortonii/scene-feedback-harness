"""Manual WholeBody evidence survives saving, retries and both agent transports."""
from __future__ import annotations

import copy
import hashlib
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1]),
               str(Path(__file__).resolve().parents[2] / "external-skills/capsule-human-tracking/scripts")]
from core import APIError, SceneStore
from gateway import WorkspaceGateway
from human_pose import HumanPoseJobs
from test_human_pose import image_data, result_for
from wholebody_profile import wholebody133_topology
from mcp_server import _visual_tool_result
from server import make_server


class PoseEditTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        project = self.root / "project"; project.mkdir()
        self.store = SceneStore(self.root / "data")
        self.gateway = WorkspaceGateway(self.store, project, external_review=True)
        self.session = self.gateway.ensure()["session_id"]
        _, data_url = image_data()
        reference = self.store.add_reference(self.session, "hand.png", data_url)
        self.jobs = HumanPoseJobs(self.store)
        export = self.jobs.export_sources({"session_id": self.session})
        profile = wholebody133_topology()
        result = result_for(export, names=profile["keypoint_names"], edges=profile["skeleton_edges"], profile=profile["keypoint_profile"])
        result["keypoint_groups"] = profile["keypoint_groups"]
        result["frames"][0]["keypoints"][92]["score"] = .05
        self.jobs.import_result({"job_id": export["job_id"], "result": result})
        self.job = self.jobs.get(export["job_id"])
        frame = self.job["frames"][0]
        self.original_path = Path(self.job["result_json_path"])
        self.original_hash = hashlib.sha256(self.original_path.read_bytes()).hexdigest()
        self.edit = {"id": "e" * 32, "job_id": self.job["job_id"], "reference_id": reference["id"],
                     "image_sha256": frame["image_sha256"], "image_orientation": frame["image_orientation"],
                     "keypoint_profile": profile["keypoint_profile"], "edits": [
                         {"name": profile["keypoint_names"][92], "x": .72, "y": .43, "visibility": "visible"},
                         {"name": profile["keypoint_names"][113], "visibility": "missing"}]}
        self.payload = {"scene_revision": self.store.scene()["revision"], "note": "Fix [[pose_edit:" + "e" * 32 + "]]",
                        "pose_edits": [self.edit], "idempotency_key": "hands-correction-retry"}

    def tearDown(self):
        self.jobs.close(); self.temp.cleanup()

    def test_persistent_correction_preserves_low_model_score_and_original(self):
        feedback = self.store.submit_feedback(self.session, self.payload)
        item = feedback["human_pose_edits"][0]
        self.assertEqual(Path(item["parent_result_path"]), self.original_path)
        self.assertEqual(json.loads(Path(item["source_manifest_path"]).read_text())["job_id"], self.job["job_id"])
        document = json.loads(Path(item["corrections_path"]).read_text())
        self.assertEqual(document, item["document"])
        frame = document["frames"][0]
        self.assertEqual(frame["effective_keypoints"][92]["score"], .05)
        self.assertEqual(frame["effective_keypoints"][92]["manual_visibility"], "visible")
        self.assertEqual(frame["effective_keypoints"][92]["x"], .72)
        self.assertEqual(frame["effective_keypoints"][113]["manual_visibility"], "missing")
        self.assertFalse(frame["effective_keypoints"][113]["in_frame"])
        self.assertEqual(frame["original_keypoints"], self.job["frames"][0]["keypoints"])
        self.assertEqual(hashlib.sha256(self.original_path.read_bytes()).hexdigest(), self.original_hash)
        reloaded = SceneStore(self.store.data_dir)
        self.assertEqual(reloaded.state["feedback"][0]["human_pose_edits"], feedback["human_pose_edits"])

    def test_retry_creates_one_correction_artifact_and_one_feedback(self):
        first = self.store.submit_feedback(self.session, self.payload)
        second = self.store.submit_feedback(self.session, self.payload)
        self.assertEqual(first, second)
        self.assertEqual(len(self.store.state["feedback"]), 1)
        self.assertEqual(len(list((self.store.data_dir / "human_pose/corrections").rglob("*.json"))), 1)

    def test_gateway_delivers_real_original_and_manual_overlay_images(self):
        feedback = self.store.submit_feedback(self.session, self.payload)
        text, paths = self.gateway._turn_input(feedback)
        item = feedback["human_pose_edits"][0]
        self.assertIn(item["corrections_path"], text)
        self.assertIn("manual_visibility=visible", text)
        self.assertIn("parent_evidence_kind", text)
        self.assertEqual(len(paths), 3)  # general reference plus correction original/overlay
        self.assertNotEqual(Path(paths[-1]).read_bytes(), Path(paths[-2]).read_bytes())

    def test_mcp_returns_actual_images_and_source_bound_json(self):
        feedback = self.store.submit_feedback(self.session, self.payload)
        result = _visual_tool_result({"items": [feedback]}, self.store.data_dir)
        images = [item for item in result.content if item.type == "image"]
        self.assertEqual(len(images), 3)
        item = result.structured_content["items"][0]["human_pose_edits"][0]
        self.assertEqual(item["document"]["source_snapshot_id"], self.job["source_snapshot_id"])
        self.assertTrue(Path(item["corrections_path"]).is_file())
        self.assertTrue(Path(item["pose_overlay_path"]).is_file())

    def test_body_face_and_foot_corrections_use_the_existing_feedback_transport(self):
        payload = copy.deepcopy(self.payload)
        payload["pose_edits"][0]["edits"] = [
            {"name": "left_shoulder", "x": .68, "y": .26, "visibility": "visible"},
            {"name": "left_big_toe", "x": .73, "y": .71, "visibility": "visible"},
            {"name": "face-0", "visibility": "missing"},
        ]
        feedback = self.store.submit_feedback(self.session, payload)
        item = feedback["human_pose_edits"][0]
        frame = item["document"]["frames"][0]
        self.assertEqual(frame["original_keypoints"], self.job["frames"][0]["keypoints"])
        effective = {point["name"]: point for point in frame["effective_keypoints"]}
        self.assertEqual((effective["left_shoulder"]["x"], effective["left_big_toe"]["y"]), (.68, .71))
        self.assertFalse(effective["face-0"]["in_frame"])
        text, paths = self.gateway._turn_input(feedback)
        self.assertIn(item["corrections_path"], text)
        self.assertEqual(len(paths), 3)
        self.assertTrue(Path(paths[-1]).read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
        result = _visual_tool_result({"items": [feedback]}, self.store.data_dir)
        self.assertEqual(len([part for part in result.content if part.type == "image"]), 3)
        self.assertEqual(result.structured_content["items"][0]["human_pose_edits"][0]["edits"],
                         payload["pose_edits"][0]["edits"])

    def test_bad_source_or_missing_token_cannot_write_partial_feedback(self):
        for change in (lambda p: p["pose_edits"][0].__setitem__("image_sha256", "0" * 64),
                       lambda p: p.__setitem__("note", "Fix the hands")):
            payload = copy.deepcopy(self.payload); change(payload)
            with self.assertRaises(APIError):
                self.store.submit_feedback(self.session, payload)
            self.assertEqual(self.store.state["feedback"], [])
            self.assertFalse((self.store.data_dir / "human_pose/corrections").exists())

    def test_correction_capability_is_explicit(self):
        self.assertIs(self.gateway.state()["pose_corrections_supported"], True)

    def test_json_download_requires_project_access_and_saved_evidence(self):
        feedback = self.store.submit_feedback(self.session, self.payload)
        item = feedback["human_pose_edits"][0]
        server = make_server(port=0, data_dir=self.store.data_dir, project_dir=self.gateway.project_dir,
                             web_dir=Path(__file__).resolve().parents[2] / "web", external_review=True)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        try:
            connection.request("GET", item["corrections_url"])
            response = connection.getresponse(); response.read()
            self.assertEqual(response.status, 403)
            connection.request("GET", item["corrections_url"], headers={"X-Scene-Harness-Key": self.store.control_token})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read()), item["document"])
            connection.request("GET", "/api/workspace/pose/corrections/" + "f" * 32 + "/" + "e" * 32,
                               headers={"X-Scene-Harness-Key": self.store.control_token})
            response = connection.getresponse(); response.read()
            self.assertEqual(response.status, 404)
        finally:
            connection.close(); server.shutdown(); worker.join(5); server.server_close()


if __name__ == "__main__":
    unittest.main()

"""Source-bound sparse WholeBody hand corrections; no inference or model calls."""
from __future__ import annotations

import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
import uuid

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(Path(__file__).parent)]
from core import APIError
from human_pose import prepare_pose_edit_feedback
import test_human_pose as fixtures

spec = importlib.util.spec_from_file_location("wholebody_edit_profile", ROOT / "external-skills/capsule-human-tracking/scripts/wholebody_profile.py")
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


class PoseEditTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.HumanPoseTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.clip()
        self.export = self.fixture.export()
        topology = profile.wholebody133_topology()
        self.result = fixtures.result_for(self.export, names=topology["keypoint_names"], edges=topology["skeleton_edges"], profile=topology["keypoint_profile"])
        self.result.update(keypoint_groups=topology["keypoint_groups"])
        self.fixture.jobs.import_result({"job_id": self.export["job_id"], "result": self.result})
        self.job = self.fixture.jobs.get(self.export["job_id"])
        self.frame = self.job["frames"][0]
        self.sample = {"id": uuid.uuid4().hex, "job_id": self.job["job_id"], "reference_id": self.frame["reference_id"],
                       "image_sha256": self.frame["image_sha256"], "image_orientation": self.frame["image_orientation"],
                       "keypoint_profile": topology["keypoint_profile"], "edits": [
                           {"name": "left_thumb4", "x": .42, "y": .61, "visibility": "visible"},
                           {"name": "right_forefinger4", "visibility": "occluded"},
                           {"name": "right_pinky_finger4", "visibility": "missing"}]}

    def prepare(self, sample=None):
        sample = copy.deepcopy(sample or self.sample)
        return prepare_pose_edit_feedback(self.fixture.store, self.fixture.session,
            {"pose_edits": [sample], "note": f"Fit the fingers [[pose_edit:{sample['id']}]]"})[0]

    def test_133_profile_sparse_edits_keep_parent_and_actual_overlay(self):
        path = Path(self.job["result_json_path"])
        original = path.read_bytes()
        evidence = self.prepare()
        document = evidence["document"]
        self.assertEqual((document["evidence_kind"], len(document["keypoint_names"])), ("manual_2d", 133))
        self.assertEqual(document["keypoint_groups"]["left_hand"], list(range(91,112)))
        self.assertEqual(document["keypoint_groups"]["right_hand"], list(range(112,133)))
        self.assertEqual(document["parent_job_id"], self.job["job_id"])
        self.assertEqual(document["source_snapshot_id"], self.job["source_snapshot_id"])
        frame = document["frames"][0]
        for key, source in (("ref_id","reference_id"),("time_seconds","time_sec"),("image_sha256","image_sha256"),("view_id","view_id"),("camera","camera")):
            self.assertEqual(frame[key], self.frame[source])
        self.assertEqual(frame["original_keypoints"], self.frame["keypoints"])
        corrected = next(point for point in frame["effective_keypoints"] if point["name"] == "left_thumb4")
        parent = next(point for point in self.frame["keypoints"] if point["name"] == "left_thumb4")
        self.assertEqual((corrected["x"], corrected["y"], corrected["score"]), (.42,.61,parent["score"]))
        self.assertEqual(corrected["manual_source"], "manual_2d")
        for name in ("right_forefinger4", "right_pinky_finger4"):
            point = next(point for point in frame["effective_keypoints"] if point["name"] == name)
            self.assertFalse(point["in_frame"])
            self.assertFalse(point["manual_position"])
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.fixture.jobs.get(self.job["job_id"])["frames"][0], self.frame)
        with Image.open(io.BytesIO(evidence["_overlay_data"])) as image:
            self.assertEqual(image.size, (160,120))

    def test_invalid_joint_coordinate_visibility_name_and_duplicate_rejected(self):
        cases = [
            {"name":"left_thumb4","x":float("nan"),"y":.5,"visibility":"visible"},
            {"name":"left_thumb4","x":True,"y":.5,"visibility":"visible"},
            {"name":"left_thumb4","x":1,"y":.5,"visibility":"visible"},
            {"name":"left_thumb4","x":.5,"y":1,"visibility":"visible"},
            {"name":"left_thumb4","x":.5,"visibility":"visible"},
            {"name":"left_thumb4","visibility":"visible"},
            {"name":"left_thumb4","x":.3,"y":.4,"visibility":"missing"},
            {"name":"left_thumb4","visibility":[]},
            {"name":"invented_finger","x":.3,"y":.4,"visibility":"visible"},
        ]
        for edit in cases:
            with self.subTest(edit=edit), self.assertRaises(APIError):
                sample = copy.deepcopy(self.sample); sample["edits"] = [edit]
                self.prepare(sample)
        sample = copy.deepcopy(self.sample); sample["edits"].append(sample["edits"][0])
        with self.assertRaises(APIError): self.prepare(sample)

    def test_source_hash_profile_frame_and_citation_are_exact(self):
        for key,value in (("image_sha256","0"*64),("image_orientation","raw"),("reference_id",uuid.uuid4().hex),("keypoint_profile","coco17")):
            sample = copy.deepcopy(self.sample); sample[key] = value
            with self.subTest(key=key), self.assertRaises(APIError): self.prepare(sample)
        for note in ("no citation", "[[pose_edit:bad]]", f"[[pose_edit:{uuid.uuid4().hex}]]"):
            with self.subTest(note=note), self.assertRaises(APIError):
                prepare_pose_edit_feedback(self.fixture.store,self.fixture.session,{"pose_edits":[self.sample],"note":note})
        bad = copy.deepcopy(self.sample); bad["id"] = []
        with self.assertRaises(APIError):
            prepare_pose_edit_feedback(self.fixture.store,self.fixture.session,{"pose_edits":[bad],"note":""})

    def test_camera_frame_changes_do_not_rebind_old_corrections(self):
        evidence = self.prepare()
        self.fixture.store.set_reference_clip(self.fixture.session,{"name":"replacement","fps":1,"frames":[
            {"name":"different.png","time_sec":0,"data_url":self.fixture.data_url}]})
        self.assertEqual(self.prepare()["document"],evidence["document"])
        image = self.fixture.store.media_dir / self.frame["reference_url"].rsplit("/",1)[1]
        image.write_bytes(b"changed")
        with self.assertRaisesRegex(APIError,"original source image changed"): self.prepare()

    def test_manual_metadata_survives_result_import_without_fake_scores_or_bbox(self):
        export = self.fixture.export()
        topology = profile.wholebody133_topology()
        result = fixtures.result_for(export,names=topology["keypoint_names"],edges=topology["skeleton_edges"],profile=topology["keypoint_profile"])
        result["keypoint_groups"] = topology["keypoint_groups"]
        for frame in result["frames"]:
            frame.update(bbox=None,tracking_status="lost")
            for point in frame["keypoints"]: point.update(score=0,in_frame=False)
        point = result["frames"][0]["keypoints"][91]
        point.update(x=.3,y=.4,manual_visibility="visible",manual_source="manual_2d",manual_position=True,in_frame=True)
        self.fixture.jobs.import_result({"job_id":export["job_id"],"result":result})
        imported = self.fixture.jobs.get(export["job_id"])["frames"][0]
        self.assertIsNone(imported["bbox"])
        self.assertEqual(imported["tracking_status"],"lost")
        self.assertEqual(imported["keypoints"][91],point)
        self.assertEqual(sum(joint["in_frame"] for joint in imported["keypoints"]),1)

    def test_inconsistent_imported_manual_metadata_and_groups_reject(self):
        for key,value in (("manual_source","not_manual"),("manual_position",False),("x",1)):
            export = self.fixture.export()
            topology = profile.wholebody133_topology()
            result = fixtures.result_for(export,names=topology["keypoint_names"],edges=topology["skeleton_edges"],profile=topology["keypoint_profile"])
            point=result["frames"][0]["keypoints"][91]
            point.update(manual_visibility="visible",manual_source="manual_2d",manual_position=True)
            point[key]=value
            with self.subTest(key=key),self.assertRaises(APIError):
                self.fixture.jobs.import_result({"job_id":export["job_id"],"result":result})
        for indices in ([91,91],[133],[],[True]):
            export=self.fixture.export(); result=fixtures.result_for(export,names=topology["keypoint_names"],edges=topology["skeleton_edges"],profile=topology["keypoint_profile"])
            result["keypoint_groups"]={"left_hand":indices}
            with self.subTest(indices=indices),self.assertRaises(APIError):
                self.fixture.jobs.import_result({"job_id":export["job_id"],"result":result})


if __name__ == "__main__": unittest.main()

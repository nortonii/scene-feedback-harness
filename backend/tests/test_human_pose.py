"""Passive pose evidence exchange: exact sources, profile and project isolation."""
from __future__ import annotations

import base64
import copy
import http.client
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import APIError, SceneStore
from gateway import WorkspaceGateway
from human_pose import HumanPoseJobs, JOINT_NAMES, SKELETON_EDGES, prepare_pose_feedback, _atomic_json, _store_frame_archive
import human_pose
import mcp_server
import server as server_module


def image_data(color="navy", size=(160, 120)):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, "PNG")
    data = output.getvalue()
    return data, "data:image/png;base64," + base64.b64encode(data).decode()


def camera():
    return {"camera_to_world": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 3], [0, 0, 0, 1]],
            "intrinsics": {"width": 160, "height": 120, "fx": 150, "fy": 150, "cx": 80, "cy": 60}}


def result_for(export, *, evidence_kind="observed_2d", names=None, edges=None, profile=None):
    manifest = export["manifest"]
    result = {key: copy.deepcopy(manifest[key]) for key in
              ("schema_version", "job_id", "track_id", "project_id", "session_id", "source_snapshot_id")}
    result.update({key: copy.deepcopy(manifest[key]) for key in ("view_id", "view_ids") if key in manifest})
    names = JOINT_NAMES if names is None else names
    result.update(evidence_kind=evidence_kind, model={"name": "external fixture"}, provenance={"method": "offline fixture"})
    if profile is not None:
        result.update(keypoint_profile=profile, keypoint_names=names, skeleton_edges=edges)
    result["frames"] = [{**{key: source[key] for key in ("ref_id", "view_id", "width", "height", "frame_index", "time_seconds", "image_sha256", "image_orientation")},
                         "keypoints": [{"name": name, "x": .2 + (index % 10) * .03, "y": .3,
                                        "score": .9, "in_frame": True} for index, name in enumerate(names)],
                         "bbox": [.1, .1, .6, .7], "tracking_status": "tracked"} for source in manifest["frames"]]
    return result


class HumanPoseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"; self.project.mkdir()
        self.store = SceneStore(self.root / "data")
        self.gateway = WorkspaceGateway(self.store, self.project, external_review=True)
        self.session = self.gateway.ensure()["session_id"]
        self.data, self.data_url = image_data()
        self.reference = self.store.add_reference(self.session, "person.png", self.data_url)
        self.store.set_reference_cameras(self.session, [{"reference_id": self.reference["id"], "camera": camera()}])
        self.jobs = HumanPoseJobs(self.store)

    def tearDown(self):
        self.jobs.close(); self.temporary.cleanup()

    def clip(self):
        first = self.store.set_reference_clip(self.session, {"name": "camera A", "fps": 2, "frames": [
            {"name": f"A{i}.png", "data_url": self.data_url, "time_sec": time, "camera": camera()}
            for i, time in enumerate((0, .5, 1, 1.5))]})["reference_clip"]
        _, other = image_data("blue")
        bundle = self.store.set_reference_clip(self.session, {"append_view": True, "name": "camera B", "fps": 3,
            "frames": [{"name": f"B{i}.png", "data_url": other, "time_sec": time, "camera": camera()}
                       for i, time in enumerate((0, .34, .8, 1.2, 1.6))]})["reference_clip"]
        return first, bundle["views"][0]

    def export(self):
        return self.jobs.export_sources({"session_id": self.session})

    def complete(self, export=None, **options):
        export = export or self.export()
        result = result_for(export, **options)
        self.jobs.import_result({"job_id": export["job_id"], "result": result})
        return self.jobs.get(export["job_id"]), result

    def test_exports_bound_full_sources_without_worker_and_retry_is_stable(self):
        request = {"session_id": self.session, "request_id": uuid.uuid4().hex}
        with patch("subprocess.Popen", side_effect=AssertionError("passive exchange cannot launch inference")):
            export = self.jobs.export_sources(request)
            self.assertEqual(self.jobs.export_sources(request), export)
        self.assertEqual(export["status"], "awaiting_import")
        self.assertEqual(export["manifest"]["session_id"], self.session)
        self.assertEqual(len(export["source_snapshot_id"]), 64)
        frame = export["manifest"]["frames"][0]
        self.assertEqual((frame["ref_id"], frame["width"], frame["height"]), (self.reference["id"], 160, 120))
        self.assertEqual(frame["camera"]["intrinsics"]["fx"], 150)
        self.assertTrue(Path(frame["image_path"]).is_file())
        self.assertFalse(self.jobs.list(self.session)["inference_supported"])
        self.assertFalse(hasattr(human_pose, "subprocess"))
        self.assertFalse(hasattr(self.jobs, "start"))
        self.assertFalse(hasattr(self.jobs, "cancel"))

    def test_import_persists_exact_source_and_immutable_feedback_overlay(self):
        job, result = self.complete()
        self.assertEqual((job["status"], job["completed_frames"]), ("completed", 1))
        self.assertEqual(job["keypoint_names"], JOINT_NAMES)
        self.assertEqual(job["frames"][0]["camera"]["intrinsics"]["fx"], 150)
        self.assertEqual(json.loads(Path(job["result_json_path"]).read_text()), result)
        ref = {"job_id": job["job_id"], "reference_id": self.reference["id"]}
        evidence = prepare_pose_feedback(self.store, self.session, [ref], f"[[pose:{job['job_id']}:{self.reference['id']}]]")
        self.assertEqual(evidence[0]["source"], "external_pose_estimate")
        with Image.open(io.BytesIO(evidence[0]["_overlay_data"])) as overlay:
            self.assertEqual(overlay.size, (160, 120))
        self.store.set_reference_clip(self.session, {"name": "replacement", "fps": 1,
            "frames": [{"name": "new.png", "data_url": self.data_url, "time_sec": 0}]})
        self.assertEqual(prepare_pose_feedback(self.store, self.session, [ref])[0]["frame"], evidence[0]["frame"])

    def test_all_views_all_frames_and_pagination_preserve_camera_identity(self):
        first, second = self.clip()
        job, _ = self.complete()
        self.assertEqual((job["total_frames"], job["total_frame_count"]), (9, 9))
        page = self.jobs.get(job["job_id"], view_id=second["clip_id"], max_frames=2)
        self.assertEqual([frame["time_sec"] for frame in page["frames"]], [0, .34])
        self.assertEqual(page["next_frame_offset"], 2)
        self.assertEqual(page["result_frame_count"], 5)
        rest = self.jobs.get(job["job_id"], view_id=second["clip_id"], frame_offset=2, max_frames=32)
        self.assertEqual([frame["time_sec"] for frame in rest["frames"]], [.8, 1.2, 1.6])
        self.assertTrue(all(frame["view_id"] == second["clip_id"] for frame in rest["frames"]))
        with self.assertRaises(APIError):
            self.jobs.get(job["job_id"], reference_id=rest["frames"][0]["reference_id"], view_id=first["clip_id"])

    def test_geometry_revision_can_change_before_external_result_arrives(self):
        export = self.export()
        revision = self.store.scene()["revision"]
        self.store.replace_scene(revision, [{"id": "box", "name": "Box", "type": "box", "position": [0, 0, 0], "size": [1, 1, 1], "rotation": [0, 0, 0], "color": "#112233"}])
        self.assertEqual(self.complete(export)[0]["status"], "completed")

    def test_replaced_clip_or_modified_original_hash_rejects_stale_import(self):
        for replacement in ("new_clip", "file_changed"):
            with self.subTest(replacement=replacement):
                export = self.export()
                if replacement == "new_clip":
                    self.store.set_reference_clip(self.session, {"name": "new", "fps": 1,
                        "frames": [{"name": "new.png", "data_url": self.data_url, "time_sec": 0}]})
                else:
                    path = Path(export["manifest"]["frames"][0]["image_path"])
                    path.write_bytes(image_data("red")[0])
                with self.assertRaisesRegex(APIError, "sources changed"):
                    self.jobs.import_result({"job_id": export["job_id"], "result": result_for(export)})
                self.assertEqual(self.jobs.get(export["job_id"])["status"], "awaiting_import")

    def test_foreign_or_unbound_results_never_persist(self):
        export = self.export()
        for key in ("job_id", "track_id", "project_id", "session_id", "source_snapshot_id", "view_id"):
            with self.subTest(key=key):
                result = result_for(export); result[key] = "foreign"
                with self.assertRaises(APIError):
                    self.jobs.import_result({"job_id": export["job_id"], "result": result})
        self.assertFalse((self.jobs.directory / export["job_id"] / "output.json").exists())

    def test_frame_identity_dimensions_order_and_count_are_exact(self):
        self.clip(); export = self.export()
        for key in ("ref_id", "view_id", "width", "height", "frame_index", "time_seconds", "image_sha256", "image_orientation"):
            result = result_for(export)
            result["frames"][0][key] = "wrong" if key.endswith("id") else 99
            with self.subTest(key=key), self.assertRaises(APIError):
                self.jobs.import_result({"job_id": export["job_id"], "result": result})
        for change in ("reverse", "missing"):
            result = result_for(export)
            if change == "reverse": result["frames"].reverse()
            else: result["frames"].pop()
            with self.subTest(change=change), self.assertRaises(APIError):
                self.jobs.import_result({"job_id": export["job_id"], "result": result})

    def test_numeric_and_joint_visibility_validation_has_no_coordinate_guesses(self):
        export = self.export()
        for key, value in (("x", -1), ("y", float("nan")), ("score", True), ("in_frame", 1), ("name", "wrong")):
            result = result_for(export); result["frames"][0]["keypoints"][0][key] = value
            with self.subTest(key=key), self.assertRaises(APIError):
                self.jobs.import_result({"job_id": export["job_id"], "result": result})
        result = result_for(export); result.pop("evidence_kind")
        with self.assertRaises(APIError):
            self.jobs.import_result({"job_id": export["job_id"], "result": result})

    def test_custom_capsule_projection_preserves_profile_edges_and_evidence_label(self):
        names = ["pelvis", "left_knee", "left_ankle"]
        job, _ = self.complete(evidence_kind="projected_3d", names=names, edges=[[0, 1], [1, 2]], profile="capsule-joints")
        self.assertEqual(job["keypoint_names"], names)
        ref = {"job_id": job["job_id"], "reference_id": self.reference["id"]}
        evidence = prepare_pose_feedback(self.store, self.session, [ref])[0]
        self.assertEqual((evidence["source"], evidence["evidence_kind"]), ("projected_3d_geometry", "projected_3d"))
        self.assertEqual(evidence["skeleton_edges"], [[0, 1], [1, 2]])
        self.assertEqual(len(evidence["frame"]["keypoints"]), 3)
        self.assertTrue(evidence["_overlay_data"].startswith(b"\x89PNG"))

    def test_custom_joint_profile_rejects_duplicate_names_invalid_edges_and_fake_coco(self):
        export = self.export()
        for names, edges, profile in ((["pelvis", "pelvis"], [], "capsule"), (["pelvis"], [[0, 1]], "capsule"),
                                      (["pelvis"], [], "coco17"), (["pelvis"], [[0, 0]], "capsule")):
            result = result_for(export, names=names, edges=edges, profile=profile)
            with self.subTest(names=names, edges=edges, profile=profile), self.assertRaises(APIError):
                self.jobs.import_result({"job_id": export["job_id"], "result": result})

    def test_lost_person_has_no_fabricated_visible_joints(self):
        export = self.export(); result = result_for(export)
        result["frames"][0].update(bbox=None, tracking_status="lost")
        with self.assertRaises(APIError):
            self.jobs.import_result({"job_id": export["job_id"], "result": result})
        for point in result["frames"][0]["keypoints"]:
            point.update(score=0, in_frame=False)
        self.assertEqual(self.jobs.import_result({"job_id": export["job_id"], "result": result})["status"], "completed")

    def test_completed_import_retry_is_idempotent_but_different_result_conflicts(self):
        job, result = self.complete()
        self.assertEqual(self.jobs.import_result({"job_id": job["job_id"], "result": result})["status"], "completed")
        result["frames"][0]["keypoints"][0]["x"] = .6
        with self.assertRaisesRegex(APIError, "different completed result"):
            self.jobs.import_result({"job_id": job["job_id"], "result": result})
        self.jobs.close(); self.jobs = HumanPoseJobs(SceneStore(self.store.data_dir))
        self.assertEqual(self.jobs.get(job["job_id"])["status"], "completed")

    def test_external_download_roundtrip_preserves_original_import_digest(self):
        job, result = self.complete()
        download = self.jobs.download(job["job_id"])
        self.assertEqual(download, result)
        self.assertEqual(self.jobs.import_result({"job_id": job["job_id"], "result": download})["status"], "completed")

    def test_explicit_calibrated_tracking_metadata_is_preserved_and_not_verified_by_workbench(self):
        export = self.export(); result = result_for(export)
        result["tracking"] = {"scope": "calibrated_cross_view_actor", "identity_guaranteed": True,
                              "cross_view_identity_source": "user_verified_actor_binding", "actor_id": "subject-1"}
        self.jobs.import_result({"job_id": export["job_id"], "result": result})
        tracking = self.jobs.get(export["job_id"])["tracking"]
        self.assertEqual(tracking["scope"], "calibrated_cross_view_actor")
        self.assertTrue(tracking["identity_guaranteed"])
        self.assertFalse(tracking["workbench_identity_verified"])

    def test_project_local_result_file_import_rejects_outside_path(self):
        export = self.export(); result = result_for(export)
        path = self.project / "external_pose.json"; path.write_text(json.dumps(result))
        self.assertEqual(self.jobs.import_result({"job_id": export["job_id"], "result_path": str(path)})["status"], "completed")
        export = self.export(); other = self.root / "foreign.json"; other.write_text(json.dumps(result_for(export)))
        with self.assertRaisesRegex(APIError, "within this project"):
            self.jobs.import_result({"job_id": export["job_id"], "result_path": str(other)})

    def test_historical_completed_inline_and_archived_results_survive_migration(self):
        export = self.export(); job, _ = self.complete(export)
        directory = self.jobs.directory / job["job_id"]
        stored = json.loads((directory / "job.json").read_text())
        for key in ("external_results", "imported_external", "keypoint_profile", "keypoint_names", "skeleton_edges", "evidence_kind"):
            stored.pop(key, None)
        stored.update(automatic=False, frames=job["frames"])
        _atomic_json(directory / "job.json", stored)
        self.jobs.close(); self.jobs = HumanPoseJobs(SceneStore(self.store.data_dir))
        self.assertEqual(self.jobs.get(job["job_id"])["keypoint_names"], JOINT_NAMES)
        ref = {"job_id": job["job_id"], "reference_id": self.reference["id"]}
        self.assertEqual(prepare_pose_feedback(self.store, self.session, [ref])[0]["source"], "vitpose_estimate")
        stored.update(automatic=True); stored.pop("frames")
        _atomic_json(directory / "job.json", stored)
        self.assertEqual(self.jobs.get(job["job_id"])["frames"], job["frames"])

    def test_old_running_job_marks_interrupted_without_relaunch(self):
        export = self.export(); path = self.jobs.directory / export["job_id"] / "job.json"
        stored = json.loads(path.read_text()); stored.update(status="running"); _atomic_json(path, stored)
        self.jobs.close(); self.jobs = HumanPoseJobs(SceneStore(self.store.data_dir))
        self.assertEqual(self.jobs.get(export["job_id"])["status"], "interrupted")


class HumanPoseHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.root = Path(self.temporary.name)
        self.project = self.root / "project"; self.project.mkdir()
        self.web = self.root / "web"; self.web.mkdir(); (self.web / "index.html").write_text("viewer")
        self.start_patch = patch.object(WorkspaceGateway, "start", lambda gateway: gateway.ensure()); self.start_patch.start()
        def create_target(gateway, _model, **_options):
            thread_id = str(uuid.uuid4()); gateway.store.workspace_thread(thread_id); return {"thread_id": thread_id}
        self.create_patch = patch.object(WorkspaceGateway, "create_target", create_target); self.create_patch.start()
        self.server = server_module.make_server(port=0, data_dir=self.root / "data", project_dir=self.project,
                                               web_dir=self.web, external_review=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.context = self.server.project_registry.root
        self.session = self.context.store.workspace()["session_id"]
        _, data_url = image_data(); self.reference = self.context.store.add_reference(self.session, "person.png", data_url)

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=3)
        self.create_patch.stop(); self.start_patch.stop(); self.temporary.cleanup()

    def request(self, method, path, body=None, capability=None, control=None):
        headers = {}
        if capability is not None: headers["X-Workspace-Capability"] = capability
        if control is not None: headers["X-Scene-Harness-Key"] = control
        if body is not None: body = json.dumps(body).encode(); headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        connection.request(method, path, body, headers); response = connection.getresponse(); data = response.read()
        contents = json.loads(data) if response.getheader("Content-Type", "").startswith("application/json") else data
        status = response.status; connection.close(); return status, contents

    def test_import_endpoints_permissions_and_removed_run_cancel_routes(self):
        self.assertEqual(self.request("POST", "/api/workspace/pose/sources", {})[0], 403)
        self.assertEqual(self.request("POST", "/api/workspace/pose/sources", {}, capability=self.context.store.browser_token)[0], 403)
        code, export = self.request("POST", "/api/workspace/pose/sources", {}, control=self.context.store.control_token)
        self.assertEqual(code, 201)
        payload = {"job_id": export["job_id"], "result": result_for(export)}
        self.assertEqual(self.request("POST", "/api/workspace/pose/import", payload)[0], 403)
        self.assertEqual(self.request("POST", "/api/workspace/pose/import", payload, capability=self.context.store.browser_token)[0], 200)
        self.assertEqual(self.request("GET", "/api/workspace/pose/" + export["job_id"])[1]["status"], "completed")
        self.assertEqual(self.request("GET", "/api/workspace/pose/" + export["job_id"] + "?download=1")[1], payload["result"])
        self.assertEqual(self.request("POST", "/api/workspace/pose", {}, control=self.context.store.control_token)[0], 404)
        self.assertEqual(self.request("POST", "/api/workspace/pose/" + export["job_id"] + "/cancel", {}, control=self.context.store.control_token)[0], 404)

    def test_browser_cannot_import_local_path_or_read_other_project_job(self):
        _, export = self.request("POST", "/api/workspace/pose/sources", {}, control=self.context.store.control_token)
        path = self.project / "pose.json"; path.write_text(json.dumps(result_for(export)))
        self.assertEqual(self.request("POST", "/api/workspace/pose/import", {"job_id": export["job_id"], "result_path": str(path)},
                                      capability=self.context.store.browser_token)[0], 403)
        code, created = self.request("POST", "/api/projects", {"name": "other", "model": "gpt-6-astra", "request_id": uuid.uuid4().hex},
                                     capability=self.context.store.browser_token)
        self.assertEqual(code, 201)
        child = self.server.project_registry.get(created["project"]["project_id"]); prefix = "/p/" + child.project_id
        self.assertEqual(self.request("GET", prefix + "/api/workspace/pose/" + export["job_id"])[0], 404)
        payload = {"job_id": export["job_id"], "result": result_for(export)}
        self.assertEqual(self.request("POST", prefix + "/api/workspace/pose/import", payload, capability=child.store.browser_token)[0], 404)
        self.assertEqual(self.request("POST", prefix + "/api/workspace/pose/import", payload, capability=self.context.store.browser_token)[0], 403)


if __name__ == "__main__":
    unittest.main()

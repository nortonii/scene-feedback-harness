"""Human pose jobs preserve frame evidence, worker failures and project isolation."""

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
import time
import unittest
from unittest.mock import patch
import uuid

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import APIError, SceneStore
from gateway import WorkspaceGateway
from human_pose import HumanPoseJobs, JOINT_NAMES, prepare_pose_feedback
import mcp_server
import server as server_module


WORKER = r'''
import argparse,json,sys,time
from pathlib import Path
from PIL import Image
p=argparse.ArgumentParser()
p.add_argument("--manifest");p.add_argument("--output");p.add_argument("--mode",default="normal")
a=p.parse_args();manifest=json.loads(Path(a.manifest).read_text())
names=["nose","left_eye","right_eye","left_ear","right_ear","left_shoulder","right_shoulder",
"left_elbow","right_elbow","left_wrist","right_wrist","left_hip","right_hip","left_knee",
"right_knee","left_ankle","right_ankle"]
if a.mode=="fail":
 print("controlled worker failure",file=sys.stderr,flush=True);sys.exit(7)
frames=[]
for index,source in enumerate(manifest["frames"]):
 with Image.open(source["image_path"]) as image:width,height=image.size
 keypoints=[{"name":name,"x":.2+i*.03,"y":.25+(i%7)*.06,"score":.9,"in_frame":True} for i,name in enumerate(names)]
 keypoints[0]["raw_score"]=1.25
 if a.mode=="invalid":keypoints[0]["name"]="wrong_joint"
 if a.mode=="nan":keypoints[0]["x"]=float("nan")
 frame={"ref_id":source["ref_id"],"width":width,"height":height,"keypoints":keypoints,
 "bbox":[.12,.12,.55,.7],"tracking_status":"lost" if a.mode=="lost" else "tracked"}
 frames.append(frame)
 for bad in ("not-json",json.dumps({"completed_frames":-1}),json.dumps({"completed_frames":9999}),json.dumps({"completed_frames":True})):
  print(bad,flush=True)
 print(json.dumps({"completed_frames":index+1}),flush=True)
 if a.mode=="hold":time.sleep(3600)
 if a.mode=="slow":time.sleep(.15)
Path(a.output).write_text(json.dumps({"job_id":manifest["job_id"],"track_id":manifest["track_id"],
 "model":{"name":"controlled ViTPose transport fixture","keypoint_format":"coco17"},"frames":frames}))
'''


def image_data(color="navy", size=(160, 120)):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, "PNG")
    data = output.getvalue()
    return data, "data:image/png;base64," + base64.b64encode(data).decode()


def camera():
    return {"camera_to_world": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 3], [0, 0, 0, 1]],
            "intrinsics": {"width": 160, "height": 120, "fx": 150, "fy": 150, "cx": 80, "cy": 60}}


class HumanPoseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.store = SceneStore(self.root / "data")
        self.gateway = WorkspaceGateway(self.store, self.project, external_review=True)
        self.session = self.gateway.ensure()["session_id"]
        self.data, self.data_url = image_data()
        self.reference = self.store.add_reference(self.session, "person.png", self.data_url)
        self.store.set_reference_cameras(self.session, [{"reference_id": self.reference["id"], "camera": camera()}])
        self.worker = self.root / "worker.py"
        self.worker.write_text(WORKER)
        self.runner_patch = patch.dict("os.environ", {"SCENE_FEEDBACK_POSE_RUNNER": self.runner("normal")})
        self.runner_patch.start()
        self.jobs = HumanPoseJobs(self.store)

    def tearDown(self):
        self.jobs.close()
        self.runner_patch.stop()
        self.temporary.cleanup()

    def runner(self, mode):
        return json.dumps([sys.executable, str(self.worker), "--manifest", "{manifest}", "--output", "{output}", "--mode", mode])

    def request(self, **changes):
        return {"session_id": self.session, "request_id": uuid.uuid4().hex,
                "reference_id": self.reference["id"], "bbox": [.1, .15, .6, .7], **changes}

    def wait(self, job_id, *, status="completed", timeout=6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.jobs.get(job_id)
            if job["status"] == status:
                return job
            if job["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                self.fail(f"expected {status}, got {job['status']}: {job.get('error')}")
            time.sleep(.02)
        self.fail(f"human pose job did not reach {status}")

    def start(self, payload=None, mode="normal"):
        with patch.dict("os.environ", {"SCENE_FEEDBACK_POSE_RUNNER": self.runner(mode)}):
            created = self.jobs.start(payload or self.request())
            # The worker reads its command asynchronously; keep the selected env
            # until launch/output completes rather than racing patch teardown.
            if mode == "hold":
                deadline = time.monotonic() + 6
                while time.monotonic() < deadline:
                    current = self.jobs.get(created["job_id"])
                    if current["completed_frames"] == 1:
                        return current
                    if current["status"] == "failed":
                        self.fail(current.get("error"))
                    time.sleep(.02)
                self.fail("controlled worker did not report its first frame")
            return self.wait(created["job_id"], status="failed" if mode in {"fail", "invalid", "nan"} else "completed")

    def clip(self):
        primary = self.store.set_reference_clip(self.session, {"name": "camera A", "fps": 2, "frames": [
            {"name": f"A{i}.png", "data_url": self.data_url, "time_sec": t, "camera": camera()}
            for i, t in enumerate((0, .5, 1, 1.5))]})["reference_clip"]
        _, blue = image_data("blue")
        bundle = self.store.set_reference_clip(self.session, {"append_view": True, "name": "camera B", "fps": 3,
            "frames": [{"name": f"B{i}.png", "data_url": blue, "time_sec": t, "camera": camera()}
                       for i, t in enumerate((0, .34, .8, 1.2, 1.6))]})["reference_clip"]
        return primary, bundle["views"][0]

    def test_current_image_subprocess_preserves_dimensions_camera_and_original(self):
        payload = self.request()
        before = copy.deepcopy(payload)
        job = self.start(payload)
        self.assertEqual(payload, before)
        self.assertEqual((job["status"], job["completed_frames"], job["total_frames"]), ("completed", 1, 1))
        frame = job["frames"][0]
        self.assertEqual((frame["reference_id"], frame["width"], frame["height"]), (self.reference["id"], 160, 120))
        self.assertEqual(frame["camera"], camera())
        self.assertEqual([p["name"] for p in frame["keypoints"]], JOINT_NAMES)
        self.assertEqual(frame["keypoints"][0]["raw_score"], 1.25)
        self.assertEqual(frame["bbox"], [.12, .12, .55, .7])
        manifest = json.loads((self.jobs.directory / job["job_id"] / "input.json").read_text())
        self.assertEqual(manifest["frames"][0]["bbox_xywh"], [16, 18, 96, 84])
        self.assertEqual(Path(manifest["frames"][0]["image_path"]).read_bytes(), self.data)
        self.assertNotIn("frames", self.jobs.list(self.session)["jobs"][0])

    def test_invalid_roi_and_source_requests_create_no_jobs(self):
        invalid = [None, {}, [], [0, 0, 0, .5], [0, 0, .5, .001], [-.1, 0, .5, .5],
                   [.8, .2, .3, .5], [True, 0, .5, .5], [float("nan"), 0, .5, .5],
                   [0, 0, float("inf"), .5]]
        for bbox in invalid:
            with self.subTest(bbox=bbox), self.assertRaises(APIError):
                self.jobs.start(self.request(bbox=bbox))
        invalid_payloads = [self.request(reference_id="missing"), self.request(reference_id=None),
                            self.request(view_id="missing"), self.request(start_time_sec=0),
                            self.request(request_id="not-an-id"), self.request(sample_fps=0),
                            self.request(confidence_threshold=2)]
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(APIError):
                self.jobs.start(payload)
        self.assertEqual(self.jobs.list(self.session)["jobs"], [])

    def test_request_id_is_durable_and_never_restarts_a_completed_worker(self):
        payload = self.request()
        first = self.start(payload)
        self.assertEqual(self.jobs.start(payload)["job_id"], first["job_id"])
        with self.assertRaisesRegex(APIError, "different request"):
            self.jobs.start(dict(payload, bbox=[.2, .2, .3, .3]))
        self.jobs.close()
        self.jobs = HumanPoseJobs(SceneStore(self.store.data_dir))
        with patch("human_pose.runtime_status", return_value={"configured": False, "message": "offline"}):
            self.assertEqual(self.jobs.start(payload)["job_id"], first["job_id"])
        self.assertEqual(len(self.jobs.list(self.session)["jobs"]), 1)

    def test_current_camera_clip_sampling_and_replacement_keep_original_provenance(self):
        primary, second = self.clip()
        payload = self.request(reference_id=None, view_id=second["clip_id"], sample_fps=2,
                               start_time_sec=.3, end_time_sec=1.6)
        job = self.start(payload)
        self.assertEqual([f["time_sec"] for f in job["frames"]], [.34, 1.2])
        self.assertEqual([f["frame_index"] for f in job["frames"]], [1, 3])
        self.assertTrue(all(f["view_id"] == second["clip_id"] and f["view_name"] == "camera B"
                            and f["clip_id"] == primary["clip_id"] for f in job["frames"]))
        refs = [{"job_id": job["job_id"], "reference_id": f["reference_id"]} for f in job["frames"]]
        original = copy.deepcopy(job["frames"])
        self.store.set_reference_clip(self.session, {"frames": [{"data_url": self.data_url}]})
        self.assertEqual(self.jobs.get(job["job_id"])["frames"], original)
        prepared = prepare_pose_feedback(self.store, self.session, refs)
        self.assertEqual([p["frame"] for p in prepared], original)
        with self.assertRaisesRegex(APIError, "view_id"):
            self.jobs.start(self.request(reference_id=None, view_id=second["clip_id"]))

    def test_progress_cancel_and_restart_are_durable_and_never_completed(self):
        primary, _ = self.clip()
        job = self.start(self.request(reference_id=None, view_id=primary["clip_id"]), "hold")
        self.assertEqual((job["status"], job["completed_frames"], job["total_frames"]), ("running", 1, 4))
        with self.assertRaisesRegex(APIError, "active"):
            self.jobs.start(self.request())
        cancelled = self.jobs.cancel(job["job_id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.jobs.threads[job["job_id"]].join(timeout=3)
        self.assertEqual(self.jobs.get(job["job_id"])["status"], "cancelled")
        self.assertEqual(self.jobs.cancel(job["job_id"])["status"], "cancelled")
        with self.assertRaisesRegex(APIError, "not completed"):
            prepare_pose_feedback(self.store, self.session, [{"job_id": job["job_id"], "reference_id": primary["frames"][0]["id"]}])
        second = self.start(self.request(), "hold")
        self.jobs.close()
        self.jobs = HumanPoseJobs(self.store)
        self.assertEqual(self.jobs.get(second["job_id"])["status"], "interrupted")
        self.assertEqual(self.jobs.get(job["job_id"])["status"], "cancelled")
        # Crash-left durable states are marked interrupted even without close().
        path = self.jobs.directory / second["job_id"] / "job.json"
        document = json.loads(path.read_text()); document["status"] = "queued"; path.write_text(json.dumps(document))
        self.jobs.close(); self.jobs = HumanPoseJobs(self.store)
        self.assertEqual(self.jobs.get(second["job_id"])["status"], "interrupted")

    def test_worker_failures_and_bad_outputs_are_durable_and_recoverable(self):
        for mode, expected in (("fail", "exited 7"), ("invalid", "joint names"), ("nan", "non-finite")):
            with self.subTest(mode=mode):
                job = self.start(mode=mode)
                self.assertEqual(job["status"], "failed")
                self.assertIn(expected, job["error"])
                self.assertEqual(job["frames"], [])
        completed = self.start()
        self.assertEqual(completed["status"], "completed")

    def test_feedback_uses_stored_keypoints_and_original_plus_overlay_for_gateway_and_mcp(self):
        job = self.start()
        payload = {"scene_revision": 1, "note": "Follow this [[pose:"+job["job_id"]+":"+self.reference["id"]+"]].",
                   "pose_refs": [{"job_id": job["job_id"], "reference_id": self.reference["id"],
                                  "keypoints": [{"name": "fake", "x": 999}]}]}
        original = copy.deepcopy(payload)
        packet = self.store.submit_feedback(self.session, payload)
        self.assertEqual(payload, original)
        estimate = packet["human_pose"][0]
        self.assertEqual(estimate["frame"]["keypoints"], job["frames"][0]["keypoints"])
        self.assertEqual(estimate["source"], "vitpose_estimate")
        self.assertEqual(estimate["coordinate_frame"], "reference_image_normalized")
        self.assertEqual(estimate["reference_original_url"], self.reference["url"])
        overlay = self.store.media_dir / estimate["pose_overlay_url"].rsplit("/", 1)[-1]
        with Image.open(overlay) as image:
            self.assertEqual(image.size, (160, 120))
            self.assertEqual(image.getpixel((32, 30)), (0, 183, 176))
        self.assertEqual((self.store.media_dir / self.reference["url"].rsplit("/", 1)[-1]).read_bytes(), self.data)
        text, paths = self.gateway._turn_input(packet)
        self.assertIn(job["track_id"], text)
        self.assertIn("vitpose", text.lower())
        self.assertIn(str(overlay), paths)
        self.assertIn(str(self.store.media_dir / self.reference["url"].rsplit("/", 1)[-1]), paths)
        with patch.object(mcp_server, "DATA_DIR", self.store.data_dir):
            result = mcp_server._visual_tool_result({"items": [copy.deepcopy(packet)]})
        self.assertEqual(result.structured_content["items"][0]["human_pose"][0]["frame"]["keypoints"], estimate["frame"]["keypoints"])
        self.assertGreaterEqual(sum(block.type == "image" for block in result.content), 2)
        self.assertEqual(SceneStore(self.store.data_dir).feedback_by_id(packet["feedback_id"]), packet)

    def test_feedback_rejects_other_sessions_unknown_frames_duplicates_and_limit(self):
        job = self.start()
        ref = {"job_id": job["job_id"], "reference_id": self.reference["id"]}
        other = self.store.create_session()["session_id"]
        invalid = [(other, [ref]), (self.session, [dict(ref, reference_id="missing")]),
                   (self.session, [ref, ref]), (self.session, [ref] * 9),
                   (self.session, [{"job_id": uuid.uuid4().hex, "reference_id": self.reference["id"]}])]
        for session, refs in invalid:
            with self.subTest(refs=refs), self.assertRaises(APIError):
                prepare_pose_feedback(self.store, session, refs)
        other_store = SceneStore(self.root / "other-data")
        with self.assertRaisesRegex(APIError, "this project"):
            prepare_pose_feedback(other_store, self.session, [ref])

    def test_inline_pose_tokens_require_valid_matching_evidence(self):
        job = self.start()
        ref = {"job_id": job["job_id"], "reference_id": self.reference["id"]}
        token = "[[pose:" + job["job_id"] + ":" + self.reference["id"] + "]]"
        self.assertEqual(len(prepare_pose_feedback(self.store, self.session, [ref], token)), 1)
        with self.assertRaisesRegex(APIError, "matching"):
            prepare_pose_feedback(self.store, self.session, [], token)
        with self.assertRaisesRegex(APIError, "malformed"):
            prepare_pose_feedback(self.store, self.session, [ref], "[[pose:broken]]")

    def test_lost_person_retains_raw_estimates_but_does_not_draw_a_skeleton(self):
        job = self.start(mode="lost")
        self.assertEqual(job["frames"][0]["tracking_status"], "lost")
        prepared = prepare_pose_feedback(self.store, self.session,
            [{"job_id": job["job_id"], "reference_id": self.reference["id"]}])[0]
        self.assertEqual(prepared["frame"]["keypoints"], job["frames"][0]["keypoints"])
        with Image.open(io.BytesIO(prepared["_overlay_data"])) as image:
            self.assertEqual(image.getpixel((32, 30)), (0, 0, 128))
            colors = {color for _, color in image.getcolors(maxcolors=image.width * image.height)}
            self.assertNotIn((0, 183, 176), colors)

    def test_all_projects_share_one_worker_slot_and_cancelling_unblocks_next(self):
        first = self.start(mode="hold")
        other_store = SceneStore(self.root / "parallel-data")
        session = other_store.create_session()["session_id"]
        reference = other_store.add_reference(session, "other.png", self.data_url)
        other = HumanPoseJobs(other_store)
        try:
            with patch.dict("os.environ", {"SCENE_FEEDBACK_POSE_RUNNER": self.runner("hold")}):
                second = other.start({"session_id": session, "request_id": uuid.uuid4().hex,
                                      "reference_id": reference["id"], "bbox": [.1, .1, .7, .7]})
                time.sleep(.25)
                self.assertEqual(other.get(second["job_id"])["status"], "queued")
                self.assertFalse((other.directory / second["job_id"] / "input.json").exists())
                self.jobs.cancel(first["job_id"])
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    current = other.get(second["job_id"])
                    if current["completed_frames"] == 1:
                        break
                    time.sleep(.02)
                self.assertEqual((current["status"], current["completed_frames"]), ("running", 1))
                other.cancel(second["job_id"])
        finally:
            other.close()


class HumanPoseHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.web = self.root / "web"
        self.web.mkdir()
        (self.web / "index.html").write_text("viewer")
        self.worker = self.root / "worker.py"
        self.worker.write_text(WORKER)
        self.runner_patch = patch.dict("os.environ", {"SCENE_FEEDBACK_POSE_RUNNER": self.runner("normal")})
        self.runner_patch.start()
        def quiet_start(gateway):
            gateway.ensure()
        self.start_patch = patch.object(WorkspaceGateway, "start", quiet_start)
        self.start_patch.start()
        def create_target(gateway, _model, **_options):
            thread_id = str(uuid.uuid4())
            gateway.store.workspace_thread(thread_id)
            return {"thread_id": thread_id}
        self.create_patch = patch.object(WorkspaceGateway, "create_target", create_target)
        self.create_patch.start()
        self.server = server_module.make_server(port=0, data_dir=self.root / "data", project_dir=self.project,
                                               web_dir=self.web, external_review=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.context = self.server.project_registry.root
        self.session = self.context.store.workspace()["session_id"]
        _, data_url = image_data()
        self.reference = self.context.store.add_reference(self.session, "person.png", data_url)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.create_patch.stop()
        self.start_patch.stop()
        self.runner_patch.stop()
        self.temporary.cleanup()

    def runner(self, mode):
        return json.dumps([sys.executable, str(self.worker), "--manifest", "{manifest}", "--output", "{output}", "--mode", mode])

    def request(self, method, path, body=None, capability=None, control=None):
        headers = {}
        if capability is not None:
            headers["X-Workspace-Capability"] = capability
        if control is not None:
            headers["X-Scene-Harness-Key"] = control
        if body is not None:
            body = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        data = response.read()
        contents = json.loads(data) if response.getheader("Content-Type", "").startswith("application/json") else data
        status = response.status
        connection.close()
        return status, contents

    def payload(self):
        return {"session_id": self.session, "request_id": uuid.uuid4().hex, "reference_id": self.reference["id"],
                "bbox": [.1, .15, .6, .7]}

    def wait(self, path, status):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            code, job = self.request("GET", path)
            self.assertEqual(code, 200)
            if job["status"] == status:
                return job
            time.sleep(.02)
        self.fail(f"HTTP pose job did not reach {status}")

    def test_http_async_jobs_require_capability_and_expose_persisted_json(self):
        payload = self.payload()
        self.assertEqual(self.request("POST", "/api/workspace/pose", payload)[0], 403)
        code, created = self.request("POST", "/api/workspace/pose", payload, self.context.store.browser_token)
        self.assertEqual(code, 202)
        path = "/api/workspace/pose/" + created["job_id"]
        job = self.wait(path, "completed")
        self.assertEqual(len(job["frames"][0]["keypoints"]), 17)
        code, listed = self.request("GET", "/api/workspace/pose?session_id=" + self.session)
        self.assertEqual(code, 200)
        self.assertEqual(listed["jobs"][0]["job_id"], job["job_id"])
        self.assertNotIn("frames", listed["jobs"][0])
        self.assertEqual(self.request("GET", "/api/workspace/pose?session_id=" + uuid.uuid4().hex)[0], 404)
        self.assertEqual(self.request("POST", path + "/cancel", {})[0], 403)
        self.assertEqual(self.request("POST", path + "/cancel", {}, self.context.store.browser_token)[1]["status"], "completed")

    def test_project_prefix_tokens_job_ids_and_media_do_not_cross_projects(self):
        code, created = self.request("POST", "/api/workspace/pose", self.payload(), self.context.store.browser_token)
        self.assertEqual(code, 202)
        job = self.wait("/api/workspace/pose/" + created["job_id"], "completed")
        code, result = self.request("POST", "/api/projects", {"name": "other", "model": "gpt-6-astra", "request_id": uuid.uuid4().hex},
                                    self.context.store.browser_token)
        self.assertEqual(code, 201)
        child = self.server.project_registry.get(result["project"]["project_id"])
        prefix = "/p/" + child.project_id
        self.assertEqual(self.request("GET", prefix + "/api/workspace/pose")[1]["jobs"], [])
        self.assertEqual(self.request("GET", prefix + "/api/workspace/pose/" + job["job_id"])[0], 404)
        self.assertEqual(self.request("POST", prefix + "/api/workspace/pose/" + job["job_id"] + "/cancel", {}, child.store.browser_token)[0], 404)
        self.assertEqual(self.request("POST", prefix + "/api/workspace/pose", self.payload(), self.context.store.browser_token)[0], 403)
        self.assertEqual(self.request("GET", prefix + self.reference["url"])[0], 404)
        code, root_jobs = self.request("GET", "/api/workspace/pose", control=self.context.store.control_token)
        self.assertEqual(code, 200)
        self.assertEqual(root_jobs["jobs"][0]["job_id"], job["job_id"])
        code, child_jobs = self.request("GET", "/api/workspace/pose", control=child.store.control_token)
        self.assertEqual(code, 200)
        self.assertEqual(child_jobs["jobs"], [])
        payload = {"scene_revision": child.store.scene()["revision"], "idempotency_key": uuid.uuid4().hex,
                   "note": "forged other-project result", "pose_refs": [{"job_id": job["job_id"], "reference_id": self.reference["id"]}]}
        code, rejected = self.request("POST", prefix + "/api/sessions/" + child.store.workspace()["session_id"] + "/feedback",
                                      payload, child.store.browser_token)
        self.assertEqual(code, 404, rejected)

    def test_http_cancel_terminates_running_worker_and_keeps_original_job(self):
        with patch.dict("os.environ", {"SCENE_FEEDBACK_POSE_RUNNER": self.runner("hold")}):
            _, created = self.request("POST", "/api/workspace/pose", self.payload(), self.context.store.browser_token)
            path = "/api/workspace/pose/" + created["job_id"]
            job = self.wait(path, "running")
            code, cancelled = self.request("POST", path + "/cancel", {}, self.context.store.browser_token)
            self.assertEqual((code, cancelled["status"]), (200, "cancelled"))
            self.assertEqual(self.request("GET", path)[1]["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()

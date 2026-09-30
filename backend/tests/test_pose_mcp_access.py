"""Exercise actual MCP HTTP credentials against isolated pose job servers."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_human_pose as fixtures

mcp_server = fixtures.mcp_server


class PoseMCPAccessTests(unittest.TestCase):
    # Reuse transport setup without collecting the fixture class's other tests.
    setUp = fixtures.HumanPoseHTTPTests.setUp
    tearDown = fixtures.HumanPoseHTTPTests.tearDown
    runner = fixtures.HumanPoseHTTPTests.runner
    request = fixtures.HumanPoseHTTPTests.request
    payload = fixtures.HumanPoseHTTPTests.payload

    @contextmanager
    def mcp_context(self, context):
        with patch.multiple(
            mcp_server,
            PORT=self.server.server_port,
            BASE_URL=f"http://127.0.0.1:{self.server.server_port}",
            DATA_DIR=context.store.data_dir,
            PROJECT_DIR=context.project_dir,
        ):
            yield

    def wait_for_worker(self, job_id):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            job = mcp_server.workspace_get_human_pose(job_id)
            if job["status"] == "running" and job["completed_frames"] == 1:
                return job
            self.assertNotIn(job["status"], {"failed", "cancelled", "interrupted"}, job.get("error"))
            time.sleep(.02)
        self.fail("isolated MCP worker did not report its first frame")

    def test_real_mcp_start_list_read_retry_and_cancel_with_control_key(self):
        with patch.dict("os.environ", {"SCENE_FEEDBACK_POSE_RUNNER": self.runner("hold")}), self.mcp_context(self.context):
            request_id = uuid.uuid4().hex
            arguments = {"bbox": [.1, .15, .6, .7], "reference_id": self.reference["id"], "request_id": request_id}
            created = mcp_server.workspace_track_human_pose(**arguments)
            self.assertEqual(created["session_id"], self.session)
            self.assertEqual(created["request_id"], request_id)
            job = self.wait_for_worker(created["job_id"])
            listed = mcp_server.workspace_get_human_pose()
            self.assertEqual([item["job_id"] for item in listed["jobs"]], [job["job_id"]])
            self.assertEqual(mcp_server.workspace_track_human_pose(**arguments)["job_id"], job["job_id"])
            process = self.context.gateway.pose_jobs.processes[job["job_id"]]
            self.assertIsNone(process.poll())
            cancelled = mcp_server.workspace_cancel_human_pose(job["job_id"])
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertIsNotNone(process.poll())
            self.assertEqual(mcp_server.workspace_get_human_pose(job["job_id"])["status"], "cancelled")
            self.assertEqual(mcp_server.workspace_cancel_human_pose(job["job_id"])["status"], "cancelled")

    def test_child_mcp_control_key_routes_jobs_and_rejects_foreign_prefix(self):
        code, created = self.request(
            "POST", "/api/projects",
            {"name": "isolated pose scene", "model": "gpt-6-astra", "request_id": uuid.uuid4().hex},
            capability=self.context.store.browser_token,
        )
        self.assertEqual(code, 201, created)
        child = self.server.project_registry.get(created["project"]["project_id"])
        child_session = child.store.workspace()["session_id"]
        _, data_url = fixtures.image_data()
        reference = child.store.add_reference(child_session, "child-person.png", data_url)
        child_prefix = "/p/" + child.project_id
        root_prefix = "/p/" + self.context.project_id
        with patch.dict("os.environ", {"SCENE_FEEDBACK_POSE_RUNNER": self.runner("hold")}), self.mcp_context(child):
            job = mcp_server.workspace_track_human_pose(
                bbox=[.1, .15, .6, .7], reference_id=reference["id"], request_id=uuid.uuid4().hex,
            )
            self.assertEqual(job["session_id"], child_session)
            self.wait_for_worker(job["job_id"])
            self.assertEqual(mcp_server.workspace_get_human_pose()["jobs"][0]["job_id"], job["job_id"])
            self.assertEqual(self.request("GET", "/api/workspace/pose")[1]["jobs"], [])
            payload = {"session_id": child_session, "reference_id": reference["id"], "bbox": [.1, .1, .5, .5], "request_id": uuid.uuid4().hex}
            self.assertEqual(self.request("POST", child_prefix + "/api/workspace/pose", payload,
                                          control=self.context.store.control_token)[0], 403)
            self.assertEqual(self.request("POST", child_prefix + "/api/workspace/pose/" + job["job_id"] + "/cancel", {},
                                          capability=child.store.browser_token, control=self.context.store.control_token)[0], 403)
            self.assertEqual(self.request("POST", root_prefix + "/api/workspace/pose/" + job["job_id"] + "/cancel", {},
                                          control=child.store.control_token)[0], 403)
            self.assertEqual(self.request("POST", child_prefix + "/api/workspace/pose/" + job["job_id"] + "/cancel", {})[0], 403)
            self.assertEqual(self.request("POST", child_prefix + "/api/workspace/pose", payload,
                                          control=uuid.uuid4().hex)[0], 403)
            self.assertEqual(mcp_server.workspace_get_human_pose(job["job_id"])["status"], "running")
            self.assertEqual(mcp_server.workspace_cancel_human_pose(job["job_id"])["status"], "cancelled")
            self.assertEqual(len(child.gateway.pose_jobs.list(child_session)["jobs"]), 1)

    def test_pose_control_access_does_not_authorize_other_browser_mutations(self):
        control = self.context.store.control_token
        requests = [
            ("/api/projects", {"name": "must not be created", "model": "gpt-6-astra", "request_id": uuid.uuid4().hex}),
            ("/api/workspace/target", {"thread_id": str(uuid.uuid4())}),
            ("/api/sessions/" + self.session + "/feedback", {"scene_revision": 1, "note": "must not be submitted"}),
        ]
        for path, body in requests:
            with self.subTest(path=path):
                code, rejected = self.request("POST", path, body, control=control)
                self.assertEqual(code, 403, rejected)
                self.assertIn("browser capability", rejected["error"])
        self.assertEqual(self.context.store.feedback(self.session)["items"], [])

    def test_mcp_paginates_long_tracks_and_keeps_complete_result_on_disk(self):
        _, image = fixtures.image_data()
        clip = self.context.store.set_reference_clip(self.session, {
            "name": "pose samples", "fps": 4,
            "frames": [{"name": f"sample-{index}", "time_sec": index / 4, "data_url": image} for index in range(16)],
        })["reference_clip"]
        with self.mcp_context(self.context):
            job = mcp_server.workspace_track_human_pose(bbox=[.1, .15, .6, .7], view_id=clip["clip_id"], sample_fps=4)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                first = mcp_server.workspace_get_human_pose(job["job_id"])
                if first["status"] == "completed":
                    break
                self.assertNotEqual(first["status"], "failed", first.get("error"))
                time.sleep(.02)
            self.assertEqual(first["status"], "completed")
            self.assertEqual(first["result_frame_count"], 16)
            self.assertEqual(len(first["frames"]), 8)
            self.assertEqual(first["next_frame_offset"], 8)
            second = mcp_server.workspace_get_human_pose(job["job_id"], frame_offset=8)
            self.assertEqual(len(second["frames"]), 8)
            self.assertIsNone(second["next_frame_offset"])
            full = json.loads(Path(first["result_json_path"]).read_text())
            self.assertEqual(first["frames"] + second["frames"], full["frames"])
            for options in ({"max_frames": 33}, {"frame_offset": -1}, {"max_frames": True}):
                with self.assertRaises(ValueError):
                    mcp_server.workspace_get_human_pose(job["job_id"], **options)

    def test_mcp_tracks_multiple_cameras_and_pages_each_view(self):
        _, image = fixtures.image_data()
        first = self.context.store.set_reference_clip(self.session, {
            "name": "left camera", "fps": 2,
            "frames": [{"name": f"left-{index}", "time_sec": index / 2, "data_url": image}
                       for index in range(4)],
        })["reference_clip"]
        bundle = self.context.store.set_reference_clip(self.session, {
            "append_view": True, "name": "right camera", "fps": 2,
            "frames": [{"name": f"right-{index}", "time_sec": index / 2, "data_url": image}
                       for index in range(4)],
        })["reference_clip"]
        second = bundle["views"][0]
        views = [
            {"view_id": first["clip_id"], "reference_id": first["frames"][0]["id"], "bbox": [.1, .1, .6, .7]},
            {"view_id": second["clip_id"], "reference_id": second["frames"][0]["id"], "bbox": [.2, .1, .5, .7]},
        ]
        with self.mcp_context(self.context):
            with self.assertRaisesRegex(ValueError, "uses views"):
                mcp_server.workspace_track_human_pose(views=views, bbox=[.1, .1, .6, .7])
            created = mcp_server.workspace_track_human_pose(views=views, end_time_sec=1.5, sample_fps=2)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                job = mcp_server.workspace_get_human_pose(created["job_id"], max_frames=32)
                if job["status"] == "completed":
                    break
                self.assertNotIn(job["status"], {"failed", "cancelled", "interrupted"}, job.get("error"))
                time.sleep(.02)
            self.assertEqual(job["status"], "completed")
            self.assertTrue(job["multi_view"])
            self.assertEqual(set(job["view_ids"]), {first["clip_id"], second["clip_id"]})
            self.assertEqual(job["result_frame_count"], 8)
            self.assertEqual({frame["view_id"] for frame in job["frames"]}, set(job["view_ids"]))
            page = mcp_server.workspace_get_human_pose(created["job_id"], view_id=second["clip_id"], max_frames=2)
            self.assertEqual((page["total_frame_count"], page["result_frame_count"], page["next_frame_offset"]), (8, 4, 2))
            self.assertTrue(all(frame["view_id"] == second["clip_id"] for frame in page["frames"]))
            next_page = mcp_server.workspace_get_human_pose(created["job_id"], view_id=second["clip_id"], frame_offset=2)
            self.assertEqual(len(next_page["frames"]), 2)
            self.assertIsNone(next_page["next_frame_offset"])
            with self.assertRaisesRegex(ValueError, "requires a pose job_id"):
                mcp_server.workspace_get_human_pose(view_id=second["clip_id"])
            with self.assertRaisesRegex(ValueError, "not a camera"):
                mcp_server.workspace_get_human_pose(created["job_id"], view_id=uuid.uuid4().hex)

    def test_one_click_http_tracks_every_imported_frame_and_supports_windowed_reads(self):
        _, image = fixtures.image_data()
        first = self.context.store.set_reference_clip(self.session, {
            "name": "camera A", "fps": 4,
            "frames": [{"name": f"A{index}", "time_sec": index / 4, "data_url": image}
                       for index in range(4)],
        })["reference_clip"]
        second = self.context.store.set_reference_clip(self.session, {
            "append_view": True, "name": "camera B", "fps": 3,
            "frames": [{"name": f"B{index}", "time_sec": index / 3, "data_url": image}
                       for index in range(3)],
        })["reference_clip"]["views"][0]
        status, created = self.request(
            "POST", "/api/workspace/pose",
            {"session_id": self.session, "request_id": uuid.uuid4().hex, "all_views": True},
            capability=self.context.store.browser_token,
        )
        self.assertEqual(status, 202, created)
        path = "/api/workspace/pose/" + created["job_id"]
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            status, job = self.request("GET", path)
            self.assertEqual(status, 200, job)
            if job["status"] == "completed":
                break
            self.assertNotIn(job["status"], {"failed", "interrupted", "cancelled"}, job.get("error"))
            time.sleep(.02)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["total_frames"], 7)
        self.assertEqual(job["result_frame_count"], 7)
        self.assertEqual([view["sampled_frames"] for view in job["views"]], [4, 3])
        self.assertEqual([view["view_id"] for view in job["views"]], [first["clip_id"], second["clip_id"]])
        self.assertEqual([frame["view_id"] for frame in job["frames"]],
                         [first["clip_id"]] * 4 + [second["clip_id"]] * 3)
        status, window = self.request("GET", path + "?frame_offset=4&max_frames=2")
        self.assertEqual(status, 200, window)
        self.assertEqual([frame["frame_index"] for frame in window["frames"]], [0, 1])
        self.assertEqual(window["next_frame_offset"], 6)
        target = second["frames"][2]["id"]
        status, exact = self.request("GET", path + "?reference_id=" + target)
        self.assertEqual(status, 200, exact)
        self.assertEqual([frame["reference_id"] for frame in exact["frames"]], [target])
        self.assertEqual(self.request("GET", path + "?reference_id=" + uuid.uuid4().hex)[0], 404)
        status, download = self.request("GET", path + "?download=1")
        self.assertEqual(status, 200, download)
        self.assertEqual(len(download["frames"]), 7)
        self.assertEqual(self.request("GET", path + "?download=1&max_frames=2")[0], 400)
        self.assertEqual(self.request("GET", path + "?frame_offset=-1")[0], 400)
        self.assertEqual(self.request("GET", path + "?max_frames=33")[0], 400)

    def test_mcp_can_start_one_click_job_without_manual_boxes(self):
        with self.mcp_context(self.context):
            created = mcp_server.workspace_track_human_pose(all_views=True)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                job = mcp_server.workspace_get_human_pose(created["job_id"])
                if job["status"] == "completed":
                    break
                self.assertNotIn(job["status"], {"failed", "interrupted", "cancelled"}, job.get("error"))
                time.sleep(.02)
            self.assertEqual(job["status"], "completed")
            self.assertTrue(job["automatic"])
            self.assertEqual(job["total_frames"], 1)
            self.assertEqual(job["frames"][0]["reference_id"], self.reference["id"])
            with self.assertRaisesRegex(ValueError, "does not accept"):
                mcp_server.workspace_track_human_pose(all_views=True, bbox=[.1, .1, .6, .7])


if __name__ == "__main__":
    unittest.main()

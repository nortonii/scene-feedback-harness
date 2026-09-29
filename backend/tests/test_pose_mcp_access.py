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


if __name__ == "__main__":
    unittest.main()

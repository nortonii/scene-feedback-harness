"""Real authenticated MCP transport for external pose source/result exchange."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_human_pose as fixtures
from plugin_rpc import PluginRPC, TOOL_MAP

mcp_server = fixtures.mcp_server


class PoseMCPAccessTests(unittest.TestCase):
    setUp = fixtures.HumanPoseHTTPTests.setUp
    tearDown = fixtures.HumanPoseHTTPTests.tearDown
    request = fixtures.HumanPoseHTTPTests.request

    @contextmanager
    def mcp_context(self, context):
        with patch.multiple(mcp_server, PORT=self.server.server_port, BASE_URL=f"http://127.0.0.1:{self.server.server_port}",
                            DATA_DIR=context.store.data_dir, PROJECT_DIR=context.project_dir):
            yield

    def test_real_mcp_export_import_read_and_retry_use_control_key(self):
        with self.mcp_context(self.context):
            request_id = uuid.uuid4().hex
            export = mcp_server.workspace_export_pose_sources(request_id=request_id)
            self.assertEqual(mcp_server.workspace_export_pose_sources(request_id=request_id)["job_id"], export["job_id"])
            result = fixtures.result_for(export)
            self.assertEqual(mcp_server.workspace_import_human_pose(export["job_id"], result=result)["status"], "completed")
            self.assertEqual(mcp_server.workspace_import_human_pose(export["job_id"], result=result)["status"], "completed")
            job = mcp_server.workspace_get_human_pose(export["job_id"])
            self.assertEqual(job["frames"][0]["reference_id"], self.reference["id"])
            self.assertTrue(Path(job["result_json_path"]).is_file())
            self.assertEqual(mcp_server.workspace_get_human_pose()["jobs"][0]["job_id"], export["job_id"])
            with self.assertRaisesRegex(ValueError, "exactly one"):
                mcp_server.workspace_import_human_pose(export["job_id"], result=result, result_path="pose.json")
            self.assertFalse(hasattr(mcp_server, "workspace_track_human_pose"))
            self.assertFalse(hasattr(mcp_server, "workspace_cancel_human_pose"))

    def test_project_scoped_mcp_routes_cannot_import_foreign_results(self):
        code, created = self.request("POST", "/api/projects", {"name": "child", "model": "gpt-6-astra", "request_id": uuid.uuid4().hex},
                                     capability=self.context.store.browser_token)
        self.assertEqual(code, 201)
        child = self.server.project_registry.get(created["project"]["project_id"])
        _, data = fixtures.image_data(); child.store.add_reference(child.store.workspace()["session_id"], "child.png", data)
        with self.mcp_context(self.context): root_export = mcp_server.workspace_export_pose_sources()
        with self.mcp_context(child):
            child_export = mcp_server.workspace_export_pose_sources()
            self.assertEqual(child_export["project_id"], child.project_id)
            with self.assertRaisesRegex(ValueError, "HTTP 404"):
                mcp_server.workspace_import_human_pose(root_export["job_id"], result=fixtures.result_for(root_export))
            wrong = fixtures.result_for(child_export); wrong["project_id"] = root_export["project_id"]
            with self.assertRaisesRegex(ValueError, "HTTP 400"):
                mcp_server.workspace_import_human_pose(child_export["job_id"], result=wrong)
            mcp_server.workspace_import_human_pose(child_export["job_id"], result=fixtures.result_for(child_export))
            self.assertEqual(len(mcp_server.workspace_get_human_pose()["jobs"]), 1)

    def test_event_plugin_tools_use_same_bound_contract_and_custom_profile(self):
        self.assertNotIn("workspace_track_human_pose", TOOL_MAP)
        self.assertNotIn("workspace_cancel_human_pose", TOOL_MAP)
        rpc = PluginRPC(self.context)
        export = json.loads(rpc.call_tool("workspace_export_pose_sources", {})["content"][0]["text"])
        result = fixtures.result_for(export, evidence_kind="projected_3d", names=["hip", "knee"], edges=[[0, 1]], profile="capsule-joints")
        completed = json.loads(rpc.call_tool("workspace_import_human_pose", {"job_id": export["job_id"], "result": result})["content"][0]["text"])
        self.assertEqual(completed["evidence_kind"], "projected_3d")
        detail = json.loads(rpc.call_tool("workspace_get_human_pose", {"job_id": export["job_id"]})["content"][0]["text"])
        self.assertEqual(detail["keypoint_names"], ["hip", "knee"])


if __name__ == "__main__":
    unittest.main()

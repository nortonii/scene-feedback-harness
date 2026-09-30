"""Exercise distributable package boundaries without touching Codex configuration."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from build_plugin import FIXED_FILES, MCP_SCHEMA, NAME, PLUGIN_SCHEMA, runtime_files, validate_package, write_zip
from configure_plugin import configure


class PluginPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="scene-plugin-package-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in FIXED_FILES:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("runtime content\n")
        (self.root / "plugin.json").write_text(json.dumps({
            "$schema": PLUGIN_SCHEMA, "name": NAME, "version": "0.1.0",
            "extensions": {"com.openai": {}},
        }))
        (self.root / "mcp.json").write_text(json.dumps({
            "$schema": MCP_SCHEMA,
            "mcpServers": {"scene_feedback": {"type": "stdio", "command": "python3",
                "args": ["${PLUGIN_ROOT}/scripts/plugin_bridge.py"], "cwd": "${PLUGIN_ROOT}"}},
        }))
        (self.root / "skills" / "visual-feedback").mkdir(parents=True)
        (self.root / "skills" / "visual-feedback" / "SKILL.md").write_text("Review visual evidence.\n")
        (self.root / "backend").mkdir()
        (self.root / "backend" / "server.py").write_text("# runtime\n")
        (self.root / "web").mkdir()
        (self.root / "web" / "index.html").write_text("<html></html>\n")
        self.files = runtime_files(self.root)

    def test_runtime_allowlist_and_reproducible_zip(self):
        for relative in (".venv/auth.json", "backend/data/control_token", "backend/tests/test_runtime.py"):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("do not distribute\n")
        files = runtime_files(self.root)
        self.assertEqual(files, self.files)
        first, second = self.root / "one.zip", self.root / "two.zip"
        write_zip(files, first)
        write_zip(files, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with zipfile.ZipFile(first) as archive:
            self.assertTrue(all(name.startswith(NAME + "/") for name in archive.namelist()))
            self.assertNotIn(NAME + "/backend/data/control_token", archive.namelist())

    def test_symlink_and_secret_files_are_rejected(self):
        (self.root / "web" / "linked.js").symlink_to(self.root / "backend" / "server.py")
        with self.assertRaisesRegex(ValueError, "regular file"):
            runtime_files(self.root)
        (self.root / "web" / "linked.js").unlink()
        (self.root / "web" / ".env").write_text("SECRET=test\n")
        with self.assertRaisesRegex(ValueError, "Credentials"):
            runtime_files(self.root)
        with self.assertRaisesRegex(ValueError, "Disallowed"):
            validate_package({**self.files, "../escape.py": b"invalid"})

    def test_local_configuration_preserves_project_identity_without_token(self):
        data, project = self.root / "data", self.root / "project"
        data.mkdir()
        project.mkdir()
        (data / "control_token").write_text("sensitive-test-token")
        result = configure(self.files, project_id="a" * 32, data_dir=data, project_dir=project, port=18769)
        env = json.loads(result["mcp.json"])["mcpServers"]["scene_feedback"]["env"]
        self.assertEqual(env["SCENE_FEEDBACK_DATA_DIR"], str(data))
        self.assertEqual(env["SCENE_FEEDBACK_PROJECT_ID"], "a" * 32)
        self.assertFalse(any(b"sensitive-test-token" in content for content in result.values()))
        for url in ("http://192.168.3.157:18769/p/" + "a" * 32 + "/mcp", "https://example.com/p/" + "b" * 32 + "/mcp"):
            with self.assertRaises(ValueError):
                configure(self.files, project_id="a" * 32, data_dir=data, project_dir=project, port=18769, mcp_url=url)

    def test_hosted_mapping_has_no_local_server_or_registration_side_effect(self):
        result = configure(self.files, app_id="plugin_asdk_app_registered_test")
        self.assertNotIn("mcp.json", result)
        self.assertEqual(json.loads(result[".app.json"])["apps"]["scene_feedback"]["id"], "plugin_asdk_app_registered_test")
        self.assertEqual(json.loads(result["plugin.json"])["extensions"]["com.openai"]["apps"], "./.app.json")
        with self.assertRaises(ValueError):
            configure(self.files, app_id="https://example.com/mcp")
        with self.assertRaises(ValueError):
            configure(self.files, app_id="plugin_asdk_app_registered_test", project_id="a" * 32)
        # A configured hosted directory must be independently rebuildable.
        hosted = self.root / "hosted"
        for relative, content in result.items():
            path = hosted / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self.assertEqual(runtime_files(hosted), result)


if __name__ == "__main__":
    unittest.main()

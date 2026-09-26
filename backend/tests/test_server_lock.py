"""The HTTP server has exclusive ownership of its persistent data directory."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server as server_module  # noqa: E402


class ServerDataDirLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.web = self.root / "web"
        self.web.mkdir()
        self.data = self.root / "data"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_server(self):
        return server_module.make_server(port=0, data_dir=self.data, web_dir=self.web, project_dir=self.project)

    def test_second_server_is_rejected_before_loading_store_and_close_releases_lock(self) -> None:
        first = self.make_server()
        try:
            with patch.object(server_module, "SceneStore", side_effect=AssertionError("store must not be opened")):
                with self.assertRaisesRegex(RuntimeError, "already using data directory"):
                    self.make_server()
        finally:
            first.server_close()

        reopened = self.make_server()
        reopened.server_close()

    def test_failed_construction_releases_lock(self) -> None:
        with patch.object(server_module, "SceneStore", side_effect=RuntimeError("could not load store")):
            with self.assertRaisesRegex(RuntimeError, "could not load store"):
                self.make_server()

        reopened = self.make_server()
        reopened.server_close()

    def test_unavailable_file_locking_fails_clearly(self) -> None:
        with patch.object(server_module, "fcntl", None):
            with self.assertRaisesRegex(RuntimeError, "requires Unix fcntl file locking"):
                self.make_server()


if __name__ == "__main__":
    unittest.main()

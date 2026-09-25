"""Focused checks for the scene review handoff and local HTTP boundary."""

from __future__ import annotations

import json
import struct
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import APIError, SceneStore  # noqa: E402
from server import make_server  # noqa: E402


def tiny_glb(path: Path) -> None:
    scene = json.dumps({"asset": {"version": "2.0"}, "scenes": [{}], "scene": 0}).encode()
    scene += b" " * (-len(scene) % 4)
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(scene)) + struct.pack("<I4s", len(scene), b"JSON") + scene)


class SceneStoreTests(unittest.TestCase):
    def test_feedback_persists_and_stale_revision_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = SceneStore(temporary)
            session = store.create_session()
            revision = store.scene()["revision"]
            feedback = store.submit_feedback(session["session_id"], {
                "scene_revision": revision,
                "annotations": [
                    {"type": "target_box", "object_id": "chair_back", "coordinate_frame": "world", "center": [0, 0.51, 1.7], "size": [1.25, 0.16, 1.7], "anchor": "bottom"},
                    {"type": "guide_line", "object_id": "chair_back", "coordinate_frame": "world", "start": [0, 0.5, 0.9], "end": [0, 0.5, 2.3]},
                ],
                "note": "Raise the backrest to this line.",
            })
            self.assertEqual(feedback["annotations"][0]["type"], "target_box")
            self.assertEqual(SceneStore(temporary).feedback(session["session_id"])["items"][0]["feedback_id"], feedback["feedback_id"])
            with self.assertRaisesRegex(APIError, "already closed"):
                store.submit_feedback(session["session_id"], {"scene_revision": revision, "note": "duplicate"})
            second = store.create_session()
            store.update_scene(revision, [{"op": "update", "object_id": "chair_back", "fields": {"size": [1.25, 0.16, 1.5]}}])
            with self.assertRaisesRegex(APIError, "scene revision changed"):
                store.submit_feedback(second["session_id"], {"scene_revision": revision, "note": "stale"})

    def test_model_import_and_missing_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = SceneStore(Path(temporary) / "data")
            model = Path(temporary) / "small.glb"
            tiny_glb(model)
            imported = store.import_model(str(model), object_id="sample_model")
            self.assertEqual(imported["object"]["type"], "model")
            self.assertTrue((store.assets_dir / imported["object"]["url"].rsplit("/", 1)[-1]).is_file())
            with self.assertRaisesRegex(APIError, "does not exist"):
                store.import_model(str(Path(temporary) / "missing.glb"))


class HTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        web_dir = root / "web"
        web_dir.mkdir()
        (web_dir / "index.html").write_text("<html>viewer</html>", encoding="utf-8")
        self.server = make_server(port=0, data_dir=root / "data", web_dir=web_dir)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.token = (root / "data" / "control_token").read_text(encoding="ascii").strip()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temporary.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None, *, key: bool = False):
        headers = {"Accept": "application/json"}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        if key:
            headers["X-Scene-Harness-Key"] = self.token
        request = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=3) as response:
                data = response.read()
                return response.status, json.loads(data) if response.headers.get_content_type() == "application/json" else data
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read())

    def test_session_feedback_and_revision_update(self) -> None:
        status, health = self.request("GET", "/api/health")
        self.assertEqual((status, health["service"]), (200, "scene-feedback-harness"))
        status, scene = self.request("GET", "/api/scene")
        self.assertEqual(status, 200)
        status, session = self.request("POST", "/api/sessions", {})
        self.assertEqual(status, 201)
        self.assertIn(f"127.0.0.1:{self.server.server_port}", session["url"])
        status, feedback = self.request("POST", f"/api/sessions/{session['session_id']}/feedback", {
            "scene_revision": scene["revision"], "annotations": [{"type": "target_box", "object_id": "chair_back", "center": [0, 0, 1.8], "size": [1, 0.2, 1.6], "anchor": "bottom"}], "note": "Taller"
        })
        self.assertEqual(status, 201)
        status, result = self.request("GET", f"/api/sessions/{session['session_id']}/feedback")
        self.assertEqual(result["items"][0]["feedback_id"], feedback["feedback_id"])
        status, denied = self.request("POST", "/api/scene/update", {"expected_revision": scene["revision"], "changes": []})
        self.assertEqual(status, 403)
        status, updated = self.request("POST", "/api/scene/update", {"expected_revision": scene["revision"], "changes": [{"op": "update", "object_id": "chair_back", "fields": {"size": [1.25, 0.16, 1.6]}}]}, key=True)
        self.assertEqual(status, 200)
        self.assertEqual(updated["revision"], scene["revision"] + 1)
        status, conflict = self.request("POST", "/api/scene/update", {"expected_revision": scene["revision"], "changes": [{"op": "delete", "object_id": "chair_back"}]}, key=True)
        self.assertEqual(status, 409)

    def test_model_asset_and_static_page(self) -> None:
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"viewer", page)
        model = Path(self.temporary.name) / "small.glb"
        tiny_glb(model)
        status, imported = self.request("POST", "/api/models/import", {"local_path": str(model), "object_id": "small_model"}, key=True)
        self.assertEqual(status, 201)
        status, asset = self.request("GET", imported["object"]["url"])
        self.assertEqual(status, 200)
        self.assertEqual(asset, model.read_bytes())


if __name__ == "__main__":
    unittest.main()

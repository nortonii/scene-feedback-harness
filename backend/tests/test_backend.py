"""Focused checks for the scene review handoff and local HTTP boundary."""

from __future__ import annotations

import json
import base64
import asyncio
import io
import os
import struct
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from PIL import Image


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import APIError, SceneStore  # noqa: E402
from server import make_server  # noqa: E402
import mcp_server  # noqa: E402


def tiny_glb(path: Path) -> None:
    scene = json.dumps({"asset": {"version": "2.0"}, "scenes": [{}], "scene": 0}).encode()
    scene += b" " * (-len(scene) % 4)
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(scene)) + struct.pack("<I4s", len(scene), b"JSON") + scene)


def image_data_url(*, large: bool = False) -> tuple[str, bytes]:
    if large:
        image = Image.frombytes("RGB", (2200, 1400), os.urandom(2200 * 1400 * 3))
        kind, mime = "JPEG", "image/jpeg"
    else:
        image = Image.new("RGB", (24, 24), "#d86643")
        kind, mime = "PNG", "image/png"
    output = io.BytesIO()
    image.save(output, format=kind, quality=90)
    data = output.getvalue()
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}", data


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
            second_round = store.submit_feedback(session["session_id"], {"scene_revision": revision, "note": "One more observation"})
            self.assertEqual(store.get_session(session["session_id"])["feedback_count"], 2)
            self.assertEqual(store.feedback(session["session_id"], cursor=1)["items"][0]["feedback_id"], second_round["feedback_id"])
            second = store.create_session()
            store.update_scene(revision, [{"op": "update", "object_id": "chair_back", "fields": {"size": [1.25, 0.16, 1.5]}}])
            with self.assertRaisesRegex(APIError, "scene revision changed"):
                store.submit_feedback(second["session_id"], {"scene_revision": revision, "note": "stale"})

    def test_visual_packet_persists_originals_and_reuses_session_after_scene_update(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_url, original = image_data_url()
            source = root / "room.png"
            source.write_bytes(original)
            store = SceneStore(root / "data")
            session = store.create_session([str(source)])
            reference = session["reference_images"][0]
            source.unlink()
            self.assertEqual((store.media_dir / reference["url"].rsplit("/", 1)[-1]).read_bytes(), original)
            revision = store.scene()["revision"]
            packet = store.submit_feedback(session["session_id"], {
                "scene_revision": revision,
                "note": "The cabinet top should meet line 1.",
                "annotations": [
                    {"id": "a1", "pane": "reference", "reference_image_id": reference["id"], "type": "line", "coordinates": {"x": 0.1, "y": 0.2, "x2": 0.9, "y2": 0.2}, "group_id": "1"},
                    {"id": "a2", "pane": "scene", "type": "point", "coordinates": {"x": 0.4, "y": 0.6}, "group_id": "1", "object_id": "chair_back"},
                ],
                "selected_object_ids": ["chair_back"], "camera": {"position": [2, 3, 4]},
                "reference_annotated_data_urls": [{"reference_id": reference["id"], "data_url": data_url}],
                "scene_original_data_url": data_url, "scene_annotated_data_url": data_url,
            })
            self.assertEqual(packet["reference_images"][0]["id"], reference["id"])
            self.assertEqual(packet["selected_object_ids"], ["chair_back"])
            self.assertEqual((store.media_dir / packet["scene_original_url"].rsplit("/", 1)[-1]).read_bytes(), original)
            with patch.object(mcp_server, "DATA_DIR", store.data_dir):
                visual = mcp_server._visual_tool_result(store.feedback(session["session_id"]))
            self.assertEqual(sum(block.type == "image" for block in visual.content), 4)
            self.assertEqual(visual.structured_content["items"][0]["scene_revision"], revision)
            scene = store.update_scene(revision, [{"op": "update", "object_id": "chair_back", "fields": {"size": [1.25, 0.16, 1.6]}}])
            store.submit_feedback(session["session_id"], {"scene_revision": scene["revision"], "note": "Now the height is right."})
            reopened = SceneStore(store.data_dir)
            self.assertEqual(reopened.get_session(session["session_id"])["feedback_count"], 2)
            self.assertEqual(reopened.feedback(session["session_id"])["next_cursor"], 2)
            self.assertEqual(reopened.feedback(session["session_id"])["items"][0]["reference_images"][0]["url"], reference["url"])

    def test_visual_annotation_rejects_unknown_reference_and_invalid_coordinates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = SceneStore(temporary)
            session = store.create_session()
            revision = store.scene()["revision"]
            for mark in (
                {"pane": "reference", "reference_image_id": "missing", "type": "point", "coordinates": {"x": 0.5, "y": 0.5}},
                {"pane": "scene", "type": "arrow", "coordinates": {"x": 0.5, "y": 0.5, "x2": 1.2, "y2": 0.1}},
            ):
                with self.assertRaises(APIError):
                    store.submit_feedback(session["session_id"], {"scene_revision": revision, "annotations": [mark]})
            image_url, _ = image_data_url()
            with self.assertRaisesRegex(APIError, "MIME type"):
                store.add_reference(session["session_id"], "wrong.jpg", image_url.replace("image/png", "image/jpeg", 1))
            self.assertEqual(store.get_session(session["session_id"])["feedback_count"], 0)

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
            before = store.scene()["revision"]
            preview = store.set_scene_preview(str(model))
            self.assertEqual(preview["revision"], before + 1)
            self.assertEqual([obj["id"] for obj in preview["objects"]], ["scene_preview"])

    def test_existing_v1_state_loads_without_losing_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = SceneStore(temporary)
            session = store.create_session()
            state = json.loads(store.state_path.read_text(encoding="utf-8"))
            state["schema_version"] = 1
            state["sessions"][session["session_id"]].pop("reference_images")
            store.state_path.write_text(json.dumps(state), encoding="utf-8")
            loaded = SceneStore(temporary)
            self.assertEqual(loaded.get_session(session["session_id"])["reference_images"], [])
            self.assertEqual(loaded.get_session(session["session_id"])["status"], "open")

    def test_model_child_selection_is_preserved_as_visual_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model = root / "room.glb"
            tiny_glb(model)
            store = SceneStore(root / "data")
            scene = store.set_scene_preview(str(model))
            session = store.create_session()
            node = {"parent_object_id": "scene_preview", "node_path": [0, 2], "node_name": "Cabinet top"}
            packet = store.submit_feedback(session["session_id"], {
                "scene_revision": scene["revision"], "note": "Move this cabinet toward the photo mark.",
                "selected_object_ids": ["scene_preview"], "selected_scene_nodes": [node],
                "annotations": [{"pane": "scene", "type": "point", "object_id": "scene_preview", "scene_node": node, "coordinates": {"x": 0.5, "y": 0.4}}],
            })
            self.assertEqual(packet["selected_scene_nodes"], [node])
            self.assertEqual(SceneStore(store.data_dir).feedback(session["session_id"])["items"][0]["annotations"][0]["scene_node"], node)
            another = store.create_session()
            with self.assertRaisesRegex(APIError, "child indices"):
                store.submit_feedback(another["session_id"], {
                    "scene_revision": scene["revision"], "note": "invalid path", "selected_object_ids": ["scene_preview"],
                    "selected_scene_nodes": [{**node, "node_path": [0, -1]}],
                })


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

    def request(self, method: str, path: str, payload: dict | None = None, *, key: bool = False, origin: str | None = None):
        headers = {"Accept": "application/json"}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        if key:
            headers["X-Scene-Harness-Key"] = self.token
        if origin:
            headers["Origin"] = origin
        request = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=10) as response:
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

    def test_browser_upload_and_large_two_reference_visual_packet(self) -> None:
        image_url, image_bytes = image_data_url(large=True)
        self.assertGreater(len(image_bytes) * 4 // 3 * 4, 8 * 1024 * 1024)
        status, session = self.request("POST", "/api/sessions", {})
        self.assertEqual(status, 201)
        references = []
        for index in range(2):
            status, reference = self.request("POST", f"/api/sessions/{session['session_id']}/references", {"name": f"reference-{index}.jpg", "data_url": image_url})
            self.assertEqual(status, 201)
            references.append(reference)
        status, stored_session = self.request("GET", f"/api/sessions/{session['session_id']}")
        self.assertEqual(len(stored_session["reference_images"]), 2)
        status, original = self.request("GET", references[0]["url"])
        self.assertEqual((status, original), (200, image_bytes))
        status, scene = self.request("GET", "/api/scene")
        status, feedback = self.request("POST", f"/api/sessions/{session['session_id']}/feedback", {
            "scene_revision": scene["revision"], "note": "Align the furniture with marks 1 and 2.",
            "reference_annotated_data_urls": [{"reference_id": item["id"], "data_url": image_url} for item in references],
            "scene_original_data_url": image_url, "scene_annotated_data_url": image_url,
        })
        self.assertEqual(status, 201)
        self.assertEqual(len(feedback["reference_annotated_images"]), 2)
        self.assertEqual(feedback["reference_images"], references)
        status, denied = self.request("POST", "/api/sessions", {"reference_images": ["/tmp/secret.png"]})
        self.assertEqual(status, 403)
        tiny_url, _ = image_data_url()
        status, denied = self.request("POST", f"/api/sessions/{session['session_id']}/references", {"name": "other.png", "data_url": tiny_url}, origin="https://example.com")
        self.assertEqual(status, 403)

    def test_mcp_tool_returns_actual_images_after_visual_submission(self) -> None:
        data_url, image_bytes = image_data_url()
        source = Path(self.temporary.name) / "photo.png"
        source.write_bytes(image_bytes)
        with patch.object(mcp_server, "PORT", self.server.server_port), patch.object(mcp_server, "BASE_URL", self.base), patch.object(mcp_server, "DATA_DIR", Path(self.temporary.name) / "data"):
            opened = asyncio.run(mcp_server.mcp.call_tool("request_visual_feedback", {"reference_images": [str(source)], "wait_for_submit": False, "open_browser": False}))
            self.assertEqual(opened.structured_content["reference_images"][0]["name"], "photo.png")
            session_id = opened.structured_content["session_id"]
            status, scene = self.request("GET", "/api/scene")
            self.assertEqual(status, 200)
            status, submitted = self.request("POST", f"/api/sessions/{session_id}/feedback", {"scene_revision": scene["revision"], "note": "Move this edge", "scene_original_data_url": data_url, "scene_annotated_data_url": data_url})
            self.assertEqual(status, 201)
            result = asyncio.run(mcp_server.mcp.call_tool("get_visual_feedback", {"session_id": session_id, "cursor": 0}))
            self.assertEqual(sum(block.type == "image" for block in result.content), 3)
            self.assertEqual(result.structured_content["next_cursor"], 1)

    def test_invalid_reused_session_does_not_replace_scene(self) -> None:
        model = Path(self.temporary.name) / "preview.glb"
        tiny_glb(model)
        status, session = self.request("POST", "/api/sessions", {})
        self.assertEqual(status, 201)
        status, before = self.request("GET", "/api/scene")
        self.assertEqual(status, 200)
        invalid_calls = (
            {"session_id": "0" * 32, "scene_glb_path": str(model)},
            {"session_id": session["session_id"], "reference_images": [], "scene_glb_path": str(model)},
            {"session_id": session["session_id"], "cursor": -1, "scene_glb_path": str(model)},
        )
        with patch.object(mcp_server, "PORT", self.server.server_port), patch.object(mcp_server, "BASE_URL", self.base), patch.object(mcp_server, "DATA_DIR", Path(self.temporary.name) / "data"):
            for arguments in invalid_calls:
                with self.assertRaises(ValueError):
                    asyncio.run(mcp_server.request_visual_feedback(**arguments, open_browser=False, wait_for_submit=False))
        status, after = self.request("GET", "/api/scene")
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()

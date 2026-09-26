"""Calibrated reference metadata and safe per-frame alignment imports."""

from __future__ import annotations

import base64
import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import APIError, SceneStore  # noqa: E402
from gateway import WorkspaceGateway  # noqa: E402
from server import make_server  # noqa: E402


def sample_image(width: int = 24, height: int = 16, color: str = "#446688") -> tuple[bytes, str]:
    image = Image.new("RGB", (width, height), color)
    output = io.BytesIO()
    image.save(output, format="PNG")
    data = output.getvalue()
    return data, "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def calibrated_camera(width: int = 24, height: int = 16) -> dict:
    return {
        "camera_to_world": [[1, 0, 0, 0.2], [0, 1, 0, -0.3], [0, 0, 1, 2.4], [0, 0, 0, 1]],
        "intrinsics": {"width": width, "height": height, "fx": 18.3, "fy": 18.2, "cx": width / 2, "cy": height / 2},
        "distortion": [-0.1, 0.03, 0, 0, 0],
        "calibration_status": "measured pose, approximate lens",
        "image_undistorted": False,
    }


class ReferenceCameraStoreTests(unittest.TestCase):
    def test_camera_and_undistorted_image_persist_without_replacing_original(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SceneStore(directory)
            session = store.create_session()
            raw, raw_url = sample_image()
            aligned, aligned_url = sample_image(color="#dd8855")
            reference = store.add_reference(session["session_id"], "cam_000480.png", raw_url)
            result = store.set_reference_cameras(session["session_id"], [{"reference_id": reference["id"], "camera": calibrated_camera(), "alignment_image_data_url": aligned_url}])
            changed = result["reference_images"][0]
            self.assertEqual(changed["url"], reference["url"])
            self.assertEqual((store.media_dir / reference["url"].rsplit("/", 1)[-1]).read_bytes(), raw)
            self.assertEqual((store.media_dir / changed["alignment_image_url"].rsplit("/", 1)[-1]).read_bytes(), aligned)
            self.assertEqual(SceneStore(directory).get_session(session["session_id"])["reference_images"][0], changed)
            self.assertEqual(store.scene()["revision"], 1)

            feedback = store.submit_feedback(session["session_id"], {"scene_revision": 1, "active_reference_id": reference["id"], "aligned_reference_id": reference["id"], "note": "compare top edge"})
            self.assertEqual((feedback["active_reference_id"], feedback["aligned_reference_id"]), (reference["id"], reference["id"]))
            self.assertEqual(feedback["reference_images"][0]["alignment_image_url"], changed["alignment_image_url"])
            gateway = WorkspaceGateway(store, directory, external_review=True)
            text, image_paths = gateway._turn_input(feedback)
            self.assertIn("场景截图已按这张参考图的标定相机视角对齐", text)
            self.assertIn(reference["id"], text)
            self.assertIn(str(store.media_dir / changed["alignment_image_url"].rsplit("/", 1)[-1]), image_paths)

    def test_invalid_cameras_and_feedback_are_rejected_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SceneStore(directory)
            session = store.create_session()
            _, raw_url = sample_image()
            reference = store.add_reference(session["session_id"], "cam.png", raw_url)
            other = store.add_reference(session["session_id"], "other.png", raw_url)
            wrong_size = calibrated_camera(width=25)
            with self.assertRaisesRegex(APIError, "dimensions"):
                store.set_reference_cameras(session["session_id"], [
                    {"reference_id": reference["id"], "camera": calibrated_camera()},
                    {"reference_id": other["id"], "camera": wrong_size},
                ])
            self.assertNotIn("camera", store.get_session(session["session_id"])["reference_images"][0])
            mirrored = calibrated_camera()
            mirrored["camera_to_world"][0][0] = -1
            with self.assertRaisesRegex(APIError, "right-handed"):
                store.set_reference_cameras(session["session_id"], [{"reference_id": reference["id"], "camera": mirrored}])
            with self.assertRaisesRegex(APIError, "calibrated reference"):
                store.submit_feedback(session["session_id"], {"scene_revision": 1, "aligned_reference_id": reference["id"]})
            with self.assertRaisesRegex(APIError, "current reference"):
                store.submit_feedback(session["session_id"], {"scene_revision": 1, "active_reference_id": "unknown"})

    def test_changing_camera_clears_old_frame_alignment_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SceneStore(directory)
            session = store.create_session()
            _, raw_url = sample_image()
            reference = store.add_reference(session["session_id"], "cam_000480.png", raw_url)
            result = store.set_reference_cameras(session["session_id"], [{"reference_id": reference["id"], "camera": calibrated_camera(), "alignment_image_data_url": raw_url}])
            self.assertIn("alignment_image_url", result["reference_images"][0])
            next_camera = calibrated_camera()
            next_camera["intrinsics"]["fx"] += 1
            result = store.set_reference_cameras(session["session_id"], [{"reference_id": reference["id"], "camera": next_camera}])
            self.assertNotIn("alignment_image_url", result["reference_images"][0])


class CameraManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.store = SceneStore(self.root / "data")
        self.gateway = WorkspaceGateway(self.store, self.project, external_review=True)
        self.session_id = self.gateway.ensure()["session_id"]
        self.data, self.data_url = sample_image()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def manifest(self, *, camera: dict | None = None) -> None:
        document = {"schema_version": 1, "entries": [{"name_prefix": "cam_rgb_", "camera": camera or calibrated_camera()}]}
        (self.store.data_dir / "reference_cameras.json").write_text(json.dumps(document), encoding="utf-8")

    def source(self, frame: str) -> Path:
        directory = self.project / "cam_rgb"
        directory.mkdir(exist_ok=True)
        path = directory / f"{frame}.png"
        path.write_bytes(self.data)
        return path

    def test_manifest_updates_existing_and_future_frames_without_copying_alignment_image(self) -> None:
        source = self.source("000480")
        first = self.gateway.add_reference_paths([str(source)])["reference_images"][0]
        self.assertNotIn("camera", first)
        self.manifest()
        first = self.gateway.apply_reference_camera_manifest()["reference_images"][0]
        self.assertEqual(first["camera"]["intrinsics"]["width"], 24)
        first = self.store.set_reference_cameras(self.session_id, [{"reference_id": first["id"], "camera": calibrated_camera(), "alignment_image_data_url": self.data_url}])["reference_images"][0]
        duplicate = self.gateway.add_reference_paths([str(source), str(source)])["reference_images"]
        self.assertEqual(len(duplicate), 1)
        self.assertEqual(duplicate[0]["alignment_image_url"], first["alignment_image_url"])
        second = self.gateway.add_reference_paths([str(self.source("000481"))])["reference_images"][1]
        self.assertIn("camera", second)
        self.assertNotIn("alignment_image_url", second)
        third = self.gateway.add_reference_data_url(self.session_id, "cam_rgb_000482.png", self.data_url)
        self.assertIn("camera", third)
        self.assertNotIn("alignment_image_url", third)

    def test_bad_manifest_or_image_size_rejects_import(self) -> None:
        path = self.source("000480")
        self.manifest(camera=calibrated_camera(width=25))
        with self.assertRaisesRegex(APIError, "dimensions"):
            self.gateway.add_reference_paths([str(path)])
        self.assertEqual(self.store.get_session(self.session_id)["reference_images"], [])
        (self.store.data_dir / "reference_cameras.json").write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(APIError, "cannot be read"):
            self.gateway.add_reference_paths([str(path)])


class CameraHTTPTests(unittest.TestCase):
    def test_camera_endpoint_requires_control_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            web = root / "web"
            project.mkdir()
            web.mkdir()
            (web / "index.html").write_text("review", encoding="utf-8")
            server = make_server(port=0, data_dir=root / "data", web_dir=web, project_dir=project, external_review=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                store = server.scene_store
                session_id = store.workspace()["session_id"]
                _, raw_url = sample_image()
                reference = store.add_reference(session_id, "cam.png", raw_url)
                payload = json.dumps({"cameras": [{"reference_id": reference["id"], "camera": calibrated_camera()}]}).encode()
                base = f"http://127.0.0.1:{server.server_port}"
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                def post(key: bool) -> tuple[int, dict]:
                    headers = {"Content-Type": "application/json"}
                    if key:
                        headers["X-Scene-Harness-Key"] = store.control_token
                    request = urllib.request.Request(base + "/api/workspace/reference-cameras", data=payload, headers=headers, method="POST")
                    try:
                        with opener.open(request) as response:
                            return response.status, json.load(response)
                    except urllib.error.HTTPError as exc:
                        with exc:
                            return exc.code, json.load(exc)
                self.assertEqual(post(False)[0], 403)
                status, result = post(True)
                self.assertEqual(status, 200)
                self.assertEqual(result["reference_images"][0]["camera"]["intrinsics"]["fx"], 18.3)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()

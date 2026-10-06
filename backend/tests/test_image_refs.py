"""Dragged images keep their own immutable source, camera and image evidence."""
from __future__ import annotations

import base64
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import APIError, SceneStore
from gateway import WorkspaceGateway
from feedback_summary import model_input_plan
import mcp_server
from plugin_rpc import PluginRPC


def picture(size=(24, 12), color="white", *, exif_orientation=None):
    image = Image.new("RGB", size, color)
    output = io.BytesIO()
    if exif_orientation is None:
        image.save(output, "PNG")
        mime = "image/png"
    else:
        exif = image.getexif()
        exif[274] = exif_orientation
        image.save(output, "JPEG", exif=exif)
        mime = "image/jpeg"
    data = output.getvalue()
    return data, "data:" + mime + ";base64," + base64.b64encode(data).decode()


class DraggedImageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.store = SceneStore(self.root / "data")
        self.gateway = WorkspaceGateway(self.store, self.project, external_review=True)
        self.session = self.gateway.ensure()["session_id"]
        self.store.replace_scene(1, [{"id": "cabinet", "type": "box", "position": [0, 0, 0], "size": [1, 1, 1]}])
        self.data, self.data_url = picture()
        self.marked, self.marked_url = picture(color="red")
        self.camera = {"position": [0, 1, 3], "target": [0, 0, 0], "up": [0, 1, 0], "fov": 45,
                       "aspect": 2, "reference_image_id": None, "alignment_exact": False}

    def tearDown(self):
        self.tmp.cleanup()

    def scene_image(self, **changes):
        return {"id": "scene1", "pane": "scene", "label": "柜子截图", "image_width": 24, "image_height": 12,
                "original_data_url": self.data_url, "scene_revision": self.store.scene()["revision"],
                "camera": copy.deepcopy(self.camera), "selected_object_ids": ["cabinet"], **changes}

    def payload(self, *images, **changes):
        return {"scene_revision": self.store.scene()["revision"],
                "note": "Compare " + " and ".join("[[image:" + image["id"] + "]]" for image in images),
                "image_refs": list(images), **changes}

    def static_image(self, *, source_size=(48, 24), capture_size=(24, 12), exif_orientation=None):
        source, source_url = picture(source_size, color="blue", exif_orientation=exif_orientation)
        reference = self.store.add_reference(self.session, "原照片", source_url)
        capture, capture_url = picture(capture_size, color="green")
        return reference, source, capture, {"id": "ref1", "pane": "reference", "label": "参考图",
            "reference_id": reference["id"], "original_data_url": capture_url,
            "image_width": capture_size[0], "image_height": capture_size[1]}

    def media(self, url):
        return (self.store.media_dir / url.rsplit("/", 1)[-1]).read_bytes()

    def reject_without_write(self, payload, message=None):
        before_state = copy.deepcopy(self.store.state)
        before_media = {path.name for path in self.store.media_dir.iterdir()}
        if message:
            with self.assertRaisesRegex(APIError, message):
                self.store.submit_feedback(self.session, payload)
        else:
            with self.assertRaises(APIError):
                self.store.submit_feedback(self.session, payload)
        self.assertEqual(self.store.state, before_state)
        self.assertEqual({path.name for path in self.store.media_dir.iterdir()}, before_media)

    def import_views(self):
        clip = self.store.set_reference_clip(self.session, {"name": "front", "fps": 2, "frames": [
            {"name": "front0.png", "time_sec": 0, "data_url": self.data_url},
            {"name": "front1.png", "time_sec": .5, "data_url": self.data_url}]})["reference_clip"]
        return self.store.set_reference_clip(self.session, {"append_view": True, "name": "side", "fps": 2, "frames": [
            {"name": "side0.png", "time_sec": 0, "data_url": self.marked_url},
            {"name": "side1.png", "time_sec": .5, "data_url": self.marked_url}]})["reference_clip"]

    def dynamic_image(self, clip):
        view = clip["views"][0]
        frame = view["frames"][1]
        return {"id": "side1", "pane": "reference", "label": "侧面第2帧", "reference_id": frame["id"],
                "clip_id": clip["clip_id"], "view_id": view["clip_id"], "time_sec": frame["time_sec"],
                "original_data_url": self.marked_url, "image_width": 24, "image_height": 12}

    def test_scene_captures_preserve_camera_selection_and_clean_marked_bytes(self):
        image = self.scene_image(annotated_data_url=self.marked_url)
        payload = self.payload(image)
        original = copy.deepcopy(payload)
        saved = self.store.submit_feedback(self.session, payload)
        evidence = saved["image_refs"][0]
        self.assertEqual(evidence["camera"], self.camera)
        self.assertEqual(evidence["selected_object_ids"], ["cabinet"])
        self.assertEqual(evidence["scene_revision"], 2)
        self.assertEqual(self.media(evidence["original_url"]), self.data)
        self.assertEqual(self.media(evidence["annotated_url"]), self.marked)
        self.assertNotIn("display_original_url", evidence)
        self.assertFalse(any(key.endswith("data_url") for key in evidence))
        self.assertEqual(saved["inline_references"][0]["image"], evidence)
        self.assertEqual(payload, original)
        self.assertEqual(SceneStore(self.store.data_dir).feedback_by_id(saved["feedback_id"]), saved)

    def test_reference_binds_server_original_and_preserves_scaled_display_capture(self):
        reference, source, capture, image = self.static_image()
        image["annotated_data_url"] = self.marked_url
        saved = self.store.submit_feedback(self.session, self.payload(image))
        evidence = saved["image_refs"][0]
        self.assertEqual(evidence["original_url"], reference["url"])
        self.assertEqual(self.media(evidence["original_url"]), source)
        self.assertEqual(self.media(evidence["display_original_url"]), capture)
        self.assertEqual(self.media(evidence["annotated_url"]), self.marked)
        self.assertEqual((evidence["source_image_width"], evidence["source_image_height"]), (48, 24))
        self.assertEqual((evidence["image_width"], evidence["image_height"]), (24, 12))

    def test_reference_source_dimensions_use_exif_display_orientation(self):
        reference, source, capture, image = self.static_image(source_size=(48, 24), capture_size=(12, 24), exif_orientation=6)
        saved = self.store.submit_feedback(self.session, self.payload(image))
        evidence = saved["image_refs"][0]
        self.assertEqual(evidence["original_url"], reference["url"])
        self.assertEqual((evidence["source_image_width"], evidence["source_image_height"]), (24, 48))

    def test_stale_scene_capture_does_not_change_main_revision_or_selection(self):
        old = self.scene_image(annotated_data_url=self.marked_url)
        self.store.update_scene(2, [{"op": "delete", "object_id": "cabinet"}])
        self.reject_without_write(self.payload(old), "confirm stale dragged")
        saved = self.store.submit_feedback(self.session, self.payload(old, confirm_stale=True))
        self.assertEqual(saved["scene_revision"], 3)
        self.assertEqual(saved["selected_object_ids"], [])
        self.assertNotIn("submitted_from_stale_snapshot", saved)
        self.assertEqual(saved["image_refs"][0]["scene_revision"], 2)
        self.assertTrue(saved["image_refs"][0]["from_stale_snapshot"])
        text, _ = self.gateway._turn_input(saved)
        source = json.loads(text.splitlines()[2].removeprefix("证据："))["image_refs"][0]
        self.assertEqual(source["scene_revision"], 2)
        self.assertTrue(source["from_stale_snapshot"])
        self.assertEqual(json.loads(text.splitlines()[2].removeprefix("证据："))["scene_revision"], 3)

    def test_scene_capture_revision_camera_and_selection_validation(self):
        changes = [{"scene_revision": 99}, {"scene_revision": True}, {"camera": {}}, {"camera": None},
                   {"camera": {"foo": 1}}, {"camera": {**self.camera, "target": self.camera["position"]}},
                   {"camera": {**self.camera, "up": [0, 0, 0]}},
                   {"camera": {"position": [1, 2]}}, {"camera": {"position": [1, 2, float("nan")]}},
                   {"camera": {"fov": 200}}, {"selected_object_ids": ["missing"]},
                   {"selected_object_ids": ["cabinet", "cabinet"]},
                   {"selected_scene_nodes": [{"parent_object_id": "cabinet", "node_path": [0]}]}]
        changes += [{"camera": {key: value for key, value in self.camera.items() if key != missing}}
                    for missing in ("position", "target", "up", "fov", "aspect")]
        for change in changes:
            with self.subTest(change=change):
                self.reject_without_write(self.payload(self.scene_image(**change)))

    def test_missing_duplicate_or_uncited_images_and_malformed_tokens_reject(self):
        image = self.scene_image()
        bad = [self.payload(image, note="[[image:missing]]"), self.payload(image, note="No citation"),
               self.payload(image, note="[[image:scene1]"), self.payload(image, note="[[image:scene 1]]"),
               self.payload(image, image), {"scene_revision": 2, "note": "[[image:scene1]]"},
               self.payload(*[self.scene_image(id="image" + str(index)) for index in range(9)])]
        for payload in bad:
            with self.subTest(note=payload["note"]):
                self.reject_without_write(payload)
        saved = self.store.submit_feedback(self.session, self.payload(image, note="[[image:scene1]] then [[image:scene1]]"))
        self.assertEqual(len(saved["inline_references"]), 1)

    def test_image_payload_dimensions_types_and_unknown_fields_reject(self):
        changes = [{"image_width": 23}, {"image_width": True}, {"image_height": 0}, {"pane": []},
                   {"original_data_url": "https://example.com/image.png"}, {"label": "\n"},
                   {"id": "not valid"}, {"original_url": "/media/fake.png"},
                   {"annotated_data_url": picture((12, 24))[1]}]
        for change in changes:
            with self.subTest(change=change):
                self.reject_without_write(self.payload(self.scene_image(**change)))

    def test_reference_aspect_foreign_source_and_scene_metadata_reject(self):
        reference, _, _, image = self.static_image()
        other = self.store.create_session()
        foreign = self.store.add_reference(other["session_id"], "另一会话", self.data_url)
        mutations = [dict(image, reference_id=foreign["id"]), dict(image, reference_id=None),
                     dict(image, scene_revision=2), dict(image, clip_id="anything"),
                     dict(image, original_data_url=picture((24, 24))[1], image_height=24)]
        for changed in mutations:
            with self.subTest(changed=changed):
                self.reject_without_write(self.payload(changed))

    def test_dynamic_reference_derives_exact_source_view_time_and_ordinal(self):
        clip = self.import_views()
        image = self.dynamic_image(clip)
        saved = self.store.submit_feedback(self.session, self.payload(image))
        evidence = saved["image_refs"][0]
        frame = clip["views"][0]["frames"][1]
        self.assertEqual(evidence["original_url"], frame["url"])
        self.assertEqual(evidence["frame_index"], 1)
        self.assertEqual(evidence["view_name"], "side")
        self.assertEqual(evidence["time_sec"], .5)
        self.assertEqual(evidence["reference_name"], "side1.png")

    def test_dynamic_reference_wrong_clip_view_time_ordinal_and_replaced_clip_reject(self):
        clip = self.import_views()
        image = self.dynamic_image(clip)
        for changes in ({"clip_id": "foreign"}, {"view_id": clip["clip_id"]}, {"time_sec": .1},
                        {"time_sec": True}, {"frame_index": 0}, {"frame_index": True}, {"view_id": None}):
            with self.subTest(changes=changes):
                self.reject_without_write(self.payload(dict(image, **changes)))
        self.store.set_reference_clip(self.session, {"clear": True})
        self.reject_without_write(self.payload(image), "current sources")

    def test_dynamic_scene_context_binds_view_but_keeps_independent_time(self):
        clip = self.import_views()
        view = clip["views"][0]
        image = self.scene_image(clip_id=clip["clip_id"], view_id=view["clip_id"], time_sec=.45,
                                 reference_id=view["frames"][1]["id"])
        saved = self.store.submit_feedback(self.session, self.payload(image))
        self.assertEqual(saved["image_refs"][0]["time_sec"], .45)
        self.assertEqual(saved["image_refs"][0]["view_name"], "side")
        self.reject_without_write(self.payload(dict(image, view_id=clip["clip_id"])), "view does not match")
        self.reject_without_write(self.payload(dict(image, time_sec=100)), "finite number")

    def test_gateway_mcp_and_plugin_deliver_actual_labeled_images(self):
        _, source, capture, reference = self.static_image()
        reference["annotated_data_url"] = self.marked_url
        scene = self.scene_image(annotated_data_url=self.marked_url)
        saved = self.store.submit_feedback(self.session, self.payload(reference, scene, crops=[{
            "source": "reference", "reference_id": reference["reference_id"], "data_url": self.marked_url}]))
        text, paths = self.gateway._turn_input(saved)
        plan = model_input_plan(saved, self.store.data_dir)
        self.assertEqual(paths, [item["path"] for item in plan["images"]])
        self.assertEqual(len(paths), 3)  # Three unique source, marked and scene byte streams.
        self.assertIn("[[image:ref1]]", text)
        self.assertIn("[[image:scene1]]", text)
        self.assertIn('"camera"', text)
        self.assertEqual({Path(path).read_bytes() for path in paths}, {source, self.marked, self.data})
        result = mcp_server._visual_tool_result({"items": [copy.deepcopy(saved)]}, self.store.data_dir, include_details=True)
        self.assertEqual(sum(item.type == "image" for item in result.content), len(paths))
        refs = result.structured_content["items"][0]["image_refs"]
        self.assertTrue(all(Path(item["original_path"]).is_file() for item in refs))
        self.assertEqual(Path(refs[0]["display_original_path"]).read_bytes(), capture)
        summarized = json.loads(result.content[0].text)["items"][0]["image_refs"]
        self.assertEqual(summarized[1]["scene_revision"], 2)
        self.assertEqual(summarized[1]["token"], "[[image:scene1]]")
        self.assertTrue(any(item.type == "text" and "I2 · 引用原图" in item.text for item in result.content))
        self.assertEqual(json.loads(result.content[0].text)["items"][0]["crops"][0]["reference_id"],
                         reference["reference_id"])
        context = SimpleNamespace(store=self.store, gateway=self.gateway)
        plugin = PluginRPC(context).call_tool("workspace_get_feedback", {"feedback_id": saved["feedback_id"], "include_details": True})
        self.assertEqual(sum(item["type"] == "image" for item in plugin["content"]), len(paths))
        self.assertEqual(plugin["structuredContent"]["items"][0]["image_refs"][1]["camera"], self.camera)

    def test_feedback_evidence_survives_reference_scene_and_clip_replacement(self):
        clip = self.import_views()
        image = self.dynamic_image(clip)
        saved = self.store.submit_feedback(self.session, self.payload(image, self.scene_image()))
        original_images = copy.deepcopy(saved["image_refs"])
        self.store.set_reference_clip(self.session, {"clear": True})
        self.store.update_scene(2, [{"op": "delete", "object_id": "cabinet"}])
        fetched = self.store.feedback_by_id(saved["feedback_id"])
        self.assertEqual(fetched["image_refs"], original_images)
        result = mcp_server._visual_tool_result({"items": [fetched]}, self.store.data_dir)
        self.assertEqual(sum(item.type == "image" for item in result.content),
                         len(model_input_plan(fetched, self.store.data_dir)["images"]))

    def test_idempotent_replay_keeps_same_evidence_after_source_replacement(self):
        clip = self.import_views()
        payload = self.payload(self.dynamic_image(clip), idempotency_key="drag-repeat-1")
        saved = self.store.submit_feedback(self.session, payload)
        files = {path.name for path in self.store.media_dir.iterdir()}
        self.store.set_reference_clip(self.session, {"clear": True})
        replay = self.store.submit_feedback(self.session, payload)
        self.assertEqual(replay, saved)
        self.assertEqual({path.name for path in self.store.media_dir.iterdir()}, files)
        changed = copy.deepcopy(payload)
        changed["image_refs"][0]["label"] = "different capture"
        self.reject_without_write(changed, "already used")

    def test_old_feedback_without_image_refs_remains_compatible(self):
        saved = self.store.submit_feedback(self.session, {"scene_revision": 2, "note": "Make cabinet taller"})
        self.assertNotIn("image_refs", saved)
        self.assertEqual(self.gateway._turn_input(saved)[1], [])
        result = mcp_server._visual_tool_result({"items": [copy.deepcopy(saved)]}, self.store.data_dir)
        self.assertEqual(sum(item.type == "image" for item in result.content), 0)
        self.assertTrue(self.gateway.state()["image_references_supported"])


if __name__ == "__main__":
    unittest.main()

"""Model-facing visual evidence stays small without changing saved feedback."""

from __future__ import annotations

import base64
import copy
import io
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import SceneStore
from dynamic import reference_views, shared_duration
from feedback_summary import image_caption, model_input_plan
from gateway import WorkspaceGateway
from mcp_server import _visual_tool_result


def picture(color: tuple[int, int, int]) -> str:
    output = io.BytesIO()
    Image.new("RGB", (24, 16), color).save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def bindings(plan: dict) -> set[str]:
    return {entry["source"] for image in plan["images"]
            for entry in [{"source": image["source"]}, *image.get("aliases", [])]}


class CompactFeedbackDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.project = root / "project"
        self.project.mkdir()
        self.store = SceneStore(root / "data")
        self.gateway = WorkspaceGateway(self.store, self.project, external_review=True)
        self.session = self.gateway.ensure()["session_id"]
        self.store.replace_scene(1, [{"id": "cabinet", "type": "box", "position": [0, 0, 0], "size": [1, 1, 1]}])

    def reference(self, index: int) -> dict:
        return self.store.add_reference(self.session, f"camera_{index}.png", picture((index * 23, 40, 90)))

    def test_eight_static_references_send_only_active_and_marked_sources(self) -> None:
        references = [self.reference(index) for index in range(1, 9)]
        marked, active = references[2], references[7]
        payload = {
            "scene_revision": 2, "note": "这里柜子位置不对，按圈出的地方改。",
            "active_reference_id": active["id"],
            "annotations": [{"id": "mark3", "pane": "reference", "type": "point",
                             "reference_image_id": marked["id"], "coordinates": {"x": .2, "y": .3}}],
            "reference_annotated_data_urls": [{"reference_id": marked["id"],
                                               "data_url": picture((240, 10, 10))}],
        }
        packet = self.store.submit_feedback(self.session, payload)
        original = copy.deepcopy(packet)
        state_bytes = self.store.state_path.read_bytes()
        plan = model_input_plan(packet, self.store.data_dir)
        self.assertEqual([(item["key"], item["id"]) for item in plan["manifest"]["reference_images"]],
                         [("R3", marked["id"]), ("R8", active["id"])])
        self.assertEqual({"R3", "R8"}, bindings(plan))
        self.assertEqual(len(plan["images"]), 3)  # marked original + marked drawing + active original
        self.assertEqual(plan["manifest"]["annotations"][0]["id"], "mark3")
        direct_text, paths = self.gateway._turn_input(packet)
        self.assertEqual(paths, [item["path"] for item in plan["images"]])
        self.assertIn("R3", direct_text)
        self.assertIn("R8", direct_text)
        result = _visual_tool_result({"items": [packet]}, self.store.data_dir)
        self.assertEqual(sum(part.type == "image" for part in result.content), len(paths))
        self.assertEqual(result.structured_content, json.loads(result.content[0].text))
        captions = [part.text for part in result.content if part.type == "text"][-len(plan["images"]):]
        self.assertEqual(captions, [image_caption(item) for item in plan["images"]])
        self.assertEqual(len(result.structured_content["items"][0]["reference_images"]), 2)
        self.assertNotIn("camera_to_world", result.content[0].text)
        detailed = _visual_tool_result({"items": [packet]}, self.store.data_dir, include_details=True)
        self.assertEqual(len(detailed.structured_content["items"][0]["reference_images"]), 8)
        self.assertEqual(packet, original)
        self.assertEqual(self.store.state_path.read_bytes(), state_bytes)
        self.assertEqual(SceneStore(self.store.data_dir).feedback_by_id(packet["feedback_id"]), original)
        self.assertEqual(plan["manifest"]["details"]["feedback_id"], packet["feedback_id"])

    def test_unmarked_annotation_is_omitted_but_selected_scene_highlight_survives(self) -> None:
        reference = self.reference(1)
        payload = {"scene_revision": 2, "note": "Move the selected cabinet.",
                   "active_reference_id": reference["id"], "selected_object_ids": ["cabinet"],
                   "reference_annotated_data_urls": [{"reference_id": reference["id"],
                                                      "data_url": picture((240, 0, 0))}],
                   "scene_original_data_url": picture((0, 0, 240)),
                   "scene_annotated_data_url": picture((240, 240, 0))}
        packet = self.store.submit_feedback(self.session, payload)
        plan = model_input_plan(packet, self.store.data_dir)
        urls = {image["url"] for image in plan["images"]}
        self.assertIn(packet["scene_annotated_url"], urls)
        self.assertIn(packet["scene_original_url"], urls)
        self.assertNotIn(packet["reference_annotated_images"][0]["url"], urls)
        self.assertEqual(plan["manifest"]["selected_object_ids"], ["cabinet"])
        self.assertEqual([item["key"] for item in plan["manifest"]["reference_images"]], ["R1"])

    def test_cross_view_dynamic_sources_keep_authoritative_view_time_and_revision(self) -> None:
        a0, a1 = picture((10, 10, 10)), picture((20, 20, 20))
        b0, b1 = picture((30, 30, 30)), picture((40, 40, 40))
        self.store.set_reference_clip(self.session, {"name": "camera_A", "fps": 2, "frames": [
            {"time_sec": 0, "data_url": a0}, {"time_sec": .5, "data_url": a1}]})
        clip = self.store.set_reference_clip(self.session, {"append_view": True, "name": "camera_B", "fps": 3,
            "duration_sec": 2.5, "frames": [{"time_sec": 0, "data_url": b0}, {"time_sec": .34, "data_url": b1}]})["reference_clip"]
        primary, secondary = reference_views(clip)
        payload = {"scene_revision": 2, "note": "Compare [[annotation:mark-primary]] across both camera views.",
                   "timeline": {"clip_id": clip["clip_id"], "view_id": secondary["clip_id"],
                                "fps": primary["fps"], "duration_sec": shared_duration(clip),
                                "time_sec": .4, "scope": {"kind": "frame"}},
                   "dynamic_frames": [
                       {"id": f"moment{index}", "view_id": view["clip_id"], "time_sec": .4,
                        "scene_revision": 2, "reference_frame_id": view["frames"][1]["id"], "camera": {},
                        "scene_original_data_url": picture((60 + index * 20, 70, 80)),
                        **({"reference_annotated_data_url": picture((240, 0, 0))} if index == 1 else {})}
                       for index, view in enumerate((primary, secondary), 1)],
                   "annotations": [{"id": "mark-primary", "type": "point", "pane": "reference",
                                    "coordinates": {"x": .2, "y": .3}, "frame_id": "moment1",
                                    "view_id": primary["clip_id"], "clip_id": clip["clip_id"],
                                    "time_sec": .4, "scene_revision": 2,
                                    "reference_image_id": primary["frames"][1]["id"]}]}
        packet = self.store.submit_feedback(self.session, payload)
        plan = model_input_plan(packet, self.store.data_dir)
        sources = plan["manifest"]["dynamic_frames"]
        self.assertEqual([source["key"] for source in sources], ["F1", "F2"])
        self.assertEqual([source["view_id"] for source in sources], [primary["clip_id"], secondary["clip_id"]])
        self.assertEqual([source["reference_time_sec"] for source in sources], [.5, .34])
        self.assertEqual([source["frame_index"] for source in sources], [1, 1])
        self.assertEqual([source["scene_revision"] for source in sources], [2, 2])
        self.assertEqual([source["time_sec"] for source in sources], [.4, .4])
        self.assertEqual([source["reference_frame_id"] for source in sources],
                         [primary["frames"][1]["id"], secondary["frames"][1]["id"]])
        self.assertIn("[[annotation:mark-primary]]", [item["token"] for item in plan["manifest"]["inline_references"]])
        self.assertTrue({"F1", "F2"} <= bindings(plan))
        self.assertEqual(self.gateway._turn_input(packet)[1], [image["path"] for image in plan["images"]])
        self.assertEqual(sum(part.type == "image" for part in _visual_tool_result(
            {"items": [packet]}, self.store.data_dir).content), len(plan["images"]))

    def test_plain_displayed_snapshot_name_selects_exact_source_only(self) -> None:
        packet = self.store.submit_feedback(self.session, {
            "scene_revision": 2, "note": "看看截图2",
            "scene_snapshots": [
                {"id": "older", "name": "截图21", "scene_revision": 2, "camera": {},
                 "scene_original_data_url": picture((21, 20, 20))},
                {"id": "target", "name": "截图2", "scene_revision": 2, "camera": {},
                 "scene_original_data_url": picture((2, 20, 20))},
            ],
        })
        plan = model_input_plan(packet, self.store.data_dir)
        self.assertEqual([source["key"] for source in plan["manifest"]["scene_snapshots"]], ["S2"])
        self.assertEqual(bindings(plan), {"S2"})

    def test_plain_animation_frame_citation_keeps_exact_time_without_index(self) -> None:
        packet = self.store.submit_feedback(self.session, {
            "scene_revision": 2, "note": "截图2 · 片段2.600s · 场景动画",
            "timeline": {"clip_id": None, "time_sec": 0, "duration_sec": 3, "fps": 1,
                         "scope": {"kind": "frame"}},
            "dynamic_frames": [
                {"id": "older", "scene_revision": 2, "time_sec": 1.6, "camera": {},
                 "scene_original_data_url": picture((21, 20, 20))},
                {"id": "target", "scene_revision": 2, "time_sec": 2.6, "camera": {},
                 "scene_original_data_url": picture((2, 20, 20))},
            ],
        })
        self.assertTrue(all("frame_index" not in frame for frame in packet["dynamic_frames"]))
        plan = model_input_plan(packet, self.store.data_dir)
        self.assertEqual([source["key"] for source in plan["manifest"]["dynamic_frames"]], ["F2"])
        self.assertEqual([source["time_sec"] for source in plan["manifest"]["dynamic_frames"]], [2.6])
        self.assertEqual(bindings(plan), {"F2"})

    def test_equal_bytes_bind_multiple_sources_once_and_enabled_overlay_uses_composite(self) -> None:
        same = picture((70, 90, 110))
        reference = self.store.add_reference(self.session, "same.png", same)
        packet = self.store.submit_feedback(self.session, {"scene_revision": 2, "note": "Compare this scene.",
            "active_reference_id": reference["id"], "scene_original_data_url": same})
        duplicate_url = "/media/" + uuid.uuid4().hex + ".png"
        (self.store.media_dir / duplicate_url.rsplit("/", 1)[-1]).write_bytes(
            (self.store.media_dir / packet["scene_original_url"].rsplit("/", 1)[-1]).read_bytes())
        equivalent = copy.deepcopy(packet)
        equivalent["scene_original_url"] = duplicate_url
        plan = model_input_plan(equivalent, self.store.data_dir)
        self.assertEqual(len(plan["images"]), 1)
        self.assertEqual(bindings(plan), {"R1", "scene"})
        direct_text, paths = self.gateway._turn_input(equivalent)
        self.assertEqual(len(paths), 1)
        self.assertIn("R1", direct_text)
        self.assertIn("scene", direct_text)
        self.assertEqual(SceneStore(self.store.data_dir).feedback_by_id(packet["feedback_id"]), packet)

        comparison = {"reference_id": reference["id"], "source": "original", "enabled": True,
                      "opacity": 45, "alignment_exact": False,
                      "rect": {"x": 0, "y": 0, "width": 1, "height": 1}}
        overlay = self.store.submit_feedback(self.session, {"scene_revision": 2, "note": "Check the overlay.",
            "active_reference_id": reference["id"], "camera": {"position": [0, 0, 3]},
            "scene_original_data_url": picture((10, 20, 30)), "comparison": comparison,
            "scene_comparison_data_url": picture((30, 20, 10))})
        overlay_plan = model_input_plan(overlay, self.store.data_dir)
        urls = [image["url"] for image in overlay_plan["images"]]
        self.assertIn(overlay["scene_comparison_url"], urls)
        self.assertIn(overlay["scene_original_url"], urls)
        self.assertFalse(any("comparison_reference" in item["role"] for item in overlay_plan["images"]))
        self.assertEqual(len(urls), len(set(urls)))
        self.assertEqual(SceneStore(self.store.data_dir).feedback_by_id(overlay["feedback_id"]), overlay)

    def test_legacy_scene_crop_remains_available_without_marks(self) -> None:
        packet = self.store.submit_feedback(self.session, {
            "scene_revision": 2, "note": "Look at the scene crop.",
            "scene_original_data_url": picture((5, 10, 15)),
            "crops": [{"source": "scene", "data_url": picture((150, 10, 15))}],
        })
        plan = model_input_plan(packet, self.store.data_dir)
        self.assertEqual(plan["manifest"]["crops"],
                         [{"key": "crop1", "archive": "crops[0]", "source": "scene", "reference_id": None}])
        self.assertIn("crop1", bindings(plan))
        self.assertEqual(self.gateway._turn_input(packet)[1], [image["path"] for image in plan["images"]])
        result = _visual_tool_result({"items": [packet]}, self.store.data_dir)
        self.assertEqual(sum(part.type == "image" for part in result.content), len(plan["images"]))
        self.assertEqual(SceneStore(self.store.data_dir).feedback_by_id(packet["feedback_id"]), packet)

    def test_legacy_screenshot_only_remains_the_scene_evidence(self) -> None:
        packet = self.store.submit_feedback(self.session, {
            "scene_revision": 2, "note": "看看当前场景", "screenshot_data_url": picture((10, 30, 50)),
        })
        archived = copy.deepcopy(packet)
        state_bytes = self.store.state_path.read_bytes()
        self.assertNotIn("scene_original_url", packet)
        self.assertEqual(packet["scene_annotated_url"], packet["screenshot_url"])
        plan = model_input_plan(packet, self.store.data_dir)
        self.assertEqual([(image["source"], image["role"]) for image in plan["images"]],
                         [("scene", "场景截图")])
        message, paths = self.gateway._turn_input(packet)
        self.assertEqual(paths, [plan["images"][0]["path"]])
        self.assertIn("场景截图", message)
        result = _visual_tool_result({"items": [packet]}, self.store.data_dir)
        self.assertEqual(sum(part.type == "image" for part in result.content), 1)
        self.assertEqual(result.structured_content["items"][0]["images"][0]["role"], "场景截图")
        self.assertEqual(packet, archived)
        self.assertEqual(self.store.state_path.read_bytes(), state_bytes)
        self.assertEqual(SceneStore(self.store.data_dir).feedback_by_id(packet["feedback_id"]), archived)


if __name__ == "__main__":
    unittest.main()

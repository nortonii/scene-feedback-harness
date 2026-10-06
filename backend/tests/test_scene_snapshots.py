"""Saved static camera views keep separate pixels, cameras and annotations."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_dynamic_scenes import image_data, calibrated_camera
from core import APIError, SceneStore
from gateway import WorkspaceGateway
import mcp_server


class SceneSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = SceneStore(self.root / 'data')
        self.gateway = WorkspaceGateway(self.store, self.root, external_review=True)
        self.session = self.gateway.ensure()['session_id']

    def payload(self):
        views = []
        marks = []
        for index, color in enumerate(('red', 'blue')):
            _, pixels = image_data(color)
            camera = {'position': [index + 1, 2, 3]}
            views.append({'id': f'view{index}', 'name': f'截图 {index + 1}', 'scene_revision': 1,
                          'camera': camera, 'scene_original_data_url': pixels, 'scene_annotated_data_url': pixels})
            marks.append({'id': f'mark{index}', 'pane': 'scene', 'type': 'point',
                          'coordinates': {'x': .2, 'y': .3}, 'snapshot_id': f'view{index}',
                          'camera': camera, 'scene_revision': 1})
        return {'scene_revision': 1, 'note': 'Compare [[annotation:mark0]] and [[annotation:mark1]].',
                'scene_snapshots': views, 'annotations': marks}

    def test_saved_views_reach_gateway_and_mcp_with_distinct_evidence(self):
        payload = self.payload()
        packet = self.store.submit_feedback(self.session, payload)
        self.assertNotIn('timeline', packet)
        self.assertNotIn('dynamic_frames', packet)
        self.assertEqual(len(packet['scene_snapshots']), 2)
        self.assertEqual(packet['annotations'], payload['annotations'])
        for original, saved in zip(payload['scene_snapshots'], packet['scene_snapshots']):
            self.assertEqual(saved['camera'], original['camera'])
            self.assertNotIn('time_sec', saved)
            self.assertNotIn('scene_original_data_url', saved)
        self.assertNotEqual(packet['scene_snapshots'][0]['scene_original_url'], packet['scene_snapshots'][1]['scene_original_url'])
        message, paths = self.gateway._turn_input(packet)
        self.assertIn('截图 1', message)
        self.assertIn('截图 2', message)
        self.assertEqual(len(paths), 2)  # Each snapshot's original and annotation have identical bytes.
        with patch.object(mcp_server, 'DATA_DIR', self.store.data_dir):
            result = mcp_server._visual_tool_result({'items': [copy.deepcopy(packet)]}, include_details=True)
        self.assertEqual(sum(item.type == 'image' for item in result.content), len(paths))
        images = json.loads(result.content[0].text)['items'][0]['images']
        self.assertEqual([item['source'] for item in images], ['S1', 'S2'])
        self.assertTrue(all(item['aliases'][0]['role'] == '场景标记或高亮' for item in images))
        self.assertTrue(all(view.get('scene_original_path') for view in result.structured_content['items'][0]['scene_snapshots']))
        self.assertEqual(SceneStore(self.store.data_dir).feedback_by_id(packet['feedback_id']), packet)

    def test_rejects_orphaned_marks_camera_mismatch_and_invalid_views(self):
        mutations = [
            lambda p: p['annotations'][0].update(snapshot_id='missing'),
            lambda p: p['annotations'][0].update(camera={'position': [99, 0, 0]}),
            lambda p: p['scene_snapshots'][1].update(id='view0'),
            lambda p: p['scene_snapshots'][0].pop('scene_original_data_url'),
            lambda p: p['scene_snapshots'][0].update(time_sec=0),
            lambda p: p.update(scene_snapshots=[p['scene_snapshots'][0]] * 9),
            lambda p: p.update(scene_snapshots={}),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                payload = self.payload()
                mutate(payload)
                with self.assertRaises(APIError):
                    self.store.submit_feedback(self.session, payload)
        self.assertEqual(self.store.list_all_feedback(), [])

    def test_annotation_names_survive_references_model_prompt_mcp_and_reload(self):
        payload = self.payload()
        for index, mark in enumerate(payload['annotations'], 1):
            mark['name'] = f'点{index}'
        packet = self.store.submit_feedback(self.session, payload)
        self.assertEqual(packet['annotations'], payload['annotations'])
        self.assertEqual([item['annotation']['name'] for item in packet['inline_references']], ['点1', '点2'])
        message, _ = self.gateway._turn_input(packet)
        evidence = json.loads(message.split('证据：', 1)[1].split('\n附件：', 1)[0])
        self.assertEqual([mark['name'] for mark in evidence['annotations']], ['点1', '点2'])
        with patch.object(mcp_server, 'DATA_DIR', self.store.data_dir):
            result = mcp_server._visual_tool_result({'items': [copy.deepcopy(packet)]}, include_details=True)
        self.assertEqual(result.structured_content['items'][0]['annotations'], payload['annotations'])
        saved = SceneStore(self.store.data_dir).feedback_by_id(packet['feedback_id'])
        self.assertEqual(saved['annotations'], payload['annotations'])
        self.assertEqual(saved['inline_references'], packet['inline_references'])

    def overlay_payload(self):
        payload = self.payload()
        _, pixels = image_data('green')
        reference = self.store.add_reference(self.session, 'target.png', pixels)
        comparison = {'reference_id': reference['id'], 'reference_name': 'untrusted name',
                      'source': 'original', 'enabled': True, 'opacity': 45,
                      'alignment_exact': False, 'rect': {'x': 0, 'y': .1, 'width': 1, 'height': .8}}
        payload['scene_snapshots'][0].update(comparison=comparison, scene_comparison_data_url=pixels)
        return payload, reference

    def test_overlay_pixels_metadata_and_originals_reach_both_delivery_modes(self):
        payload, reference = self.overlay_payload()
        # Top-level evidence follows the same contract, including live captures.
        first = payload['scene_snapshots'][0]
        payload.update({key: copy.deepcopy(first[key]) for key in ('camera', 'comparison', 'scene_original_data_url', 'scene_comparison_data_url')})
        packet = self.store.submit_feedback(self.session, payload)
        for saved in (packet, packet['scene_snapshots'][0]):
            self.assertEqual(saved['comparison']['reference_name'], 'target.png')
            self.assertEqual(saved['comparison']['opacity'], 45)
            self.assertEqual(saved['comparison']['rect'], first['comparison']['rect'])
            self.assertEqual(saved['comparison_reference_original_url'], reference['url'])
            self.assertEqual(saved['comparison_reference_url'], reference['url'])
            self.assertNotEqual(saved['scene_comparison_url'], saved['scene_original_url'])
        message, paths = self.gateway._turn_input(packet)
        self.assertIn('叠图重影不是新增物体', message)
        self.assertIn('叠图对比（辅助）', message)
        composite_path = str(self.store.media_dir / packet['scene_snapshots'][0]['scene_comparison_url'].rsplit('/', 1)[-1])
        self.assertTrue(any(Path(path).read_bytes() == Path(composite_path).read_bytes() for path in paths))
        with patch.object(mcp_server, 'DATA_DIR', self.store.data_dir):
            result = mcp_server._visual_tool_result({'items': [copy.deepcopy(packet)]}, include_details=True)
        saved = result.structured_content['items'][0]['scene_snapshots'][0]
        self.assertEqual(saved['scene_comparison_path'], composite_path)
        self.assertTrue(any(content.type == 'text' and 'overlay ghosting is not geometry' in content.text for content in result.content))
        self.assertEqual(SceneStore(self.store.data_dir).feedback_by_id(packet['feedback_id']), packet)

    def test_disabled_overlay_preserves_settings_without_a_misleading_composite(self):
        payload, reference = self.overlay_payload()
        first = payload['scene_snapshots'][0]
        first['comparison']['enabled'] = False
        first.pop('scene_comparison_data_url')
        packet = self.store.submit_feedback(self.session, payload)
        saved = packet['scene_snapshots'][0]
        self.assertFalse(saved['comparison']['enabled'])
        self.assertNotIn('scene_comparison_url', saved)
        self.assertEqual(saved['comparison_reference_original_url'], reference['url'])

    def test_overlay_rejects_missing_reference_metadata_pixels_and_bad_placement(self):
        base, _ = self.overlay_payload()
        mutations = [
            lambda v: v['comparison'].update(reference_id='unknown'),
            lambda v: v['comparison'].update(opacity=101),
            lambda v: v['comparison'].update(opacity=float('nan')),
            lambda v: v['comparison'].update(enabled='true'),
            lambda v: v['comparison'].update(source='undistorted'),
            lambda v: v['comparison'].update(source_url='/media/not-the-saved-source.png'),
            lambda v: v['comparison']['rect'].update(width=-1),
            lambda v: v.pop('scene_comparison_data_url'),
            lambda v: v.pop('comparison'),
            lambda v: v.update(scene_comparison_data_url='not-an-image'),
            lambda v: v['comparison'].update(enabled=False),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                payload = copy.deepcopy(base)
                mutate(payload['scene_snapshots'][0])
                with self.assertRaises(APIError):
                    self.store.submit_feedback(self.session, payload)
        self.assertEqual(self.store.list_all_feedback(), [])

    def test_calibrated_overlay_retains_both_original_and_undistorted_sources(self):
        payload, reference = self.overlay_payload()
        raw, _ = image_data('blue')
        aligned_url = self.store._write_media(raw)
        source = self.store.state['sessions'][self.session]['reference_images'][0]
        source.update(alignment_image_url=aligned_url, camera=calibrated_camera())
        self.store._save()
        payload['scene_snapshots'][0]['comparison'].update(source='undistorted', source_url=aligned_url, alignment_exact=True)
        saved = self.store.submit_feedback(self.session, payload)['scene_snapshots'][0]
        self.assertEqual(saved['comparison_reference_original_url'], reference['url'])
        self.assertEqual(saved['comparison_reference_url'], aligned_url)
        self.assertEqual(saved['comparison']['reference_camera'], calibrated_camera())
        self.assertTrue(saved['comparison']['alignment_exact'])

    def test_composite_must_use_scene_image_dimensions(self):
        import base64
        from io import BytesIO
        from PIL import Image
        payload, _ = self.overlay_payload()
        buffer = BytesIO()
        Image.new('RGB', (32, 24)).save(buffer, format='PNG')
        payload['scene_snapshots'][0]['scene_comparison_data_url'] = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()
        with self.assertRaisesRegex(APIError, 'dimensions'):
            self.store.submit_feedback(self.session, payload)

    def test_dynamic_overlay_keeps_its_own_reference_and_time(self):
        payload, reference = self.overlay_payload()
        frame = payload.pop('scene_snapshots')[0]
        frame.update(time_sec=0, static_reference_id=reference['id'])
        payload.update(annotations=[], note='Compare the saved overlay.', dynamic_frames=[frame],
                       timeline={'clip_id': None, 'time_sec': 0, 'duration_sec': 1, 'fps': 1, 'scope': {'kind': 'frame'}})
        packet = self.store.submit_feedback(self.session, payload)
        saved = packet['dynamic_frames'][0]
        self.assertEqual(saved['comparison']['reference_id'], reference['id'])
        self.assertEqual(saved['time_sec'], 0)
        self.assertTrue(saved['scene_comparison_url'])
        message, paths = self.gateway._turn_input(packet)
        self.assertIn('叠图对比（辅助）', message)
        composite = self.store.media_dir / saved['scene_comparison_url'].rsplit('/', 1)[-1]
        self.assertTrue(any(Path(path).read_bytes() == composite.read_bytes() for path in paths))
        evidence = json.loads(message.split('证据：', 1)[1].split('\n附件：', 1)[0])
        self.assertTrue(any(alias['role'] == '叠图对比（辅助）'
                            for image in evidence['images'] for alias in image.get('aliases', [])))

    def test_mixed_static_and_dynamic_versions_preserve_oldest_revision(self):
        self.store.state['scene']['revision'] = 2
        self.store._save()
        payload = self.payload()
        payload.update(confirm_stale=True, latest_scene_revision=2,
                       timeline={'clip_id': None, 'time_sec': 0, 'duration_sec': 1, 'fps': 1, 'scope': {'kind': 'frame'}},
                       dynamic_frames=[{**payload['scene_snapshots'][1], 'id': 'moment', 'time_sec': 0, 'scene_revision': 2}])
        packet = self.store.submit_feedback(self.session, payload)
        self.assertEqual(packet['scene_revision'], 1)
        self.assertEqual(packet['dynamic_frames'][0]['scene_revision'], 2)
        self.assertEqual(packet['scene_snapshots'][0]['scene_revision'], 1)


if __name__ == '__main__':
    unittest.main()

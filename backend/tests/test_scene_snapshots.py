"""Saved static camera views keep separate pixels, cameras and annotations."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_dynamic_scenes import image_data
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
        self.assertEqual(len(paths), 4)
        with patch.object(mcp_server, 'DATA_DIR', self.store.data_dir):
            result = mcp_server._visual_tool_result({'items': [copy.deepcopy(packet)]})
        self.assertEqual(sum(item.type == 'image' for item in result.content), 4)
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

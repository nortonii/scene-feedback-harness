"""Pixel and persistence checks for the browser's exported overlay evidence."""
import base64
from io import BytesIO

from PIL import Image


def verify_overlay_payload(sent, first_view, store):
    def decode(data):
        return Image.open(BytesIO(base64.b64decode(data.split(',', 1)[1]))).convert('RGB')

    first, second = sent['scene_snapshots']
    assert first['comparison']['enabled'] and first['comparison']['opacity'] == 25
    assert first['comparison']['reference_id'] == first_view['comparison']['reference_id']
    assert first['comparison']['rect'] == first_view['comparison']['rect']
    assert not second['comparison']['enabled']
    assert 'scene_comparison_data_url' not in second
    assert 'data_url' not in first['comparison'], 'Pixel blobs must not leak into model metadata'
    scene = decode(first['scene_original_data_url'])
    actual = decode(first['scene_comparison_data_url'])
    reference = decode(first_view['comparison']['data_url'])
    assert actual.size == scene.size
    rect = first['comparison']['rect']
    w, h = scene.size
    rw, rh = round(rect['width'] * w), round(rect['height'] * h)
    x, y = round(rect['x'] * w), round(rect['y'] * h)
    reference = reference.resize((rw, rh), Image.Resampling.BILINEAR)
    layer = scene.copy()
    layer.paste(reference, (x, y))
    expected = Image.blend(scene, layer, .25)
    errors, differences = [], []
    for row in range(1, 20):
        for column in range(1, 20):
            pos = (w * column // 20, h * row // 20)
            got, want, clean = actual.getpixel(pos), expected.getpixel(pos), scene.getpixel(pos)
            errors.append(max(abs(a - b) for a, b in zip(got, want)))
            differences.append(max(abs(a - b) for a, b in zip(got, clean)))
    # Ignore the few samples occupied by a red user mark or resampling boundary.
    assert sum(error < 12 for error in errors) / len(errors) > .9
    assert sum(difference > 5 for difference in differences) > 20, 'Export omitted the reference overlay'
    annotated = decode(first['scene_annotated_data_url'])
    marks = [mark for mark in sent['annotations'] if mark.get('snapshot_id') == first['id']]
    assert marks
    mark = marks[0]
    px, py = round(mark['coordinates']['x'] * w), round(mark['coordinates']['y'] * h)
    red = lambda rgb: rgb[0] > 130 and rgb[0] > rgb[1] * 1.4 and rgb[0] > rgb[2] * 1.4
    assert any(red(actual.getpixel((cx, cy))) and red(annotated.getpixel((cx, cy)))
               for cx in range(max(0, px-12), min(w, px+13))
               for cy in range(max(0, py-12), min(h, py+13))), 'Composite lost its annotation'
    packet = store.list_all_feedback()[0]
    stored = packet['scene_snapshots'][0]
    assert stored['comparison']['opacity'] == 25
    path = store.media_dir / stored['scene_comparison_url'].rsplit('/', 1)[-1]
    with Image.open(path) as saved:
        assert saved.size == actual.size
        assert saved.convert('RGB').tobytes() == actual.tobytes()

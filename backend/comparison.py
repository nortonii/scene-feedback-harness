"""Validated reference overlays attached to frozen scene evidence."""
from __future__ import annotations

import copy
from io import BytesIO
from typing import Any

from PIL import Image

from core import APIError


def prepare_comparison(store: Any, session: dict, item: dict) -> tuple[dict, dict]:
    from dynamic import number, reference_views

    value = item.get('comparison')
    pixels = item.get('scene_comparison_data_url')
    if value is None:
        if pixels is not None:
            raise APIError(400, 'comparison image requires comparison metadata')
        return {}, {}
    if not isinstance(value, dict):
        raise APIError(400, 'comparison must be an object')
    references = list(session.get('reference_images', []))
    for view in reference_views(session.get('reference_clip')):
        references.extend(view['frames'])
    reference_id = value.get('reference_id')
    reference = next((ref for ref in references if ref['id'] == reference_id), None)
    if reference is None:
        raise APIError(400, 'comparison reference is no longer available in this session')
    source = value.get('source')
    if source not in ('original', 'undistorted') or (source == 'undistorted' and not reference.get('alignment_image_url')):
        raise APIError(400, 'comparison reference source is invalid')
    source_url = reference['alignment_image_url'] if source == 'undistorted' else reference['url']
    if value.get('source_url', source_url) != source_url:
        raise APIError(400, 'comparison source changed since its screenshot was captured')
    enabled = value.get('enabled')
    exact = value.get('alignment_exact')
    if type(enabled) is not bool or type(exact) is not bool:
        raise APIError(400, 'comparison enabled and alignment_exact must be boolean')
    opacity = number(value.get('opacity'), 'comparison opacity', maximum=100)
    rect = value.get('rect')
    if not isinstance(rect, dict) or set(rect) != {'x', 'y', 'width', 'height'}:
        raise APIError(400, 'comparison rect must specify x, y, width and height')
    rect = {key: number(rect[key], 'comparison rect ' + key,
                       minimum=-16 if key in ('x', 'y') else .000001,
                       maximum=16) for key in ('x', 'y', 'width', 'height')}
    if not isinstance(item.get('camera'), dict) or not item.get('scene_original_data_url'):
        raise APIError(400, 'comparison requires its original scene image and camera')
    metadata = {'reference_id': reference_id, 'reference_name': reference.get('name', reference_id),
                'source': source, 'source_url': source_url, 'enabled': enabled, 'opacity': opacity, 'rect': rect,
                'alignment_exact': exact, 'coordinate_space': 'normalized_scene_image'}
    if reference.get('camera'):
        metadata['reference_camera'] = copy.deepcopy(reference['camera'])
    fields = {'comparison': metadata,
              'comparison_reference_original_url': reference['url'],
              'comparison_reference_url': reference['alignment_image_url'] if source == 'undistorted' else reference['url']}
    images = {}
    if enabled and opacity > 0:
        if pixels is None:
            raise APIError(400, 'enabled comparison requires its annotated composite image')
        composite = store._decode_image_data_url(pixels)
        original = store._decode_image_data_url(item['scene_original_data_url'])
        with Image.open(BytesIO(composite)) as overlay, Image.open(BytesIO(original)) as scene:
            if overlay.size != scene.size:
                raise APIError(400, 'comparison image must match the original scene dimensions')
        images['scene_comparison'] = composite
    elif pixels is not None:
        raise APIError(400, 'disabled comparison cannot contain a composite image')
    return fields, images

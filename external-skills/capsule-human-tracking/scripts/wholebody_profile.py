"""Canonical COCO-WholeBody133 observation order and named topology.

Source: MMPose's COCO-WholeBody dataset metainfo. This module is pure Python;
loading its schema never loads Torch, a model checkpoint, or image data.
"""

from __future__ import annotations


WHOLEBODY133_PROFILE = "coco-wholebody133"
COCO17_PROFILE = "coco17"

COCO17_NAMES = (
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_hip", "right_hip",
    "left_knee", "right_knee", "left_ankle", "right_ankle",
)
FOOT6_NAMES = (
    "left_big_toe", "left_small_toe", "left_heel",
    "right_big_toe", "right_small_toe", "right_heel",
)
FACE68_NAMES = tuple(f"face-{index}" for index in range(68))
HAND21_SUFFIXES = (
    "hand_root",
    *(f"thumb{index}" for index in range(1, 5)),
    *(f"forefinger{index}" for index in range(1, 5)),
    *(f"middle_finger{index}" for index in range(1, 5)),
    *(f"ring_finger{index}" for index in range(1, 5)),
    *(f"pinky_finger{index}" for index in range(1, 5)),
)
LEFT_HAND21_NAMES = tuple("left_" + name for name in HAND21_SUFFIXES)
RIGHT_HAND21_NAMES = tuple("right_" + name for name in HAND21_SUFFIXES)
WHOLEBODY133_NAMES = COCO17_NAMES + FOOT6_NAMES + FACE68_NAMES + LEFT_HAND21_NAMES + RIGHT_HAND21_NAMES

BODY17_INDICES = tuple(range(0, 17))
FOOT6_INDICES = tuple(range(17, 23))
FACE68_INDICES = tuple(range(23, 91))
LEFT_HAND21_INDICES = tuple(range(91, 112))
RIGHT_HAND21_INDICES = tuple(range(112, 133))
COCO17_TO_WHOLEBODY133 = {name: index for index, name in enumerate(COCO17_NAMES)}
WHOLEBODY_HAND_ROOT_TO_BODY_WRIST = {91: 9, 112: 10}

WHOLEBODY133_GROUPS = {
    "body": list(BODY17_INDICES),
    "feet": list(FOOT6_INDICES),
    "face": list(FACE68_INDICES),
    "left_hand": list(LEFT_HAND21_INDICES),
    "right_hand": list(RIGHT_HAND21_INDICES),
}

_BODY_FOOT_EDGE_NAMES = (
    ("left_ankle", "left_knee"), ("left_knee", "left_hip"),
    ("right_ankle", "right_knee"), ("right_knee", "right_hip"),
    ("left_hip", "right_hip"), ("left_shoulder", "left_hip"),
    ("right_shoulder", "right_hip"), ("left_shoulder", "right_shoulder"),
    ("left_shoulder", "left_elbow"), ("right_shoulder", "right_elbow"),
    ("left_elbow", "left_wrist"), ("right_elbow", "right_wrist"),
    ("left_eye", "right_eye"), ("nose", "left_eye"),
    ("nose", "right_eye"), ("left_eye", "left_ear"),
    ("right_eye", "right_ear"), ("left_ear", "left_shoulder"),
    ("right_ear", "right_shoulder"),
    ("left_ankle", "left_big_toe"), ("left_ankle", "left_small_toe"),
    ("left_ankle", "left_heel"), ("right_ankle", "right_big_toe"),
    ("right_ankle", "right_small_toe"), ("right_ankle", "right_heel"),
)
_HAND_EDGE_NAMES = tuple(
    (f"{side}_hand_root" if index == 0 else f"{side}_{finger}{index}", f"{side}_{finger}{index + 1}")
    for side in ("left", "right")
    for finger in ("thumb", "forefinger", "middle_finger", "ring_finger", "pinky_finger")
    for index in range(0, 4)
)
_NAME_TO_INDEX = {name: index for index, name in enumerate(WHOLEBODY133_NAMES)}
WHOLEBODY133_EDGES = tuple(
    (_NAME_TO_INDEX[left], _NAME_TO_INDEX[right])
    for left, right in _BODY_FOOT_EDGE_NAMES + _HAND_EDGE_NAMES
)
HAND_EDGES = tuple((0 if index == 0 else base + index - 1, base + index)
                   for base in (1, 5, 9, 13, 17) for index in range(4))
# Short aliases for the reconstruction adapter; indices in HAND_EDGES are
# local to each 21-joint hand, while SKELETON_EDGES index all 133 joints.
KEYPOINT_NAMES = WHOLEBODY133_NAMES
SKELETON_EDGES = WHOLEBODY133_EDGES


def wholebody133_topology() -> dict:
    """A fresh JSON-ready profile descriptor for source-bound pose results."""
    return {
        "keypoint_profile": WHOLEBODY133_PROFILE,
        "keypoint_names": list(WHOLEBODY133_NAMES),
        "skeleton_edges": [list(edge) for edge in WHOLEBODY133_EDGES],
        "keypoint_groups": {name: list(indices) for name, indices in WHOLEBODY133_GROUPS.items()},
    }


assert len(WHOLEBODY133_NAMES) == len(set(WHOLEBODY133_NAMES)) == 133
assert len(WHOLEBODY133_EDGES) == 65
assert len(HAND_EDGES) == 20

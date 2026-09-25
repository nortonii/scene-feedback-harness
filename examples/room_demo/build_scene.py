"""Build a self-contained GLB from editable room_demo/scene.json parameters.

This small deterministic builder exists only to demonstrate a real Codex edit:
change cabinet.center/size in scene.json, run this script, then publish the GLB
through workspace_publish_scene. It is not part of the reconstruction workflow.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent


def positive_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0 or value > 100:
        raise ValueError(f"{name} must be a positive finite number at most 100")
    return float(value)


def vector(value: Any, name: str, *, positive: bool = False) -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{name} must be a three-number array")
    result = []
    for index, item in enumerate(value):
        if positive:
            result.append(positive_number(item, f"{name}[{index}]"))
        elif isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item) or abs(item) > 100:
            raise ValueError(f"{name}[{index}] must be a finite number between -100 and 100")
        else:
            result.append(float(item))
    return result


def color(value: Any) -> list[float]:
    if not isinstance(value, str) or len(value) != 7 or not value.startswith("#"):
        raise ValueError("cabinet.color must be #rrggbb")
    try:
        return [int(value[index:index + 2], 16) / 255 for index in (1, 3, 5)] + [1.0]
    except ValueError as exc:
        raise ValueError("cabinet.color must be #rrggbb") from exc


def box_geometry(size: list[float]) -> tuple[list[float], list[float], list[int]]:
    x, y, z = (number / 2 for number in size)
    faces = [
        ((0, 0, 1), [(-x, -y, z), (x, -y, z), (x, y, z), (-x, y, z)]),
        ((0, 0, -1), [(-x, y, -z), (x, y, -z), (x, -y, -z), (-x, -y, -z)]),
        ((1, 0, 0), [(x, -y, -z), (x, y, -z), (x, y, z), (x, -y, z)]),
        ((-1, 0, 0), [(-x, y, -z), (-x, -y, -z), (-x, -y, z), (-x, y, z)]),
        ((0, 1, 0), [(x, y, -z), (-x, y, -z), (-x, y, z), (x, y, z)]),
        ((0, -1, 0), [(-x, -y, -z), (x, -y, -z), (x, -y, z), (-x, -y, z)]),
    ]
    positions: list[float] = []
    normals: list[float] = []
    indices: list[int] = []
    for face_index, (normal, corners) in enumerate(faces):
        for corner in corners:
            positions.extend(corner)
            normals.extend(normal)
        offset = face_index * 4
        indices.extend((offset, offset + 1, offset + 2, offset, offset + 2, offset + 3))
    return positions, normals, indices


def build(source: Path, output: Path) -> Path:
    parameters = json.loads(source.read_text(encoding="utf-8"))
    room = parameters["room"]
    cabinet = parameters["cabinet"]
    width = positive_number(room["width"], "room.width")
    depth = positive_number(room["depth"], "room.depth")
    height = positive_number(room["height"], "room.height")
    cabinet_center = vector(cabinet["center"], "cabinet.center")
    cabinet_size = vector(cabinet["size"], "cabinet.size", positive=True)
    cabinet_color = color(cabinet["color"])
    if not (cabinet_size[0] < width and cabinet_size[1] < depth and cabinet_size[2] < height):
        raise ValueError("cabinet size must fit inside the room")
    if (abs(cabinet_center[0]) + cabinet_size[0] / 2 > width / 2
        or abs(cabinet_center[1]) + cabinet_size[1] / 2 > depth / 2
        or cabinet_center[2] - cabinet_size[2] / 2 < 0
        or cabinet_center[2] + cabinet_size[2] / 2 > height):
        raise ValueError("cabinet must be contained within the room")

    boxes = [
        ("Floor", [0, 0, -0.055], [width, depth, 0.11], [0.70, 0.71, 0.68, 1]),
        ("Back_Wall", [0, depth / 2 + 0.055, height / 2], [width, 0.11, height], [0.90, 0.86, 0.77, 1]),
        ("Left_Wall", [-width / 2 - 0.055, 0, height / 2], [0.11, depth, height], [0.78, 0.82, 0.84, 1]),
        ("Cabinet", cabinet_center, cabinet_size, cabinet_color),
    ]
    document: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "scene-feedback-harness room demo"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(boxes)))}],
        "nodes": [], "meshes": [], "materials": [], "accessors": [], "bufferViews": [], "buffers": [],
    }
    binary = bytearray()

    def add_blob(data: bytes, target: int | None = None) -> int:
        while len(binary) % 4:
            binary.append(0)
        offset = len(binary)
        binary.extend(data)
        view: dict[str, int] = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
        if target is not None:
            view["target"] = target
        document["bufferViews"].append(view)
        return len(document["bufferViews"]) - 1

    def add_accessor(view: int, component_type: int, count: int, kind: str, *, minimum: list[float] | None = None, maximum: list[float] | None = None) -> int:
        accessor: dict[str, Any] = {"bufferView": view, "componentType": component_type, "count": count, "type": kind}
        if minimum is not None:
            accessor["min"] = minimum
        if maximum is not None:
            accessor["max"] = maximum
        document["accessors"].append(accessor)
        return len(document["accessors"]) - 1

    for name, center, size, rgba in boxes:
        positions, normals, indices = box_geometry(size)
        position_view = add_blob(struct.pack(f"<{len(positions)}f", *positions), 34962)
        normal_view = add_blob(struct.pack(f"<{len(normals)}f", *normals), 34962)
        index_view = add_blob(struct.pack(f"<{len(indices)}H", *indices), 34963)
        position_accessor = add_accessor(position_view, 5126, 24, "VEC3", minimum=[-axis / 2 for axis in size], maximum=[axis / 2 for axis in size])
        normal_accessor = add_accessor(normal_view, 5126, 24, "VEC3")
        index_accessor = add_accessor(index_view, 5123, 36, "SCALAR", minimum=[0], maximum=[23])
        material_index = len(document["materials"])
        document["materials"].append({"name": name, "pbrMetallicRoughness": {"baseColorFactor": rgba, "metallicFactor": 0, "roughnessFactor": 0.83}, "doubleSided": True})
        mesh_index = len(document["meshes"])
        document["meshes"].append({"name": name, "primitives": [{"attributes": {"POSITION": position_accessor, "NORMAL": normal_accessor}, "indices": index_accessor, "material": material_index}]})
        document["nodes"].append({"name": name, "mesh": mesh_index, "translation": center})

    while len(binary) % 4:
        binary.append(0)
    document["buffers"].append({"byteLength": len(binary)})
    json_bytes = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * (-len(json_bytes) % 4)
    total_length = 12 + 8 + len(json_bytes) + 8 + len(binary)
    glb = struct.pack("<4sII", b"glTF", 2, total_length)
    glb += struct.pack("<I4s", len(json_bytes), b"JSON") + json_bytes
    glb += struct.pack("<I4s", len(binary), b"BIN\x00") + binary
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(glb)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=HERE / "scene.json")
    parser.add_argument("--out", type=Path, default=HERE / "output" / "current.glb")
    args = parser.parse_args()
    print(build(args.source, args.out).resolve())


if __name__ == "__main__":
    main()

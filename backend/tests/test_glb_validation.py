"""GLB publish-boundary tests independent of the larger backend suite."""

from __future__ import annotations

import json
import io
import struct
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import APIError, SceneStore  # noqa: E402


def make_glb(document: dict, *, bin_bytes: bytes | None = None, extra_chunks: tuple[tuple[bytes, bytes], ...] = ()) -> bytes:
    encoded = json.dumps(document, separators=(",", ":")).encode("utf-8")
    encoded += b" " * (-len(encoded) % 4)
    chunks = [(b"JSON", encoded)]
    if bin_bytes is not None:
        chunks.append((b"BIN\x00", bin_bytes + bytes(-len(bin_bytes) % 4)))
    chunks.extend(extra_chunks)
    body = b"".join(struct.pack("<I4s", len(payload), kind) + payload for kind, payload in chunks)
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def simple_document() -> dict:
    return {"asset": {"version": "2.0"}, "scenes": [{}], "scene": 0}


def geometry_document() -> dict:
    return {
        **simple_document(),
        "buffers": [{"byteLength": 12}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 12}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 1, "type": "VEC3"}],
    }


class GLBValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = SceneStore(self.root / "data")
        self.path = self.root / "candidate.glb"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def validate(self, data: bytes) -> Path:
        self.path.write_bytes(data)
        return self.store._validate_glb_source(str(self.path))

    def rejects(self, data: bytes, message: str) -> None:
        before = self.store.scene()
        self.path.write_bytes(data)
        with self.assertRaisesRegex(APIError, message):
            self.store.set_scene_preview(str(self.path))
        self.assertEqual(self.store.scene(), before)
        self.assertEqual(list(self.store.assets_dir.iterdir()), [])

    def test_minimal_and_bin_backed_glbs_are_accepted(self) -> None:
        self.assertEqual(self.validate(make_glb(simple_document())), self.path.resolve())
        geometry = make_glb(geometry_document(), bin_bytes=struct.pack("<fff", 1, 2, 3))
        self.assertEqual(self.validate(geometry), self.path.resolve())
        self.assertEqual(self.store.set_scene_preview(str(self.path))["objects"][0]["id"], "scene_preview")
        # Khronos permits unknown chunks following the JSON/BIN pair.
        self.assertEqual(self.validate(make_glb(geometry_document(), bin_bytes=struct.pack("<fff", 1, 2, 3), extra_chunks=((b"XTRA", b"abcd"),))), self.path.resolve())

    def test_external_and_data_uris_are_rejected_before_publish(self) -> None:
        cases = (
            {**simple_document(), "buffers": [{"byteLength": 12, "uri": "https://example.org/geometry.bin"}]},
            {**simple_document(), "images": [{"uri": "textures/cabinet.png"}]},
            {**simple_document(), "images": [{"uri": "data:image/png;base64,iVBORw0KGgo="}]},
            {**simple_document(), "extensions": {"EXT_example": {"uri": "https://example.org/texture"}}},
        )
        for document in cases:
            with self.subTest(document=document):
                self.rejects(make_glb(document), "URI resources are not allowed, including data URIs")

    def test_chunk_framing_and_bin_declaration_are_checked(self) -> None:
        geometry = make_glb(geometry_document(), bin_bytes=bytes(12))
        with self.subTest("trailing incomplete chunk header"):
            malformed = geometry + b"BAD"
            malformed = malformed[:8] + struct.pack("<I", len(malformed)) + malformed[12:]
            self.rejects(malformed, "truncated chunk header")
        with self.subTest("chunk length outside file"):
            malformed = geometry[:12] + struct.pack("<I", 0xFFFFFFFC) + geometry[16:]
            self.rejects(malformed, "invalid chunk length")
        with self.subTest("BIN duplicated"):
            self.rejects(make_glb(geometry_document(), bin_bytes=bytes(12), extra_chunks=((b"BIN\x00", bytes(4)),)), "BIN chunk must occur only as the second")
        with self.subTest("BIN after unknown chunk"):
            self.rejects(make_glb(geometry_document(), extra_chunks=((b"XTRA", bytes(4)), (b"BIN\x00", bytes(12)))), "BIN chunk must occur only as the second")
        with self.subTest("declared buffer without BIN"):
            self.rejects(make_glb(geometry_document()), "BIN chunk is missing")
        with self.subTest("nonzero padding"):
            document = {**simple_document(), "buffers": [{"byteLength": 5}]}
            self.rejects(make_glb(document, bin_bytes=b"12345\x01\x00\x00"), "BIN padding must be zero")

    def test_buffer_view_accessor_and_image_bounds_are_checked(self) -> None:
        binary = struct.pack("<fff", 1, 2, 3)
        cases = (
            ({**geometry_document(), "bufferViews": [{"buffer": 0, "byteOffset": 8, "byteLength": 12}]}, "bufferView 0 exceeds"),
            ({**geometry_document(), "bufferViews": [{"buffer": 1, "byteLength": 12}]}, "bufferView 0.buffer index is out of bounds"),
            ({**geometry_document(), "accessors": [{"bufferView": 2, "componentType": 5126, "count": 1, "type": "VEC3"}]}, "accessor 0.bufferView index is out of bounds"),
            ({**geometry_document(), "accessors": [{"bufferView": 0, "componentType": 5126, "count": 2, "type": "VEC3"}]}, "accessor 0 exceeds its bufferView"),
            ({**geometry_document(), "images": [{"bufferView": 5, "mimeType": "image/png"}]}, "image 0.bufferView index is out of bounds"),
            ({**geometry_document(), "images": [{"bufferView": 0, "mimeType": "image/png"}]}, "image 0 bytes do not match mimeType"),
        )
        for document, message in cases:
            with self.subTest(message=message):
                self.rejects(make_glb(document, bin_bytes=binary), message)

    def test_sparse_accessor_bounds_are_checked(self) -> None:
        document = geometry_document()
        document["bufferViews"] = [
            {"buffer": 0, "byteLength": 4},
            {"buffer": 0, "byteOffset": 4, "byteLength": 8},
        ]
        document["accessors"] = [{
            "componentType": 5126, "count": 1, "type": "SCALAR",
            "sparse": {"count": 1, "indices": {"bufferView": 0, "componentType": 5125, "byteOffset": 1}, "values": {"bufferView": 1}},
        }]
        self.rejects(make_glb(document, bin_bytes=bytes(12)), "sparse.indices exceeds its bufferView")

    def test_embedded_image_must_decode_not_just_have_a_signature(self) -> None:
        output = io.BytesIO()
        Image.new("RGB", (2, 2), "red").save(output, format="PNG")
        png = output.getvalue()
        document = {
            **simple_document(), "buffers": [{"byteLength": len(png)}],
            "bufferViews": [{"buffer": 0, "byteLength": len(png)}],
            "images": [{"bufferView": 0, "mimeType": "image/png"}],
        }
        self.assertEqual(self.validate(make_glb(document, bin_bytes=png)), self.path.resolve())
        short = b"\x89PNG\r\n\x1a\n"
        document["buffers"][0]["byteLength"] = len(short)
        document["bufferViews"][0]["byteLength"] = len(short)
        self.rejects(make_glb(document, bin_bytes=short), "image 0 bytes do not match mimeType")

    def test_copy_gate_checks_exact_published_bytes_and_cleans_failure(self) -> None:
        self.path.write_bytes(make_glb({**simple_document(), "images": [{"uri": "https://example.org/external.png"}]}))
        with self.assertRaisesRegex(APIError, "URI resources"):
            self.store._copy_glb(self.path)
        self.assertEqual(list(self.store.assets_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()

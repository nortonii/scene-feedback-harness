"""Create an isolated workspace with the demo reference and initial GLB.

Run before starting the server, then use the same --data-dir when starting it.
The script leaves an existing demo scene and reference untouched.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
sys.path.insert(0, str(PROJECT / "backend"))

from core import SceneStore  # noqa: E402
from build_scene import build  # noqa: E402


def main() -> None:
    data_dir = HERE / "output" / "data"
    store = SceneStore(data_dir)
    workspace = store.ensure_workspace(PROJECT)
    session_id = workspace["session_id"]
    if not store.get_session(session_id)["reference_images"]:
        raw = (HERE / "reference.png").read_bytes()
        store.add_reference(session_id, "reference.png", "data:image/png;base64," + base64.b64encode(raw).decode("ascii"))
    scene = store.scene()
    if not scene["objects"]:
        glb = build(HERE / "scene.json", HERE / "output" / "current.glb")
        scene = store.set_scene_preview(str(glb), expected_revision=scene["revision"])
    print(f"Demo data: {data_dir}\nSession: {session_id}\nScene revision: {scene['revision']}")


if __name__ == "__main__":
    main()

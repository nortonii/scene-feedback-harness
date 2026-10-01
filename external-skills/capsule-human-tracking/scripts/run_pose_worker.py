#!/usr/bin/env python3
"""Run one ViTPose job; stdout is reserved for progress JSON lines."""

from __future__ import annotations

import argparse
import contextlib
import json
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pose_worker import run_inference  # noqa: E402
from wholebody_profile import COCO17_PROFILE, WHOLEBODY133_PROFILE  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Infer observed WholeBody133 keypoints from an exported workbench source manifest")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-path", type=Path, required=True, help="Local ViTPose+ Base checkpoint directory")
    parser.add_argument("--detector-path", type=Path, help="Local official Faster R-CNN COCO .pth for automatic source tracking")
    parser.add_argument("--profile", choices=(WHOLEBODY133_PROFILE, COCO17_PROFILE),
                        default=WHOLEBODY133_PROFILE, help="WholeBody133 by default; coco17 is explicit legacy mode")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.expanduser().resolve(strict=True).read_text(encoding="utf-8"))
    project_dir = manifest.get("project_dir")
    if not isinstance(project_dir, str) or not project_dir:
        parser.error("source export needs its actual project_dir")
    project = Path(project_dir).expanduser().resolve(strict=True)
    output = args.output.expanduser().resolve()
    if not output.is_relative_to(project):
        parser.error("result_path must stay inside the exported project_dir")
    if output.exists():
        parser.error("output already exists; choose a new result path")
    progress_stream = sys.stdout

    def progress(value):
        progress_stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
        progress_stream.flush()

    try:
        # Third-party loader output belongs on stderr; stdout contains progress
        # JSON lines that callers can inspect or capture independently.
        with contextlib.redirect_stdout(sys.stderr):
            run_inference(args.manifest, output, model_path=args.model_path,
                          detector_path=args.detector_path, auto_detect=args.detector_path is not None,
                          profile=args.profile,
                          progress=progress)
        return 0
    except Exception as exc:
        progress({"type": "error", "error": str(exc)[:1000]})
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

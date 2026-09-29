#!/usr/bin/env python3
"""Run one ViTPose job; stdout is reserved for progress JSON lines."""

from __future__ import annotations

import argparse
import contextlib
import json
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from pose_worker import run_inference  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Infer 17 COCO keypoints inside manually supplied human ROIs")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-path", type=Path)
    args = parser.parse_args()
    progress_stream = sys.stdout

    def progress(value):
        progress_stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
        progress_stream.flush()

    try:
        # Third-party loader output belongs on stderr, never in the progress
        # protocol parsed by the HTTP job manager.
        with contextlib.redirect_stdout(sys.stderr):
            run_inference(args.manifest, args.output, model_path=args.model_path, progress=progress)
        return 0
    except Exception as exc:
        progress({"type": "error", "error": str(exc)[:1000]})
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

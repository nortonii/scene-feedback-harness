# Workbench source frames → 2D human evidence → capsule handoff

This is the **offline observer** for Scene Feedback's light workbench. The MCP plugin exports source identity, accepts a checked result, and displays it; inference runs only when an agent explicitly invokes this skill. Use the source images, frame times, camera metadata, and project directory in the returned export. Do not substitute a case-specific clip, camera array, or workspace path.

For the commands below, set `capsule_skill_dir=/absolute/path/to/capsule-human-tracking` to the installed skill directory. Replace `/actual/project/...` with paths returned by the **current** source export.

## Export and inspect

1. Call `workspace_export_pose_sources` in the current Codex task. Keep its `job_id`, `manifest_json_path`, `project_dir`, and `source_snapshot_id`. An optional `request_id` makes retrying the same export idempotent. The export contains every imported source frame from every current view, or all static reference images when no clip is present. It does **not** start a GPU job.
2. Inspect the returned manifest and run:

```bash
python3 "$capsule_skill_dir/scripts/pose_evidence.py" \
  source-export --manifest /actual/project/pose_sources/manifest.json
```

The summary reports exact project/session/snapshot/job/track identity, view counts, and the presence of per-frame cameras. This command verifies the exported image bytes against each `image_sha256`, dimensions against the EXIF-oriented display image, frame order, and source binding **before importing Torch**. A source image replacement, missing file, or changed EXIF orientation requires a fresh export. `image_orientation` is `exif_oriented_display`; normalized coordinates refer to that displayed image.

The exported manifest is algorithm-neutral: it has no preferred detector, ROI, model, or keypoint format. Its `schema_version` is 1 for one view and 2 for multiple views. `frames` have exact `ref_id`, `view_id`, `frame_index`, `time_seconds`, `image_path`, `width`, `height`, `image_sha256`, and `image_orientation`; any `camera` belongs to the actual source, not a guessed calibration. Use `project_dir` from this manifest for the result path because the importer only accepts a server-local path under this directory. If the inference process runs on another machine, stage source files with preserved bytes and return the result JSON through an authorized transfer, then use the `result` object import form; the original export identity remains authoritative.

## Infer COCO17 joints explicitly

Use a Python environment with compatible Torch and torchvision, plus `scripts/requirements-pose.txt`. Point to an **already available** ViTPose+ Base checkpoint directory and the official Faster R-CNN MobileNet V3 320 COCO detector checkpoint. The skill does not install or download them during inference. Checkpoint paths are explicit; none are tied to a reconstruction case.

```bash
/path/to/pose-python "$capsule_skill_dir/scripts/pose_evidence.py" \
  track --manifest /actual/project/pose_sources/manifest.json \
  --output /actual/project/pose_results/vitpose-observed.json \
  --model-path /path/to/vitpose-plus-base \
  --detector-path /path/to/fasterrcnn_mobilenet_v3_large_320_fpn-907ea3f9.pth
```

The detector chooses one prominent person **independently in each view** using detection confidence, area, and later ROI overlap; it redetects periodically or after tracking loss. It estimates every exported source frame at the imported frame rate. Multiple people can cause identity switches, including across views. A long sequence can take substantial time; progress JSON lines report completed and total frames. The output is `observed_2d` evidence with `provenance.kind: image_inference`, model/checkpoint provenance, 17 named COCO joints, source binding fields and hashes, and per-frame normalized boxes/joints. It is a 2D estimate, not a measured 3D body or validated cross-camera person identity.

If a person ROI is already independently known for every frame, the low-level `run_pose_worker.py` accepts source manifests with explicit pixel `bbox_xywh` and `--model-path` without `--detector-path`. Do not add an ROI to the workbench's neutral exported manifest in place; make a separate local working copy that retains exact source identity, and validate the resulting JSON against the original export before import.

## Adapt an existing 2D observation file

Use this route when a real 2D tracker or human annotation already produced image observations. Do **not** convert the capsule's MHR127 3D joints by projection and label them observations. A projected model must declare `evidence_kind: projected_3d` and its actual projection provenance.

`adapt-observations` accepts a JSON object like this, with **one frame for each exported source frame in the same order** (all views). Replace the illustrated identifiers, times, dimensions, hashes, joint names, pixel coordinates, and source artifact with actual values. The original observation file must explicitly bind to the project/session/snapshot and each image hash/orientation; this prevents silently assigning annotations from different image bytes to a fresh export. All joints in `keypoint_names` must appear in each frame in that order. A missing person uses `bbox_xywh: null`, zero scores, and out-of-frame joints.

```json
{
  "evidence_kind": "observed_2d",
  "project_id": "exact-exported-project-id",
  "session_id": "exact-exported-session-id",
  "source_snapshot_id": "exact-exported-source-snapshot-id",
  "keypoint_profile": "actual-tracker-profile",
  "keypoint_names": ["left_shoulder", "right_shoulder"],
  "skeleton_edges": [[0, 1]],
  "provenance": {
    "kind": "measurement",
    "method": "the real annotation or tracker method",
    "source_artifact": "/actual/project/annotations/source.json"
  },
  "frames": [{
    "ref_id": "exact-exported-ref-id", "view_id": "exact-exported-view-id",
    "frame_index": 0, "time_seconds": 0.0, "width": 640, "height": 480,
    "image_sha256": "exact-exported-64-character-sha256",
    "image_orientation": "exif_oriented_display",
    "bbox_xywh": [200, 80, 180, 340],
    "keypoints": [
      {"name": "left_shoulder", "xy_px": [245, 142], "score": 0.9},
      {"name": "right_shoulder", "xy_px": [320, 140], "score": 0.9}
    ]
  }]
}
```

```bash
python3 "$capsule_skill_dir/scripts/pose_evidence.py" \
  adapt-observations --manifest /actual/project/pose_sources/manifest.json \
  --observations /actual/project/annotations/source.json \
  --output /actual/project/pose_results/existing-observed.json
```

The adapter converts pixel coordinates using each actual source image width and height, copies the exact source keys and hashes, validates the named topology, and rejects changed view/frame/time identity. It does not infer a body, adjust cameras, or synthesize unseen joints. Name a genuine provenance method; do not call model predictions measurements.

## Validate, import, and use in the capsule workflow

```bash
python3 "$capsule_skill_dir/scripts/pose_evidence.py" \
  check-result --manifest /actual/project/pose_sources/manifest.json \
  --result /actual/project/pose_results/observed.json
python3 "$capsule_skill_dir/scripts/pose_evidence.py" \
  import-payload --manifest /actual/project/pose_sources/manifest.json \
  --result /actual/project/pose_results/observed.json
```

Pass the printed `{job_id, result_path}` to `workspace_import_human_pose` for a local project artifact. Alternatively pass `{job_id, result: <validated JSON object>}` when the source and result are on different hosts. Then call `workspace_get_human_pose` to confirm import and inspect views/frames. Replaced reference sources or a different project/session/snapshot are rejected; a scene geometry revision by itself does not invalidate the source snapshot. The workbench result panel can cite the imported pose evidence in feedback to Astra.

For an auditable capsule handoff, make a new directory:

```bash
python3 "$capsule_skill_dir/scripts/pose_evidence.py" \
  handoff --manifest /actual/project/pose_sources/manifest.json \
  --result /actual/project/pose_results/observed.json \
  --output /actual/project/pose_handoff/first-pass
```

The handoff copies the manifest and result, records hashes and source identity, and references original images and cameras without inventing a calibration. To compare these joints with existing MHR127/HOT3D21 motion, provide an explicitly checked `--joint-map` JSON with `source_profile`, `target_profile: "mhr127-hot3d21"`, `pairs` mapping **declared source joint names → MHR127 integer indices**, and a `validation_note` describing how the correspondence was verified. No COCO17→MHR127 map is assumed. Project the candidate 3D motion with each actual camera, inspect 2D residuals and masks, then make a deliberate correction to the existing causal motion and validate/review it with `capsule.py`. This skill does not triangulate the 2D joints, create a general 3D body, or feed them automatically into the mask-only leg refiner.

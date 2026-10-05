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

## Prepare a genuine WholeBody133 model

The default observer returns **133 COCO-WholeBody joints**: 17 body, 6 feet, 68 face, 21 left hand, and 21 right hand. Its joint names and skeleton order match [MMPose's official COCO-WholeBody metainfo](https://github.com/open-mmlab/mmpose/blob/main/configs/_base_/datasets/coco_wholebody.py). The [ViTPose++ paper repository](https://github.com/ViTAE-Transformer/ViTPose#wholebody-dataset) publishes the Base multi-task checkpoint and its 133-output WholeBody expert. Selecting expert 5 on the widely shared `usyd-community/vitpose-plus-base` **does not** suffice: that converted checkpoint has a 17-channel head. The observer checks the head's actual safetensors shape and fails before loading Torch if it is not 133.

Provide two **already downloaded** local inputs: the official ViTPose++ Base multi-task `vitpose+_base.pth` and the matching Hugging Face `usyd-community/vitpose-plus-base` directory. The official checkpoint is also mirrored by [HF staff's original checkpoint collection](https://huggingface.co/nielsr/vitpose-original-checkpoints/tree/main). Convert once into a new directory:

```bash
/path/to/pose-python "$capsule_skill_dir/scripts/convert_wholebody_checkpoint.py" \
  --official-checkpoint /path/to/vitpose+_base.pth \
  --base-hf /path/to/vitpose-plus-base \
  --output /path/to/vitpose-plus-base-wholebody133
```

The converter uses `torch.load(weights_only=True)`, verifies that **353 matching shared HF tensors** are bitwise equal to the original checkpoint, then replaces all 14 decoder tensors with the original `associate_keypoint_heads.4` (133 outputs). It records source hashes in `conversion.json`. It never downloads weights and refuses to overwrite an existing output. Do not use arbitrary similarly named `.pth` files that fail this match.

Local verification on 2026-09-30 used `/home/nortonii/.venvs/vggt-scene-mesh/bin/python`, original checkpoint `/home/nortonii/data/arctic-official/models/vitpose-original-checkpoints/vitpose+_base.pth` (SHA-256 `de043d2ea25a8bcffeb8ea278a68f52e487b918866b629efb2f894b15fdb3545`), and converted model `/home/nortonii/data/arctic-official/models/vitpose-plus-base-wholebody133/model.safetensors` (SHA-256 `008269345729f41d5a40bef51648c87700ed584749f96e05f1aebfff1faa7727`). These paths are an example of one deployment, not defaults in the skill.

## Infer WholeBody133 joints

Use a Python environment with compatible Torch and torchvision, plus `scripts/requirements-pose.txt`. Point to the converted **133-channel** ViTPose++ directory and the official Faster R-CNN MobileNet V3 320 COCO detector checkpoint. The skill does not install or download them during inference. Checkpoint paths are explicit; none are tied to a reconstruction case.

```bash
/path/to/pose-python "$capsule_skill_dir/scripts/pose_evidence.py" \
  track --manifest /actual/project/pose_sources/manifest.json \
  --output /actual/project/pose_results/vitpose-observed.json \
  --model-path /path/to/vitpose-plus-base-wholebody133 \
  --detector-path /path/to/fasterrcnn_mobilenet_v3_large_320_fpn-907ea3f9.pth
```

The detector chooses one prominent person **independently in each view** using detection confidence, area, and later ROI overlap; it redetects periodically or after tracking loss. It estimates every exported source frame at the imported frame rate. The ROI quality gate uses only the first 17 body joints, so confident face or hand predictions do not shift the crop when the torso is uncertain. A detected person still yields a usable tracked frame even if the torso is cut off; the next ROI propagation remains conservative. Multiple people can cause identity switches, including across views. A long sequence can take substantial time; progress JSON lines report completed and total frames. The output is `observed_2d` evidence with `provenance.kind: image_inference`, 133 named joints in five groups, checkpoint provenance, exact source binding and hashes, and per-frame normalized boxes/joints. Hand tips under object occlusion can be estimated in the wrong place even when the heatmap score is high; use the workbench's point correction tool to inspect and amend them. This is a 2D image estimate, not a measured 3D body or validated cross-camera person identity.

For an existing 17-joint model, request legacy output explicitly with `--profile coco17 --model-path /path/to/vitpose-plus-base`. The default 133 mode never pads 17 predictions with fabricated hand joints.

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

## Apply WholeBody133 keypoint corrections from the workbench

In the workbench, use “关键点” on the original reference image's hover toolbar to edit its latest available evidence. The editor supports all declared joints and offers only the body, foot, face and hand regions present in the result. Drag a point, or select a missing joint before placing it on the original image. Current-round “关键点修改N” cards accumulate edits per frame and insert `[[pose_edit:ID]]` into the prompt; use `@` to cite them or reopen the saved source frame. The reference panel lists current drafts.

This skill's `apply-corrections` accepts only canonical WholeBody133 parents; COCO17 and custom-topology edits require a compatible consumer, without padding or relabeling their topology. The workbench exports a `scene_feedback_pose_corrections` JSON document for edited joints. It includes the original result's project/session/source snapshot, parent job ID, exact frame view/index/time/dimensions/hash/orientation, canonical joint names, and the archived original keypoints. The edited `visibility` is `visible`, `occluded`, or `missing`: visible points have normalized image coordinates; occluded points may have a manually inferred position but are **not** counted as visible observations; missing points have no supplied coordinates.

Merge it into a **new** result file; do not overwrite the model output. Use the exact corrections path returned by the workbench; its stored documents are under the current project's `data/human_pose/corrections/` directory.

```bash
python3 "$capsule_skill_dir/scripts/pose_evidence.py" \
  apply-corrections --manifest /actual/project/pose_sources/manifest.json \
  --result /actual/project/pose_results/vitpose-observed.json \
  --corrections /actual/project/data/human_pose/corrections/FB/Edit.json \
  --output /actual/project/pose_results/vitpose-corrected.json
```

The merger rejects another snapshot, parent job, view, frame time, image hash, orientation, changed original joint, or noncanonical topology. Changed joints retain their original model `score` and a `model_prediction` copy; `manual_source: manual_2d`, `manual_visibility`, and `manual_position` identify the user's independent evidence. Fitting code must give an explicit manual visible point its own weight instead of treating the retained heatmap score as a human confidence value. Occluded/missing points carry zero visible-observation weight. Unedited inferred joints remain model estimates. A parent `projected_3d` result remains `projected_3d` after manual edits; its unedited projections cannot become 2D observations by relabeling. A visible point may be added even when the detector missed the person and that frame has no person box.

A completed workbench pose job is immutable: **do not call `workspace_import_human_pose` again with this corrected file and the same `job_id`**. That returns a conflict instead of replacing the archived result. For an `observed_2d` parent, pass the new file directly to the [WholeBody reconstruction workflow](wholebody-reconstruction.md) as `observations_path`; keep the original imported result and correction document for audit. A corrected `projected_3d` parent stays reference evidence for review/comparison and is rejected by this triangulation route, even if it contains a few manual points. Displaying a merged result as a new workbench job requires a fresh export and a separately validated source binding; this skill does not silently rebind it.

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

The handoff copies the manifest and result, records hashes and source identity, and references original images and cameras without inventing a calibration. To compare these joints with existing MHR127/HOT3D21 motion, provide an explicitly checked `--joint-map` JSON with `source_profile`, `target_profile: "mhr127-hot3d21"`, `pairs` mapping **declared source joint names → MHR127 integer indices**, and a `validation_note` describing how the correspondence was verified. No COCO17→MHR127 map is assumed. Project the candidate 3D motion with each actual camera, inspect 2D residuals and masks, then make a deliberate correction to the existing causal motion and validate/review it with `capsule.py`. This legacy adapter does not feed joints automatically into the mask-only leg refiner. For source-bound multiview WholeBody133 observations with verified cross-view identity, use the separate [fixed-body-and-hands reconstruction route](wholebody-reconstruction.md).

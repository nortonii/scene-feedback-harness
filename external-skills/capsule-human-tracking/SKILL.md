---
name: capsule-human-tracking
description: Observe and manually correct COCO-WholeBody133 body, face, feet and hand keypoints from source-bound workbench frames, then reconstruct editable fixed-geometry capsule bodies and articulated fingers from calibrated multi-view evidence. Use for WholeBody tracking, hand keypoint annotation, hand reconstruction, capsule motion refinement, visual-workbench human evidence or Astra handoff; preserve explicit legacy MHR127/HOT3D21 workflows.
---

# WholeBody Capsule 人体与手部

Default to **COCO-WholeBody133**: 17 body, 6 foot, 68 face, and 21 keypoints per hand. Observe the actual exported reference frames, let the user correct visible hand joints in the workbench, then reconstruct a fixed capsule body and articulated fingers using calibrated multiple views. Deliver an editable animated human `.blend`, an integrated scene copy when alignment is supplied, and a GLB preview when requested. Face landmarks are evidence; this workflow does not reconstruct a detailed facial surface.

Read [pose-workbench.md](references/pose-workbench.md) for source export, real 133-point ViTPose inference, result import and manual correction merging. Read [wholebody-reconstruction.md](references/wholebody-reconstruction.md) before triangulation, fixed-template initialization or Blender delivery. Model execution stays in this skill, outside the lightweight MCP plugin. Legacy COCO17 evidence remains readable but cannot supply missing hand observations.

## Default workflow

When applying submitted `[[pose_edit:ID]]` feedback, reopen its **original parent** using `source_manifest_path`, `parent_result_path` and `corrections_path` from the feedback. If needed, `workspace_get_human_pose(parent_job_id)` gives the complete archived result path. Do not replace these bindings with a fresh export or the displayed latest result. A completed pose job is immutable: use a merged `observed_2d` file directly for reconstruction, preserving its parent and the saved correction. A `projected_3d` parent remains reference evidence and is rejected by this triangulation route.

1. Call `workspace_export_pose_sources` for the current project. Verify the manifest before loading a model. Process every supplied view and frame at its imported sample rate; never silently substitute a prior project's footage, camera, identity or motion.
2. Run `pose_evidence.py track` with a **verified 133-output checkpoint**, then `check-result` and `import-payload`; import through `workspace_import_human_pose`. Selecting ViTPose expert 5 on a 17-output model is insufficient. Model loading is local only; setup/download is a separate step.
3. For hand corrections, open the result's “修正关键点”, choose left/right hand and joint, then click/drag on the source image and send the shared prompt. Read the attached original, orange overlay and source-bound correction JSON. `apply-corrections` merges into a new file; preserve model scores and distinguish visible, occluded and missing points. Projected joints cannot become measured observations by changing their label.
4. Verify one actor across views and synchronized calibrated cameras. Run `capsule.py wholebody-reconstruct` with an explicit camera convention and identity association. Initialize body and each hand once from reliable evidence, then advance from the previous accepted state. Missing/occluded fingers hold their prior pose; report uninitialized hands as unsupported rather than inventing depth.
5. Run `capsule.py wholebody-build` to create fixed meshes, animate segment transforms and reopen/audit the saved Blender files. Integrating into an existing scene requires an explicit world-to-Blender transform. Preserve input files and original scene objects; publish the inspected preview with `workspace_publish_scene` only for the authorized reconstruction task.

## Non-obvious rules

- Initialize body geometry once. Update pose from the previous accepted state using current observations; keep mesh topology, segment lengths and radii fixed through time. A one-time template revision is different from per-frame shape fitting.
- SAM3D/MHR may supply the initial template. Do not use each frame's reconstructed body as a new baseline when the user wants previous-frame tracking. Merely differencing independent poses does not make the estimator causal.
- Separate **template errors** (e.g. an abdomen cap protruding between thighs) from **tracking errors** (e.g. a sleeve mislabeled as trousers). Correct the responsible geometry or observation association, not unrelated motion.
- Associate left/right pants contours using the previous knees projected in the current cameras. Exclude arm occlusion boundaries. A neural per-frame knee split can assign a real knee to the opposite leg and drag a leg toward a sleeve.
- Missing observations are not evidence for motion. Preserve or damp the previous state; do not accumulate unconstrained outward angular velocity. Initial alignment correction is not initial velocity.
- Check both fit and temporal stability. Row coverage alone can hide a missing knee cap; a union with the shin can explain a thigh boundary incorrectly. The tool fits **thigh** perspective tangency and retains prior causal shin increments.
- Independent prefix replay verifies this refinement conditional on its baseline. It does not prove that upstream hand interpolation or root/arm tracking is causal. Report that distinction and retain provisional scale/occlusion limitations.
- Keep **observed 2D**, manual visible observations, segmentation masks and **projected 3D** as separately labeled evidence. Manual placement supplies a fitting weight, not a higher detector confidence. WholeBody hand-root indices 91/112 correspond to body wrists 9/10; fuse matching observations and reject conflicting anchors. WholeBody21, HOT3D/UmeTrack21 and MHR127 orders are different; require an explicit verified mapping. No 2D result alone establishes cross-camera identity, depth, metric scale or a 3D trajectory.

## Tools and legacy routing

Use `python3 <skill-dir>/scripts/capsule.py --help`. All mutating stages require a new empty output directory and do not publish over an existing review page. Paths in config resolve relative to its location.

| Task | Command |
|---|---|
| Observe real WholeBody133 reference frames | `pose_evidence.py track` |
| Merge source-bound manual hand corrections into a new result | `pose_evidence.py apply-corrections` |
| Initialize and track body plus fingers from calibrated views | `capsule.py wholebody-reconstruct` |
| Build/reopen WholeBody Blender and optional GLB | `capsule.py wholebody-build` |
| Turn the tested case into an explicit input bundle | `import-spi102` |
| Check cameras, motion, media samples and template compatibility | `inspect` |
| Correct both legs using current masks and previous knee positions | `refine-legs` |
| Check fixed lengths, FK, scope and independent prefix replay | `validate` |
| Revise one fixed capsule, such as the abdomen | `template-edit` |
| Build and reopen standalone/integrated Blender assets | `build` |
| Render selected/all frames in both cameras and create review HTML/video | `review` |
| Package the current result, tools, configuration and provenance for Astra | `handoff` |

- The remaining commands retain the validated **MHR127 + HOT3D/UmeTrack21** route for an existing fixed-length tracked motion and mask-based leg refinement. For that input/adapter, read [schema.md](references/schema.md). Do not route a WholeBody133 result through MHR127 by padding or guessing indices.
- To run the tools or package for Astra, read [astra-workflow.md](references/astra-workflow.md).
- For the actual SPI102 artifacts, failure diagnosis, validated parameters and measured outcomes, read [spi102-case.md](references/spi102-case.md). Those constants are case settings, not universal anatomy.
- For workbench source export, standalone observed 2D inference, result import and evidence handoff, read [pose-workbench.md](references/pose-workbench.md). The scripts live in this skill and never import the MCP plugin backend. Do not start a GPU job or download weights merely to inspect sources.

## Completion checks

Preserve input files and scene alignment. Inspect selected reference overlays in **every supplied view**, including the reported bad frames and neighboring transitions. When delivering the full sequence, update all requested frame renders/videos, not just a screenshot. Compare held-out errors on the same observations; verify bone/hand/foot lengths, recurrence, forward kinematics, unaffected joints and original static objects. Reopen saved blends and verify animations are embedded with no deforming shape keys, scales or external geometry caches.

A successful tool run is not automatic visual acceptance. Keep candidate outputs isolated until inspection; then update the authorized delivery and its existing review URL together. Never claim a generated file is served until the URL and linked assets have been checked.

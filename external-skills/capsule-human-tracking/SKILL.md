---
name: capsule-human-tracking
description: Track observed 2D human keypoints from exported reference frames, and build or refine editable fixed-geometry capsule humans with causal motion increments and validated Blender delivery. Use for visual-workbench human pose evidence, dynamic capsule modeling, leg silhouette mismatch, limb drift, or Astra handoff.
---

# Capsule Human 连续跟踪

Deliver one persistent capsule template, a continuous animation, a separate human `.blend`, an integrated scene copy, and inspectable before/after views. The bundled tools support **MHR127 bodies + HOT3D/UmeTrack21 hands** and calibrated pinhole views. They consume an existing fixed-length tracked motion; they do not estimate cameras, run segmentation, or initialize a general body from raw video.

For a visual workbench with imported source images, the separate 2D observer in `scripts/pose_evidence.py` can run detector + ViTPose on **the actual exported frames** and return provenance-bound COCO17 observations. This is evidence for human-guided fitting, not a replacement for the existing MHR127 motion or mask-based leg refiner. Read [pose-workbench.md](references/pose-workbench.md) only for that workflow.

## Non-obvious rules

- Initialize body geometry once. Update pose from the previous accepted state using current observations; keep mesh topology, segment lengths and radii fixed through time. A one-time template revision is different from per-frame shape fitting.
- SAM3D/MHR may supply the initial template. Do not use each frame's reconstructed body as a new baseline when the user wants previous-frame tracking. Merely differencing independent poses does not make the estimator causal.
- Separate **template errors** (e.g. an abdomen cap protruding between thighs) from **tracking errors** (e.g. a sleeve mislabeled as trousers). Correct the responsible geometry or observation association, not unrelated motion.
- Associate left/right pants contours using the previous knees projected in the current cameras. Exclude arm occlusion boundaries. A neural per-frame knee split can assign a real knee to the opposite leg and drag a leg toward a sleeve.
- Missing observations are not evidence for motion. Preserve or damp the previous state; do not accumulate unconstrained outward angular velocity. Initial alignment correction is not initial velocity.
- Check both fit and temporal stability. Row coverage alone can hide a missing knee cap; a union with the shin can explain a thigh boundary incorrectly. The tool fits **thigh** perspective tangency and retains prior causal shin increments.
- Independent prefix replay verifies this refinement conditional on its baseline. It does not prove that upstream hand interpolation or root/arm tracking is causal. Report that distinction and retain provisional scale/occlusion limitations.
- Keep **observed 2D** joints, existing segmentation masks, and **projected 3D** joints as separately labeled evidence. COCO17 joint indices are not MHR127/HOT3D21 indices; require an explicit, verified correspondence before comparing or fitting. No 2D result alone establishes cross-camera identity, depth, metric scale, or a 3D trajectory.

## Tools and routing

Use `python3 <skill-dir>/scripts/capsule.py --help`. All mutating stages require a new empty output directory and do not publish over an existing review page. Paths in config resolve relative to its location.

| Task | Command |
|---|---|
| Turn the tested case into an explicit input bundle | `import-spi102` |
| Check cameras, motion, media samples and template compatibility | `inspect` |
| Correct both legs using current masks and previous knee positions | `refine-legs` |
| Check fixed lengths, FK, scope and independent prefix replay | `validate` |
| Revise one fixed capsule, such as the abdomen | `template-edit` |
| Build and reopen standalone/integrated Blender assets | `build` |
| Render selected/all frames in both cameras and create review HTML/video | `review` |
| Package the current result, tools, configuration and provenance for Astra | `handoff` |

- For a new input or adapter, read [schema.md](references/schema.md). Reject unknown joint order or camera convention rather than guessing.
- To run the tools or package for Astra, read [astra-workflow.md](references/astra-workflow.md).
- For the actual SPI102 artifacts, failure diagnosis, validated parameters and measured outcomes, read [spi102-case.md](references/spi102-case.md). Those constants are case settings, not universal anatomy.
- For workbench source export, standalone observed 2D inference, result import and evidence handoff, read [pose-workbench.md](references/pose-workbench.md). The scripts live in this skill and never import the MCP plugin backend. Do not start a GPU job or download weights merely to inspect sources.

## Completion checks

Preserve input files and scene alignment. Inspect selected reference overlays in **every supplied view**, including the reported bad frames and neighboring transitions. When delivering the full sequence, update all requested frame renders/videos, not just a screenshot. Compare held-out errors on the same observations; verify bone/hand/foot lengths, recurrence, forward kinematics, unaffected joints and original static objects. Reopen saved blends and verify animations are embedded with no deforming shape keys, scales or external geometry caches.

A successful tool run is not automatic visual acceptance. Keep candidate outputs isolated until inspection; then update the authorized delivery and its existing review URL together. Never claim a generated file is served until the URL and linked assets have been checked.

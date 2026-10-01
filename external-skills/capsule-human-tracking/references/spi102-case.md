# SPI102 verified case and failure lessons

## Approved assets (2026-09-18)

Original static scene, untouched:
`/home/nortonii/scenes/show3d_SPI102_keyboard_vggt_omega/blender/static_multiview_refined_20260916/static_scene_multiview.blend`

Approved animation and review:
`/home/nortonii/scenes/show3d_SPI102_keyboard_vggt_omega/blender/capsule_human_temporal_tracking_20260918/`

- `capsule_human_only.blend`, `static_scene_with_capsule_human.blend`
- `tracked_motion.npz`, `capsule_motion_increments.npz`
- `leg_fit_v2_report.json`, `validation.json`
- Existing review: `http://127.0.0.1:9061/review/`; user service `spi102-capsule-temporal-review`
- 1134 frames at 60 fps, 56 immutable capsule meshes, 37 preserved static objects.

Input observations:
`/home/nortonii/scenes/show3d_SPI102_keyboard_dynamic/alignment_v3/reconstruction/`

RGB and cleaned bit masks are in the sibling `frames/h0,h1/` and **`masks/h0,h1/`** directories. The historical preprocessing script says `masks_clean`, but that directory is not the packaged dataset path. Use the actual files.

## Prepared tools and Astra handoff

Root:
`/home/nortonii/scenes/show3d_SPI102_keyboard_vggt_omega/blender/capsule_human_astra_toolkit_20260918/`

- `bundle/config.json`: reusable input config, exported template and alignment; media references the source dataset.
- `astra_handoff/ASTRA_HANDOFF.md`: primary Astra starting point.
- `astra_handoff/config.json`: portable relative media/input paths, with tools and all 1134×2 RGB/mask pairs copied.
- `astra_handoff/inputs/capsule_human_only.blend`: **exact approved current human**, not the subsequent tool-validation candidate.
- `astra_handoff/delivery/static_scene_with_capsule_human.blend`: exact approved integrated scene.
- `validation_run/`: isolated tool forward test, 1134-frame refinement, reopened builds, selected stereo review and an 8-frame stereo video smoke test.
- `prefix_run/`: independently executed first 900 frames; exactly matches the full tool run's prefix.
- `toolkit_validation.json`: tool and packaging checks. The live scene and live review were not republished during skill creation.

The reusable command is:
`/home/nortonii/.codex/skills/capsule-human-tracking/scripts/capsule.py`

## What failed and what fixed it

### Independent body geometry each frame

The user explicitly wanted `pose[t] = pose[t-1] + motion_increment[t]`, not a per-frame neural body with offsets or independently reconstructed geometry. The initial body/hand template supplied fixed lengths and radii; later updates optimized motion only. The Blender actor has only location/quaternion curves, no mesh cache, shape keys, or per-frame length/scale animation.

The earlier full-body causal estimator used current stereo contours, reliable ViTPose points and UmeTrack wrists, a headset/torso prior, bounded increments and velocity continuity. The reusable leg stage assumes that causal baseline already exists; it does not substitute a new general body estimator.

### Strange object between thighs

Diagnostic part colors identified the **abdomen capsule**, whose oversized lower rounded cap projected into the crotch. This was a template error, not an extra object or a motion problem. In this subject's source units, the lower cap center moved 65% from body joint 1 toward 36, abdomen radius changed .125 → .085, and the transverse pelvis radius .095 → .070. All frames share the revised mesh. Preserve the previous version and inspect the torso connection in another view; these constants are not general anatomical rules.

### Right leg veered outward near source frame 850

Between roughly frames 550–850, the old pants targets included sleeve edges. The per-frame neural knee split also assigned genuine right-knee observations to the other leg. Smoothness alone could not fix sustained wrong observations: the leg steadily followed the sleeve.

An initial conservative arm-exclusion band stopped the drift but discarded nearly all right-leg observations. Final refinement instead re-extracts pants contours from each current mask, selects each connected row run near the **previous projected knee**, masks observed arm-contact boundaries, and fits exact perspective thigh tangency. This recovers useful knee edges without a per-frame SAM3D split.

### Better silhouette fit introduced jitter

Raw mask edges fluctuate. Bounded pose steps alone were not sufficient. The accepted thigh increment uses 0.55 of the current optimized increment plus 0.45 of the previous accepted increment; unsupported observations do not extrapolate motion. Reset the increment history after frame-0 alignment: otherwise a large initialization correction becomes an artificial first-frame velocity.

Using the thigh+shin union to fit a trouser edge let the shin explain the knee cap and misplaced the thigh. Fit visible thigh boundaries with the thigh capsule only; propagate unseen anatomy with its existing causal prior rather than overfitting it.

## Approved-version evidence

Same held-out boundary points (approximate 512×640 tangent-distance pixels):

| Metric | Before final leg refinement | After |
|---|---:|---:|
| Left mean | 3.705 | 3.423 |
| Right mean | 7.280 | 5.639 |
| Left, frames 800–900 | 4.014 | 3.222 |
| Right, frames 800–900 | 17.249 | 13.160 |
| Leg second difference P95, source units | 0.003558 | 0.003091 |

These are fitting diagnostics, not ground-truth 3D accuracy. A fixed capsule approximates clothing folds and occluded anatomy. Body bone-length error was ~1.7e-16 in source units. Prefix replay matched exactly. Hands, torso, root, radii and all 56 meshes stayed unchanged during the final leg-only refinement.

## Operational traps

- Source frame is zero-based; Blender frame = source + 1.
- Use raw source/actor coordinates for fitting, and apply the inherited actor-to-static alignment once. Its scale (~0.7651 here) is provisional and constant; its pose varies over original scene anchors.
- JSON `radius` must agree with fitter `motion.radii` for a limb. The template-edit tool emits both when a fitted limb radius changes.
- Preserve the baseline before replacing review images: earlier `before/` images were hardlinks. Copy to a new inode rather than overwriting shared links.
- Distinguish current verification from historical fix reports; a prior statement that the first 500 frames were unchanged no longer describes the later full-sequence refinement.
- Cache-bust image/video URLs when republishing the same live endpoint. A new render is not visible merely because the `.blend` changed.

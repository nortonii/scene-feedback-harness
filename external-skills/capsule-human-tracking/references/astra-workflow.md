# Astra: quick execution and handoff

Use a fresh run directory, with `build/` and `review/` beneath it. The approved SPI102 prepared bundle and handoff locations are listed in [spi102-case.md](spi102-case.md).

## Start from a prepared configuration

In these commands, substitute actual absolute paths for TOOL, CONFIG and RUN. They are shell variables, not skill-specific environment requirements.

```bash
TOOL=/home/nortonii/.codex/skills/capsule-human-tracking/scripts/capsule.py
CONFIG=/path/to/config.json
RUN=/path/to/new-run
python3 "$TOOL" inspect --config "$CONFIG"
python3 "$TOOL" refine-legs --config "$CONFIG" --output "$RUN"
python3 "$TOOL" refine-legs --config "$CONFIG" --frames 900 --output "${RUN}-prefix"
python3 "$TOOL" validate --motion "$RUN/motion.npz" --prefix "${RUN}-prefix/motion.npz" --report "$RUN/validation.json"
python3 "$TOOL" build --config "$CONFIG" --motion "$RUN/motion.npz" --output "$RUN/build"
python3 "$TOOL" review --config "$CONFIG" --after "$RUN/build/capsule_human_only.blend" --frames 0,mid,last --output "$RUN/review"
python3 -m http.server 9071 --bind 127.0.0.1 --directory "$RUN/review"
```

Choose a meaningful prefix shorter than the full sequence. Add `validate --reference /path/to/input-motion.npz` for the leg-only scope check. Review defaults to all supplied cameras; use `--views 0` only for an intentionally single-camera check. For the final full video use a **different output directory**, `--frames all --video`. Before serving, pick an unused port; the example command does not persist after terminal exit. Reuse the existing review service for an authorized update and verify URLs after replacing artifacts.

For a new motion shorter than the original config, pass frame indices within its range or emit a config whose motion is that shortened sequence before using `--frames all`.

## Import the tested layout

```bash
python3 "$TOOL" import-spi102 \
  --case /path/to/capsule_human_temporal_tracking_case \
  --source /path/to/alignment_v3/reconstruction \
  --output /path/to/new-input-bundle
```

This adapter expects `tracked_motion.npz`, `capsule_human_only.blend`, and `manifest.json` in the case. It copies the motion, cameras, source static scene and standalone human; exports explicit capsule endpoint references, exact mesh vertices and every evaluated actor alignment transform. RGB and masks remain path references until `handoff --copy-media` is requested. A mismatch with the known template fails rather than silently assigning joints.

## Fix a static template problem

Example only: shorten the abdomen once and revise its radius. Do not copy these anatomy constants to unrelated subjects without inspection.

```bash
python3 "$TOOL" template-edit --config "$CONFIG" --name CH_Abdomen \
  --radius 0.085 \
  --a-json '{"source":"body_joints","weights":[[1,0.35],[36,0.65]]}' \
  --output /path/to/template-revision
python3 "$TOOL" inspect --config /path/to/template-revision/config.json
```

Build using the emitted config. A template edit changes one shared mesh and corresponding endpoint placement in all frames; it does not rerun or independently reconstruct body poses.

## Package the approved current result

```bash
python3 "$TOOL" handoff --config "$CONFIG" --run "$RUN" --output /path/to/astra-handoff
```

The run must contain `motion.npz` and the completed `build/` assets. The output contains:

- `ASTRA_HANDOFF.md`: ready-to-run commands and caveats;
- `config.json`, `inputs/`: latest motion, exact human mesh template, calibration, actor alignment and static scene;
- `delivery/`: integrated blend and local transform increments;
- `tools/`: independent copies of the reusable scripts;
- `reports/`, `handoff_manifest.json`: validation and file hashes.

Default handoffs retain absolute media references for same-host reuse. Add `--copy-media` for transfer to a different host; it copies the actual frame/mask files and rewrites relative paths. Inspect the emitted config after moving a bundle. Blender and Python runtime dependencies are not bundled. Do not send the handoff to another person or service unless separately requested.

## New videos and unsupported skeletons

Prepare calibrated image sequences, consistent per-part masks, one fixed body template and a provenance-backed causal baseline first. The CLI does not silently call SAM3D or estimate a skeleton. For another joint order, write an explicit topology adapter and meaningful FK/length checks; do not edit indices until an overlay appears plausible. For a raw-image scene, camera/static-scene preparation can use the existing photos-to-blender workflow before this skill.

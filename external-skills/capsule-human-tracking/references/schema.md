# Configuration and array contract

For exported workbench source frames and observed 2D joint result files, see [pose-workbench.md](pose-workbench.md). Those files carry project/session/source snapshot identity and a declared joint profile; they are separate from the MHR127/HOT3D21 motion, camera and mask arrays below. A mapping between profiles must be explicit and checked against the actual source.

## Runtime

Validated on Python 3 with NumPy, SciPy, OpenCV and Pillow, Blender 4.5.3, and ffmpeg for video. `inspect`, `validate` and CLI help avoid loading Blender. The Blender executable is selected by `--blender`, `BLENDER_BIN`, PATH, then `~/.local/opt/blender-*/blender`. Tool subprocesses use argument arrays; no project modules are imported.

## config.json (schema_version 1)

```json
{
  "schema_version": 1,
  "profile": "mhr127-hot3d21",
  "fps": 60,
  "image_scale": 0.5,
  "image_size": [512, 640],
  "source_unit_in_meters": 1.0,
  "scale_note": "Inherited coordinate convention; not independently measured",
  "paths": {
    "motion": "inputs/motion.npz",
    "base_human": "inputs/capsule_human_only.blend",
    "cameras": "inputs/cameras.npz",
    "static_scene": "inputs/static_scene.blend",
    "template": "inputs/template.json",
    "alignment": "inputs/alignment.npz",
    "images": ["frames/h0/{frame:05d}.jpg", "frames/h1/{frame:05d}.jpg"],
    "masks": ["masks/h0/{frame:05d}.png", "masks/h1/{frame:05d}.png"]
  },
  "fit": {"pants_bit": 4, "occluder_bits": 11, "rows_start_fraction": 0.6375}
}
```

All paths, including format strings, resolve relative to the config directory; absolute paths are accepted. Masks and RGB must already share the declared image resolution. `image_scale` multiplies the supplied calibration K, **not** the RGB. Set 1 for a K already in image pixels. The example 0.5 is only for SPI102's 1024×1280 K and 512×640 images. Distorted/fisheye images need a consistent camera adapter or prior undistortion.

`source_unit_in_meters` scales the position regularizer; it does not establish metric truth. Diagnostics default to source units. New camera layouts or legs elsewhere in the image may require changing `rows_start_fraction`; pixel gates scale with image width. Current code is validated on the seated egocentric case, not a universal gait or anatomy estimator.

## Motion NPZ

No pickle/object arrays. Let F be sequence length:

| Key | Shape | Meaning |
|---|---|---|
| body_joints | F×127×3 | Actor-local MHR joint positions |
| left_hand, right_hand | F×21×3 | HOT3D order, wrist index 5 |
| pose_state | F×22 | root xyz (3), torso Euler xyz (3), eight world-direction azimuth/elevation pairs (16) |
| motion_increment | F×22 | State difference; frame 0 = state[0] - initial_state |
| initial_state | 22 | Initialization before frame-0 correction |
| segment_lengths | 8 | Fixed lengths in the edge order below |
| radii | 4×2 | left arm, right arm, left leg, right leg; upper/lower |
| rest_body | 127×3 | Fixed torso/body reference |

Additional numeric arrays are preserved. Arrays whose first dimension equals F are truncated in prefix runs; avoid ambiguous extra arrays of the same length unless they are framewise.

Edges: `(75,76),(76,77),(39,40),(40,41),(2,3),(3,4),(18,19),(19,20)`.
Feet: knee/ankle/toe `(3,4,8),(19,20,24)`. Pelvis joint is **1**, not joint 0.

The refiner keeps root/torso/arms/hands unchanged. The four thigh direction values are solved from previous accepted directions; the shin direction increments are inherited from the supplied baseline. The baseline must therefore have the intended upstream provenance. Validators check FK against actual joint positions, not only algebraic differences.

## Cameras and alignment

`cameras.npz`: `camera_to_scene` V×F×4×4, `K` V×3×3. Despite the inherited key name, the cameras are in the **same actor-local coordinate system as motion**. Rotations must be rigid and right-handed. Points project using `(point - camera_translation) @ camera_rotation`, positive camera Z forward, image y down.

`alignment.npz`: `actor_to_scene` F×4×4, mapping actor-local coordinates into the preserved static Blender scene. The worker bakes evaluated root transforms, retaining translation, quaternion and constant scale. Perspective review uses this alignment exactly once and the Blender camera-axis conversion `diag(1,-1,-1)`.

## Template JSON

Each capsule has `name`, `radius`, `length`, RGBA `color`, immutable local `vertices`/`faces`, and endpoints `a`,`b`. Endpoints are either:

```json
{"source":"body_joints","weights":[[1,0.35],[36,0.65]]}
```

or a `torso_offset` vector rotated by the torso Euler rotation and translated by root state xyz. Hand endpoints use `left_hand` or `right_hand`.

Mesh capsule axis is local +Z, cap centers at z=0 and z=length. Geometry arrays are exported from the source scene so a build does not silently alter approved geometry. Each endpoint pair must stay at constant distance through time. `template-edit` regenerates the selected capsule once with true hemispherical caps and updates its dimensions. For fitted limb radius edits it also emits a motion file with the revised shared radius; limb endpoint changes require an explicit FK adapter and are rejected by the simple edit command.

## Reports and limits

`report.json` includes held-out thigh boundary errors. Metric = angle between observed ray and sphere tangent cone, times focal length; near-image-pixel units, not exact Euclidean pixel distance or 3D ground truth. Every fifth row is held out. This checks observations excluded from optimization, not independent segmentation quality.

`build_report.json` includes both reopened blend hashes, original static object count/hash and unchanged template meshes. `capsule_motion_increments.npz` stores names, initial local transforms and left-multiplied deltas: `T[t] = ΔT[t] @ T[t-1]`; the first delta is identity. Actor-to-static alignment remains separate.

A nonzero CLI exit or missing `CAPSULE_TOOL_COMPLETE` in Blender logs is failure; Blender may otherwise exit 0 after a Python exception. Outputs are never overwritten implicitly. Missing media, dimension mismatch, variable bone length, malformed state or an unsupported profile should be fixed at the source before continuing.

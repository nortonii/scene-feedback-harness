"""Build editable fixed WholeBody capsules; save/reopen without modifying source."""
import hashlib
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bpy
import numpy as np
from mathutils import Vector
from common import capsule_mesh, sha, write_json
from wholebody_reconstruction import validate_motion

job = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text())
run, out = Path(job["run"]), Path(job["output"])
model = json.loads((run / "wholebody_template.json").read_text())
with np.load(run / "wholebody_motion.npz", allow_pickle=False) as data:
    motion = {key: data[key] for key in data.files}
validate_motion(model, motion)
config = json.loads((run / "reconstruction_config.json").read_text())
fps = config.get("fps", 30)
if type(fps) not in (int, float) or not 1 <= fps <= 120:
    raise ValueError("Blender fps must be between 1 and 120")
transform = np.asarray(job["world_to_blender"])
positions = motion["positions"] @ transform[:3, :3].T + transform[:3, 3]
times = motion["time_seconds"]
frame_numbers = 1 + (times - times[0]) * fps


def mesh_hash(obj):
    return hashlib.sha256(np.array([list(vertex.co) for vertex in obj.data.vertices], dtype="float32").tobytes()).hexdigest()


def inventory(names=None):
    result = {}
    for obj in bpy.context.scene.objects:
        if names is not None and obj.name not in names:
            continue
        value = {"type": obj.type, "matrix": np.asarray(obj.matrix_world).tolist(),
                 "visibility": [obj.hide_render, obj.hide_viewport],
                 "action": obj.animation_data.action.name if obj.animation_data and obj.animation_data.action else None}
        if obj.type == "MESH":
            value.update(vertices=mesh_hash(obj), faces=[list(poly.vertices) for poly in obj.data.polygons],
                         materials=[material.name if material else None for material in obj.data.materials])
        result[obj.name] = value
    return result


def animate(obj, locations, quaternions, available):
    obj.rotation_mode = "QUATERNION"
    obj.animation_data_create()
    action = bpy.data.actions.new(obj.name + "_Motion")
    obj.animation_data.action = action
    quaternions = quaternions.copy()
    for index in range(1, len(quaternions)):
        if quaternions[index] @ quaternions[index - 1] < 0:
            quaternions[index] *= -1
    for path, values in (("location", locations), ("rotation_quaternion", quaternions)):
        for axis in range(values.shape[1]):
            curve = action.fcurves.new(path, index=axis)
            curve.keyframe_points.add(len(times))
            curve.keyframe_points.foreach_set("co", np.c_[frame_numbers, values[:, axis]].astype("float32").ravel())
            for key in curve.keyframe_points:
                key.interpolation = "LINEAR"
            curve.update()
    for path in ("hide_render", "hide_viewport"):
        curve = action.fcurves.new(path)
        curve.keyframe_points.add(len(times))
        curve.keyframe_points.foreach_set("co", np.c_[frame_numbers, ~available].astype("float32").ravel())
        for key in curve.keyframe_points:
            key.interpolation = "CONSTANT"
        curve.update()
    obj.animation_data.action_slot = action.slots[0]


original_hash = sha(job["scene"]) if job["scene"] else None
if job["scene"]:
    bpy.ops.wm.open_mainfile(filepath=job["scene"])
else:
    bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.frame_set(1)
bpy.context.view_layer.update()
original = inventory()
original_names = list(original)
if any(name.startswith("WB_") for name in original_names):
    raise ValueError("Scene already has WB_ actor objects; use the preserved scene source")
collection = bpy.data.collections.new("WholeBody_Fixed_Capsules")
scene.collection.children.link(collection)
root = bpy.data.objects.new("WB_Actor", None)
collection.objects.link(root)
root["source_profile"] = model["source_profile"]
root["actor_id"] = model["association"]["actor_id"]
root["coordinate_frame"] = "explicit world_to_blender applied to camera_world"
specs = []
hashes = {}
terminals = {}
identity_quaternions = np.tile([1., 0., 0., 0.], (len(times), 1))
hand_root_constraints = []


def moving_root(part_name, index):
    obj = bpy.data.objects.new("WB_Root_" + part_name, None)
    collection.objects.link(obj)
    obj.parent = root
    available = motion["available"][:, index]
    first = int(np.flatnonzero(available)[0])
    locations = positions[:, index].copy()
    locations[~available] = locations[first]
    animate(obj, locations, identity_quaternions, available)
    return obj


for part_name, part in model["parts"].items():
    root_index = part["root"]
    if part_name in ("left_hand", "right_hand"):
        part_root = moving_root(part_name, root_index)
        wrist = 9 if part_name == "left_hand" else 10
        if wrist in terminals:
            forearm, forearm_length, _ = terminals[wrist]
            anchor = bpy.data.objects.new("WB_" + part_name + "_wrist_anchor", None)
            collection.objects.link(anchor)
            anchor.parent = forearm
            anchor.location = (0, 0, forearm_length)
            constraint = part_root.constraints.new("COPY_LOCATION")
            constraint.name = "Same_side_body_wrist"
            constraint.target = anchor
            for index, frame in enumerate(frame_numbers):
                constraint.influence = float(motion["available"][index, wrist])
                constraint.keyframe_insert("influence", frame=float(frame))
            for curve in part_root.animation_data.action.fcurves:
                if curve.data_path.startswith("constraints["):
                    for key in curve.keyframe_points:
                        key.interpolation = "CONSTANT"
            hand_root_constraints.append(part_name)
        terminals[root_index] = (part_root, 0, identity_quaternions)
    elif root_index not in terminals:
        terminals[root_index] = (moving_root(part_name, root_index), 0, identity_quaternions)
    for edge_index, ((parent, child), length, radius) in enumerate(zip(part["edges"], part["lengths"], part["radii"])):
        name = "WB_" + part_name + "_" + model["node_names"][child]
        vertices, faces = capsule_mesh(length, radius, sides=12, rings=4)
        mesh = bpy.data.meshes.new(name + "_Mesh")
        mesh.from_pydata(vertices, [], faces)
        mesh.update()
        obj = bpy.data.objects.new(name, mesh)
        collection.objects.link(obj)
        parent_object, parent_length, parent_world_quaternions = terminals[parent]
        obj.parent = parent_object
        obj["part"] = part_name
        obj["source_parent_index"] = parent
        obj["source_child_index"] = child
        obj["fixed_length_camera_units"] = length
        obj["fixed_radius_camera_units"] = radius
        obj["geometry"] = "Fixed vertices; location/quaternion animation; no per-frame scale"
        material = bpy.data.materials.new(name + "_Material")
        material.diffuse_color = (.34, .58, .68, 1) if "hand" in part_name else (.68, .46, .29, 1)
        mesh.materials.append(material)
        obj.color = material.diffuse_color
        for polygon in mesh.polygons:
            polygon.use_smooth = True
        available = motion["available"][:, parent] & motion["available"][:, child]
        first = int(np.flatnonzero(available)[0])
        starts = positions[:, parent].copy()
        directions = positions[:, child] - positions[:, parent]
        starts[~available] = starts[first]
        directions[~available] = directions[first]
        if np.max(abs(np.linalg.norm(directions, axis=1) - length)) > 1e-7:
            raise ValueError("Nonrigid capsule passed to Blender")
        quaternions = np.asarray([list(Vector(direction).to_track_quat("Z", "Y")) for direction in directions])
        # Each segment is attached to its parent's fixed endpoint. Interpolation
        # changes only its relative rotation, so fingers cannot detach between samples.
        from mathutils import Quaternion
        local_quaternions = np.asarray([list(Quaternion(qparent).inverted() @ Quaternion(qchild))
                                        for qparent, qchild in zip(parent_world_quaternions, quaternions)])
        local_locations = np.tile([0., 0., parent_length], (len(times), 1))
        animate(obj, local_locations, local_quaternions, available)
        terminals[child] = (obj, length, quaternions)
        hashes[name] = mesh_hash(obj)
        specs.append({"name": name, "part": part_name, "parent": parent, "child": child, "length": length})

scene.render.fps = int(round(fps))
scene.render.fps_base = int(round(fps)) / fps
scene.frame_start = 1
scene.frame_end = int(math.ceil(frame_numbers[-1]))
scene.frame_set(1)
bpy.context.view_layer.update()
if inventory(original_names) != original:
    raise ValueError("Original scene inventory changed")
text = bpy.data.texts.new("WHOLEBODY_RECONSTRUCTION_EVIDENCE")
text.write(json.dumps({"source_profile": model["source_profile"], "status": model["status"],
                      "association": model["association"], "scale": model["source_unit_in_meters"],
                      "parts": model["parts"], "world_to_blender": job["world_to_blender"]}, indent=2))
bpy.ops.file.pack_all()
integrated = out / "scene_with_wholebody.blend" if job["scene"] else None
if integrated:
    bpy.ops.wm.save_as_mainfile(filepath=str(integrated), compress=True)
for name in original_names:
    bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)
standalone = out / "wholebody_human.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(standalone), compress=True)
if integrated:
    bpy.ops.wm.open_mainfile(filepath=str(integrated))
    bpy.context.scene.frame_set(1)
    bpy.context.view_layer.update()
    if inventory(original_names) != original:
        raise ValueError("Original scene changed after reopening integrated copy")
bpy.ops.wm.open_mainfile(filepath=str(standalone))
maximum_error = 0.
for spec in specs:
    obj = bpy.data.objects[spec["name"]]
    if obj.data.shape_keys or obj.modifiers or tuple(obj.scale) != (1., 1., 1.) or mesh_hash(obj) != hashes[obj.name]:
        raise ValueError("Reopened capsule has altered geometry")
    curves = obj.animation_data.action.fcurves
    if any(curve.data_path not in ("location", "rotation_quaternion", "hide_render", "hide_viewport")
           or len(curve.keyframe_points) != len(times) for curve in curves):
        raise ValueError("Capsule animation has unexpected deformation or external data")
for index in range(len(times)):
    frame = float(frame_numbers[index])
    bpy.context.scene.frame_set(int(frame), subframe=frame % 1)
    bpy.context.view_layer.update()
    for spec in specs:
        obj = bpy.data.objects[spec["name"]]
        if not motion["available"][index, spec["parent"]] or not motion["available"][index, spec["child"]]:
            if not obj.hide_render:
                raise ValueError("Uninitialized geometry is visible in earlier frames")
            continue
        a = np.asarray(obj.matrix_world.translation)
        b = np.asarray(obj.matrix_world @ Vector((0, 0, spec["length"])))
        maximum_error = max(maximum_error, float(np.max(abs(a - positions[index, spec["parent"]]))),
                            float(np.max(abs(b - positions[index, spec["child"]]))))
if maximum_error > 1e-5 or original_hash and sha(job["scene"]) != original_hash:
    raise ValueError("Saved animation endpoint verification or scene source preservation failed")
connection_error = 0.
for index in range(len(times) - 1):
    frame = float((frame_numbers[index] + frame_numbers[index + 1]) / 2)
    bpy.context.scene.frame_set(int(frame), subframe=frame % 1)
    bpy.context.view_layer.update()
    for spec in specs:
        obj = bpy.data.objects[spec["name"]]
        if obj.parent.type == "MESH" and not obj.hide_render:
            end = obj.parent.matrix_world @ Vector((0, 0, obj.parent["fixed_length_camera_units"]))
            connection_error = max(connection_error, float((obj.matrix_world.translation - end).length))
    for part_name in hand_root_constraints:
        hand_root = bpy.data.objects["WB_Root_" + part_name]
        constraint = hand_root.constraints["Same_side_body_wrist"]
        if constraint.influence > .5 and not hand_root.hide_render:
            connection_error = max(connection_error, float((hand_root.matrix_world.translation - constraint.target.matrix_world.translation).length))
if connection_error > 1e-6:
    raise ValueError("FK or hand wrist connection detached between source samples: " + str(connection_error))
glb = None
if job["preview_glb"]:
    index = job["preview_frame"]
    frame = float(frame_numbers[index])
    bpy.context.scene.frame_set(int(frame), subframe=frame % 1)
    bpy.context.view_layer.update()
    bpy.ops.object.select_all(action="DESELECT")
    for obj in bpy.data.collections["WholeBody_Fixed_Capsules"].objects:
        if obj.type != "MESH":
            obj.hide_viewport = False
            obj.select_set(True)
    for spec in specs:
        obj = bpy.data.objects[spec["name"]]
        if motion["available"][index, spec["child"]]:
            obj.hide_viewport = False
            obj.select_set(True)
    glb = out / "wholebody_preview.glb"
    bpy.ops.export_scene.gltf(filepath=str(glb), export_format="GLB", use_selection=True, export_animations=False)
    if not glb.is_file() or glb.read_bytes()[:4] != b"glTF":
        raise ValueError("GLB preview was not created")
b2g = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]])
write_json(out / "build_report.json", {"frames": len(times), "capsules": len(specs),
    "finger_segments_per_hand": {side: sum(spec["part"] == side + "_hand" for spec in specs) for side in ("left", "right")},
    "standalone_blend": str(standalone), "integrated_blend": str(integrated) if integrated else None,
    "original_objects_preserved": len(original_names), "original_file_sha256": original_hash,
    "reopened_standalone": True, "reopened_integrated": bool(integrated),
    "immutable_meshes": True, "max_saved_endpoint_error": maximum_error,
    "parented_fixed_endpoint_fk": True, "midframe_connection_max_error": connection_error,
    "body_wrist_constraints": hand_root_constraints,
    "glb_preview": str(glb) if glb else None, "glb_preview_source_time_seconds": float(times[job["preview_frame"]]) if glb else None,
    "world_to_blender": job["world_to_blender"], "world_to_glb": (b2g @ transform).tolist() if glb else None,
    "glb_note": "Static inspection preview at stated time; inspect world_to_glb before reusing in the original workbench" if glb else None,
    "files": {path.name: sha(path) for path in (standalone, integrated, glb) if path is not None}})
print("WHOLEBODY_BUILD_COMPLETE")

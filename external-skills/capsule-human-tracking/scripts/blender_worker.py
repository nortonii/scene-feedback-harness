"""Blender 4.5 worker. Invoked with --python ... -- task.json; no project imports."""
import sys,json,math,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import bpy,numpy as np
from mathutils import Vector,Matrix
from common import arrays,endpoint,rotation,write_json,sha

JOB=json.loads(Path(sys.argv[sys.argv.index('--')+1]).read_text())

def ref(source,index):return {'source':source,'weights':[[index,1.0]]}
def body(i):return ref('body_joints',i)
def specs_mhr():
    specs={'Pelvis':(body(2),body(18)),'Abdomen':({'source':'body_joints','weights':[[1,.35],[36,.65]]},body(36)),'Chest':(body(36),body(37)),'Shoulders':(body(75),body(39)),'Neck':(body(37),body(110))}
    for side,hip,knee,ankle,toe,shoulder,elbow in [('Left',2,3,4,8,75,76),('Right',18,19,20,24,39,40)]:
        h=lambda i:ref(side.lower()+'_hand',i)
        for name,a,b in [('Thigh',body(hip),body(knee)),('Shin',body(knee),body(ankle)),('Foot',body(ankle),body(toe)),('UpperArm',body(shoulder),body(elbow)),('Forearm',body(elbow),h(5)),('Palm',h(5),h(20))]:specs[side+'_'+name]=(a,b)
        chains={'Thumb':[5,6,7,0],'Index':[20,8,9,10,1],'Middle':[20,11,12,13,2],'Ring':[20,14,15,16,3],'Pinky':[20,17,18,19,4]}
        for name,chain in chains.items():
            for k,(a,b) in enumerate(zip(chain[:-1],chain[1:])):specs[f'{side}_{name}_{k+1:02}']=(h(a),h(b))
    return specs

def extract():
    bpy.ops.wm.open_mainfile(filepath=JOB['human']);m=arrays(JOB['motion']);s=bpy.context.scene;s.frame_set(1);bpy.context.view_layer.update()
    out=Path(JOB['output']);defs=specs_mhr();caps=[];R=rotation(m['pose_state'][:1,3:6])[0]
    for o in bpy.data.collections['05_Capsule_Human'].objects:
        if o.type!='MESH':continue
        if o.data.shape_keys or o.modifiers:raise ValueError(f'Expected immutable source capsule: {o.name}')
        name=o.name.removeprefix('CH_');length=float(o['capsule_length_source_units'])
        a=np.array(o.location);b=np.array(o.matrix_basis@Vector((0,0,length)))
        if name=='Head':ab=tuple({'torso_offset':((p-m['pose_state'][0,:3])@R).tolist()} for p in [a,b])
        else:ab=defs[name]
        for spec,expected in zip(ab,[a,b]):
            if np.max(abs(endpoint(spec,m)[0]-expected))>1e-5:raise ValueError(f'Adapter does not match {name}; supply explicit endpoint mapping')
        caps.append({'name':o.name,'a':ab[0],'b':ab[1],'radius':float(o['capsule_radius_source_units']),'length':length,'color':list(o.color),'vertices':[list(v.co) for v in o.data.vertices],'faces':[list(p.vertices) for p in o.data.polygons]})
    root=bpy.data.objects['Capsule_Human_Root'];mat=[]
    for f in range(len(m['body_joints'])):
        s.frame_set(f+1);bpy.context.view_layer.update();mat.append(np.array(root.matrix_world))
    write_json(out/'template.json',{'schema_version':1,'profile':'mhr127-hot3d21','capsules':caps,'note':'Exported immutable vertices and explicit endpoint references; geometry changes require a consistent template revision.'})
    np.savez_compressed(out/'alignment.npz',actor_to_scene=np.asarray(mat))
    write_json(out/'extraction.json',{'source_human_sha256':sha(JOB['human']),'capsules':len(caps),'alignment_frames':len(mat)})

def animate(o,locations,quaternions,scales=None):
    o.rotation_mode='QUATERNION';o.animation_data_create();action=bpy.data.actions.new(o.name+'_Motion');o.animation_data.action=action
    q=np.asarray(quaternions).copy()
    for f in range(1,len(q)):
        if q[f]@q[f-1]<0:q[f]*=-1
    fields=[('location',locations),('rotation_quaternion',q)]
    if scales is not None:
        if np.max(abs(scales-scales[0]))>1e-6:raise ValueError('Actor alignment scale must be constant over time')
        o.scale=scales[0]
    for path,values in fields:
        for axis in range(values.shape[1]):
            fc=action.fcurves.new(path,index=axis);fc.keyframe_points.add(len(values));xy=np.c_[np.arange(1,len(values)+1),values[:,axis]].astype('float32');fc.keyframe_points.foreach_set('co',xy.ravel())
            for p in fc.keyframe_points:p.interpolation='LINEAR'
            fc.update()
    o.animation_data.action_slot=action.slots[0]

def inventory(names=None):
    result={}
    for o in bpy.context.scene.objects:
        if names is not None and o.name not in names:continue
        a={'type':o.type,'matrix':np.array(o.matrix_world).tolist(),'hide':[o.hide_render,o.hide_viewport]}
        if o.type=='MESH':
            v=np.array([list(v.co) for v in o.data.vertices],dtype='float32');a.update({'vertices':hashlib.sha256(v.tobytes()).hexdigest(),'faces':[list(p.vertices) for p in o.data.polygons],'materials':[m.name if m else None for m in o.data.materials]})
        if o.type=='CAMERA':a['camera']=[o.data.lens,o.data.shift_x,o.data.shift_y,o.data.sensor_width]
        result[o.name]=a
    return result

def build():
    c=JOB['config'];m=arrays(JOB['motion']);out=Path(JOB['output']);n=len(m['body_joints']);template=json.loads(Path(c['paths']['template']).read_text());alignment=arrays(c['paths']['alignment'])['actor_to_scene'][:n]
    original=c['paths']['static_scene'];original_hash=sha(original);bpy.ops.wm.open_mainfile(filepath=original);s=bpy.context.scene;s.frame_set(1);bpy.context.view_layer.update();before=inventory();names=list(before)
    if bpy.data.objects.get('Capsule_Human_Root'):raise ValueError('Input scene already contains an actor; provide the preserved static scene')
    col=bpy.data.collections.new('05_Capsule_Human');s.collection.children.link(col);root=bpy.data.objects.new('Capsule_Human_Root',None);col.objects.link(root)
    decomp=[Matrix(x.tolist()).decompose() for x in alignment];animate(root,np.array([x[0] for x in decomp]),np.array([x[1] for x in decomp]),np.array([x[2] for x in decomp]))
    absolute=[];mesh_hashes={}
    for spec in template['capsules']:
        a=endpoint(spec['a'],m);b=endpoint(spec['b'],m);length=np.linalg.norm(b-a,axis=1)
        if np.max(abs(length-spec['length']))>1e-5:raise ValueError(f'Fixed length mismatch: {spec["name"]}')
        mesh=bpy.data.meshes.new(spec['name']+'_Mesh');mesh.from_pydata(spec['vertices'],[],spec['faces']);mesh.update();o=bpy.data.objects.new(spec['name'],mesh);col.objects.link(o);o.parent=root
        mat=bpy.data.materials.new(spec['name']+'_Material');mat.diffuse_color=spec['color'];mesh.materials.append(mat);o.color=spec['color']
        for poly in mesh.polygons:poly.use_smooth=True
        quats=np.array([list(Vector(v).to_track_quat('Z','Y')) for v in b-a]);animate(o,a,quats)
        o['capsule_radius_source_units']=spec['radius'];o['capsule_length_source_units']=spec['length'];o['geometry']='Immutable mesh; animated location and quaternion only'
        transforms=[]
        for pos,v in zip(a,b-a):
            M=Vector(v).to_track_quat('Z','Y').to_matrix().to_4x4();M.translation=Vector(pos);transforms.append(np.array(M))
        absolute.append(transforms);mesh_hashes[o.name]=hashlib.sha256(np.asarray(spec['vertices'],dtype='float32').tobytes()).hexdigest()
    absolute=np.stack(absolute,1);delta=np.broadcast_to(np.eye(4),absolute.shape).copy();delta[1:]=absolute[1:]@np.linalg.inv(absolute[:-1])
    np.savez_compressed(out/'capsule_motion_increments.npz',object_names=np.array([x['name'] for x in template['capsules']]),initial_transforms=absolute[0],relative_transform=delta,coordinate_frame='actor-local; T[t] = relative_transform[t] @ T[t-1]')
    s.frame_start=1;s.frame_end=n;s.render.fps=c['fps'];s.frame_set(1);bpy.context.view_layer.update()
    if inventory(names)!=before:raise ValueError('Original scene inventory changed during build')
    points=np.array([tuple(o.matrix_world@Vector(v)) for o in col.objects if o.type=='MESH' for v in o.bound_box]);center=(points.min(0)+points.max(0))/2;extent=max(np.ptp(points,axis=0))
    cam=bpy.data.objects.new('Capsule_Inspection_Camera',bpy.data.cameras.new('Capsule_Inspection_Camera'));s.collection.objects.link(cam);cam.location=Vector(center+np.array([1.4,-2,1])*extent);cam.rotation_euler=(Vector(center)-cam.location).to_track_quat('-Z','Y').to_euler();cam.data.type='ORTHO';cam.data.ortho_scale=extent*1.55;s.camera=cam
    text=bpy.data.texts.new('CAPSULE_HUMAN_README');text.write('Fixed geometry capsule actor. First pose initializes motion; subsequent poses use previous-state increments. Hidden anatomy and scene scale remain approximate.\nConfiguration: '+c['_file'])
    bpy.ops.file.pack_all();integrated=out/'static_scene_with_capsule_human.blend';bpy.ops.wm.save_as_mainfile(filepath=str(integrated),compress=True)
    for name in names:bpy.data.objects.remove(bpy.data.objects[name],do_unlink=True)
    standalone=out/'capsule_human_only.blend';bpy.ops.wm.save_as_mainfile(filepath=str(standalone),compress=True)
    # Reopen both deliveries. Check original inventory and immutable mesh data.
    bpy.ops.wm.open_mainfile(filepath=str(integrated));bpy.context.scene.frame_set(1);bpy.context.view_layer.update()
    if inventory(names)!=before:raise ValueError('Original scene changed after reopening')
    bpy.ops.wm.open_mainfile(filepath=str(standalone));actor=bpy.data.collections['05_Capsule_Human'];caps=[o for o in actor.objects if o.type=='MESH']
    for o in caps:
        if o.data.shape_keys or o.modifiers or tuple(o.scale)!=(1.,1.,1.):raise ValueError('Actor geometry deforms')
        if hashlib.sha256(np.array([list(v.co) for v in o.data.vertices],dtype='float32').tobytes()).hexdigest()!=mesh_hashes[o.name]:raise ValueError('Template mesh changed')
        if any(len(fc.keyframe_points)!=n or fc.data_path not in ['location','rotation_quaternion'] for fc in o.animation_data.action.fcurves):raise ValueError('Unexpected capsule animation')
    err=0.
    for f in sorted(set([0,n//2,n-1])):
        bpy.context.scene.frame_set(f+1);bpy.context.view_layer.update()
        for spec in template['capsules']:
            o=bpy.data.objects[spec['name']];err=max(err,float(np.max(abs(np.array(o.matrix_basis)-absolute[f,[x['name'] for x in template['capsules']].index(o.name)]))))
    if err>1e-5 or sha(original)!=original_hash:raise ValueError('Animation matrix or input-file verification failed')
    write_json(out/'build_report.json',{'frames':n,'capsules':len(caps),'original_objects_preserved':len(names),'original_file_sha256':original_hash,'template_meshes_unchanged':True,'reopened_both_blends':True,'sampled_local_matrix_max_error':err,'files':{p.name:sha(p) for p in [standalone,integrated]}})

def render():
    c=JOB['config'];out=Path(JOB['output']);cal=arrays(c['paths']['cameras']);K=cal['K']*c['image_scale'];K[:,2,2]=1;W,H=c['image_size'];frames=JOB['frames']
    for label,path in [('before',JOB['before']),('after',JOB['after'])]:
        bpy.ops.wm.open_mainfile(filepath=path);s=bpy.context.scene
        if max(frames)+1>s.frame_end or min(frames)+1<s.frame_start:raise ValueError('Requested review frame outside the supplied blend timeline')
        root=bpy.data.objects['Capsule_Human_Root'];s.render.engine='BLENDER_WORKBENCH';s.display.render_aa='8';s.display.shading.light='STUDIO';s.display.shading.color_type='MATERIAL';s.display.shading.show_shadows=False;s.display.shading.show_cavity=True;s.display.shading.cavity_type='BOTH';s.display.shading.show_object_outline=True;s.render.film_transparent=True;s.render.image_settings.file_format='PNG';s.render.resolution_x=W;s.render.resolution_y=H;s.render.resolution_percentage=100
        cam=bpy.data.objects.new('Review_Camera',bpy.data.cameras.new('Review_Camera'));s.collection.objects.link(cam);s.camera=cam
        for ci in JOB['views']:
            dest=out/label/f'h{ci}';dest.mkdir(parents=True,exist_ok=True);k=K[ci];cam.data.sensor_fit='HORIZONTAL';cam.data.sensor_width=36;cam.data.lens=k[0,0]*36/W;cam.data.shift_x=(W/2-k[0,2])/W;cam.data.shift_y=(k[1,2]-H/2)*(k[0,0]/k[1,1])/W;cam.data.clip_start=.005;s.render.pixel_aspect_x=1;s.render.pixel_aspect_y=k[0,0]/k[1,1]
            for f in frames:
                s.frame_set(f+1);bpy.context.view_layer.update();pose=np.array(root.matrix_world)@cal['camera_to_scene'][ci,f];pose[:3,:3]/=np.cbrt(abs(np.linalg.det(pose[:3,:3])));pose[:3,:3]=pose[:3,:3]@np.diag([1,-1,-1]);cam.matrix_world=Matrix(pose.tolist());s.render.filepath=str(dest/f'{f:05d}.png');bpy.ops.render.render(write_still=True)
    write_json(out/'render_report.json',{'frames':frames,'views':JOB['views'],'renders':len(frames)*len(JOB['views'])*2,'before':JOB['before'],'after':JOB['after']})

{'extract':extract,'build':build,'render':render}[JOB['action']]()
print('CAPSULE_TOOL_COMPLETE',JOB['action'],flush=True)

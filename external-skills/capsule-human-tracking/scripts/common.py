"""Shared config, array and fixed-geometry validation. No Blender dependency."""
from pathlib import Path
import json, hashlib
import numpy as np

LEG_EDGES=[(2,3),(3,4),(18,19),(19,20)]
BODY_EDGES=[(75,76),(76,77),(39,40),(40,41)]+LEG_EDGES
FOOT_EDGES=[(3,4,8),(19,20,24)]
HAND_EDGES=[(5,20),(5,6),(6,7),(7,0),(20,8),(8,9),(9,10),(10,1),(20,11),(11,12),(12,13),(13,2),(20,14),(14,15),(15,16),(16,3),(20,17),(17,18),(18,19),(19,4)]

def arrays(path):
    with np.load(path,allow_pickle=False) as z:return {k:z[k] for k in z.files}

def write_json(path,data):
    Path(path).write_text(json.dumps(data,indent=2,ensure_ascii=False,allow_nan=False)+'\n')

def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def config(path):
    path=Path(path).resolve();c=json.loads(path.read_text())
    if c.get('schema_version')!=1:raise ValueError('Expected schema_version=1')
    if c.get('profile')!='mhr127-hot3d21':raise ValueError('Unsupported topology; supply an explicit adapter rather than guessing indices')
    for k,v in c['paths'].items():
        if isinstance(v,list):c['paths'][k]=[str((path.parent/x).resolve()) for x in v]
        elif v is not None:c['paths'][k]=str((path.parent/v).resolve())
    c['_file']=str(path);return c

def fresh(path):
    path=Path(path).resolve()
    if path.exists() and any(path.iterdir()):raise ValueError(f'Output is not empty; choose a new run directory: {path}')
    path.mkdir(parents=True,exist_ok=True);return path

def rotation(euler):
    x,y,z=np.asarray(euler).T;sx,cx=np.sin(x),np.cos(x);sy,cy=np.sin(y),np.cos(y);sz,cz=np.sin(z),np.cos(z)
    return np.stack([cz*cy,cz*sy*sx-sz*cx,cz*sy*cx+sz*sx,sz*cy,sz*sy*sx+cz*cx,sz*sy*cx-cz*sx,-sy,cy*sx,cy*cx],-1).reshape(-1,3,3)

def endpoint(spec,m):
    if 'source' in spec:
        a=m[spec['source']];return sum(float(w)*a[:,int(i)] for i,w in spec['weights'])
    return m['pose_state'][:,:3]+np.einsum('fij,j->fi',rotation(m['pose_state'][:,3:6]),np.array(spec['torso_offset']))

def motion_report(m,reference=None,prefix=None):
    required=['body_joints','left_hand','right_hand','pose_state','motion_increment','initial_state','segment_lengths','radii','rest_body']
    for k in required:
        if k not in m or not np.isfinite(m[k]).all():raise ValueError(f'Missing/nonfinite motion field: {k}')
    n=len(m['body_joints'])
    expected={'body_joints':(n,127,3),'left_hand':(n,21,3),'right_hand':(n,21,3),'pose_state':(n,22),'motion_increment':(n,22),'initial_state':(22,),'segment_lengths':(8,),'radii':(4,2),'rest_body':(127,3)}
    for k,shape in expected.items():
        if m[k].shape!=shape:raise ValueError(f'{k}: expected {shape}, got {m[k].shape}')
    if n<1 or (m['radii']<=0).any() or (m['segment_lengths']<=0).any():raise ValueError('Empty sequence or nonpositive dimensions')
    ls=np.stack([np.linalg.norm(m['body_joints'][:,b]-m['body_joints'][:,a],axis=-1) for a,b in BODY_EDGES],1)
    bone=float(abs(ls-m['segment_lengths']).max())
    rec=float(abs(np.diff(m['pose_state'],axis=0)-m['motion_increment'][1:]).max()) if n>1 else 0.
    initial=float(abs(m['pose_state'][0]-m['initial_state']-m['motion_increment'][0]).max())
    # Confirm the recorded state actually generates the delivered leg geometry.
    aa=m['pose_state'][:,14:].reshape(n,4,2);ds=np.stack([np.cos(aa[:,:,0])*np.cos(aa[:,:,1]),np.sin(aa[:,:,0])*np.cos(aa[:,:,1]),np.sin(aa[:,:,1])],-1)
    fk=max(float(abs(m['body_joints'][:,b]-m['body_joints'][:,a]-m['segment_lengths'][i+4]*ds[:,i]).max()) for i,(a,b) in enumerate(LEG_EDGES))
    hands={}
    for side in ['left','right']:
        h=m[side+'_hand'];length=np.stack([np.linalg.norm(h[:,b]-h[:,a],axis=-1) for a,b in HAND_EDGES],1)
        hands[side]=float(np.ptp(length,axis=0).max())
    feet=max(float(np.ptp(np.linalg.norm(m['body_joints'][:,t]-m['body_joints'][:,a],axis=1))) for _,a,t in FOOT_EDGES)
    if max(bone,rec,initial,fk,feet,*hands.values())>1e-7:raise ValueError(f'Fixed geometry/recurrence/FK validation failed: {bone,rec,initial,fk,feet,hands}')
    report={'frames':n,'bone_length_max_error':bone,'recurrence_max_error':rec,'initial_increment_max_error':initial,'leg_fk_max_error':fk,'hand_length_ranges':hands,'foot_length_range':feet}
    if n>2:
        j=m['body_joints'][:,[3,4,19,20]];report['leg_second_difference_p95_source_units']=float(np.percentile(np.linalg.norm(np.diff(j,n=2,axis=0),axis=-1),95));report['leg_max_step_source_units']=float(np.linalg.norm(np.diff(j,axis=0),axis=-1).max())
    if reference is not None:
        keep=[i for i in range(127) if i not in [3,4,8,19,20,24]]
        errors={'other_joints':float(abs(m['body_joints'][:,keep]-reference['body_joints'][:n,keep]).max())}
        for k in ['left_hand','right_hand']:errors[k]=float(abs(m[k]-reference[k][:n]).max())
        errors['root_torso_arms_state']=float(abs(m['pose_state'][:,:14]-reference['pose_state'][:n,:14]).max())
        for k in ['radii','segment_lengths']:errors[k]=float(abs(m[k]-reference[k]).max())
        if max(errors.values())>1e-10:raise ValueError(f'Leg-only change exceeded scope: {errors}')
        report['leg_only_scope_errors']=errors
    if prefix is not None:
        p=len(prefix['body_joints'])
        if p>n:raise ValueError('Prefix longer than full sequence')
        report['prefix_errors']={k:float(abs(m[k][:p]-prefix[k]).max()) for k in ['body_joints','pose_state','motion_increment']}
        if max(report['prefix_errors'].values())>1e-10:raise ValueError('Prefix replay differs from full run')
        report['prefix_frames']=p
    return report

def inspect(c):
    m=arrays(c['paths']['motion']);r=motion_report(m);cal=arrays(c['paths']['cameras']);n=r['frames']
    C=cal['camera_to_scene'];K=cal['K']
    if C.ndim!=4 or C.shape[1]<n or C.shape[2:]!=(4,4) or K.shape!=(len(C),3,3):raise ValueError('Camera shapes inconsistent with sequence')
    if not np.isfinite(C).all() or not np.isfinite(K).all():raise ValueError('Nonfinite camera calibration')
    if np.any(K[:,0,0]<=0) or np.any(K[:,1,1]<=0) or np.max(abs(K[:,0,1]))>1e-8 or np.max(abs(K[:,2]-[0,0,1]))>1e-8:raise ValueError('Expected positive focal lengths, zero skew, standard pinhole K')
    alignment=arrays(c['paths']['alignment'])['actor_to_scene']
    if alignment.shape[0]<n or alignment.shape[1:]!=(4,4) or not np.isfinite(alignment).all():raise ValueError('Alignment must contain at least F finite 4x4 transforms')
    basis=alignment[:n,:3,:3];scale=np.cbrt(np.linalg.det(basis))
    if (scale<=0).any() or np.ptp(scale)>1e-5 or np.max(abs(np.swapaxes(basis,1,2)@basis-scale[:,None,None]**2*np.eye(3)))>1e-5:raise ValueError('Alignment must use a positive, constant uniform scale and rigid rotation')
    if np.max(abs(np.linalg.det(C[:,:,:3,:3])-1))>1e-3:raise ValueError('Camera-to-actor rotations must be rigid; remove scale explicitly')
    if c['fps']<=0 or c['image_scale']<=0:raise ValueError('Invalid fps/image_scale')
    for key in ['masks','images']:
        if len(c['paths'][key])!=len(C):raise ValueError(f'{key} count must match camera count')
        for pattern in c['paths'][key]:
            for f in sorted(set([0,n//2,n-1])):
                if not Path(pattern.format(frame=f)).is_file():raise FileNotFoundError(pattern.format(frame=f))
    from PIL import Image
    for ci in range(len(C)):
        im=Image.open(c['paths']['images'][ci].format(frame=0));mask=Image.open(c['paths']['masks'][ci].format(frame=0))
        if list(im.size)!=c['image_size'] or mask.size!=im.size:raise ValueError('Image/mask resolution mismatch')
    if c['paths'].get('template'):
        t=json.loads(Path(c['paths']['template']).read_text())
        for spec in t['capsules']:
            lengths=np.linalg.norm(endpoint(spec['b'],m)-endpoint(spec['a'],m),axis=1)
            if np.ptp(lengths)>1e-6 or abs(lengths[0]-spec['length'])>1e-5:raise ValueError(f'Template length mismatch: {spec["name"]}')
        r['capsules']=len(t['capsules'])
    r.update({'views':len(C),'image_size':c['image_size'],'motion_sha256':sha(c['paths']['motion'])});return r

def capsule_mesh(length,radius,sides=16,rings=5):
    """True hemispherical caps joined by a cylinder, +Z aligned, fixed dimensions."""
    import math
    if radius<=0 or length<0:raise ValueError('Capsule radius must be positive and length nonnegative')
    verts=[(0,0,-radius)];faces=[]
    profiles=[(radius*math.cos(t),radius*math.sin(t),False) for t in np.linspace(-math.pi/2,0,rings+1)[1:]]+[(radius*math.cos(t),radius*math.sin(t),True) for t in np.linspace(0,math.pi/2,rings+1)[:-1]]
    for rad,z,upper in profiles:
        for k in range(sides):
            theta=2*math.pi*k/sides;verts.append((rad*math.cos(theta),rad*math.sin(theta),z+(length if upper else 0)))
    top=len(verts);verts.append((0,0,radius+length))
    for k in range(sides):faces.append((0,1+(k+1)%sides,1+k))
    for ring in range(len(profiles)-1):
        a=1+ring*sides;b=a+sides
        for k in range(sides):q=(k+1)%sides;faces.append((a+k,a+q,b+q,b+k))
    last=1+(len(profiles)-1)*sides
    for k in range(sides):faces.append((last+k,last+(k+1)%sides,top))
    return verts,faces

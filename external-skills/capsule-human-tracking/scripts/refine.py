"""Causal leg refinement from per-frame pants masks. MHR127/HOT3D21 adapter."""
from pathlib import Path
import numpy as np, cv2, time
from scipy.optimize import least_squares
from common import arrays, write_json, motion_report

def run(c, output, frames=None):
 from types import SimpleNamespace
 args=SimpleNamespace(frames=frames)
 base=arrays(c['paths']['motion']);cal=arrays(c['paths']['cameras'])
 C=cal['camera_to_scene'];K=cal['K']*c['image_scale'];K[:,2,2]=1
 N=min(args.frames or len(base['body_joints']),len(base['body_joints']));radii=base['radii'][2:];lengths=base['segment_lengths'][4:];W,HH=c['image_size'];px=W/512;cfg=c.get('fit',{});rows=np.arange(round(HH*cfg.get('rows_start_fraction',.6375)),round(HH*.99),max(1,round(4*px)));unit=c.get('source_unit_in_meters',1.0)
 T=np.linspace(0,1,24)[None,:,None];radius=np.repeat(radii,24,axis=1);edges=[(2,3),(3,4),(18,19),(19,20)]
 def forward(f,angles):
  q=base['body_joints'][f].copy();a=angles.reshape(4,2);ds=np.stack([np.cos(a[:,0])*np.cos(a[:,1]),np.sin(a[:,0])*np.cos(a[:,1]),np.sin(a[:,1])],1)
  for i,(u,v) in enumerate(edges):q[v]=q[u]+lengths[i]*ds[i]
  return q

 def project(q,f,ci):
  c=(q-C[ci,f,:3,3])@C[ci,f,:3,:3];uv=c[:,:2]/c[:,2:,].clip(.05)*[K[ci,0,0],K[ci,1,1]]+K[ci,:2,2];return uv,c

 def centers(q,f,ci):
  a=q[[2,3,18,19]].reshape(2,2,3);b=q[[3,4,19,20]].reshape(2,2,3)
  cs=(a[:,:,None]*(1-T)+b[:,:,None]*T).reshape(2,48,3)
  return (cs-C[ci,f,:3,3])@C[ci,f,:3,:3]

 def angular_error(cs,ci,side,pts):
  ray=np.c_[(pts-K[ci,:2,2])/[K[ci,0,0],K[ci,1,1]],np.ones(len(pts))];ray/=np.linalg.norm(ray,axis=1)[:,None]
  distance=np.linalg.norm(cs[side,:24],axis=1);direction=cs[side,:24]/distance[:,None]
  theta=np.arccos(np.clip(ray@direction.T,-1+1e-12,1-1e-12));rad=np.arcsin(np.clip(radius[side,:24]/distance,0,.99))
  return (theta-rad).min(1)*K[ci,0,0]

 angles=base['pose_state'][0,14:].copy();last=np.zeros(8);body=[];states=[];targets=[];valids=[];logs=[];start=time.time()
 for f in range(N):
  # Reuse accepted incremental shin motion from the prior causal pass; thighs
  # are corrected by current observations starting solely at the prior pose.
  if f:angles[[2,3,6,7]]+=base['motion_increment'][f,14:][[2,3,6,7]]
  prev=angles.copy();qprev=forward(f,prev);tar=np.zeros((len(C),2,len(rows),2));valid=np.zeros_like(tar,dtype=bool)
  for ci in range(len(C)):
   uv,cam=project(qprev,f,ci);knees=uv[[3,19]];split=int(np.clip(knees[:,0].mean(),40*px,W-40*px))
   mask=cv2.imread(c['paths']['masks'][ci].format(frame=f),0);
   if mask is None or mask.shape!=(HH,W):raise ValueError(f'Missing or incorrectly sized mask at view {ci}, frame {f}')
   pants=(mask&cfg.get('pants_bit',4))>0
   other=cv2.dilate(((mask&cfg.get('occluder_bits',11))>0).astype('uint8'),np.ones((max(1,round(9*px)),)*2,np.uint8))>0
   for side in range(2):
    kr=K[ci,0,0]*radii[side,0]/max(cam[[3,19][side],2],.1)
    for ri,y in enumerate(rows):
     if y<knees[side,1]-kr-20*px:continue
     xs=np.flatnonzero(pants[max(0,y-1):y+2].any(0));xs=xs[xs<split] if side==0 else xs[xs>=split]
     runs=np.split(xs,np.flatnonzero(np.diff(xs)>1)+1);runs=[a for a in runs if len(a)>=max(3,round(6*px))]
     if not runs:continue
     run=min(runs,key=lambda a:max(a[0]-knees[side,0],knees[side,0]-a[-1],0))
     # A separate sleeve run far from the tracked knee is never a leg.
     if max(run[0]-knees[side,0],knees[side,0]-run[-1],0)>kr+15*px:continue
     for edge,x in enumerate([run[0],run[-1]]):
      tar[ci,side,ri,edge]=x
      ok=3*px<x<W-3*px and not other[y,x]
      if side==0 and edge==1 and x>=split-5*px:ok=False
      if side==1 and edge==0 and x<=split+5*px:ok=False
      valid[ci,side,ri,edge]=ok
  train=valid.copy();train[:,:,np.arange(len(rows))%5==2]=False
  points=[]
  for ci in range(len(C)):
   for side in range(2):
    ri,edge=np.nonzero(train[ci,side]);pts=np.c_[tar[ci,side,ri,edge],rows[ri]]
    # Bound association in actual perspective, even for disconnected masks.
    err=angular_error(centers(qprev,f,ci),ci,side,pts) if len(pts) else np.array([])
    keep=abs(err)<28*px;points.append(pts[keep])
  lastq=body[-1][[3,4,19,20]] if f else qprev[[3,4,19,20]]
  def residual(delta4):
   delta=np.zeros(8);delta[[0,1,4,5]]=delta4;q=forward(f,prev+delta);res=[]
   for ci in range(len(C)):
    cs=centers(q,f,ci)
    for side in range(2):
     pts=points[ci*2+side]
     if len(pts):res.extend((angular_error(cs,ci,side,pts)/(3*px)/np.sqrt(len(pts))*3).tolist())
   res.extend((delta4/.04*.30).tolist());res.extend(((delta4-.35*last[[0,1,4,5]])/.025*.25).tolist())
   res.extend(((q[[3,4,19,20]]-lastq)/(.009/unit)*.30).ravel().tolist())
   return np.asarray(res)
  count=np.array([sum(len(points[ci*2+s]) for ci in range(len(C))) for s in range(2)]);bound=np.repeat(np.where(count>=8,.04,1e-12),2)
  if f==0:bound=np.repeat(np.where(count>=8,.18,1e-12),2)
  fit=least_squares(residual,np.zeros(4),bounds=(-bound,bound),max_nfev=25 if f else 50,loss='soft_l1',f_scale=1,ftol=.0005,xtol=.0001,gtol=.0005)
  accepted=.55*fit.x+.45*last[[0,1,4,5]] if f else fit.x
  accepted=np.where(np.repeat(count>=8,2),np.clip(accepted,-bound,bound),0)
  last=np.zeros(8);last[[0,1,4,5]]=accepted;angles=prev+last;q=forward(f,angles);body.append(q);states.append(angles.copy());targets.append(tar);valids.append(valid)
  logs.append({'frame':f,'accepted_samples':count.tolist(),'max_thigh_step_deg':float(np.rad2deg(abs(fit.x).max()))})
  if f==0:last[:]=0  # Initial alignment is not an observed velocity.
  if f%100==0:print('FIT',f,round(time.time()-start,1),count.tolist(),flush=True)
 body=np.array(body);states=np.array(states)
 def align(a,b):
  a=a/np.linalg.norm(a);b=b/np.linalg.norm(b);v=np.cross(a,b);c=a@b;V=np.array([[0,-v[2],v[1]],[v[2],0,-v[0]],[-v[1],v[0],0]])
  return np.eye(3)+V+V@V/max(1+c,1e-10)
 for knee,ankle,toe in [(3,4,8),(19,20,24)]:
  direction=base['body_joints'][0,ankle]-base['body_joints'][0,knee];offset=base['body_joints'][0,toe]-base['body_joints'][0,ankle]
  for f in range(N):
   nd=body[f,ankle]-body[f,knee];offset=align(direction,nd)@offset;body[f,toe]=body[f,ankle]+offset;direction=nd

 out={k:v[:N].copy() if v.ndim and len(v)==len(base['body_joints']) else v.copy() for k,v in base.items()}
 out['body_joints']=body;out['pose_state'][:,14:]=states
 out['motion_increment'][0]=out['pose_state'][0]-out['initial_state'];out['motion_increment'][1:]=np.diff(out['pose_state'],axis=0)
 output=Path(output);np.savez_compressed(output/'motion.npz',**out)
 observed={'target':np.stack(targets,1),'valid':np.stack(valids,1),'rows':rows}
 np.savez_compressed(output/'observations.npz',**observed)
 metrics={}
 for label,joints in [('before',base['body_joints'][:N]),('after',body)]:
  values=[[],[]];by_frame=[]
  for f in range(N):
   fe=[[],[]]
   for ci in range(len(C)):
    cs=centers(joints[f],f,ci)
    for side in range(2):
     mask=observed['valid'][ci,f,side].copy();mask[np.arange(len(rows))%5!=2]=False
     ri,edge=np.nonzero(mask);pts=np.c_[observed['target'][ci,f,side,ri,edge],rows[ri]]
     if len(pts):
      error=abs(angular_error(cs,ci,side,pts));values[side].extend(error.tolist());fe[side].extend(error.tolist())
   by_frame.append([float(np.mean(v)) if len(v) else None for v in fe])
  metrics[label]={'left_mean_approx_px':float(np.mean(values[0])) if values[0] else None,'right_mean_approx_px':float(np.mean(values[1])) if values[1] else None,'counts':[len(v) for v in values]}
  write_json(output/f'{label}_frame_errors.json',by_frame)
 report=motion_report(out,reference=base)
 report.update({'method':'Previous-knee mask association; perspective tangency; bounded and damped thigh increments','runtime_s':time.time()-start,'heldout_boundary':metrics,'metric_units':'angular tangent distance times focal length; approximate image pixels','causality_scope':'Leg refinement is causal conditioned on the supplied root/torso/arm/shin trajectory; upstream causality requires independent provenance.','requires_visual_review':True})
 write_json(output/'report.json',report);write_json(output/'frame_log.json',logs)
 return report

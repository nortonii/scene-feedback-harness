#!/usr/bin/env python3
"""CLI for the capsule-human-tracking skill. See --help and references/schema.md."""
import argparse,json,os,shutil,subprocess,sys
from pathlib import Path
from common import config,arrays,write_json,sha,fresh,inspect,motion_report,endpoint,capsule_mesh

HERE=Path(__file__).resolve().parent

def blender_binary(value=None):
    if value:return str(Path(value).resolve())
    value=os.environ.get('BLENDER_BIN') or shutil.which('blender')
    if value:return value
    choices=sorted((Path.home()/'.local/opt').glob('blender-*/blender'))
    if choices:return str(choices[-1])
    raise ValueError('Supply --blender /path/to/blender (validated with 4.5.3)')

def worker(job,binary):
    out=Path(job['output']);jobfile=out/(job['action']+'_job.json');write_json(jobfile,job)
    log=out/(job['action']+'_blender.log')
    with log.open('w') as f:r=subprocess.run([blender_binary(binary),'-b','-t','8','--python',str(HERE/'blender_worker.py'),'--',str(jobfile)],stdout=f,stderr=subprocess.STDOUT)
    if r.returncode or 'CAPSULE_TOOL_COMPLETE '+job['action'] not in log.read_text():raise RuntimeError(f'Blender worker failed; inspect {log}')

def import_case(a):
    case=Path(a.case).resolve();src=Path(a.source).resolve();out=fresh(a.output);inp=out/'inputs';inp.mkdir()
    manifest=json.loads((case/'manifest.json').read_text());static=Path(a.static_scene or manifest['input_blend']).resolve()
    for origin,name in [(case/'tracked_motion.npz','baseline_motion.npz'),(case/'capsule_human_only.blend','baseline_human.blend'),(src/'cameras_refined.npz','cameras.npz'),(static,'static_scene.blend')]:shutil.copy2(origin,inp/name)
    c={'schema_version':1,'profile':'mhr127-hot3d21','fps':manifest.get('fps',60),'image_scale':.5,'image_size':[512,640],'source_unit_in_meters':1.0,'scale_note':'Inherited source convention; no independent metric calibration.','paths':{'motion':'inputs/baseline_motion.npz','base_human':'inputs/baseline_human.blend','cameras':'inputs/cameras.npz','static_scene':'inputs/static_scene.blend','template':'inputs/template.json','alignment':'inputs/alignment.npz','masks':[str(src.parent/f'masks/h{i}/{{frame:05d}}.png') for i in range(2)],'images':[str(src.parent/f'frames/h{i}/{{frame:05d}}.jpg') for i in range(2)]},'fit':{'pants_bit':4,'occluder_bits':11,'rows_start_fraction':.6375},'provenance':{'case':str(case),'source':str(src),'upstream_causality':'SPI102 fixed-geometry tracker; prefix tests recorded in source case.','sam3d_use':'First template initialization upstream; leg refinement never loads per-frame SAM3D geometry.'}}
    worker({'action':'extract','human':str(inp/'baseline_human.blend'),'motion':str(inp/'baseline_motion.npz'),'output':str(inp)},a.blender)
    path=out/'config.json';write_json(path,c);report=inspect(config(path));write_json(out/'inspection.json',report)
    return {'config':str(path),'inspection':report}

def review_package(c,out,frames,views,video):
    from PIL import Image,ImageDraw
    from html import escape
    out=Path(out);w,h=c['image_size'];thumb=[]
    for ci in views:
        source=out/'source'/f'h{ci}';source.mkdir(parents=True,exist_ok=True)
        for f in frames:
            shutil.copy2(c['paths']['images'][ci].format(frame=f),source/f'{f:05d}.jpg')
    def comparison(ci,f):
        canvas=Image.new('RGB',(w*2,h+32),(20,28,39));d=ImageDraw.Draw(canvas)
        for x,stage in [(0,'before'),(w,'after')]:
            im=Image.open(out/f'source/h{ci}/{f:05d}.jpg').convert('RGBA');actor=Image.open(out/f'{stage}/h{ci}/{f:05d}.png').convert('RGBA');actor.putalpha(actor.getchannel('A').point(lambda a:int(a*.65)));canvas.paste(Image.alpha_composite(im,actor).convert('RGB'),(x,32));d.text((x+8,8),f'{stage.upper()} | camera {ci} | source frame {f}',fill='white')
        return canvas
    for ci in views:
        for f in sorted(set([frames[0],frames[len(frames)//2],frames[-1]])):
            fn=f'comparison_h{ci}_{f:05d}.jpg';comparison(ci,f).save(out/fn,quality=93);thumb.append(fn)
    if video:
        if frames!=list(range(frames[0],frames[-1]+1)):raise ValueError('Video requires a contiguous frame selection to avoid false timing')
        if not shutil.which('ffmpeg'):raise ValueError('ffmpeg required for --video')
        for ci in views:
            cmd=['ffmpeg','-hide_banner','-loglevel','error','-y','-f','rawvideo','-pix_fmt','rgb24','-s',f'{w*2}x{h+32}','-r',str(c['fps']),'-i','-','-c:v','libx264','-preset','fast','-crf','21','-pix_fmt','yuv420p','-movflags','+faststart',str(out/f'comparison_h{ci}.mp4')]
            proc=subprocess.Popen(cmd,stdin=subprocess.PIPE)
            try:
                for f in frames:proc.stdin.write(comparison(ci,f).tobytes())
            finally:proc.stdin.close()
            if proc.wait():raise RuntimeError('ffmpeg failed')
    state=json.dumps({'frames':frames,'views':views},separators=(',',':'))
    html='''<!doctype html><meta charset="utf-8"><title>Capsule Human review</title><style>body{max-width:1100px;margin:30px auto;background:#141c27;color:#e7eef6;font:16px system-ui}img,video{max-width:100%}.row{display:flex;gap:10px}.pane{position:relative;width:50%}.pane img{width:100%;display:block}.actor{position:absolute;inset:0;opacity:.65}button,input,select{margin:8px;padding:8px}#slider{width:60%}</style><h1>Capsule Human · before / after</h1><p>Fixed geometry, previous-state motion increments. Occluded anatomy remains inferred. Source frames are zero-based; Blender frames are source + 1.</p><select id="view"></select><button id="prev">Previous</button><button id="next">Next</button><input id="slider" type="range"><p id="status"></p><label>Overlay <input id="alpha" type="range" min="0" max="100" value="65"></label><div class="row"><div class="pane"><img id="src0"><img id="old" class="actor"></div><div class="pane"><img id="src1"><img id="new" class="actor"></div></div>'''
    if video:html+=''.join(f'<h2>Camera {ci}</h2><video controls loop src="comparison_h{ci}.mp4"></video>' for ci in views)
    html+=''.join(f'<img loading="lazy" src="{escape(x)}">' for x in thumb)
    html+='''<script>const D=__DATA__,$=x=>document.getElementById(x);let k=0;D.views.forEach(v=>$('view').add(new Option('Camera '+v,v)));$('slider').min=0;$('slider').max=D.frames.length-1;function load(){let f=D.frames[k],n=String(f).padStart(5,'0'),v=$('view').value;$('src0').src=$('src1').src=`source/h${v}/${n}.jpg`;$('old').src=`before/h${v}/${n}.png`;$('new').src=`after/h${v}/${n}.png`;$('slider').value=k;$('status').textContent=`Frame ${f} / Blender ${f+1} · before (left), after (right)`}function set(n){k=Math.max(0,Math.min(D.frames.length-1,n));load()}$('prev').onclick=()=>set(k-1);$('next').onclick=()=>set(k+1);$('slider').oninput=e=>set(+e.target.value);$('view').onchange=load;$('alpha').oninput=e=>{$('old').style.opacity=$('new').style.opacity=e.target.value/100};load();</script>'''.replace('__DATA__',state)
    (out/'index.html').write_text(html)

def handoff(a):
    c=config(a.config);run=Path(a.run).resolve();out=fresh(a.output)
    skill_root=HERE.parent if (HERE.parent/'SKILL.md').exists() else HERE.parent/'skill'
    if not (skill_root/'SKILL.md').exists():raise ValueError('Handoff requires skill documentation beside scripts or in the bundle skill/ directory')
    if out.is_relative_to(skill_root):raise ValueError('Handoff output must be outside the skill source directory')
    inp=out/'inputs';inp.mkdir();tools=out/'tools';shutil.copytree(HERE,tools,ignore=shutil.ignore_patterns('__pycache__'));shutil.copytree(skill_root,out/'skill',ignore=shutil.ignore_patterns('__pycache__'))
    after=run/'build/capsule_human_only.blend'
    if not (run/'motion.npz').is_file() or not after.is_file():raise ValueError('Handoff requires run/motion.npz and run/build/capsule_human_only.blend')
    values={'motion':run/'motion.npz','base_human':after,'cameras':Path(c['paths']['cameras']),'template':Path(c['paths']['template']),'alignment':Path(c['paths']['alignment']),'static_scene':Path(c['paths']['static_scene'])}
    names={'motion':'motion.npz','base_human':'capsule_human_only.blend','cameras':'cameras.npz','template':'template.json','alignment':'alignment.npz','static_scene':'static_scene.blend'}
    for key,src in values.items():shutil.copy2(src,inp/names[key]);c['paths'][key]='inputs/'+names[key]
    n=len(arrays(run/'motion.npz')['body_joints'])
    for key in ['images','masks']:
        if a.copy_media:
            patterns=[]
            for ci,pattern in enumerate(c['paths'][key]):
                suffix=Path(pattern).suffix;dest=out/key/f'h{ci}';dest.mkdir(parents=True)
                for f in range(n):shutil.copy2(pattern.format(frame=f),dest/f'{f:05d}{suffix}')
                patterns.append(f'{key}/h{ci}/{{frame:05d}}{suffix}')
            c['paths'][key]=patterns
    c.pop('_file',None);write_json(out/'config.json',c)
    reports=out/'reports';reports.mkdir()
    for src in [run/'report.json',run/'build/build_report.json',run/'validation.json']:
        if src.exists():shutil.copy2(src,reports/src.name)
    delivery=out/'delivery';delivery.mkdir();shutil.copy2(run/'build/static_scene_with_capsule_human.blend',delivery/'static_scene_with_capsule_human.blend');shutil.copy2(run/'build/capsule_motion_increments.npz',delivery/'capsule_motion_increments.npz')
    invocation=f'python3 "{out}/tools/capsule.py"'
    (out/'ASTRA_HANDOFF.md').write_text(f'''# Astra handoff: fixed capsule human

Read `skill/SKILL.md` first, then `config.json`; all executable tools are bundled in `tools/`.
The current human is `inputs/capsule_human_only.blend`; the integrated scene is `delivery/static_scene_with_capsule_human.blend`.

```bash
{invocation} inspect --config "{out}/config.json"
{invocation} refine-legs --config "{out}/config.json" --output /tmp/capsule-next-run
{invocation} build --config "{out}/config.json" --motion /tmp/capsule-next-run/motion.npz --output /tmp/capsule-next-run/build
{invocation} review --config "{out}/config.json" --after /tmp/capsule-next-run/build/capsule_human_only.blend --output /tmp/capsule-next-run/review --frames 0,{min(n-1,850)},{n-1}
```

A new output directory is required for each run. Inspect the review in both views and compare reports before replacing a delivered artifact. These tools never publish to the live review server automatically.
Media {'is copied into this bundle' if a.copy_media else 'uses absolute references to the current host; use handoff --copy-media for transfer to another host'}.
The declared profile is MHR127/HOT3D21. Do not infer a different joint ordering.
Geometry is fixed across time; current observations correct motion increments from the previous accepted frame. Per-frame independent SAM3D meshes are not inputs to refinement. Upstream root/arm/shin causality depends on the supplied baseline provenance.
''')
    files={str(f.relative_to(out)):{'bytes':f.stat().st_size,'sha256':sha(f)} for f in out.rglob('*') if f.is_file()}
    write_json(out/'handoff_manifest.json',{'portable_media':a.copy_media,'profile':c['profile'],'frames':n,'files':files})
    return {'handoff':str(out/'ASTRA_HANDOFF.md'),'config':str(out/'config.json'),'portable_media':a.copy_media,'files':len(files)}

def edit_template(a):
    import numpy as np
    c=config(a.config);m=arrays(c['paths']['motion']);t=json.loads(Path(c['paths']['template']).read_text());matches=[x for x in t['capsules'] if x['name']==a.name]
    if len(matches)!=1:raise ValueError('Capsule name must uniquely match template inventory')
    if a.radius is None and a.a_json is None and a.b_json is None:raise ValueError('Provide a radius or endpoint edit')
    spec=matches[0]
    limb_radius={'CH_Left_UpperArm':(0,0),'CH_Left_Forearm':(0,1),'CH_Right_UpperArm':(1,0),'CH_Right_Forearm':(1,1),'CH_Left_Thigh':(2,0),'CH_Left_Shin':(2,1),'CH_Right_Thigh':(3,0),'CH_Right_Shin':(3,1)}
    if a.name in limb_radius and (a.a_json or a.b_json):raise ValueError('Fitted limb endpoints require a matching FK/topology adapter; torso endpoints may be edited directly')
    if a.radius is not None:spec['radius']=a.radius
    if a.a_json:spec['a']=json.loads(a.a_json)
    if a.b_json:spec['b']=json.loads(a.b_json)
    length=np.linalg.norm(endpoint(spec['b'],m)-endpoint(spec['a'],m),axis=1)
    if np.ptp(length)>1e-6:raise ValueError('Endpoint edit changes length across frames; revise the shared template or kinematic model')
    spec['length']=float(length[0]);spec['vertices'],spec['faces']=capsule_mesh(spec['length'],spec['radius'])
    out=fresh(a.output);write_json(out/'template.json',t);c.pop('_file',None);c['paths']['template']='template.json'
    if a.name in limb_radius and a.radius is not None:
        m['radii'][limb_radius[a.name]]=a.radius;np.savez_compressed(out/'motion.npz',**m);c['paths']['motion']='motion.npz'
    write_json(out/'config.json',c)
    write_json(out/'template_revision.json',{'capsule':a.name,'radius':spec['radius'],'length':spec['length'],'fixed_across_frames':True})
    return {'config':str(out/'config.json'),'template':str(out/'template.json'),'revision':spec['name']}

def main():
    ap=argparse.ArgumentParser(description=__doc__);sp=ap.add_subparsers(dest='command',required=True)
    p=sp.add_parser('import-spi102',help='Export a proven MHR capsule case into an explicit reusable config');p.add_argument('--case',required=True);p.add_argument('--source',required=True,help='reconstruction/ directory containing cameras_refined.npz');p.add_argument('--static-scene');p.add_argument('--output',required=True);p.add_argument('--blender')
    p=sp.add_parser('template-edit',help='Revise one shared capsule once, then use the new config');p.add_argument('--config',required=True);p.add_argument('--name',required=True);p.add_argument('--radius',type=float);p.add_argument('--a-json');p.add_argument('--b-json');p.add_argument('--output',required=True)
    p=sp.add_parser('inspect',help='Validate inputs, motion schema, template, and camera convention');p.add_argument('--config',required=True)
    p=sp.add_parser('refine-legs',help='Causal fixed-length leg silhouette refinement');p.add_argument('--config',required=True);p.add_argument('--output',required=True);p.add_argument('--frames',type=int)
    p=sp.add_parser('validate',help='Check fixed lengths, FK, increments, scope and independent prefix replay');p.add_argument('--motion',required=True);p.add_argument('--reference');p.add_argument('--prefix');p.add_argument('--report')
    p=sp.add_parser('build',help='Build standalone + integrated Blender, increments, and reopen/audit');p.add_argument('--config',required=True);p.add_argument('--motion',required=True);p.add_argument('--output',required=True);p.add_argument('--blender')
    p=sp.add_parser('review',help='Render before/after for both cameras; emit standalone review HTML');p.add_argument('--config',required=True);p.add_argument('--before');p.add_argument('--after',required=True);p.add_argument('--output',required=True);p.add_argument('--frames',default='0,mid,last',help='Comma-separated source frames, or all');p.add_argument('--views',default='all');p.add_argument('--video',action='store_true');p.add_argument('--blender')
    p=sp.add_parser('handoff',help='Bundle latest actor, config, code and provenance for Astra');p.add_argument('--config',required=True);p.add_argument('--run',required=True);p.add_argument('--output',required=True);p.add_argument('--copy-media',action='store_true')
    p=sp.add_parser('wholebody-reconstruct',help='Calibrated source-bound WholeBody133 -> causal fixed body and finger capsules');p.add_argument('--config',required=True);p.add_argument('--output',required=True);p.add_argument('--frames',type=int)
    p=sp.add_parser('wholebody-build',help='Save/reopen editable WholeBody body/hands; optional calibrated scene copy and static GLB');p.add_argument('--run',required=True);p.add_argument('--output',required=True);p.add_argument('--scene');p.add_argument('--world-to-blender-json',help='Explicit rigid camera-world -> Blender-world 4x4 JSON');p.add_argument('--preview-glb',action='store_true');p.add_argument('--preview-frame',type=int);p.add_argument('--blender')
    a=ap.parse_args()
    if a.command=='import-spi102':result=import_case(a)
    elif a.command=='template-edit':result=edit_template(a)
    elif a.command=='inspect':result=inspect(config(a.config))
    elif a.command=='refine-legs':
        c=config(a.config);inspect(c)
        if a.frames is not None and a.frames<1:raise ValueError('--frames must be positive')
        out=fresh(a.output);write_json(out/'invocation.json',{'config':str(Path(a.config).resolve()),'input_motion_sha256':sha(c['paths']['motion']),'frames':a.frames});from refine import run
        result=run(c,out,a.frames);result={'output':str(out),'report':result}
    elif a.command=='validate':
        result=motion_report(arrays(a.motion),arrays(a.reference) if a.reference else None,arrays(a.prefix) if a.prefix else None)
        if a.report:write_json(a.report,result)
    elif a.command=='build':
        c=config(a.config);m=arrays(a.motion);motion_report(m);out=fresh(a.output);worker({'action':'build','config':c,'motion':str(Path(a.motion).resolve()),'output':str(out)},a.blender);result=json.loads((out/'build_report.json').read_text())
    elif a.command=='review':
        c=config(a.config);n=len(arrays(c['paths']['motion'])['body_joints']);frames=list(range(n)) if a.frames=='all' else sorted(set(n//2 if x=='mid' else n-1 if x=='last' else int(x) for x in a.frames.split(',')))
        views=list(range(len(c['paths']['images']))) if a.views=='all' else [int(x) for x in a.views.split(',')]
        if not frames or min(frames)<0 or max(frames)>=n or not views or min(views)<0 or max(views)>=len(c['paths']['images']):raise ValueError('Frames/views out of range')
        if a.video and frames!=list(range(frames[0],frames[-1]+1)):raise ValueError('--video requires contiguous frames')
        out=fresh(a.output);worker({'action':'render','config':c,'before':str(Path(a.before).resolve()) if a.before else c['paths']['base_human'],'after':str(Path(a.after).resolve()),'frames':frames,'views':views,'output':str(out)},a.blender);review_package(c,out,frames,views,a.video);result={'review':str(out/'index.html'),'serve':f'python3 -m http.server 9071 --bind 127.0.0.1 --directory "{out}"','frames':len(frames),'views':views}
    elif a.command=='handoff':result=handoff(a)
    elif a.command=='wholebody-reconstruct':
        from wholebody_reconstruction import reconstruct_files
        result=reconstruct_files(a.config,a.output,a.frames)
    elif a.command=='wholebody-build':
        from wholebody_reconstruction import build_files
        transform=json.loads(Path(a.world_to_blender_json).read_text()) if a.world_to_blender_json else None
        result=build_files(a.run,a.output,blender=a.blender,scene=a.scene,world_to_blender=transform,preview_glb=a.preview_glb,preview_frame=a.preview_frame)
    print(json.dumps(result,indent=2,ensure_ascii=False))

if __name__=='__main__':
    try:main()
    except (ValueError,FileNotFoundError,RuntimeError,KeyError) as e:
        print(json.dumps({'error':str(e)},ensure_ascii=False),file=sys.stderr);sys.exit(1)

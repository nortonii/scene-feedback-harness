#!/usr/bin/env python3
"""Forward-test the separately released capsule skill against the workbench.

Only synthetic pixel observations are used; no model download, GPU inference,
Codex turn or persistent project is created. Skill subprocesses assert that
source-only commands do not import Torch.
"""
from __future__ import annotations
import argparse, base64, copy, io, json
from pathlib import Path
import subprocess, sys, tempfile
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description='Validate external capsule skill / workbench exchange with synthetic 2D observations; no inference or Codex task runs.')
parser.add_argument('--skill',type=Path,default=ROOT/'external-skills/capsule-human-tracking',help='Standalone capsule skill directory')
parser.add_argument('--python',type=Path,default=Path(sys.executable),help='Python for source-only skill commands; requires Pillow')
args=parser.parse_args()
SKILL=args.skill.expanduser().resolve(strict=True)
CLI_PYTHON=args.python.expanduser().resolve(strict=True)
sys.path[:0]=[str(ROOT/'backend'),str(ROOT)]
from PIL import Image
from server import make_server


def image_data(color):
    output=io.BytesIO();Image.new('RGB',(160,120),color).save(output,'PNG')
    return 'data:image/png;base64,'+base64.b64encode(output.getvalue()).decode()


def cli(*args,success=True):
    guard="import os,runpy,sys; entry=sys.argv.pop(1); sys.argv[0]=entry; sys.path.insert(0,os.path.dirname(entry)); runpy.run_path(entry,run_name='__main__'); assert 'torch' not in sys.modules, 'Source-only command imported torch'"
    result=subprocess.run([str(CLI_PYTHON),'-c',guard,str(SKILL/'scripts/pose_evidence.py'),*map(str,args)],capture_output=True,text=True,timeout=15)
    if success:
        assert result.returncode==0,(args,result.stderr,result.stdout)
        return json.loads(result.stdout)
    assert result.returncode!=0,(args,result.stdout)
    return result


with tempfile.TemporaryDirectory(prefix='capsule-workbench-forward-') as temp:
    root=Path(temp);project=root/'project';project.mkdir()
    server=make_server(port=0,data_dir=root/'data',project_dir=project,web_dir=ROOT/'web',external_review=True,feedback_transport='mcp_events')
    store=server.scene_store;workspace=server.workspace_gateway.ensure();session=workspace['session_id']
    try:
        camera={'camera_to_world':[[1,0,0,0],[0,1,0,0],[0,0,1,3],[0,0,0,1]],'intrinsics':{'width':160,'height':120,'fx':150,'fy':150,'cx':80,'cy':60}}
        first=store.set_reference_clip(session,{'name':'front','fps':2,'frames':[{'name':f'front_{i}.png','data_url':image_data('navy'),'time_sec':t,'camera':camera}for i,t in enumerate((0,.5,1))]})['reference_clip']
        clip=store.set_reference_clip(session,{'append_view':True,'name':'side','fps':2,'frames':[{'name':f'side_{i}.png','data_url':image_data('maroon'),'time_sec':t,'camera':camera}for i,t in enumerate((0,.5,1))]})['reference_clip']
        exported=server.workspace_gateway.pose_jobs.export_sources({'session_id':session});manifest=exported['manifest'];manifest_path=Path(exported['manifest_json_path'])
        assert 'options' not in manifest and 'keypoint_format' not in manifest
        assert manifest['project_dir']==str(project.resolve())
        assert len(manifest['frames'])==6 and len(manifest['view_ids'])==2
        source=cli('source-export','--manifest',manifest_path)
        assert source['source_frames']==6 and len(source['views'])==2
        print('PASS source export: neutral two-camera hashes/orientation/cameras validated without importing torch',flush=True)
        names=['head_observed','pelvis_observed'];edges=[[0,1]]
        observations={'evidence_kind':'observed_2d','keypoint_profile':'capsule-measured2-fixture','keypoint_names':names,'skeleton_edges':edges,
                      'project_id':manifest['project_id'],'session_id':session,'source_snapshot_id':manifest['source_snapshot_id'],
                      'provenance':{'kind':'manual_measurement','method':'synthetic pixel fixture; no real inference','source_artifact':'observed-pixels.json'},
                      'frames':[]}
        for frame in manifest['frames']:
            observations['frames'].append({**{key:frame[key]for key in('ref_id','view_id','frame_index','width','height','time_seconds','image_sha256','image_orientation')},
                'bbox_xywh':[30,10,100,105],'keypoints':[{'name':name,'xy_px':xy,'score':.9}for name,xy in zip(names,([50,20],[90,90]))]})
        observations_path=project/'observed-pixels.json';observations_path.write_text(json.dumps(observations))
        result_path=project/'pose-result.json'
        cli('adapt-observations','--manifest',manifest_path,'--observations',observations_path,'--output',result_path)
        report=cli('check-result','--manifest',manifest_path,'--result',result_path)
        assert report['frames']==6 and report['joint_count']==2 and report['evidence_kind']=='observed_2d'
        payload=cli('import-payload','--manifest',manifest_path,'--result',result_path)
        assert payload=={'job_id':manifest['job_id'],'result_path':str(result_path.resolve())}
        imported=server.workspace_gateway.pose_jobs.import_result(payload)
        assert imported['status']=='completed'and imported['skeleton_edges']==edges
        readback=server.workspace_gateway.pose_jobs.get(imported['job_id'],max_frames='all')
        assert len(readback['frames'])==6 and readback['keypoint_names']==names
        assert all(frame['image_sha256']==original['image_sha256']and frame['image_orientation']==original['image_orientation']for frame,original in zip(readback['frames'],manifest['frames']))
        handoff_path=project/'handoff';cli('handoff','--manifest',manifest_path,'--result',result_path,'--output',handoff_path)
        assert (handoff_path/'source_manifest.json').is_file()and(handoff_path/'pose_result.json').is_file()
        handoff=json.loads((handoff_path/'handoff.json').read_text());assert handoff['joint_map']is None
        frame=readback['frames'][-1]
        packet=store.submit_feedback(session,{'scene_revision':store.scene()['revision'],'note':f"Check [[pose:{imported['job_id']}:{frame['reference_id']}]]",'pose_refs':[{'job_id':imported['job_id'],'reference_id':frame['reference_id']}]})
        sample=packet['human_pose'][0];assert sample['keypoint_names']==names and sample['skeleton_edges']==edges and sample['evidence_kind']=='observed_2d'
        assert sample['frame']['image_sha256']==frame['image_sha256']
        assert(store.media_dir/sample['pose_overlay_url'].rsplit('/',1)[-1]).is_file()
        print('PASS adapt/check/import/handoff: named pixel observations reach real importer, readback and feedback with source origins intact',flush=True)
        baseline=json.loads(result_path.read_text())
        mutations={
            'wrong view':lambda d:d['frames'][0].__setitem__('view_id','wrong'),
            'wrong time':lambda d:d['frames'][0].__setitem__('time_seconds',.123),
            'wrong hash':lambda d:d['frames'][0].__setitem__('image_sha256','0'*64),
            'self edge':lambda d:d.__setitem__('skeleton_edges',[[0,0]]),
            'NaN':lambda d:d['frames'][0]['keypoints'][0].__setitem__('x',float('nan')),
        }
        for name,mutate in mutations.items():
            modified=copy.deepcopy(baseline);mutate(modified);negative=project/('negative-'+name.replace(' ','-')+'.json');negative.write_text(json.dumps(modified))
            cli('check-result','--manifest',manifest_path,'--result',negative,success=False)
        Image.new('RGB',(160,120),'white').save(manifest['frames'][0]['image_path'])
        cli('source-export','--manifest',manifest_path,success=False)
        print('PASS negatives: wrong view/time/hash, self edge, NaN and changed source-image bytes rejected before inference',flush=True)
    finally:
        server.server_close()
print('ALL CAPSULE SKILL FORWARD CHECKS PASSED',flush=True)

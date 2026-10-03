"""Isolated browser checks for inline decisions and camera navigation. No model calls."""
from pathlib import Path
import json
import struct
import sys
import tempfile
import threading
import uuid
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'), str(ROOT/'examples/room_demo')]
from core import SceneStore
from server import make_server
from build_scene import build
from playwright.sync_api import sync_playwright, expect
from workspace_ui_helpers import control, open_annotation_tools, choose_tool

class Decisions:
    def __init__(self): self.calls=[]; self.fail=True
    def respond_to_request(self, request, result):
        self.calls.append((request,result))
        if self.fail:
            self.fail=False
            raise ValueError('fixture temporary rejection')
    def close(self): pass

def main():
 with tempfile.TemporaryDirectory(prefix='navigation-approval-',dir=ROOT.parent/'tmp') as tmp:
  tmp=Path(tmp); seed=SceneStore(tmp/'data')
  session=seed.create_session(reference_images=[str(ROOT/'examples/room_demo/reference.png')])
  seed.set_scene_preview(str(build(ROOT/'examples/room_demo/scene.json',tmp/'room.glb')))
  server=make_server(port=0,data_dir=tmp/'data',project_dir=ROOT,web_dir=ROOT/'web',enable_codex=False)
  store=server.scene_store;gateway=server.workspace_gateway;gateway.ensure(session['session_id']);store.workspace_agent(status='idle')
  decisions=Decisions();gateway.adapter=decisions
  thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,args=['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader','--disable-accelerated-2d-canvas'])
    page=browser.new_page(viewport={'width':1440,'height':900});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    source=(ROOT/'web/app.js').read_text()+'''\nwindow.__navCheck={camera,controls,state,refreshWorkspace,pickScene,resolveSceneNode,objectBox,frameAll,
      position:()=>({p:camera.position.toArray(),t:controls.target.toArray(),up:camera.up.toArray()}),
      center:()=>{const node=resolveSceneNode(state.selectedSceneNode);return (node?new THREE.Box3().setFromObject(node):objectBox(state.selectedId)).getCenter(new THREE.Vector3()).toArray();},
      geometry:()=>[...state.objectNodes.values()].map(node=>node.matrixWorld.toArray())};'''
    page.route('**/app.js',lambda route:route.fulfill(status=200,content_type='text/javascript',body=source))
    page.goto(server.browser_url(session['session_id']))
    page.wait_for_function('window.__navCheck && __navCheck.state.workspaceReady && !__navCheck.state.sceneLoading')
    if page.locator('#chat-launcher').is_visible():control(page, '#chat-launcher').click()
    page.locator('#feedback-note').fill('保留输入草稿')
    if page.locator('#chat-history-toggle').get_attribute('aria-expanded')=='true':control(page, '#chat-history-toggle').click()
    control(page, '#chat-collapse').click()
    expect(page.locator('#chat-dock')).to_be_hidden()
    aid,bid=uuid.uuid4().hex,uuid.uuid4().hex
    approvals=[{'approval_id':aid,'request_id':'fixture-command','kind':'item/commandExecution/requestApproval','details':{'reason':'生成新的预览文件','command':'python build_scene.py'},'prompt':'fixture'},
      {'approval_id':bid,'request_id':'fixture-form','kind':'mcpServer/elicitation/request','details':{'mode':'form','message':'填写场景名称','requestedSchema':{'type':'object','properties':{'name':{'type':'string','title':'场景名称'}},'required':['name']}},'prompt':'fixture'}]
    with store.lock:
      store.state['workspace']['approvals']=approvals;store._save()
    store.workspace_agent(status='awaiting_approval')
    page.evaluate('__navCheck.refreshWorkspace()')
    expect(page.locator('#activity-dialog')).not_to_be_visible()
    expect(page.locator('#chat-launcher')).to_contain_text('待确认')
    expect(page.locator('.chat-launcher-attention')).to_be_visible()
    expect(page.locator('.chat-launcher-spinner')).to_be_hidden()
    control(page, '#chat-launcher').click()
    command=page.locator(f'[data-approval-id="{aid}"]');form=page.locator(f'[data-approval-id="{bid}"]')
    expect(command).to_be_focused(timeout=5000)
    expect(page.locator('#chat-history')).to_be_hidden()
    expect(page.locator('#chat-approvals')).to_be_visible()
    form.locator('input').fill('书房')
    page.evaluate('__navCheck.refreshWorkspace()')
    expect(form.locator('input')).to_have_value('书房')
    expect(command.locator('.approval-raw')).not_to_be_visible()
    command.locator('summary').click();expect(command.locator('.approval-raw')).to_contain_text('build_scene.py')
    command.get_by_role('button',name='允许',exact=True).click()
    expect(command.locator('.approval-error')).to_contain_text('fixture temporary rejection')
    expect(command.get_by_role('button',name='允许',exact=True)).to_be_enabled()
    expect(form.locator('input')).to_have_value('书房')
    command.get_by_role('button',name='允许',exact=True).click()
    expect(command).to_have_count(0)
    expect(form.locator('input')).to_have_value('书房')
    form.get_by_role('button',name='提交',exact=False).click()
    expect(page.locator('#chat-approvals')).to_be_hidden()
    expect(page.locator('#feedback-note')).to_have_value('保留输入草稿')
    page.reload();page.wait_for_function('window.__navCheck && __navCheck.state.workspaceReady && !__navCheck.state.sceneLoading')
    if page.locator('#chat-history-toggle').get_attribute('aria-expanded')=='false':control(page, '#chat-history-toggle').click()
    expect(page.locator('#conversation')).to_contain_text('已允许该操作')
    assert len(decisions.calls)==3,decisions.calls
    assert decisions.calls[-1][1]['content']['name']=='书房'
    print('PASS inline approvals, collapsed attention, focus, failure retry, preserved forms/draft and durable decisions',flush=True)
    # A queued feedback requiring explicit confirmation also belongs in the composer.
    fid=uuid.uuid4().hex
    with store.lock:
      store.state['workspace']['queue'].append({'feedback_id':fid,'status':'delivery_uncertain','scene_revision':store.scene()['revision']});store._save()
    page.evaluate('__navCheck.refreshWorkspace()')
    expect(page.locator('#chat-pending-queue')).to_contain_text('确认未收到，重试')
    expect(page.locator('#activity-dialog')).not_to_be_visible()
    with store.lock:store.state['workspace']['queue']=[];store._save()
    page.evaluate('__navCheck.refreshWorkspace()')
    control(page, '#chat-collapse').click();expect(page.locator('#chat-dock')).to_be_hidden()
    def choose(view):
      if not page.locator('.view-popover').get_attribute('open') == '': control(page, '.view-popover > summary').click()
      control(page, f'[data-camera-view="{view}"]').click()
    geometry=page.evaluate('__navCheck.geometry()')
    # Six precise directions and an opposite-side transition without crossing the pivot.
    for view,axis,sign in [('front',1,-1),('back',1,1),('left',0,-1),('right',0,1),('top',2,1),('bottom',2,-1)]:
      choose(view)
      if view=='back':
        radii=page.evaluate('''()=>new Promise(resolve=>{const a=[];function sample(){a.push(__navCheck.controls.getDistance());if(a.length<10)requestAnimationFrame(sample);else resolve(a)}requestAnimationFrame(sample)})''')
        assert min(radii)>0 and max(radii)-min(radii)<.001,radii
      page.wait_for_function('!__navCheck.controls.transition')
      d=page.evaluate('(()=>{const c=__navCheck;return c.camera.position.clone().sub(c.controls.target).normalize().toArray()})()')
      assert d[axis]*sign>.999,(view,d)
    choose('iso');page.wait_for_function('!__navCheck.controls.transition')
    control(page, '.view-popover > summary').click();control(page, '#frame-all').click();page.wait_for_function('!__navCheck.controls.transition')
    page.wait_for_timeout(150)
    # Pick an actual visible object with a real double click.
    xy=page.evaluate('''()=>{const c=document.querySelector('#viewport canvas'),r=c.getBoundingClientRect();for(let y=.35;y<.85;y+=.1)for(let x=.2;x<.8;x+=.1){const p={clientX:r.x+r.width*x,clientY:r.y+r.height*y};if(__navCheck.pickScene(p))return [p.clientX,p.clientY];}throw Error('No visible scene object');}''')
    page.mouse.dblclick(*xy);page.wait_for_function('!__navCheck.controls.transition')
    actual=page.evaluate('__navCheck.position().t');expected=page.evaluate('__navCheck.center()')
    assert max(abs(a-b) for a,b in zip(actual,expected))<1e-5,(actual,expected)
    assert page.evaluate('__navCheck.geometry()')==geometry
    before_pan=page.evaluate('__navCheck.position()')
    canvas=page.locator('#viewport canvas').bounding_box()
    px,py=canvas['x']+canvas['width']*.45,canvas['y']+canvas['height']*.55
    page.mouse.move(px,py);page.mouse.down(button='right');page.mouse.move(px,py+55,steps=15);page.mouse.up(button='right');page.wait_for_timeout(450)
    after_pan=page.evaluate('__navCheck.position()')
    assert abs(after_pan['t'][2]-before_pan['t'][2])>.001,'Screen-space pan should move vertically'
    old_distance=page.evaluate('__navCheck.controls.getDistance()')
    page.mouse.wheel(0,-180);page.wait_for_timeout(350)
    assert page.evaluate('__navCheck.controls.getDistance()')<old_distance
    control(page, '#chat-launcher').click();expect(page.locator('#feedback-note')).to_be_focused()
    before_typing=page.evaluate('__navCheck.position()')
    page.locator('#feedback-note').press('f')
    after_typing=page.evaluate('__navCheck.position()')
    assert max(abs(a-b) for key in ('p','t','up') for a,b in zip(after_typing[key],before_typing[key]))<.005,(before_typing,after_typing)
    control(page, '#chat-collapse').click();expect(page.locator('#chat-dock')).to_be_hidden()
    print('PASS six directions, smooth opposite view, double-click pivot, screen pan/zoom, typing guard and unchanged geometry',flush=True)
    control(page, '.view-popover > summary').click();control(page, '#free-rotation').click()
    assert page.evaluate('__navCheck.controls.freeRotation')
    box=page.locator('#viewport canvas').bounding_box()
    x,y=box['x']+box['width']*.5,box['y']+box['height']*.45
    before=page.evaluate('__navCheck.position()')
    page.mouse.move(x,y);page.mouse.down();page.mouse.move(x+110,y+130,steps=20);page.mouse.up();page.wait_for_timeout(100)
    after=page.evaluate('__navCheck.position()')
    assert sum(abs(a-b) for a,b in zip(before['up'],after['up']))>.1
    page.reload();page.wait_for_function('window.__navCheck && __navCheck.state.workspaceReady && !__navCheck.state.sceneLoading')
    assert page.evaluate('__navCheck.controls.freeRotation')
    restored=page.evaluate('__navCheck.position()')
    assert max(abs(a-b) for a,b in zip(after['up'],restored['up']))<.005,(after,restored)
    control(page, '.view-popover > summary').click();control(page, '#upright-camera').click()
    assert page.evaluate('__navCheck.position().up')==[0,0,1]
    assert not page.evaluate('__navCheck.controls.freeRotation')
    control(page, '#capture-scene-button').click()
    expect(page.locator('#camera-navigation')).to_be_hidden()
    snapshot=page.evaluate('JSON.stringify(__navCheck.state.snapshot)')
    control(page, '#browse-button').click();choose('back');page.wait_for_function('!__navCheck.controls.transition')
    control(page, '#snapshot-button').click()
    assert page.evaluate('JSON.stringify(__navCheck.state.snapshot)')==snapshot
    print('PASS free rotation, persisted tilted camera, upright reset and frozen snapshot protection',flush=True)
    control(page, '#browse-button').click()
    page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(250)
    expect(page.locator('#camera-navigation')).to_be_hidden()
    control(page, '.view-popover > summary').click()
    r=page.locator('.view-popover .popover-content').bounding_box()
    assert r['x']>=0 and r['x']+r['width']<=391
    assert page.locator('#scene-stage #camera-navigation').count()==0
    out=ROOT.parent/'inspection/navigation-approval';out.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(out/'mobile-navigation.png'))
    page.set_viewport_size({'width':1440,'height':900})
    with store.lock:store.state['workspace']['approvals']=[approvals[0]];store._save()
    store.workspace_agent(status='awaiting_approval');page.evaluate('__navCheck.refreshWorkspace()')
    control(page, '#chat-launcher').click();expect(page.locator('#chat-approvals')).to_be_visible();page.wait_for_timeout(650)
    page.screenshot(path=str(out/'inline-confirmation.png'))
    # Publish a standard Y-up GLB in the isolated fixture, retaining its geometry coordinates.
    raw=(tmp/'room.glb').read_bytes();length=struct.unpack_from('<I',raw,12)[0];doc=json.loads(raw[20:20+length])
    doc['asset']['generator']='Y-up fixture'
    children=doc['scenes'][doc.get('scene',0)]['nodes'];parent=len(doc['nodes'])
    doc['nodes'].append({'name':'Y-up export','children':children,'rotation':[-2**-.5,0,0,2**-.5]})
    doc['scenes'][doc.get('scene',0)]['nodes']=[parent]
    encoded=json.dumps(doc).encode();encoded+=b' '*((-len(encoded))%4);tail=raw[20+length:]
    target=tmp/'standard.glb';target.write_bytes(struct.pack('<4sII',b'glTF',2,20+len(encoded)+len(tail))+struct.pack('<I4s',len(encoded),b'JSON')+encoded+tail)
    # The server owns a separate store instance; use it to publish the fixture.
    store.set_scene_preview(str(target))
    page.reload();page.wait_for_function("window.__navCheck && !__navCheck.state.sceneLoading && __navCheck.state.detectedUpAxis==='y'")
    assert page.evaluate('__navCheck.position().up')==[0,1,0]
    choose('top');page.wait_for_function('!__navCheck.controls.transition')
    pose=page.evaluate('__navCheck.position()');delta=[a-b for a,b in zip(pose['p'],pose['t'])]
    assert delta[1]>0 and abs(delta[0])+abs(delta[2])<abs(delta[1])*.001,delta
    geometry=page.evaluate('__navCheck.geometry()')
    control(page, '.view-popover > summary').click();page.locator('#ground-axis').select_option('z')
    page.wait_for_function('!__navCheck.controls.transition');assert page.evaluate('__navCheck.position().up')==[0,0,1]
    page.reload();page.wait_for_function('window.__navCheck && !__navCheck.state.sceneLoading')
    assert page.evaluate('__navCheck.position().up')==[0,0,1]
    assert page.evaluate('__navCheck.state.groundAxis')=='z'
    control(page, '.view-popover > summary').click();page.locator('#ground-axis').select_option('auto')
    page.wait_for_function('!__navCheck.controls.transition');assert page.evaluate('__navCheck.position().up')==[0,1,0]
    assert page.evaluate('__navCheck.geometry()')==geometry
    print('PASS standard Y-up GLB, legacy Z-up demo, axis override persistence and unchanged model transforms',flush=True)
    assert not errors,errors
    browser.close();print('PASS responsive navigation and no browser errors',flush=True)
  finally:
   server.shutdown();server.server_close();thread.join(timeout=3)
if __name__=='__main__':main()

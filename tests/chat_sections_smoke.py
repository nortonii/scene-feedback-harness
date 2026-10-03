"""Chat sections animate and resize without changing draft evidence or the camera."""
from pathlib import Path
import sys,tempfile,threading,json
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'examples/room_demo')]
from core import SceneStore
from server import make_server
from build_scene import build
from playwright.sync_api import sync_playwright,expect
from immersive_theme_smoke import HOOK,wait_ready,settle,point
from workspace_ui_helpers import control,choose_tool

def main():
 with tempfile.TemporaryDirectory(prefix='chat-sections-',dir=ROOT.parent/'tmp') as folder:
  tmp=Path(folder);seed=SceneStore(tmp/'data');session=seed.create_session(reference_images=[str(ROOT/'examples/room_demo/reference.png')]);seed.set_scene_preview(str(build(ROOT/'examples/room_demo/scene.json',tmp/'room.glb')))
  server=make_server(port=0,data_dir=tmp/'data',project_dir=ROOT,web_dir=ROOT/'web',enable_codex=False);store=server.scene_store;server.workspace_gateway.ensure(session['session_id']);store.workspace_agent(status='idle')
  for i in range(12):store.workspace_event('assistant_message',{'text':f'检查记录 {i+1}：'+'保持截图和标记的原始位置。'*4})
  threading.Thread(target=server.serve_forever,daemon=True).start()
  try:
   with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,args=['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader','--disable-accelerated-2d-canvas'])
    page=browser.new_page(viewport={'width':1440,'height':960});errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.route('**/app.js',lambda route:route.fulfill(status=200,content_type='text/javascript',body=(ROOT/'web/app.js').read_text()+HOOK))
    page.goto(server.browser_url(session['session_id']));wait_ready(page);settle(page)
    dock=page.locator('#chat-dock');note=page.locator('#feedback-note');history=page.locator('#chat-history-toggle');feedback=page.locator('#feedback-evidence-summary')
    done=lambda:page.wait_for_function("!document.querySelector('#chat-dock').classList.contains('sections-animating')")
    snapshot=lambda:page.evaluate('({evidence:__appearanceCheck.evidence(),pose:Object.fromEntries(Object.entries(__appearanceCheck.pose()).map(([k,v])=>[k,v.map(n=>Math.round(n*1e5)/1e5)])),note:__appearanceCheck.promptText()})')
    assert abs(history.bounding_box()['y']-feedback.bounding_box()['y'])<2
    assert feedback.locator('xpath=..').get_attribute('class')=='chat-dock-header'
    assert 43<=note.bounding_box()['height']<=45
    note.fill('保留这段修改要求。')
    control(page,'#chat-collapse').click();expect(dock).to_be_hidden();control(page,'#capture-scene-button').click();choose_tool(page,'point','scene');point(page,'#scene-annotations',.62,.35);control(page,'#annotation-tools-close').click();control(page,'#chat-launcher').click();settle(page)
    before=snapshot()
    print('PASS shared header, compact native input and preserved marked draft',flush=True)
    def sample(selector):
     return page.evaluate('''async selector=>{const dock=document.querySelector('#chat-dock'),button=document.querySelector(selector),result=[];button.click();const start=performance.now();while(performance.now()-start<540){result.push(dock.getBoundingClientRect().height);await new Promise(requestAnimationFrame);}return result;}''',selector)
    for selector in ['#chat-history-toggle','#chat-history-toggle','#feedback-evidence-summary','#feedback-evidence-summary']:
     frames=sample(selector);delta=frames[-1]-frames[0]
     assert len(frames)>5 and abs(delta)>30,(selector,frames)
     assert max(abs(b-a) for a,b in zip(frames,frames[1:]))<abs(delta)*.75,(selector,frames)
     assert all((b-a)*(1 if delta>0 else -1)>-2 for a,b in zip(frames,frames[1:])),(selector,frames)
    assert snapshot()==before, [key for key in before if snapshot()[key]!=before[key]]
    print('PASS history and evidence grow/shrink through intermediate heights with stable evidence and camera',flush=True)
    for selector in ['#chat-history-toggle','#feedback-evidence-summary']:
     result=page.evaluate('''async selector=>{const dock=document.querySelector('#chat-dock'),button=document.querySelector(selector);const before=button.getAttribute('aria-expanded');button.click();await new Promise(r=>setTimeout(r,110));const start=dock.getBoundingClientRect().height;button.click();const after=dock.getBoundingClientRect().height;return {jump:Math.abs(after-start),before};}''',selector)
     assert result['jump']<3,result
     done();assert page.locator(selector).get_attribute('aria-expanded')==result['before']
    feedback.click();done();expect(page.locator('#feedback-evidence-list .evidence-card')).to_have_count(2)
    history.click();done();expect(page.locator('#chat-history')).to_be_hidden()
    def drag(selector,amount):
     handle=page.locator(selector);box=handle.bounding_box();x=box['x']+box['width']/2;y=box['y']+box['height']/2
     page.mouse.move(x,y);page.mouse.down();page.mouse.move(x,y-amount,steps=8);page.mouse.up()
    old=note.bounding_box()['height'];drag('#prompt-resize-handle',80);assert note.bounding_box()['height']>=old+75
    scroller=page.locator('#feedback-evidence-scroll');old=scroller.bounding_box()['height'];drag('#feedback-evidence-resize',70);assert scroller.bounding_box()['height']>=old+65
    note_height=note.bounding_box()['height'];feedback_height=scroller.bounding_box()['height']
    page.locator('#feedback-evidence-resize').press('ArrowDown');assert scroller.bounding_box()['height']==feedback_height-16
    page.locator('#feedback-evidence-resize').press('ArrowUp');assert scroller.bounding_box()['height']==feedback_height
    assert snapshot()==before, [key for key in before if snapshot()[key]!=before[key]]
    page.reload();page.wait_for_function('window.__appearanceCheck && !__appearanceCheck.state.sceneLoading');settle(page)
    assert note.bounding_box()['height']==note_height;assert scroller.bounding_box()['height']==feedback_height
    expect(page.locator('#chat-history')).to_be_hidden();expect(page.locator('#feedback-evidence')).to_be_visible();assert snapshot()==before, [key for key in before if snapshot()[key]!=before[key]]
    print('PASS reversible motion and independent pointer/keyboard heights survive reload without opening history',flush=True)
    history.click();done();handle=page.locator('#chat-resize-handle');handle.press('ArrowUp');done()
    expanded=dock.bounding_box()['height'];history.click();done();history.click();done();assert abs(dock.bounding_box()['height']-expanded)<2
    # Preserve a scrolled reading position across a fold, including an arrival while hidden.
    conversation=page.locator('#conversation');conversation.evaluate('el=>el.scrollTop=28');page.wait_for_timeout(50);scroll=conversation.evaluate('el=>el.scrollTop')
    history.click();done();store.workspace_event('assistant_message',{'text':'隐藏时收到的新记录'});expect(page.locator('#conversation')).to_contain_text('隐藏时收到的新记录',timeout=10000);history.click();done();assert abs(conversation.evaluate('el=>el.scrollTop')-scroll)<3
    page.locator('#prompt-resize-handle').dblclick();page.locator('#feedback-evidence-resize').dblclick();assert note.bounding_box()['height']==44;assert scroller.bounding_box()['height']==180
    print('PASS custom history size, reader position, unread arrivals and double-click reset remain independent',flush=True)
    out=ROOT.parent/'inspection/chat-sections';out.mkdir(parents=True,exist_ok=True)
    for width in [1440,390,340]:
     page.set_viewport_size({'width':width,'height':900 if width>640 else 844});settle(page)
     for theme in ['dark','light']:
      if page.locator('html').get_attribute('data-theme')!=theme:control(page,'#theme-toggle').click();settle(page)
      note.focus();done()
      a=history.bounding_box();b=feedback.bounding_box();assert abs(a['y']-b['y'])<2 and a['x']+a['width']<=b['x']+1,(a,b)
      for element in [dock,history,feedback,page.locator('#chat-collapse'),page.locator('#submit-button')]:
       r=element.bounding_box();assert r['x']>=-1 and r['x']+r['width']<=width+1 and r['y']>=-1 and r['y']+r['height']<=844+60,(width,r)
      page.screenshot(path=str(out/f'{theme}-{width}.png'))
    # Maximum sizes must leave the footer reachable even on a narrow screen.
    page.locator('#prompt-resize-handle').press('End');page.locator('#feedback-evidence-resize').press('End');done()
    submit=page.locator('#submit-button').bounding_box();assert submit['y']+submit['height']<=844,submit
    page.locator('#prompt-resize-handle').dblclick();page.locator('#feedback-evidence-resize').dblclick()
    history.click();feedback.click();done();page.screenshot(path=str(out/'compact-340.png'))
    history.click();feedback.click();done()
    page.emulate_media(reduced_motion='reduce');history.click();feedback.click();assert not dock.evaluate("el=>el.classList.contains('sections-animating')");assert snapshot()==before, [key for key in before if snapshot()[key]!=before[key]]
    assert not errors,errors;assert not store.list_all_feedback();browser.close()
    print('PASS desktop/narrow day-night layout, reduced motion and no browser errors or production feedback',flush=True)
  finally:server.shutdown();server.server_close()
if __name__=='__main__':main()

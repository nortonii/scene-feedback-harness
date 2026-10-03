"""Exercise controls during animation, not only their settled end states."""
from playwright.sync_api import expect
from immersive_theme_smoke import settle,bounds

def verify_sidebar_motion(page,out):
    drawer=page.locator('#projects-dialog');panel=page.locator('.sidebar-panel')
    page.evaluate('''() => {
      window.__documentTransitions=0;
      const start=document.startViewTransition?.bind(document);
      if(start) document.startViewTransition=(...args)=>{++window.__documentTransitions;return start(...args);};
    }''')
    if not drawer.evaluate('el=>el.open'):page.locator('#projects-dialog-button').click()
    page.wait_for_function("document.querySelector('#projects-dialog').dataset.phase==='open'")
    pose=page.evaluate('__appearanceCheck.pose()');evidence=page.evaluate('__appearanceCheck.evidence()')
    for width in [1440,390]:
      page.set_viewport_size({'width':width,'height':960 if width==1440 else 844})
      for theme in ['light','dark']:
        if page.locator('html').get_attribute('data-theme')!=theme:page.locator('#theme-toggle').click()
        settle(page);rect=bounds(page,'.sidebar-panel')
        for selector,layout in [('#immersive-toggle','immersive'),('#comparison-layout-button','compare'),('#immersive-toggle','immersive')]:
          button=page.locator(selector);button.click()
          expect(page.locator('html')).to_have_attribute('data-layout',layout)
          assert page.evaluate('__documentTransitions')==0,'A document transition would capture and cover the modal'
          # Hold the scene midway; the drawer must stay live above it.
          page.locator('#scene-stage').evaluate('el=>el.getAnimations().forEach(a=>{a.pause();a.currentTime=180})')
          assert bounds(page,'.sidebar-panel')==rect
          for target in ['#close-projects','#comparison-layout-button','#immersive-toggle','#theme-toggle']:
            assert page.locator(target).evaluate('''el=>{
              const r=el.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
              return hit===el || el.contains(hit);
            }'''),target
          if selector=='#comparison-layout-button':page.screenshot(path=str(out/f'live-layout-{theme}-{width}.png'))
          # The next iteration clicks the opposite layout while this one is in progress.
        page.locator('#close-projects').click();expect(drawer).to_be_hidden()
        page.locator('#scene-stage').evaluate('el=>el.getAnimations().forEach(a=>a.finish())')
        page.locator('#projects-dialog-button').click();page.wait_for_function("document.querySelector('#projects-dialog').dataset.phase==='open'")
    assert page.evaluate('__appearanceCheck.pose()')==pose
    assert page.evaluate('__appearanceCheck.evidence()')==evidence
    print('PASS live layout switching: drawer never joins document snapshots; buttons remain clickable mid-transition in both themes and widths',flush=True)
    # Freeze an in-flight close, then reverse it in the same JS task to compare positions.
    result=page.evaluate('''() => {
      const panel=document.querySelector('.sidebar-panel'),shade=document.querySelector('.sidebar-shade'),button=document.querySelector('#close-projects');
      button.click();
      const motion=panel.getAnimations()[0],veil=shade.getAnimations()[0];
      for(const a of [motion,veil]) {a.pause();a.currentTime=240;}
      const x=panel.getBoundingClientRect().x,opacity=getComputedStyle(shade).opacity;
      button.click();
      return {same:motion===panel.getAnimations()[0],delta:panel.getBoundingClientRect().x-x,opacityDelta:Math.abs(getComputedStyle(shade).opacity-opacity)};
    }''')
    assert result['same'] and abs(result['delta'])<.01 and result['opacityDelta']<.001,result
    page.wait_for_function("document.querySelector('#projects-dialog').dataset.phase==='open'")
    # Repeated Escape must let the current close finish, without restarting it.
    page.keyboard.press('Escape');page.keyboard.press('Escape');expect(drawer).to_be_hidden()
    page.locator('#projects-dialog-button').click();page.wait_for_function("document.querySelector('#projects-dialog').dataset.phase==='open'")
    page.locator('#close-projects').click();page.emulate_media(reduced_motion='reduce');expect(drawer).to_be_hidden()
    page.locator('#projects-dialog-button').click();expect(drawer).to_have_attribute('data-phase','open')
    page.locator('#close-projects').click();expect(drawer).to_be_hidden();page.emulate_media(reduced_motion='no-preference')
    print('PASS continuous drawer reversal, synchronized scrim, repeated Escape and reduced-motion changes during close',flush=True)

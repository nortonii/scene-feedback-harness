"""Browser checks for the conversation's busy indicator and interruptible motion.

Called by focus_workspace_smoke.py with its isolated, editable room fixture.
Only isolated fixture messages/status change; no feedback or model task is submitted.
"""

from __future__ import annotations

import re

from playwright.sync_api import expect


# Read visible bounds, rather than the stable layout rectangle: clipping and
# translation expand the glass while the text keeps its natural size.
MOTION_HELPERS = r"""
  const dock = document.getElementById('chat-dock');
  const launcher = document.getElementById('chat-launcher');
  const animations = () => [...dock.getAnimations({subtree:true}),
    ...launcher.getAnimations()].filter(animation => animation.effect?.getTiming().iterations !== Infinity);
  const motion = () => dock.getAnimations().find(animation =>
    animation.effect.getKeyframes().some(frame => frame.clipPath));
  const read = () => {
    const style = getComputedStyle(dock), rect = dock.getBoundingClientRect();
    const values = style.clipPath === 'none' ? ['0']
      : style.clipPath.match(/^inset\((.*?)\s*(?:round\s+.*)?\)$/)?.[1].trim().split(/\s+/);
    if (!values) throw new Error('Conversation does not expose its expanding bounds: ' + style.clipPath);
    const sides = values.length === 1 ? [values[0], values[0], values[0], values[0]]
      : values.length === 2 ? [values[0], values[1], values[0], values[1]]
      : values.length === 3 ? [values[0], values[1], values[2], values[1]] : values;
    const [top, right, bottom, left] = sides.map((value, index) => parseFloat(value) *
      (value.endsWith('%') ? (index % 2 ? rect.width : rect.height) / 100 : 1));
    const width = Math.max(0, rect.width - Math.max(0, left) - Math.max(0, right));
    const height = Math.max(0, rect.height - Math.max(0, top) - Math.max(0, bottom));
    const note = document.getElementById('feedback-note').getBoundingClientRect();
    return {x:rect.x + Math.max(0, left), y:rect.y + Math.max(0, top), width, height,
      area:width * height, fullArea:rect.width * rect.height, opacity:Number(style.opacity),
      noteWidth:note.width, noteHeight:note.height, clip:style.clipPath};
  };
  const seek = fraction => {
    for (const animation of animations()) {
      animation.pause();
      animation.currentTime = Number(animation.effect.getTiming().duration) * fraction;
    }
    return read();
  };
  const resume = () => animations().forEach(animation => animation.play());
"""


def sample_morph(page, trigger, screenshots=None):
    result = page.evaluate("""trigger => {
    """ + MOTION_HELPERS + """
      document.getElementById(trigger).click();
      if (!motion()) throw new Error('Conversation did not animate its visible size');
      const duration = motion().effect.getTiming().duration;
      const samples = [0, .15, .45, .8, 1].map(seek);
      return {duration, samples};
    }""", trigger)
    if screenshots:
        action = 'opening' if trigger == 'chat-launcher' else 'closing'
        for fraction in (.2, .6, 1):
            page.evaluate("""fraction => {
            """ + MOTION_HELPERS + """
              seek(fraction);
            }""", fraction)
            page.screenshot(path=str(screenshots / f'chat-{action}-{round(fraction * 100)}.png'))
    page.evaluate('() => {' + MOTION_HELPERS + 'resume();}')
    return result


def assert_morph(samples, *, expanding):
    areas = [sample['area'] / sample['fullArea'] for sample in samples]
    if expanding:
        assert areas[0] < .2 and areas[-1] > .99, areas
        assert all(left <= right + .001 for left, right in zip(areas, areas[1:])), areas
    else:
        assert areas[0] > .99 and areas[-1] < .2, areas
        assert all(left >= right - .001 for left, right in zip(areas, areas[1:])), areas
    assert any(.2 < value < .9 for value in areas[1:-1]), areas
    assert max(sample['noteWidth'] for sample in samples) - min(sample['noteWidth'] for sample in samples) < 1
    assert max(sample['noteHeight'] for sample in samples) - min(sample['noteHeight'] for sample in samples) < 1


def verify_chat_activity(page, store, screenshots):
    from focus_workspace_smoke import draft, geometry

    dock = page.locator('#chat-dock')
    launcher = page.locator('#chat-launcher')
    spinner = launcher.locator('.chat-launcher-spinner')
    note = page.locator('#feedback-note')
    conversation = page.locator('#conversation')
    expect(dock).to_be_visible()
    expect(note).to_be_enabled()
    original_note = note.input_value()
    original_geometry = geometry(page)
    original_reduced_motion = page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")

    def settle():
        page.wait_for_function("""() => ['chat-dock', 'chat-launcher'].every(id =>
          document.getElementById(id).getAnimations({subtree:true}).every(animation =>
            animation.effect?.getTiming().iterations === Infinity || animation.playState !== 'running'))
        """)

    def set_status(status, label):
        store.workspace_agent(status=status)
        expect(page.locator('#chat-status')).to_have_text(label, timeout=10000)

    try:
        page.emulate_media(reduced_motion='no-preference')
        note.fill('动画检查：保留未发送的修改意见。')
        for index in range(8):
            store.workspace_event('assistant_message', {
                'text': f'动画滚动检查 {index + 1}：' + '继续核对参考图中的柜子位置，保留原图和标记。' * 5})
        expect(conversation).to_contain_text('动画滚动检查 8', timeout=10000)
        assert conversation.evaluate('el => el.scrollHeight > el.clientHeight + 100')
        conversation.evaluate("el => { el.scrollTop = 80; el.dispatchEvent(new Event('scroll')); }")
        original_scroll = conversation.evaluate('el => el.scrollTop')
        settle()
        original_box = dock.bounding_box()
        original_draft = draft(page)
        set_status('running', 'Codex 正在处理')
        expect(launcher).to_have_attribute('aria-busy', 'true')
        closing = sample_morph(page, 'chat-collapse', screenshots)
        assert 350 <= closing['duration'] <= 700, closing['duration']
        assert_morph(closing['samples'], expanding=False)
        expect(dock).to_have_attribute('aria-hidden', 'true')
        assert dock.evaluate('el => el.inert'), 'Closing conversation still accepts input'
        expect(spinner).to_be_visible()
        expect(launcher).to_have_attribute('aria-label', re.compile(r'^打开会话，Codex 正在处理'))
        expect(dock).to_be_hidden()
        settle()
        assert spinner.evaluate('el => getComputedStyle(el).animationName') == 'chat-busy-spin'
        assert geometry(page) == original_geometry
        if screenshots:
            page.screenshot(path=str(screenshots / 'chat-collapsed-running.png'))

        for status, label in [('idle', '等待反馈'), ('error', 'Codex 执行出错'),
                              ('awaiting_approval', '等待审批')]:
            set_status(status, label)
            expect(launcher).to_have_attribute('aria-busy', 'false')
            expect(spinner).to_be_hidden()
        set_status('idle', '等待反馈')
        opening = sample_morph(page, 'chat-launcher', screenshots)
        assert 400 <= opening['duration'] <= 750, opening['duration']
        assert_morph(opening['samples'], expanding=True)
        expect(note).to_be_focused()
        settle()
        expect(launcher).to_be_hidden()
        assert dock.bounding_box() == original_box
        assert abs(conversation.evaluate('el => el.scrollTop') - original_scroll) <= 1

        # Seek within each live transition so reversals interrupt its middle,
        # without timing-sensitive sleeps on slower renderers.
        interrupted = page.evaluate("""() => {
        """ + MOTION_HELPERS + """
          document.getElementById('chat-collapse').click();
          const reversals = [];
          for (const fraction of [.35, .45, .25, .3, .4]) {
            const before = seek(fraction);
            document.getElementById(dock.inert ? 'chat-launcher' : 'chat-collapse').click();
            const after = read();
            reversals.push({before, after, animating:!!motion()});
          }
          resume();
          return {reversals, reopened:!dock.inert && dock.getAttribute('aria-hidden') === 'false'};
        }""")
        assert interrupted['reopened'], interrupted
        for reversal in interrupted['reversals']:
            assert reversal['animating'], reversal
            for dimension in ('x', 'y', 'width', 'height'):
                assert abs(reversal['before'][dimension] - reversal['after'][dimension]) < 2, reversal
            assert abs(reversal['before']['opacity'] - reversal['after']['opacity']) < .02, reversal
        settle()
        expect(dock).to_be_visible()
        expect(launcher).to_be_hidden()
        expect(note).to_be_focused()
        assert not dock.evaluate('el => el.inert')
        assert dock.bounding_box() == original_box
        assert abs(conversation.evaluate('el => el.scrollTop') - original_scroll) <= 1

        # Changing the motion preference during a transition settles at once.
        page.evaluate("""() => {
        """ + MOTION_HELPERS + """
          document.getElementById('chat-collapse').click();
          seek(.3);
        }""")
        page.emulate_media(reduced_motion='reduce')
        expect(dock).to_be_hidden()
        assert dock.evaluate('el => el.getAnimations({subtree:true}).length') == 0
        launcher.click()
        expect(dock).to_be_visible()

        set_status('running', 'Codex 正在处理')
        page.locator('#chat-collapse').click()
        expect(dock).to_be_hidden()
        expect(spinner).to_be_visible()
        assert spinner.evaluate('el => getComputedStyle(el).animationName') == 'none'
        assert launcher.evaluate('el => el.getAnimations().length') == 0
        launcher.click()
        expect(dock).to_be_visible()
        expect(note).to_be_focused()
        assert dock.evaluate('el => el.getAnimations({subtree:true}).length') == 0
        expect(launcher).to_be_hidden()
        assert geometry(page) == original_geometry
        assert draft(page) == original_draft, 'Chat activity or motion changed the pending visual draft'
        expect(note).to_have_value('动画检查：保留未发送的修改意见。')
        assert abs(conversation.evaluate('el => el.scrollTop') - original_scroll) <= 1
    finally:
        store.workspace_agent(status='idle')
        page.emulate_media(reduced_motion='reduce' if original_reduced_motion else 'no-preference')
        if dock.get_attribute('aria-hidden') == 'true':
            launcher.evaluate('el => el.click()')
        settle()
        expect(launcher).to_have_attribute('aria-busy', 'false', timeout=10000)
        expect(dock).to_be_visible()
        note.fill(original_note)
        conversation.evaluate("el => { el.scrollTop = el.scrollHeight; el.dispatchEvent(new Event('scroll')); }")

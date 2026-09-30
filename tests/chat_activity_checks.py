"""Browser checks for the conversation's busy indicator and interruptible motion.

Called by focus_workspace_smoke.py with its isolated, editable room fixture.
Only the fixture's agent status changes; no feedback or model task is submitted.
"""

from __future__ import annotations

import re

from playwright.sync_api import expect


def verify_chat_activity(page, store, screenshots):
    from focus_workspace_smoke import draft, geometry

    dock = page.locator('#chat-dock')
    launcher = page.locator('#chat-launcher')
    spinner = launcher.locator('.chat-launcher-spinner')
    note = page.locator('#feedback-note')
    expect(dock).to_be_visible()
    expect(note).to_be_enabled()
    original_draft = draft(page)
    original_note = note.input_value()
    original_geometry = geometry(page)
    original_reduced_motion = page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")

    def settle():
        page.wait_for_function("""() => ['chat-dock', 'chat-launcher'].every(id =>
          document.getElementById(id).getAnimations().every(animation => animation.playState !== 'running'))
        """)

    def set_status(status, label):
        store.workspace_agent(status=status)
        expect(page.locator('#chat-status')).to_have_text(label, timeout=10000)

    try:
        page.emulate_media(reduced_motion='no-preference')
        settle()
        original_box = dock.bounding_box()
        set_status('running', 'Codex 正在处理')
        expect(launcher).to_have_attribute('aria-busy', 'true')
        page.locator('#chat-collapse').click()
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
        launcher.click()
        expect(note).to_be_focused()
        settle()
        expect(launcher).to_be_hidden()
        assert dock.bounding_box() == original_box

        # Bypass Playwright's stability wait so the second action truly lands
        # during the first transition, as with quick clicks or a dragged mark.
        interrupted = page.evaluate("""async () => {
          const dock = document.getElementById('chat-dock');
          document.getElementById('chat-collapse').click();
          await new Promise(resolve => setTimeout(resolve, 40));
          const closing = dock.getAnimations().some(animation => animation.playState === 'running');
          document.getElementById('chat-launcher').click();
          return {closing, reopened:!dock.inert && dock.getAttribute('aria-hidden') === 'false'};
        }""")
        assert interrupted == {'closing': True, 'reopened': True}, interrupted
        settle()
        expect(dock).to_be_visible()
        expect(launcher).to_be_hidden()
        expect(note).to_be_focused()
        assert not dock.evaluate('el => el.inert')
        assert dock.bounding_box() == original_box

        set_status('running', 'Codex 正在处理')
        page.emulate_media(reduced_motion='reduce')
        page.locator('#chat-collapse').click()
        expect(dock).to_be_hidden()
        expect(spinner).to_be_visible()
        assert spinner.evaluate('el => getComputedStyle(el).animationName') == 'none'
        assert launcher.evaluate('el => el.getAnimations().length') == 0
        launcher.click()
        expect(dock).to_be_visible()
        expect(note).to_be_focused()
        assert dock.evaluate('el => el.getAnimations().length') == 0
        expect(launcher).to_be_hidden()
        assert geometry(page) == original_geometry
        assert draft(page) == original_draft, 'Chat activity or motion changed the pending visual draft'
        expect(note).to_have_value(original_note)
    finally:
        store.workspace_agent(status='idle')
        page.emulate_media(reduced_motion='reduce' if original_reduced_motion else 'no-preference')
        if dock.get_attribute('aria-hidden') == 'true':
            launcher.evaluate('el => el.click()')
        settle()
        expect(launcher).to_have_attribute('aria-busy', 'false', timeout=10000)
        expect(dock).to_be_visible()

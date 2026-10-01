"""Exercise scene creation/switching in CLI mode using an isolated stdio fixture."""
from pathlib import Path
import re
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend' / 'tests'))
from test_cli_projects import CLIProjectTests
from playwright.sync_api import sync_playwright, expect

fixture = CLIProjectTests()
fixture.setUp()
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=['--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--disable-accelerated-2d-canvas'])
        page = browser.new_page(viewport={'width':1440, 'height':900})
        errors=[]
        page.on('pageerror', lambda exc: errors.append(str(exc)))
        page.goto(f'http://127.0.0.1:{fixture.server.server_port}/')
        expect(page.locator('#submit-button')).to_be_enabled(timeout=20000)
        if page.locator('#chat-launcher').is_visible():
            page.locator('#chat-launcher').click()
        page.locator('#feedback-note').fill('原场景草稿保留')
        page.locator('#projects-dialog-button').click()
        page.locator('#create-project-panel summary').click()
        expect(page.locator('#project-model')).to_have_value('fixture-vision')
        expect(page.locator('#create-project')).to_be_disabled()
        page.locator('#project-name').fill('CLI 浏览器新场景')
        page.locator('#project-effort').select_option('high')
        expect(page.locator('#create-project')).to_be_enabled()
        page.locator('#create-project').click()
        page.wait_for_url(re.compile(r'/p/[0-9a-f]{32}/'))
        expect(page.locator('#scene-name')).to_have_text('CLI 浏览器新场景')
        expect(page.locator('#submit-button')).to_be_enabled()
        if page.locator('#chat-launcher').is_visible():
            page.locator('#chat-launcher').click()
        expect(page.locator('#feedback-note')).to_have_value('')
        child_url = page.url
        page.locator('#feedback-note').fill('新场景独立草稿')
        page.reload()
        expect(page.locator('#submit-button')).to_be_enabled()
        expect(page.locator('#feedback-note')).to_have_value('新场景独立草稿')
        page.locator('#projects-dialog-button').click()
        root_id=fixture.root_context.project_id
        page.locator('#project-list .project-item').filter(has_text=fixture.root_context.name).click()
        page.wait_for_url(re.compile('/p/' + root_id + '/'))
        expect(page.locator('#feedback-note')).to_have_value('原场景草稿保留')
        page.goto(child_url)
        expect(page.locator('#feedback-note')).to_have_value('新场景独立草稿')
        assert len(fixture.wire('thread/start')) == 2
        assert not fixture.wire('turn/start'), 'Creation must not send an unsolicited modeling turn'
        assert not errors, errors
        browser.close()
        print('PASS: CLI models, create, navigate, reload, independent drafts, return to old scene; no model turns')
finally:
    fixture.tearDown()

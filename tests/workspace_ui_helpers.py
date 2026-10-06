"""Open contextual workspace controls through their actual visible UI entries."""
from playwright.sync_api import expect


def open_annotation_tools(page, context=None):
    panel = page.locator('#annotation-tool-panel')
    if not panel.count():
        return
    if context is None:
        context = 'scene' if page.locator('#scene-live-card').get_attribute('aria-pressed') == 'false' and page.locator('#scene-snapshot-media').is_visible() else 'reference'
    if panel.is_visible() and (context is None or panel.get_attribute('data-context') == context):
        return
    if context == 'reference' and page.locator('html').get_attribute('data-layout') == 'immersive' and not page.locator('#reference-media').is_visible():
        page.locator('#immersive-reference-toggle').click()
    stage_locator = page.locator('#scene-stage' if context == 'scene' else '#reference-stage')
    stage_locator.wait_for(state='visible')
    stage = stage_locator.bounding_box()
    top = max(10, stage['y'] + 10)
    if context == 'scene' and page.locator('html').get_attribute('data-layout') == 'immersive':
        header_bottom = page.locator('.scene-pane .pane-head').evaluate('el => el.getBoundingClientRect().bottom')
        topbar_bottom = page.locator('.topbar').evaluate('el => el.getBoundingClientRect().bottom')
        top = max(top, header_bottom + 10, topbar_bottom + 10)
    page.mouse.move(0, 0)  # Leave the previous pane and clear a dismissed hover.
    page.mouse.move(stage['x'] + stage['width'] / 2, top + 20)
    expect(panel).to_be_visible()
    panel.hover()
    expect(panel).to_be_visible()


def control(page, selector):
    """Prepare a locator by opening its dock and enclosing menus, never force it."""
    locator = page.locator(selector)
    if locator.count() != 1:
        return locator
    sidebar = page.locator('#projects-dialog')
    in_sidebar = locator.evaluate("el => !!el.closest('#projects-dialog')")
    if in_sidebar:
        if not sidebar.evaluate('el => el.open'):
            page.locator('#projects-dialog-button').click()
        page.wait_for_function("document.querySelector('#projects-dialog').dataset.phase === 'open'")
        view = locator.evaluate("el => el.closest('[data-sidebar-view]')?.dataset.sidebarView")
        if view and sidebar.get_attribute('data-page') != view:
            if sidebar.get_attribute('data-page') != 'home':
                sidebar.locator('[data-sidebar-home]:visible').click()
            if view != 'home':
                sidebar.locator(f'[data-sidebar-page="{view}"]').click()
    elif selector != '#projects-dialog-button' and sidebar.evaluate('el => el.open') and not locator.evaluate("el => !!el.closest('dialog[open]')"):
        if sidebar.get_attribute('data-phase') != 'closing':
            page.locator('#close-projects').click()
        expect(sidebar).not_to_be_visible()
    if locator.evaluate("el => !!el.closest('#prompt-attach-menu')"):
        if not page.locator('#chat-dock').is_visible():
            page.locator('#chat-launcher').click()
        if page.locator('#prompt-attach-button').get_attribute('aria-expanded') != 'true':
            page.locator('#prompt-attach-button').click()
    if locator.evaluate("el => !!el.closest('#annotation-tool-panel')"):
        if not page.locator('#annotation-tool-panel').is_visible():
            open_annotation_tools(page)
    # A summary is itself the user's menu toggle; leave it closed for the caller.
    is_summary = locator.evaluate("el => el.matches('summary')")
    for menu in locator.locator('xpath=ancestor::details').all():
        if is_summary and menu.locator(':scope > summary').evaluate('(el, target) => el === document.querySelector(target)', selector):
            continue
        if not menu.evaluate('el => el.open'):
            menu.locator(':scope > summary').click()
    if selector in ('#theme-toggle', '#immersive-toggle', '#comparison-layout-button'):
        return AppearanceControl(page, locator)
    return locator


class AppearanceControl:
    """Legacy flows return to canvas after changing appearance via the drawer."""
    def __init__(self, page, locator):
        self.page, self.locator = page, locator
    def __getattr__(self, name):
        return getattr(self.locator, name)
    def click(self, **kwargs):
        self.locator.click(**kwargs)
        self.page.locator('#close-projects').click()
        expect(self.page.locator('#projects-dialog')).not_to_be_visible()


def choose_tool(page, name, context=None):
    if context is not None:
        open_annotation_tools(page, context)
    control(page, f'button[data-tool="{name}"]').click()

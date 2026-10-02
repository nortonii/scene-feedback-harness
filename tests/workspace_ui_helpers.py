"""Open contextual workspace controls through their actual visible UI entries."""
from playwright.sync_api import expect


def open_annotation_tools(page, context=None):
    panel = page.locator('#annotation-tool-panel')
    if not panel.count():
        return
    if context is None:
        context = 'scene' if page.locator('#snapshot-button').get_attribute('aria-pressed') == 'true' else 'reference'
    if panel.is_visible() and (context is None or panel.get_attribute('data-context') == context):
        return
    if context == 'scene':
        page.locator('#scene-annotation-toggle').click()
    else:
        if page.locator('html').get_attribute('data-layout') == 'immersive' and not page.locator('#annotate-reference-button').is_visible():
            page.locator('#immersive-reference-toggle').click()
        page.locator('#annotate-reference-button').click()
    expect(panel).to_be_visible()


def control(page, selector):
    """Prepare a locator by opening its dock and enclosing menus, never force it."""
    locator = page.locator(selector)
    if locator.count() != 1:
        return locator
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
    return locator


def choose_tool(page, name, context=None):
    if context is not None:
        open_annotation_tools(page, context)
    control(page, f'button[data-tool="{name}"]').click()

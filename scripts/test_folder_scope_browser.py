#!/usr/bin/env python3
"""Check folder-scoped scene lists against an isolated real HTTP workbench.

All scenes, imports and stored state live in a temporary directory. This check
never contacts a Codex model, submits feedback, or changes production projects.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import sys
import tempfile
import threading
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "scripts"), str(ROOT / "tests")]

from gateway import WorkspaceGateway
from server import make_server
from test_folder_import_browser import fixtures, http, model_catalog, png, source_files
from test_object_double_click_browser import model
from workspace_ui_helpers import control


def ready(page) -> None:
    page.wait_for_function("window.__folderScope?.state.workspaceReady && "
                           "!__folderScope.state.sceneLoading && !__folderScope.state.seeking && "
                           "!__folderScope.state.loadingProjects")


def sidebar(page) -> None:
    if not page.locator("#projects-dialog").evaluate("el=>el.open"):
        page.locator("#projects-dialog-button").click()
    page.wait_for_function("document.getElementById('projects-dialog').dataset.phase==='open'")


def import_folder(page, path: Path | str) -> tuple[int, dict]:
    sidebar(page)
    if page.locator("#projects-dialog").get_attribute("data-page") == "import":
        page.locator('#sidebar-folder-import [data-sidebar-home]').click()
    page.locator("#sidebar-open-folder").click()
    entry = page.locator("#folder-import-path")
    entry.fill(str(path))
    with page.expect_response(lambda response: response.request.method == "POST" and
                              response.url.endswith("/api/projects/import-folder")) as response:
        entry.press("Enter")
    result = response.value.json()
    page.wait_for_function("!document.getElementById('folder-import-submit').disabled && "
                           "!__folderScope.state.loadingProjects")
    page.locator('#sidebar-folder-import [data-sidebar-home]').click()
    return response.value.status, result


def row(page, project_id: str):
    return page.locator(f'.project-row[data-project-id="{project_id}"]')


def row_ids(page) -> set[str]:
    return set(page.locator("#project-list .project-row").evaluate_all("rows=>rows.map(row=>row.dataset.projectId)"))


def saved_state(context) -> dict:
    return copy.deepcopy({key: context.store.state[key] for key in ("scene", "sessions", "workspace", "feedback")})


def same_names(directory: Path) -> None:
    for marker in directory.rglob("workbench*ready.json"):
        data = json.loads(marker.read_text())
        if data.get("ready") is not False:
            data["name"] = "同名场景"
            marker.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    executable = Path("/tmp/dynamic-browser-cache/chromium-1243/chrome-linux64/chrome")
    parser.add_argument("--browser-executable", default=str(executable) if executable.is_file() else None)
    args = parser.parse_args()
    from playwright.sync_api import expect, sync_playwright

    with tempfile.TemporaryDirectory(prefix="folder-scope-browser-") as temporary:
        root = Path(temporary)
        project = root / "original"
        project.mkdir()
        model(project / "original.glb")
        png(project / "original.png", "gray")
        folder = root / "scenes"
        dynamic, static, _ = fixtures(folder)
        same_names(folder)
        # Its path shares the selected prefix, and its labels share both new
        # scene names. Neither fact makes these historical scenes folder members.
        outside = root / "scenes-old"
        model_catalog(outside)
        catalog = json.loads((outside / "manifest.json").read_text())
        for entry in catalog["scenes"]:
            entry["name"] = "同名场景"
        (outside / "manifest.json").write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
        empty = root / "empty"
        empty.mkdir()
        blocked = root / "blocked"
        blocked.mkdir()
        (blocked / "workbench-ready.json").write_text(json.dumps({
            "status": "ready", "name": "缺少模型", "scene_glb": "missing.glb",
        }), encoding="utf-8")
        source_before = {str(directory): source_files(directory) for directory in (project, folder, outside, empty, blocked)}
        errors, writes, import_requests = [], [], []
        with patch.object(WorkspaceGateway, "create_target", side_effect=AssertionError("folder scope created a task")) as targets, \
             patch.object(WorkspaceGateway, "list_models", side_effect=AssertionError("folder scope requested models")) as models:
            server = make_server(port=0, data_dir=root / "data", project_dir=project, web_dir=ROOT / "web",
                                 external_review=True, feedback_transport="mcp_events")
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            store, gateway, registry = server.scene_store, server.workspace_gateway, server.project_registry
            session_id = gateway.ensure()["session_id"]
            store.import_model(str(project / "original.glb"), object_id="original_model", name="原始模型")
            gateway.add_reference_paths([str(project / "original.png")])
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                status, workspace = http(base, "/api/workspace/state")
                assert status == 200
                capability = workspace["browser_capability"]
                status, prior = http(base, "/api/projects/import-folder", payload={
                    "path": str(outside), "request_id": uuid.uuid4().hex}, capability=capability)
                assert status == 200 and len(prior["projects"]) == 2
                outside_ids = {item["project_id"] for item in prior["projects"]}
                historical_before = {context.project_id: saved_state(context) for context in registry.contexts()}
                with patch.object(registry, "_factory", wraps=registry._factory) as factory, sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=True,
                        **({"executable_path": args.browser_executable} if args.browser_executable else {}),
                        args=["--no-sandbox", "--no-proxy-server", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
                    context = browser.new_context(viewport={"width": 1440, "height": 1000})
                    hook = "\nwindow.__folderScope={state,projectFolderScope,cameraData,workspacePrefix,loadProjects};"
                    context.route("**/app.js", lambda route: route.fulfill(status=200, content_type="application/javascript",
                                  body=(ROOT / "web" / "app.js").read_text() + hook))

                    hold_catalog, catalog_responses = {"next": False}, []

                    def duplicate_catalog(route):
                        response = route.fetch()
                        data = response.json()
                        if data.get("projects"):
                            data["projects"].extend([copy.deepcopy(data["projects"][0])] * 2)
                        catalog_responses.append(data)
                        if hold_catalog["next"]:
                            hold_catalog.update(next=False, route=route, response=response, data=data)
                            return
                        route.fulfill(response=response, json=data)

                    context.route("**/api/projects", duplicate_catalog)
                    page = context.new_page()
                    page.on("pageerror", lambda error: errors.append(str(error)))

                    def request(request):
                        path = urlsplit(request.url).path
                        if request.method not in {"GET", "HEAD", "OPTIONS"}:
                            writes.append((request.method, path))
                        if request.method == "POST" and path.endswith("/api/projects/import-folder"):
                            import_requests.append(request.post_data_json)

                    page.on("request", request)
                    page.goto(server.browser_url(session_id))
                    ready(page)
                    sidebar(page)
                    expect(page.locator("#project-list .project-item")).to_have_count(3)
                    expect(page.locator("#project-folder-scope")).to_be_hidden()
                    assert row_ids(page) == outside_ids | {registry.root.project_id}
                    assert page.evaluate("__folderScope.state.projects.length") == 5
                    page.locator("#close-projects").click()
                    page.wait_for_function("!document.getElementById('projects-dialog').open")
                    note = page.locator("#feedback-note")
                    if not note.is_visible():
                        control(page, "#chat-launcher").click()
                    draft = "保留正在查看的模型和未发送草稿"
                    note.fill(draft)
                    camera_before = page.evaluate("JSON.stringify(__folderScope.cameraData())")
                    original_url = page.url

                    # Hold a genuinely old catalog response while importing.
                    # Import completion must fetch fresh rows without depending
                    # on this old request; its late result must not replace them.
                    sidebar(page)
                    hold_catalog["next"] = True
                    page.evaluate("()=>{window.__oldList=__folderScope.loadProjects();return true}")
                    for _ in range(100):
                        if "route" in hold_catalog:
                            break
                        page.wait_for_timeout(20)
                    assert "route" in hold_catalog and len(hold_catalog["data"]["projects"]) == 5

                    # The service resolves this spelling before the successful
                    # import chooses the folder. Two equal labels stay distinct.
                    status, result = import_folder(page, str(folder) + "/../scenes/")
                    assert status == 200 and len(result["projects"]) == 2 and len(result["errors"]) == 1, result
                    imported_ids = {item["project_id"] for item in result["projects"]}
                    expect(page.locator("#project-list .project-item")).to_have_count(2)
                    assert row_ids(page) == imported_ids and not row_ids(page) & outside_ids
                    expect(page.locator("#project-list .project-name strong")).to_have_text(["同名场景", "同名场景"])
                    expect(page.locator("#project-folder-name")).to_have_attribute("title", str(folder))
                    assert page.evaluate("__folderScope.projectFolderScope.path") == str(folder)
                    assert page.evaluate("__folderScope.state.projects.length") == 7
                    assert len(catalog_responses[-1]["projects"]) == 7
                    hold_catalog["route"].fulfill(response=hold_catalog["response"], json=hold_catalog["data"])
                    page.evaluate("()=>window.__oldList")
                    assert page.evaluate("__folderScope.state.projects.length") == 7
                    expect(page.locator("#project-list .project-item")).to_have_count(2)
                    assert page.url == original_url and note.input_value() == draft
                    assert page.evaluate("JSON.stringify(__folderScope.cameraData())") == camera_before
                    assert factory.call_count == 2 and len(registry.contexts()) == 5
                    for project_id, before in historical_before.items():
                        assert saved_state(registry.get(project_id)) == before
                    print("PASS partial import shows two same-name scenes despite prefix-collision history, duplicate IDs and a delayed stale catalog response; active scene/camera/draft and all registry state are preserved", flush=True)

                    # A partial result retains its request for retries. No source
                    # context is created twice, including a fresh API request.
                    status, retry = import_folder(page, str(folder) + "/../scenes/")
                    assert status == 200 and not retry["imported"]
                    assert {item["project_id"] for item in retry["projects"]} == imported_ids
                    assert import_requests[-1]["request_id"] == import_requests[-2]["request_id"]
                    status, retry = http(base, "/api/projects/import-folder", payload={
                        "path": str(folder), "request_id": uuid.uuid4().hex}, capability=capability)
                    assert status == 200 and not retry["imported"]
                    assert {item["project_id"] for item in retry["projects"]} == imported_ids and factory.call_count == 2
                    for attempted in (empty, blocked, root / "does-not-exist"):
                        status, failed = import_folder(page, attempted)
                        assert status != 200 or not failed["projects"], failed
                        assert page.evaluate("__folderScope.projectFolderScope.path") == str(folder)
                        expect(page.locator("#project-list .project-item")).to_have_count(2)
                        assert row_ids(page) == imported_ids
                    assert factory.call_count == 2 and len(registry.contexts()) == 5
                    print("PASS same-request and fresh-request retries reuse both IDs; empty, blocked and missing-folder failures keep the previous folder view without deleting any scenes", flush=True)

                    dynamic_project = next(item for item in result["projects"] if item["project_dir"] == str(dynamic))
                    row(page, dynamic_project["project_id"]).locator(".project-item").click()
                    page.wait_for_url(re.compile(re.escape(base + "/p/" + dynamic_project["project_id"]) + r"/.*"))
                    ready(page)
                    sidebar(page)
                    expect(page.locator("#project-list .project-item")).to_have_count(2)
                    assert page.evaluate("__folderScope.projectFolderScope.path") == str(folder)
                    page.reload()
                    ready(page)
                    sidebar(page)
                    expect(page.locator("#project-list .project-item")).to_have_count(2)
                    page.go_back()
                    ready(page)
                    sidebar(page)
                    assert page.evaluate("__folderScope.state.sessionId") == session_id
                    assert page.evaluate("__folderScope.projectFolderScope.path") == str(folder)
                    expect(page.locator("#project-list .project-item")).to_have_count(2)
                    expect(page.locator("#feedback-note")).to_have_value(draft)
                    page.wait_for_function("!__folderScope.state.loadingProjects")
                    page.screenshot(path="/tmp/scene_feedback_folder_scope_067.png")
                    print("PASS scope persists across real scene navigation, reload and browser Back; returning restores the original session and draft", flush=True)

                    page.locator("#project-folder-all").click()
                    expect(page.locator("#project-folder-scope")).to_be_hidden()
                    expect(page.locator("#project-list .project-item")).to_have_count(5)
                    assert row_ids(page) == outside_ids | imported_ids | {registry.root.project_id}
                    assert page.evaluate("__folderScope.projectFolderScope.path") is None
                    page.reload()
                    ready(page)
                    sidebar(page)
                    expect(page.locator("#project-list .project-item")).to_have_count(5)
                    expect(page.locator("#project-folder-scope")).to_be_hidden()
                    status, retry = import_folder(page, folder)
                    assert status == 200 and not retry["imported"]
                    assert {item["project_id"] for item in retry["projects"]} == imported_ids
                    expect(page.locator("#project-list .project-item")).to_have_count(2)
                    assert page.evaluate("__folderScope.projectFolderScope.path") == str(folder)
                    assert factory.call_count == 2
                    for directory in (project, folder, outside, empty, blocked):
                        assert source_files(directory) == source_before[str(directory)]
                    for project_id, before in historical_before.items():
                        assert saved_state(registry.get(project_id)) == before
                    for scene_context in registry.contexts():
                        assert scene_context.gateway.adapter is None
                        assert scene_context.store.workspace()["created_thread_ids"] == []
                        assert scene_context.store.state["feedback"] == []
                    assert all(path.endswith("/api/projects/folders") or path.endswith("/api/projects/import-folder")
                               for _, path in writes), writes
                    assert not errors, errors
                    targets.assert_not_called()
                    models.assert_not_called()
                    print("PASS All restores every historical project and survives reload; reimport restores folder scope with the same IDs; all PNG/GLB/Blender/source bytes and old state are exact, no task/model/feedback calls", flush=True)
                    browser.close()
            finally:
                server.shutdown()
                worker.join(5)
                server.server_close()
    print("ALL FOLDER SCOPE BROWSER CHECKS PASSED", flush=True)


if __name__ == "__main__":
    main()

**语言 / Languages:** [简体中文](README.md) · **English** · [日本語](README.ja.md) · [한국어](README.ko.md) · [Русский](README.ru.md)

# Astra Visual Feedback Workbench

Give people and Astra a shared view of the same reference photo and current scene. Mark, draw, or point on the image or 3D view, then add a short note. The MCP tool returns the original image, annotated image, scene screenshots, and relevant context to the model. **Astra decides whether to change an object, camera, material, or reconstruction approach.** People do not need to enter coordinates or geometric constraints.

![Side-by-side annotations on the reference image and scene](preview.png)

## What it does

- View a reference image on the left and rotate, zoom, or select objects in the current 3D scene on the right. Switch between multiple reference images.
- Add points, rectangles, lines, arrows, and text on either side. Give related marks the same number when useful. If something is missing from the scene, you can mark only the reference image.
- Overlay the current reference image transparently on the 3D view for a visual comparison. The overlay **does not automatically align the cameras**.
- Click “Send to Astra” and keep using the same session. When Astra updates the scene, the page refreshes automatically. Annotation drafts and the viewing angle stay in the browser for the next round.

Submitted visual feedback includes the original reference image, annotated reference image, original and annotated screenshots of the current scene, relevant crops, a note, selected object IDs, the scene revision, and the viewing camera. Clicking a part inside a GLB also includes that node's name and path. Images are returned as MCP image content; metadata is provided in both structured content and text. Absolute local paths are also included so a host can read the images if it does not forward MCP image blocks.

## Try it locally

You need Python 3.11+ and a browser with WebGL support. Run these commands from the root of the cloned repository:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python backend/server.py
```

Open <http://127.0.0.1:18765/> and upload a reference image. The right side initially shows a sample chair; it will show your own scene after you connect one. This is a standalone browser workbench and does not require a client that embeds MCP Apps. The service listens only on `127.0.0.1`. Imported images, scenes, and feedback are stored in `backend/data/`, which is ignored by Git.

## Connect Codex / Astra

Register the MCP server from the repository root. `$PWD` expands to the absolute path of the repository:

```bash
codex mcp add scene_feedback -- "$PWD/.venv/bin/python" "$PWD/backend/mcp_server.py"
```

Set a longer interaction timeout for `[mcp_servers.scene_feedback]` in `~/.codex/config.toml`. Edit the table created by `codex mcp add` rather than adding a second table with the same name. If you use a Codex mode that does not show approvals, you can allow automatic approval for just the tool that creates the workbench; the feedback-reading tool is declared read-only. Then reload the MCP configuration: [Codex MCP setup guide](https://learn.chatgpt.com/docs/extend/mcp).

```toml
[mcp_servers.scene_feedback]
tool_timeout_sec = 900

[mcp_servers.scene_feedback.tools.request_visual_feedback]
approval_mode = "approve"
```

The recommended two-step call is:

```text
request_visual_feedback(
  reference_images=["/absolute/path/reference.jpg"],
  scene_glb_path="/absolute/path/current.glb",
  wait_for_submit=false
)
→ session_id, url, next_cursor

The user marks the scene at the URL and clicks “Send to Astra”

wait_visual_feedback(session_id, cursor=next_cursor)
→ image blocks + annotations/note/objects/camera/revision + a new next_cursor
```

You can also pass an existing list of scene objects with `current_scene={"objects": [...]}`, or omit the scene to keep using the workbench's current scene. `scene_glb_path` imports a GLB for preview; it does not prescribe how Astra models the scene. GLB and reference-image paths must be local to the machine running the MCP server. You can also add reference images directly in the browser.

After receiving feedback, Astra uses its own modeling tools to update the scene. For the next round, call `request_visual_feedback(session_id=..., scene_glb_path="/absolute/path/updated.glb", wait_for_submit=false)`, then call `wait_visual_feedback` with the returned `next_cursor`. Reusing `session_id` keeps the page and reference images open. A browser submission cannot independently resume a Codex turn that has already ended; Astra needs an MCP call waiting for the feedback, or a custom host must start the next turn.

If the host can open a local browser, `request_visual_feedback()` tries to open the page and wait for submission by default. If it cannot open the browser, the tool returns the URL immediately; you can then wait with `wait_visual_feedback`. The session stays open after a wait times out. Use `get_visual_feedback(session_id, cursor)` to check it, or wait again.

## Feedback format

Annotation coordinates are normalized screen coordinates (`0–1`) within the relevant image or view. They identify where the user pointed on the screen; they are neither world coordinates nor modeling commands. For example:

```json
{
  "scene_revision": 12,
  "note": "The top of the cabinet should be close to line ① in the left image; a lamp is also missing on the right.",
  "selected_object_ids": ["cabinet"],
  "annotations": [
    {
      "pane": "reference",
      "reference_image_id": "<reference-id>",
      "type": "line",
      "group_id": "1",
      "coordinates": {"x": 0.23, "y": 0.32, "x2": 0.68, "y2": 0.32}
    },
    {
      "pane": "scene",
      "type": "point",
      "group_id": "1",
      "object_id": "cabinet",
      "coordinates": {"x": 0.54, "y": 0.46}
    }
  ]
}
```

Both `group_id` and `object_id` are optional. When a node inside a GLB is selected, the feedback also includes `selected_scene_nodes`. Each node has its parent model object ID, its index path within the GLB, and its name when available. These are additional references to the image location, not instructions for the model to execute. The original and annotated images are kept separately so Astra can distinguish the photo's original content from the person's marks. If the scene revision changes, old scene marks show their source revision in the interface. Submission checks the current revision to avoid treating an old view as a new one. Retained marks are sent again in the next round; you can delete them individually or clear them all.

The existing `get_scene`, `update_scene`, `replace_scene`, and `import_scene_model` tools remain available for displaying the scene or compatibility with existing callers. The workbench itself does not modify GLB meshes.

## Verification and scope

```bash
.venv/bin/python -m unittest discover -s backend/tests -v
```

This is a prototype for a trusted local environment. Other processes on the same machine can access the page and session API, so do not expose the local port to untrusted clients. Apply automatic approval only to a local server you trust. Codex CLI has been tested reading reference-image content from this tool's MCP image blocks. Other hosts may or may not forward the images, depending on their implementation. If a host displays only text, Astra can read the local image paths in the feedback.

Original project code is licensed under the [MIT License](LICENSE). The bundled Three.js files retain their [original MIT license](web/vendor/three/LICENSE).

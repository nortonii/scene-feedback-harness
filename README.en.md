**语言 / Languages:** [简体中文](README.md) · **English** · [日本語](README.ja.md) · [한국어](README.ko.md) · [Русский](README.ru.md)

# Codex Visual Reconstruction Workbench

In the default mode, mark the reference image and the current 3D scene, write one prompt, and click “Send.” The workbench sends your text, original image, annotated image, and scene snapshot as **a user message in the same workbench-owned Codex thread**. Once Codex edits the project and publishes a GLB, the result appears on this page. You do not need to return to a terminal to trigger another read. An existing Codex desktop task can use the same review UI through the external MCP mode below.

![Side-by-side annotations on a reference image and scene](preview.png)

## How it works

```text
Browser: reference image + 3D scene + annotations + text
                      │ User clicks “Send”
                      ▼
              Local Workspace Gateway
                      │ Real image inputs and execution events
                      ▼
        Codex App Server: same project, same thread
                      │ Edit source files, export GLB, call MCP tools
                      ▼
              Workbench refreshes the scene
```

In the default mode, the Gateway keeps one persistent Codex thread for this project. It starts `codex app-server` over stdio and sends images as `localImage` input items. This mode does not inject messages into other Codex desktop or terminal sessions you may have open. MCP is used to read context, request a user review, and publish the scene; **the web page's Send button starts a user turn directly**.

This interface helps people point out problems. It does not define a reconstruction algorithm or ask people to enter coordinates or geometric constraints. Codex can use the modeling, reconstruction, and editing tools already available in the project.

## Page layout and navigation

The page uses a warm off-white background and black or gray controls. On wide screens the reference and scene sit side by side, with a horizontal toolbar above and one prompt box below. Task, activity, and reference panels open when needed.

| Control | Purpose |
| --- | --- |
| Toolbar | `选择` (select), point, box, line, and arrow; `更多` (more) contains text, freehand, and optional correspondence numbers. `物品 / 部件` switches item / part selection |
| Current task name at the top | Open the task panel to switch recipients or create a task with a name, model, reasoning effort, and permissions |
| `记录` at the top | Execution, approvals, feedback queue, and message history. Closed by default; a new approval or actionable failed, stale, or uncertain-delivery item opens it once |
| `物体与标记` below the canvases | Object and mark references. Clicking `引用` inserts at the prompt cursor, closes the panel, and returns focus to the prompt |
| Reference pane’s `导入` menu | Import reference images, a video, or a frame sequence, and set the sequence FPS |
| Timeline’s `选项` menu | Feedback scope, time interval, and GLB animation action selection |

The bottom button normally reads `发送反馈` (send feedback). While the task is running it reads `加入下一轮` (queue for the next turn). When viewing an older scene snapshot, a button such as `标注 v13 · 查看最新 v15 ↗` appears below the overlay controls; click it to view the latest live scene. The old snapshot and marks remain available.

## What you can do on the page

- Switch between reference images and pan or zoom them on the left; rotate, zoom, and select nodes in a GLB scene on the right.
- Switch between item and part selection. Item selection picks a top-level GLB scene node (one whole physical item in the current asset); part selection picks the detailed named node you click. Primitive scene objects are always selected as a whole. Either level can be referenced in the same prompt.
- Draw points, rectangles, lines, arrows, freehand strokes, and text on either side. Related marks can share a number; a missing object can be marked only on the reference image.
- Use the toolbar's `撤销 / 重做` (undo / redo) to reverse or restore adding, deleting, or clearing marks. Shortcuts are `Ctrl/Cmd + Z` and `Ctrl/Cmd + Shift + Z`; the prompt field keeps native text undo. Annotation history lasts only for the current page and starts fresh after a reload.
- Write one freeform prompt to Codex. Put the cursor in your text and click “引用” (“Insert reference”) beside an object, a selected GLB node, or a visual mark to insert multiple references into that same prompt. Their forms are `[[object:ID]]`, `[[node:MODEL_ID:0/2]]`, and `[[annotation:ID]]`. For example, ask Codex to move a cabinet toward a marked spot and align its top with a marked line, then send the prompt once. You can mark and reference something that has not been modeled yet on the reference image; the scene still highlights only one selected object at a time.
- Click `标注` (“Annotate”) to bind scene marks to **the screenshot, camera, selected object, and scene revision at that moment**. Rotating the live 3D view will not move old marks onto other objects, and a newly published scene will not overwrite a snapshot being annotated.
- Inserting a mark reference, choosing `选择` (“Select”), or clicking `返回 3D` returns to live 3D selection. The frozen screenshot and existing marks remain; click `看标注` (“View marks”) to continue marking that screenshot.
- Sending saves an immutable feedback packet: your exact words, original and annotated reference images, clean and annotated scene screenshots, selected nodes, camera, and scene revision. Marks are your hints; the original image is kept separately.
- Once the server confirms that it saved feedback over HTTP, the prompt text clears for the next round while existing marks remain, even if delivery to Codex is queued or uncertain. If the HTTP save fails or is unconfirmed, the local draft and outbox entry remain for correction and retry.
- New feedback submitted while Codex is running joins a queue for the next turn. If the scene changes while feedback is queued, the page first asks you to confirm feedback made against the old revision. Approval requests and stop actions are also handled on the page. Your project, thread, and drafts remain available after refreshing the page.

Scene input currently uses a self-contained `.glb` file. Publishing requires geometry and texture resources to be embedded in the GLB's BIN chunk. Both external URIs and data URIs are rejected: even though a data URI can be self-contained, this version accepts only BIN-embedded resources. You can submit a reference image without an initial scene.

## Align to a reference camera

Selecting a reference with camera metadata automatically moves the 3D view to its capture pose. The overlay is shown by default; adjust its opacity directly, or set it to 0% to hide it. After orbiting manually, click `对齐` (“Align”) to return to that camera. When an undistorted copy is supplied, the overlay uses it to match the pinhole camera projection; the original reference remains separate for viewing and feedback. References without calibration can still be compared manually. Approximate intrinsics can leave residual alignment error near the image edges.

Each reference camera stores `camera_to_world` (a row-major 4×4 matrix in the GLB world) and pixel-based `intrinsics` (`width`, `height`, `fx`, `fy`, `cx`, `cy`). Attach `camera` and optional `alignment_image_data_url` to an existing image by its `reference_id` through the protected `POST /api/workspace/reference-cameras` endpoint. A `reference_cameras.json` manifest in the private workbench data directory matches camera metadata by filename prefix on future imports ([example](examples/reference_cameras.example.json)); send `{"apply_manifest":true}` to the same endpoint to apply it to existing images. Camera poses and the published GLB must use the same world coordinates.

## Dynamic scenes: shared timeline and feedback across frames

A reference image sequence or video can share a timeline with an animated GLB. Play, pause, seek, and stepping to the previous or next sampled frame update both the reference and the 3D animation. The existing static-image and static-GLB workflow remains available. GLB animation supports node translation, rotation, scale, skinning, and morph targets with fixed topology. Variable-topology mesh caches, fluids, and live physics simulation are not integrated yet.

- Starting a mark pauses playback and records its time, reference frame, camera, and scene revision. Keep up to 8 scene snapshots from different moments and return to a moment to review or annotate it.
- One prompt can reference marks and objects from several moments. Use the timeline’s `选项` menu to choose the marked frames (`所标帧`), a time interval, or the whole clip as the feedback scope. The interval expresses the requested scope; drawn lines are not interpreted as motion paths or geometric constraints.
- Marks appear on their own frame. A submission includes original and annotated references and scene snapshots for the selected moments, plus timestamps, cameras, object references, and scene revisions. The whole video is not sent frame by frame to the model.
- Each reference frame can carry its own camera pose, used for alignment when that frame is selected. Publishing a new GLB preserves the playback position; seeking does not increase `scene_revision`.
- Preserve `semantic_id` or `stable_id` in node `extras` when exporting a GLB to help identify the same object across frames and exports. A node path locates a node only within its scene revision. Names and paths are not guaranteed to survive a new export; this does not automatically track objects.

Use the reference pane’s `导入` menu to import an ordered image sequence at a chosen FPS or a video for server-side sampling. Video import requires executable `ffmpeg` and `ffprobe` on the service host; image sequences do not. Video is sampled on a common time grid at 10 FPS by default, with a maximum of 600 frames per clip. Imports exceeding the limit fail; lower the sampling rate or trim the clip first.

Codex can also import project-local material with `workspace_set_reference_clip(manifest_path=None, video_path=None, fps=None, camera_manifest_path=None, clear=False)`. Supply either `manifest_path` or `video_path`. Image sequences use the manifest’s own `fps` (30 when omitted); the tool’s `fps` parameter applies only to video (default 10). A frame can supply `time_sec`; otherwise its zero-based index divided by the manifest FPS is used. Frame rate is not inferred from filenames. Frame paths may be relative to the manifest, but every input must remain inside the project. FPS must be between 0.1 and 120. The video size limit is 40 MiB for browser uploads and 250 MiB for project-local files. `clear=True` removes the reference clip without changing the scene revision.

Example image-sequence manifest (all input files must be inside the current project):

```json
{
  "name": "assembly-review",
  "fps": 10,
  "frames": [
    {"path": "/absolute/path/to/project/frames/0000.png", "time_sec": 0.0},
    {"path": "/absolute/path/to/project/frames/0001.png", "time_sec": 0.1}
  ]
}
```

To preserve camera alignment, import through a manifest / MCP: add the `camera` schema defined above to each sequence frame. Importing image or video files in the browser alone does not obtain camera poses. A video can supply a separate `camera_manifest_path`. Example fixed-camera manifest (replace these example values with actual calibration):

```json
{
  "camera": {
    "camera_to_world": [[1,0,0,0],[0,1,0,0],[0,0,1,2],[0,0,0,1]],
    "intrinsics": {"width":1920,"height":1080,"fx":1200,"fy":1200,"cx":960,"cy":540}
  }
}
```

For a moving camera, use `{"frames":[{"time_sec":0.0,"camera":…},…]}` covering the sampled video times. This version matches nearby samples; it does not interpolate or estimate cameras. Video camera intrinsics are rescaled to the sampled images. Image-sequence intrinsics must already match each image’s dimensions.

Import the manifest, or sample a video at the chosen FPS:

```text
workspace_set_reference_clip(manifest_path="/absolute/path/to/project/clip.json")
workspace_set_reference_clip(video_path="/absolute/path/to/project/reference.mp4", fps=10, camera_manifest_path="/absolute/path/to/project/video-cameras.json")
```

## Quick start: room and cabinet

The browser UI uses Chinese labels: `标注` means “Annotate,” `返回 3D` returns to live selection, and `发送反馈` means “Send feedback.”

You need Python 3.11+, a WebGL-capable browser, and a signed-in **`codex-cli 0.156.1`**. The App Server request and response formats were checked against JSON Schema generated by this version. Other versions produce an explicit error instead of silently using incompatible fields.

Run these commands from the root of the cloned repository:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
codex --version
.venv/bin/python examples/room_demo/seed_demo.py
.venv/bin/python backend/server.py --project-dir "$PWD" --data-dir "$PWD/examples/room_demo/output/data"
```

The Gateway automatically configures the workbench’s `scene_feedback` MCP tools in the project's Codex thread; you do not need to run `codex mcp add` manually. The Gateway passes its port, data directory, and project directory to that thread, preventing another project or global MCP setting from pointing it at the wrong workbench.

Open <http://127.0.0.1:18765/>. The target illustration is on the left, and the initial room GLB is on the right. Select the cabinet, point out its target position in the image, enter “The cabinet should be closer to the left wall; please adjust it according to the image on the left,” and click “Send.” The demo source parameters are in [`examples/room_demo/scene.json`](examples/room_demo/scene.json). [`build_scene.py`](examples/room_demo/build_scene.py) generates a GLB from those parameters; the result appears on the page after Codex calls `workspace_publish_scene`. `seed_demo.py` uses the separate `examples/room_demo/output/data` directory, so it does not overwrite regular workspace data.

For your own project, run:

```bash
.venv/bin/python backend/server.py --project-dir "/absolute/path/to/your/project" --data-dir "/absolute/path/to/private/workspace-data"
```

Add reference images in the browser. Have Codex build or export a self-contained GLB from your project's source files and publish it through MCP. The project directory and thread ID are stored in the data directory, allowing the same thread to be restored after a restart. A data directory is bound to one project and will not silently switch to another.

## Review from an existing Codex desktop task (external MCP mode)

An existing Codex desktop task can receive feedback in two ways. With `--external-review` alone, that task calls `request_visual_feedback`. By default the tool waits for browser submission **even when the server has no graphical desktop and cannot open a browser automatically**. After you click “Send,” the text and images return as an MCP tool result to the task that is still waiting. Explicitly passing `wait_for_submit=False` makes the tool return a URL, `session_id`, and `next_cursor` immediately; the task must then call `wait_visual_feedback(session_id, cursor=next_cursor)` to receive the submission. If the waiting call has already ended or timed out, the feedback remains saved but will not wake an idle task. Read it from the original task or use the task binding option below. `get_visual_feedback` can reread submitted feedback.

Install the dependencies above first. Use absolute paths in a terminal to register a global MCP server and run a separate review service:

```bash
REPO=/absolute/path/to/scene_feedback_harness
PROJECT=/absolute/path/to/your/existing/project
DATA=/absolute/path/to/private/external-review-data
codex mcp add scene_feedback_external \
  --env SCENE_FEEDBACK_PORT=18768 \
  --env SCENE_FEEDBACK_DATA_DIR="$DATA" \
  --env SCENE_FEEDBACK_PROJECT_DIR="$PROJECT" \
  -- "$REPO/.venv/bin/python" "$REPO/backend/mcp_server.py"
"$REPO/.venv/bin/python" "$REPO/backend/server.py" \
  --project-dir "$PROJECT" --data-dir "$DATA" --port 18768 --external-review
```

In `~/.codex/config.toml`, set `tool_timeout_sec = 900` under the `[mcp_servers.scene_feedback_external]` table created by `codex mcp add`. Use the review tools' default `timeout_sec` of 600 or less, leaving time to transfer images. **Restart Codex desktop** so the existing task refreshes its MCP tool catalog. In that task, say “进入人工调试模式” (“enter human review mode”) or explicitly ask it to call `request_visual_feedback` with project-local reference image paths and the current GLB path (omit the GLB if no initial scene exists). In the unbound mode, Send completes the waiting MCP call; if the tool was explicitly told not to wait, call `wait_visual_feedback` separately.

`PROJECT` is the reconstruction root under review; the reference images and GLB must be inside it. It may be a subdirectory of the Codex task's working directory. `DATA` is a private directory dedicated to that project. The example uses a separate port, data directory, and MCP server name to keep it apart from the default mode above. Published scenes can still update the page through `workspace_publish_scene`.

### Bind an existing desktop task for automatic continuation

To make “Send” continue **the already open Codex desktop task** without keeping an MCP tool call waiting, stop the service above and restart it with the same project and data directory plus that task's UUID. `THREAD_ID` is the Codex desktop task ID, **not** the workbench `session_id`:

```bash
THREAD_ID=your-existing-codex-task-uuid
"$REPO/.venv/bin/python" "$REPO/backend/server.py" \
  --project-dir "$PROJECT" --data-dir "$DATA" --port 18768 \
  --external-review --shared-thread-id "$THREAD_ID"
```

The workbench connects to the running **Codex Desktop App Server on the same host and under the same user**. Each submission becomes a new user turn in that existing task, with your text, original and annotated images, and scene snapshots; no new task is created. Feedback waits in a queue while the task is busy. The page shows queued, running, and completed delivery states. If the scene revision changes while feedback waits, confirm the old-revision feedback before it is sent. Human approval and interaction requests appear in the workbench; it never approves them automatically. If a disconnect makes delivery uncertain, inspect the original task history before retrying to avoid duplicates. This mode uses `websocket-client`, installed through `backend/requirements.txt`. In bound mode, `request_visual_feedback` opens or updates the workbench and returns its URL; **no MCP result needs to remain pending**.

Feedback known not to have been sent stays queued while the connection is unavailable or the original task is busy, then retries automatically. A definite send failure has a manual Retry button. If delivery is uncertain, the workbench checks the original task history by feedback ID. An unresolved item is isolated so later feedback can proceed, but it is **never resent automatically**. Check the original task first; use “Confirm not received, retry” only when the feedback is absent.

To change agents, open the task that should take over in Codex Desktop, then click the current task name at the top of the workbench and select it under `发送到哪个 Codex 任务` in the task panel. The current scene and references stay in place. New feedback goes to the selected task; unsent feedback remains assigned to its original task and resumes only if you switch back. The Codex task controls its own model; changing the workbench target does not change that model.

You can also expand `新建 Codex 任务` (“Create Codex task”) in that task panel, select an available model, reasoning effort, and permission mode, then click “Create and switch.” The default is workspace write with approvals when needed. You can explicitly choose full access (`never` approval policy, removing routine file and command sandbox approvals) or read only (writes require approval). Tool-specific consent or interaction may still need a response. This starts a fresh conversation without copying the previous chat history. The scene, references, and workbench annotations remain available. No task is created until you click the button. The new task's permissions cannot retroactively change a turn already running or awaiting approval.

## Open the page from another device on the LAN

Keep the service on the machine that holds the project and runs Codex and MCP; other devices only need a browser. Stop the old service on this port, then use the same `REPO`, `PROJECT`, and `DATA` values as above. Replace the example IP with the **service host's** reachable LAN IPv4 address:

```bash
LAN_IP=192.168.1.10
"$REPO/.venv/bin/python" "$REPO/backend/server.py" \
  --project-dir "$PROJECT" --data-dir "$DATA" --port 18768 --external-review \
  --listen-host 0.0.0.0 --public-base-url "http://$LAN_IP:18768"
```

The startup output or the MCP `request_visual_feedback` / `workspace_open` result provides a complete link containing `access_token`. Open that **full link** in the other device's browser. After checking it, the page removes the token from the address bar and keeps access in a browser cookie. Entering only `http://$LAN_IP:18768/` will not grant access; keep the access link private. The default workbench mode accepts the same two flags with its existing port and data directory; omit `--external-review` there. For a persistent service, run the same command under a systemd user service. The workbench page and its protected API are available over the LAN. Codex on the project host still calls MCP through `127.0.0.1`. If the page is unreachable, allow the selected TCP port in the host firewall. Plain HTTP LAN mode is intended for a trusted network.

For the desktop task binding option, also keep `--shared-thread-id "$THREAD_ID"` in the LAN startup command. The browser may be on another LAN device; the service connecting to Codex Desktop must still run under the same user on the desktop task's host.

## MCP tools

| Tool | Purpose |
| --- | --- |
| `workspace_open` | Return or open this project's workbench URL |
| `workspace_get_context` | Read current reference images, scene revision, and project context |
| `workspace_get_feedback` | Read submitted feedback and its actual images |
| `workspace_publish_scene` | Validate and publish a new GLB, check the expected revision, and notify the page to refresh |
| `workspace_set_reference_clip` | Import timestamped reference frames with optional per-frame cameras, or sample a project-local video with FFmpeg |
| `workspace_request_feedback` | Default mode: ask the user to inspect something on the page and return immediately; their reply becomes the next user message |
| `request_visual_feedback` / `wait_visual_feedback` | Unbound external MCP mode: wait and return text and images as a tool result; bound desktop mode: the former returns the page URL and submission starts a new turn |
| `get_visual_feedback` | External MCP mode: reread submitted visual feedback |

The repository's [Visual Reconstruction Skill](.agents/skills/visual-reconstruction-feedback/SKILL.md) reminds Codex to distinguish original images from annotations, edit project source files, and publish a GLB when the result is ready. Default mode sends a new turn directly. Unbound external MCP mode returns feedback through a waiting `request_visual_feedback` or `wait_visual_feedback` call. Bound desktop mode starts a new turn after browser submission.

## Verification and limits

```bash
.venv/bin/python -m unittest discover -s backend/tests -v
```

The real App Server integration was tested with two different local images: Codex recognized a red image in the first turn, then recognized a blue image in the **same thread** after stdio was closed and restarted. The `thread/read` history contains `localImage` items for both turns. This verifies that the images reached the model, rather than merely sending their file paths.

An end-to-end Selenium run also exercised two browser → Codex turns in the same thread. In the first turn, numbered rectangles were drawn on the reference image and scene and submitted from the browser. Codex received five actual image inputs, edited the demo's `scene.json`, generated a GLB, and successfully published through the project-scoped `workspace_publish_scene` MCP tool twice. Approval was handled on the page; the workbench refreshed as the scene advanced from revision 2 to 3 and then 4. The cabinet's final center was `x=-0.8`. In the second turn, another line was drawn on the reference image and submitted from the page. Codex received five more `input_image` items in the same thread and replied with a confirmation. It did not edit files or publish another scene, so the final revision remained 4.

By default the service listens only on `127.0.0.1`. With the LAN flags above, the page requires an access link and cookie, and submissions still require the browser capability. Codex turns in the `workspace-write` sandbox enable `networkAccess: true` to reach the local MCP Gateway. Users decide approval requests on the page; the workbench does not approve them automatically. Runtime data and demo output are excluded from Git.

Project code is licensed under the [MIT License](LICENSE). The bundled Three.js files retain their [original MIT license](web/vendor/three/LICENSE).

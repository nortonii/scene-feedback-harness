**语言 / Languages:** [简体中文](README.md) · **English** · [日本語](README.ja.md) · [한국어](README.ko.md) · [Русский](README.ru.md)

[Changelog (Chinese)](CHANGELOG.md) · [Maintenance guide (Chinese)](CONTRIBUTING.md)

# Codex Visual Reconstruction Workbench

In the default mode, mark the reference image and the current 3D scene, write one prompt, and click “Send.” The workbench sends your text, original image, annotated image, and scene snapshot as **a user message in the same workbench-owned Codex thread**. Once Codex edits the project and publishes a GLB, the result appears on this page. You do not need to return to a terminal to trigger another read. An existing Codex desktop task can use the same review UI through the external MCP mode below.

![Reference and scene with a collapsible glass conversation dock](preview.png)

## Plugin and MCP Events (experimental)

Install the workbench as a local Codex plugin, or connect an MCP 2.0 host through event subscriptions and visual evidence read tools. Installing a local plugin does not guarantee native event wake-up of an existing Codex task; verify host subscriptions and callbacks before switching delivery. See [Plugin and MCP Events setup](docs/mcp-events-plugin.md) (Chinese) for project binding, ZIP packaging, and registered hosted connections.

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

The page retains its warm off-white background and black or gray controls, with a consistent system sans-serif typeface and distinct sizes for headings, body text, controls, and metadata. Repeated helper copy is omitted; actionable queue, connection, and delivery states appear when needed. Compact headers and margins give the side-by-side reference and scene more room. A translucent glass conversation dock with soft highlights and rounded edges floats at the bottom; task, activity, and reference panels open when needed.

| Control | Purpose |
| --- | --- |
| Toolbar | `选择` (select), point, box, line, and arrow; `更多` (more) contains text, freehand, and optional correspondence numbers. `物品 / 部件` switches item / part selection |
| Scene name at the top | Switch independent scenes; with Codex Desktop connected, create a new scene and reconstruction task |
| Current task name at the top | With Codex Desktop connected, switch the current scene's recipient or create a task with a name, model, reasoning effort, and permissions |
| Conversation dock | History appears above the prompt. `收起记录` hides history only; `收起会话` hides the entire dock, and `展开会话` at the lower right brings it back |
| `记录` at the top | Execution, approvals, and feedback queue. Closed by default; a new approval, actionable failure, or uncertain delivery opens it once |
| `物体与标记` in the dock header | Object and mark references. Clicking `引用` inserts at the prompt cursor, closes the panel, and returns focus to the prompt |
| Reference pane’s `导入` menu | Import reference images, videos, or a frame sequence, and set the sequence FPS; dynamic imports append independent views |
| `叠图` at the top of the scene pane | Toggle the reference overlay; click its percentage to open the opacity slider and presets |
| Reference view selector | Switch dynamic reference views on the shared timeline |
| Reference pane’s `人体结果` menu | View, import and cite 2D skeletons produced by the capsule skill |
| Timeline’s `选项` menu | Feedback scope, time interval, and GLB animation action selection |

Collapsing the dock keeps the current unsent draft, marks, camera, and frozen evidence intact, so you can keep selecting and annotating. Inserting an object, mark, or human-pose reference opens the prompt automatically. With history expanded, drag the thin handle along the top edge to resize the dock; pulling upward shows more history. Double-click it to restore the default size, or focus it and use the up/down arrow keys. Collapsing history hides the resize handle and keeps the prompt compact; explicitly expanding history restores resizing and the previous height. Collapse preferences and the custom height are saved per workspace session in this browser and restored on reload; smaller windows clamp the height to fit. New messages show an unread count without opening the dock. Reading older messages preserves your scroll position; use the latest-message button to catch up. The dock replays recent persisted workspace messages, displaying up to 100 entries. In unbound external MCP mode, later model replies still appear in the original Codex task.

The prompt fills the dock width, with a circular up-arrow send button in its lower-right corner. Its tooltip and accessible name show `发送反馈` (send feedback), or `加入下一轮` (queue for the next turn) while the task is running. You can also send with `Ctrl/Cmd + Enter`; confirming an IME composition does not submit. When viewing an older scene snapshot, a button such as `标注 v13 · 查看最新 v15 ↗` appears below the scene toolbar; click it to view the latest live scene. The current round’s unsent snapshot and marks remain available. Once the server confirms that it saved feedback, they are cleared from the editor; the submitted packet retains the complete evidence.

## What you can do on the page

- Switch between reference images and pan or zoom them on the left; rotate, zoom, and select nodes in a GLB scene on the right.
- Switch between item and part selection. Item selection picks a top-level GLB scene node (one whole physical item in the current asset); part selection picks the detailed named node you click. Primitive scene objects are always selected as a whole. Either level can be referenced in the same prompt.
- Draw points, rectangles, lines, arrows, freehand strokes, and text on either side. Related marks can share a number; a missing object can be marked only on the reference image.
- Use the toolbar's `撤销 / 重做` (undo / redo) to reverse or restore adding, deleting, or clearing marks. Shortcuts are `Ctrl/Cmd + Z` and `Ctrl/Cmd + Shift + Z`. `清空` (clear) removes all marks, fixed snapshots, and saved dynamic moments in one click, returns to the live 3D scene, and can be undone; it keeps the prompt and reference images. The prompt field keeps native text undo. Annotation history starts fresh after a reload or a successfully saved submission. Undo does not restore a submitted round’s marks into the next draft.
- Write one freeform prompt to Codex. Put the cursor in your text and click “引用” (“Insert reference”) beside an object, a selected GLB node, or a visual mark to insert multiple references into that same prompt. Their forms are `[[object:ID]]`, `[[node:MODEL_ID:0/2]]`, and `[[annotation:ID]]`. For example, ask Codex to move a cabinet toward a marked spot and align its top with a marked line, then send the prompt once. You can mark and reference something that has not been modeled yet on the reference image; the scene still highlights only one selected object at a time. In the `物体与标记` (objects and marks) panel, click `引用全部标记` (reference all marks) to insert all missing mark references at the caret, including saved dynamic moments. Existing references are not repeated; the panel closes so you can continue writing.
- Select a drawing tool and click or drag directly in the right pane to annotate. The workbench automatically saves **the screenshot, camera, selected object, and scene revision at that moment**. You can also click `标注` (“Annotate”) to freeze the view beforehand. A static scene reuses its screenshot when you continue drawing at the same view and revision. Replacing an annotated static screenshot asks before clearing its old scene marks. Cancel keeps it unchanged, and a confirmed replacement can be undone. Dynamic scenes keep snapshots for each saved moment. Rotating the live 3D view will not move old marks onto other objects, and a newly published scene will not overwrite a snapshot being annotated.
- Inserting a mark reference, choosing `选择` (“Select”), or clicking `返回 3D` returns to live 3D selection. The current round’s unsent frozen screenshot and existing marks remain; click `标注截图` (“Annotation snapshot”) to return to the saved view and continue marking it. Scene arrows and other marks stay attached to their original screenshot. Rotating the live view or refreshing the page does not overwrite it: sending includes the marked screenshot and its original camera, rather than placing those arrows over the newly rotated view.
- Sending saves an immutable feedback packet: your exact words, original and annotated reference images, clean and annotated scene screenshots, selected nodes, camera, and scene revision. Marks are your hints; the original image is kept separately.
- Once the server confirms that it saved feedback over HTTP, it automatically clears the prompt, all marks, fixed snapshots, saved dynamic moments, object/node selection, and this round's references. The view returns to live 3D and an empty draft is saved for the next round, even if delivery to Codex is queued or uncertain. Submitted feedback evidence, reference images, and the current animation time remain; when a new scene result arrives, any draft already started for the next round remains. If the HTTP save fails or is unconfirmed, the local draft and outbox entry remain for correction and retry.
- New feedback submitted while Codex is running joins a queue for the next turn. Saved feedback is delivered automatically even if the scene changes while it waits. The model receives both the original snapshot revision and the current revision at delivery, so it can interpret your prompt against the older evidence. Even if the snapshot is already old when you send, clicking Send submits it directly without another confirmation; the model still receives its actual revision. Approval requests and stop actions are also handled on the page.
- Refreshing restores your project, thread, and the current unsent draft and original snapshots; it does not turn an old screenshot into a new revision or restore a successfully saved round to the editor. “View latest” switches to the live scene while retaining unsent evidence. To annotate the current result afresh, use `清空` (clear), then draw again; accidental clearing can be undone. Snapshot and saved feedback revisions are never rewritten.

Scene input currently uses a self-contained `.glb` file. Publishing requires geometry and texture resources to be embedded in the GLB's BIN chunk. Both external URIs and data URIs are rejected: even though a data URI can be self-contained, this version accepts only BIN-embedded resources. You can submit a reference image without an initial scene.

## Switch scenes and start a new reconstruction task

Click the scene name at the top to open the scene list. Return to an existing scene, or enter a name and choose a model, reasoning effort, and permissions to create **a new scene and a new Codex Desktop task**. Import reference images or video, then send your reconstruction instructions from the prompt box. An initial GLB is optional.

Each scene keeps its own references, cameras, dynamic views, reconstruction, feedback history, and receiving task. Switching saves the current draft; returning restores it. Separate tabs can view different scenes while previous tasks and queued feedback stay with their original scene. Finish an in-progress submission or import before switching.

New working directories live under `projects/<project_id>/workspace` in the service data directory, using the same LAN address and port. Each new task gets MCP configured for its own directory; existing MCP clients keep their original scene. If task creation is uncertain or binding fails, the scene stays in the list for recovery. Repeating the same creation request does not create another task.

Creating Desktop tasks requires a workbench connected to Codex Desktop (`--external-review --shared-thread-id`); the browser may run on another LAN device. The existing task panel still switches or creates tasks **within the current scene**.

## Align to a reference camera

Selecting a reference with camera metadata automatically moves the 3D view to its capture pose. The scene toolbar has a one-click overlay toggle and a percentage control that opens an opacity slider plus 25% / 45% / 75% presets. The initial opacity is 45%; 0% hides the overlay, and turning it back on restores the last nonzero value. Display preferences are saved per workspace session in this browser. Snapshot annotation pauses the overlay; returning to 3D restores the previous settings. After orbiting manually, click `对齐` (“Align”) to return to that camera. When an undistorted copy is supplied, the overlay uses it to match the pinhole camera projection; the original reference remains separate for viewing and feedback. References without calibration can still be compared manually. Approximate intrinsics can leave residual alignment error near the image edges.

Each reference camera stores `camera_to_world` (a row-major 4×4 matrix in the GLB world) and pixel-based `intrinsics` (`width`, `height`, `fx`, `fy`, `cx`, `cy`). Attach `camera` and optional `alignment_image_data_url` to an existing image by its `reference_id` through the protected `POST /api/workspace/reference-cameras` endpoint. A `reference_cameras.json` manifest in the private workbench data directory matches camera metadata by filename prefix on future imports ([example](examples/reference_cameras.example.json)); send `{"apply_manifest":true}` to the same endpoint to apply it to existing images. Camera poses and the published GLB must use the same world coordinates.

## Dynamic scenes: multiple views on a shared timeline

Multiple reference views of one clip, each an image sequence or video, can share a timeline with an animated GLB. Play, pause, seek, and stepping to the previous or next sampled frame update both the reference and the 3D animation. The existing static-image and static-GLB workflow remains available. GLB animation supports node translation, rotation, scale, skinning, and morph targets with fixed topology. Variable-topology mesh caches, fluids, and live physics simulation are not integrated yet.

- Switching reference views keeps the current time and automatically aligns to the new view’s current-frame camera; views without camera metadata can be compared manually. Use the same time origin for each view’s `time_sec`. One submission can reference marks from multiple views; each mark retains its view name, `view_id`, frame/time, original image, and camera.
- Reference thumbnails whose camera calibration uniquely matches a dynamic view show `同步` (synced). Clicking one switches to that view, which stays active when you seek. Unmatched or ambiguous thumbnails remain static references; use the left-side view selector to choose a dynamic view directly.
- Inspect static thumbnails while paused; playing, seeking, or stepping to the previous or next frame automatically returns to the dynamic reference sequence. A newly published sequence resumes synchronization; saved marks keep their original frames.
- Starting a mark pauses playback and records its time, reference frame, camera, and scene revision. Keep up to 8 saved moments in total across all views and return to a moment to review or annotate it.
- One prompt can reference marks and objects from several views and moments. Use the timeline’s `选项` menu to choose the marked frames (`所标帧`), a time interval, or the whole clip as the feedback scope. The interval expresses the requested scope; drawn lines are not interpreted as motion paths or geometric constraints.
- Single and bulk citations of timed marks show `片段第 N 帧 · T s` (clip frame N · T s). Citations also show the view name; structured feedback carries `view_id`. The ordinal starts at 1 within that view’s imported or sampled sequence; saved marks keep their own frame and time as the timeline moves. The model receives a zero-based `frame_index` derived from the saved reference ID and that view’s sequence order. Static-reference or GLB-only marks show only the time.
- Marks appear on their own view and frame. A submission includes original and annotated references and scene snapshots for the selected moments, plus timestamps, cameras, object references, and scene revisions. The whole video is not sent frame by frame to the model.
- Each reference frame can carry its own camera pose, used for alignment when that frame is selected. Publishing a new GLB preserves the playback position; seeking does not increase `scene_revision`.
- Preserve `semantic_id` or `stable_id` in node `extras` when exporting a GLB to help identify the same object across frames and exports. A node path locates a node only within its scene revision. Names and paths are not guaranteed to survive a new export; this does not automatically track objects.

Use the reference pane’s `导入` menu to select several videos; each is appended as a separate view and sampled on the server. Each ordered-image import creates one view at the chosen FPS. Browser imports append views and preserve existing views and marks. Video import requires executable `ffmpeg` and `ffprobe` on the service host; image sequences do not. Video is sampled on a common time grid at 10 FPS by default. There are at most 8 views and 600 frames per view. Imports exceeding the limits fail; lower the sampling rate or trim the clip first.

Codex can also import project-local material with `workspace_set_reference_clip(manifest_path=None, video_path=None, fps=None, camera_manifest_path=None, clear=False, append_view=False, view_name=None)`. Supply either `manifest_path` or `video_path`. Use `append_view=True` to append views and preserve existing material and marks; `view_name` can name the new view. The default `append_view=False` preserves the old API’s replacement behavior and replaces the entire existing view group. Submit feedback that depends on old frames before replacing them. `clear=True` removes the entire reference view group without changing the scene revision.

Image sequences use the manifest’s own `fps` (30 when omitted); the tool’s `fps` parameter applies only to video (default 10). A frame can supply `time_sec`; otherwise its zero-based index divided by the manifest FPS is used. Frame rate is not inferred from filenames. FPS must be between 0.1 and 120. Size limits apply separately to each view: 40 MiB for a browser video file or an image sequence’s total image data; 250 MiB for a project-local video file, its total decoded frames, or an image sequence’s total image data. Every input file must remain inside the project.

An existing single clip continues as the primary view with its original `clip_id`, frames, and marks. The top-level `reference_clip` fields still describe the primary view; its `views` array contains only additional views. Appending keeps the primary `clip_id` stable, and each view’s own `clip_id` serves as its `view_id`. This feature organizes viewing and feedback across cameras; the same Codex task performs geometry changes.

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

See [reference_multiview.example.json](examples/reference_multiview.example.json) for a batch manifest. It may have a top-level `name`; each entry in `views` may specify `name`, `fps`, and `duration_sec`, and exactly one source: `frames`, `manifest_path`, or `video_path`. A frame supports `path` and optional `time_sec`, `name`, and `camera`; a video may supply `camera_manifest_path`. Relative paths use the outer manifest directory, while a nested manifest’s frame paths use its own directory. The first entry becomes the primary view; `append_view=True` appends the whole batch to an existing group. Replace the example camera values with actual calibration.

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
workspace_set_reference_clip(manifest_path="/absolute/path/to/project/reference_multiview.json", append_view=True)
workspace_set_reference_clip(video_path="/absolute/path/to/project/camera_B.mp4", fps=10, camera_manifest_path="/absolute/path/to/project/video-cameras.json", append_view=True, view_name="camera_B")
```

## Human results: generated by the capsule skill, reviewed in the workbench

The separate [capsule-human-tracking skill](external-skills/capsule-human-tracking/SKILL.md) runs human tracking and reconstruction. The `人体结果` pane displays, imports and cites 2D evidence. The workbench plugin does not run ViTPose or install inference packages or checkpoints.

A reconstruction task calls `workspace_export_pose_sources` to snapshot this project's reference frames, uses the skill to process actual images or validate existing 2D tracks, then returns the result with `workspace_import_human_pose`. The pane also accepts the corresponding JSON. The snapshot binds the project, session, view, frame, time and image; results from another project or replaced references are rejected. A geometry revision alone does not invalidate unchanged reference images.

Camera and timeline changes display the matching source-frame skeleton. Results can declare their joint names and edges; previously completed COCO17 results remain readable. Source labels distinguish observations, inferred estimates and projections of an existing 3D model. Projections are not measured 2D tracks.

Use `引用人体` to insert `[[pose:JOB_ID:REFERENCE_ID]]` in the shared prompt. Feedback includes original images, skeleton overlays, confidence, view, frame, time and available camera metadata, with up to eight samples. Saved evidence remains available; successful feedback clears the current citations. Cross-view identity, triangulation and 3D fitting belong to the reconstruction skill and require explicit calibration and topology.

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

The workbench connects to the running **Codex Desktop App Server on the same host and under the same user**. Each submission becomes a new user turn in that existing task, with your text, original and annotated images, and scene snapshots; no new task is created. Feedback waits in a queue while the task is busy. The page shows queued, running, and completed delivery states. A scene revision change while feedback waits does not block saved feedback; the model receives the snapshot revision and the current revision at delivery. Human approval and interaction requests appear in the workbench; it never approves them automatically. If a disconnect makes delivery uncertain, inspect the original task history before retrying to avoid duplicates. This mode uses `websocket-client`, installed through `backend/requirements.txt`. In bound mode, `request_visual_feedback` opens or updates the workbench and returns its URL; **no MCP result needs to remain pending**.

Older `blocked_stale` feedback safely returns to the queue and delivers automatically if it has saved snapshot evidence and no Codex turn has started. Its evidence and revision remain unchanged. Feedback known not to have been sent stays queued while the connection is unavailable or the original task is busy, then retries automatically. A definite send failure has a manual Retry button. If delivery is uncertain, the workbench checks the original task history by feedback ID. An unresolved item is isolated so later feedback can proceed, but it is **never resent automatically**. Check the original task first; use “Confirm not received, retry” only when the feedback is absent.

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
| `workspace_export_pose_sources` | Export a source snapshot of every current view and reference frame for the external capsule skill |
| `workspace_import_human_pose` | Validate and import 2D human results bound to that snapshot; no inference is started |
| `workspace_get_human_pose` | Read imported or historical completed results by view, source frame or page |
| `workspace_publish_scene` | Validate and publish a new GLB, check the expected revision, and notify the page to refresh |
| `workspace_set_reference_clip` | Import a single-view or multi-view manifest, or sample a project-local video with FFmpeg; `append_view=True` appends views |
| `workspace_request_feedback` | Default mode: ask the user to inspect something on the page and return immediately; their reply becomes the next user message |
| `request_visual_feedback` / `wait_visual_feedback` | Unbound external MCP mode: wait and return text and images as a tool result; bound desktop mode: the former returns the page URL and submission starts a new turn |
| `get_visual_feedback` | External MCP mode: reread submitted visual feedback |

The repository's [Visual Reconstruction Skill](.agents/skills/visual-reconstruction-feedback/SKILL.md) reminds Codex to distinguish original images from annotations, edit project source files, and publish a GLB when the result is ready. Default mode sends a new turn directly. Unbound external MCP mode returns feedback through a waiting `request_visual_feedback` or `wait_visual_feedback` call. Bound desktop mode starts a new turn after browser submission.

## Verification and limits

```bash
.venv/bin/python -m unittest discover -s backend/tests -v
```

The floating-dock browser regression uses temporary data and an ephemeral loopback port, with Codex disabled and no access to real sessions. Install the optional Playwright and Chromium dependencies to run it:

```bash
.venv/bin/python -m pip install playwright
.venv/bin/python -m playwright install chromium
.venv/bin/python tests/focus_workspace_smoke.py
```

It covers independent collapse controls, unread messages, replay, restoration of unsent drafts and frozen evidence, reference insertion, duplicate-send protection, the timeline, and narrow screens.

The real App Server integration was tested with two different local images: Codex recognized a red image in the first turn, then recognized a blue image in the **same thread** after stdio was closed and restarted. The `thread/read` history contains `localImage` items for both turns. This verifies that the images reached the model, rather than merely sending their file paths.

An end-to-end Selenium run also exercised two browser → Codex turns in the same thread. In the first turn, numbered rectangles were drawn on the reference image and scene and submitted from the browser. Codex received five actual image inputs, edited the demo's `scene.json`, generated a GLB, and successfully published through the project-scoped `workspace_publish_scene` MCP tool twice. Approval was handled on the page; the workbench refreshed as the scene advanced from revision 2 to 3 and then 4. The cabinet's final center was `x=-0.8`. In the second turn, another line was drawn on the reference image and submitted from the page. Codex received five more `input_image` items in the same thread and replied with a confirmation. It did not edit files or publish another scene, so the final revision remained 4.

By default the service listens only on `127.0.0.1`. With the LAN flags above, the page requires an access link and cookie, and submissions still require the browser capability. Codex turns in the `workspace-write` sandbox enable `networkAccess: true` to reach the local MCP Gateway. Users decide approval requests on the page; the workbench does not approve them automatically. Runtime data and demo output are excluded from Git.

Project code is licensed under the [MIT License](LICENSE). The bundled Three.js files retain their [original MIT license](web/vendor/three/LICENSE).

---
name: visual-reconstruction-feedback
description: Request human visual review from an existing Codex task or respond to feedback in the workbench-owned task, then publish updated GLB scenes.
---

# Visual reconstruction feedback

The workbench sends the user's note with original and marked reference images, clean and marked scene snapshots, the selected object, camera, and scene revision. Treat marks as the user's visual instructions, not as geometry already present in the reference image. A selected object or numbered correspondence is a useful clue, not a required constraint.

Inspect the supplied images and the project source. Use the existing reconstruction or editing tools appropriate to the user's request. Keep scene edits in editable source files; export a self-contained GLB when the result is ready. Call `workspace_publish_scene` with the GLB path and the expected current revision so the workbench displays the result. If the revision changed, inspect the new context before publishing again.

Use `workspace_get_context` or `workspace_get_feedback` when the current packet lacks needed project or prior-feedback detail.

## Choose the review path for the current task

- **Existing Codex desktop task using `--external-review`:** When the user says “进入人工调试模式” or “进入人工参与调试模式” for this reconstruction project, call `request_visual_feedback` with project-local reference image paths and the current project-local GLB when available. It normally opens the browser and waits, returning the user's note and real original/annotated image blocks to this same task. If it returns only a URL, `session_id`, and `next_cursor`, share the URL and call `wait_visual_feedback` with that session and cursor. If waiting times out, keep the session and cursor and call again when needed; do not create a new Codex turn. Use `get_visual_feedback` to reread submitted packets. Keep each tool's `timeout_sec` at 600 or less with the MCP server's `tool_timeout_sec` set higher. Process the returned images and continue the project edit in this task.
- **Workbench-owned App Server task (default mode):** `workspace_request_feedback` asks the user to review a result and returns immediately. The reply arrives as a later user turn in the workbench's project thread. Do not wait inside the tool for that reply.

The trigger phrases above apply to this project's visual reconstruction workflow. They are not a general instruction to enter review mode for unrelated tasks.

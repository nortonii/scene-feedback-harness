---
name: visual-reconstruction-feedback
description: Respond to visual feedback submitted through this repository's reconstruction workbench, and publish updated GLB scenes back to it.
---

# Visual reconstruction feedback

The workbench sends the user's note with original and marked reference images, clean and marked scene snapshots, the selected object, camera, and scene revision. Treat marks as the user's visual instructions, not as geometry already present in the reference image. A selected object or numbered correspondence is a useful clue, not a required constraint.

Inspect the supplied images and the project source. Use the existing reconstruction or editing tools appropriate to the user's request. Keep scene edits in editable source files; export a self-contained GLB when the result is ready. Call `workspace_publish_scene` with the GLB path and the expected current revision so the workbench displays the result. If the revision changed, inspect the new context before publishing again.

Use `workspace_get_context` or `workspace_get_feedback` when the current packet lacks needed project or prior-feedback detail. `workspace_request_feedback` can ask the user to review a result; it returns immediately, and the user's reply arrives as a later user turn in this same project thread. Do not wait inside the tool for that reply.

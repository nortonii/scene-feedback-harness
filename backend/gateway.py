"""Durable browser-to-Codex delivery for one local reconstruction workspace."""

from __future__ import annotations

import copy
import json
import logging
import threading
from pathlib import Path
from typing import Any

from core import APIError, SceneStore, _now


class WorkspaceGateway:
    def __init__(self, store: SceneStore, project_dir: str | Path, *, adapter: Any = None):
        self.store = store
        self.project_dir = Path(project_dir).expanduser().resolve()
        self.adapter = adapter
        self._worker_lock = threading.Lock()
        self._worker_running = False
        self._started = False

    def ensure(self, preferred_session_id: str | None = None) -> dict[str, Any]:
        return self.store.ensure_workspace(self.project_dir, preferred_session_id=preferred_session_id)

    def state(self, *, include_capability: bool = False, preferred_session_id: str | None = None) -> dict[str, Any]:
        self.ensure(preferred_session_id)
        result = self.store.workspace()
        result.pop("events", None)
        result["events_cursor"] = result.pop("event_seq")
        for approval in result.get("approvals", []):
            approval.pop("request_id", None)
        if include_capability:
            result["browser_capability"] = self.store.browser_token
        return result

    def start(self) -> None:
        self.ensure()
        self._started = True
        if self.adapter is None:
            self.store.workspace_agent(status="disconnected", error="Codex App Server is not configured")
            return
        try:
            thread_id = self.adapter.start()
            self.store.workspace_thread(thread_id)
            with self.store.lock:
                workspace = self.store.state["workspace"]
                active_id = workspace.get("active_feedback_id")
                workspace["approvals"] = []
                if active_id:
                    item = next((entry for entry in workspace["queue"] if entry["feedback_id"] == active_id), None)
                    if item and item["status"] in {"dispatching", "running"}:
                        item["status"] = "delivery_uncertain"
                        item["error"] = "Gateway restarted during a turn; inspect the thread before retrying."
                        workspace["agent"] = {"status": "delivery_uncertain", "turn_id": item.get("turn_id"), "error": item["error"]}
                        self.store._save()
                        self.store.workspace_event("delivery_uncertain", {"feedback_id": active_id, "message": item["error"]})
                else:
                    workspace["agent"] = {"status": "idle", "turn_id": None, "error": None}
                    self.store._save()
            self.wake()
        except Exception as exc:
            logging.exception("Could not connect local Codex App Server")
            self.store.workspace_agent(status="disconnected", error=str(exc)[:500])
            self.store.workspace_event("disconnected", {"message": str(exc)[:500]})

    def close(self) -> None:
        if self.adapter is not None:
            self.adapter.close()

    def submit(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        workspace = self.ensure(preferred_session_id=session_id)
        if session_id != workspace["session_id"]:
            raise APIError(409, "feedback belongs to another session; use the workspace session")
        if not payload.get("idempotency_key"):
            raise APIError(400, "idempotency_key is required for direct Codex delivery")
        feedback = self.store.submit_feedback(session_id, payload)
        self.wake()
        with self.store.lock:
            item = next(entry for entry in self.store.state["workspace"]["queue"] if entry["feedback_id"] == feedback["feedback_id"])
            result = copy.deepcopy(feedback)
            result["delivery"] = copy.deepcopy(item)
            return result

    def confirm_queue(self, feedback_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("retry_uncertain") is True:
            if self.adapter is None:
                raise APIError(503, "Codex App Server is unavailable")
            try:
                status = self.adapter.refresh()
            except Exception as exc:
                raise APIError(503, f"cannot reconcile Codex thread before retry: {exc}") from exc
            if status.get("turn_state") != "idle":
                raise APIError(409, "Codex turn is still active or uncertain; inspect the thread before retrying")
        with self.store.lock:
            workspace = self.store.state["workspace"]
            item = next((entry for entry in workspace["queue"] if entry["feedback_id"] == feedback_id), None)
            if item is None:
                raise APIError(404, "queued feedback not found")
            if item["status"] == "blocked_stale":
                if payload.get("confirm") is True:
                    item["status"] = "queued"
                    item["confirmed_against_revision"] = self.store.state["scene"]["revision"]
                    kind = "stale_feedback_confirmed"
                elif payload.get("confirm") is False:
                    item["status"] = "discarded"
                    kind = "queued_feedback_discarded"
                else:
                    raise APIError(400, "confirm must be true or false")
            elif item["status"] == "delivery_uncertain":
                if payload.get("retry_uncertain") is True:
                    item["status"] = "queued"
                    workspace["active_feedback_id"] = None
                    workspace["agent"] = {"status": "idle", "turn_id": None, "error": None}
                    kind = "uncertain_feedback_retry_requested"
                elif payload.get("retry_uncertain") is False:
                    item["status"] = "discarded"
                    workspace["active_feedback_id"] = None
                    workspace["agent"] = {"status": "idle", "turn_id": None, "error": None}
                    kind = "uncertain_feedback_discarded"
                else:
                    raise APIError(400, "retry_uncertain must be true or false")
            else:
                raise APIError(409, "feedback is not awaiting confirmation")
            self.store._save()
            self.store.workspace_event(kind, {"feedback_id": feedback_id})
            result = copy.deepcopy(item)
        self.wake()
        return result

    def wake(self) -> None:
        if not self._started or self.adapter is None:
            return
        with self._worker_lock:
            if self._worker_running:
                return
            self._worker_running = True
        threading.Thread(target=self._dispatch, daemon=True, name="workspace-dispatch").start()

    def _dispatch(self) -> None:
        try:
            if not self.adapter.status().get("connected"):
                try:
                    thread_id = self.adapter.start()
                    self.store.workspace_thread(thread_id)
                    with self.store.lock:
                        if not self.store.state["workspace"].get("active_feedback_id"):
                            self.store.state["workspace"]["agent"] = {"status": "idle", "turn_id": None, "error": None}
                            self.store._save()
                except Exception as exc:
                    self.store.workspace_agent(status="disconnected", error=str(exc)[:500])
                    self.store.workspace_event("disconnected", {"message": str(exc)[:500]})
                    return
            with self.store.lock:
                workspace = self.store.state["workspace"]
                if workspace.get("active_feedback_id"):
                    return
                item = next((entry for entry in workspace["queue"] if entry["status"] in {"queued", "blocked_stale", "delivery_uncertain"}), None)
                if item is None or item["status"] != "queued":
                    return
                revision = self.store.state["scene"]["revision"]
                if item["scene_revision"] != revision and item.get("confirmed_against_revision") != revision:
                    item["status"] = "blocked_stale"
                    self.store._save()
                    self.store.workspace_event("stale_feedback_confirmation_required", {"feedback_id": item["feedback_id"], "captured_revision": item["scene_revision"], "current_revision": revision})
                    return
                item["status"] = "dispatching"
                workspace["active_feedback_id"] = item["feedback_id"]
                workspace["agent"] = {"status": "running", "turn_id": None, "error": None}
                self.store._save()
                feedback = next(entry for entry in self.store.state["feedback"] if entry["feedback_id"] == item["feedback_id"])
                feedback = copy.deepcopy(feedback)
                if item["scene_revision"] != revision:
                    feedback["submitted_from_stale_snapshot"] = True
            text, image_paths = self._turn_input(feedback)
            response = self.adapter.start_turn(text, image_paths, message_id=feedback["feedback_id"])
            with self.store.lock:
                workspace = self.store.state["workspace"]
                current = next(entry for entry in workspace["queue"] if entry["feedback_id"] == feedback["feedback_id"])
                if current["status"] == "dispatching":
                    current["status"] = "running"
                    current["turn_id"] = response.get("turn_id")
                    workspace["agent"] = {"status": "running", "turn_id": response.get("turn_id"), "error": None}
                    self.store._save()
                    self.store.workspace_event("turn_started", {"feedback_id": feedback["feedback_id"], "turn_id": response.get("turn_id")})
        except Exception as exc:
            error = str(exc)[:500]
            uncertain = exc.__class__.__name__ in {"UncertainDeliveryError", "TurnBusyError"}
            with self.store.lock:
                workspace = self.store.state["workspace"]
                active_id = workspace.get("active_feedback_id")
                item = next((entry for entry in workspace["queue"] if entry["feedback_id"] == active_id), None)
                if item:
                    item["status"] = "delivery_uncertain" if uncertain else "failed"
                    item["error"] = error
                workspace["active_feedback_id"] = active_id if uncertain else None
                workspace["agent"] = {"status": "delivery_uncertain" if uncertain else "error", "turn_id": None, "error": error}
                self.store._save()
                self.store.workspace_event("delivery_uncertain" if uncertain else "turn_failed", {"feedback_id": active_id, "message": error})
            logging.exception("Could not deliver workspace feedback")
        finally:
            with self._worker_lock:
                self._worker_running = False
            with self.store.lock:
                workspace = self.store.state.get("workspace", {})
                pending = not workspace.get("active_feedback_id") and any(item["status"] == "queued" for item in workspace.get("queue", []))
            if pending and self.adapter.status().get("connected"):
                self.wake()

    def _turn_input(self, feedback: dict[str, Any]) -> tuple[str, list[str]]:
        fallback = "（仅提供参考图，请据图开始或继续重建。）" if feedback.get("reference_images") and not feedback.get("annotations") else "（仅有视觉标记）"
        lines = ["用户通过 Visual Reconstruction Workspace 发送视觉反馈。", "项目根目录：" + str(self.project_dir), "用户原话：", feedback.get("note", "") or fallback, "", f"场景版本：{feedback['scene_revision']}", f"反馈 ID：{feedback['feedback_id']}"]
        if feedback.get("submitted_from_stale_snapshot"):
            lines.append("这份反馈针对较早的冻结场景截图。用户已确认继续发送；请依据截图和版本判断，不要把旧标记当成当前视角坐标。")
        if feedback.get("selected_object_ids"):
            lines.append("选中对象 ID：" + ", ".join(feedback["selected_object_ids"]))
        if feedback.get("selected_scene_nodes"):
            lines.append("选中 GLB 节点：" + json.dumps(feedback["selected_scene_nodes"], ensure_ascii=False))
        if feedback.get("camera"):
            lines.append("冻结视角：" + json.dumps(feedback["camera"], ensure_ascii=False))
        if feedback.get("annotations"):
            lines.append("标记数据：" + json.dumps(feedback["annotations"], ensure_ascii=False))
        lines += ["", "附件顺序："]
        image_paths: list[str] = []

        def add(label: str, url: str) -> None:
            name = url.rsplit("/", 1)[-1]
            if not url.startswith("/media/") or not name or "/" in name:
                raise APIError(500, "stored image URL is invalid")
            path = (self.store.media_dir / name).resolve()
            if path.parent != self.store.media_dir or not path.is_file():
                raise APIError(500, "stored image is missing")
            image_paths.append(str(path))
            lines.append(f"{len(image_paths)}. {label}")

        annotated = {entry["reference_id"]: entry["url"] for entry in feedback.get("reference_annotated_images", [])}
        for reference in feedback.get("reference_images", []):
            add(f"参考原图 {reference['name']} (ID {reference['id']})", reference["url"])
            if reference["id"] in annotated:
                add(f"带用户标记的参考图 {reference['name']} (ID {reference['id']})", annotated[reference["id"]])
        for key, label in (("scene_original_url", "冻结视角的干净场景截图"), ("scene_annotated_url", "带用户标记和高亮的场景截图")):
            if feedback.get(key):
                add(label, feedback[key])
        for crop in feedback.get("crops", []):
            add(f"{crop['source']} 局部放大图", crop["url"])
        lines += ["", "红线、箭头、编号、框和画笔痕迹是用户后画的提示，不是参考图中的真实几何。请结合图像和原话继续当前重建任务；修改完成后调用 workspace_publish_scene 发布新的 GLB。"]
        return "\n".join(lines), image_paths

    def on_adapter_event(self, event: dict[str, Any]) -> None:
        method = event.get("method", "")
        params = event.get("params") or {}
        try:
            if method == "adapter/request_pending":
                approval_id = __import__("uuid").uuid4().hex
                details = params.get("params", {})
                if not isinstance(details, dict):
                    details = {}
                encoded = json.dumps(details, ensure_ascii=False, allow_nan=False)
                too_large = len(encoded.encode("utf-8")) > 64 * 1024
                questions = details.get("questions")
                question_ids = None
                if isinstance(questions, list) and len(questions) <= 100 and all(isinstance(question, dict) and isinstance(question.get("id"), str) and question["id"] for question in questions):
                    candidate_ids = [question["id"] for question in questions]
                    if len(json.dumps(candidate_ids, ensure_ascii=False).encode("utf-8")) <= 4096:
                        question_ids = candidate_ids
                request = {
                    "approval_id": approval_id,
                    "request_id": params.get("request_id"),
                    "kind": params.get("method"),
                    "prompt": encoded[:4000],
                    "details": None if too_large else copy.deepcopy(details),
                    "details_truncated": too_large,
                    "question_ids": question_ids,
                    "at": _now(),
                }
                with self.store.lock:
                    workspace = self.store.state["workspace"]
                    workspace["approvals"].append(request)
                    workspace["agent"]["status"] = "awaiting_approval"
                    self.store._save()
                self.store.workspace_event("approval_requested", {"approval_id": approval_id, "kind": request["kind"], "prompt": request["prompt"], "details_truncated": too_large})
            elif method == "turn/completed":
                turn = params.get("turn") or {}
                turn_id = turn.get("id")
                outcome = turn.get("status", "completed")
                with self.store.lock:
                    workspace = self.store.state["workspace"]
                    active_id = workspace.get("active_feedback_id")
                    item = next((entry for entry in workspace["queue"] if entry["feedback_id"] == active_id), None)
                    known_turn_id = item.get("turn_id") if item else None
                    if known_turn_id and turn_id and known_turn_id != turn_id:
                        self.store.workspace_event("codex_event", {"method": method, "turn_id": turn_id, "status": outcome, "message": "completion did not match the active turn"})
                        return
                    if item:
                        item["status"] = outcome if outcome in {"completed", "failed", "interrupted"} else "failed"
                        item["turn_id"] = turn_id or item.get("turn_id")
                    workspace["active_feedback_id"] = None
                    workspace["approvals"] = []
                    workspace["agent"] = {"status": "idle", "turn_id": None, "error": None}
                    self.store._save()
                self.store.workspace_event("turn_completed" if outcome == "completed" else "turn_failed", {"feedback_id": active_id, "turn_id": turn_id, "status": outcome})
                self.wake()
            elif method == "turn/started":
                turn = params.get("turn") or {}
                with self.store.lock:
                    workspace = self.store.state["workspace"]
                    active_id = workspace.get("active_feedback_id")
                    item = next((entry for entry in workspace["queue"] if entry["feedback_id"] == active_id), None)
                    if item and item["status"] == "dispatching" and turn.get("id"):
                        item["turn_id"] = turn["id"]
                        workspace["agent"]["turn_id"] = turn["id"]
                        self.store._save()
                self.store.workspace_event("codex_event", {"method": method, "turn_id": turn.get("id")})
            elif method == "adapter/disconnected":
                with self.store.lock:
                    workspace = self.store.state["workspace"]
                    active_id = workspace.get("active_feedback_id")
                    if active_id:
                        item = next((entry for entry in workspace["queue"] if entry["feedback_id"] == active_id), None)
                        if item and item["status"] in {"dispatching", "running"}:
                            item["status"] = "delivery_uncertain"
                            item["error"] = "Codex App Server disconnected during a turn; inspect the thread before retrying."
                        workspace["agent"] = {"status": "delivery_uncertain", "turn_id": item.get("turn_id") if item else None, "error": item.get("error") if item else None}
                    else:
                        workspace["agent"] = {"status": "disconnected", "turn_id": None, "error": "Codex App Server disconnected"}
                    workspace["approvals"] = []
                    self.store._save()
                self.store.workspace_event("delivery_uncertain" if active_id else "disconnected", {"feedback_id": active_id, "message": "Codex App Server disconnected"})
            elif method == "adapter/reconnected":
                thread_id = params.get("thread_id")
                if thread_id:
                    self.store.workspace_thread(thread_id)
                with self.store.lock:
                    active_id = self.store.state["workspace"].get("active_feedback_id")
                if not active_id:
                    self.store.workspace_agent(status="idle")
                self.store.workspace_event("reconnected", {})
                if not active_id:
                    self.wake()
            elif method == "adapter/empty_thread_recreated":
                thread_id = params.get("thread_id")
                if thread_id:
                    self.store.workspace_thread(thread_id)
            elif method == "item/completed":
                item = params.get("item") if isinstance(params, dict) else None
                if isinstance(item, dict) and item.get("type") == "agentMessage":
                    message = item.get("text")
                    if isinstance(message, str) and message:
                        self.store.workspace_event("assistant_message", {"text": message[:30_000]})
                else:
                    summary = {"method": method, "item_type": item.get("type") if isinstance(item, dict) else None}
                    if isinstance(item, dict) and isinstance(item.get("command"), str):
                        summary["command"] = item["command"][:1000]
                    self.store.workspace_event("codex_event", summary)
            elif method == "error":
                self.store.workspace_event("codex_event", {"method": method, "preview": json.dumps(params, ensure_ascii=False)[:4000]})
        except Exception:
            logging.exception("Could not process Codex App Server event")

    def respond_to_approval(self, approval_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.adapter is None:
            raise APIError(503, "Codex App Server is unavailable")
        if not isinstance(payload, dict):
            raise APIError(400, "approval response must be a JSON object")
        with self.store.lock:
            request = next((entry for entry in self.store.state["workspace"]["approvals"] if entry["approval_id"] == approval_id), None)
            if request is None:
                raise APIError(404, "approval request not found")
            request = copy.deepcopy(request)
        decision = payload.get("decision")
        kind = request["kind"]
        details = request.get("details")
        if details is None and not request.get("details_truncated"):
            try:
                details = json.loads(request.get("prompt", ""))
            except (TypeError, ValueError):
                details = {}
        if not isinstance(details, dict):
            details = {}
        supported_kinds = {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
            "mcpServer/elicitation/request",
            "item/permissions/requestApproval",
            "item/tool/requestUserInput",
        }
        if request.get("details_truncated") and kind not in supported_kinds:
            raise APIError(422, "unsupported request has oversized details; interrupt this turn from the workbench")
        if request.get("details_truncated") and decision not in {"decline", "cancel"}:
            raise APIError(413, "approval details are too large to inspect in the workbench; accepting is unavailable")
        if kind in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
            if decision not in {"accept", "acceptForSession", "decline", "cancel"}:
                raise APIError(400, "decision must be accept, acceptForSession, decline or cancel")
            result = {"decision": decision}
        elif kind == "mcpServer/elicitation/request":
            if decision not in {"accept", "decline", "cancel"}:
                raise APIError(400, "decision must be accept, decline or cancel")
            result = {"action": decision}
            if decision == "accept":
                mode = details.get("mode")
                if mode in {"url", "openai/userVerification"}:
                    if mode == "url" and (not isinstance(details.get("url"), str) or not details["url"]):
                        raise APIError(400, "URL elicitation has no usable URL")
                    if "content" in payload:
                        raise APIError(400, "URL elicitation accept must not include form content")
                elif mode in {"form", "openai/form", "openaiForm"}:
                    schema = details.get("requestedSchema")
                    if not isinstance(schema, dict):
                        raise APIError(400, "form elicitation has no requested schema")
                    content = payload.get("content")
                    if content is None and schema.get("type") == "object" and not schema.get("properties") and not schema.get("required"):
                        content = {}
                    if not isinstance(content, dict):
                        raise APIError(400, "accepted elicitation needs form content")
                    self._validate_form_content(schema, content, strict=mode == "form")
                    result["content"] = copy.deepcopy(content)
                else:
                    raise APIError(400, "unsupported elicitation mode")
        elif kind == "item/permissions/requestApproval":
            if decision not in {"accept", "decline", "cancel"}:
                raise APIError(400, "permission decision must be accept, decline or cancel")
            if decision == "accept":
                requested = details.get("permissions")
                if not isinstance(requested, dict):
                    raise APIError(400, "permission request has no profile to inspect")
                submitted = payload.get("permissions", requested)
                if submitted != requested:
                    raise APIError(400, "granted permissions must exactly match the inspected request")
                scope = payload.get("scope", "turn")
                if scope not in {"turn", "session"}:
                    raise APIError(400, "permission scope must be turn or session")
                result = {"permissions": copy.deepcopy(requested), "scope": scope}
            else:
                # The 0.156.1 response has no decline flag. An empty granted
                # profile grants none of the requested extra permissions.
                result = {"permissions": {}, "scope": "turn"}
        elif kind == "item/tool/requestUserInput":
            if decision not in {"accept", "decline", "cancel"}:
                raise APIError(400, "user-input decision must be accept, decline or cancel")
            questions = details.get("questions")
            if isinstance(questions, list) and all(isinstance(question, dict) and isinstance(question.get("id"), str) and question["id"] for question in questions):
                question_ids = [question["id"] for question in questions]
            elif request.get("details_truncated") and decision in {"decline", "cancel"}:
                question_ids = request.get("question_ids")
                if not isinstance(question_ids, list):
                    raise APIError(422, "cannot safely answer oversized user-input request; interrupt this turn from the workbench")
            else:
                raise APIError(400, "user-input request has no valid questions")
            if len(question_ids) != len(set(question_ids)):
                raise APIError(400, "user-input request has duplicate question IDs")
            if decision == "accept":
                answers = payload.get("answers")
                if not isinstance(answers, dict) or set(answers) != set(question_ids):
                    raise APIError(400, "answers must match every requested question ID")
                for question_id, answer in answers.items():
                    if not isinstance(answer, dict) or set(answer) != {"answers"} or not isinstance(answer["answers"], list) or not answer["answers"]:
                        raise APIError(400, f"question {question_id!r} needs answers")
                    if len(answer["answers"]) > 10 or any(not isinstance(value, str) or not 1 <= len(value) <= 4000 for value in answer["answers"]):
                        raise APIError(400, "user-input answers must be short text")
                result = {"answers": copy.deepcopy(answers)}
            else:
                result = {"answers": {question_id: {"answers": []} for question_id in question_ids}}
        else:
            result = payload.get("result")
            if not isinstance(result, dict):
                raise APIError(422, "unsupported request needs a structured result; interrupt this turn from the workbench")
        try:
            encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise APIError(400, "approval response contains invalid JSON values") from exc
        if len(encoded.encode("utf-8")) > 64 * 1024:
            raise APIError(413, "approval response is too large")
        try:
            self.adapter.respond_to_request(request["request_id"], result)
        except ValueError as exc:
            raise APIError(400, str(exc)) from exc
        with self.store.lock:
            workspace = self.store.state["workspace"]
            workspace["approvals"] = [entry for entry in workspace["approvals"] if entry["approval_id"] != approval_id]
            workspace["agent"]["status"] = "running"
            self.store._save()
        self.store.workspace_event("approval_resolved", {"approval_id": approval_id, "decision": decision or "structured"})
        return {"approval_id": approval_id, "status": "responded"}

    @staticmethod
    def _validate_form_content(schema: dict[str, Any], content: dict[str, Any], *, strict: bool) -> None:
        required = schema.get("required") or []
        if not isinstance(required, list) or any(not isinstance(key, str) for key in required):
            raise APIError(400, "elicitation schema has invalid required fields")
        if any(key not in content for key in required):
            raise APIError(400, "form content is missing a required field")
        if not strict:
            return
        properties = schema.get("properties") or {}
        if not isinstance(properties, dict) or any(key not in properties for key in content):
            raise APIError(400, "form content contains an unrequested field")
        for key, value in content.items():
            field = properties[key]
            if not isinstance(field, dict):
                raise APIError(400, "elicitation schema has an invalid field")
            kind = field.get("type")
            if kind == "string" and not isinstance(value, str):
                raise APIError(400, f"form field {key!r} must be text")
            if kind == "boolean" and not isinstance(value, bool):
                raise APIError(400, f"form field {key!r} must be true or false")
            if kind in {"integer", "number"} and (isinstance(value, bool) or not isinstance(value, (int, float)) or (kind == "integer" and not isinstance(value, int))):
                raise APIError(400, f"form field {key!r} must be a {kind}")
            if kind == "array" and (not isinstance(value, list) or any(not isinstance(item, str) for item in value)):
                raise APIError(400, f"form field {key!r} must be a list of text values")
            if "enum" in field and value not in field["enum"]:
                raise APIError(400, f"form field {key!r} is not an offered option")
            if kind == "string" and isinstance(value, str):
                if isinstance(field.get("minLength"), int) and len(value) < field["minLength"]:
                    raise APIError(400, f"form field {key!r} is too short")
                if isinstance(field.get("maxLength"), int) and len(value) > field["maxLength"]:
                    raise APIError(400, f"form field {key!r} is too long")
                choices = field.get("oneOf")
                if isinstance(choices, list) and value not in [option.get("const") for option in choices if isinstance(option, dict)]:
                    raise APIError(400, f"form field {key!r} is not an offered option")
            if kind in {"integer", "number"} and isinstance(value, (int, float)) and not isinstance(value, bool):
                minimum = field.get("minimum")
                maximum = field.get("maximum")
                if isinstance(minimum, (int, float)) and not isinstance(minimum, bool) and value < minimum:
                    raise APIError(400, f"form field {key!r} violates minimum")
                if isinstance(maximum, (int, float)) and not isinstance(maximum, bool) and value > maximum:
                    raise APIError(400, f"form field {key!r} violates maximum")
            if kind == "array" and isinstance(value, list):
                if isinstance(field.get("minItems"), int) and len(value) < field["minItems"]:
                    raise APIError(400, f"form field {key!r} has too few choices")
                if isinstance(field.get("maxItems"), int) and len(value) > field["maxItems"]:
                    raise APIError(400, f"form field {key!r} has too many choices")
                item_schema = field.get("items")
                if isinstance(item_schema, dict):
                    choices = item_schema.get("enum")
                    if not isinstance(choices, list) and isinstance(item_schema.get("anyOf"), list):
                        choices = [option.get("const") for option in item_schema["anyOf"] if isinstance(option, dict)]
                    if isinstance(choices, list) and any(item not in choices for item in value):
                        raise APIError(400, f"form field {key!r} contains an unoffered choice")

    def interrupt(self) -> dict[str, Any]:
        if self.adapter is None:
            raise APIError(503, "Codex App Server is unavailable")
        with self.store.lock:
            active_id = self.store.state["workspace"].get("active_feedback_id")
            turn_id = self.store.state["workspace"]["agent"].get("turn_id")
        if not active_id:
            raise APIError(409, "there is no active Codex turn")
        response = self.adapter.interrupt(turn_id)
        self.store.workspace_event("interrupt_requested", {"feedback_id": active_id, "turn_id": turn_id})
        return response

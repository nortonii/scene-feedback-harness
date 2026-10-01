"""Durable MCP Events subscriptions and Standard Webhooks feedback delivery.

Webhook receipt acknowledges an event, not execution of a model turn. The
immutable feedback packet remains in SceneStore and is read with MCP tools.
"""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import hmac
import http.client
import ipaddress
import json
import logging
import math
import os
import queue
import re
import secrets
import socket
import ssl
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from core import APIError


EVENT_NAME = "visual_feedback.submitted"
MAX_EVENT_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 16 * 1024
DEFAULT_TTL_SEC = 24 * 3600
MAX_TTL_SEC = 7 * 24 * 3600
VERIFICATION_CACHE_SEC = 300
ROTATION_WINDOW_SEC = 300
WEBHOOK_TIMEOUT_SEC = 10
MAX_ATTEMPTS = 8
ID_RE = re.compile(r"^[0-9a-f]{32}$")
logger = logging.getLogger(__name__)


class MCPEventsError(APIError):
    """A JSON-RPC event error that can also be used by the HTTP gateway."""

    def __init__(self, message: str, *, code: int = -32602, reason: str = "invalid_params", status: int = 400):
        self.code = code
        self.data = {"reason": reason}
        super().__init__(status, message, detail={"jsonrpc_code": code, **self.data})


class _WebhookFailure(Exception):
    def __init__(self, reason: str, *, transient: bool = False):
        self.reason = reason
        self.transient = transient
        super().__init__(reason)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _secret_bytes(value: Any) -> bytes:
    if not isinstance(value, str) or not value.startswith("whsec_") or len(value) > 100:
        raise MCPEventsError("A Standard Webhooks signing secret is required.", reason="invalid_secret")
    try:
        decoded = base64.b64decode(value[6:], validate=True)
    except (ValueError, binascii.Error):
        raise MCPEventsError("Invalid webhook signing secret.", reason="invalid_secret") from None
    if not 24 <= len(decoded) <= 64:
        raise MCPEventsError("Webhook signing key must contain 24 to 64 bytes.", reason="invalid_secret")
    return decoded


def _public_ip(address: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        value = ipaddress.ip_address(address)
    except ValueError:
        raise _WebhookFailure("unsafe_destination") from None
    if (not value.is_global or value.is_multicast or value.is_reserved or value.is_unspecified
            or value.is_loopback or value.is_link_local or getattr(value, "is_site_local", False)
            or isinstance(value, ipaddress.IPv6Address) and value.ipv4_mapped is not None):
        raise _WebhookFailure("unsafe_destination")
    return value


def _callback_url(value: Any) -> str:
    """Validate URL syntax without resolving DNS (resolved at every connection)."""
    if (not isinstance(value, str) or not value or len(value) > 8192
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)):
        raise MCPEventsError("Invalid webhook callback URL.", reason="unsafe_destination")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.fragment):
            raise ValueError()
        port = parsed.port or 443
        if not 1 <= port <= 65535:
            raise ValueError()
        hostname = parsed.hostname.encode("idna").decode("ascii").lower()
        if not hostname or "%" in hostname or "\\" in hostname:
            raise ValueError()
        try:
            literal = ipaddress.ip_address(hostname)
        except ValueError:
            # Restrict hostnames to conventional DNS labels; this also rejects
            # unusual numeric/URL spellings before the resolver sees them.
            if (not re.fullmatch(r"[a-z0-9.-]+", hostname) or len(hostname) > 253
                    or any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
                           for label in hostname.rstrip(".").split("."))):
                raise ValueError()
        else:
            _public_ip(str(literal))
    except (ValueError, UnicodeError, _WebhookFailure):
        raise MCPEventsError("Callback must be a public HTTPS URL.", reason="unsafe_destination") from None
    authority = f"[{hostname}]" if ":" in hostname else hostname
    if port != 443:
        authority += f":{port}"
    return urlunsplit(("https", authority, parsed.path or "/", parsed.query, ""))


def _validated_addresses(hostname: str, port: int) -> list[tuple[int, str]]:
    resolved = queue.Queue(maxsize=1)

    def resolve() -> None:
        try:
            resolved.put((socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM), None))
        except OSError as exc:
            resolved.put((None, exc))

    # DNS itself has no stdlib per-call timeout. A late resolver result is
    # discarded, and it can never create a connection after this call ends.
    threading.Thread(target=resolve, daemon=True, name="mcp-events-dns").start()
    try:
        answers, error = resolved.get(timeout=WEBHOOK_TIMEOUT_SEC)
    except queue.Empty:
        raise _WebhookFailure("timeout", transient=True) from None
    if error is not None:
        raise _WebhookFailure("dns_failed", transient=True) from None
    if not answers or len(answers) > 64:
        raise _WebhookFailure("unsafe_destination")
    addresses: list[tuple[int, str]] = []
    for family, _kind, _protocol, _canonname, sockaddr in answers:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            raise _WebhookFailure("unsafe_destination")
        address = str(_public_ip(sockaddr[0]))
        if (family, address) not in addresses:
            addresses.append((family, address))
    return addresses


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to checked numeric addresses; TLS still verifies the DNS name."""

    def __init__(self, hostname: str, port: int, addresses: list[tuple[int, str]]):
        super().__init__(hostname, port, timeout=WEBHOOK_TIMEOUT_SEC, context=ssl.create_default_context())
        self._addresses = addresses
        self._socket_lock = threading.Lock()
        self._raw_socket: socket.socket | None = None
        self._active_tls: ssl.SSLSocket | None = None
        self._aborted = threading.Event()

    def abort(self) -> None:
        """Interrupt connect, TLS handshake, headers, or a slow response body."""
        self._aborted.set()
        with self._socket_lock:
            sockets = [self.sock, self._raw_socket, self._active_tls]
            self._raw_socket = None
            self._active_tls = None
        seen: set[int] = set()
        for connected in sockets:
            if connected is None or id(connected) in seen:
                continue
            seen.add(id(connected))
            try:
                connected.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connected.close()
            except OSError:
                pass

    def connect(self) -> None:
        deadline = time.monotonic() + WEBHOOK_TIMEOUT_SEC
        last_error: OSError | None = None
        for family, address in self._addresses:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self._aborted.is_set():
                raise TimeoutError()
            raw = socket.socket(family, socket.SOCK_STREAM)
            raw.settimeout(remaining)
            tls = None
            try:
                with self._socket_lock:
                    if self._aborted.is_set():
                        raise TimeoutError()
                    self._raw_socket = raw
                raw.connect((address, self.port))
                # Expose the TLS socket before starting its handshake so the
                # request watchdog can interrupt that phase as well.
                tls = self._context.wrap_socket(raw, server_hostname=self.host, do_handshake_on_connect=False)
                with self._socket_lock:
                    if self._aborted.is_set():
                        raise TimeoutError()
                    self.sock = tls
                    self._active_tls = tls
                    self._raw_socket = None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                tls.settimeout(remaining)
                tls.do_handshake()
                return
            except OSError as exc:
                last_error = exc
                raw.close()
                if tls is not None:
                    tls.close()
                with self._socket_lock:
                    self.sock = None
                    self._active_tls = None
                    self._raw_socket = None
        if last_error is not None:
            raise last_error
        raise _WebhookFailure("unsafe_destination")


def public_webhook_request(url: str, body: bytes, headers: dict[str, str]) -> tuple[int, bytes]:
    """Bounded public HTTPS POST with no proxy, redirects, or DNS re-resolution."""
    if len(body) > MAX_EVENT_BYTES:
        raise _WebhookFailure("payload_too_large")
    checked_url = _callback_url(url)
    parsed = urlsplit(checked_url)
    port = parsed.port or 443
    addresses = _validated_addresses(parsed.hostname, port)
    connection = _PinnedHTTPSConnection(parsed.hostname, port, addresses)
    expired = threading.Event()

    def interrupt_request() -> None:
        expired.set()
        connection.abort()

    # Socket timeouts alone reset on every successful read. This deadline
    # also bounds a peer that keeps trickling header/body bytes indefinitely.
    watchdog = threading.Timer(WEBHOOK_TIMEOUT_SEC, interrupt_request)
    watchdog.daemon = True
    watchdog.start()
    try:
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        connection.request("POST", target, body=body, headers=headers)
        response = connection.getresponse()
        if 300 <= response.status <= 399:
            raise _WebhookFailure("redirect_rejected")
        length = response.getheader("Content-Length")
        if length is not None:
            try:
                if int(length) > MAX_RESPONSE_BYTES or int(length) < 0:
                    raise _WebhookFailure("response_too_large")
            except ValueError:
                raise _WebhookFailure("invalid_response") from None
        data = response.read(MAX_RESPONSE_BYTES + 1)
        if expired.is_set():
            raise TimeoutError()
        if len(data) > MAX_RESPONSE_BYTES:
            raise _WebhookFailure("response_too_large")
        return response.status, data
    except (TimeoutError, socket.timeout):
        raise _WebhookFailure("timeout", transient=True) from None
    except (OSError, http.client.HTTPException):
        raise _WebhookFailure("timeout" if expired.is_set() else "connection_failed", transient=True) from None
    finally:
        watchdog.cancel()
        connection.abort()
        connection.close()
        watchdog.join(timeout=1)


class MCPEvents:
    """One project's durable webhook subscriptions and pending deliveries."""

    def __init__(self, store: Any, project_id: str, send_webhook: Callable | None = None, *, clock: Callable | None = None,
                 principal_validator: Callable[[str], bool] | None = None):
        if not isinstance(project_id, str) or not ID_RE.fullmatch(project_id):
            raise ValueError("invalid MCP Events project ID")
        self.store = store
        self.project_id = project_id
        self.path = Path(store.data_dir) / "mcp_events.json"
        self._send_webhook = send_webhook or public_webhook_request
        self._clock = clock or time.time
        # HTTP hosts can bind this to their current account/token policy, so
        # credentials revoked after subscribe also stop background delivery.
        self.principal_validator = principal_validator
        self._lock = threading.RLock()
        self._dispatch_lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._document = {"schema_version": 1, "project_id": project_id, "subscriptions": {}, "outbox": {}, "events": {}}
        if self.path.exists():
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            try:
                with os.fdopen(os.open(self.path, flags), encoding="utf-8") as handle:
                    loaded = json.load(handle)
                if (not isinstance(loaded, dict) or loaded.get("schema_version") != 1
                        or loaded.get("project_id") != project_id
                        or not isinstance(loaded.get("subscriptions"), dict)
                        or not isinstance(loaded.get("outbox"), dict)):
                    raise ValueError()
                self._document = loaded
                loaded.setdefault("events", {})
                for item in loaded["outbox"].values():
                    if item.get("state") == "dispatching":
                        item["state"] = "pending"
                os.chmod(self.path, 0o600)
            except (OSError, ValueError, TypeError):
                raise RuntimeError("MCP Events subscription storage is invalid; restore its saved state.") from None

    def _save_locked(self) -> None:
        descriptor, temporary = tempfile.mkstemp(prefix="mcp-events-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(_json_bytes(self._document))
                handle.write(b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _session(self, *, require_open: bool = True) -> str:
        with self.store.lock:
            workspace = self.store.state.get("workspace") or {}
            if workspace.get("project_id") != self.project_id:
                raise MCPEventsError("Event subscription belongs to another project.", reason="unauthorized", status=403)
            session_id = workspace.get("session_id")
            session = self.store.state.get("sessions", {}).get(session_id)
            if not session or require_open and session.get("status") != "open":
                raise MCPEventsError("This feedback session is not open.", reason="session_closed", status=409)
            return session_id

    def list_events(self) -> dict[str, Any]:
        return {"events": [{
            "name": EVENT_NAME,
            "description": "A user submitted annotated visual feedback for this reconstruction session. The full immutable packet, including images, is available through workspace_get_feedback.",
            "delivery": ["webhook"],
            "inputSchema": {"type": "object", "properties": {"session_id": {"type": "string", "pattern": "^[0-9a-f]{32}$", "description": "Current visual workbench session ID."}}, "required": ["session_id"], "additionalProperties": False},
            "payloadSchema": {"type": "object", "properties": {
                "project_id": {"type": "string"}, "session_id": {"type": "string"}, "feedback_id": {"type": "string"},
                "scene_revision": {"type": "integer", "minimum": 1}, "summary": {"type": "string"}, "note_preview": {"type": "string"},
            }, "required": ["project_id", "session_id", "feedback_id", "scene_revision", "summary", "note_preview"], "additionalProperties": False},
        }]}

    def _identity(self, params: Any, principal: Any, *, subscribing: bool) -> tuple[str, dict, str]:
        if (not isinstance(principal, str) or not principal or len(principal) > 512
                or not self._principal_allowed(principal)):
            raise MCPEventsError("An authenticated event principal is required.", reason="unauthorized", status=403)
        if not isinstance(params, dict) or params.get("name") != EVENT_NAME:
            raise MCPEventsError("Unsupported event name.", reason="unknown_event")
        allowed = {"name", "arguments", "delivery", "cursor", "ttlMs"} if subscribing else {"name", "arguments", "delivery"}
        if set(params) - allowed:
            raise MCPEventsError("Unexpected event subscription fields.")
        arguments = params.get("arguments")
        if (not isinstance(arguments, dict) or set(arguments) != {"session_id"}
                or arguments.get("session_id") != self._session(require_open=subscribing)):
            raise MCPEventsError("Subscribe only to this project's current feedback session.", reason="unauthorized", status=403)
        delivery = params.get("delivery")
        if (not isinstance(delivery, dict) or delivery.get("mode") != "webhook"
                or set(delivery) - {"mode", "url", "secret"}):
            raise MCPEventsError("Only webhook event delivery is supported.", reason="unsupported_delivery")
        url = _callback_url(delivery.get("url"))
        identity = _json_bytes({"principal": principal, "url": url, "name": EVENT_NAME, "arguments": arguments})
        return "sub_" + hashlib.sha256(identity).hexdigest(), copy.deepcopy(arguments), url

    def _headers(self, subscription: dict, event_id: str, body: bytes, now: float) -> dict[str, str]:
        timestamp = str(int(now))
        signed = event_id.encode("utf-8") + b"." + timestamp.encode("ascii") + b"." + body
        keys = [subscription["secret"]]
        if subscription.get("previous_secret") and subscription.get("rotation_until", 0) > now:
            keys.append(subscription["previous_secret"])
        signatures = ["v1," + base64.b64encode(hmac.new(_secret_bytes(key), signed, hashlib.sha256).digest()).decode("ascii") for key in keys]
        return {"Content-Type": "application/json", "webhook-id": event_id,
                "webhook-timestamp": timestamp, "webhook-signature": " ".join(signatures),
                "X-MCP-Subscription-Id": subscription["id"]}

    def _post(self, url: str, body: bytes, headers: dict) -> tuple[int, bytes]:
        try:
            status, response = self._send_webhook(url, body, headers)
            if type(status) is not int or not isinstance(response, bytes):
                raise _WebhookFailure("invalid_response")
            if len(response) > MAX_RESPONSE_BYTES:
                raise _WebhookFailure("response_too_large")
            if 300 <= status <= 399:
                raise _WebhookFailure("redirect_rejected")
            return status, response
        except _WebhookFailure:
            raise
        except (TimeoutError, socket.timeout):
            raise _WebhookFailure("timeout", transient=True) from None
        except (ConnectionError, OSError):
            raise _WebhookFailure("connection_failed", transient=True) from None
        except (ValueError, TypeError):
            raise _WebhookFailure("invalid_response") from None
        except Exception:
            # Do not expose callback URLs, response bodies, or signing secrets
            # from a custom transport exception in a persisted/public status.
            raise _WebhookFailure("unexpected_response", transient=True) from None

    def subscribe(self, params: dict, principal: str) -> dict[str, Any]:
        subscription_id, arguments, url = self._identity(params, principal, subscribing=True)
        secret = params["delivery"].get("secret")
        _secret_bytes(secret)
        if params.get("cursor") is not None:
            raise MCPEventsError("This event does not support replay cursors.", reason="replay_unsupported")
        ttl = params.get("ttlMs", DEFAULT_TTL_SEC * 1000)
        if ttl is None:
            ttl = DEFAULT_TTL_SEC * 1000
        if type(ttl) is not int or ttl <= 0:
            raise MCPEventsError("ttlMs must be a positive integer or null.")
        ttl_sec = min(ttl, MAX_TTL_SEC * 1000) / 1000
        now = self._clock()
        with self._lock:
            existing = copy.deepcopy(self._document["subscriptions"].get(subscription_id))
        verified_until = now + VERIFICATION_CACHE_SEC
        if not (existing and existing.get("active") and existing.get("secret") == secret
                and existing.get("verified_until", 0) > now):
            challenge = secrets.token_urlsafe(32)
            body = _json_bytes({"type": "verification", "challenge": challenge})
            verification_id = "msg_verification_" + uuid.uuid4().hex
            candidate = {"id": subscription_id, "secret": secret}
            try:
                status, response = self._post(url, body, self._headers(candidate, verification_id, body, now))
                if not 200 <= status <= 299:
                    raise _WebhookFailure("challenge_failed")
                try:
                    echoed = json.loads(response).get("challenge")
                except (ValueError, TypeError, AttributeError):
                    raise _WebhookFailure("challenge_failed") from None
                if (not isinstance(echoed, str) or len(echoed) > 1024
                        or not hmac.compare_digest(echoed.encode("utf-8"), challenge.encode("ascii"))
                        or self._clock() - now > WEBHOOK_TIMEOUT_SEC + 5):
                    raise _WebhookFailure("challenge_failed")
            except _WebhookFailure as exc:
                raise MCPEventsError("Webhook callback verification failed.", code=-32015, reason=exc.reason, status=400) from None
        else:
            verified_until = existing["verified_until"]
        # Recheck the session after network verification; no application data
        # is activated if access changed while the callback was being checked.
        if arguments["session_id"] != self._session() or not self._principal_allowed(principal):
            raise MCPEventsError("Feedback session changed during callback verification.", reason="unauthorized", status=403)
        now = self._clock()
        expires_at = math.floor((now + ttl_sec) * 1000) / 1000
        with self._dispatch_lock, self._lock:
            previous = self._document["subscriptions"].get(subscription_id)
            subscription = {"id": subscription_id, "name": EVENT_NAME, "arguments": arguments,
                            "principal": principal, "url": url, "secret": secret, "active": True,
                            "expires_at": expires_at, "verified_until": verified_until,
                            "created_at": previous.get("created_at", now) if previous else now, "updated_at": now}
            if previous and previous.get("secret") != secret:
                subscription["previous_secret"] = previous["secret"]
                subscription["rotation_until"] = now + ROTATION_WINDOW_SEC
            elif previous and previous.get("rotation_until", 0) > now:
                subscription["previous_secret"] = previous["previous_secret"]
                subscription["rotation_until"] = previous["rotation_until"]
            self._document["subscriptions"][subscription_id] = subscription
            for saved in self._document["events"].values():
                if (saved.get("awaiting_subscription")
                        and saved["event"]["data"]["session_id"] == arguments["session_id"]):
                    self._queue_event_locked(subscription_id, saved["event"], now)
                    saved["awaiting_subscription"] = False
            self._save_locked()
        self._wake.set()
        return {"id": subscription_id, "refreshBefore": _iso(expires_at), "cursor": None, "truncated": False}

    def unsubscribe(self, params: dict, principal: str) -> dict:
        subscription_id, _arguments, _url = self._identity(params, principal, subscribing=False)
        with self._dispatch_lock, self._lock:
            subscription = self._document["subscriptions"].get(subscription_id)
            if subscription:
                subscription["active"] = False
                subscription["updated_at"] = self._clock()
                for item in self._document["outbox"].values():
                    if item["subscription_id"] == subscription_id and item["state"] in {"pending", "dispatching"}:
                        item["state"] = "cancelled"
                        item["last_reason"] = "unsubscribed"
                self._save_locked()
        self._wake.set()
        return {}

    def _principal_allowed(self, principal: str) -> bool:
        if self.principal_validator is None:
            return True
        try:
            return self.principal_validator(principal) is True
        except Exception:
            return False

    def _active(self, subscription: dict, session_id: str, now: float) -> bool:
        return bool(subscription.get("active") and subscription.get("expires_at", 0) > now
                    and subscription.get("arguments", {}).get("session_id") == session_id
                    and self._principal_allowed(subscription.get("principal", "")))

    def _feedback_status_locked(self, feedback_id: str, session_id: str, now: float) -> dict:
        subscribers = sum(self._active(item, session_id, now) for item in self._document["subscriptions"].values())
        items = [item for item in self._document["outbox"].values() if item["event"]["eventId"] == feedback_id]
        states = {item["state"] for item in items}
        if states & {"pending", "dispatching"}:
            status = "event_pending"
        elif "delivered" in states:
            status = "event_delivered"
        elif "failed" in states:
            status = "event_failed"
        else:
            status = "event_unsubscribed"
        awaiting = self._document["events"].get(feedback_id, {}).get("awaiting_subscription") is True
        return {"feedback_id": feedback_id, "status": status, "subscriber_count": subscribers,
                "awaiting_subscription": awaiting}

    def _queue_event_locked(self, subscription_id: str, event: dict, now: float) -> None:
        key = subscription_id + ":" + event["eventId"]
        if key not in self._document["outbox"]:
            self._document["outbox"][key] = {"subscription_id": subscription_id, "event": copy.deepcopy(event),
                                            "state": "pending", "attempts": 0, "next_attempt_at": now,
                                            "created_at": self._document["events"].get(event["eventId"], {}).get("created_at", now),
                                            "last_status": None, "last_reason": None}

    def feedback_status(self, feedback_id: str) -> dict:
        """Read one event's receipt state without emitting or modifying anything."""
        session_id = self._session(require_open=False)
        try:
            active_session = self._session()
        except MCPEventsError:
            active_session = None
        with self._lock:
            result = self._feedback_status_locked(feedback_id, session_id, self._clock())
            if active_session is None:
                result["subscriber_count"] = 0
            return result

    def emit_feedback(self, feedback: dict) -> dict:
        session_id = self._session()
        if (not isinstance(feedback, dict) or feedback.get("session_id") != session_id
                or not isinstance(feedback.get("feedback_id"), str) or not ID_RE.fullmatch(feedback["feedback_id"])
                or type(feedback.get("scene_revision")) is not int or feedback["scene_revision"] < 1):
            raise MCPEventsError("Invalid feedback event for this project.", reason="unauthorized", status=403)
        timestamp = feedback.get("submitted_at")
        try:
            occurrence = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if occurrence.tzinfo is None:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise MCPEventsError("Feedback occurrence time must include a timezone.") from None
        note = feedback.get("note", "")
        if not isinstance(note, str):
            raise MCPEventsError("Feedback note must be text.")
        summary = (f"{len(feedback.get('annotations', []))} annotations; "
                   f"{len(feedback.get('selected_object_ids', []))} selected objects; "
                   f"{len(feedback.get('dynamic_frames', []))} frozen dynamic frames.")
        event = {"eventId": feedback["feedback_id"], "name": EVENT_NAME,
                 "timestamp": occurrence.isoformat(timespec="seconds"), "cursor": None,
                 "data": {"project_id": self.project_id, "session_id": session_id,
                          "feedback_id": feedback["feedback_id"], "scene_revision": feedback["scene_revision"],
                          "summary": summary, "note_preview": note[:4000]}}
        if len(_json_bytes(event)) > MAX_EVENT_BYTES:
            raise MCPEventsError("Event summary exceeds the webhook payload limit.", reason="payload_too_large")
        now = self._clock()
        with self._lock:
            saved = self._document["events"].get(feedback["feedback_id"])
            if saved is not None:
                # Recovery must neither change immutable event bytes nor add
                # historical deliveries to subscribers created afterwards.
                return self._feedback_status_locked(feedback["feedback_id"], session_id, now)
            saved = {"event": copy.deepcopy(event), "created_at": now, "awaiting_subscription": True}
            self._document["events"][feedback["feedback_id"]] = saved
            for subscription_id, subscription in self._document["subscriptions"].items():
                if self._active(subscription, session_id, now):
                    self._queue_event_locked(subscription_id, event, now)
                    saved["awaiting_subscription"] = False
            self._save_locked()
            result = self._feedback_status_locked(feedback["feedback_id"], session_id, now)
        self._wake.set()
        return result

    def status(self, session_id: str) -> dict:
        current_session = self._session(require_open=False)
        if session_id != current_session:
            raise MCPEventsError("Feedback session belongs to another project.", reason="unauthorized", status=403)
        try:
            active_session = self._session()
        except MCPEventsError:
            active_session = None
        now = self._clock()
        with self._lock:
            subscribers = sum(self._active(item, active_session, now) for item in self._document["subscriptions"].values())
            items = [item for item in self._document["outbox"].values() if item["event"]["data"]["session_id"] == session_id]
            saved_events = [saved for saved in self._document["events"].values() if saved["event"]["data"]["session_id"] == session_id]
            last = max(reversed(saved_events), key=lambda saved: saved["created_at"], default=None)
            last_delivery = None
            if last:
                feedback_id = last["event"]["eventId"]
                last_delivery = self._feedback_status_locked(feedback_id, session_id, now)
                last_delivery["subscriber_count"] = subscribers
                attempts = [item for item in items if item["event"]["eventId"] == feedback_id]
                latest_attempt = max(attempts, key=lambda item: item.get("attempted_at", item["created_at"]), default=None)
                last_delivery.update({"attempts": max((item["attempts"] for item in attempts), default=0),
                                      "last_status": latest_attempt.get("last_status") if latest_attempt else None,
                                      "error": latest_attempt.get("last_reason") if latest_attempt else None})
            return {"transport": "mcp_events", "session_id": session_id, "subscriber_count": subscribers,
                    "status": "event_ready" if subscribers else "event_unsubscribed",
                    "pending_count": sum(item["state"] in {"pending", "dispatching"} for item in items),
                    "waiting_count": sum(saved.get("awaiting_subscription") is True for saved in saved_events),
                    "delivered_count": sum(item["state"] == "delivered" for item in items),
                    "failed_count": sum(item["state"] == "failed" for item in items), "last_delivery": last_delivery}

    def dispatch_once(self) -> int:
        """Attempt at most one due delivery, returning 1 if a POST was attempted."""
        try:
            session_id = self._session()
        except MCPEventsError:
            session_id = None
        with self._dispatch_lock:
            now = self._clock()
            with self._lock:
                selected = None
                changed = False
                for key, item in self._document["outbox"].items():
                    if item["state"] not in {"pending", "dispatching"}:
                        continue
                    subscription = self._document["subscriptions"].get(item["subscription_id"])
                    if not subscription or not self._active(subscription, session_id, now):
                        item["state"] = "expired" if subscription and subscription.get("active") else "cancelled"
                        item["last_reason"] = "subscription_expired" if subscription and subscription.get("active") else "unsubscribed"
                        changed = True
                        continue
                    if selected is None and item["next_attempt_at"] <= now:
                        selected = key
                if selected is None:
                    if changed:
                        self._save_locked()
                    return 0
                item = self._document["outbox"][selected]
                item["state"] = "dispatching"
                item["attempts"] += 1
                item["attempted_at"] = now
                subscription = copy.deepcopy(self._document["subscriptions"][item["subscription_id"]])
                event = copy.deepcopy(item["event"])
                self._save_locked()
            body = _json_bytes(event)
            headers = self._headers(subscription, event["eventId"], body, self._clock())
            status = None
            reason = None
            transient = False
            try:
                status, _response = self._post(subscription["url"], body, headers)
                if not 200 <= status <= 299:
                    reason = "http_" + str(status)
                    transient = status in {408, 425, 429} or 500 <= status <= 599
            except _WebhookFailure as exc:
                reason, transient = exc.reason, exc.transient
            with self._lock:
                item = self._document["outbox"][selected]
                now = self._clock()
                item["last_status"] = status
                item["last_reason"] = reason
                if reason is None:
                    item["state"] = "delivered"
                    item["delivered_at"] = now
                elif transient and item["attempts"] < MAX_ATTEMPTS and self._active(subscription, session_id, now):
                    item["state"] = "pending"
                    item["next_attempt_at"] = now + min(300, 2 ** (item["attempts"] - 1))
                else:
                    item["state"] = "failed"
                self._save_locked()
            return 1

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True, name="mcp-events-webhooks")
            self._thread.start()
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                attempted = self.dispatch_once()
            except Exception as exc:
                # Storage can recover on the next attempt. Logging only the
                # exception class avoids disclosing callback/account secrets.
                logger.warning("MCP Events worker paused after %s", type(exc).__name__)
                self._wake.wait(timeout=5)
                self._wake.clear()
                continue
            if not attempted:
                self._wake.wait(timeout=1)
                self._wake.clear()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            # DNS resolution and the whole HTTP request each have a hard
            # timeout; wait for both before the host releases its data lock.
            thread.join(timeout=2 * WEBHOOK_TIMEOUT_SEC + 1)

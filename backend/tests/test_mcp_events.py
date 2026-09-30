"""MCP Events lifecycle, durable delivery, signing, and callback safety."""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import io
import json
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import SceneStore  # noqa: E402
from mcp_events import (  # noqa: E402
    DEFAULT_TTL_SEC, EVENT_NAME, MAX_ATTEMPTS, MAX_EVENT_BYTES,
    MCPEvents, MCPEventsError, _PinnedHTTPSConnection, _WebhookFailure,
    _callback_url, public_webhook_request,
)


PROJECT_ID = "1" * 32
FEEDBACK_ID = "2" * 32
SECRET = "whsec_" + base64.b64encode(b"a" * 32).decode("ascii")
NEW_SECRET = "whsec_" + base64.b64encode(b"b" * 32).decode("ascii")


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


class Receiver:
    def __init__(self):
        self.calls = []
        self.delivery_responses = []
        self.challenge_response = None

    def __call__(self, url, body, headers):
        self.calls.append((url, body, headers.copy()))
        decoded = json.loads(body)
        if decoded.get("type") == "verification":
            return self.challenge_response or (200, json.dumps({"challenge": decoded["challenge"]}).encode())
        if self.delivery_responses:
            response = self.delivery_responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response
        return 202, b"{}"


class MCPEventsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.store = SceneStore(self.root / "data")
        workspace = self.store.ensure_workspace(self.project, project_id=PROJECT_ID)
        self.session_id = workspace["session_id"]
        self.clock = Clock()
        self.receiver = Receiver()
        self.events = MCPEvents(self.store, PROJECT_ID, self.receiver, clock=self.clock)
        self.params = {"name": EVENT_NAME, "arguments": {"session_id": self.session_id},
                       "delivery": {"mode": "webhook", "url": "https://receiver.example/callback", "secret": SECRET},
                       "cursor": None}
        self.feedback = {"feedback_id": FEEDBACK_ID, "session_id": self.session_id, "scene_revision": 3,
                         "submitted_at": datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(),
                         "note": "Move the cabinet here.", "annotations": [{"id": "a"}],
                         "scene_original_url": "/screenshots/secret.png", "dynamic_frames": []}

    def tearDown(self):
        self.events.close()
        self.temporary.cleanup()

    def subscribe(self, **updates):
        params = {**self.params, **updates}
        return self.events.subscribe(params, "account-one")

    def unsubscribe(self, principal="account-one"):
        params = {key: value for key, value in self.params.items() if key != "cursor"}
        params["delivery"] = {key: value for key, value in params["delivery"].items() if key != "secret"}
        return self.events.unsubscribe(params, principal)

    def test_catalog_and_signed_verification_then_signed_immutable_delivery(self):
        catalog = self.events.list_events()["events"][0]
        self.assertEqual(catalog["name"], EVENT_NAME)
        self.assertEqual(catalog["delivery"], ["webhook"])
        subscribed = self.subscribe()
        self.assertIsNone(subscribed["cursor"])
        self.assertFalse(subscribed["truncated"])
        verification = json.loads(self.receiver.calls[0][1])
        self.assertEqual(verification["type"], "verification")
        self.assertTrue(self.receiver.calls[0][2]["webhook-id"].startswith("msg_verification_"))
        self.assertEqual(self.events.emit_feedback(self.feedback)["status"], "event_pending")
        self.assertEqual(self.events.dispatch_once(), 1)
        _url, body, headers = self.receiver.calls[1]
        event = json.loads(body)
        self.assertEqual(event["eventId"], FEEDBACK_ID)
        self.assertEqual(event["data"]["feedback_id"], FEEDBACK_ID)
        self.assertEqual(event["data"]["note_preview"], self.feedback["note"])
        self.assertNotIn("scene_original_url", event["data"])
        signed = FEEDBACK_ID.encode() + b"." + headers["webhook-timestamp"].encode() + b"." + body
        expected = "v1," + base64.b64encode(hmac.new(b"a" * 32, signed, hashlib.sha256).digest()).decode()
        self.assertEqual(headers["webhook-signature"], expected)
        self.assertEqual(headers["X-MCP-Subscription-Id"], subscribed["id"])
        self.assertEqual(self.events.feedback_status(FEEDBACK_ID)["status"], "event_delivered")
        self.assertEqual(self.events.status(self.session_id)["delivered_count"], 1)

    def test_failed_verification_never_persists_or_sends_application_data(self):
        self.receiver.challenge_response = (200, b'{"challenge":"wrong"}')
        with self.assertRaises(MCPEventsError) as caught:
            self.subscribe()
        self.assertEqual(caught.exception.code, -32015)
        self.assertEqual(caught.exception.data, {"reason": "challenge_failed"})
        self.assertEqual(self.events.status(self.session_id)["subscriber_count"], 0)
        self.assertFalse(self.events.path.exists())
        self.events.emit_feedback(self.feedback)
        self.assertEqual(self.events.dispatch_once(), 0)
        self.assertEqual(len(self.receiver.calls), 1)

    def test_refresh_idempotency_principal_isolation_and_verification_cache(self):
        first = self.subscribe()
        second = self.subscribe()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.receiver.calls), 1)
        other = self.events.subscribe(self.params, "account-two")
        self.assertNotEqual(first["id"], other["id"])
        self.assertEqual(self.events.status(self.session_id)["subscriber_count"], 2)
        self.events.emit_feedback(self.feedback)
        self.events.emit_feedback({**self.feedback, "note": "Changed duplicate must not alter stored packet."})
        self.assertEqual(self.events.status(self.session_id)["pending_count"], 2)
        self.assertEqual(self.unsubscribe(), {})
        self.assertEqual(self.unsubscribe(), {})
        self.assertEqual(self.events.status(self.session_id)["subscriber_count"], 1)
        self.events.dispatch_once()
        self.assertEqual(json.loads(self.receiver.calls[-1][1])["data"]["note_preview"], self.feedback["note"])

    def test_no_subscriber_feedback_is_durable_then_delivered_after_subscribe(self):
        result = self.events.emit_feedback(self.feedback)
        self.assertEqual(result["status"], "event_unsubscribed")
        self.assertTrue(result["awaiting_subscription"])
        last = self.events.status(self.session_id)["last_delivery"]
        self.assertEqual(last["feedback_id"], FEEDBACK_ID)
        self.assertEqual(last["attempts"], 0)
        self.assertIsNone(last["last_status"])
        self.assertEqual(self.events.status(self.session_id)["waiting_count"], 1)
        restarted = MCPEvents(self.store, PROJECT_ID, self.receiver, clock=self.clock)
        restarted.subscribe(self.params, "account-one")
        self.assertEqual(restarted.feedback_status(FEEDBACK_ID)["status"], "event_pending")
        self.assertFalse(restarted.feedback_status(FEEDBACK_ID)["awaiting_subscription"])
        self.assertEqual(restarted.status(self.session_id)["waiting_count"], 0)
        restarted.dispatch_once()
        self.assertEqual(restarted.feedback_status(FEEDBACK_ID)["status"], "event_delivered")
        restarted.emit_feedback(self.feedback)
        self.assertEqual(restarted.dispatch_once(), 0)
        self.assertEqual(stat.S_IMODE(restarted.path.stat().st_mode), 0o600)

    def test_new_subscriber_does_not_receive_old_delivered_or_assigned_events(self):
        self.subscribe()
        self.events.emit_feedback(self.feedback)
        self.events.dispatch_once()
        second = {**self.feedback, "feedback_id": "3" * 32}
        self.events.emit_feedback(second)
        other = self.events.subscribe(self.params, "account-two")
        self.events.emit_feedback(self.feedback)
        self.events.emit_feedback(second)
        restarted = MCPEvents(self.store, PROJECT_ID, self.receiver, clock=self.clock)
        restarted.emit_feedback(self.feedback)
        restarted.emit_feedback(second)
        outbox = json.loads(self.events.path.read_text())["outbox"]
        self.assertFalse(any(item["subscription_id"] == other["id"] for item in outbox.values()))
        self.assertEqual(self.events.status(self.session_id)["pending_count"], 1)

    def test_transient_retry_survives_restart_preserves_body_and_changes_signature(self):
        self.subscribe()
        self.receiver.delivery_responses = [(503, b"{}"), (202, b"{}")]
        self.events.emit_feedback(self.feedback)
        self.events.dispatch_once()
        first_delivery = self.receiver.calls[-1]
        self.assertEqual(self.events.feedback_status(FEEDBACK_ID)["status"], "event_pending")
        restarted = MCPEvents(self.store, PROJECT_ID, self.receiver, clock=self.clock)
        self.assertEqual(restarted.dispatch_once(), 0)
        self.clock.now += 2
        self.assertEqual(restarted.dispatch_once(), 1)
        second_delivery = self.receiver.calls[-1]
        self.assertEqual(first_delivery[1], second_delivery[1])
        self.assertEqual(first_delivery[2]["webhook-id"], second_delivery[2]["webhook-id"])
        self.assertNotEqual(first_delivery[2]["webhook-signature"], second_delivery[2]["webhook-signature"])
        self.assertEqual(restarted.status(self.session_id)["last_delivery"]["attempts"], 2)

    def test_retry_budget_and_permanent_responses(self):
        for response in [(410, b"{}"), (413, b"{}"), (302, b"{}")]:
            with self.subTest(status=response[0]):
                self.subscribe()
                feedback = {**self.feedback, "feedback_id": hashlib.md5(str(response).encode()).hexdigest()}
                self.receiver.delivery_responses = [response]
                self.events.emit_feedback(feedback)
                self.events.dispatch_once()
                self.clock.now += 10
                self.assertEqual(self.events.dispatch_once(), 0)
                self.assertEqual(self.events.feedback_status(feedback["feedback_id"])["status"], "event_failed")
        self.receiver.delivery_responses = [(503, b"{}")] * MAX_ATTEMPTS
        self.events.emit_feedback(self.feedback)
        for _attempt in range(MAX_ATTEMPTS):
            self.events.dispatch_once()
            self.clock.now += 301
        self.assertEqual(self.events.feedback_status(FEEDBACK_ID)["status"], "event_failed")
        self.assertEqual(self.events.dispatch_once(), 0)

    def test_expiration_unsubscribe_and_closed_session_stop_queued_retries(self):
        self.subscribe(ttlMs=1000)
        self.events.emit_feedback(self.feedback)
        self.clock.now += 2
        self.assertEqual(self.events.dispatch_once(), 0)
        self.assertEqual(self.events.status(self.session_id)["subscriber_count"], 0)
        self.subscribe()
        second = {**self.feedback, "feedback_id": "3" * 32}
        self.events.emit_feedback(second)
        self.unsubscribe()
        self.assertEqual(self.events.dispatch_once(), 0)
        self.assertEqual(self.events.feedback_status(second["feedback_id"])["status"], "event_unsubscribed")
        self.assertFalse(self.events.feedback_status(second["feedback_id"])["awaiting_subscription"])
        self.subscribe()
        third = {**self.feedback, "feedback_id": "4" * 32}
        self.events.emit_feedback(third)
        self.store.cancel_session(self.session_id)
        self.assertEqual(self.events.dispatch_once(), 0)
        self.assertEqual(self.unsubscribe(), {})

    def test_finite_null_and_bounded_ttl_grants_and_session_authorization(self):
        requested = self.subscribe(ttlMs=None)
        expiry = datetime.fromisoformat(requested["refreshBefore"].replace("Z", "+00:00")).timestamp()
        self.assertEqual(expiry, self.clock.now + DEFAULT_TTL_SEC)
        requested = self.subscribe(ttlMs=999999999999)
        expiry = datetime.fromisoformat(requested["refreshBefore"].replace("Z", "+00:00")).timestamp()
        self.assertEqual(expiry, self.clock.now + 7 * 24 * 3600)
        with self.assertRaises(MCPEventsError):
            self.subscribe(arguments={"session_id": "5" * 32})
        with self.assertRaises(MCPEventsError):
            self.subscribe(arguments={"session_id": self.session_id, "thread_id": "another"})
        with self.assertRaises(MCPEventsError):
            self.subscribe(cursor="replay")
        with self.assertRaises(MCPEventsError):
            self.events.subscribe(self.params, "")

    def test_invalid_signing_secrets_are_rejected_before_callback(self):
        secrets = [None, "plain-secret", "whsec_!!", "whsec_" + base64.b64encode(b"x" * 23).decode(),
                   "whsec_" + base64.b64encode(b"x" * 65).decode()]
        for secret in secrets:
            with self.subTest(secret=secret), self.assertRaises(MCPEventsError) as caught:
                self.subscribe(delivery={**self.params["delivery"], "secret": secret})
            self.assertEqual(caught.exception.data["reason"], "invalid_secret")
        self.assertEqual(self.receiver.calls, [])

    def test_expired_verification_cache_requires_a_new_challenge(self):
        first = self.subscribe()
        self.clock.now += 301
        second = self.subscribe()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.receiver.calls), 2)
        self.assertNotEqual(json.loads(self.receiver.calls[0][1])["challenge"], json.loads(self.receiver.calls[1][1])["challenge"])

    def test_revoked_principal_cannot_refresh_or_receive_pending_events(self):
        self.events.principal_validator = lambda principal: principal == "account-one"
        self.subscribe()
        self.events.emit_feedback(self.feedback)
        self.events.principal_validator = lambda _principal: False
        self.assertEqual(self.events.dispatch_once(), 0)
        self.assertEqual(self.events.status(self.session_id)["subscriber_count"], 0)
        with self.assertRaises(MCPEventsError) as caught:
            self.subscribe()
        self.assertEqual(caught.exception.data["reason"], "unauthorized")
        self.assertEqual(len(self.receiver.calls), 1)

    def test_rotation_dual_signatures_and_sanitized_status(self):
        self.subscribe()
        self.clock.now += 2
        self.subscribe(delivery={**self.params["delivery"], "secret": NEW_SECRET})
        self.events.emit_feedback(self.feedback)
        self.events.dispatch_once()
        signatures = self.receiver.calls[-1][2]["webhook-signature"].split()
        self.assertEqual(len(signatures), 2)
        status = json.dumps(self.events.status(self.session_id))
        for sensitive in [SECRET, NEW_SECRET, "receiver.example", "account-one", "Move the cabinet"]:
            self.assertNotIn(sensitive, status)
        self.clock.now += 301
        self.events.emit_feedback({**self.feedback, "feedback_id": "6" * 32})
        self.events.dispatch_once()
        self.assertEqual(len(self.receiver.calls[-1][2]["webhook-signature"].split()), 1)

    def test_large_feedback_has_bounded_summary_without_image_or_secret_bytes(self):
        self.subscribe()
        note = "图" * 50000
        self.events.emit_feedback({**self.feedback, "note": note, "scene_original_data_url": "data:image/png;base64,secret"})
        self.events.dispatch_once()
        body = self.receiver.calls[-1][1]
        self.assertLessEqual(len(body), MAX_EVENT_BYTES)
        self.assertEqual(len(json.loads(body)["data"]["note_preview"]), 4000)
        self.assertNotIn(b"data:image", body)

    def test_background_start_is_idempotent_and_stops(self):
        self.subscribe()
        self.events.start()
        worker = self.events._thread
        self.events.start()
        self.assertIs(self.events._thread, worker)
        self.events.emit_feedback(self.feedback)
        deadline = time.monotonic() + 2
        while self.events.feedback_status(FEEDBACK_ID)["status"] != "event_delivered" and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(self.events.feedback_status(FEEDBACK_ID)["status"], "event_delivered")
        self.events.close()
        self.assertFalse(worker.is_alive())

    def test_worker_recovers_from_storage_failure_without_logging_secrets(self):
        self.subscribe()
        self.events.emit_feedback(self.feedback)
        self.events._wake.clear()
        original = self.events._save_locked
        failures = [True]

        def save():
            if failures:
                failures.pop()
                raise OSError("confidential callback secret")
            original()

        with patch.object(self.events, "_save_locked", side_effect=save), self.assertLogs("mcp_events", level="WARNING") as caught:
            self.events.start()
            deadline = time.monotonic() + 2
            while failures and time.monotonic() < deadline:
                time.sleep(.01)
            self.events._wake.set()
            deadline = time.monotonic() + 2
            while self.events.feedback_status(FEEDBACK_ID)["status"] != "event_delivered" and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(self.events.feedback_status(FEEDBACK_ID)["status"], "event_delivered")
            self.events.close()
        self.assertNotIn("confidential", " ".join(caught.output))


class CallbackSafetyTests(unittest.TestCase):
    def test_invalid_urls_and_nonpublic_literal_addresses_are_rejected(self):
        values = ["http://receiver.example/callback", "https://user:pass@receiver.example/", "https://127.0.0.1/",
                  "https://192.168.3.157/", "https://[::1]/", "https://[::ffff:127.0.0.1]/",
                  "https://[fec0::1]/", "https://224.0.0.1/", "https://receiver.example/#fragment",
                  "https://receiver.example/\r\nInjected: true"]
        for value in values:
            with self.subTest(url=value), self.assertRaises(MCPEventsError):
                _callback_url(value)

    def test_dns_is_checked_before_connect_and_each_request_after_rebinding(self):
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
        private = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.3.157", 443))]
        response = MagicMock()
        response.status = 202
        response.getheader.return_value = None
        response.read.return_value = b"{}"
        connection = MagicMock()
        connection.getresponse.return_value = response
        with patch("mcp_events.socket.getaddrinfo", side_effect=[public, private]), patch("mcp_events._PinnedHTTPSConnection", return_value=connection) as factory:
            self.assertEqual(public_webhook_request("https://receiver.example/callback", b"{}", {})[0], 202)
            with self.assertRaises(_WebhookFailure) as caught:
                public_webhook_request("https://receiver.example/callback", b"{}", {})
            self.assertEqual(caught.exception.reason, "unsafe_destination")
            factory.assert_called_once_with("receiver.example", 443, [(socket.AF_INET, "8.8.8.8")])
            self.assertEqual(connection.request.call_count, 1)

    def test_connect_uses_validated_numeric_address_and_original_tls_hostname(self):
        raw = MagicMock()
        tls = MagicMock()
        context = MagicMock()
        context.wrap_socket.return_value = tls
        with patch("mcp_events.ssl.create_default_context", return_value=context), patch("mcp_events.socket.socket", return_value=raw):
            connection = _PinnedHTTPSConnection("receiver.example", 443, [(socket.AF_INET, "8.8.8.8")])
            connection.connect()
            raw.connect.assert_called_once_with(("8.8.8.8", 443))
            context.wrap_socket.assert_called_once_with(raw, server_hostname="receiver.example", do_handshake_on_connect=False)
            tls.do_handshake.assert_called_once_with()

    def test_whole_request_deadline_interrupts_trickling_headers_and_body(self):
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
        for phase in ["headers", "body"]:
            with self.subTest(phase=phase):
                interrupted = threading.Event()
                response = MagicMock()
                response.status = 200
                response.getheader.return_value = None
                connection = MagicMock()
                connection.abort.side_effect = interrupted.set

                def trickle():
                    if not interrupted.wait(timeout=2):
                        raise AssertionError("watchdog did not interrupt response")
                    raise OSError("socket interrupted")

                if phase == "headers":
                    connection.getresponse.side_effect = trickle
                else:
                    connection.getresponse.return_value = response
                    response.read.side_effect = lambda _limit: trickle()
                started = time.monotonic()
                with patch("mcp_events.WEBHOOK_TIMEOUT_SEC", .05), patch("mcp_events.socket.getaddrinfo", return_value=public), patch("mcp_events._PinnedHTTPSConnection", return_value=connection):
                    with self.assertRaises(_WebhookFailure) as caught:
                        public_webhook_request("https://receiver.example/callback", b"{}", {})
                self.assertEqual(caught.exception.reason, "timeout")
                self.assertTrue(caught.exception.transient)
                self.assertLess(time.monotonic() - started, 1)
                self.assertGreaterEqual(connection.abort.call_count, 1)
                connection.close.assert_called_once_with()

    def test_abort_interrupts_registered_raw_and_tls_sockets(self):
        raw, tls = MagicMock(), MagicMock()
        connection = _PinnedHTTPSConnection("receiver.example", 443, [(socket.AF_INET, "8.8.8.8")])
        connection._raw_socket = raw
        connection.sock = tls
        connection.abort()
        raw.shutdown.assert_called_once_with(socket.SHUT_RDWR)
        tls.shutdown.assert_called_once_with(socket.SHUT_RDWR)
        self.assertTrue(connection._aborted.is_set())

    def test_connection_close_response_keeps_body_readable_until_final_abort(self):
        tls = MagicMock()
        tls.makefile.return_value = io.BytesIO(b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 2\r\n\r\n{}")
        connection = _PinnedHTTPSConnection("receiver.example", 443, [(socket.AF_INET, "8.8.8.8")])
        connection.sock = tls
        connection._active_tls = tls
        connection._HTTPConnection__state = http.client._CS_REQ_SENT
        connection._method = "POST"
        response = connection.getresponse()
        self.assertTrue(response.will_close)
        self.assertIsNone(connection.sock)
        tls.shutdown.assert_not_called()
        self.assertEqual(response.read(), b"{}")
        # The separate socket reference remains available to the watchdog
        # even after HTTPConnection dropped its owner reference.
        connection.abort()
        tls.shutdown.assert_called_once_with(socket.SHUT_RDWR)

    def test_redirects_large_response_and_mixed_dns_answers_never_follow(self):
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
        private = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        response = MagicMock()
        response.status = 302
        response.getheader.return_value = "https://127.0.0.1/"
        connection = MagicMock()
        connection.getresponse.return_value = response
        with patch("mcp_events.socket.getaddrinfo", return_value=public), patch("mcp_events._PinnedHTTPSConnection", return_value=connection):
            with self.assertRaises(_WebhookFailure) as caught:
                public_webhook_request("https://receiver.example/callback", b"{}", {})
            self.assertEqual(caught.exception.reason, "redirect_rejected")
            self.assertEqual(connection.request.call_count, 1)
            response.status = 200
            response.getheader.return_value = "262144"
            with self.assertRaises(_WebhookFailure) as caught:
                public_webhook_request("https://receiver.example/callback", b"{}", {})
            self.assertEqual(caught.exception.reason, "response_too_large")
        with patch("mcp_events.socket.getaddrinfo", return_value=public + [private]), patch("mcp_events._PinnedHTTPSConnection") as factory:
            with self.assertRaises(_WebhookFailure):
                public_webhook_request("https://receiver.example/callback", b"{}", {})
            factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()

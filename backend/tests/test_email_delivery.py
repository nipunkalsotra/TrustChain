"""
tests/test_email_delivery.py — real email delivery, not mocks (this
repo's stated testing philosophy, see CLAUDE.md). A real in-process SMTP
server (aiosmtpd) stands in for a real mail provider the same way
Testcontainers stands in for a real Postgres — the SMTP protocol
exchange, the message encoding, and the actual bytes-over-the-wire are
all real; only WHICH server is on the other end is swapped for a
disposable one. real_brevo_server below does the same thing for
notifications/backends/brevo.py's HTTPS calls, using stdlib
http.server instead of a third-party dependency.
"""

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import db.alerts as alerts_db
from db.engine import get_sessionmaker
from tests.conftest import seed_user_and_token

aiosmtpd_controller = pytest.importorskip("aiosmtpd.controller")
aiosmtpd_handlers = pytest.importorskip("aiosmtpd.handlers")

_SMTP_PORT = 8825  # fixed, not ephemeral — port=0 doesn't reliably trigger-check in every sandbox; see session notes
_BREVO_PORT = 8826  # same reasoning, own port so both fixtures can coexist


def run(coro):
    return asyncio.run(coro)


class _CapturingHandler(aiosmtpd_handlers.Message):
    def __init__(self):
        super().__init__()
        self.received: list = []

    def handle_message(self, message):
        self.received.append(message)


@pytest.fixture
def real_smtp_server(monkeypatch):
    """A REAL SMTP server, started and stopped per test — not a mock of
    smtplib. notifications/backends/smtp.py talks to it over a real
    socket."""
    handler = _CapturingHandler()
    controller = aiosmtpd_controller.Controller(handler, hostname="127.0.0.1", port=_SMTP_PORT)
    controller.start()

    monkeypatch.setenv("EMAIL_BACKEND", "smtp")
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_PORT", str(_SMTP_PORT))
    monkeypatch.setenv("SMTP_USE_TLS", "false")
    # This local test server (aiosmtpd's plain Controller/Message handler)
    # supports no AUTH extension at all — explicitly blank these out
    # rather than leaving them to whatever's ambient in a developer's own
    # backend/.env. A real gap this exact line fixes: a developer with
    # real SMTP_USERNAME/PASSWORD configured locally (e.g. to manually
    # test a real provider) previously made this fixture non-hermetic —
    # smtp.py's `if settings.smtp_username: client.login(...)` would then
    # fire against this auth-less server and fail with "SMTP AUTH
    # extension not supported by server", unrelated to whatever the test
    # actually meant to exercise.
    monkeypatch.setenv("SMTP_USERNAME", "")
    monkeypatch.setenv("SMTP_PASSWORD", "")
    from config import get_settings
    get_settings.cache_clear()

    yield handler

    controller.stop()
    get_settings.cache_clear()


class _CapturingBrevoHandler(BaseHTTPRequestHandler):
    """Not a mock of httpx — a real HTTP server on a real loopback socket,
    speaking real HTTP/1.1. notifications/backends/brevo.py's httpx call
    goes over an actual TCP connection to this, decoded/encoded for real;
    only the URL points at 127.0.0.1 instead of api.brevo.com."""

    received: list = []  # class attribute so the fixture can reset it without a new server per test

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        body["_api_key_header"] = self.headers.get("api-key", "")
        # Captured and asserted on in the test below — a real bug this
        # exact field would have caught earlier: an initial version of
        # brevo.py posted to /v3/smtpEmail (doesn't exist on Brevo's real
        # API, which 404s with "Invalid route/method passed") instead of
        # the correct /v3/smtp/email. Because BaseHTTPRequestHandler's
        # do_POST doesn't validate self.path by default, that wrong-URL
        # bug passed this test completely undetected until manual
        # end-to-end testing against the real Brevo API caught it.
        body["_path"] = self.path
        type(self).received.append(body)
        response = json.dumps({"messageId": "real-local-brevo-msg-1"}).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format, *args):
        pass  # silence per-request stderr noise; test assertions are the real signal


@pytest.fixture
def real_brevo_server(monkeypatch):
    """A REAL local HTTP server standing in for api.brevo.com — same
    reasoning as real_smtp_server above, adapted for an HTTPS-based
    backend (brevo.py) instead of raw SMTP."""
    _CapturingBrevoHandler.received = []
    server = ThreadingHTTPServer(("127.0.0.1", _BREVO_PORT), _CapturingBrevoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    monkeypatch.setenv("EMAIL_BACKEND", "brevo")
    monkeypatch.setenv("BREVO_API_KEY", "test-key-not-a-real-secret")
    monkeypatch.setenv("BREVO_API_URL", f"http://127.0.0.1:{_BREVO_PORT}/v3/smtp/email")
    from config import get_settings
    get_settings.cache_clear()

    yield _CapturingBrevoHandler

    server.shutdown()
    server.server_close()
    get_settings.cache_clear()


def test_brevo_backend_actually_sends_to_a_real_server(real_brevo_server):
    """The real gap this test exists to catch: an earlier version of this
    integration used a Brevo REST API key (xkeysib-...) as an SMTP
    password against smtp.py, which Brevo correctly rejects with a real
    535 Authentication failed — the two credential types are for two
    different Brevo products. This exercises the REST path the API key
    actually belongs to."""
    from notifications.backends.brevo import BrevoBackend

    result = run(BrevoBackend().send(
        to="owner@example.com", subject="[TrustChain CRITICAL] test", text_body="hello evidence", html_body="<p>hello</p>",
    ))
    assert result.provider_message_id == "real-local-brevo-msg-1"
    assert len(real_brevo_server.received) == 1
    received = real_brevo_server.received[0]
    assert received["subject"] == "[TrustChain CRITICAL] test"
    assert received["to"] == [{"email": "owner@example.com"}]
    assert received["_api_key_header"] == "test-key-not-a-real-secret"
    assert received["_path"] == "/v3/smtp/email"  # NOT /v3/smtpEmail — see do_POST's comment


def test_brevo_api_url_default_is_the_real_endpoint():
    """Pins config.py's actual default (independent of the fixture above,
    which overrides it to point at the local test server) to Brevo's real
    transactional-email endpoint. The fixture test alone can't catch a
    wrong DEFAULT — only that the code respects whatever URL it's given —
    so this exists specifically to guard the literal production value."""
    import os
    for key in ("BREVO_API_URL",):
        os.environ.pop(key, None)
    from config import get_settings
    get_settings.cache_clear()
    assert get_settings().brevo_api_url == "https://api.brevo.com/v3/smtp/email"
    get_settings.cache_clear()


def test_sender_drains_a_real_pending_delivery_via_brevo(real_brevo_server):
    """Same as test_sender_drains_a_real_pending_delivery_and_marks_it_sent
    below, but through the brevo backend end-to-end — proves
    notifications/sender.py's outbox-claiming loop works with this
    backend too, not just smtp."""
    user = seed_user_and_token(email="email_send_brevo1@example.com")
    now = int(time.time())
    raised = run(alerts_db.raise_alert(
        org_id=user["orgId"], alert_type="rls_test", severity="critical", title="t", summary="s",
        subject="email:brevo1", evidence={"stepId": 1}, detector="test", now=now,
    ))

    from notifications.sender import run_once
    handled = run(run_once(worker_id="test-sender-brevo"))
    assert handled >= 1

    async def _fetch_delivery():
        from sqlalchemy import select
        from db.models import AlertDelivery
        async with get_sessionmaker()() as session:
            return (await session.execute(
                select(AlertDelivery).where(AlertDelivery.alert_id == raised["alertId"])
            )).scalar_one()

    delivery = run(_fetch_delivery())
    assert delivery.status == "sent"
    assert delivery.sent_at is not None
    assert delivery.provider_message_id == "real-local-brevo-msg-1"
    assert len(real_brevo_server.received) == 1


def test_smtp_backend_actually_sends_to_a_real_server(real_smtp_server):
    from notifications.backends.smtp import SmtpBackend

    result = run(SmtpBackend().send(
        to="owner@example.com", subject="[TrustChain CRITICAL] test", text_body="hello evidence", html_body="<p>hello</p>",
    ))
    assert result.provider_message_id.startswith("smtp-")
    assert len(real_smtp_server.received) == 1
    assert real_smtp_server.received[0]["Subject"] == "[TrustChain CRITICAL] test"
    assert real_smtp_server.received[0]["To"] == "owner@example.com"


def test_sender_drains_a_real_pending_delivery_and_marks_it_sent(real_smtp_server):
    user = seed_user_and_token(email="email_send1@example.com")
    now = int(time.time())
    raised = run(alerts_db.raise_alert(
        org_id=user["orgId"], alert_type="rls_test", severity="critical", title="t", summary="s",
        subject="email:1", evidence={"stepId": 1}, detector="test", now=now,
    ))

    from notifications.sender import run_once
    handled = run(run_once(worker_id="test-sender"))
    assert handled >= 1

    async def _fetch_delivery():
        from sqlalchemy import select
        from db.models import AlertDelivery
        async with get_sessionmaker()() as session:
            return (await session.execute(
                select(AlertDelivery).where(AlertDelivery.alert_id == raised["alertId"])
            )).scalar_one()

    delivery = run(_fetch_delivery())
    assert delivery.status == "sent"
    assert delivery.sent_at is not None
    assert delivery.provider_message_id is not None
    assert len(real_smtp_server.received) == 1


def test_sender_retries_on_failure_then_succeeds_on_the_next_attempt(monkeypatch):
    """Simulates a transient failure (e.g. the mail provider briefly
    unreachable) without a real network outage — swap in a backend whose
    FIRST send raises and second succeeds, same shape a real SES/SMTP
    hiccup-then-recover looks like from sender.py's perspective."""
    from notifications.backends.base import EmailSendError, SendResult
    from notifications.backends import memory

    call_count = {"n": 0}

    class _FlakyBackend:
        async def send(self, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise EmailSendError("simulated transient failure")
            return SendResult(provider_message_id="flaky-recovered")

    import notifications.sender as sender_module
    monkeypatch.setattr(sender_module, "get_backend", lambda name: _FlakyBackend())

    from config import get_settings
    monkeypatch.setenv("ALERT_DELIVERY_BACKOFF_BASE_SECONDS", "0.01")
    monkeypatch.setenv("ALERT_DELIVERY_MAX_ATTEMPTS", "5")
    get_settings.cache_clear()

    user = seed_user_and_token(email="email_retry1@example.com")
    now = int(time.time())
    raised = run(alerts_db.raise_alert(
        org_id=user["orgId"], alert_type="rls_test", severity="critical", title="t", summary="s",
        subject="email:retry", evidence={}, detector="test", now=now,
    ))

    from notifications.sender import run_once

    run(run_once(worker_id="test-retry"))  # first attempt: fails, scheduled to retry shortly
    run(asyncio.sleep(0.05))
    run(run_once(worker_id="test-retry"))  # second attempt: succeeds

    async def _fetch():
        from sqlalchemy import select
        from db.models import AlertDelivery
        async with get_sessionmaker()() as session:
            return (await session.execute(
                select(AlertDelivery).where(AlertDelivery.alert_id == raised["alertId"])
            )).scalar_one()

    delivery = run(_fetch())
    assert delivery.status == "sent"
    assert delivery.attempts == 2
    assert delivery.provider_message_id == "flaky-recovered"

    get_settings.cache_clear()
    memory.reset()


def test_dead_letter_after_max_attempts(monkeypatch):
    from notifications.backends.base import EmailSendError

    class _AlwaysFailsBackend:
        async def send(self, **kwargs):
            raise EmailSendError("permanently down")

    import notifications.sender as sender_module
    monkeypatch.setattr(sender_module, "get_backend", lambda name: _AlwaysFailsBackend())

    from config import get_settings
    monkeypatch.setenv("ALERT_DELIVERY_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("ALERT_DELIVERY_BACKOFF_BASE_SECONDS", "0.01")
    get_settings.cache_clear()

    user = seed_user_and_token(email="email_deadletter1@example.com")
    now = int(time.time())
    raised = run(alerts_db.raise_alert(
        org_id=user["orgId"], alert_type="rls_test", severity="critical", title="t", summary="s",
        subject="email:deadletter", evidence={}, detector="test", now=now,
    ))

    from notifications.sender import run_once
    for _ in range(2):
        run(run_once(worker_id="test-deadletter"))
        run(asyncio.sleep(0.05))

    async def _fetch():
        from sqlalchemy import select
        from db.models import AlertDelivery
        async with get_sessionmaker()() as session:
            return (await session.execute(
                select(AlertDelivery).where(AlertDelivery.alert_id == raised["alertId"])
            )).scalar_one()

    delivery = run(_fetch())
    assert delivery.status == "dead_letter"
    assert delivery.attempts >= 2

    get_settings.cache_clear()

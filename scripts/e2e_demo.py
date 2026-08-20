#!/usr/bin/env python3
"""
scripts/e2e_demo.py — Phase 4 plan §4's A-to-Z walkthrough, codified
(step 10): signup → email verification → invite → roles → a real SDK
agent → statistics → tamper → forensic verification → tenant isolation,
against a REAL running backend over real HTTP, with a real Postgres and
a real Anvil underneath. No frontend, no mocks. See
docs/e2e-walkthrough.md for prerequisites and how to read a failure at
each stage.

Re-runnable from a clean database with no manual edits: every email
address and org/project name is suffixed with a run tag (current unix
time) so a second run never collides with a first run's leftover state.

HONEST LIMITATION on email-token extraction, read before assuming this
is silently faking anything: `email_verification_tokens` and
`password_reset_tokens` store only sha256(token) — by design, the same
discipline invitations/API-keys/refresh-tokens already follow (ADR-0014)
— so the raw token exists nowhere except inside the email it was sent
in. There is no backdoor that recovers it from the database. This script
extracts it from the REAL text of the REAL email the backend actually
queued for delivery, by tailing EMAIL_BACKEND=console's real structured
log output (the default backend, written to .logs/fastapi.log by
./start.sh) — not a fabricated shortcut, and not calling
db.email_verification/db.password_reset a second time to mint a
DIFFERENT token than the one actually mailed. If your backend is
configured with EMAIL_BACKEND=brevo (real inbox delivery, as
docs/e2e-walkthrough.md's manual walkthrough uses), pass BOTH
--log-file /dev/null and --watchdog-log-file /dev/null: the script then
pauses at each of the 3 points needing your own inbox — 2 prompts ask
you to paste the real token (or the full link) you read out of a real
email, 1 (the tamper-alert stage, which needs no secret, only "did
delivery happen") asks a plain yes/no — and continues once you answer,
rather than trying and failing to tail a console log that will never
have anything in it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "sdk" / "python"))

RUN_TAG = str(int(time.time()))


class Transcript:
    """Readable, timestamped progress output — this IS the "print a
    readable transcript" requirement (Phase 4 plan step 10), not just a
    debugging aid; a passing run's own stdout is the artifact a reader
    checks against docs/e2e-walkthrough.md's "expected output" table."""

    def stage(self, n: int, title: str) -> None:
        print(f"\n{'=' * 70}\nSTAGE {n} · {title}\n{'=' * 70}")

    def step(self, msg: str) -> None:
        print(f"  · {msg}")

    def ok(self, msg: str) -> None:
        print(f"  ✓ {msg}")

    def fail(self, msg: str) -> None:
        print(f"  ✗ {msg}", file=sys.stderr)


T = Transcript()


class DemoFailure(RuntimeError):
    """Raised with a message naming the stage and the assertion that
    failed — main() catches this at the top level and exits 1 with a
    clean, single-line-per-cause report, rather than a raw traceback a
    reader has to reverse-engineer against docs/e2e-walkthrough.md."""


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise DemoFailure(message)


# ── Token extraction from the console email backend's real log output ──
#
# Correctness note (found and fixed after this docstring had claimed it
# for a while without it actually being true): the module docstring above
# promises that passing --log-file /dev/null falls back to prompting on
# stdin for a real EMAIL_BACKEND=brevo/smtp run. That fallback did not
# actually exist in this file until now — pointing --log-file at
# /dev/null against a real backend just made _poll_new_console_send_line
# time out and raise DemoFailure, the same as any other log file with no
# matching line in it. Fixed by having every token-extraction call site
# check for the /dev/null sentinel FIRST and prompt directly instead of
# ever touching the log-polling path in that mode.

_DEV_NULL = Path("/dev/null")


def _log_offset(log_file: Path) -> int:
    return log_file.stat().st_size if log_file.exists() else 0


def _prompt_for_token(what: str, to_email: str) -> str:
    """The actual --log-file /dev/null fallback the module docstring
    promises: check your own inbox (real delivery — EMAIL_BACKEND=brevo/
    smtp, not console) and paste the token by hand. Only ever reached
    when log_file IS /dev/null (an explicit, deliberate opt-in), never as
    a silent fallback after a console-log timeout — a script blocking on
    stdin unexpectedly, deep into an otherwise-unattended run, would be
    far more confusing than the DemoFailure it would otherwise raise."""
    print(f"\n  ⏸ stuck: need the real {what} sent to {to_email}")
    print(f"    Check that inbox now and paste the token (or the full link — either works): ", end="", flush=True)
    raw = sys.stdin.readline().strip()
    _assert(bool(raw), f"no {what} provided on stdin")
    m = re.search(rf"({_TOKEN_CHARS})\s*$", raw)  # accepts a bare token OR a full .../<token> link
    _assert(m is not None, f"couldn't find a token-shaped string in what was pasted for {what}: {raw[:300]!r}")
    return m.group(1)


def _poll_new_console_send_line(log_file: Path, to_email: str, since_pos: int, timeout: float) -> tuple[str, int]:
    """Polls log_file for a NEW `email_console_send` log line addressed
    to to_email, starting from byte offset since_pos (so a re-run, or a
    second email to the same address later in the same run, never
    matches a stale earlier line). Returns (matching_line, new_byte_offset).

    Real file I/O against the backend's real structlog output — not a
    mock of email delivery. Relies on structlog's ConsoleRenderer (the
    default: json_logs is only true when ENVIRONMENT != "development",
    see logging_config.py) rendering each event, including a multi-line
    `body` value, as ONE physical line — verified for real: Python's
    repr() of a string escapes embedded "\\n" as the two literal
    characters backslash-n rather than a real line break, which is
    exactly what ConsoleRenderer applies to every field value."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if log_file.exists():
            text = log_file.read_text(errors="replace")
            new_text = text[since_pos:]
            new_end = since_pos + len(new_text.encode())
            for line in new_text.splitlines():
                if "email_console_send" in line and to_email in line:
                    return line, new_end
        time.sleep(0.3)
    raise DemoFailure(
        f"never saw an email_console_send line for {to_email} appear in {log_file} within {timeout}s — "
        f"is EMAIL_BACKEND=console (the default)? If you're using a real backend (brevo/smtp), "
        f"check that inbox by hand and see this script's own module docstring."
    )


_TOKEN_CHARS = r"[A-Za-z0-9_-]+"  # secrets.token_urlsafe()'s exact output charset — never padding, never a slash


def _extract_labeled_token(log_file: Path, to_email: str, label: str, since_pos: int, timeout: float = 90.0) -> tuple[str, int]:
    """For the verify-email/reset-password templates, whose body
    contains a literal "{label}: <token>" line (notifications/
    templates.py's render_verification_email/render_password_reset_email).

    Matches the token's own charset explicitly, NOT `\\S+` — the log line
    is a Python repr() of the email body (see _poll_new_console_send_line's
    docstring), so the real "\\n" right after the token became the two
    literal characters backslash+n, which `\\S+` would greedily swallow
    (backslash isn't whitespace) and hand back as part of the "token".

    log_file == /dev/null (the module docstring's documented signal for
    "I'm on a real EMAIL_BACKEND, not console") skips log-polling
    entirely and prompts on stdin instead — see _prompt_for_token."""
    if log_file == _DEV_NULL:
        return _prompt_for_token(label, to_email), since_pos
    line, new_pos = _poll_new_console_send_line(log_file, to_email, since_pos, timeout)
    m = re.search(rf"{re.escape(label)}: ({_TOKEN_CHARS})", line)
    _assert(m is not None, f"found the email to {to_email} but no {label!r} in it: {line[:300]}")
    return m.group(1), new_pos


def _extract_invite_token(log_file: Path, to_email: str, since_pos: int, timeout: float = 90.0) -> tuple[str, int]:
    """The invitation template (render_invitation_email) has no
    "Label: token" line — the raw token is only the last path segment of
    the "Accept: <link>" URL. secrets.token_urlsafe()'s output charset is
    exactly [A-Za-z0-9_-], so matching that charset after "/invite/" is
    precise regardless of what surrounds it in the repr'd body.

    Same /dev/null stdin fallback as _extract_labeled_token above."""
    if log_file == _DEV_NULL:
        return _prompt_for_token("invitation link", to_email), since_pos
    line, new_pos = _poll_new_console_send_line(log_file, to_email, since_pos, timeout)
    m = re.search(rf"/invite/({_TOKEN_CHARS})", line)
    _assert(m is not None, f"found the invitation email to {to_email} but no /invite/<token> link in it: {line[:300]}")
    return m.group(1), new_pos


# ── HTTP helpers ─────────────────────────────────────────────────────

def _check(resp: httpx.Response, expected: int, context: str) -> dict:
    _assert(
        resp.status_code == expected,
        f"{context}: expected HTTP {expected}, got {resp.status_code}: {resp.text[:500]}",
    )
    return resp.json() if resp.content else {}


def run(base_url: str, log_file: Path, watchdog_log_file: Path) -> None:
    client = httpx.Client(base_url=base_url, timeout=15.0)

    T.step(f"Checking {base_url}/ready ...")
    ready = _check(client.get("/ready"), 200, "GET /ready")
    _assert(ready.get("ready") is True, f"/ready reports not ready: {ready}")
    T.ok("backend is ready (DB reachable, migrations current)")

    owner_email = f"demo+owner+{RUN_TAG}@example.com"
    admin_email = f"demo+admin+{RUN_TAG}@example.com"
    member_email = f"demo+member+{RUN_TAG}@example.com"
    other_org_email = f"demo+otherorg+{RUN_TAG}@example.com"
    password = "a-strong-unique-demo-password-7f2k9"

    # ── Stage 1 — Register and verify ───────────────────────────────
    T.stage(1, "Register and verify")
    pos = _log_offset(log_file)
    signup = _check(
        client.post("/auth/signup", json={
            "name": "Ana Owner", "email": owner_email, "password": password,
            "org_name": f"Acme AI {RUN_TAG}", "project_name": "Support Bot",
        }),
        200, "POST /auth/signup (owner)",
    )
    owner_token = signup["token"]
    T.ok(f"signed up: {signup['name']} <{signup['email']}>")

    raw_verify_token, pos = _extract_labeled_token(log_file, owner_email, "Verification token", pos)
    T.ok("extracted verification token from the real queued email (console backend log)")
    _check(client.post(f"/auth/verify-email/{raw_verify_token}"), 200, "POST /auth/verify-email")

    me = _check(client.get("/me", headers=_auth(owner_token)), 200, "GET /me")
    _assert(me["user"]["emailVerified"] is True, f"/me does not show emailVerified=true after verifying: {me}")
    org_id = me["active"]["orgId"]
    _assert(me["active"]["role"] == "owner", f"expected role owner, got {me['active']['role']}")
    T.ok(f"A1+A2 pass: org={org_id}, role=owner, emailVerified=true")

    # ── Stage 2 — Invite an admin ───────────────────────────────────
    T.stage(2, "Invite an admin")
    pos = _log_offset(log_file)
    _check(
        client.post(f"/orgs/{org_id}/invitations", json={"email": admin_email, "role": "admin"}, headers=_auth(owner_token)),
        200, "POST /orgs/{id}/invitations (admin)",
    )
    raw_invite_token, pos = _extract_invite_token(log_file, admin_email, pos)
    admin_signup = _check(
        client.post("/auth/signup", json={
            "name": "Ben Admin", "email": admin_email, "password": password, "invite_token": raw_invite_token,
        }),
        200, "POST /auth/signup (admin, via invite)",
    )
    admin_token = admin_signup["token"]

    members = _check(client.get(f"/orgs/{org_id}/members", headers=_auth(owner_token)), 200, "GET /orgs/{id}/members")
    roles = {m["email"]: m["role"] for m in members["members"]} if "members" in members else {}
    T.ok(f"members now: {roles}")
    _assert(roles.get(owner_email) == "owner", f"owner role missing/wrong: {roles}")
    _assert(roles.get(admin_email) == "admin", f"admin role missing/wrong: {roles}")
    T.ok("A3 passes: both members listed with correct roles")

    # ── Stage 3 — Prove the roles bite ──────────────────────────────
    T.stage(3, "Prove the roles bite")
    pos = _log_offset(log_file)
    _check(
        client.post(f"/orgs/{org_id}/invitations", json={"email": member_email, "role": "member"}, headers=_auth(owner_token)),
        200, "POST /orgs/{id}/invitations (member)",
    )
    raw_member_invite, pos = _extract_invite_token(log_file, member_email, pos)
    member_signup = _check(
        client.post("/auth/signup", json={
            "name": "Mia Member", "email": member_email, "password": password, "invite_token": raw_member_invite,
        }),
        200, "POST /auth/signup (member, via invite)",
    )
    member_token = member_signup["token"]

    forbidden = client.post(
        f"/orgs/{org_id}/invitations", json={"email": "someone@example.com", "role": "viewer"}, headers=_auth(member_token),
    )
    _assert(forbidden.status_code == 403, f"member was NOT refused inviting — got {forbidden.status_code}: {forbidden.text}")
    T.ok("member correctly refused (403) trying to invite")

    allowed = client.post(
        f"/orgs/{org_id}/invitations", json={"email": f"demo+viewer+{RUN_TAG}@example.com", "role": "viewer"},
        headers=_auth(admin_token),
    )
    _assert(allowed.status_code == 200, f"admin was refused inviting — got {allowed.status_code}: {allowed.text}")
    T.ok("A4 passes: admin succeeds, member is refused")

    # ── Stage 4 — Connect a real agent through the SDK ──────────────
    T.stage(4, "Connect a real agent through the SDK")
    # NOTE: the Phase 4 plan's own Stage 4 example writes "steps:write" —
    # the real scope POST /steps enforces (main.py's require_scope call
    # on that route) is "logs:write". Using the scope that actually
    # exists rather than reproducing the plan's typo verbatim, since this
    # script has to really work against the real backend, not just read
    # like the plan.
    api_key_resp = _check(
        client.post(
            "/api-keys", json={"scopes": ["runs:read", "logs:write", "agents:register"]},
            headers=_auth(owner_token),
        ),
        200, "POST /api-keys",
    )
    api_key = api_key_resp["raw_key"]
    T.ok(f"issued API key ...{api_key[-4:]}")

    from examples.support_agent.agent import SupportAgent

    agent = SupportAgent.connect(api_key=api_key, base_url=base_url)
    try:
        receipt = agent.answer("Where is my refund for order 4471?")
        _assert(receipt.step_id is not None, f"log_and_wait did not return a real step_id: {receipt}")
        T.ok(f"logged step_id={receipt.step_id} via TrustChain.log_and_wait")

        # The anchor-worker batches steps rather than anchoring each one
        # instantly (that's the entire point of Merkle-batch anchoring —
        # see ADR-0002); GET /steps/{id}/proof 404s until this step's
        # batch has actually been submitted and indexed. Poll rather than
        # assume it's already anchored a few hundred ms after log_and_wait
        # returned. agent.tc was built with on_error="raise" (deliberate —
        # see SupportAgent.connect's own comment), so a 404 raises here
        # rather than returning None; catch it per attempt instead of
        # fighting that design.
        import trustchain_sdk

        T.step("waiting for the anchor-worker to batch and anchor this step...")
        proof = None
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            try:
                proof = agent.tc.get_proof(receipt.step_id)
            except trustchain_sdk.TrustChainError:
                proof = None
            if proof is not None:
                break
            time.sleep(2.0)
        _assert(proof is not None, "step never got anchored (get_proof kept 404ing) within 60s")
        verifies = agent.tc.verify_proof(proof)
        _assert(verifies, "local Merkle proof verification failed")
        T.ok("proof fetched and verifies locally")

        from examples.support_agent.agent import _make_classify_intent_audited

        classify_intent = _make_classify_intent_audited(agent)
        intent = classify_intent("What's your return policy?")
        T.ok(f"logged a second step via @tc.audited (non-blocking): intent={intent!r}")
        time.sleep(1.0)  # let the background queue actually flush before stage 5 counts steps
    finally:
        agent.close()
    T.ok("A5 passes: real step_id, proof verifies, step appears via the SDK")

    # ── Stage 5 — Read the statistics ───────────────────────────────
    T.stage(5, "Read the statistics")
    # NOTE: the Phase 4 plan's own Stage 5 example calls GET /trust-scores
    # with no query params — the real endpoint 422s without one (it's
    # `run_id`-scoped, see sdk/python/trustchain_sdk/client.py's
    # trust_scores()). proof.run_id (from stage 4, the SDK's own
    # auto-generated per-agent run id) is a real one this project
    # actually has data for.
    for path, params in (
        ("/stats", {}), ("/runs", {}), ("/trust-scores", {"run_id": proof.run_id}),
        ("/leaderboard", {}), ("/gas-spend", {}), ("/integrity/status", {}),
    ):
        resp = client.get(path, params=params, headers=_auth(owner_token))
        _assert(resp.status_code == 200, f"GET {path}: expected 200, got {resp.status_code}: {resp.text[:300]}")
        T.step(f"{path} -> 200")
    T.ok("A6 passes: every statistics endpoint returns data")

    # ── Stage 6 — Tamper, attributed to a real person ───────────────
    T.stage(6, "Tamper, attributed to a real person")
    operator_name, operator_password = _create_db_operator(f"demo{RUN_TAG}", "Demo Operator")
    T.ok(f"issued individual db credential: {operator_name}")

    _tamper_step_row(operator_name, operator_password, receipt.step_id)
    T.ok(f"tampered with step {receipt.step_id}'s output_hash via a raw SQL UPDATE as {operator_name}")

    T.step("waiting for the integrity watchdog's next sweep...")
    tampered_alert = _poll_for_alert(client, owner_token, "step_row_tampered", timeout=90.0)
    _assert(tampered_alert is not None, "no step_row_tampered alert appeared within the timeout")
    T.ok(f"A7 passes: alert #{tampered_alert['id']} appeared with no application involvement")

    # ── Stage 7 — The email, and establishing what changed ──────────
    T.stage(7, "The email, and establishing what changed")
    evidence = tampered_alert.get("evidence", {})
    _assert(evidence.get("editedByOperator") == "Demo Operator", f"editedByOperator wrong/missing: {evidence}")
    _assert(evidence.get("editedByDbRole") == operator_name, f"editedByDbRole wrong/missing: {evidence}")
    _assert("output_hash" in evidence.get("changedColumns", []), f"changedColumns missing output_hash: {evidence}")
    _assert("oldOutputHash" in evidence and "newOutputHash" in evidence, f"hash pair missing: {evidence}")
    T.ok("alert evidence carries editedByOperator, editedByDbRole, and both hashes")

    # The alert-email sender loop (notifications/sender.py) runs
    # in-process inside integrity_watchdog.main, a SEPARATE process from
    # the API server — its console-backend log lines land in
    # .logs/integrity_watchdog.log, not .logs/fastapi.log (see
    # start.sh's "Integrity watchdog + alert-email sender" section and
    # docker-compose.yml's integrity-watchdog service, which run the two
    # in one process specifically so they share one delivery
    # transaction — see integrity_watchdog/main.py's own docstring).
    both_emailed = _confirm_both_recipients_emailed(watchdog_log_file, owner_email, admin_email, "editedByOperator")
    _assert(both_emailed, "did not see the tamper alert queued to BOTH owner and admin in the console log")
    T.ok("A8 passes: both owner and admin were emailed, naming the operator")

    original_text = "Your refund of $50 was issued on 12 August and should arrive by Friday."
    verify_true = _check(
        client.post(
            "/integrity/verify-content",
            json={"stepId": receipt.step_id, "field": "output", "candidateText": original_text},
            headers=_auth(owner_token),
        ),
        200, "POST /integrity/verify-content (true original)",
    )
    _assert(verify_true["matchesCurrent"] is False, f"expected matchesCurrent=false, got {verify_true}")
    _assert(verify_true["matchesOriginal"] is True, f"expected matchesOriginal=true, got {verify_true}")

    verify_false = _check(
        client.post(
            "/integrity/verify-content",
            json={"stepId": receipt.step_id, "field": "output", "candidateText": "a made-up wrong guess"},
            headers=_auth(owner_token),
        ),
        200, "POST /integrity/verify-content (wrong guess)",
    )
    _assert(verify_false["matchesCurrent"] is False and verify_false["matchesOriginal"] is False, f"wrong guess should be false/false: {verify_false}")
    T.ok("A9 passes: true original confirms, wrong guess is rejected")

    # ── Stage 8 — Deletion, and tenant isolation ─────────────────────
    T.stage(8, "Deletion, and tenant isolation")
    _delete_step_row(operator_name, operator_password, receipt.step_id)
    # NOTE: a DELETEd step that's still referenced by an anchored batch's
    # leaf_order is caught by the merkle-roots detector (sweep_merkle_roots),
    # not the row-hash-mismatch one — its alert_type is "step_missing",
    # distinct from "step_row_tampered" (an edit). Its evidence carries
    # per-step attribution under "deletionForensics" (integrity_watchdog/
    # main.py — this repo's own watchdog code didn't wire that up before
    # this Phase 4 pass; see that file's own comment on the fix).
    delete_alert = _poll_for_alert(client, owner_token, "step_missing", timeout=90.0)
    _assert(delete_alert is not None, "no step_missing alert appeared for the deleted step")
    forensics = delete_alert.get("evidence", {}).get("deletionForensics", {}).get(str(receipt.step_id), {})
    _assert(forensics.get("editedByOperator") == "Demo Operator", f"deletion not attributed: {delete_alert}")
    T.ok(f"deletion attributed to {forensics.get('editedByOperator')!r}: alert #{delete_alert['id']}")

    other_signup = _check(
        client.post("/auth/signup", json={
            "name": "Other Owner", "email": other_org_email, "password": password,
            "org_name": f"Other Org {RUN_TAG}", "project_name": "Unrelated",
        }),
        200, "POST /auth/signup (second org)",
    )
    other_token = other_signup["token"]

    other_runs = _check(client.get("/runs", headers=_auth(other_token)), 200, "GET /runs (other org)")
    _assert(other_runs.get("runs", []) == [], f"second org can see the first org's runs: {other_runs}")
    other_alerts = _check(client.get("/alerts", headers=_auth(other_token)), 200, "GET /alerts (other org)")
    _assert(other_alerts.get("alerts", []) == [], f"second org can see the first org's alerts: {other_alerts}")

    cross_tenant_verify = client.post(
        "/integrity/verify-content",
        json={"stepId": receipt.step_id, "field": "output", "candidateText": "anything"},
        headers=_auth(other_token),
    )
    _assert(
        cross_tenant_verify.status_code == 404,
        f"cross-tenant verify-content should 404 (never 403 — that would confirm the step exists), "
        f"got {cross_tenant_verify.status_code}",
    )
    T.ok("A10 passes: second org sees nothing, cross-tenant lookup 404s")

    print(f"\n{'=' * 70}\nALL STAGES PASSED — run tag {RUN_TAG}\n{'=' * 70}")


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_db_operator(short_name: str, display_name: str) -> tuple[str, str]:
    import subprocess

    result = subprocess.run(
        [sys.executable, "scripts/db_operator.py", "create", short_name, "--display-name", display_name],
        cwd=str(REPO_ROOT / "backend"), capture_output=True, text=True, timeout=30,
    )
    _assert(result.returncode == 0, f"db_operator.py create failed: {result.stderr}")
    m = re.search(r"Password \(SHOWN ONCE.*?\): (\S+)", result.stdout)
    _assert(m is not None, f"could not parse operator password from db_operator.py output:\n{result.stdout}")
    role_match = re.search(r"Created role: (\S+)", result.stdout)
    _assert(role_match is not None, f"could not parse operator role name:\n{result.stdout}")
    return role_match.group(1), m.group(1)


def _db_connect_kwargs() -> dict:
    """Same defaults docker-compose.yml's postgres service and
    backend/.env.example use for local dev — overridable via
    DATABASE_HOST/DATABASE_PORT/DATABASE_NAME if your setup differs."""
    import os

    return {
        "host": os.environ.get("DATABASE_HOST", "localhost"),
        "port": int(os.environ.get("DATABASE_PORT", "5432")),
        "database": os.environ.get("DATABASE_NAME", "trustchain"),
    }


def _tamper_step_row(role: str, password: str, step_id: int) -> None:
    import asyncio

    import asyncpg

    async def _do():
        conn = await asyncpg.connect(user=role, password=password, **_db_connect_kwargs())
        try:
            await conn.execute(
                "UPDATE steps SET output_hash = '0x' || repeat('f', 64) WHERE id = $1", step_id,
            )
        finally:
            await conn.close()

    asyncio.run(_do())


def _delete_step_row(role: str, password: str, step_id: int) -> None:
    import asyncio

    import asyncpg

    async def _do():
        conn = await asyncpg.connect(user=role, password=password, **_db_connect_kwargs())
        try:
            await conn.execute("DELETE FROM anchor_outbox WHERE step_id = $1", step_id)
            await conn.execute("DELETE FROM steps WHERE id = $1", step_id)
        finally:
            await conn.close()

    asyncio.run(_do())


def _poll_for_alert(client: httpx.Client, token: str, alert_type: str, timeout: float) -> Optional[dict]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get("/alerts", params={"status": "open", "limit": 50}, headers=_auth(token))
        if resp.status_code == 200:
            for alert in resp.json().get("alerts", []):
                if alert.get("alertType") == alert_type:
                    return alert
        time.sleep(3.0)
    return None


def _confirm_both_recipients_emailed(
    log_file: Path, owner_email: str, admin_email: str, marker: str, timeout: float = 90.0,
) -> bool:
    """Polls rather than checking once — integrity_watchdog/main.py's own
    loop raises the alert (visible via GET /alerts, and to _poll_for_alert
    above, the moment that transaction commits) and only THEN, after the
    rest of that same run_cycle() finishes, calls sender_run_once() to
    actually send the queued emails (see that file's main() — sequential
    by design, not concurrent). A alert can be visible via the API well
    before its email is actually sent; checking the log exactly once
    right after _poll_for_alert returns is a real race, not a hypothetical
    one — caught by an actual flaky run of this exact script.

    log_file == /dev/null (real EMAIL_BACKEND, no console log to tail):
    unlike the token-extraction helpers above, this confirmation needs no
    secret — just "did both addresses actually get an email" — so the
    /dev/null fallback is a plain stdin y/n prompt rather than pasting
    anything back in."""
    if log_file == _DEV_NULL:
        print("\n  ⏸ stuck: can't tail a console log in real-backend mode")
        print(f"    Check both {owner_email} and {admin_email} for the tamper-alert email — got both? [y/N]: ", end="", flush=True)
        return sys.stdin.readline().strip().lower() in ("y", "yes")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if log_file.exists():
            text = log_file.read_text(errors="replace")
            lines = text.splitlines()
            seen_owner = any(owner_email in line and marker in line for line in lines)
            seen_admin = any(admin_email in line and marker in line for line in lines)
            if seen_owner and seen_admin:
                return True
        time.sleep(3.0)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument(
        "--log-file", default=str(REPO_ROOT / ".logs" / "fastapi.log"),
        help="Path to the API server's own stdout log (./start.sh writes this by default). "
             "Only used when EMAIL_BACKEND=console (the default) — see this module's docstring.",
    )
    parser.add_argument(
        "--watchdog-log-file", default=str(REPO_ROOT / ".logs" / "integrity_watchdog.log"),
        help="Path to the integrity-watchdog process's own stdout log — the alert-email sender "
             "loop runs inside THIS process, not the API server, so tamper-alert emails land here.",
    )
    args = parser.parse_args()

    try:
        run(args.base_url, Path(args.log_file), Path(args.watchdog_log_file))
    except DemoFailure as e:
        T.fail(str(e))
        return 1
    except httpx.HTTPError as e:
        T.fail(f"HTTP error talking to {args.base_url}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

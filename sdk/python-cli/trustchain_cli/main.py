"""
trustchain_cli.main — `trustchain` command-line tool.

Credential resolution, in priority order: --token/--api-key flag >
TRUSTCHAIN_TOKEN/TRUSTCHAIN_API_KEY env var > cached `trustchain login`
token. The backend accepts EITHER a session JWT or an API key in the
same Authorization header (see backend/auth.py's get_current_principal),
so this CLI never needs to know or care which kind of credential it's
holding — same code path either way.
"""

import argparse
import getpass
import json
import sys
from typing import Optional

import httpx

from trustchain_cli import credentials
from trustchain_sdk import TrustChain, TrustChainClient, TrustChainError

DEFAULT_BASE_URL = "http://localhost:8000"


def _resolve_base_url(args: argparse.Namespace) -> str:
    import os
    if args.base_url:
        return args.base_url
    if os.environ.get("TRUSTCHAIN_BASE_URL"):
        return os.environ["TRUSTCHAIN_BASE_URL"]
    cached = credentials.load()
    if cached and cached.get("base_url"):
        return cached["base_url"]
    return DEFAULT_BASE_URL


def _resolve_credential(args: argparse.Namespace) -> str:
    import os
    if getattr(args, "token", None):
        return args.token
    if getattr(args, "api_key", None):
        return args.api_key
    if os.environ.get("TRUSTCHAIN_TOKEN"):
        return os.environ["TRUSTCHAIN_TOKEN"]
    if os.environ.get("TRUSTCHAIN_API_KEY"):
        return os.environ["TRUSTCHAIN_API_KEY"]
    cached_token = credentials.load_token()
    if cached_token:
        return cached_token
    print(
        "error: no credential — run `trustchain login`, or pass --token/--api-key, "
        "or set TRUSTCHAIN_TOKEN/TRUSTCHAIN_API_KEY",
        file=sys.stderr,
    )
    sys.exit(1)


def _print_json(value) -> None:
    print(json.dumps(value, indent=2, default=str))


# ── auth ──────────────────────────────────────────────────────────────

def cmd_login(args: argparse.Namespace) -> None:
    base_url = _resolve_base_url(args)
    password = args.password or getpass.getpass(f"Password for {args.email}: ")
    response = httpx.post(f"{base_url}/auth/login", json={"email": args.email, "password": password}, timeout=10.0)
    if response.status_code != 200:
        print(f"error: login failed ({response.status_code}): {response.text}", file=sys.stderr)
        sys.exit(1)
    body = response.json()
    credentials.save_token(body["token"], body["email"], base_url)
    print(f"Logged in as {body['email']} ({base_url})")


def cmd_logout(args: argparse.Namespace) -> None:
    credentials.clear()
    print("Logged out.")


# ── keys ──────────────────────────────────────────────────────────────

def cmd_keys_create(args: argparse.Namespace) -> None:
    base_url = _resolve_base_url(args)
    token = _resolve_credential(args)
    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
    response = httpx.post(
        f"{base_url}/api-keys",
        json={"scopes": scopes, "environment": args.environment},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
    if response.status_code != 200:
        print(f"error: {response.status_code}: {response.text}", file=sys.stderr)
        sys.exit(1)
    body = response.json()
    print(f"Created key (id={body['id']}, scopes={body['scopes']}):")
    print(f"  {body['raw_key']}")
    print("Shown exactly once — store it now.")


def cmd_keys_list(args: argparse.Namespace) -> None:
    base_url = _resolve_base_url(args)
    token = _resolve_credential(args)
    response = httpx.get(f"{base_url}/api-keys", headers={"Authorization": f"Bearer {token}"}, timeout=10.0)
    if response.status_code != 200:
        print(f"error: {response.status_code}: {response.text}", file=sys.stderr)
        sys.exit(1)
    _print_json(response.json())


def cmd_keys_revoke(args: argparse.Namespace) -> None:
    base_url = _resolve_base_url(args)
    token = _resolve_credential(args)
    response = httpx.delete(f"{base_url}/api-keys/{args.key_id}", headers={"Authorization": f"Bearer {token}"}, timeout=10.0)
    if response.status_code != 200:
        print(f"error: {response.status_code}: {response.text}", file=sys.stderr)
        sys.exit(1)
    print(f"Revoked key {args.key_id}.")


# ── runs ──────────────────────────────────────────────────────────────

def cmd_runs_list(args: argparse.Namespace) -> None:
    with TrustChainClient(_resolve_credential(args), base_url=_resolve_base_url(args)) as client:
        _print_json(client.list_runs(limit=args.limit))


def cmd_runs_get(args: argparse.Namespace) -> None:
    with TrustChainClient(_resolve_credential(args), base_url=_resolve_base_url(args)) as client:
        try:
            _print_json(client.get_run(args.run_id))
        except TrustChainError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(1)


# ── verify — the flagship command ────────────────────────────────────

def cmd_verify(args: argparse.Namespace) -> None:
    """`trustchain verify <run-id>` — fetches every anchored step for a
    run and verifies each one's Merkle proof LOCALLY (no trust in the
    API's own say-so beyond the leaf/proof/root values themselves —
    same reasoning as TrustChain.verify_proof, see the SDK). Exits
    non-zero if any step fails to verify or isn't anchored yet."""
    with TrustChainClient(_resolve_credential(args), base_url=_resolve_base_url(args)) as client:
        try:
            audit = client.audit_log(args.run_id)
        except TrustChainError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(1)

        entries = audit["entries"]
        if not entries:
            print(f"No audit entries found for run '{args.run_id}'.")
            sys.exit(1)

        all_ok = True
        for entry in entries:
            step_id = entry["entryId"]
            label = f"step {step_id} ({entry['agentId']}/{entry['action']})"
            if entry["anchorStatus"] != "confirmed":
                print(f"  PENDING  {label} — anchorStatus={entry['anchorStatus']!r}")
                all_ok = False
                continue
            try:
                proof_data = client.get_step_proof(step_id)
            except TrustChainError as e:
                print(f"  ERROR    {label} — could not fetch proof: {e}")
                all_ok = False
                continue

            from trustchain_sdk.merkle import hex_to_bytes
            from trustchain_sdk.merkle import verify_proof as verify_proof_locally

            ok = verify_proof_locally(
                hex_to_bytes(proof_data["leaf"]),
                [hex_to_bytes(p) for p in proof_data["proof"]],
                hex_to_bytes(proof_data["root"]),
            )
            status = "VERIFIED" if ok else "FAILED"
            print(f"  {status:8} {label} — tx={proof_data.get('txHash')}")
            all_ok = all_ok and ok

        print()
        if all_ok:
            print(f"All {len(entries)} step(s) for run '{args.run_id}' verified.")
        else:
            print(f"Verification FAILED for run '{args.run_id}' — see above.")
            sys.exit(1)


# ── agents ────────────────────────────────────────────────────────────

def cmd_agents_list(args: argparse.Namespace) -> None:
    with TrustChainClient(_resolve_credential(args), base_url=_resolve_base_url(args)) as client:
        _print_json(client.list_agents(include_revoked=args.include_revoked))


def cmd_agents_verify(args: argparse.Namespace) -> None:
    tc = TrustChain(_resolve_credential(args), base_url=_resolve_base_url(args), on_error="raise")
    try:
        result = tc.verify_agent(
            agent_id=args.agent_id, model=args.model, version=args.version, system_prompt=args.system_prompt,
        )
    except TrustChainError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    _print_json(vars(result))
    if not result.verified:
        sys.exit(1)


def cmd_agents_register(args: argparse.Namespace) -> None:
    tc = TrustChain(_resolve_credential(args), base_url=_resolve_base_url(args), on_error="raise")
    try:
        tx_hash = tc.register_agent(
            agent_id=args.agent_id, model=args.model, version=args.version, system_prompt=args.system_prompt,
        )
    except TrustChainError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Registered '{args.agent_id}' — tx {tx_hash}")


# ── Phase 3: organizations & members ─────────────────────────────────
# Raw httpx calls, not TrustChainClient — these are human-session-scoped
# admin operations (org/member management is JWT-only on the backend,
# see main.py's Phase 3 section docstring), same pattern `cmd_keys_*`
# above already uses for the same reason.

def _authed_request(args: argparse.Namespace, method: str, path: str, **kwargs) -> dict:
    base_url = _resolve_base_url(args)
    token = _resolve_credential(args)
    response = httpx.request(method, f"{base_url}{path}", headers={"Authorization": f"Bearer {token}"}, timeout=10.0, **kwargs)
    if response.status_code >= 400:
        print(f"error: {response.status_code}: {response.text}", file=sys.stderr)
        sys.exit(1)
    return response.json()


def cmd_org_list(args: argparse.Namespace) -> None:
    _print_json(_authed_request(args, "GET", "/orgs"))


def cmd_org_create(args: argparse.Namespace) -> None:
    _print_json(_authed_request(args, "POST", "/orgs", json={"name": args.name, "project_name": args.project_name}))


def cmd_org_rename(args: argparse.Namespace) -> None:
    _authed_request(args, "PATCH", f"/orgs/{args.org_id}", json={"name": args.name})
    print(f"Renamed org {args.org_id}.")


def cmd_org_members(args: argparse.Namespace) -> None:
    _print_json(_authed_request(args, "GET", f"/orgs/{args.org_id}/members"))


def cmd_member_invite(args: argparse.Namespace) -> None:
    result = _authed_request(
        args, "POST", f"/orgs/{args.org_id}/invitations", json={"email": args.email, "role": args.role},
    )
    print(f"Invited {args.email} to org {args.org_id} as {args.role} (invitation id={result['id']}).")


def cmd_member_list(args: argparse.Namespace) -> None:
    _print_json(_authed_request(args, "GET", f"/orgs/{args.org_id}/members"))


def cmd_member_set_role(args: argparse.Namespace) -> None:
    _authed_request(args, "PATCH", f"/orgs/{args.org_id}/members/{args.user_id}", json={"role": args.role})
    print(f"Set user {args.user_id}'s role to {args.role} in org {args.org_id}.")


def cmd_member_remove(args: argparse.Namespace) -> None:
    _authed_request(args, "DELETE", f"/orgs/{args.org_id}/members/{args.user_id}")
    print(f"Removed user {args.user_id} from org {args.org_id}.")


# ── Phase 3: alerts & integrity ──────────────────────────────────────

def cmd_alerts_list(args: argparse.Namespace) -> None:
    params = {"limit": args.limit}
    if args.status:
        params["status"] = args.status
    if args.severity:
        params["severity"] = args.severity
    _print_json(_authed_request(args, "GET", "/alerts", params=params))


def cmd_alerts_ack(args: argparse.Namespace) -> None:
    _authed_request(args, "POST", f"/alerts/{args.alert_id}/acknowledge", json={})
    print(f"Acknowledged alert {args.alert_id}.")


def cmd_alerts_resolve(args: argparse.Namespace) -> None:
    _authed_request(args, "POST", f"/alerts/{args.alert_id}/resolve", json={"resolution_note": args.note or ""})
    print(f"Resolved alert {args.alert_id}.")


def cmd_integrity_status(args: argparse.Namespace) -> None:
    _print_json(_authed_request(args, "GET", "/integrity/status"))


def cmd_integrity_verify_run(args: argparse.Namespace) -> None:
    _print_json(_authed_request(args, "POST", f"/integrity/verify-run/{args.run_id}", json={}))


def cmd_integrity_verify_content(args: argparse.Namespace) -> None:
    """Phase 4 G3 — the CLI-reachable form of POST /integrity/verify-content.
    Candidate text comes from --file or stdin, never a bare positional
    argument: real agent input/output is multi-line and full of shell
    metacharacters (quotes, backticks, $-expansions) that would not
    survive being pasted directly into a command line intact."""
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            candidate_text = f.read()
    else:
        candidate_text = sys.stdin.read()
    if not candidate_text:
        print("error: no candidate text given — pass --file PATH or pipe it via stdin", file=sys.stderr)
        sys.exit(1)

    _print_json(_authed_request(
        args, "POST", "/integrity/verify-content",
        json={"stepId": args.step_id, "field": args.field, "candidateText": candidate_text},
    ))


# ── agents verify/sync --manifest — the CI integrity gate (plan §11) ──

def _load_manifest(path: str) -> dict:
    import yaml
    with open(path) as f:
        manifest = yaml.safe_load(f)
    if "agents" not in manifest:
        print(f"error: {path} has no top-level 'agents:' list", file=sys.stderr)
        sys.exit(1)
    return manifest


def _agent_from_manifest_entry(entry: dict) -> dict:
    """system_prompt_file is read relative to CWD (the CI job's checkout
    root) — same convention as any other file path in a CI config."""
    prompt = entry.get("system_prompt", "")
    if "system_prompt_file" in entry:
        with open(entry["system_prompt_file"]) as f:
            prompt = f.read()
    return {"id": entry["id"], "model": entry["model"], "version": str(entry["version"]), "system_prompt": prompt}


def cmd_agents_verify_manifest(args: argparse.Namespace) -> None:
    """`trustchain agents verify-manifest --manifest trustchain.yaml` (plan §11.2)
    — checks every agent in the manifest against its registered on-chain
    identity and exits non-zero if ANY differs, so a CI job fails the
    build the moment a model/version/prompt change reaches a PR without
    a matching `agents sync`."""
    manifest = _load_manifest(args.manifest)
    tc = TrustChain(_resolve_credential(args), base_url=_resolve_base_url(args), on_error="raise")

    any_drift = False
    for entry in manifest["agents"]:
        agent = _agent_from_manifest_entry(entry)
        try:
            result = tc.verify_agent(**agent)
        except TrustChainError as e:
            print(f"  ERROR   {agent['id']:20} could not verify: {e}")
            any_drift = True
            continue
        if result is None:
            print(f"  ERROR   {agent['id']:20} verification failed")
            any_drift = True
        elif result.hash_matches:
            print(f"  OK      {agent['id']:20} {result.provided_hash}")
        else:
            print(f"  DRIFT   {agent['id']:20} registered={result.stored_hash}  local={result.provided_hash}")
            any_drift = True

    print()
    if any_drift:
        print("One or more agents differ from their registered on-chain identity.")
        print(f"If this is intentional, run: trustchain agents sync-manifest --manifest {args.manifest} --reason \"...\"")
        sys.exit(1)
    print(f"All {len(manifest['agents'])} agent(s) match their registered identity.")


def cmd_agents_sync_manifest(args: argparse.Namespace) -> None:
    """`trustchain agents sync-manifest --manifest trustchain.yaml --reason "..."`
    (plan §11.2) — registers every agent in the manifest, unconditionally
    (declare_agent's local-cache-only idempotency doesn't apply here,
    since a fresh CI job has no warm cache — the backend's own
    AgentUpdated-only-on-real-change behavior is what keeps a genuinely
    unchanged agent from generating a spurious event). --reason is logged
    here and by the backend's access log for correlating a specific
    agent_identity_changed alert against a specific deploy by timestamp
    — see indexer/agent_events.py's _raise_identity_alert docstring for
    why it is NOT currently threaded into the alert's own evidence."""
    manifest = _load_manifest(args.manifest)
    tc = TrustChain(_resolve_credential(args), base_url=_resolve_base_url(args), on_error="raise")

    for entry in manifest["agents"]:
        agent = _agent_from_manifest_entry(entry)
        try:
            tx_hash = tc.register_agent(**agent)
        except TrustChainError as e:
            print(f"  ERROR   {agent['id']:20} {e}", file=sys.stderr)
            sys.exit(1)
        reason = f" — reason: {args.reason}" if args.reason else ""
        print(f"  synced  {agent['id']:20} tx={tx_hash}{reason}")


# ── dev — local docker-compose stack helpers ─────────────────────────

def _find_compose_file() -> Optional["object"]:
    from pathlib import Path
    directory = Path.cwd()
    for _ in range(6):
        candidate = directory / "docker-compose.yml"
        if candidate.exists():
            return candidate
        if directory.parent == directory:
            break
        directory = directory.parent
    return None


def _run_compose(args_list: list[str]) -> None:
    import subprocess
    compose_file = _find_compose_file()
    if compose_file is None:
        print("error: no docker-compose.yml found in this directory or any parent", file=sys.stderr)
        sys.exit(1)
    cmd = ["docker", "compose", "-f", str(compose_file), *args_list]
    result = subprocess.run(cmd)
    sys.exit(result.returncode)


def cmd_dev_up(args: argparse.Namespace) -> None:
    _run_compose(["up", "-d"])


def cmd_dev_down(args: argparse.Namespace) -> None:
    _run_compose(["down"])


def cmd_dev_status(args: argparse.Namespace) -> None:
    _run_compose(["ps"])


def cmd_dev_logs(args: argparse.Namespace) -> None:
    extra = [args.service] if args.service else []
    _run_compose(["logs", "-f", "--tail", "100", *extra])


# ── argument parser ───────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trustchain", description="TrustChain CLI")
    parser.add_argument("--base-url", default=None, help=f"API base URL (default {DEFAULT_BASE_URL})")
    parser.add_argument("--token", default=None, help="Session JWT (overrides cached login / env vars)")
    parser.add_argument("--api-key", default=None, help="API key (overrides cached login / env vars)")

    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="Log in and cache a session token")
    p_login.add_argument("email")
    p_login.add_argument("--password", default=None, help="Prompted for if omitted")
    p_login.set_defaults(func=cmd_login)

    p_logout = sub.add_parser("logout", help="Clear the cached session token")
    p_logout.set_defaults(func=cmd_logout)

    p_keys = sub.add_parser("keys", help="Manage API keys").add_subparsers(dest="keys_command", required=True)
    p_keys_create = p_keys.add_parser("create", help="Create an API key")
    p_keys_create.add_argument("--scopes", required=True, help="Comma-separated, e.g. runs:read,runs:write")
    p_keys_create.add_argument("--environment", default="live", choices=["live", "test"])
    p_keys_create.set_defaults(func=cmd_keys_create)
    p_keys_list = p_keys.add_parser("list", help="List API keys")
    p_keys_list.set_defaults(func=cmd_keys_list)
    p_keys_revoke = p_keys.add_parser("revoke", help="Revoke an API key")
    p_keys_revoke.add_argument("key_id", type=int)
    p_keys_revoke.set_defaults(func=cmd_keys_revoke)

    p_runs = sub.add_parser("runs", help="Inspect runs").add_subparsers(dest="runs_command", required=True)
    p_runs_list = p_runs.add_parser("list", help="List recent runs")
    p_runs_list.add_argument("--limit", type=int, default=50)
    p_runs_list.set_defaults(func=cmd_runs_list)
    p_runs_get = p_runs.add_parser("get", help="Fetch one run's result")
    p_runs_get.add_argument("run_id")
    p_runs_get.set_defaults(func=cmd_runs_get)

    p_verify = sub.add_parser("verify", help="Verify every anchored step's Merkle proof for a run")
    p_verify.add_argument("run_id")
    p_verify.set_defaults(func=cmd_verify)

    p_agents = sub.add_parser("agents", help="Manage agent identities").add_subparsers(
        dest="agents_command", required=True
    )
    p_agents_list = p_agents.add_parser("list", help="List registered agents in the current project")
    p_agents_list.add_argument("--include-revoked", action="store_true", help="Also show revoked agents")
    p_agents_list.set_defaults(func=cmd_agents_list)
    p_agents_verify = p_agents.add_parser("verify", help="Live-verify an agent's registered fingerprint")
    p_agents_verify.add_argument("agent_id")
    p_agents_verify.add_argument("--model", required=True)
    p_agents_verify.add_argument("--version", required=True)
    p_agents_verify.add_argument("--system-prompt", required=True)
    p_agents_verify.set_defaults(func=cmd_agents_verify)
    p_agents_register = p_agents.add_parser("register", help="Register an agent's fingerprint")
    p_agents_register.add_argument("agent_id")
    p_agents_register.add_argument("--model", required=True)
    p_agents_register.add_argument("--version", required=True)
    p_agents_register.add_argument("--system-prompt", required=True)
    p_agents_register.set_defaults(func=cmd_agents_register)
    # Manifest-based bulk check/sync (Phase 3 §11's CI integrity gate) —
    # a SEPARATE subparser rather than an optional --manifest flag on
    # `agents verify` above: that command's agent_id/--model/--version/
    # --system-prompt are all required positional/flags for the single-
    # agent form, which argparse can't cleanly make mutually exclusive
    # with a completely different multi-agent input shape.
    p_agents_verify_manifest = p_agents.add_parser(
        "verify-manifest", help="Verify every agent in a trustchain.yaml manifest (CI gate)",
    )
    p_agents_verify_manifest.add_argument("--manifest", required=True, help="Path to trustchain.yaml")
    p_agents_verify_manifest.set_defaults(func=cmd_agents_verify_manifest)
    p_agents_sync_manifest = p_agents.add_parser(
        "sync-manifest", help="Register every agent in a trustchain.yaml manifest",
    )
    p_agents_sync_manifest.add_argument("--manifest", required=True, help="Path to trustchain.yaml")
    p_agents_sync_manifest.add_argument("--reason", default=None, help="Logged alongside the registration (e.g. a deploy SHA)")
    p_agents_sync_manifest.set_defaults(func=cmd_agents_sync_manifest)

    p_org = sub.add_parser("org", help="Manage organizations").add_subparsers(dest="org_command", required=True)
    p_org_list = p_org.add_parser("list", help="List organizations you belong to")
    p_org_list.set_defaults(func=cmd_org_list)
    p_org_create = p_org.add_parser("create", help="Create a new organization")
    p_org_create.add_argument("name")
    p_org_create.add_argument("--project-name", default="Default")
    p_org_create.set_defaults(func=cmd_org_create)
    p_org_rename = p_org.add_parser("rename", help="Rename an organization")
    p_org_rename.add_argument("org_id", type=int)
    p_org_rename.add_argument("name")
    p_org_rename.set_defaults(func=cmd_org_rename)
    p_org_members = p_org.add_parser("members", help="List an organization's members")
    p_org_members.add_argument("org_id", type=int)
    p_org_members.set_defaults(func=cmd_org_members)

    p_member = sub.add_parser("member", help="Manage organization membership").add_subparsers(
        dest="member_command", required=True
    )
    p_member_invite = p_member.add_parser("invite", help="Invite someone to an organization")
    p_member_invite.add_argument("org_id", type=int)
    p_member_invite.add_argument("email")
    p_member_invite.add_argument("--role", required=True, choices=["admin", "member", "viewer"])
    p_member_invite.set_defaults(func=cmd_member_invite)
    p_member_list = p_member.add_parser("list", help="List an organization's members")
    p_member_list.add_argument("org_id", type=int)
    p_member_list.set_defaults(func=cmd_member_list)
    p_member_set_role = p_member.add_parser("set-role", help="Change a member's role")
    p_member_set_role.add_argument("org_id", type=int)
    p_member_set_role.add_argument("user_id", type=int)
    p_member_set_role.add_argument("--role", required=True, choices=["admin", "member", "viewer"])
    p_member_set_role.set_defaults(func=cmd_member_set_role)
    p_member_remove = p_member.add_parser("remove", help="Remove a member")
    p_member_remove.add_argument("org_id", type=int)
    p_member_remove.add_argument("user_id", type=int)
    p_member_remove.set_defaults(func=cmd_member_remove)

    p_alerts = sub.add_parser("alerts", help="View and manage integrity alerts").add_subparsers(
        dest="alerts_command", required=True
    )
    p_alerts_list = p_alerts.add_parser("list", help="List alerts")
    p_alerts_list.add_argument("--status", default=None, choices=["open", "acknowledged", "resolved"])
    p_alerts_list.add_argument("--severity", default=None, choices=["critical", "warning", "info"])
    p_alerts_list.add_argument("--limit", type=int, default=50)
    p_alerts_list.set_defaults(func=cmd_alerts_list)
    p_alerts_ack = p_alerts.add_parser("ack", help="Acknowledge an alert")
    p_alerts_ack.add_argument("alert_id", type=int)
    p_alerts_ack.set_defaults(func=cmd_alerts_ack)
    p_alerts_resolve = p_alerts.add_parser("resolve", help="Resolve an alert")
    p_alerts_resolve.add_argument("alert_id", type=int)
    p_alerts_resolve.add_argument("--note", default=None)
    p_alerts_resolve.set_defaults(func=cmd_alerts_resolve)

    p_integrity = sub.add_parser("integrity", help="Continuous verification status").add_subparsers(
        dest="integrity_command", required=True
    )
    p_integrity_status = p_integrity.add_parser("status", help="Coverage/health of the active project")
    p_integrity_status.set_defaults(func=cmd_integrity_status)
    p_integrity_verify_run = p_integrity.add_parser("verify-run", help="Synchronously verify one run")
    p_integrity_verify_run.add_argument("run_id")
    p_integrity_verify_run.set_defaults(func=cmd_integrity_verify_run)
    p_integrity_verify_content = p_integrity.add_parser(
        "verify-content",
        help="Confirm/refute a candidate text against a step's stored hash (Phase 4 G3)",
    )
    p_integrity_verify_content.add_argument("step_id", type=int)
    p_integrity_verify_content.add_argument("field", choices=["input", "output"])
    p_integrity_verify_content.add_argument(
        "--file", default=None, help="Read candidate text from this file (default: read from stdin)",
    )
    p_integrity_verify_content.set_defaults(func=cmd_integrity_verify_content)

    p_dev = sub.add_parser("dev", help="Local docker-compose dev stack helpers").add_subparsers(
        dest="dev_command", required=True
    )
    p_dev_up = p_dev.add_parser("up", help="docker compose up -d")
    p_dev_up.set_defaults(func=cmd_dev_up)
    p_dev_down = p_dev.add_parser("down", help="docker compose down")
    p_dev_down.set_defaults(func=cmd_dev_down)
    p_dev_status = p_dev.add_parser("status", help="docker compose ps")
    p_dev_status.set_defaults(func=cmd_dev_status)
    p_dev_logs = p_dev.add_parser("logs", help="docker compose logs -f")
    p_dev_logs.add_argument("service", nargs="?", default=None)
    p_dev_logs.set_defaults(func=cmd_dev_logs)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

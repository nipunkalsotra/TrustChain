"""
scripts/db_operator.py — issue, list, and revoke individually-attributed
Postgres roles for humans doing manual database work.

WHY THIS EXISTS: anchor-worker/indexer/integrity-watchdog all correctly
share the `trustchain` superuser role (they're automated processes —
one consistent identity per process is right, not a gap). The actual
gap (ADR-0020, "database audit logging and attribution") is humans:
anyone running `psql`/manual queries has historically also connected as
`trustchain`, so steps_history.db_role — which faithfully records
session_user on every row — can only ever say "trustchain" no matter
which person it was. This script is the fix: each operator gets their
OWN role/password, so session_user actually distinguishes them.

Issued roles are SUPERUSER (same privilege level `trustchain` already
has — this is about INDIVIDUAL ACCOUNTABILITY for who did something, not
about reducing what any one operator can do; that's a separate,
narrower-permissions decision if ever wanted) and LOGIN (so they can
connect directly), with a freshly generated random password shown
EXACTLY ONCE at creation — same "shown once, never stored/logged again"
convention as ApiKey/Invitation raw tokens elsewhere in this codebase.
Postgres itself stores only the role's password hash; nothing about the
plaintext password is ever written to db_operators or anywhere else.

Usage (run from backend/, needs DATABASE_URL pointed at a real Postgres
— connects with the `trustchain` superuser's own credentials to have
authority to CREATE ROLE):
    python3 scripts/db_operator.py create nipun --display-name "Nipun Kalsotra"
    python3 scripts/db_operator.py list
    python3 scripts/db_operator.py revoke nipun
"""

import argparse
import asyncio
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg  # noqa: E402

from config import get_settings  # noqa: E402

_ROLE_PREFIX = "trustchain_op_"


def _role_name(short_name: str) -> str:
    # Postgres role names aren't case-sensitive-safe/space-safe by
    # default without quoting everywhere downstream — normalize hard so
    # a role name is always a safe bareword.
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in short_name.lower())
    return f"{_ROLE_PREFIX}{safe}"


def _dsn_for_asyncpg() -> str:
    # get_settings().database_url is a SQLAlchemy DSN
    # (postgresql+asyncpg://...) — asyncpg.connect() wants the plain
    # postgresql:// form.
    return get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def create(short_name: str, display_name: str) -> None:
    role = _role_name(short_name)
    password = secrets.token_urlsafe(24)
    now = int(time.time())

    conn = await asyncpg.connect(_dsn_for_asyncpg())
    try:
        current_role = await conn.fetchval("SELECT current_user")
        async with conn.transaction():
            # CREATE ROLE's PASSWORD clause doesn't accept a bind
            # parameter ($1) at all — Postgres's DDL grammar wants a
            # literal there, not a placeholder (found by actually running
            # this, not by reading the docs first). Dollar-quoted rather
            # than single-quoted so there's no escaping to get wrong;
            # safe here specifically because secrets.token_urlsafe()'s
            # output charset (A-Za-z0-9_-) can never contain a quote or
            # break out of the literal.
            await conn.execute(f'CREATE ROLE "{role}" LOGIN SUPERUSER PASSWORD $pw${password}$pw$')
            await conn.execute(
                "INSERT INTO db_operators (role_name, display_name, created_at, created_by) VALUES ($1, $2, $3, $4)",
                role, display_name, now, current_role,
            )
    finally:
        await conn.close()

    print(f"Created role: {role}")
    print(f"Password (SHOWN ONCE — store it in your own password manager, not here): {password}")
    print("Give this operator a connection string like:")
    print(f"  postgresql://{role}:<password>@<host>:5432/trustchain")
    print("Any manual UPDATE/DELETE they run against `steps` will now show up in")
    print(f"steps_history.db_role as '{role}', resolvable back to '{display_name}' via db_operators.")


async def list_operators() -> None:
    conn = await asyncpg.connect(_dsn_for_asyncpg())
    try:
        rows = await conn.fetch(
            "SELECT role_name, display_name, created_at, created_by, revoked_at FROM db_operators ORDER BY created_at"
        )
    finally:
        await conn.close()

    if not rows:
        print("No operators issued yet.")
        return
    for r in rows:
        status = "REVOKED" if r["revoked_at"] else "active"
        print(f"{r['role_name']:30s} {r['display_name']:30s} {status:8s} created_by={r['created_by']}")


async def revoke(short_name: str) -> None:
    role = _role_name(short_name)
    now = int(time.time())

    conn = await asyncpg.connect(_dsn_for_asyncpg())
    try:
        async with conn.transaction():
            # LOGIN removed immediately — the role itself is kept (not
            # DROPped) so its name stays a valid, resolvable db_role
            # value on any steps_history rows it already produced;
            # dropping it outright would either fail (rows still
            # reference the name in a plain varchar column, no FK, so it
            # wouldn't actually block — but it WOULD orphan the
            # historical attribution, turning a resolvable name into a
            # dangling one for no benefit).
            await conn.execute(f'ALTER ROLE "{role}" NOLOGIN')
            result = await conn.execute(
                "UPDATE db_operators SET revoked_at = $1 WHERE role_name = $2 AND revoked_at IS NULL", now, role
            )
    finally:
        await conn.close()

    if result == "UPDATE 0":
        print(f"No active operator named '{role}' found (already revoked, or never created).")
    else:
        print(f"Revoked login for {role}. Historical steps_history attribution to this name is unaffected.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="Issue a new individually-attributed operator role")
    p_create.add_argument("short_name", help="Short identifier, e.g. 'nipun' -> role trustchain_op_nipun")
    p_create.add_argument("--display-name", required=True, help="Real name shown in db_operators / alert evidence")

    sub.add_parser("list", help="List all issued operator roles")

    p_revoke = sub.add_parser("revoke", help="Revoke an operator's ability to log in (keeps the name for history)")
    p_revoke.add_argument("short_name", help="Same short identifier passed to 'create'")

    args = parser.parse_args()

    if args.command == "create":
        asyncio.run(create(args.short_name, args.display_name))
    elif args.command == "list":
        asyncio.run(list_operators())
    elif args.command == "revoke":
        asyncio.run(revoke(args.short_name))


if __name__ == "__main__":
    main()

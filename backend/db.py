"""
db.py — lightweight SQLite persistence for TrustChain.

Stdlib-only (sqlite3 + hashlib), wrapped in asyncio.to_thread so it never
blocks the event loop — same pattern blockchain/client.py uses for RPC calls.

Two tables:
  users — signup/login accounts (password hashed with PBKDF2, never plaintext)
  runs  — pipeline run history, so it survives a backend restart (previously
          only kept in an in-memory dict in main.py)
"""

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import sqlite3
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "trustchain.db"
_PBKDF2_ITERATIONS = 260_000

_conn: Optional[sqlite3.Connection] = None
_write_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _init_schema(_conn)
    return _conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            email         TEXT UNIQUE NOT NULL,
            name          TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at    INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS runs (
            run_id       TEXT PRIMARY KEY,
            task         TEXT,
            user_email   TEXT,
            status       TEXT NOT NULL DEFAULT 'running',
            result_json  TEXT,
            created_at   INTEGER,
            completed_at INTEGER
        );
    """)
    conn.commit()


# ── Password hashing — PBKDF2-HMAC-SHA256, stdlib only ──────────────────────

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _PBKDF2_ITERATIONS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest_hex = stored.split("$", 1)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _PBKDF2_ITERATIONS)
    return hmac.compare_digest(digest.hex(), digest_hex)


# ── Users ─────────────────────────────────────────────────────────────────

def _create_user_sync(email: str, name: str, password: str, created_at: int) -> dict:
    with _write_lock:
        conn = _get_conn()
        try:
            conn.execute(
                "INSERT INTO users (email, name, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (email, name, hash_password(password), created_at),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise ValueError("email already registered")
    return {"email": email, "name": name}


async def create_user(email: str, name: str, password: str, created_at: int) -> dict:
    return await asyncio.to_thread(_create_user_sync, email, name, password, created_at)


def _authenticate_user_sync(email: str, password: str) -> Optional[dict]:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if row is None or not verify_password(password, row["password_hash"]):
        return None
    return {"email": row["email"], "name": row["name"]}


async def authenticate_user(email: str, password: str) -> Optional[dict]:
    return await asyncio.to_thread(_authenticate_user_sync, email, password)


# ── Runs ──────────────────────────────────────────────────────────────────

def _row_to_run_dict(row: sqlite3.Row) -> dict:
    return {
        "runId":       row["run_id"],
        "task":        row["task"],
        "userEmail":   row["user_email"],
        "status":      row["status"],
        "result":      json.loads(row["result_json"]) if row["result_json"] else None,
        "createdAt":   row["created_at"],
        "completedAt": row["completed_at"],
    }


def _create_run_sync(run_id: str, task: str, user_email: Optional[str], created_at: int) -> None:
    with _write_lock:
        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO runs (run_id, task, user_email, status, created_at)
            VALUES (?, ?, ?, 'running', ?)
            ON CONFLICT(run_id) DO UPDATE SET task = excluded.task, user_email = excluded.user_email
            """,
            (run_id, task, user_email, created_at),
        )
        conn.commit()


async def create_run(run_id: str, task: str, user_email: Optional[str], created_at: int) -> None:
    await asyncio.to_thread(_create_run_sync, run_id, task, user_email, created_at)


def _set_run_status_sync(run_id: str, status: str, result: Optional[dict], completed_at: int) -> None:
    with _write_lock:
        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO runs (run_id, status, result_json, completed_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                status = excluded.status,
                result_json = excluded.result_json,
                completed_at = excluded.completed_at
            """,
            (run_id, status, json.dumps(result) if result is not None else None, completed_at),
        )
        conn.commit()


async def complete_run(run_id: str, result: dict, completed_at: int) -> None:
    await asyncio.to_thread(_set_run_status_sync, run_id, "complete", result, completed_at)


async def fail_run(run_id: str, message: str, completed_at: int) -> None:
    await asyncio.to_thread(_set_run_status_sync, run_id, "error", {"message": message}, completed_at)


def _get_run_sync(run_id: str) -> Optional[dict]:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return _row_to_run_dict(row) if row else None


async def get_run(run_id: str) -> Optional[dict]:
    return await asyncio.to_thread(_get_run_sync, run_id)


def _list_runs_sync(limit: int) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM runs ORDER BY COALESCE(created_at, 0) DESC LIMIT ?", (limit,)
    ).fetchall()
    return [_row_to_run_dict(r) for r in rows]


async def list_runs(limit: int = 50) -> list[dict]:
    return await asyncio.to_thread(_list_runs_sync, limit)

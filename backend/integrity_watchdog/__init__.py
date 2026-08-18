"""
integrity_watchdog/ — the always-on continuous verification process
(Phase 3 §6). Run with `python -m integrity_watchdog.main`.

detectors/  — pure(ish) detection logic (step_rows: 3, merkle_roots: 4,
              liveness: 5). Detectors 1 (identity drift, write-path) and
              2 (identity change surveillance, indexer-driven) live in
              agents/base.py and indexer/agent_events.py respectively —
              see main.py's module docstring for why they're not here.
tenancy.py  — resolves which org(s)/project(s) a cross-tenant finding
              (a batch can span many projects) belongs to, for alerting.
cursor.py   — the ROLLING tier's resumable, bounded-per-cycle sweep state.
lock.py     — sole-active-sweeper Postgres advisory lock.
main.py     — the process loop; also runs notifications/sender.py's
              delivery loop in-process.
"""

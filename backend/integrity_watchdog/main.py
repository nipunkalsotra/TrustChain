"""
integrity_watchdog/main.py — the watchdog's process loop (Phase 3 §6.7).

Run with `python -m integrity_watchdog.main`. Structured exactly like
anchor_worker/main.py and indexer/main.py so it's immediately familiar:
same signal-handling/graceful-drain shape, same Postgres-superuser
connection reasoning (this process's job — sweeping every project's
steps/batches for tamper evidence — is legitimately cross-tenant, same as
the indexer's "index every project's chain events"), same dedicated
Prometheus port.

TIERED SCANNING (plan §6.7) is what keeps per-cycle cost flat as history
grows:
  HOT   — every cycle, everything created/confirmed in the last
          watchdog_hot_window_seconds. Small, bounded, catches fresh
          tampering within about one poll interval.
  ROLLING — a persistent cursor (watchdog_cursor table) walks ALL of
          history at a FIXED per-cycle row budget, wrapping to the start
          on completion. A full pass takes longer as history grows; the
          cost of any ONE cycle does not.
  ON-DEMAND — POST /integrity/verify-run/{run_id} (main.py) runs every
          detector against one run synchronously, outside this loop
          entirely.

This same process also runs notifications/sender.py's delivery loop as a
concurrent task — one more always-on Python process on a single-EC2-
instance deployment for no benefit, per the Phase 3 plan's explicit
"don't add a fourth container for this" call.
"""

import asyncio
import json
import signal
import time

from sqlalchemy import desc, select, text

import observability
from config import get_settings
from db.alerts import raise_alert
from db.engine import get_sessionmaker
from db.models import AnchorBatch, BatchVerification, DbOperator, Step, StepHistory
from integrity_watchdog import tenancy as watchdog_tenancy
from integrity_watchdog.cursor import advance_cursor, get_cursor
from integrity_watchdog.detectors import liveness, merkle_roots, step_rows
from integrity_watchdog.lock import WatchdogLock
from logging_config import get_logger
from notifications.digest import send_due_digests
from notifications.sender import run_once as sender_run_once

logger = get_logger(__name__)


# ── Detector 3: step row self-consistency ──────────────────────────────

async def _resolve_operator_display_name(session, db_role: str) -> str | None:
    """ADR-0020's actual fix for 'who' — steps_history.db_role is always
    a real session_user, but that only names an individual HUMAN if they
    connected under their own individually-issued role (scripts/
    db_operator.py) rather than the shared `trustchain` superuser. Looks
    up db_operators for a friendly name; returns None for `trustchain`
    itself (an automated process, not a person) or any role that was
    never issued through that script."""
    if db_role is None:
        return None
    operator_row = (await session.execute(select(DbOperator).where(DbOperator.role_name == db_role))).scalar_one_or_none()
    return operator_row.display_name if operator_row else None


async def _forensic_evidence(session, step_id: int) -> dict:
    """Extra evidence from steps_history (migration b9a8a1970b3c, DELETE
    coverage + db_operators added by 010d34f64a31), if the
    steps_audit_trigger caught this specific edit — it only exists for
    edits made AFTER that migration ran, so this returns {} (not an
    error) for anything older, or for a step whose leaf_hash was also
    rewritten consistently by the same UPDATE that still changed some
    OTHER column the trigger tracks. Most-recent row wins if there are
    several (a step could in principle be tampered with more than once)
    — ordered by (changed_at, id) DESC, not changed_at alone:
    changed_at is second-granularity (int(time.time())), so two edits to
    the SAME step inside one second (a routine UPDATE followed quickly by
    a real tamper, or vice versa — found by a real test race, not
    theoretical) tie on changed_at, and an ORDER BY with no secondary key
    lets Postgres return either one non-deterministically. id is
    autoincrementing, so it's always a true insertion-order tiebreaker."""
    row = (await session.execute(
        select(StepHistory).where(StepHistory.step_id == step_id)
        .order_by(desc(StepHistory.changed_at), desc(StepHistory.id)).limit(1)
    )).scalar_one_or_none()
    if row is None:
        return {}

    changed = json.loads(row.changed_columns)
    operator_name = await _resolve_operator_display_name(session, row.db_role)

    if changed == ["__deleted__"]:
        # The DELETE-trigger sentinel (010d34f64a31) — there's no "new"
        # anything to diff against, the row is just gone. Distinct
        # summary rather than trying to render hash pairs that would all
        # misleadingly show NULL.
        return {
            "whatHappened": "the entire step row was DELETED, not edited",
            "deletedAtUnix": row.changed_at,
            "editedByDbRole": row.db_role,
            "editedByOperator": operator_name,
            "editedFromClientAddr": row.db_client_addr,
        }

    return {
        "changedColumns": changed,
        "changedAtUnix": row.changed_at,
        # Only include hash pairs for columns that actually changed —
        # showing unchanged old==new pairs would bury the real diff.
        **({"oldOutputHash": row.old_output_hash, "newOutputHash": row.new_output_hash} if "output_hash" in changed else {}),
        **({"oldInputHash": row.old_input_hash, "newInputHash": row.new_input_hash} if "input_hash" in changed else {}),
        **({"oldLeafHash": row.old_leaf_hash, "newLeafHash": row.new_leaf_hash} if "leaf_hash" in changed else {}),
        **({"oldAgentCodeHash": row.old_agent_code_hash, "newAgentCodeHash": row.new_agent_code_hash} if "agent_code_hash" in changed else {}),
        # WHICH DB role/session made the change, and — if they connected
        # under an individually-issued operator role rather than the
        # shared `trustchain` superuser — the actual human's name.
        "editedByDbRole": row.db_role,
        "editedByOperator": operator_name,
        "editedFromClientAddr": row.db_client_addr,
    }


async def _raise_step_row_alerts(session, mismatches: list[dict]) -> None:
    step_ids = [m["step"].id for m in mismatches]
    by_step = {m["step"].id: m for m in mismatches}
    grouped = await watchdog_tenancy.group_steps_by_org(session, step_ids)

    for org_id, bucket in grouped.items():
        for step_id in bucket["stepIds"]:
            m = by_step[step_id]
            forensics = await _forensic_evidence(session, step_id)
            await raise_alert(
                org_id=org_id, project_id=next(iter(bucket["projectIds"])), alert_type="step_row_tampered",
                severity="critical", title=f"Recorded step #{step_id} no longer matches its own hash",
                summary=(
                    f"Step {step_id} (run {m['step'].run_id}, agent {m['step'].agent_id}) was recomputed "
                    f"from its currently-stored content and does not match its stored leaf_hash — "
                    f"the row was edited after it was hashed."
                ),
                subject=f"step:{step_id}",
                evidence={
                    "stepId": step_id, "runId": m["step"].run_id, "storedLeaf": m["step"].leaf_hash,
                    "expectedLeaf": m["expectedLeaf"], "leafSchemaVersion": m["step"].leaf_schema_version,
                    **forensics,
                },
                detector="step_rows",
            )


async def sweep_step_rows(session, step_ids: list[int]) -> int:
    if not step_ids:
        return 0
    steps = (await session.execute(select(Step).where(Step.id.in_(step_ids)))).scalars().all()
    mismatches = await step_rows.check_steps(steps)
    if mismatches:
        await _raise_step_row_alerts(session, mismatches)
    return len(mismatches)


# ── Detector 4: Merkle roots (missing / rebuilt / on-chain) ────────────

async def _raise_batch_alerts(session, batch: AnchorBatch, alert_type: str, title: str, summary: str, evidence_by_org_extra: dict) -> None:
    grouped = await watchdog_tenancy.group_steps_by_org(session, list(batch.leaf_order))
    for org_id, bucket in grouped.items():
        await raise_alert(
            org_id=org_id, project_id=next(iter(bucket["projectIds"])), alert_type=alert_type,
            severity="critical", title=title, summary=summary, subject=f"batch:{batch.id}",
            evidence={"batchId": batch.id, "yourStepIds": bucket["stepIds"], **evidence_by_org_extra},
            detector="merkle_roots",
        )


async def sweep_merkle_roots(session, batches: list[AnchorBatch], check_onchain: bool) -> dict:
    """Returns {"missing": n, "rootMismatch": n, "onchainMismatch": n,
    "onchainCheckFailures": n}."""
    counts = {"missing": 0, "rootMismatch": 0, "onchainMismatch": 0, "onchainCheckFailures": 0}

    for batch in batches:
        step_rows_result = (await session.execute(select(Step).where(Step.id.in_(batch.leaf_order)))).scalars().all()
        steps_by_id = {s.id: s for s in step_rows_result}

        rebuilt_root, missing = merkle_roots.rebuild_root(steps_by_id, list(batch.leaf_order))
        if missing:
            counts["missing"] += 1
            observability.INTEGRITY_CHECKS_TOTAL.labels(detector="merkle_roots_missing", result="missing").inc()
            # Phase 4: attribute WHO deleted each missing step, the same
            # way _raise_step_row_alerts already does for an EDITED row
            # (_forensic_evidence's DELETE-sentinel case, migration
            # 010d34f64a31, exists specifically for this — it just wasn't
            # wired into this detector). Found by a real end-to-end run
            # (scripts/e2e_demo.py's Stage 8): a step deleted after being
            # anchored raised a step_missing alert with NO attribution at
            # all, silently defeating the entire "who tampered with what"
            # value proposition for exactly the most destructive tamper
            # case — deletion, not just editing.
            deletion_forensics = {}
            for missing_step_id in missing:
                evidence = await _forensic_evidence(session, missing_step_id)
                if evidence:
                    deletion_forensics[str(missing_step_id)] = evidence
            await _raise_batch_alerts(
                session, batch, "step_missing",
                f"Batch {batch.id} is missing {len(missing)} anchored step(s)",
                f"Batch {batch.id}'s leaf_order references step(s) that no longer exist in the steps table.",
                {
                    "missingStepIds": missing, "expectedCount": len(batch.leaf_order), "foundCount": len(steps_by_id),
                    **({"deletionForensics": deletion_forensics} if deletion_forensics else {}),
                },
            )
        elif rebuilt_root.lower() != batch.merkle_root.lower():
            counts["rootMismatch"] += 1
            observability.INTEGRITY_CHECKS_TOTAL.labels(detector="merkle_roots_rebuild", result="mismatch").inc()
            await _raise_batch_alerts(
                session, batch, "batch_root_mismatch",
                f"Batch {batch.id} no longer rebuilds to its recorded Merkle root",
                f"Recomputing batch {batch.id}'s tree from its current step content yields a different root "
                f"than what was anchored — one or more of its steps was edited after anchoring.",
                {"storedRoot": batch.merkle_root, "rebuiltRoot": rebuilt_root, "leafOrderLength": len(batch.leaf_order)},
            )
        else:
            observability.INTEGRITY_CHECKS_TOTAL.labels(detector="merkle_roots_rebuild", result="ok").inc()

        if check_onchain and not missing and batch.onchain_anchor_id is not None:
            existing = await session.get(BatchVerification, batch.id)
            if existing is not None and existing.onchain_root_verified_at is not None:
                continue  # immutable fact, cached — see BatchVerification's own docstring
            try:
                matches, onchain_root = await merkle_roots.check_onchain_root(batch)
            except Exception as e:
                counts["onchainCheckFailures"] += 1
                logger.warning("onchain_root_check_failed", batch_id=batch.id, error=str(e))
                continue

            now = int(time.time())
            await session.execute(
                text("""
                    INSERT INTO batch_verifications (batch_id, onchain_root_verified_at, onchain_root, last_rebuilt_at, last_result)
                    VALUES (:bid, :now, :root, :now, :result)
                    ON CONFLICT (batch_id) DO UPDATE SET
                        onchain_root_verified_at = EXCLUDED.onchain_root_verified_at,
                        onchain_root = EXCLUDED.onchain_root,
                        last_rebuilt_at = EXCLUDED.last_rebuilt_at,
                        last_result = EXCLUDED.last_result
                """),
                {"bid": batch.id, "now": now, "root": onchain_root, "result": "ok" if matches else "mismatch"},
            )
            await session.commit()

            if not matches:
                counts["onchainMismatch"] += 1
                await _raise_batch_alerts(
                    session, batch, "onchain_root_mismatch",
                    f"Batch {batch.id}'s recorded root doesn't match what's anchored on-chain",
                    f"Batch {batch.id}'s stored merkle_root differs from AgentAuditLogV2.getBatch()'s "
                    f"on-chain record — the database's own record of this batch was altered after anchoring.",
                    {"storedRoot": batch.merkle_root, "onchainRoot": onchain_root, "anchorId": batch.onchain_anchor_id,
                     "txHash": batch.tx_hash, "blockNumber": batch.block_number},
                )

    return counts


# ── Detector 5: anchoring liveness ──────────────────────────────────────

async def sweep_liveness(session, stall_threshold_seconds: int) -> None:
    stalled = await liveness.check_stalled(session, stall_threshold_seconds)
    observability.INTEGRITY_CHECKS_TOTAL.labels(
        detector="liveness", result="mismatch" if stalled["stalled"] else "ok",
    ).inc()
    if stalled["stalled"] and stalled["oldestStepId"] is not None:
        grouped = await watchdog_tenancy.group_steps_by_org(session, [stalled["oldestStepId"]])
        severity = "critical" if stalled["oldestPendingAgeSeconds"] >= 2 * stall_threshold_seconds else "warning"
        for org_id, bucket in grouped.items():
            await raise_alert(
                org_id=org_id, project_id=next(iter(bucket["projectIds"])), alert_type="anchoring_stalled",
                severity=severity, title="Anchoring appears stalled",
                summary=f"{stalled['pendingCount']} step(s) are still unanchored; the oldest has been "
                        f"waiting {stalled['oldestPendingAgeSeconds']}s.",
                subject="anchoring:stalled",
                evidence={"pendingCount": stalled["pendingCount"], "oldestPendingAgeSeconds": stalled["oldestPendingAgeSeconds"]},
                detector="liveness",
            )

    dead = await liveness.check_dead_lettered(session)
    if dead["count"] > 0:
        grouped = await watchdog_tenancy.group_steps_by_org(session, dead["stepIds"])
        for org_id, bucket in grouped.items():
            await raise_alert(
                org_id=org_id, project_id=next(iter(bucket["projectIds"])), alert_type="anchoring_dead_lettered",
                severity="critical", title="Some steps will never be anchored without intervention",
                summary=f"{len(bucket['stepIds'])} of your step(s) are in an anchor_outbox row that exhausted its retries.",
                subject="anchoring:dead_letter",
                evidence={"yourStepIds": bucket["stepIds"]},
                detector="liveness",
            )


# ── One full cycle: hot tier + rolling tier over both steps and batches ─

async def run_cycle(settings=None) -> dict:
    settings = settings or get_settings()
    session_factory = get_sessionmaker()
    now = int(time.time())
    counts = {"stepsChecked": 0, "batchesChecked": 0}

    async with session_factory() as session:
        # HOT — everything recent, every cycle.
        hot_cutoff = now - settings.watchdog_hot_window_seconds
        hot_step_ids = (await session.execute(
            text("SELECT id FROM steps WHERE created_at >= :cutoff ORDER BY id"), {"cutoff": hot_cutoff}
        )).scalars().all()
        t0 = time.monotonic()
        await sweep_step_rows(session, list(hot_step_ids))
        observability.WATCHDOG_SWEEP_DURATION_SECONDS.labels(detector="step_rows", tier="hot").observe(time.monotonic() - t0)
        counts["stepsChecked"] += len(hot_step_ids)

        hot_batches = (await session.execute(
            select(AnchorBatch).where(AnchorBatch.status == "confirmed", AnchorBatch.block_number.isnot(None))
            .order_by(AnchorBatch.id.desc()).limit(settings.watchdog_rolling_batches_per_cycle)
        )).scalars().all()
        t0 = time.monotonic()
        await sweep_merkle_roots(session, list(hot_batches), settings.watchdog_onchain_root_check_enabled)
        observability.WATCHDOG_SWEEP_DURATION_SECONDS.labels(detector="merkle_roots", tier="hot").observe(time.monotonic() - t0)
        counts["batchesChecked"] += len(hot_batches)

        await sweep_liveness(session, int(settings.watchdog_hot_window_seconds / 4) or 1)

        # ROLLING — fixed budget, cursor-tracked, wraps on completion.
        step_cursor = await get_cursor(session, "step_rows_rolling")
        max_step_id = (await session.execute(text("SELECT max(id) FROM steps"))).scalar_one() or 0
        rolling_step_ids = (await session.execute(
            text("SELECT id FROM steps WHERE id > :last ORDER BY id LIMIT :n"),
            {"last": step_cursor["lastId"], "n": settings.watchdog_rolling_steps_per_cycle},
        )).scalars().all()
        t0 = time.monotonic()
        await sweep_step_rows(session, list(rolling_step_ids))
        observability.WATCHDOG_SWEEP_DURATION_SECONDS.labels(detector="step_rows", tier="rolling").observe(time.monotonic() - t0)
        counts["stepsChecked"] += len(rolling_step_ids)

        new_last = rolling_step_ids[-1] if rolling_step_ids else step_cursor["lastId"]
        wrapped = not rolling_step_ids and step_cursor["lastId"] > 0
        await advance_cursor(
            session, "step_rows_rolling", 0 if wrapped else new_last, wrapped,
            int((time.monotonic() - t0) * 1000),
        )
        observability.WATCHDOG_CURSOR_LAG_ITEMS.labels(detector="step_rows_rolling").set(max(0, max_step_id - new_last))

        batch_cursor = await get_cursor(session, "merkle_roots_rolling")
        max_batch_id = (await session.execute(text("SELECT max(id) FROM anchor_batches"))).scalar_one() or 0
        rolling_batches = (await session.execute(
            select(AnchorBatch).where(
                AnchorBatch.id > batch_cursor["lastId"], AnchorBatch.status == "confirmed",
            ).order_by(AnchorBatch.id).limit(settings.watchdog_rolling_batches_per_cycle)
        )).scalars().all()
        t0 = time.monotonic()
        await sweep_merkle_roots(session, list(rolling_batches), settings.watchdog_onchain_root_check_enabled)
        observability.WATCHDOG_SWEEP_DURATION_SECONDS.labels(detector="merkle_roots", tier="rolling").observe(time.monotonic() - t0)
        counts["batchesChecked"] += len(rolling_batches)

        new_batch_last = rolling_batches[-1].id if rolling_batches else batch_cursor["lastId"]
        batch_wrapped = not rolling_batches and batch_cursor["lastId"] > 0
        await advance_cursor(
            session, "merkle_roots_rolling", 0 if batch_wrapped else new_batch_last, batch_wrapped,
            int((time.monotonic() - t0) * 1000),
        )
        observability.WATCHDOG_CURSOR_LAG_ITEMS.labels(detector="merkle_roots_rolling").set(max(0, max_batch_id - new_batch_last))

        for detector in ("step_rows_rolling", "merkle_roots_rolling"):
            observability.WATCHDOG_LAST_SUCCESS_TIMESTAMP.labels(detector=detector).set(now)
            c = await get_cursor(session, detector)
            if c["wrappedAt"]:
                observability.WATCHDOG_FULL_SWEEP_AGE_SECONDS.set(now - c["wrappedAt"])

        # Platform-wide open alert count, by severity — feeds Grafana's
        # "Open alerts by severity" panel (observability.OPEN_ALERTS, a
        # Gauge with no tenant label — see its own docstring). Recomputed
        # fresh from `alerts` every cycle rather than incremented at
        # raise time and decremented at resolve time, so a resolution
        # made through any path (API, direct DB fix, a future admin
        # tool) is reflected here without that path needing to remember
        # to touch this gauge too — this was itself a real gap: the
        # Gauge was defined in observability.py but nothing ever called
        # .set() on it, so the panel silently showed "No data" forever.
        open_counts = dict((await session.execute(
            text("SELECT severity, count(*) FROM alerts WHERE status = 'open' GROUP BY severity")
        )).all())
        for severity in ("critical", "warning", "info"):
            observability.OPEN_ALERTS.labels(severity=severity).set(open_counts.get(severity, 0))

    return counts


async def main() -> None:
    settings = get_settings()
    logger.info("integrity_watchdog_starting", enabled=settings.watchdog_enabled)
    observability.start_metrics_server(settings.watchdog_metrics_port)
    observability.init_sentry(settings.sentry_dsn, settings.environment, settings.sentry_traces_sample_rate)
    observability.init_tracing("trustchain-integrity-watchdog", settings.otel_exporter_otlp_endpoint)

    if not settings.watchdog_enabled:
        logger.info("integrity_watchdog_disabled_idling")
        while True:
            await asyncio.sleep(3600)

    lock = WatchdogLock()
    while not await lock.try_acquire():
        logger.info("integrity_watchdog_waiting_for_lock")
        await asyncio.sleep(settings.watchdog_poll_interval_seconds)

    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown_event.set)

    try:
        while not shutdown_event.is_set():
            try:
                counts = await run_cycle(settings)
                logger.info("integrity_watchdog_cycle_complete", **counts)
            except Exception:
                logger.exception("integrity_watchdog_cycle_failed")

            try:
                # The notification sender runs inline here rather than as a
                # separate asyncio.create_task — one more always-running
                # background task in this same process either way, and
                # running it synchronously between sweep cycles keeps the
                # process's failure modes simple (one loop, one place a
                # crash can happen) rather than needing to supervise two
                # independently-scheduled coroutines.
                await sender_run_once(worker_id="integrity-watchdog")
            except Exception:
                logger.exception("integrity_watchdog_sender_iteration_failed")

            try:
                # Cheap on every cycle even at the default 60s poll
                # interval against a daily digest cadence: with no digest
                # subscribers (the common case) this is one indexed query
                # that returns immediately; with subscribers, the actual
                # "are they due yet" check is per-recipient against their
                # own last_digest_sent_at, so most calls still no-op.
                await send_due_digests()
            except Exception:
                logger.exception("integrity_watchdog_digest_iteration_failed")

            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=settings.watchdog_poll_interval_seconds)
            except asyncio.TimeoutError:
                pass
    finally:
        await lock.release()
        logger.info("integrity_watchdog_shutting_down")


if __name__ == "__main__":
    asyncio.run(main())

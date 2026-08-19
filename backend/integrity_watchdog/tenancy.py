"""
integrity_watchdog/tenancy.py — resolving which org/project a set of
steps belongs to, for alert-raising purposes only.

WHY THIS EXISTS SEPARATELY FROM db/orgs.py: anchor_batches is
DELIBERATELY cross-tenant (the anchor worker batches steps from many
projects into one Merkle tree for efficiency — see ADR-0002) — a single
batch's `leaf_order` can span several different projects, even several
different orgs. A detector-4 finding on that batch therefore can't be
attributed to one org; it has to be attributed to EVERY org that has a
step in the affected set, and each org's alert must only ever reveal
THAT org's own step ids in its evidence (invariant I7 — a cross-tenant
finding must never leak another tenant's identifiers into an alert
neither of them consented to share).
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Project, Run, Step, StepHistory


async def group_steps_by_org(session: AsyncSession, step_ids: list[int]) -> dict[int, dict]:
    """Returns {org_id: {"projectIds": {project_id, ...}, "stepIds": [...]}}
    for the given step ids — a plain join from steps to runs to projects,
    executed with the watchdog's own cross-tenant DB role (see this
    module's own process running as `trustchain`, not `trustchain_api` —
    RLS doesn't apply to it, by design, the same as anchor-worker/indexer).

    Falls back to steps_history.project_id (denormalized specifically to
    survive the referenced step being DELETED entirely — see StepHistory's
    own docstring) for any step_id the primary join can't resolve. This
    matters more than it looks: sweep_merkle_roots' "missing" detector
    calls this with step_ids that no longer have a `steps` row AT ALL —
    the primary join, which depends on that exact row existing, silently
    resolves to nothing for them. Before this fallback existed, a step
    DELETED outright (arguably the single most damaging tamper case —
    actively erasing the evidence, not just editing it) could never be
    attributed to any org: the detector's own counters correctly
    incremented, but zero alerts were ever actually raised for anyone.
    Found by a real end-to-end deletion test, not by inspection."""
    if not step_ids:
        return {}
    stmt = (
        select(Step.id, Project.org_id, Project.id.label("project_id"))
        .join(Run, Run.run_id == Step.run_id)
        .join(Project, Project.id == Run.project_id)
        .where(Step.id.in_(step_ids))
    )
    rows = (await session.execute(stmt)).all()

    grouped: dict[int, dict] = {}
    resolved_ids: set[int] = set()
    for step_id, org_id, project_id in rows:
        bucket = grouped.setdefault(org_id, {"projectIds": set(), "stepIds": []})
        bucket["projectIds"].add(project_id)
        bucket["stepIds"].append(step_id)
        resolved_ids.add(step_id)

    unresolved = [sid for sid in step_ids if sid not in resolved_ids]
    if unresolved:
        history_stmt = (
            select(StepHistory.step_id, Project.org_id, Project.id.label("project_id"))
            .join(Project, Project.id == StepHistory.project_id)
            .where(StepHistory.step_id.in_(unresolved), StepHistory.project_id.isnot(None))
        )
        history_rows = (await session.execute(history_stmt)).all()
        seen_from_history: set[int] = set()
        for step_id, org_id, project_id in history_rows:
            if step_id in seen_from_history:
                continue  # a step can have several steps_history rows (edited, then deleted) — count it once
            seen_from_history.add(step_id)
            bucket = grouped.setdefault(org_id, {"projectIds": set(), "stepIds": []})
            bucket["projectIds"].add(project_id)
            bucket["stepIds"].append(step_id)

    return grouped

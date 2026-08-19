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

from db.models import Project, Run, Step


async def group_steps_by_org(session: AsyncSession, step_ids: list[int]) -> dict[int, dict]:
    """Returns {org_id: {"projectIds": {project_id, ...}, "stepIds": [...]}}
    for the given step ids — a plain join from steps to runs to projects,
    executed with the watchdog's own cross-tenant DB role (see this
    module's own process running as `trustchain`, not `trustchain_api` —
    RLS doesn't apply to it, by design, the same as anchor-worker/indexer)."""
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
    for step_id, org_id, project_id in rows:
        bucket = grouped.setdefault(org_id, {"projectIds": set(), "stepIds": []})
        bucket["projectIds"].add(project_id)
        bucket["stepIds"].append(step_id)
    return grouped

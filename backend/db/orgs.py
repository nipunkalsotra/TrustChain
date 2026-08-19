"""
db/orgs.py — organization, project and membership CRUD (Phase 3 §4, §9.2-9.4).

Query layer only — permission checks happen in main.py's route handlers
via permissions.require_permission BEFORE any of these are called; these
functions trust the caller has already authorized the operation, same
division of responsibility db/tenancy.py's existing functions already
have with main.py's auth.require_scope calls.
"""

from typing import Optional

from sqlalchemy import func, select, update

from db.engine import get_sessionmaker
from db.models import Membership, Organization, Project, User


# ── Reads ────────────────────────────────────────────────────────────────

async def list_orgs_for_user(user_id: int) -> list[dict]:
    """Every org the user is a member of, with counts — GET /orgs, and
    the `memberships` array in GET /me (Phase 3 Appendix C)."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stmt = (
            select(Organization, Membership.role)
            .join(Membership, Membership.org_id == Organization.id)
            .where(Membership.user_id == user_id, Organization.deleted_at.is_(None))
            .order_by(Organization.id)
        )
        rows = (await session.execute(stmt)).all()

        results = []
        for org, role in rows:
            member_count = (await session.execute(
                select(func.count()).select_from(Membership).where(Membership.org_id == org.id)
            )).scalar_one()
            project_count = (await session.execute(
                select(func.count()).select_from(Project).where(Project.org_id == org.id, Project.deleted_at.is_(None))
            )).scalar_one()
            results.append({
                "id": org.id, "name": org.name, "plan": org.plan, "role": role,
                "memberCount": member_count, "projectCount": project_count, "createdAt": org.created_at,
            })
        return results


async def get_org(org_id: int) -> Optional[dict]:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        org = await session.get(Organization, org_id)
        if org is None or org.deleted_at is not None:
            return None
        return {
            "id": org.id, "name": org.name, "plan": org.plan,
            "gasBudgetWei": org.gas_budget_wei, "gasSpentWei": org.gas_spent_wei,
            "tokenBudget": org.token_budget, "tokensSpent": org.tokens_spent,
            "createdAt": org.created_at,
        }


async def list_projects(org_id: int) -> list[dict]:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stmt = (
            select(Project).where(Project.org_id == org_id, Project.deleted_at.is_(None)).order_by(Project.id)
        )
        rows = (await session.execute(stmt)).scalars().all()
        return [
            {"id": p.id, "orgId": p.org_id, "name": p.name, "environment": p.environment, "createdAt": p.created_at}
            for p in rows
        ]


async def get_project(project_id: int) -> Optional[dict]:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        p = await session.get(Project, project_id)
        if p is None or p.deleted_at is not None:
            return None
        return {"id": p.id, "orgId": p.org_id, "name": p.name, "environment": p.environment, "createdAt": p.created_at}


async def list_members(org_id: int) -> list[dict]:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        InvitedBy = User.__table__.alias("invited_by_user")
        stmt = (
            select(
                User.id, User.name, User.email, Membership.role, Membership.created_at, Membership.invited_by,
                InvitedBy.c.name.label("invited_by_name"),
            )
            .select_from(Membership)
            .join(User, User.id == Membership.user_id)
            .outerjoin(InvitedBy, InvitedBy.c.id == Membership.invited_by)
            .where(Membership.org_id == org_id)
            .order_by(Membership.created_at)
        )
        rows = (await session.execute(stmt)).all()
        return [
            {
                "userId": r.id, "name": r.name, "email": r.email, "role": r.role,
                "joinedAt": r.created_at, "invitedByName": r.invited_by_name,
            }
            for r in rows
        ]


# ── Writes ───────────────────────────────────────────────────────────────

async def create_org(user_id: int, name: str, project_name: str, now: int) -> dict:
    """POST /orgs — an explicit, additional org for an already-registered
    user (distinct from the one auto-provisioned at signup). Same
    org+project+owner-membership shape as tenancy.provision_personal_org,
    reused here rather than duplicated."""
    from db import tenancy

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        project = await tenancy.provision_personal_org(session, user_id, name, now, org_name=name, project_name=project_name)
        await session.commit()
    return {"orgId": project.org_id, "projectId": project.id}


async def rename_org(org_id: int, name: str) -> bool:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        result = await session.execute(
            update(Organization).where(Organization.id == org_id, Organization.deleted_at.is_(None)).values(name=name)
        )
        await session.commit()
        return result.rowcount > 0


async def soft_delete_org(org_id: int, now: int) -> bool:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        result = await session.execute(
            update(Organization).where(Organization.id == org_id, Organization.deleted_at.is_(None)).values(deleted_at=now)
        )
        await session.commit()
        return result.rowcount > 0


async def count_active_orgs_for_user(user_id: int) -> int:
    """Used by DELETE /orgs/{id} to refuse deleting a user's only org —
    a user must always resolve to at least one project (every downstream
    principal-resolution invariant assumes this)."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stmt = (
            select(func.count(func.distinct(Organization.id)))
            .select_from(Membership)
            .join(Organization, Organization.id == Membership.org_id)
            .where(Membership.user_id == user_id, Organization.deleted_at.is_(None))
        )
        return (await session.execute(stmt)).scalar_one()


async def create_project(org_id: int, name: str, environment: str, now: int) -> dict:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        project = Project(org_id=org_id, name=name, environment=environment, created_at=now)
        session.add(project)
        await session.commit()
    return {"id": project.id, "orgId": org_id, "name": name, "environment": environment, "createdAt": now}


async def rename_project(project_id: int, name: str) -> bool:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        result = await session.execute(
            update(Project).where(Project.id == project_id, Project.deleted_at.is_(None)).values(name=name)
        )
        await session.commit()
        return result.rowcount > 0


async def count_active_projects(org_id: int) -> int:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stmt = select(func.count()).select_from(Project).where(Project.org_id == org_id, Project.deleted_at.is_(None))
        return (await session.execute(stmt)).scalar_one()


async def soft_delete_project(project_id: int, now: int) -> bool:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        result = await session.execute(
            update(Project).where(Project.id == project_id, Project.deleted_at.is_(None)).values(deleted_at=now)
        )
        await session.commit()
        return result.rowcount > 0


# ── Membership mutation ─────────────────────────────────────────────────

async def count_owners(org_id: int) -> int:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stmt = select(func.count()).select_from(Membership).where(Membership.org_id == org_id, Membership.role == "owner")
        return (await session.execute(stmt)).scalar_one()


async def get_membership(user_id: int, org_id: int) -> Optional[dict]:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        m = await session.get(Membership, {"user_id": user_id, "org_id": org_id})
        if m is None:
            return None
        return {"userId": m.user_id, "orgId": m.org_id, "role": m.role, "createdAt": m.created_at}


async def change_role(user_id: int, org_id: int, new_role: str, now: int) -> bool:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        result = await session.execute(
            update(Membership).where(Membership.user_id == user_id, Membership.org_id == org_id)
            .values(role=new_role, updated_at=now)
        )
        await session.commit()
        return result.rowcount > 0


async def remove_member(user_id: int, org_id: int) -> bool:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        m = await session.get(Membership, {"user_id": user_id, "org_id": org_id})
        if m is None:
            return False
        await session.delete(m)
        await session.commit()
        return True


async def transfer_ownership(from_user_id: int, to_user_id: int, org_id: int, now: int) -> bool:
    """Atomic: target becomes owner, actor becomes admin, in one
    transaction — the two updates must never be observable independently
    (an org with two owners, or zero, mid-flight)."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        target = await session.get(Membership, {"user_id": to_user_id, "org_id": org_id})
        actor = await session.get(Membership, {"user_id": from_user_id, "org_id": org_id})
        if target is None or actor is None:
            return False
        target.role = "owner"
        target.updated_at = now
        actor.role = "admin"
        actor.updated_at = now
        await session.commit()
        return True


def is_last_owner(role: str, owner_count: int) -> bool:
    return role == "owner" and owner_count <= 1


def rank_allows_target_modification(actor_rank: int, target_current_rank: int, requested_rank: int) -> bool:
    """An actor may only modify a member whose CURRENT rank AND requested
    new rank are both strictly below the actor's own rank (Phase 3 §5.5)
    — an admin (rank 30) can promote a viewer (10) to member (20), but
    cannot create another admin (30) or touch an owner (40), and cannot
    demote a peer admin either."""
    return target_current_rank < actor_rank and requested_rank < actor_rank

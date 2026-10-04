"""Read models for the redesigned workspace; no mutation or HTTP concerns."""

from datetime import datetime, time, timedelta, timezone
import json

from sqlalchemy import String, cast, func, or_, select

from app.db import Audit, Job, Organization, ProcurementSession, User, public
from app.services.metrics import measurement


def session_page(
    db, organization_id, query="", status="ALL", days=0, offset=0, limit=20
):
    statement = select(ProcurementSession).where(
        ProcurementSession.organization_id == organization_id
    )
    if query.strip():
        statement = statement.where(
            ProcurementSession.title.icontains(query.strip(), autoescape=True)
        )
    if status != "ALL":
        statement = statement.where(ProcurementSession.status == status)
    if days:
        statement = statement.where(
            ProcurementSession.created_at
            >= datetime.now(timezone.utc) - timedelta(days=days)
        )
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    sessions = list(
        db.scalars(
            statement.order_by(
                ProcurementSession.created_at.desc(), ProcurementSession.id
            )
            .offset(offset)
            .limit(limit)
        )
    )
    ids = [session.id for session in sessions]
    updates = (
        dict(
            db.execute(
                select(Audit.session_id, func.max(Audit.created_at))
                .where(Audit.session_id.in_(ids))
                .group_by(Audit.session_id)
            ).all()
        )
        if ids
        else {}
    )
    return {
        "total": total,
        "items": [
            {
                **public(session),
                "updated_at": updates.get(session.id) or session.created_at.isoformat(),
            }
            for session in sessions
        ],
    }


def workspace_overview(db, organization_id, days=0):
    sessions = select(ProcurementSession).where(
        ProcurementSession.organization_id == organization_id
    )
    if days:
        sessions = sessions.where(
            ProcurementSession.created_at
            >= datetime.now(timezone.utc) - timedelta(days=days)
        )
    session_ids = sessions.with_only_columns(ProcurementSession.id)
    total = db.scalar(select(func.count()).select_from(sessions.subquery())) or 0
    active = (
        db.scalar(
            select(func.count()).select_from(
                sessions.where(
                    ProcurementSession.status.not_in(
                        ["CANCELLED", "CLEANING", "CLEANUP_FAILED"]
                    )
                ).subquery()
            )
        )
        or 0
    )
    latest = db.scalar(
        select(Job)
        .where(
            Job.session_id.in_(session_ids),
            Job.kind.in_(["full", "match"]),
            Job.status == "COMPLETED",
        )
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(1)
    )
    metrics = measurement(db, latest.session_id) if latest else None
    organization = db.get(Organization, organization_id)
    return {
        "organization": organization.name if organization else "Рабочее пространство",
        "total_sessions": total,
        "active_sessions": active,
        "latest_session_id": latest.session_id if latest else None,
        "latest_session_title": metrics["session_title"] if metrics else None,
        "efficiency": metrics["efficiency"] if metrics else None,
        "needs_review_pairs": metrics["matching"]["needs_review_pairs"]
        if metrics
        else 0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def audit_page(
    db,
    sid,
    query="",
    event_type="ALL",
    author_id=None,
    date_from=None,
    date_to=None,
    offset=0,
    limit=50,
):
    statement = (
        select(Audit, User.username)
        .outerjoin(User, Audit.user_id == User.id)
        .where(Audit.session_id == sid)
    )
    if event_type != "ALL":
        statement = statement.where(Audit.event_type == event_type)
    if author_id is not None:
        statement = statement.where(
            Audit.user_id.is_(None) if author_id == 0 else Audit.user_id == author_id
        )
    if query.strip():
        value = query.strip()
        conditions = [
            Audit.event_type.icontains(value, autoescape=True),
            cast(Audit.event_details, String).icontains(value, autoescape=True),
            User.username.icontains(value, autoescape=True),
        ]
        if db.get_bind().dialect.name == "sqlite":
            # SQLite JSON may retain escaped Cyrillic from existing stored events.
            escaped = json.dumps(value, ensure_ascii=True)[1:-1]
            conditions.append(
                cast(Audit.event_details, String).icontains(escaped, autoescape=True)
            )
        statement = statement.where(or_(*conditions))
    if date_from:
        statement = statement.where(
            Audit.created_at
            >= datetime.combine(date_from, time.min, tzinfo=timezone.utc).isoformat()
        )
    if date_to:
        statement = statement.where(
            Audit.created_at
            < datetime.combine(
                date_to + timedelta(days=1), time.min, tzinfo=timezone.utc
            ).isoformat()
        )
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = db.execute(
        statement.order_by(Audit.id.desc()).offset(offset).limit(limit)
    ).all()
    authors = db.execute(
        select(User.id, User.username)
        .join(Audit, Audit.user_id == User.id)
        .where(Audit.session_id == sid)
        .distinct()
    ).all()
    events = list(
        db.scalars(
            select(Audit.event_type)
            .where(Audit.session_id == sid)
            .distinct()
            .order_by(Audit.event_type)
        )
    )
    return {
        "total": total,
        "events": [
            {**public(event), "author": username or "Система"}
            for event, username in rows
        ],
        "authors": [{"id": ident, "name": name} for ident, name in authors],
        "event_types": events,
    }

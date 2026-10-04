from copy import deepcopy
from math import isfinite
from typing import Any, Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, StrictBool, Field
from sqlalchemy import select, delete, func, or_
from app.db import (
    get_db,
    Item,
    Match,
    Job,
    QualityReview,
    QualityCompletion,
    public,
    now,
)
from app.api.deps import get_current_user, own_session, require_expert
from app.services.execution import ACTIVE_STATUSES
from app.services.quality import latest_job, evaluate
from app.services.audit import record

router = APIRouter()


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    category_reviewed: StrictBool = False
    category_id: str | None = None
    suitable: StrictBool | None = None
    comment: str = Field(default="", max_length=2000)


class FinishInput(BaseModel):
    job_id: str


def current(db, sid, jid=None, write=False):
    job = latest_job(db, sid)
    if not job or (jid is not None and jid != job.id):
        raise HTTPException(
            409, "Расчёт изменился или не завершён. Обновите страницу проверки."
        )
    if write and db.scalar(
        select(Job.id)
        .where(Job.session_id == sid, Job.status.in_(ACTIVE_STATUSES))
        .limit(1)
    ):
        raise HTTPException(409, "Дождитесь завершения текущей обработки")
    return job


def target(db, sid, job, kind, ident):
    if kind == "candidate":
        obj = db.get(Match, ident)
        if not obj or obj.session_id != sid or obj.job_id != job.id:
            raise HTTPException(404, "Пара не входит в выбранный расчёт")
    else:
        obj = db.get(Item, ident)
        if (
            not obj
            or obj.session_id != sid
            or not obj.passport
            or not db.scalar(
                select(Match.id)
                .where(
                    Match.job_id == job.id,
                    or_(Match.item_id == ident, Match.requirement_id == ident),
                )
                .limit(1)
            )
        ):
            raise HTTPException(404, "Паспорт не входит в выбранный расчёт")
    return obj


@router.get("/quality/{sid}")
def summary(sid: str, db=Depends(get_db), user=Depends(get_current_user)):
    own_session(db, sid, user)
    return evaluate(db, sid)


@router.get("/quality/{sid}/targets")
def targets(
    sid: str,
    kind: Literal["candidate", "passport"] = "candidate",
    shortlisted: bool = False,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    q: str = Query("", max_length=255),
    unreviewed: bool = False,
    requirement_id: str | None = None,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    own_session(db, sid, user)
    job = current(db, sid)
    if kind == "candidate":
        stmt = select(Match).where(Match.job_id == job.id)
        if shortlisted:
            stmt = stmt.where(Match.result["is_shortlisted"].as_boolean() == True)
        stmt = stmt.order_by(
            Match.requirement_id,
            func.coalesce(Match.result["rank"].as_integer(), 10**9),
            Match.id,
        )
    else:
        ids = (
            select(Match.item_id)
            .where(Match.job_id == job.id)
            .union(select(Match.requirement_id).where(Match.job_id == job.id))
        )
        stmt = select(Item).where(Item.id.in_(ids)).order_by(Item.kind, Item.id)
    if kind == "candidate" and requirement_id:
        stmt = stmt.where(Match.requirement_id == requirement_id)
    if q.strip():
        if kind == "candidate":
            matching_items = select(Item.id).where(
                Item.session_id == sid,
                Item.source_text.icontains(q.strip(), autoescape=True),
            )
            stmt = stmt.where(
                or_(
                    Match.item_id.in_(matching_items),
                    Match.requirement_id.in_(matching_items),
                )
            )
        else:
            stmt = stmt.where(Item.source_text.icontains(q.strip(), autoescape=True))
    if unreviewed:
        reviewed = select(QualityReview.target_id).where(
            QualityReview.job_id == job.id, QualityReview.kind == kind
        )
        stmt = stmt.where(
            (Match.id if kind == "candidate" else Item.id).not_in(reviewed)
        )
    total = (
        db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    )
    rows = list(db.scalars(stmt.limit(limit).offset(offset)))
    reviews = {
        r.target_id: public(r)
        for r in db.scalars(
            select(QualityReview).where(
                QualityReview.job_id == job.id,
                QualityReview.kind == kind,
                QualityReview.target_id.in_([r.id for r in rows]),
            )
        )
    }
    result = []
    for obj in rows:
        value = public(obj)
        if kind == "candidate":
            value.update(
                residual=public(db.get(Item, obj.item_id)),
                requirement=public(db.get(Item, obj.requirement_id)),
            )
        result.append({"target": value, "review": reviews.get(obj.id)})
    return {"job_id": job.id, "total": total, "items": result}


@router.put("/quality/{sid}/{kind}/{ident}")
def save(
    sid: str,
    kind: Literal["candidate", "passport"],
    ident: str,
    payload: ReviewInput,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    require_expert(user)
    s = own_session(db, sid, user, lock=True)
    job = current(db, sid, payload.job_id, True)
    obj = target(db, sid, job, kind, ident)
    if kind == "candidate":
        if (
            payload.suitable is None
            or payload.attributes
            or payload.category_reviewed
            or payload.category_id is not None
        ):
            raise HTTPException(
                422, "Укажите только подходит / не подходит и комментарий"
            )
        answer = {"suitable": payload.suitable, "comment": payload.comment}
        prediction = deepcopy(obj.result)
    else:
        if payload.suitable is not None:
            raise HTTPException(422, "Оценка кандидата сохраняется отдельно")
        features = {f["key"]: f for f in s.schema_doc["features"]}
        if not payload.attributes and not payload.category_reviewed:
            raise HTTPException(
                422, "Отметьте хотя бы один проверенный параметр или категорию"
            )
        for key, v in payload.attributes.items():
            f = features.get(key)
            if not f:
                raise HTTPException(422, "Неизвестный параметр")
            if v is None:
                continue
            if f["kind"] == "string":
                if not isinstance(v, str) or not v.strip():
                    raise HTTPException(
                        422, "Для отсутствующего значения используйте null"
                    )
                payload.attributes[key] = " ".join(v.split())
            elif (
                isinstance(v, bool)
                or not isinstance(v, (int, float))
                or not isfinite(v)
                or (f["kind"] == "integer" and int(v) != v)
            ):
                raise HTTPException(422, "Укажите конечное число в единицах схемы")
        if (
            payload.category_reviewed
            and payload.category_id is not None
            and payload.category_id not in {c["id"] for c in s.schema_doc["categories"]}
        ):
            raise HTTPException(422, "Неизвестная категория")
        answer = {
            "attributes": payload.attributes,
            "category_reviewed": payload.category_reviewed,
            "category_id": payload.category_id,
            "comment": payload.comment,
        }
        prediction = deepcopy(obj.passport)
    review = db.scalar(
        select(QualityReview).where(
            QualityReview.job_id == job.id,
            QualityReview.kind == kind,
            QualityReview.target_id == ident,
        )
    )
    if review is None:
        review = QualityReview(
            session_id=sid,
            job_id=job.id,
            kind=kind,
            target_id=ident,
            prediction=prediction,
            answer=answer,
            reviewer_id=user.id,
        )
        db.add(review)
    else:
        review.answer = answer
        review.prediction = prediction
        review.reviewer_id = user.id
        review.updated_at = now()
    db.execute(delete(QualityCompletion).where(QualityCompletion.job_id == job.id))
    record(
        db,
        sid,
        "QUALITY_REVIEW_SAVED",
        {"job_id": job.id, "kind": kind, "target_id": ident, "answer": answer},
        user.id,
    )
    db.commit()
    return public(review)


@router.delete("/quality/{sid}/{kind}/{ident}")
def remove(
    sid: str,
    kind: Literal["candidate", "passport"],
    ident: str,
    job_id: str,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    require_expert(user)
    own_session(db, sid, user, lock=True)
    job = current(db, sid, job_id, True)
    target(db, sid, job, kind, ident)
    db.execute(
        delete(QualityReview).where(
            QualityReview.job_id == job.id,
            QualityReview.kind == kind,
            QualityReview.target_id == ident,
        )
    )
    db.execute(delete(QualityCompletion).where(QualityCompletion.job_id == job.id))
    record(
        db,
        sid,
        "QUALITY_REVIEW_REMOVED",
        {"job_id": job.id, "kind": kind, "target_id": ident},
        user.id,
    )
    db.commit()
    return {"removed": True}


@router.post("/quality/{sid}/finalize")
def finalize(
    sid: str, payload: FinishInput, db=Depends(get_db), user=Depends(get_current_user)
):
    require_expert(user)
    own_session(db, sid, user, lock=True)
    job = current(db, sid, payload.job_id, True)
    if not db.scalar(
        select(QualityReview.id).where(QualityReview.job_id == job.id).limit(1)
    ):
        raise HTTPException(422, "Нет проверенных данных")
    completed = db.get(QualityCompletion, job.id)
    if completed is None:
        db.add(QualityCompletion(session_id=sid, job_id=job.id, reviewer_id=user.id))
    else:
        completed.finalized_at = now()
        completed.reviewer_id = user.id
    record(db, sid, "QUALITY_REVIEW_FINALIZED", {"job_id": job.id}, user.id)
    db.commit()
    return evaluate(db, sid)

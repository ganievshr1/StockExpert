"""HTTP adapters for workspace read models and explicit taxonomy additions."""

from datetime import date
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import get_current_user, own_session, require_expert
from app.api.routes import latest_results, no_active
from app.db import get_db
from app.services import workspace
from app.services.audit import record
from app.services.contracts import Feature, SchemaDraft
from app.services.taxonomy import root_id
from app.services.report_document import ReportSections, build_report

router = APIRouter()


@router.get("/reports/{sid}/document")
def report_document(
    sid: str,
    format: Literal["pdf", "xlsx"] = "pdf",
    summary: bool = True,
    candidates: bool = True,
    evidence: bool = True,
    history: bool = True,
    inline: bool = False,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    session = own_session(db, sid, user)
    sections = ReportSections(summary, candidates, evidence, history)
    if not sections.any():
        raise HTTPException(422, "Выберите хотя бы один раздел отчёта")
    content = build_report(db, session, latest_results(db, sid), sections, format)
    media_type = (
        "application/pdf"
        if format == "pdf"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    disposition = "inline" if inline else "attachment"
    return Response(
        content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'{disposition}; filename="stockexpert-report.{format}"'
        },
    )


@router.get("/workspace/sessions")
def sessions(
    q: str = Query("", max_length=255),
    status: str = "ALL",
    days: int = Query(0, ge=0, le=3650),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    return workspace.session_page(
        db, user.organization_id, q, status, days, offset, limit
    )


@router.get("/workspace/overview")
def overview(
    days: int = Query(0, ge=0, le=3650),
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    return workspace.workspace_overview(db, user.organization_id, days)


@router.get("/audit/{sid}/search")
def audit_search(
    sid: str,
    q: str = Query("", max_length=255),
    event_type: str = "ALL",
    author_id: int | None = Query(None, ge=0),
    date_from: date | None = None,
    date_to: date | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    own_session(db, sid, user)
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "Начало периода позже окончания")
    return workspace.audit_page(
        db, sid, q, event_type, author_id, date_from, date_to, offset, limit
    )


class CategoryAddition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=150)
    parent_id: str | None = None


class FeatureAddition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category_id: str
    feature: Feature


def editable_taxonomy(db, sid, user):
    require_expert(user)
    session = own_session(db, sid, user, lock=True)
    no_active(db, sid)
    if session.approved or session.schema_doc.get("taxonomy_status") != "review":
        raise HTTPException(
            409, "Добавление доступно после построения категорий и до утверждения схемы"
        )
    return session, SchemaDraft.model_validate(session.schema_doc).model_dump()


def save_addition(db, session, schema, event, details, user):
    try:
        session.schema_doc = SchemaDraft.model_validate(schema).model_dump()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    record(db, session.id, event, details, user.id)
    db.commit()
    return session.schema_doc


@router.post("/taxonomy/{sid}/categories", status_code=201)
def add_category(
    sid: str,
    payload: CategoryAddition,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    session, schema = editable_taxonomy(db, sid, user)
    name = payload.name.strip()
    if not name:
        raise HTTPException(422, "Введите название категории")
    parent = next(
        (
            category
            for category in schema["categories"]
            if category["id"] == payload.parent_id
        ),
        None,
    )
    if payload.parent_id and parent is None:
        raise HTTPException(422, "Родительская категория не найдена")
    if any(
        category["parent_id"] == payload.parent_id
        and category["name"].casefold() == name.casefold()
        for category in schema["categories"]
    ):
        raise HTTPException(409, "Категория с таким названием уже существует")
    category = {
        "id": "manual_" + uuid4().hex,
        "name": name,
        "parent_id": payload.parent_id,
        "feature_keys": list(parent["feature_keys"])
        if parent
        else [
            feature["key"] for feature in schema["features"] if feature["key"] == "name"
        ],
        "aliases": [],
        "item_count": 0,
        "requirement_count": 0,
    }
    schema["categories"].append(category)
    return save_addition(db, session, schema, "CATEGORY_ADDED", category, user)


@router.post("/taxonomy/{sid}/features", status_code=201)
def add_feature(
    sid: str,
    payload: FeatureAddition,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    session, schema = editable_taxonomy(db, sid, user)
    root = root_id(schema, payload.category_id)
    if not root:
        raise HTTPException(422, "Категория не найдена")
    if any(feature["key"] == payload.feature.key for feature in schema["features"]):
        raise HTTPException(409, "Ключ признака уже существует")
    feature = payload.feature.model_dump()
    schema["features"].append(feature)
    for category in schema["categories"]:
        if root_id(schema, category["id"]) == root:
            category["feature_keys"].append(feature["key"])
    try:
        return save_addition(
            db,
            session,
            schema,
            "FEATURE_ADDED",
            {"material_id": root, "feature": feature},
            user,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None

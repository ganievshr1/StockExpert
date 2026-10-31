import asyncio, csv, hashlib, io, json, uuid
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, Request
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, func, delete, or_
from sqlalchemy.exc import IntegrityError
from app.db import (
    get_db,
    SessionLocal,
    Organization,
    User,
    ProcurementSession,
    Document,
    Item,
    Job,
    Match,
    Audit,
    public,
)
from app.api.deps import get_current_user, require_editor, require_expert, own_session
from app.core.config import settings
from app.core.security import verify_password, create_access_token
from app.services import storage, taxonomy as taxonomy_service
from app.services import model_registry, normalization_engine
from app.services.llm import LLMError
from app.services.inventory import available_item_ids, stock_state
from app.services.parsing import sheets, document_text
from app.services.contracts import SchemaDraft
from app.services.workflow import validate_mapping
from app.services.audit import record, verify

router = APIRouter()


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionCreate(Input):
    title: str = Field(min_length=1, max_length=255)
    base_session_id: str | None = None
    initiator_dept: str | None = None
    category_hint: str | None = None
    llm_profile_id: str | None = Field(default=None, max_length=80)
    taxonomy_enabled: bool = False
    normalization_method: Literal['llm', 'regex'] = 'llm'


class NormalizationSelection(Input):
    normalization_method: Literal['llm', 'regex']


class ModelSelection(Input):
    llm_profile_id: str = Field(min_length=1, max_length=80)


class OrganizationCreate(Input):
    name: str = Field(min_length=1, max_length=255)
    inn: str | None = Field(default=None, pattern=r"^\d{10}(\d{2})?$")
    industry_sector: str = "ВИНК"
    company_type: str = "head"
    is_active: bool = True


class OrganizationUpdate(Input):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None


class Start(Input):
    target_shortlist_size: int = Field(default=150, ge=1, le=1000)


class Normalize(Input):
    batch_size: int = Field(default=1, ge=1, le=16)
    grammar_strict: bool = True
    model: str | None = None


class Approval(Input):
    freeze_version: str = Field(min_length=1, max_length=50)
    comments: str = ""


class Decision(Input):
    item_id: str  # match result UUID, not residual UUID
    decision: str
    allocated_qty: float = 0
    comment: str = ""


class HashCheck(Input):
    expected_root_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def no_active(db, sid):
    if db.scalar(
        select(Job).where(
            Job.session_id == sid,
            Job.status.in_(["QUEUED", "RUNNING", "CANCEL_REQUESTED"]),
        )
    ):
        raise HTTPException(409, "Session already has an active job")


def enqueue(db, s, user, kind, params=None):
    require_editor(user)
    no_active(db, s.id)
    from app.services.run_history import capture_latest
    capture_latest(db, s)
    config = model_registry.session_config(db, s)
    if normalization_engine.needs_llm(kind, s.normalization_method, s.taxonomy_enabled):
        try:
            model_registry.preflight(config)
        except LLMError as exc:
            raise HTTPException(503, str(exc)) from None
    s.llm_config = config.model_dump()
    job = Job(
        session_id=s.id,
        kind=kind,
        params={**(params or {}), "llm_config": config.model_dump(),
                "normalization_config": normalization_engine.config(s.normalization_method), "taxonomy_enabled": s.taxonomy_enabled},
    )
    db.add(job)
    db.flush()
    s.status = "QUEUED"
    record(
        db,
        s.id,
        "JOB_QUEUED",
        {"job_id": job.id, "kind": kind, "llm_config": config.model_dump(),
         "normalization_config": job.params['normalization_config']},
        user.id,
    )
    db.commit()
    # Beat publishes DB outbox; API success does not depend on broker availability.
    return {
        "task_id": job.id,
        "status": job.status,
        "message": "Saved to durable queue",
    }


def latest_match_job(db, sid):
    return db.scalar(
        select(Job.id)
        .where(
            Job.session_id == sid,
            Job.kind.in_(["match", "full"]),
            Job.status == "COMPLETED",
        )
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(1)
    )


def match_statement(db, sid):
    jid = latest_match_job(db, sid)
    return (
        select(Match).where(Match.session_id == sid, Match.job_id == jid)
        if jid
        else select(Match).where(False)
    )


def latest_results(db, sid):
    return list(db.scalars(match_statement(db, sid)))


def match_counts(db, sid):
    base = match_statement(db, sid)

    def count(*clauses):
        return (
            db.scalar(select(func.count()).select_from(base.where(*clauses).subquery()))
            or 0
        )

    return {
        "pairs_evaluated": count(),
        "shortlisted_pairs": count(
            Match.result["is_shortlisted"].as_boolean() == True,
            Match.item_id.in_(available_item_ids(db)),
        ),
        "contradicted_pairs": count(
            Match.result["state"].as_string() == "CONTRADICTED"
        ),
        "needs_review_pairs": count(
            Match.result["state"].as_string() == "NEEDS_REVIEW"
        ),
    }


def result_public(m):
    return {**public(m), **m.result}


def job_status(db, sid):
    j = db.scalar(
        select(Job)
        .where(Job.session_id == sid)
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    return public(j) if j else {"status": "NOT_STARTED"}


@router.post("/auth/token")
def login(form: OAuth2PasswordRequestForm = Depends(), db=Depends(get_db)):
    u = db.scalar(select(User).where(User.username == form.username))
    if not u or not u.is_active or not verify_password(form.password, u.password_hash):
        raise HTTPException(401, "Invalid credentials")
    return {
        "access_token": create_access_token(u),
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "role": u.role,
    }


@router.get("/auth/me")
def me(user=Depends(get_current_user)):
    return public(user)


@router.post("/auth/refresh")
def refresh(user=Depends(get_current_user)):
    return {"access_token": create_access_token(user), "token_type": "bearer"}


@router.get("/organizations")
def organizations(
    industry_sector: str | None = None,
    is_active: bool | None = None,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    stmt = select(Organization)
    if user.role != "admin":
        stmt = stmt.where(Organization.id == user.organization_id)
    if industry_sector:
        stmt = stmt.where(Organization.industry_sector == industry_sector)
    if is_active is not None:
        stmt = stmt.where(Organization.is_active == is_active)
    return [public(o) for o in db.scalars(stmt)]


@router.post("/organizations", status_code=201)
def create_org(
    payload: OrganizationCreate, db=Depends(get_db), user=Depends(get_current_user)
):
    if user.role != "admin":
        raise HTTPException(403, "Admin required")
    o = Organization(**payload.model_dump())
    db.add(o)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Organization already exists")
    return public(o)


def org_access(db, oid, user):
    if user.role != "admin" and oid != user.organization_id:
        raise HTTPException(404, "Organization not found")
    o = db.get(Organization, oid)
    if not o:
        raise HTTPException(404, "Organization not found")
    return o


@router.get("/organizations/{oid}")
def get_org(oid: int, db=Depends(get_db), user=Depends(get_current_user)):
    return public(org_access(db, oid, user))


@router.put("/organizations/{oid}")
def update_org(
    oid: int,
    payload: OrganizationUpdate,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    if user.role != "admin":
        raise HTTPException(403, "Admin required")
    o = org_access(db, oid, user)
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(o, k, v)
    db.commit()
    return public(o)


@router.delete("/organizations/{oid}")
def delete_org(oid: int, db=Depends(get_db), user=Depends(get_current_user)):
    if user.role != "admin":
        raise HTTPException(403, "Admin required")
    o = org_access(db, oid, user)
    o.is_active = False
    db.commit()
    return {"status": "deactivated"}


@router.get("/organizations/{oid}/users")
def org_users(oid: int, db=Depends(get_db), user=Depends(get_current_user)):
    org_access(db, oid, user)
    return [
        public(u) for u in db.scalars(select(User).where(User.organization_id == oid))
    ]


@router.post("/procurement/sessions", status_code=201)
def create_session(
    payload: SessionCreate, db=Depends(get_db), user=Depends(get_current_user)
):
    require_editor(user)
    try:
        config = (
            model_registry.get_profile(payload.llm_profile_id)
            if payload.llm_profile_id
            else model_registry.default_config()
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    s = ProcurementSession(
        title=payload.title,
        organization_id=user.organization_id,
        created_by=user.id,
        llm_config=config.model_dump(),
        normalization_method=payload.normalization_method,
        taxonomy_enabled=payload.taxonomy_enabled,
    )
    db.add(s)
    db.flush()
    if payload.base_session_id:
        from app.services.bases import attach_base

        attach_base(db, s, payload.base_session_id, user)
        from app.services.flat_schema import configure_reused
        configure_reused(db, s)
    record(
        db,
        s.id,
        "SESSION_CREATED",
        {**payload.model_dump(), "llm_config": s.llm_config},
        user.id,
    )
    db.commit()
    return session_public(db, s)


@router.get("/procurement/sessions")
def list_sessions(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    return [
        public(s)
        for s in db.scalars(
            select(ProcurementSession)
            .where(ProcurementSession.organization_id == user.organization_id)
            .order_by(ProcurementSession.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ]


@router.get("/procurement/sessions/{sid}")
def get_session(sid: str, db=Depends(get_db), user=Depends(get_current_user)):
    return session_public(db, own_session(db, sid, user))


def session_public(db, s):
    return {
        **public(s),
        "llm_locked": model_registry.selection_locked(db, s),
        "llm_legacy": not bool(s.llm_config),
        "llm_config": model_registry.session_config(db, s).model_dump(),
        "normalization_locked": normalization_engine.selection_locked(db, s),
    }


@router.put('/procurement/sessions/{sid}/normalization-method')
def select_normalization_method(sid: str, payload: NormalizationSelection, db=Depends(get_db), user=Depends(get_current_user)):
    require_editor(user)
    s = own_session(db, sid, user, lock=True)
    no_active(db, sid)
    if normalization_engine.selection_locked(db, s):
        raise HTTPException(409, 'Метод H1 зафиксирован. Для сравнения создайте новую сессию и загрузите исходный Excel; готовая база сохраняет прежние паспорта.')
    previous = s.normalization_method
    s.normalization_method = payload.normalization_method
    record(db, sid, 'NORMALIZATION_METHOD_SELECTED', {'previous': previous, 'method': s.normalization_method}, user.id)
    db.commit()
    return session_public(db, s)


@router.put("/procurement/sessions/{sid}/llm")
def select_model(
    sid: str,
    payload: ModelSelection,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    require_editor(user)
    s = own_session(db, sid, user, lock=True)
    no_active(db, sid)
    if model_registry.selection_locked(db, s):
        raise HTTPException(
            409,
            "Модель зафиксирована для этой сессии. Для сравнения создайте новую сессию с другой моделью.",
        )
    try:
        config = model_registry.get_profile(payload.llm_profile_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    previous = s.llm_config
    s.llm_config = config.model_dump()
    record(
        db,
        sid,
        "LLM_SELECTED",
        {"previous": previous, "llm_config": s.llm_config},
        user.id,
    )
    db.commit()
    return session_public(db, s)


def upload(sid, file, kind, db, user, replace_id=None):
    require_editor(user)
    s = own_session(db, sid, user, lock=True)
    no_active(db, sid)
    reuse_requirement = (
        bool(s.base_snapshot)
        and kind == "requirement"
        and not db.scalar(select(Job.id).where(Job.session_id == sid).limit(1))
    )
    if s.schema_doc and not reuse_requirement:
        raise HTTPException(
            409, "Schema already proposed. Create a new session to change source files."
        )
    replaced = None
    if replace_id:
        replaced = db.get(Document, replace_id)
        if not replaced or replaced.session_id != sid or replaced.kind != kind:
            raise HTTPException(404, "Document not found")
        if db.scalar(select(Job.id).where(Job.session_id == sid).limit(1)):
            raise HTTPException(
                409, "Source replacement is only available before processing starts"
            )
    name = Path(file.filename or "").name
    allowed = {".xlsx"} if kind == "residual" else {".xlsx", ".pdf", ".docx", ".txt"}
    if Path(name).suffix.lower() not in allowed:
        raise HTTPException(415, "Unsupported file type")
    data = file.file.read(settings.MAX_UPLOAD_BYTES + 1)
    if not data or len(data) > settings.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Empty or oversized file")
    try:
        if kind == "residual":
            preview = list(sheets(data))
            if not preview:
                raise ValueError("No Excel data")
            meta = {
                "sheets": [
                    {"name": n, "rows": len(rows), "preview": rows[:10]}
                    for n, rows in preview
                ]
            }
        else:
            meta = {"text": document_text(data, name)}
        meta["size_bytes"] = len(data)
    except Exception as exc:
        raise HTTPException(422, f"Cannot parse file: {exc}")
    sha = hashlib.sha256(data).hexdigest()
    old = db.scalar(
        select(Document).where(
            Document.session_id == sid, Document.kind == kind, Document.sha256 == sha
        )
    )
    if old:
        if replaced and old.id != replaced.id:
            raise HTTPException(409, "This file already exists in the session")
        return public(old)
    key = f"{user.organization_id}/{sid}/{uuid.uuid4()}{Path(name).suffix.lower()}"
    storage.put(key, data)
    d = Document(
        session_id=sid, kind=kind, filename=name, object_key=key, sha256=sha, meta=meta
    )
    db.add(d)
    db.flush()
    if kind == "residual":
        for sheet_name, rows in preview:
            for row in rows:
                db.add(
                    Item(
                        session_id=sid,
                        document_id=d.id,
                        kind="residual",
                        sheet=sheet_name,
                        row_index=row["row_index"],
                        raw=row["cells"],
                    )
                )
    old_key = replaced.object_key if replaced else None
    if replaced:
        db.execute(delete(Item).where(Item.document_id == replaced.id))
        record(
            db,
            sid,
            "DOCUMENT_REPLACED",
            {"previous_document_id": replaced.id, "document_id": d.id},
            user.id,
        )
        db.delete(replaced)
    s.status = "UPLOADED"
    record(
        db,
        sid,
        "DOCUMENT_UPLOADED",
        {"document_id": d.id, "sha256": sha, "kind": kind},
        user.id,
    )
    db.commit()
    if old_key:
        try:
            storage.delete_key(old_key)
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "Replaced source cleanup deferred for session %s", sid
            )
    return public(d)


@router.post("/procurement/sessions/{sid}/documents/{did}/replace")
def replace_document(
    sid: str,
    did: str,
    file: UploadFile = File(...),
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    own_session(db, sid, user)
    document = db.get(Document, did)
    if not document or document.session_id != sid:
        raise HTTPException(404, "Document not found")
    return upload(sid, file, document.kind, db, user, replace_id=did)


@router.post("/procurement/sessions/{sid}/upload-residuals", status_code=201)
def upload_residuals(
    sid: str,
    file: UploadFile = File(...),
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    return upload(sid, file, "residual", db, user)


@router.post("/procurement/sessions/{sid}/upload-requirement", status_code=201)
def upload_requirement(
    sid: str,
    file: UploadFile = File(...),
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    return upload(sid, file, "requirement", db, user)


@router.get("/procurement/sessions/{sid}/documents")
def documents(sid: str, db=Depends(get_db), user=Depends(get_current_user)):
    own_session(db, sid, user)
    return [
        public(d)
        for d in db.scalars(select(Document).where(Document.session_id == sid))
    ]


@router.get("/procurement/sessions/{sid}/metrics")
def metrics(sid: str, db=Depends(get_db), user=Depends(get_current_user)):
    own_session(db, sid, user)
    base = select(Item).where(Item.session_id == sid, Item.kind == "residual")
    total = (
        db.scalar(
            select(func.count()).select_from(
                base.where(Item.source_text != "").subquery()
            )
        )
        or 0
    )
    normalized_count = (
        db.scalar(
            select(func.count()).select_from(
                base.where(
                    Item.passport["attributes"].as_string().is_not(None)
                ).subquery()
            )
        )
        or 0
    )
    counts = match_counts(db, sid)
    return {
        "total_rows": total,
        "normalized_items": normalized_count,
        "shortlisted_pairs": counts["shortlisted_pairs"],
        "contradicted_pairs": counts["contradicted_pairs"],
        "time_saved_hours": None,
        "potential_savings_rub": None,
    }


@router.get("/procurement/sessions/{sid}/stream-progress")
def stream(
    sid: str, request: Request, db=Depends(get_db), user=Depends(get_current_user)
):
    own_session(db, sid, user)

    async def events():
        for _ in range(300):
            if await request.is_disconnected():
                break
            with SessionLocal() as session:
                s = session.get(ProcurementSession, sid)
                if s is None:
                    break
                j = job_status(session, sid)
                data = {"status": s.status, "progress": s.progress, "job": j}
            yield "data: " + json.dumps(data, default=str, ensure_ascii=False) + "\n\n"
            if j.get("status") not in ("QUEUED", "RUNNING", "CANCEL_REQUESTED"):
                break
            await asyncio.sleep(2)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/pipeline/{sid}/prepare", status_code=202)
@router.post("/taxonomy/{sid}/bootstrap", status_code=202)
def prepare(sid: str, db=Depends(get_db), user=Depends(get_current_user)):
    s = own_session(db, sid, user, lock=True)
    if s.approved or s.schema_doc:
        raise HTTPException(
            409, "Schema already exists; edit draft or create a new session"
        )
    kinds = set(db.scalars(select(Document.kind).where(Document.session_id == sid)))
    if not {"residual", "requirement"} <= kinds:
        raise HTTPException(409, "Upload both residuals and requirement")
    return enqueue(db, s, user, "prepare")


@router.get("/pipeline/{sid}/schema")
def schema(sid: str, db=Depends(get_db), user=Depends(get_current_user)):
    return own_session(db, sid, user).schema_doc


@router.put("/pipeline/{sid}/schema")
def update_schema(
    sid: str, payload: SchemaDraft, db=Depends(get_db), user=Depends(get_current_user)
):
    require_expert(user)
    s = own_session(db, sid, user, lock=True)
    no_active(db, sid)
    if s.approved:
        raise HTTPException(
            409, "Approved schema is immutable; create a new session/version"
        )
    data = payload.model_dump()
    try:
        taxonomy_service.check_review_edit(s.schema_doc, data)
        validate_mapping(db, sid, data)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    s.schema_doc = data
    record(db, sid, "SCHEMA_EDITED", {"schema": data}, user.id)
    db.commit()
    return data


@router.post("/pipeline/{sid}/approve-mapping")
def approve_mapping(sid: str, db=Depends(get_db), user=Depends(get_current_user)):
    require_expert(user)
    s = own_session(db, sid, user, lock=True)
    no_active(db, sid)
    if not taxonomy_service.is_new(s.schema_doc):
        raise HTTPException(409, "Legacy session: approve the existing schema")
    if s.schema_doc.get("mapping_approved"):
        raise HTTPException(409, "Column mapping already approved")
    try:
        SchemaDraft.model_validate(s.schema_doc)
        validate_mapping(db, sid, s.schema_doc)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    s.schema_doc = {**s.schema_doc, "mapping_approved": True}
    s.status = "MAPPING_APPROVED"
    record(db, sid, "MAPPING_APPROVED", {"mappings": s.schema_doc["mappings"]}, user.id)
    db.commit()
    return public(s)


@router.post("/taxonomy/{sid}/rebuild", status_code=202)
def rebuild_taxonomy(sid: str, db=Depends(get_db), user=Depends(get_current_user)):
    require_expert(user)
    s = own_session(db, sid, user, lock=True)
    if (
        not s.taxonomy_enabled
        or not taxonomy_service.is_new(s.schema_doc)
        or not s.schema_doc.get("mapping_approved")
        or s.approved
    ):
        raise HTTPException(409, "Rebuild requires an unapproved passport taxonomy")
    return enqueue(db, s, user, "taxonomy")


@router.get("/taxonomy/{sid}/tree")
def taxonomy(sid: str, db=Depends(get_db), user=Depends(get_current_user)):
    s = own_session(db, sid, user)
    return {
        "categories": s.schema_doc.get("categories", []),
        "approved": s.approved,
        "version": s.taxonomy_version,
    }


@router.put("/taxonomy/{sid}/categories/{cat_id}")
def update_category(
    sid: str,
    cat_id: str,
    payload: dict,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    require_expert(user)
    s = own_session(db, sid, user, lock=True)
    no_active(db, sid)
    if s.approved:
        raise HTTPException(409, "Taxonomy frozen")
    if set(payload) - {"name", "parent_id"}:
        raise HTTPException(422, "Use schema endpoint to edit feature definitions")
    data = json.loads(json.dumps(s.schema_doc))
    category = next((c for c in data.get("categories", []) if c["id"] == cat_id), None)
    if category is None:
        raise HTTPException(404, "Category not found")
    category.update(payload)
    try:
        data = SchemaDraft.model_validate(data).model_dump()
        taxonomy_service.check_review_edit(s.schema_doc, data)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    s.schema_doc = data
    record(db, sid, "CATEGORY_EDITED", {"category": category}, user.id)
    db.commit()
    return category


@router.post("/taxonomy/{sid}/approve")
def approve(
    sid: str, payload: Approval, db=Depends(get_db), user=Depends(get_current_user)
):
    require_expert(user)
    s = own_session(db, sid, user, lock=True)
    no_active(db, sid)
    if s.approved:
        raise HTTPException(409, "Already approved")
    if (
        taxonomy_service.is_new(s.schema_doc)
        and s.schema_doc.get("taxonomy_status") != "review"
    ):
        raise HTTPException(409, "Normalize all positions and build taxonomy first")
    try:
        SchemaDraft.model_validate(s.schema_doc)
        validate_mapping(db, sid, s.schema_doc)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    s.approved = True
    s.taxonomy_version = payload.freeze_version
    s.status = "APPROVED"
    if taxonomy_service.is_new(s.schema_doc):
        s.schema_doc = {**s.schema_doc, "taxonomy_status": "approved"}
    record(
        db,
        sid,
        "SCHEMA_APPROVED",
        {
            "version": payload.freeze_version,
            "schema": s.schema_doc,
            "comments": payload.comments,
        },
        user.id,
    )
    db.commit()
    return public(s)


@router.post("/pipeline/{sid}/start-normalization", status_code=202)
def start_normalization(
    sid: str, payload: Normalize, db=Depends(get_db), user=Depends(get_current_user)
):
    s = own_session(db, sid, user, lock=True)
    if not (
        s.schema_doc.get("mapping_approved")
        if taxonomy_service.is_new(s.schema_doc)
        else s.approved
    ):
        raise HTTPException(409, "Prepare and approve column mapping first")
    if not db.scalar(
        select(Document.id)
        .where(Document.session_id == sid, Document.kind == "requirement")
        .limit(1)
    ):
        raise HTTPException(409, "Upload a requirement document first")
    if not payload.grammar_strict or (
        payload.model and payload.model != model_registry.session_config(db, s).model
    ):
        raise HTTPException(
            422, "Используйте выбранную модель сессии и строгую проверку схемы"
        )
    return enqueue(db, s, user, "normalize", {"batch_size": payload.batch_size})


@router.post("/pipeline/{sid}/run", status_code=202)
def full_pipeline(
    sid: str, payload: Start, db=Depends(get_db), user=Depends(get_current_user)
):
    s = own_session(db, sid, user, lock=True)
    if not (
        s.schema_doc.get("mapping_approved")
        if taxonomy_service.is_new(s.schema_doc)
        else s.approved
    ):
        raise HTTPException(409, "Prepare and approve column mapping first")
    if not db.scalar(
        select(Document.id)
        .where(Document.session_id == sid, Document.kind == "requirement")
        .limit(1)
    ):
        raise HTTPException(409, "Upload a requirement document first")
    # Repeated runs with a ready base and ready requirements are matching only:
    # no extraction, classification or generative-model availability check.
    from app.services.workflow import requirements_ready

    kind = "match" if s.approved and requirements_ready(db, s) else "full"
    return enqueue(db, s, user, kind, payload.model_dump())


@router.get("/pipeline/{sid}/normalization-status")
@router.get("/matching/{sid}/status")
def status(sid: str, db=Depends(get_db), user=Depends(get_current_user)):
    s = own_session(db, sid, user)
    return {"session_status": s.status, "progress": s.progress, **job_status(db, sid)}


@router.post("/pipeline/{sid}/jobs/{jid}/retry", status_code=202)
def retry_job(sid: str, jid: str, db=Depends(get_db), user=Depends(get_current_user)):
    require_editor(user)
    s = own_session(db, sid, user, lock=True)
    no_active(db, sid)
    job = db.get(Job, jid)
    if not job or job.session_id != sid:
        raise HTTPException(404, "Job not found")
    if job.status != "FAILED":
        raise HTTPException(409, "Only failed jobs may be retried")
    config = model_registry.pin_job_config(db, s, job)
    h1 = normalization_engine.pin_job(job)
    if normalization_engine.needs_llm(job.kind, h1['method'], job.params.get('taxonomy_enabled', True)):
        try:
            model_registry.preflight(config)
        except LLMError as exc:
            raise HTTPException(503, str(exc)) from None
    job.status = "QUEUED"
    job.dispatched_at = None
    job.heartbeat_at = None
    job.error = None
    s.status = "QUEUED"
    record(db, sid, "JOB_REQUEUED", {"job_id": jid}, user.id)
    db.commit()
    return public(job)


@router.get("/pipeline/{sid}/normalization-review")
def normalization_review(
    sid: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """Problem rows are visible before matching, including their source locations."""
    own_session(db, sid, user)
    stmt = select(Item).where(
        Item.session_id == sid,
        or_(
            Item.passport["normalization"]["requires_review"].as_boolean() == True,
            Item.passport["classification"]["requires_review"].as_boolean() == True,
            Item.passport["taxonomy_mapping"]["requires_review"].as_boolean() == True,
        ),
    )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    return {
        "total": total,
        "items": [public(item) for item in db.scalars(
            stmt.order_by(Item.document_id, Item.sheet, Item.row_index, Item.id).limit(limit).offset(offset)
        )],
    }


@router.get("/pipeline/{sid}/passports")
def passports(
    sid: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    base_type: str | None = None,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    own_session(db, sid, user)
    stmt = select(Item).where(
        Item.session_id == sid, Item.passport["attributes"].as_string().is_not(None)
    )
    if base_type:
        s = own_session(db, sid, user)
        descendants = {
            c["id"]
            for c in s.schema_doc.get("categories", [])
            if taxonomy_service.root_id(s.schema_doc, c["id"]) == base_type
        }
        stmt = stmt.where(Item.category.in_(descendants | {base_type}))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    return {
        "total": total,
        "items": [
            public(i)
            for i in db.scalars(stmt.order_by(Item.id).limit(limit).offset(offset))
        ],
    }


@router.get("/pipeline/{sid}/passports/{pid}")
def passport(sid: str, pid: str, db=Depends(get_db), user=Depends(get_current_user)):
    own_session(db, sid, user)
    i = db.get(Item, pid)
    if not i or i.session_id != sid:
        raise HTTPException(404, "Item not found")
    return public(i)


@router.get("/taxonomy/{sid}/categories/{cat_id}/items")
def category_items(
    sid: str,
    cat_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    return passports(sid, limit, offset, cat_id, db, user)


@router.get("/matching/{sid}/benchmark")
def benchmark(sid: str, db=Depends(get_db), user=Depends(get_current_user)):
    own_session(db, sid, user)
    return {
        "items": [
            public(i)
            for i in db.scalars(
                select(Item).where(Item.session_id == sid, Item.kind == "requirement")
            )
        ]
    }


@router.post("/matching/{sid}/run", status_code=202)
def matching(
    sid: str, payload: Start, db=Depends(get_db), user=Depends(get_current_user)
):
    s = own_session(db, sid, user, lock=True)
    if not s.approved or s.status not in (
        "NORMALIZED",
        "MATCHED",
        "FAILED",
        "APPROVED",
        "BASE_READY",
    ):
        raise HTTPException(409, "Normalize and approve taxonomy first")
    return enqueue(db, s, user, "match", payload.model_dump())


@router.get("/matching/{sid}/candidates")
def candidates(
    sid: str,
    status: str = "ALL",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    shortlisted: bool = True,
    requirement_id: str | None = None,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    own_session(db, sid, user)
    stmt = match_statement(db, sid)
    if shortlisted:
        stmt = stmt.where(
            Match.result["is_shortlisted"].as_boolean() == True,
            Match.item_id.in_(available_item_ids(db)),
        )
    if status != "ALL":
        stmt = stmt.where(Match.result["state"].as_string() == status)
    if requirement_id:
        stmt = stmt.where(Match.requirement_id == requirement_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = list(
        db.scalars(
            stmt.order_by(
                Match.requirement_id,
                func.coalesce(Match.result["rank"].as_integer(), 10**9),
                Match.id,
            )
            .limit(limit)
            .offset(offset)
        )
    )
    ids = {i for m in page for i in (m.item_id, m.requirement_id)}
    details = (
        {
            i.id: {
                "id": i.id,
                "source_text": i.source_text,
                "passport": i.passport,
                "issues": i.issues,
                "stock_id": i.stock_id,
                "stock_state": stock_state(db, i),
                "sheet": i.sheet,
                "row_index": i.row_index,
                "document_id": i.document_id,
            }
            for i in db.scalars(
                select(Item).where(Item.session_id == sid, Item.id.in_(ids))
            )
        }
        if ids
        else {}
    )
    return {
        "total": total,
        "items": [
            {
                **result_public(m),
                "residual": details.get(m.item_id),
                "requirement": details.get(m.requirement_id),
            }
            for m in page
        ],
    }


def get_match(db, sid, mid):
    m = db.get(Match, mid)
    if not m or m.session_id != sid:
        raise HTTPException(404, "Match result not found; use match UUID")
    return m


@router.get("/matching/{sid}/candidates/{mid}")
@router.get("/matching/{sid}/rejections/{mid}/explain")
def candidate(sid: str, mid: str, db=Depends(get_db), user=Depends(get_current_user)):
    own_session(db, sid, user)
    return result_public(get_match(db, sid, mid))


@router.post("/matching/{sid}/decide")
def decide(
    sid: str, payload: Decision, db=Depends(get_db), user=Depends(get_current_user)
):
    require_expert(user)
    own_session(db, sid, user, lock=True)
    m = get_match(db, sid, payload.item_id)
    if (
        payload.decision not in ("RECOMMENDED", "REJECTED_MANUAL", "NEEDS_REVIEW")
        or payload.allocated_qty != 0
    ):
        raise HTTPException(
            422,
            "Only recommendations supported; physical allocation requires ERP stock reservation",
        )
    if not payload.comment.strip():
        raise HTTPException(422, "Expert comment required")
    if (
        payload.decision == "RECOMMENDED"
        and stock_state(db, db.get(Item, m.item_id)) != "AVAILABLE"
    ):
        raise HTTPException(409, "Позиция недоступна в общей базе")
    m.decision = payload.decision
    m.expert_comment = payload.comment
    record(db, sid, "DECISION_RECORDED", payload.model_dump(), user.id)
    db.commit()
    return result_public(m)


@router.get("/reports/{sid}/summary")
@router.get("/reports/{sid}/protocol/preview")
def summary(
    sid: str,
    include_items: bool = True,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    s = own_session(db, sid, user)
    return {
        "session_id": sid,
        "title": s.title,
        "document_status": "DRAFT",
        **match_counts(db, sid),
        "savings_rub": None,
        "items": [result_public(m) for m in latest_results(db, sid)]
        if include_items
        else [],
    }


@router.get("/reports/{sid}/export/excel")
@router.get("/matching/{sid}/export")
def export_excel(sid: str, db=Depends(get_db), user=Depends(get_current_user)):
    from openpyxl import Workbook

    own_session(db, sid, user, lock=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Сравнение"
    ws.append(
        [
            "Потребность",
            "Остаток",
            "Статус",
            "Shortlist",
            "Причина",
            "Решение эксперта",
            "Комментарий эксперта",
            "Недостающие признаки",
            "Rank",
            "RRF",
            "CrossEncoder",
            "ID пары",
        ]
    )
    names = {
        i.id: i.source_text
        for i in db.scalars(select(Item).where(Item.session_id == sid))
    }
    for m in latest_results(db, sid):
        row = [
            names.get(m.requirement_id, m.requirement_id),
            names.get(m.item_id, m.item_id),
            m.result["state"],
            str(m.result["is_shortlisted"]),
            m.result["reason"],
            m.decision,
            m.expert_comment or "",
            ", ".join(m.result.get("missing_fields", [])),
            str(m.result.get("rank") or ""),
            str(m.result.get("rrf_score") or ""),
            str(
                m.result.get("cross_encoder_score")
                if m.result.get("cross_encoder_score") is not None
                else ""
            ),
            m.id,
        ]
        for col, value in enumerate(row, 1):
            ws.cell(
                ws.max_row + 1 if col == 1 else ws.max_row, col, value
            ).data_type = "s"
    from app.services.metrics import measurement, add_sheets

    add_sheets(wb, measurement(db, sid))
    out = io.BytesIO()
    wb.save(out)
    data = out.getvalue()
    storage.put(f"{user.organization_id}/{sid}/reports/{uuid.uuid4()}.xlsx", data)
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="stockexpert-report.xlsx"'
        },
    )


@router.get("/reports/{sid}/export/pdf")
def export_pdf(sid: str, db=Depends(get_db), user=Depends(get_current_user)):
    from app.services.reporting import pdf_report

    s = own_session(db, sid, user, lock=True)
    data = pdf_report(
        s,
        latest_results(db, sid),
        {
            i.id: i.source_text
            for i in db.scalars(select(Item).where(Item.session_id == sid))
        },
    )
    storage.put(f"{user.organization_id}/{sid}/reports/{uuid.uuid4()}.pdf", data)
    return Response(
        data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'attachment; filename="stockexpert-report.pdf"'
        },
    )


@router.post("/reports/{sid}/send-to-sed")
def sed(sid: str, db=Depends(get_db), user=Depends(get_current_user)):
    require_editor(user)
    own_session(db, sid, user)
    raise HTTPException(
        501, "SED connector and qualified signing not configured; export draft PDF/XLSX"
    )


@router.get("/audit/{sid}/events")
def audit_events(
    sid: str,
    event_type: str = "ALL",
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    own_session(db, sid, user)
    stmt = select(Audit).where(Audit.session_id == sid)
    if event_type != "ALL":
        stmt = stmt.where(Audit.event_type == event_type)
    return {
        "events": [
            public(a)
            for a in db.scalars(stmt.order_by(Audit.id).limit(limit).offset(offset))
        ]
    }


@router.post("/audit/{sid}/verify-hash")
def verify_hash(
    sid: str, payload: HashCheck, db=Depends(get_db), user=Depends(get_current_user)
):
    own_session(db, sid, user)
    valid, root = verify(
        list(
            db.scalars(select(Audit).where(Audit.session_id == sid).order_by(Audit.id))
        )
    )
    return {
        "verified": valid and root == payload.expected_root_hash,
        "chain_valid": valid,
        "root_hash": root,
    }


@router.get("/audit/{sid}/trail")
def trail(sid: str, db=Depends(get_db), user=Depends(get_current_user)):
    own_session(db, sid, user)
    rows = list(
        db.scalars(select(Audit).where(Audit.session_id == sid).order_by(Audit.id))
    )
    valid, root = verify(rows)
    return {
        "events_chain": [public(r) for r in rows],
        "chain_valid": valid,
        "root_hash": root,
        "note": "Hash chain is not an external signature or WORM storage",
    }


@router.get("/audit/{sid}/export/csv")
def audit_csv(sid: str, db=Depends(get_db), user=Depends(get_current_user)):
    own_session(db, sid, user)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["id", "event", "time", "hash"])
    for a in db.scalars(
        select(Audit).where(Audit.session_id == sid).order_by(Audit.id)
    ):
        writer.writerow([a.id, a.event_type, a.created_at, a.sha256_hash])
    return Response(
        out.getvalue().encode("utf-8-sig"),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit.csv"'},
    )


@router.get("/system/config")
def system_config(user=Depends(get_current_user)):
    config = model_registry.default_config()
    return {
        "api_contract_version": "3.6",
        "ui_contract_version": "3.7",
        "llm_provider": config.provider,
        "llm_model": config.model,
        "external_processing": config.provider == "deepseek",
        "api_key_configured": bool(settings.DEEPSEEK_API_KEY.get_secret_value()),
        "default_llm_config": config.model_dump(),
        "default_normalization_method": "regex",
        "model_selection_supported": True,
        "concurrency": config.concurrency,
        "embedding_model": settings.EMBEDDING_MODEL,
        "reranker_enabled": settings.RERANKER_ENABLED,
    }


@router.get("/system/llm-models")
def llm_models(user=Depends(get_current_user)):
    return model_registry.catalogue()


class TaxonomySelection(Input):
    taxonomy_enabled: bool

@router.put('/procurement/sessions/{sid}/taxonomy-mode')
def select_taxonomy_mode(sid: str, payload: TaxonomySelection, db=Depends(get_db), user=Depends(get_current_user)):
    require_editor(user)
    s=own_session(db,sid,user,lock=True);no_active(db,sid)
    if normalization_engine.selection_locked(db,s):
        raise HTTPException(409,'Режим H2 фиксируется после начала обработки. Создайте новую сессию для другого режима.')
    s.taxonomy_enabled=payload.taxonomy_enabled
    if s.base_snapshot.get('reused_passports'):
        from app.services.flat_schema import configure_reused
        configure_reused(db,s)
    record(db,sid,'H2_MODE_SELECTED',payload.model_dump(),user.id);db.commit()
    return session_public(db,s)

@router.put('/procurement/sessions/{sid}/comparison-rules')
def comparison_rules(sid: str, payload: SchemaDraft, db=Depends(get_db), user=Depends(get_current_user)):
    require_expert(user)
    s=own_session(db,sid,user,lock=True);no_active(db,sid)
    if s.taxonomy_enabled or s.schema_doc.get('taxonomy_status')!='skipped':
        raise HTTPException(409,'Правила без дерева доступны после H1 при отключённом H2')
    data=payload.model_dump(); old={f['key']:f for f in s.schema_doc['features']}
    if set(old)!={f['key'] for f in data['features']} or len(old)!=len(data['features']):
        raise HTTPException(422,'Нельзя менять состав извлечённых характеристик')
    updated=[]
    for f in data['features']:
        before=old[f['key']]
        if any(f.get(k)!=before.get(k) for k in ('label','kind','unit')):
            raise HTTPException(422,'Изменять можно только операцию сравнения и допуск')
        updated.append({**before,'comparison':f['comparison'],'tolerance':f['tolerance']})
    from app.services.run_history import capture_latest
    capture_latest(db,s)
    s.schema_doc={**s.schema_doc,'features':updated}
    record(db,sid,'COMPARISON_RULES_EDITED',{'features':updated},user.id);db.commit()
    return s.schema_doc

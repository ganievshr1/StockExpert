import uuid
from datetime import datetime, timezone
from sqlalchemy import create_engine, String, Integer, JSON, DateTime, ForeignKey, UniqueConstraint, Text, Boolean, Float
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from app.core.config import settings

def uid(): return str(uuid.uuid4())
def now(): return datetime.now(timezone.utc)
from sqlalchemy import URL
# Split credentials avoid URL parsing errors for @, /, :, # and % in passwords.
database_url = URL.create(
    'postgresql+psycopg', username=settings.DB_USER,
    password=settings.POSTGRES_PASSWORD.get_secret_value(), host=settings.DB_HOST,
    port=settings.DB_PORT, database=settings.DB_NAME,
) if settings.DB_HOST else settings.DATABASE_URL
engine = create_engine(database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)
J = JSON().with_variant(JSONB, 'postgresql')
class Base(DeclarativeBase): pass
class Organization(Base):
    __tablename__='organizations'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    inn: Mapped[str|None] = mapped_column(String(12), unique=True)
    industry_sector: Mapped[str] = mapped_column(default='ВИНК')
    company_type: Mapped[str] = mapped_column(default='head')
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
class User(Base):
    __tablename__='users'
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str]
    role: Mapped[str] = mapped_column(default='buyer')
    organization_id: Mapped[int] = mapped_column(ForeignKey('organizations.id'))
    is_active: Mapped[bool] = mapped_column(default=True)
class ProcurementSession(Base):
    __tablename__='procurement_sessions'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    organization_id: Mapped[int] = mapped_column(ForeignKey('organizations.id'), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    title: Mapped[str]
    status: Mapped[str] = mapped_column(default='CREATED')
    progress: Mapped[int] = mapped_column(default=0)
    schema_doc: Mapped[dict] = mapped_column(J, default=dict)
    approved: Mapped[bool] = mapped_column(default=False)
    taxonomy_version: Mapped[str|None]
    base_snapshot: Mapped[dict] = mapped_column(J, default=dict)
    llm_config: Mapped[dict] = mapped_column(J, default=dict)
    taxonomy_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')
    normalization_method: Mapped[str] = mapped_column(String(16), default='llm', server_default='llm')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
class Document(Base):
    __tablename__='documents'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    session_id: Mapped[str] = mapped_column(ForeignKey('procurement_sessions.id'), index=True)
    kind: Mapped[str]
    filename: Mapped[str]
    object_key: Mapped[str]
    sha256: Mapped[str] = mapped_column(String(64))
    meta: Mapped[dict] = mapped_column(J, default=dict)
class StockPosition(Base):
    __tablename__='stock_positions'
    id: Mapped[str] = mapped_column(String(36),primary_key=True,default=uid)
    organization_id: Mapped[int] = mapped_column(ForeignKey('organizations.id'),index=True)
    base_session_id: Mapped[str] = mapped_column(String(36),index=True)
    state: Mapped[str] = mapped_column(String(16),default='AVAILABLE',index=True)
    used_by_session_id: Mapped[str|None] = mapped_column(String(36))
    used_at: Mapped[datetime|None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=now)

class Item(Base):
    __tablename__='items'
    __table_args__=(UniqueConstraint('document_id','sheet','row_index',name='uq_item_source'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    session_id: Mapped[str] = mapped_column(ForeignKey('procurement_sessions.id'), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey('documents.id'))
    stock_id: Mapped[str|None] = mapped_column(ForeignKey('stock_positions.id'),index=True)
    kind: Mapped[str]  # residual or requirement
    sheet: Mapped[str] = mapped_column(default='')
    row_index: Mapped[int]
    raw: Mapped[dict] = mapped_column(J)
    source_text: Mapped[str] = mapped_column(Text, default='')
    passport: Mapped[dict|None] = mapped_column(J)
    category: Mapped[str|None]
    issues: Mapped[list] = mapped_column(J, default=list)
class Job(Base):
    __tablename__='jobs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    session_id: Mapped[str] = mapped_column(ForeignKey('procurement_sessions.id'), index=True)
    kind: Mapped[str]
    status: Mapped[str] = mapped_column(default='QUEUED')
    stage: Mapped[str] = mapped_column(default='QUEUED')
    params: Mapped[dict] = mapped_column(J, default=dict)
    error: Mapped[str|None] = mapped_column(Text)
    dispatched_at: Mapped[datetime|None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime|None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
class Match(Base):
    __tablename__='match_results'
    __table_args__=(UniqueConstraint('job_id','requirement_id','item_id',name='uq_match_pair'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    session_id: Mapped[str] = mapped_column(ForeignKey('procurement_sessions.id'), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'))
    requirement_id: Mapped[str] = mapped_column(ForeignKey('items.id'))
    item_id: Mapped[str] = mapped_column(ForeignKey('items.id'))
    result: Mapped[dict] = mapped_column(J)
    decision: Mapped[str] = mapped_column(default='PENDING')
    expert_comment: Mapped[str|None] = mapped_column(Text)
class Audit(Base):
    __tablename__='audit_logs'
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('procurement_sessions.id'), index=True)
    user_id: Mapped[int|None] = mapped_column(ForeignKey('users.id'))
    event_type: Mapped[str]
    event_details: Mapped[dict] = mapped_column(J)
    created_at: Mapped[str]
    prev_hash: Mapped[str]
    sha256_hash: Mapped[str]

def get_db():
    with SessionLocal() as db: yield db

def public(obj):
    return {c.name: getattr(obj,c.name) for c in obj.__table__.columns if c.name != 'password_hash'}


class StageTiming(Base):
    __tablename__='stage_timings'
    id: Mapped[str] = mapped_column(String(36),primary_key=True,default=uid)
    session_id: Mapped[str] = mapped_column(ForeignKey('procurement_sessions.id'),index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'),index=True)
    attempt: Mapped[str] = mapped_column(String(36))
    stage: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=now)
    finished_at: Mapped[datetime|None] = mapped_column(DateTime(timezone=True))
    elapsed_seconds: Mapped[float|None] = mapped_column(Float)
    outcome: Mapped[str] = mapped_column(String(32),default='RUNNING')

class QualityReview(Base):
    __tablename__='quality_reviews'
    __table_args__=(UniqueConstraint('job_id','kind','target_id',name='uq_quality_target'),)
    id: Mapped[str] = mapped_column(String(36),primary_key=True,default=uid)
    session_id: Mapped[str] = mapped_column(ForeignKey('procurement_sessions.id'),index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'),index=True)
    kind: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[str] = mapped_column(String(36))
    prediction: Mapped[dict] = mapped_column(J)
    answer: Mapped[dict] = mapped_column(J)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=now)

class QualityCompletion(Base):
    __tablename__='quality_completions'
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'),primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('procurement_sessions.id'),index=True)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    finalized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=now)

class ExpertDataset(Base):
    """Immutable reference version, shared only within its organization."""
    __tablename__ = 'expert_datasets'
    __table_args__ = (UniqueConstraint('organization_id', 'checksum', name='uq_expert_dataset_checksum'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    organization_id: Mapped[int] = mapped_column(ForeignKey('organizations.id'), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    title: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(80))
    checksum: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(J)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class ExpertEvaluation(Base):
    """One subscription per reference/session; evaluation never changes pipeline data."""
    __tablename__ = 'expert_evaluations'
    __table_args__ = (UniqueConstraint('dataset_id', 'session_id', name='uq_expert_evaluation'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    dataset_id: Mapped[str] = mapped_column(ForeignKey('expert_datasets.id'), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('procurement_sessions.id'), index=True)
    requested_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    status: Mapped[str] = mapped_column(String(32), default='WAITING')
    result: Mapped[dict] = mapped_column(J, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class NormalizationExperiment(Base):
    """Append-only experiment snapshots, independent of mutable session passports."""
    __tablename__ = 'normalization_experiments'
    __table_args__ = (UniqueConstraint('organization_id', 'request_id', name='uq_regex_request'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    organization_id: Mapped[int] = mapped_column(ForeignKey('organizations.id'), index=True)
    requested_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    # Historical link, deliberately not a FK: deleting a session cannot erase its experiment.
    source_session_id: Mapped[str | None] = mapped_column(String(36), index=True)
    source_title: Mapped[str] = mapped_column(String(300))
    dataset_id: Mapped[str | None] = mapped_column(ForeignKey('expert_datasets.id'), index=True)
    request_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(24), default='QUEUED', index=True)
    method: Mapped[str] = mapped_column(String(32), default='regex')
    rules_version: Mapped[str] = mapped_column(String(80))
    rules_sha256: Mapped[str] = mapped_column(String(64))
    input_sha256: Mapped[str] = mapped_column(String(64))
    input_snapshot: Mapped[dict] = mapped_column(J)
    baseline: Mapped[dict] = mapped_column(J, default=dict)
    predictions: Mapped[list] = mapped_column(J, default=list)
    result: Mapped[dict] = mapped_column(J, default=dict)
    row_count: Mapped[int] = mapped_column(Integer)
    processed_rows: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))



class RunSnapshot(Base):
    __tablename__='run_snapshots'
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'),primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('procurement_sessions.id'),index=True)
    payload: Mapped[dict] = mapped_column(J)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=now)

class MetricRevision(Base):
    __tablename__='metric_revisions'
    id: Mapped[str] = mapped_column(String(36),primary_key=True,default=uid)
    session_id: Mapped[str] = mapped_column(ForeignKey('procurement_sessions.id'),index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'),index=True)
    source: Mapped[str] = mapped_column(String(16))
    dataset_id: Mapped[str|None] = mapped_column(ForeignKey('expert_datasets.id'))
    action: Mapped[str] = mapped_column(String(32))
    result: Mapped[dict] = mapped_column(J,default=dict)
    inputs: Mapped[dict] = mapped_column(J,default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=now)

class LabExperiment(Base):
    __tablename__='lab_experiments'
    id: Mapped[str] = mapped_column(String(36),primary_key=True,default=uid)
    session_id: Mapped[str] = mapped_column(ForeignKey('procurement_sessions.id'),index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'),index=True)
    requested_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    source: Mapped[str] = mapped_column(String(16))
    dataset_id: Mapped[str|None] = mapped_column(ForeignKey('expert_datasets.id'))
    status: Mapped[str] = mapped_column(String(24),default='QUEUED',index=True)
    payload: Mapped[dict] = mapped_column(J)
    result: Mapped[dict] = mapped_column(J,default=dict)
    summary: Mapped[dict] = mapped_column(J,default=dict)
    error: Mapped[str|None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=now)
    finished_at: Mapped[datetime|None] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime|None] = mapped_column(DateTime(timezone=True))

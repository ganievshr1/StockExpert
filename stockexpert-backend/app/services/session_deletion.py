"""Delete session-owned data while retaining stock used by other snapshots."""

from sqlalchemy import delete, select

from app.db import (
    Audit, Document, Item, Job, Match, ProcurementSession, QualityCompletion,
    QualityReview, StageTiming, StockPosition,
)
from app.services import search, storage
from app.db import ExpertEvaluation,MetricRevision,RunSnapshot,LabExperiment
from app.services.execution import ACTIVE_STATUSES


class SessionDeletionError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _locked_session(db, session_id, organization_id):
    return db.scalar(
        select(ProcurementSession)
        .where(
            ProcurementSession.id == session_id,
            ProcurementSession.organization_id == organization_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def delete_session(db, session_id: str, organization_id: int) -> None:
    session = _locked_session(db, session_id, organization_id)
    if session is None:
        raise SessionDeletionError(404, "Сессия не найдена")
    if db.scalar(select(LabExperiment.id).where(LabExperiment.session_id==session_id,LabExperiment.status.in_(['QUEUED','RUNNING'])).limit(1)):
        raise SessionDeletionError(409,'Дождитесь завершения лабораторного эксперимента')
    if db.scalar(select(Job.id).where(
        Job.session_id == session_id, Job.status.in_(ACTIVE_STATUSES)
    ).limit(1)):
        raise SessionDeletionError(
            409, "Сначала остановите обработку сессии и дождитесь её завершения."
        )

    # A durable marker also blocks uploads/jobs after a process interruption.
    # Retrying deletion (or file cleanup) is safe for both cleanup statuses.
    session.status = "CLEANING"
    db.commit()
    session = _locked_session(db, session_id, organization_id)
    if session is None:  # Another deletion completed while we reacquired the lock.
        return
    # File cleanup may have finished between the two locks and allowed a new job.
    if db.scalar(select(LabExperiment.id).where(LabExperiment.session_id==session_id,LabExperiment.status.in_(['QUEUED','RUNNING'])).limit(1)):
        raise SessionDeletionError(409,'Дождитесь завершения лабораторного эксперимента')
    if db.scalar(select(Job.id).where(
        Job.session_id == session_id, Job.status.in_(ACTIVE_STATUSES)
    ).limit(1)):
        raise SessionDeletionError(409, "Обработка сессии запущена повторно. Остановите её перед удалением.")

    try:
        # The savepoint retains the session lock on cleanup failure, so no new
        # mutation can enter between a rollback and marking the session blocked.
        with db.begin_nested():
            storage.delete_session_files(organization_id, session_id)
            search.delete_session_index(session_id)
            stock_ids = list(db.scalars(select(Item.stock_id).where(
                Item.session_id == session_id, Item.stock_id.is_not(None)
            ).distinct()))
            for model in (
                LabExperiment, MetricRevision, RunSnapshot, ExpertEvaluation, QualityCompletion, QualityReview, Match, StageTiming,
                Job, Item, Document, Audit,
            ):
                db.execute(delete(model).where(model.session_id == session_id))
            # Used stock is an inventory ledger, not session-owned data. Keep its
            # USED state and provenance; deleting a session must never free stock.
            db.execute(delete(StockPosition).where(
                StockPosition.organization_id == organization_id,
                StockPosition.base_session_id == session_id,
                StockPosition.state != "USED",
                ~select(Item.id).where(Item.stock_id == StockPosition.id).exists(),
            ))
            # A source may have been removed before its last copied snapshot.
            # Prune only this snapshot's now-unreferenced, unused positions.
            for offset in range(0, len(stock_ids), 500):
                db.execute(delete(StockPosition).where(
                    StockPosition.organization_id == organization_id,
                    StockPosition.id.in_(stock_ids[offset:offset + 500]),
                    StockPosition.state != "USED",
                    ~select(Item.id).where(Item.stock_id == StockPosition.id).exists(),
                ))
            db.delete(session)
            db.flush()
    except Exception as exc:
        session.status = "CLEANUP_FAILED"
        db.commit()
        raise SessionDeletionError(
            503, "Не удалось полностью очистить данные сессии. Повторите удаление."
        ) from exc
    db.commit()


def delete_all_sessions(db, organization_id: int) -> dict:
    """Use an organization-wide snapshot, independently of UI filters/pagination."""
    sessions = list(db.execute(
        select(ProcurementSession.id, ProcurementSession.title)
        .where(ProcurementSession.organization_id == organization_id)
        .order_by(ProcurementSession.id)
    ))
    if db.scalar(
        select(Job.id).join(ProcurementSession, Job.session_id == ProcurementSession.id)
        .where(ProcurementSession.organization_id == organization_id,
               Job.status.in_(ACTIVE_STATUSES)).limit(1)
    ):
        raise SessionDeletionError(
            409, "Есть сессии с активной обработкой. Остановите их и дождитесь завершения перед удалением всех сессий."
        )
    deleted, failed = [], []
    for session_id, title in sessions:
        try:
            delete_session(db, session_id, organization_id)
            deleted.append(session_id)
        except SessionDeletionError as exc:
            db.rollback()
            if exc.status_code == 404:
                deleted.append(session_id)
            else:
                failed.append({"session_id": session_id, "title": title,
                               "status_code": exc.status_code, "detail": exc.detail})
    return {"deleted_session_ids": deleted, "failed": failed}

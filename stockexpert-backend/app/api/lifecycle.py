from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel
from sqlalchemy import select,delete
from app.db import get_db,ProcurementSession,Document,Item,Match,Job,Audit,StageTiming,QualityReview,QualityCompletion,public
from app.api.deps import get_current_user,require_editor,own_session
from app.services.execution import ACTIVE_STATUSES
from app.services import storage,search
from app.services.audit import record
from app.services.session_deletion import (
    SessionDeletionError, delete_session as remove_session,
    delete_all_sessions as remove_all_sessions,
)
router=APIRouter()

class DeletedSession(BaseModel):
    deleted: bool
    session_id: str

class FailedSessionDeletion(BaseModel):
    session_id: str
    title: str
    status_code: int
    detail: str

class DeletedSessions(BaseModel):
    deleted_session_ids: list[str]
    failed: list[FailedSessionDeletion]

@router.delete('/procurement/sessions', response_model=DeletedSessions)
def delete_all_sessions(db=Depends(get_db),user=Depends(get_current_user)):
    require_editor(user)
    try:
        return remove_all_sessions(db,user.organization_id)
    except SessionDeletionError as exc:
        raise HTTPException(exc.status_code,exc.detail) from None

@router.delete('/procurement/sessions/{sid}', response_model=DeletedSession)
def delete_session(sid:str,db=Depends(get_db),user=Depends(get_current_user)):
    require_editor(user)
    try:
        remove_session(db,sid,user.organization_id)
    except SessionDeletionError as exc:
        raise HTTPException(exc.status_code,exc.detail) from None
    return {'deleted':True,'session_id':sid}

@router.post('/procurement/sessions/{sid}/cancel')
def cancel(sid:str,db=Depends(get_db),user=Depends(get_current_user)):
    require_editor(user);s=own_session(db,sid,user,lock=True)
    jobs=list(db.scalars(select(Job).where(Job.session_id==sid,Job.status.in_(ACTIVE_STATUSES))))
    for job in jobs:
        job.status='CANCELLED' if job.status=='QUEUED' else 'CANCEL_REQUESTED'
        if job.status=='CANCELLED':job.stage='CANCELLED'
    s.status='CANCEL_REQUESTED' if any(j.status=='CANCEL_REQUESTED' for j in jobs) else 'CANCELLED'
    record(db,sid,'SESSION_CANCEL_REQUESTED',{'job_ids':[j.id for j in jobs]},user.id);db.commit()
    return {'session':public(s),'pending':s.status=='CANCEL_REQUESTED','message':'Already sent model requests may finish before cancellation is acknowledged.'}

@router.delete('/procurement/sessions/{sid}/documents/{did}')
def delete_document(sid:str,did:str,db=Depends(get_db),user=Depends(get_current_user)):
    require_editor(user);s=own_session(db,sid,user,lock=True)
    if db.scalar(select(Job.id).where(Job.session_id==sid,Job.status.in_(ACTIVE_STATUSES))):raise HTTPException(409,'Cancel the running task and wait for CANCELLED first')
    reusable_requirement=bool(s.base_snapshot) and not db.scalar(select(Job.id).where(Job.session_id==sid).limit(1)) and db.scalar(select(Document.id).where(Document.id==did,Document.session_id==sid,Document.kind=='requirement'))
    if not reusable_requirement and (s.schema_doc or db.scalar(select(Job.id).where(Job.session_id==sid).limit(1))):
        raise HTTPException(409,'This file is used by the pipeline. Clear all session files to reset derived data safely.')
    d=db.get(Document,did)
    if not d or d.session_id!=sid:raise HTTPException(404,'Document not found')
    try:storage.delete_key(d.object_key)
    except Exception:raise HTTPException(503,'Storage cleanup failed; retry deletion') from None
    db.execute(delete(Item).where(Item.document_id==did,Item.session_id==sid))
    db.delete(d);db.flush()
    s.status='UPLOADED' if db.scalar(select(Document.id).where(Document.session_id==sid).limit(1)) else 'CREATED'
    record(db,sid,'DOCUMENT_DELETED',{'document_id':did},user.id);db.commit();return {'deleted':True,'document_id':did}

@router.delete('/procurement/sessions/{sid}/files')
def clear_files(sid:str,db=Depends(get_db),user=Depends(get_current_user)):
    require_editor(user)
    s=db.scalar(select(ProcurementSession).where(ProcurementSession.id==sid,ProcurementSession.organization_id==user.organization_id).with_for_update())
    if not s:raise HTTPException(404,'Session not found')
    if db.scalar(select(Job.id).where(Job.session_id==sid,Job.status.in_(ACTIVE_STATUSES))):raise HTTPException(409,'Cancellation is not finished. Wait for CANCELLED, then clear the files.')
    try:
        # Keep the row lock through external deletion. Concurrent uploads/exports/jobs cannot recreate data.
        s.status='CLEANING';db.flush()
        storage.delete_session_files(s.organization_id,sid)
        search.delete_session_index(sid)
    except Exception:
        db.rollback();s=db.get(ProcurementSession,sid);s.status='CLEANUP_FAILED';db.commit()
        raise HTTPException(503,'Cleanup incomplete. Session is blocked; retry clear-files to finish safely.') from None
    for model in (QualityCompletion,QualityReview,Match,StageTiming,Job,Item,Document,Audit):db.execute(delete(model).where(model.session_id==sid))
    s.base_snapshot={};s.schema_doc={};s.approved=False;s.taxonomy_version=None;s.progress=0;s.status='CREATED'
    record(db,sid,'SESSION_FILES_CLEARED',{'scope':'sources, reports, index, passports, results, jobs, timings, previous audit'},user.id)
    db.commit();return {'cleared':True,'session':public(s)}

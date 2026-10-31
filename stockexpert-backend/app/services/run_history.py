"""Freeze predictions once. Never reconstruct historical passports from current ones."""
from copy import deepcopy
from types import SimpleNamespace
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from app.db import RunSnapshot,MetricRevision,Job,Item,Document,QualityReview,QualityCompletion,Match,public

def capture(db,session,job,legacy=False):
    existing=db.get(RunSnapshot,job.id)
    if existing:return existing
    payload=jsonable_encoder({'legacy_capture':legacy,'session':public(session),'items':[public(i) for i in db.scalars(select(Item).where(Item.session_id==session.id))], 'documents':[public(d) for d in db.scalars(select(Document).where(Document.session_id==session.id))], 'job_ids':[j.id for j in db.scalars(select(Job).where(Job.session_id==session.id,Job.created_at<=job.created_at))]})
    payload['matches']=jsonable_encoder([{'id':m.id,'requirement_id':m.requirement_id,'item_id':m.item_id,'result':m.result} for m in db.scalars(select(Match).where(Match.job_id==job.id,Match.session_id==session.id))])
    row=RunSnapshot(job_id=job.id,session_id=session.id,payload=payload);db.add(row);db.flush();return row

def capture_latest(db,session):
    from app.services.quality import latest_job
    job=latest_job(db,session.id)
    if job:return capture(db,session,job,legacy=True)

def get_snapshot(db,session,job):
    snap=db.get(RunSnapshot,job.id)
    if snap:return snap.payload
    from app.services.quality import latest_job
    latest=latest_job(db,session.id)
    from app.services.execution import ACTIVE_STATUSES
    active=db.scalar(select(Job.id).where(Job.session_id==session.id,Job.status.in_(ACTIVE_STATUSES)).limit(1))
    if latest and latest.id==job.id and not active:return capture(db,session,job,legacy=True).payload
    return None

def manual_revision(db,sid,job_id,action):
    from app.services.quality import evaluate
    reviews=[{'kind':r.kind,'target_id':r.target_id,'prediction':r.prediction,'answer':r.answer,'reviewer_id':r.reviewer_id} for r in db.scalars(select(QualityReview).where(QualityReview.job_id==job_id))]
    snap=db.get(RunSnapshot,job_id)
    sources=[{k:i.get(k) for k in ('id','document_id','sheet','row_index','kind','raw','source_text')} for i in snap.payload['items']] if snap else []
    row=MetricRevision(session_id=sid,job_id=job_id,source='manual',action=action,result=jsonable_encoder(evaluate(db,sid,job_id)),inputs={'reviews':reviews,'source_rows':sources,'finalized':bool(db.get(QualityCompletion,job_id))})
    db.add(row);db.flush();return row

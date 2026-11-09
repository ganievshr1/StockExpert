"""Independent manual/reference dashboards and immutable evaluation revisions."""
from copy import deepcopy
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import select,delete
from sqlalchemy.orm import defer
from app.db import get_db,Job,RunSnapshot,MetricRevision,QualityReview,QualityCompletion,ExpertEvaluation,ExpertDataset,LabExperiment,public,now
from app.api.deps import get_current_user,own_session,require_expert
from app.services.quality import evaluate
from app.services.run_history import get_snapshot,manual_revision
from app.services.expert_evaluation import refresh_evaluation
from app.services.execution import ACTIVE_STATUSES
router=APIRouter()
def compact_revision(row):
    value={c.name:getattr(row,c.name) for c in row.__table__.columns if c.name!='inputs'}
    value['result']={k:v for k,v in row.result.items() if k not in ('rows','pairs','source_jobs','label_mapping','fields')}
    return value

def compact_experiment(row):
    value={c.name:getattr(row,c.name) for c in row.__table__.columns if c.name not in ('payload','result','summary')}
    value['result']=row.summary or {}
    return value
class Action(BaseModel):
    model_config=ConfigDict(extra='forbid')
    job_id:str
    source:Literal['manual','automatic']
    dataset_id:str|None=None

def selected(db,s,jid):
    job=db.get(Job,jid)
    if not job or job.session_id!=s.id or job.status!='COMPLETED' or job.kind not in ('normalize','taxonomy','match','full'):raise HTTPException(404,'Завершённый запуск не найден')
    return job

def idle(db,sid):
    if db.scalar(select(Job.id).where(Job.session_id==sid,Job.status.in_(ACTIVE_STATUSES)).limit(1)):raise HTTPException(409,'Дождитесь завершения обработки')

def reference(db,user,ident):
    row=db.get(ExpertDataset,ident)
    if not row or row.organization_id!=user.organization_id:raise HTTPException(404,'Эталон не найден')
    return row

@router.get('/laboratory/{sid}')
def dashboard(sid:str,job_id:str|None=None,db=Depends(get_db),user=Depends(get_current_user)):
    s=own_session(db,sid,user)
    jobs=list(db.scalars(select(Job).where(Job.session_id==sid,Job.status=='COMPLETED',Job.kind.in_(['normalize','taxonomy','match','full'])).order_by(Job.created_at.desc(),Job.id.desc())))
    job=selected(db,s,job_id) if job_id else (jobs[0] if jobs else None)
    snap=get_snapshot(db,s,job) if job else None
    if snap:db.commit()
    revisions=list(db.scalars(select(MetricRevision).options(defer(MetricRevision.inputs)).where(MetricRevision.session_id==sid).order_by(MetricRevision.created_at.desc())))
    automatic={}
    for row in revisions:
        if row.source=='automatic' and job and row.job_id==job.id and row.dataset_id not in automatic:automatic[row.dataset_id]=compact_revision(row)
    refs=list(db.scalars(select(ExpertDataset).where(ExpertDataset.organization_id==user.organization_id)))
    labs=list(db.scalars(select(LabExperiment).options(defer(LabExperiment.payload),defer(LabExperiment.result)).where(LabExperiment.session_id==sid).order_by(LabExperiment.created_at.desc())))
    return {'job_id':job.id if job else None,'runs':[{'id':j.id,'kind':j.kind,'created_at':j.created_at,'snapshot_available':bool(db.get(RunSnapshot,j.id)),'taxonomy_enabled':j.params.get('taxonomy_enabled',True)} for j in jobs], 'manual':evaluate(db,sid,job.id) if job else evaluate(db,sid), 'automatic':list(automatic.values()),'references':[{'id':r.id,'title':r.title,'version':r.version} for r in refs], 'history':[compact_revision(r) for r in revisions], 'experiments':[compact_experiment(r) for r in labs], 'taxonomy_enabled':snap['session'].get('taxonomy_enabled',True) if snap else s.taxonomy_enabled,'legacy_snapshot':bool(snap and snap.get('legacy_capture'))}

@router.post('/laboratory/{sid}/recalculate')
def recalculate(sid:str,payload:Action,db=Depends(get_db),user=Depends(get_current_user)):
    require_expert(user);s=own_session(db,sid,user,lock=True);idle(db,sid);job=selected(db,s,payload.job_id)
    if payload.source=='manual':
        if not db.scalar(select(QualityReview.id).where(QualityReview.job_id==job.id).limit(1)):raise HTTPException(422,'Сначала проверьте хотя бы одну позицию')
        done=db.get(QualityCompletion,job.id)
        if not done:db.add(QualityCompletion(job_id=job.id,session_id=sid,reviewer_id=user.id))
        else:done.finalized_at=now();done.reviewer_id=user.id
        db.flush();row=manual_revision(db,sid,job.id,'recalculate');db.commit();return public(row)
    ref=reference(db,user,payload.dataset_id)
    if not get_snapshot(db,s,job):raise HTTPException(409,'Для этого старого запуска не сохранён снимок паспортов')
    ev=db.scalar(select(ExpertEvaluation).where(ExpertEvaluation.session_id==sid,ExpertEvaluation.dataset_id==ref.id))
    if not ev:ev=ExpertEvaluation(session_id=sid,dataset_id=ref.id,requested_by=user.id);db.add(ev);db.flush()
    try:refresh_evaluation(db,ev,job.id)
    except ValueError as exc:raise HTTPException(422,str(exc))
    db.commit();return public(ev)

@router.post('/laboratory/{sid}/reset')
def reset(sid:str,payload:Action,db=Depends(get_db),user=Depends(get_current_user)):
    require_expert(user);s=own_session(db,sid,user,lock=True);idle(db,sid);job=selected(db,s,payload.job_id)
    if payload.source=='manual':
        manual_revision(db,sid,job.id,'before_reset')
        db.execute(delete(QualityCompletion).where(QualityCompletion.job_id==job.id))
        db.execute(delete(QualityReview).where(QualityReview.job_id==job.id))
        manual_revision(db,sid,job.id,'reset')
    else:
        ref=reference(db,user,payload.dataset_id)
        db.add(MetricRevision(session_id=sid,job_id=job.id,source='automatic',dataset_id=ref.id,action='reset',result={},inputs={}))
        ev=db.scalar(select(ExpertEvaluation).where(ExpertEvaluation.session_id==sid,ExpertEvaluation.dataset_id==ref.id))
        if ev and ev.result.get('job_id')==job.id:ev.result={};ev.status='RESET';ev.updated_at=now()
    db.commit();return {'reset':True,'source':payload.source,'job_id':job.id}

@router.post('/laboratory/{sid}/history/{rid}/recalculate')
def restore(sid:str,rid:str,db=Depends(get_db),user=Depends(get_current_user)):
    require_expert(user);s=own_session(db,sid,user,lock=True);idle(db,sid)
    old=db.get(MetricRevision,rid)
    if not old or old.session_id!=sid:raise HTTPException(404,'Версия оценки не найдена')
    if old.source=='automatic':return recalculate(sid,Action(job_id=old.job_id,source='automatic',dataset_id=old.dataset_id),db,user)
    if not old.inputs.get('reviews'):raise HTTPException(422,'В этой версии нет ручных ответов. Выберите версию до сброса.')
    manual_revision(db,sid,old.job_id,'before_restore')
    db.execute(delete(QualityReview).where(QualityReview.job_id==old.job_id));db.execute(delete(QualityCompletion).where(QualityCompletion.job_id==old.job_id))
    for r in old.inputs['reviews']:db.add(QualityReview(session_id=sid,job_id=old.job_id,**deepcopy(r)))
    db.flush()
    return recalculate(sid,Action(job_id=old.job_id,source='manual'),db,user)

class ExperimentInput(Action):
    methods:list[str]=Field(min_length=1,max_length=20)
    h1_mode:Literal['attributes','name_exact']='attributes'
    seed:int=Field(default=42,ge=0,le=1000000)
    train_fraction:float=Field(default=.7,ge=.2,le=.9)
    clusters:int=Field(default=8,ge=2,le=1000)
    min_cluster_size:int=Field(default=5,ge=2,le=1000)
    repeats:int=Field(default=1,ge=1,le=10)
    profile_id:str|None=None
    h2_name_weight:float=Field(default=1.0,gt=0,le=10)
    h2_attribute_weight:float=Field(default=2.0,gt=0,le=10)
    h2_reference_confirmed:bool=False

@router.post('/laboratory/{sid}/h2-check')
def h2_check(sid:str,payload:ExperimentInput,db=Depends(get_db),user=Depends(get_current_user)):
    require_expert(user);s=own_session(db,sid,user);job=selected(db,s,payload.job_id)
    from app.services.lab import make_input,stock_rows,split_rows
    from app.services.lab_h2 import inspect_dataset
    snap=get_snapshot(db,s,job)
    if not snap:raise HTTPException(409,'Нет снимка выбранного запуска')
    ref=reference(db,user,payload.dataset_id) if payload.source=='automatic' else None
    try:
        data=make_input(db,snap,job,ref,payload.model_dump())
        result=inspect_dataset(data,stock_rows(data),split_rows)
    except ValueError as exc:raise HTTPException(422,str(exc))
    db.commit()
    return result

@router.post('/laboratory/{sid}/experiments',status_code=202)
def start_experiment(sid:str,payload:ExperimentInput,db=Depends(get_db),user=Depends(get_current_user)):
    require_expert(user);s=own_session(db,sid,user,lock=True);idle(db,sid);job=selected(db,s,payload.job_id)
    from app.services.lab import METHODS,make_input,stock_rows,split_rows
    if len(set(payload.methods))!=len(payload.methods) or not set(payload.methods)<=METHODS.keys():raise HTTPException(422,'Неизвестный или повторяющийся метод')
    if db.scalar(select(LabExperiment.id).where(LabExperiment.session_id==sid,LabExperiment.status.in_(['QUEUED','RUNNING'])).limit(1)):raise HTTPException(409,'Эксперимент уже выполняется')
    snap=get_snapshot(db,s,job)
    if not snap:raise HTTPException(409,'Нет снимка выбранного запуска')
    ref=reference(db,user,payload.dataset_id) if payload.source=='automatic' else None
    from app.services.model_registry import session_config,get_profile,default_config
    try:
        needs_model=bool(set(payload.methods)&{'h1_llm','h1_llm_raw','h1_hybrid','h2_llm','h2_hybrid'})
        config=get_profile(payload.profile_id) if payload.profile_id else session_config(db,s) if needs_model else default_config()
        data=make_input(db,snap,job,ref,payload.model_dump());data['llm_config']=config.model_dump()
        if any(m.startswith('h2_') for m in payload.methods):
            from app.services.lab_h2 import inspect_dataset,PROTOCOL
            inspect_dataset(data,stock_rows(data),split_rows)
            data['options']['h2_protocol']=PROTOCOL
    except ValueError as exc:raise HTTPException(422,str(exc))
    exp=LabExperiment(session_id=sid,job_id=job.id,requested_by=user.id,source=payload.source,dataset_id=ref.id if ref else None,payload=data)
    db.add(exp);db.commit();return {'id':exp.id,'status':exp.status}

@router.get('/laboratory/{sid}/methods')
def methods(sid:str,db=Depends(get_db),user=Depends(get_current_user)):
    own_session(db,sid,user)
    from app.services.lab import METHODS
    return METHODS

@router.get('/laboratory/{sid}/export')
def export(sid:str,job_id:str|None=None,db=Depends(get_db),user=Depends(get_current_user)):
    from app.services.lab_export import workbook
    from fastapi.responses import Response
    data=dashboard(sid,job_id,db,user)
    return Response(workbook(data),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="StockExpert_H1_H2_H3.xlsx"'})

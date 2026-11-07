import logging
from datetime import timedelta
from celery import Celery
from sqlalchemy import select,or_
from redis import Redis
from app.core.config import settings
from app.db import SessionLocal, Job, ProcurementSession, NormalizationExperiment, now
from app.services.audit import record
from app.services.llm import LLMTemporaryError
from app.services import llm,model_registry,taxonomy,normalization_engine
from app.services.execution import Execution,Cancelled,ACTIVE_STATUSES
from app.services.workflow import prepare,normalize_all,match_all
from app.services import job_telemetry
from app.services.job_routing import (queue_for_job, prepared_base_id, TASK_ROUTES,
                                     SHARED_QUEUE, CONTROL_QUEUE, LABORATORY_QUEUE, REGEX_QUEUE, B1_QUEUE)

celery_app=Celery('stockexpert',broker=settings.REDIS_URL,backend=settings.REDIS_URL)
celery_app.conf.update(task_serializer='json',accept_content=['json'],result_serializer='json',task_acks_late=True,task_reject_on_worker_lost=True,worker_prefetch_multiplier=1,broker_connection_retry_on_startup=True,broker_transport_options={'visibility_timeout':86400},result_backend_transport_options={'visibility_timeout':86400},visibility_timeout=86400,task_default_queue=SHARED_QUEUE,task_routes=TASK_ROUTES,beat_schedule={'dispatch-outbox':{'task':'stockexpert.dispatch','schedule':settings.DISPATCH_INTERVAL_SECONDS,'options':{'queue':CONTROL_QUEUE}}})

@celery_app.task(name='stockexpert.dispatch')
def dispatch():
    # DB is the durable outbox. Expired publication/worker leases are republished.
    cutoff=now()-timedelta(minutes=10)
    with SessionLocal() as db:
        jobs=db.scalars(select(Job).where(Job.status.in_(ACTIVE_STATUSES),or_(Job.dispatched_at.is_(None),Job.dispatched_at<cutoff)).order_by(Job.created_at,Job.id).with_for_update(skip_locked=True)).all()
        for job in jobs:
            if job.heartbeat_at and job.heartbeat_at.replace(tzinfo=cutoff.tzinfo)>cutoff: continue
            session=db.get(ProcurementSession,job.session_id)
            if session is None:continue
            queue=queue_for_job(job.kind,prepared_base_id(session))
            run_job.apply_async(args=[job.id],queue=queue)
            job.dispatched_at=now()
            job_telemetry.published(job,queue,settings.JOB_ROUTING_MODE,job.dispatched_at)
        db.commit()
    dispatch_regex_experiments()
    dispatch_laboratory()


def dispatch_regex_experiments():
    from app.services.regex_experiments import expire_abandoned
    cutoff = now() - timedelta(minutes=1)
    with SessionLocal() as db:
        expire_abandoned(db)
        experiments = db.scalars(select(NormalizationExperiment).where(
            NormalizationExperiment.status == 'QUEUED',
            or_(NormalizationExperiment.dispatched_at.is_(None), NormalizationExperiment.dispatched_at < cutoff)
        ).with_for_update(skip_locked=True)).all()
        for experiment in experiments:
            run_regex_experiment.apply_async(args=[experiment.id], queue=REGEX_QUEUE)
            experiment.dispatched_at = now()
        db.commit()


@celery_app.task(name='stockexpert.regex_experiment')
def run_regex_experiment(ident):
    from app.services.regex_experiments import run_experiment
    run_experiment(ident)

@celery_app.task(name='stockexpert.run',bind=True,max_retries=2)
def run_job(self,jid):
    redis=Redis.from_url(settings.REDIS_URL)
    with SessionLocal() as db:
        job=db.get(Job,jid)
        if not job or job.status not in ACTIVE_STATUSES: return
        lock=redis.lock('session:'+job.session_id,timeout=900,blocking_timeout=1)
        if not lock.acquire(blocking=True): return
        execution=None
        try:
            s=db.scalar(select(ProcurementSession).where(ProcurementSession.id==job.session_id).with_for_update())
            if s is None:return
            # Publication is committed by the dispatcher after send succeeds.
            # Lock/refresh the job here to avoid overwriting its first-publication
            # metrics with a stale copy loaded before that transaction committed.
            db.refresh(job,with_for_update=True)
            if job.status not in ACTIVE_STATUSES:return
            if job.status=='CANCEL_REQUESTED':raise Cancelled()
            job.status='RUNNING'
            job_telemetry.started(job,queue=(self.request.delivery_info or {}).get('routing_key'))
            db.commit()
            config=model_registry.pin_job_config(db,s,job)
            h1=normalization_engine.pin_job(job)
            normalization_engine.check_rules(job)
            job.status='RUNNING'
            job.params={**job.params,'runtime':{**config.model_dump(),'llm_fingerprint':config.fingerprint(),'retrieval':'Dense + BM25 + RRF','reranker':settings.RERANKER_MODEL if settings.RERANKER_ENABLED else None}}
            job.params={**job.params,'runtime':{**job.params['runtime'],'h1':h1,'h2':config.model_dump() if s.taxonomy_enabled else {'enabled':False}}}
            if h1['method']=='regex' and job.kind in ('prepare','normalize'):
                job.params={**job.params,'runtime':{**job.params['runtime'],'provider':'regex','model':h1['rules_version'],'llm_calls':0}}
            if job.kind in ('full','normalize'):job.params={**job.params,'normalization_counts':job.params.get('normalization_counts',{})}
            db.commit()
            execution=Execution(db,job,lock);tick=execution
            tick('PARSING' if job.kind=='prepare' else 'REQUIREMENT_PARSING' if job.kind in ('full','normalize') else 'VECTORIZATION')
            with llm.using_config(config):
                if job.kind=='taxonomy' and not s.taxonomy_enabled:
                    raise ValueError('H2 отключён в этой сессии. Построение таксономии запрещено.')
                if normalization_engine.needs_llm(job.kind,h1['method'],s.taxonomy_enabled):model_registry.preflight(config)
                if job.kind=='prepare': prepare(db,s,job,tick)
                elif job.kind=='normalize': normalize_all(db,s,job,tick)
                elif job.kind=='match': match_all(db,s,job,tick)
                elif job.kind=='full':
                    normalize_all(db,s,job,tick)
                    if s.approved:match_all(db,s,job,tick)
                    else:job.kind='normalize'  # Completed H1/H2 is not a completed matching run.
                elif job.kind=='taxonomy':taxonomy.build(db,s,job,tick)
                else: raise ValueError('Unknown job type')
            db.scalar(select(ProcurementSession.id).where(ProcurementSession.id==s.id).with_for_update())
            execution.check();execution.finish('COMPLETED')
            # Serialize final status with cancel requests.
            db.scalar(select(ProcurementSession.id).where(ProcurementSession.id==s.id).with_for_update())
            execution.check()
            job.status='COMPLETED';job.stage='COMPLETED';job.error=None
            job_telemetry.finished(job,'COMPLETED')
            from app.services.run_history import capture
            capture(db,s,job)
            db.commit()
            try:
                from app.services.expert_evaluation import evaluate_subscriptions
                evaluate_subscriptions(db,s.id)
            except Exception:
                db.rollback()
                logging.exception('Expert metric calculation failed; pipeline results are preserved')
        except Cancelled:
            db.rollback()
            if execution:execution.finish('CANCELLED')
            job=db.get(Job,jid);job.status='CANCELLED';job.stage='CANCELLED';job.error=None
            job_telemetry.finished(job,'CANCELLED')
            s=db.get(ProcurementSession,job.session_id);s.status='CANCELLED'
            record(db,s.id,'SESSION_CANCELLED',{'job_id':jid});db.commit()
        except Exception as exc:
            db.rollback()
            if execution:execution.finish('FAILED')
            job=db.get(Job,jid)
            transient=isinstance(exc,(ConnectionError,TimeoutError,LLMTemporaryError)) or type(exc).__module__.startswith(('httpx','httpcore','minio','urllib3'))
            cancelled=db.scalar(select(Job.status).where(Job.id==jid))=='CANCEL_REQUESTED'
            retry=not cancelled and transient and self.request.retries<self.max_retries
            job.status='CANCELLED' if cancelled else 'QUEUED' if retry else 'FAILED';job.error=f'{type(exc).__name__}: {str(exc)[:800]}'
            s=db.get(ProcurementSession,job.session_id)
            if cancelled:job.stage='CANCELLED';job.error=None
            if not retry: s.status='CANCELLED' if cancelled else 'FAILED'
            job_telemetry.finished(job,job.status,terminal=not retry)
            record(db,s.id,'SESSION_CANCELLED' if cancelled else 'JOB_RETRY' if retry else 'JOB_FAILED',{'job_id':jid,'error':job.error});db.commit()
            logging.exception('Pipeline job failed: %s',jid)
            if retry: raise self.retry(exc=exc,countdown=15*(self.request.retries+1))
        finally:
            if lock.owned(): lock.release()

@celery_app.task(name='stockexpert.laboratory')
def run_laboratory(ident):
    from app.db import LabExperiment
    from app.services.lab import run,summarize
    # One benchmark at a time per server so its own methods do not compete.
    redis=Redis.from_url(settings.REDIS_URL)
    lock=redis.lock('laboratory:benchmark',timeout=90,blocking_timeout=1,thread_local=False)
    if not lock.acquire(blocking=True):return
    from threading import Event,Thread
    stop=Event();lost=Event()
    def heartbeat():
        while not stop.wait(20):
            try:
                lock.reacquire()
                with SessionLocal() as heartbeat_db:
                    active=heartbeat_db.get(LabExperiment,ident)
                    if active:active.dispatched_at=now();heartbeat_db.commit()
            except Exception:lost.set();return
    thread=Thread(target=heartbeat,daemon=True);thread.start()
    try:
        with SessionLocal() as db:
            exp=db.get(LabExperiment,ident)
            if not exp or exp.status not in ('QUEUED','RUNNING'):return
            exp.status='RUNNING';db.commit()
            def progress(value):
                if lost.is_set():raise RuntimeError('Потеряна блокировка эксперимента. Сохранённые методы доступны в истории.')
                exp.result=value;exp.summary=summarize(value);db.commit()
            try:
                exp.result=run(exp.payload,progress,exp.result)
                exp.status='COMPLETED' if all(r['status']=='COMPLETED' for r in exp.result['methods'].values()) else 'PARTIAL'
            except Exception as exc:
                exp.status='FAILED';exp.error=f'{type(exc).__name__}: {str(exc)[:600]}'
            exp.summary=summarize(exp.result)
            exp.finished_at=now();db.commit()
    finally:
        stop.set();thread.join(timeout=2)
        if lock.owned():lock.release()

def dispatch_laboratory():
    from app.db import LabExperiment
    with SessionLocal() as db:
        cutoff=now()-timedelta(minutes=2)
        for exp in db.scalars(select(LabExperiment).where(LabExperiment.status.in_(['QUEUED','RUNNING']),or_(LabExperiment.dispatched_at.is_(None),LabExperiment.dispatched_at<cutoff)).with_for_update(skip_locked=True)):
            run_laboratory.apply_async(args=[exp.id],queue=LABORATORY_QUEUE);exp.dispatched_at=now()
        db.commit()


@celery_app.task(name='stockexpert.b1_experiment',queue=B1_QUEUE)
def run_b1_experiment(ident):
    from app.services.b1_experiments import run_experiment
    run_experiment(ident)

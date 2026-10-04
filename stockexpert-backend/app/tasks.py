import logging
from datetime import timedelta
from celery import Celery
from sqlalchemy import select,or_
from redis import Redis
from app.core.config import settings
from app.db import SessionLocal, Job, ProcurementSession, now
from app.services.audit import record
from app.services.llm import LLMTemporaryError
from app.services import llm,model_registry,taxonomy
from app.services.execution import Execution,Cancelled,ACTIVE_STATUSES
from app.services.workflow import prepare,normalize_all,match_all

celery_app=Celery('stockexpert',broker=settings.REDIS_URL,backend=settings.REDIS_URL)
celery_app.conf.update(task_serializer='json',accept_content=['json'],result_serializer='json',task_acks_late=True,task_reject_on_worker_lost=True,worker_prefetch_multiplier=1,broker_connection_retry_on_startup=True,broker_transport_options={'visibility_timeout':86400},result_backend_transport_options={'visibility_timeout':86400},visibility_timeout=86400,task_default_queue='normalize_mtr',beat_schedule={'dispatch-outbox':{'task':'stockexpert.dispatch','schedule':10.0}})

@celery_app.task(name='stockexpert.dispatch')
def dispatch():
    # DB is the durable outbox. Expired publication/worker leases are republished.
    cutoff=now()-timedelta(minutes=10)
    with SessionLocal() as db:
        jobs=db.scalars(select(Job).where(Job.status.in_(ACTIVE_STATUSES),or_(Job.dispatched_at.is_(None),Job.dispatched_at<cutoff)).with_for_update(skip_locked=True)).all()
        for job in jobs:
            if job.heartbeat_at and job.heartbeat_at.replace(tzinfo=cutoff.tzinfo)>cutoff: continue
            run_job.apply_async(args=[job.id],queue='vector_match' if job.kind=='match' else 'normalize_mtr')
            job.dispatched_at=now()
        db.commit()

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
            db.refresh(job)
            if job.status not in ACTIVE_STATUSES:return
            if job.status=='CANCEL_REQUESTED':raise Cancelled()
            config=model_registry.pin_job_config(db,s,job)
            job.status='RUNNING'
            job.params={**job.params,'runtime':{**config.model_dump(),'llm_fingerprint':config.fingerprint(),'retrieval':'Dense + BM25 + RRF','reranker':settings.RERANKER_MODEL if settings.RERANKER_ENABLED else None}}
            if job.kind in ('full','normalize'):job.params={**job.params,'normalization_counts':job.params.get('normalization_counts',{})}
            db.commit()
            execution=Execution(db,job,lock);tick=execution
            tick('PARSING' if job.kind=='prepare' else 'REQUIREMENT_PARSING' if job.kind in ('full','normalize') else 'VECTORIZATION')
            with llm.using_config(config):
                if job.kind!='match':model_registry.preflight(config)
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
            job.status='COMPLETED';job.stage='COMPLETED';job.error=None;db.commit()
        except Cancelled:
            db.rollback()
            if execution:execution.finish('CANCELLED')
            job=db.get(Job,jid);job.status='CANCELLED';job.stage='CANCELLED';job.error=None
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
            record(db,s.id,'SESSION_CANCELLED' if cancelled else 'JOB_RETRY' if retry else 'JOB_FAILED',{'job_id':jid,'error':job.error});db.commit()
            logging.exception('Pipeline job failed: %s',jid)
            if retry: raise self.retry(exc=exc,countdown=15*(self.request.retries+1))
        finally:
            if lock.owned(): lock.release()

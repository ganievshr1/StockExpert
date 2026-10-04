"""Cooperative cancellation and persistent stage timings. No model calls here."""
from datetime import timezone
from uuid import uuid4
from sqlalchemy import select
from app.db import Job,StageTiming,now
from app.core.config import settings

ACTIVE_STATUSES=('QUEUED','RUNNING','CANCEL_REQUESTED')
class Cancelled(Exception): pass

def seconds(start,end):
    return max(0,(end.replace(tzinfo=timezone.utc)-start.replace(tzinfo=timezone.utc)).total_seconds())

class Execution:
    def __init__(self,db,job,lock):
        self.db,self.job,self.lock=db,job,lock
        # Discovery/requirement splitting can block on a slow local generator.
        # Its complete retry budget must fit inside the session lease.
        timeout=job.params.get('llm_config',{}).get('timeout',settings.LLM_TIMEOUT)
        self.lease_seconds=max(900,(timeout+30)*(settings.LLM_RETRIES+1)+120)
        self.attempt=str(uuid4());self.current=None
        # A crashed attempt remains explicitly interrupted rather than successful.
        for row in db.scalars(select(StageTiming).where(StageTiming.job_id==job.id,StageTiming.finished_at.is_(None))):
            row.finished_at=job.heartbeat_at or now();row.elapsed_seconds=seconds(row.started_at,row.finished_at);row.outcome='INTERRUPTED'
        db.commit()
    def check(self):
        status=self.db.scalar(select(Job.status).where(Job.id==self.job.id))
        if status in ('CANCEL_REQUESTED','CANCELLED'):
            raise Cancelled('Session cancellation requested')
    def __call__(self,stage):
        self.check();self.transition(stage);self.heartbeat()
    def heartbeat(self):
        self.lock.extend(self.lease_seconds,replace_ttl=True)
        self.job.heartbeat_at=now();self.db.commit()
    def drain(self,stage):
        # Keep lease while already-started model requests settle. Do not swallow cancellation elsewhere.
        status=self.db.scalar(select(Job.status).where(Job.id==self.job.id))
        if status=='CANCEL_REQUESTED': self.transition('CANCELLING')
        self.heartbeat()
    def transition(self,stage):
        if self.current and self.current.stage==stage:return
        self.finish('COMPLETED')
        self.current=StageTiming(session_id=self.job.session_id,job_id=self.job.id,attempt=self.attempt,stage=stage)
        self.db.add(self.current);self.job.stage=stage;self.db.commit()
    def finish(self,outcome):
        if self.current:
            self.current.finished_at=now();self.current.elapsed_seconds=seconds(self.current.started_at,self.current.finished_at);self.current.outcome=outcome
            self.db.commit();self.current=None


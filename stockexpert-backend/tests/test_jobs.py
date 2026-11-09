from unittest.mock import Mock
from sqlalchemy import select
from app.db import SessionLocal,Job,ProcurementSession
from app import tasks

def test_outbox_publication_and_failure_record(client,auth,monkeypatch):
    sid=client.post('/api/v1/procurement/sessions',headers=auth,json={'title':'Outbox'}).json()['id']
    with SessionLocal() as db:
        job=Job(session_id=sid,kind='prepare');db.add(job);db.commit();jid=job.id
    publish=Mock();monkeypatch.setattr(tasks.run_job,'apply_async',publish)
    tasks.dispatch.run()
    assert publish.call_args.kwargs['args']==[jid]
    assert publish.call_args.kwargs['queue']=='shared'
    tasks.dispatch.run();assert publish.call_count==1
    lock=Mock();lock.acquire.return_value=True;lock.owned.return_value=True
    redis=Mock();redis.lock.return_value=lock
    monkeypatch.setattr(tasks.Redis,'from_url',lambda _:redis)
    def failed(*args): raise ValueError('unsupported document')
    monkeypatch.setattr(tasks,'prepare',failed)
    tasks.run_job.run(jid)
    with SessionLocal() as db:
        job=db.get(Job,jid);assert job.status=='FAILED' and 'unsupported document' in job.error
        metrics=job.params['execution_metrics']
        assert metrics['first_published_at'] and metrics['first_started_at'] and metrics['finished_at']
        assert metrics['publication_count']==1 and metrics['attempt_count']==1
        assert metrics['outcome']=='FAILED' and 'completed_at' not in metrics
        assert db.get(ProcurementSession,sid).status=='FAILED'
    retry=client.post(f'/api/v1/pipeline/{sid}/jobs/{jid}/retry',headers=auth)
    assert retry.status_code==202
    with SessionLocal() as db: assert db.get(Job,jid).dispatched_at is None


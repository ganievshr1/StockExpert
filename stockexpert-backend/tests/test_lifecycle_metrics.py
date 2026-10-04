from io import BytesIO
from datetime import timedelta
import pytest
from sqlalchemy import select
from openpyxl import load_workbook
from app.db import SessionLocal,ProcurementSession,Job,StageTiming,Document,now
from app.services import storage,search
from app.services.execution import Execution,Cancelled


def create(client,auth):
    return client.post('/api/v1/procurement/sessions',headers=auth,json={'title':'Lifecycle'}).json()['id']

def test_cancel_then_clear_scoped(client,auth,monkeypatch):
    sid=create(client,auth);other=create(client,auth);calls=[]
    with SessionLocal() as db:
        j=Job(session_id=sid,kind='normalize',status='RUNNING',params={});db.add(j);db.commit();jid=j.id
    url=f'/api/v1/procurement/sessions/{sid}'
    assert client.post(url+'/cancel',headers=auth).json()['pending'] is True
    assert client.delete(url+'/files',headers=auth).status_code==409
    with SessionLocal() as db:
        db.get(Job,jid).status='CANCELLED';db.commit()
    monkeypatch.setattr(storage,'delete_session_files',lambda org,s:calls.append(s))
    monkeypatch.setattr(search,'delete_session_index',lambda s:calls.append(s))
    assert client.delete(url+'/files',headers=auth).json()['session']['status']=='CREATED'
    assert calls==[sid,sid]
    with SessionLocal() as db:
        assert db.get(Job,jid) is None
        assert db.get(ProcurementSession,other) is not None
    assert client.delete(url+'/files',headers=auth).status_code==200

def test_failed_cleanup_retry_blocks_new_work(client,auth,monkeypatch):
    sid=create(client,auth);url=f'/api/v1/procurement/sessions/{sid}'
    def fail(*a):raise RuntimeError('storage down')
    monkeypatch.setattr(storage,'delete_session_files',fail)
    assert client.delete(url+'/files',headers=auth).status_code==503
    assert client.post(url+'/upload-requirement',headers=auth,files={'file':('need.txt',b'abc')}).status_code==409
    monkeypatch.setattr(storage,'delete_session_files',lambda *a:None)
    monkeypatch.setattr(search,'delete_session_index',lambda *a:None)
    assert client.delete(url+'/files',headers=auth).status_code==200

def test_queued_cancellation_and_no_gold_metrics(client,auth):
    sid=create(client,auth)
    with SessionLocal() as db:
        j=Job(session_id=sid,kind='prepare',status='QUEUED',params={});db.add(j);db.commit();jid=j.id
    assert client.post(f'/api/v1/procurement/sessions/{sid}/cancel',headers=auth).json()['pending'] is False
    with SessionLocal() as db:assert db.get(Job,jid).status=='CANCELLED'
    metrics=client.get(f'/api/v1/metrics/{sid}',headers=auth).json()
    assert all(t['value'] is None for t in metrics['targets'])
    assert metrics['extraction']['fill_rate'] is None
    assert metrics['timing']['measured'] is False
    r=client.get(f'/api/v1/metrics/{sid}/export',headers=auth)
    wb=load_workbook(BytesIO(r.content))
    assert wb.sheetnames==['Метрики сессии','Время этапов','Сжатие кандидатов']
    assert wb.active['A2'].value==sid

def test_timing_cancellation_checkpoint(client,auth):
    sid=create(client,auth)
    class Lock:
        def extend(self,*a,**kw):pass
    with SessionLocal() as db:
        j=Job(session_id=sid,kind='normalize',status='RUNNING',params={});db.add(j);db.commit()
        execution=Execution(db,j,Lock());execution('PARSING')
        execution.current.started_at=now()-timedelta(seconds=5);db.commit()
        execution('EXTRACTION_CLASSIFICATION')
        j.status='CANCEL_REQUESTED';db.commit()
        with pytest.raises(Cancelled):execution('VECTORIZATION')
        execution.drain('EXTRACTION_CLASSIFICATION');execution.finish('CANCELLED')
    result=client.get(f'/api/v1/metrics/{sid}',headers=auth).json()
    assert result['timing']['parsing_seconds']>=5
    assert result['timing']['attempts']==1
    assert result['timing']['segments'][-1]['outcome']=='CANCELLED'
    assert 'VECTORIZATION' not in [s['stage'] for s in result['timing']['segments']]

def test_foreign_session_denied(client,auth):
    from app.db import Organization
    with SessionLocal() as db:
        org=Organization(name='Other');db.add(org);db.flush()
        s=ProcurementSession(organization_id=org.id,created_by=1,title='other');db.add(s);db.commit();sid=s.id
    assert client.post(f'/api/v1/procurement/sessions/{sid}/cancel',headers=auth).status_code==404
    assert client.delete(f'/api/v1/procurement/sessions/{sid}/files',headers=auth).status_code==404
    assert client.get(f'/api/v1/metrics/{sid}',headers=auth).status_code==404

def test_worker_acknowledges_cancellation_and_ignores_redelivery(client,auth,monkeypatch):
    from app import tasks
    sid=create(client,auth)
    class Lock:
        def acquire(self,**kw):return True
        def extend(self,*a,**kw):pass
        def owned(self):return True
        def release(self):pass
    class Redis:
        @staticmethod
        def from_url(url):return Redis()
        def lock(self,*a,**kw):return Lock()
    monkeypatch.setattr(tasks,'Redis',Redis)
    calls=[]
    def prepare(db,s,j,tick):
        calls.append(j.id)
        with SessionLocal() as other:
            other.get(Job,j.id).status='CANCEL_REQUESTED';other.commit()
        tick('SCHEMA_DISCOVERY')
        raise AssertionError('continued after cancellation')
    monkeypatch.setattr(tasks,'prepare',prepare)
    with SessionLocal() as db:
        j=Job(session_id=sid,kind='prepare',status='QUEUED',params={});db.add(j);db.commit();jid=j.id
    tasks.run_job.run(jid);tasks.run_job.run(jid)
    assert calls==[jid]
    with SessionLocal() as db:
        assert db.get(Job,jid).status=='CANCELLED'
        assert db.get(ProcurementSession,sid).status=='CANCELLED'
        assert db.scalar(select(StageTiming)).outcome=='CANCELLED'


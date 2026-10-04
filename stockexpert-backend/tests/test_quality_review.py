from io import BytesIO
import pytest
from sqlalchemy import select
from openpyxl import load_workbook
from app.db import SessionLocal,Item,Match,Job,QualityReview,QualityCompletion,User,Organization
from app.core.security import create_access_token
from test_reusable_bases import base
from test_shared_inventory import create_match

@pytest.fixture
def run(client,auth,base):
    sid,*_=base
    target,mid=create_match(client,auth,sid)
    with SessionLocal() as db:
        m=db.get(Match,mid)
        return target,m.job_id,mid,m.item_id,m.requirement_id

def save(client,auth,run,kind,ident,**kw):
    sid,jid,*_=run
    return client.put(f'/api/v1/quality/{sid}/{kind}/{ident}',headers=auth,json={'job_id':jid,**kw})
def finish(client,auth,run):
    return client.post(f'/api/v1/quality/{run[0]}/finalize',headers=auth,json={'job_id':run[1]})

def test_measured_metrics_only_after_finalization_and_no_prediction_overwrite(client,auth,run):
    sid,jid,mid,stock,req=run
    assert save(client,auth,run,'candidate',mid,suitable=True).status_code==200
    assert save(client,auth,run,'passport',stock,attributes={'voltage_v':3300},category_reviewed=True,category_id='cable').status_code==200
    assert save(client,auth,run,'passport',req,attributes={'voltage_v':6600}).status_code==200
    assert client.get(f'/api/v1/quality/{sid}',headers=auth).json()['f1'] is None
    q=finish(client,auth,run).json()
    assert q['f1']==.5 and q['accuracy']==1 and q['precision_shortlist']==1 and q['recall_at_150']==1
    assert q['coverage']['tp']==1 and q['coverage']['fp']==1 and q['coverage']['fn']==1
    with SessionLocal() as db:assert db.get(Item,req).passport['attributes']['voltage_v']==3300
    metrics=client.get(f'/api/v1/metrics/{sid}',headers=auth).json();assert metrics['targets'][0]['value']==.5
    wb=load_workbook(BytesIO(client.get(f'/api/v1/metrics/{sid}/export',headers=auth).content))
    assert any(r[2].value=='F1' and r[3].value==.5 for r in wb['Метрики сессии'])
    assert save(client,auth,run,'candidate',mid,suitable=False).status_code==200
    q=client.get(f'/api/v1/quality/{sid}',headers=auth).json();assert not q['finalized'] and q['f1'] is None
    q=finish(client,auth,run).json();assert q['precision_shortlist']==0 and q['recall_at_150'] is None

def test_recall_requires_all_pairs_including_outside_top150(client,auth,run):
    sid,jid,mid,stock,req=run
    with SessionLocal() as db:
        first=db.get(Match,mid)
        other=Match(session_id=sid,job_id=jid,requirement_id=req,item_id=req,result={**first.result,'rank':151,'is_shortlisted':False});db.add(other);db.commit();outside=other.id
    save(client,auth,run,'candidate',mid,suitable=True)
    q=finish(client,auth,run).json();assert q['precision_shortlist']==1 and q['recall_at_150'] is None
    assert q['coverage']['requirements_fully_reviewed']==0
    save(client,auth,run,'candidate',outside,suitable=True)
    q=finish(client,auth,run).json();assert q['recall_at_150']==.5
    assert q['coverage']['pairs_reviewed']==q['coverage']['pairs_total']==2

def test_missing_and_spurious_values_and_null_denominator(client,auth,run):
    sid,jid,mid,stock,req=run
    save(client,auth,run,'passport',stock,attributes={'voltage_v':None})
    q=finish(client,auth,run).json();assert q['f1']==0 and q['coverage']['fp']==1 and q['accuracy'] is None
    with SessionLocal() as db:
        item=db.get(Item,req);item.passport={**item.passport,'attributes':{'voltage_v':None}};db.commit()
    save(client,auth,run,'passport',req,attributes={'voltage_v':3300})
    q=finish(client,auth,run).json();assert q['f1']==0 and q['coverage']['fn']==1
    assert client.delete(f'/api/v1/quality/{sid}/passport/{stock}?job_id={jid}',headers=auth).status_code==200
    assert client.delete(f'/api/v1/quality/{sid}/passport/{req}?job_id={jid}',headers=auth).status_code==200
    assert finish(client,auth,run).status_code==422

def test_invalid_values_roles_and_stale_run(client,auth,run):
    sid,jid,mid,stock,req=run
    assert save(client,auth,run,'passport',stock,attributes={'unknown':1}).status_code==422
    assert save(client,auth,run,'passport',stock,attributes={'voltage_v':True}).status_code==422
    assert save(client,auth,run,'candidate',mid,suitable='false').status_code==422
    assert save(client,auth,run,'passport',stock,category_reviewed=True,category_id='unknown').status_code==422
    with SessionLocal() as db:
        user=db.scalar(select(User));user.role='buyer';db.commit()
    assert save(client,auth,run,'candidate',mid,suitable=True).status_code==403
    with SessionLocal() as db:
        user=db.scalar(select(User));user.role='admin';db.commit()
        new=Job(session_id=sid,kind='match',status='COMPLETED');db.add(new);db.commit()
    assert save(client,auth,run,'candidate',mid,suitable=True).status_code==409
    assert not client.get(f'/api/v1/quality/{sid}',headers=auth).json()['finalized']

def test_quality_scope_and_cleanup(client,auth,run,base):
    sid,jid,mid,stock,req=run;other=base[0]
    assert client.put(f'/api/v1/quality/{other}/candidate/{mid}',headers=auth,json={'job_id':jid,'suitable':True}).status_code==409
    save(client,auth,run,'candidate',mid,suitable=True);finish(client,auth,run)
    assert client.delete(f'/api/v1/procurement/sessions/{sid}/files',headers=auth).status_code==200
    with SessionLocal() as db:
        assert db.scalar(select(QualityReview.id)) is None
        assert db.scalar(select(QualityCompletion.job_id)) is None


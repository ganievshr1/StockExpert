from sqlalchemy import select
from app.db import SessionLocal,Job,Match,Item,StockPosition
from app import tasks
from test_reusable_bases import base

def create_match(client,auth,sid):
    target=client.post('/api/v1/procurement/sessions',headers=auth,json={'title':'Shared stock','base_session_id':sid}).json()['id']
    client.post(f'/api/v1/procurement/sessions/{target}/upload-requirement',headers=auth,files={'file':('need.txt','Кабель 3,3кВ'.encode())})
    jid=client.post(f'/api/v1/pipeline/{target}/run',headers=auth,json={}).json()['task_id'];tasks.run_job.run(jid)
    with SessionLocal() as db:
        assert db.get(Job,jid).status=='COMPLETED'
        mid=db.scalar(select(Match.id).where(Match.job_id==jid))
    assert client.post(f'/api/v1/matching/{target}/decide',headers=auth,json={'item_id':mid,'decision':'RECOMMENDED','comment':'Проверено'}).status_code==200
    return target,mid

def test_use_is_shared_and_second_use_rejected(client,auth,base):
    source,*_=base
    a,ma=create_match(client,auth,source);b,mb=create_match(client,auth,source)
    used=client.post(f'/api/v1/matching/{a}/candidates/{ma}/use',headers=auth);assert used.status_code==200,used.text
    assert client.post(f'/api/v1/matching/{b}/candidates/{mb}/use',headers=auth).status_code==409
    assert client.get(f'/api/v1/matching/{b}/candidates',headers=auth).json()['total']==0
    history=client.get(f'/api/v1/matching/{b}/candidates?shortlisted=false',headers=auth).json()['items'];assert history[0]['residual']['stock_state']=='USED'
    assert client.get('/api/v1/normalized-bases',headers=auth).json()==[]
    assert client.post('/api/v1/procurement/sessions',headers=auth,json={'title':'empty','base_session_id':source}).status_code==409
    metric=client.get(f'/api/v1/metrics/{b}',headers=auth).json()['efficiency'];assert metric['available_stock_rows']==0 and metric['available_shortlisted_pairs']==0
    with SessionLocal() as db:assert db.scalar(select(StockPosition)).used_by_session_id==a

def test_clear_base_affects_all_snapshots_but_preserves_history(client,auth,base):
    source,*_=base;a,ma=create_match(client,auth,source)
    r=client.delete(f'/api/v1/normalized-bases/{source}/stock',headers=auth);assert r.status_code==200 and r.json()['rows']==1
    assert client.get(f'/api/v1/matching/{a}/candidates',headers=auth).json()['total']==0
    assert client.get(f'/api/v1/matching/{a}/candidates?shortlisted=false',headers=auth).json()['total']==1
    assert client.post(f'/api/v1/matching/{a}/candidates/{ma}/use',headers=auth).status_code==409
    assert client.get('/api/v1/normalized-bases',headers=auth).json()==[]
    assert client.delete(f'/api/v1/normalized-bases/{source}/stock',headers=auth).json()['rows']==0

def test_clear_rejected_while_related_session_runs(client,auth,base):
    source,*_=base
    a=client.post('/api/v1/procurement/sessions',headers=auth,json={'title':'Busy','base_session_id':source}).json()['id']
    with SessionLocal() as db:db.add(Job(session_id=a,kind='match',status='RUNNING'));db.commit()
    assert client.delete(f'/api/v1/normalized-bases/{source}/stock',headers=auth).status_code==409
    with SessionLocal() as db:assert db.scalar(select(StockPosition.state))=='AVAILABLE'

def test_using_stock_requires_recommendation(client,auth,base):
    source,*_=base;a,ma=create_match(client,auth,source)
    with SessionLocal() as db:db.get(Match,ma).decision='PENDING';db.commit()
    assert client.post(f'/api/v1/matching/{a}/candidates/{ma}/use',headers=auth).status_code==409

def test_initial_excel_rows_saved_before_normalization(client,auth,monkeypatch):
    from io import BytesIO
    from openpyxl import Workbook
    from app.services import storage
    objects={};monkeypatch.setattr(storage,'put',lambda k,data,*a,**kw:objects.__setitem__(k,data));monkeypatch.setattr(storage,'delete_key',lambda k:objects.pop(k,None))
    sid=client.post('/api/v1/procurement/sessions',headers=auth,json={'title':'New Excel'}).json()['id']
    wb=Workbook();wb.active.append(['Название']);wb.active.append(['Кабель 3,3кВ']);buf=BytesIO();wb.save(buf)
    r=client.post(f'/api/v1/procurement/sessions/{sid}/upload-residuals',headers=auth,files={'file':('new.xlsx',buf.getvalue())});assert r.status_code==201
    with SessionLocal() as db:
        rows=list(db.scalars(select(Item).where(Item.session_id==sid)));assert len(rows)==2;assert all(i.passport is None for i in rows)
        assert rows[1].raw['0']=='Кабель 3,3кВ'
    assert client.delete(f'/api/v1/procurement/sessions/{sid}/documents/'+r.json()['id'],headers=auth).status_code==200
    with SessionLocal() as db:assert db.scalar(select(Item.id).where(Item.session_id==sid)) is None


import threading
import time
from sqlalchemy import select
import pytest
from app.core.config import settings
from app.db import SessionLocal,ProcurementSession,Document,Item,Job
from app.services import workflow


def setup_items(client,auth):
    sid=client.post('/api/v1/procurement/sessions',headers=auth,json={'title':'Parallel test'}).json()['id']
    with SessionLocal() as db:
        s=db.get(ProcurementSession,sid)
        d=Document(session_id=sid,kind='residual',filename='synthetic.xlsx',object_key='local',sha256='0'*64,meta={})
        db.add(d);db.flush()
        s.approved=True
        s.schema_doc={'mappings':[{'document_id':d.id,'sheet':'Stock','header_row':0,'name_columns':['0'],'category_column':None}],
                      'features':[{'key':'name','label':'Название','kind':'string','unit':'none','comparison':'eq','tolerance':0}],
                      'categories':[{'id':'cable','name':'Кабель','parent_id':None}]}
        for i in range(6):
            db.add(Item(id=f'item-{i}',session_id=sid,document_id=d.id,kind='residual',sheet='Stock',row_index=i+1,raw={'0':str(i)}))
        j=Job(session_id=sid,kind='normalize');db.add(j);db.commit();return sid,j.id


def test_bounded_parallel_calls_and_db_on_main_thread(client,auth,monkeypatch):
    sid,jid=setup_items(client,auth)
    monkeypatch.setattr(settings,'LLM_PROVIDER','deepseek');monkeypatch.setattr(settings,'LLM_CONCURRENCY',2)
    lock=threading.Lock();current=0;peak=0
    def extract(text,schema):
        nonlocal current,peak
        with lock: current+=1;peak=max(peak,current)
        time.sleep(.02)
        with lock: current-=1
        return {'category_id':'cable','attributes':{'name':text}},[]
    monkeypatch.setattr(workflow,'extract_one',extract)
    main=threading.get_ident()
    def tick(stage): assert threading.get_ident()==main
    with SessionLocal() as db:
        workflow.normalize_all(db,db.get(ProcurementSession,sid),db.get(Job,jid),tick)
        assert all(i.passport for i in db.scalars(select(Item)))
        assert db.get(ProcurementSession,sid).progress==65
    assert peak==2


def test_retry_reuses_committed_passports(client,auth,monkeypatch):
    sid,jid=setup_items(client,auth)
    monkeypatch.setattr(settings,'LLM_CONCURRENCY',1)
    calls=[]
    def extract(text,schema):
        calls.append(text)
        if text=='2': raise ValueError('simulated interruption')
        return {'category_id':'cable','attributes':{'name':text}},[]
    monkeypatch.setattr(workflow,'extract_one',extract)
    with SessionLocal() as db:
        with pytest.raises(ValueError,match='simulated interruption'):
            workflow.normalize_all(db,db.get(ProcurementSession,sid),db.get(Job,jid),lambda _:None)
        assert db.get(Item,'item-0').passport and db.get(Item,'item-1').passport
    resumed=[]
    def success(text,schema):
        resumed.append(text);return {'category_id':'cable','attributes':{'name':text}},[]
    monkeypatch.setattr(workflow,'extract_one',success)
    with SessionLocal() as db: workflow.normalize_all(db,db.get(ProcurementSession,sid),db.get(Job,jid),lambda _:None)
    assert set(resumed)=={'2','3','4','5'}


"""Model choice across API, queued jobs, thread workers and actual HTTP contracts.

Model endpoints, Redis and object storage are substitutes; document parsing,
validation, normalization, SQL persistence and in-memory Qdrant are real.
"""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from unittest.mock import Mock

import httpx
import pytest
from openpyxl import Workbook
from pydantic import SecretStr
from qdrant_client import QdrantClient
from sqlalchemy import select

from app import tasks
from app.core.config import settings
from app.core.security import create_access_token
from app.db import SessionLocal, ProcurementSession, Document, Item, Job, User, Organization, StockPosition
from app.services import llm, model_registry as registry, storage, search, workflow
from app.services.model_registry import preflight as real_preflight
from test_reusable_bases import base

API='/api/v1'
SCHEMA={'type':'object','properties':{'value':{'type':'string'}},'required':['value'],'additionalProperties':False}


def mock_http(monkeypatch, handler):
    original=httpx.Client
    monkeypatch.setattr(llm.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))


def fake_redis(monkeypatch):
    lock=Mock();lock.acquire.return_value=True;lock.owned.return_value=True
    redis=Mock();redis.lock.return_value=lock
    monkeypatch.setattr(tasks.Redis,'from_url',lambda _:redis)


def create(client,auth,profile='qwen25-7b-q4',**extra):
    r=client.post(API+'/procurement/sessions',headers=auth,json={'title':'Model test','llm_profile_id':profile,**extra})
    assert r.status_code==201,r.text
    return r.json()


def test_model_allowlist_and_session_isolation(client,auth):
    a=create(client,auth);b=create(client,auth,'deepseek')
    url=API+'/procurement/sessions/'+a['id']
    for profile in ('arbitrary-model','http://other-service',''):
        assert client.put(url+'/llm',headers=auth,json={'llm_profile_id':profile}).status_code==422
    assert client.put(url+'/llm',headers=auth,json={'llm_profile_id':'qwen3-8b','url':'http://other-service'}).status_code==422
    changed=client.put(url+'/llm',headers=auth,json={'llm_profile_id':'qwen3-8b'})
    assert changed.json()['llm_config']['model']=='qwen3:8b-q4_K_M'
    assert client.get(url,headers=auth).json()['llm_config']['thinking'] is False
    assert client.get(API+'/procurement/sessions/'+b['id'],headers=auth).json()['llm_config']['provider']=='deepseek'


def test_catalogue_metadata_no_paid_calls_or_secret_leaks(client,auth,monkeypatch):
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY',SecretStr('do-not-expose-test-key'))
    qwen=registry.get_profile('qwen3-8b');paths=[]
    def handler(req):
        paths.append(req.url.path)
        assert req.method=='GET' and 'authorization' not in req.headers
        return httpx.Response(200,json={'models':[{'name':qwen.model}]} if req.url.path=='/api/tags' else {'version':'0.9.0'})
    mock_http(monkeypatch,handler)
    assert client.get(API+'/system/llm-models').status_code==401
    r=client.get(API+'/system/llm-models',headers=auth)
    assert r.status_code==200 and 'do-not-expose-test-key' not in r.text
    models={m['profile_id']:m for m in r.json()['models']}
    assert len(models)==5 and models['qwen3-8b']['availability']['available']
    assert models['qwen25-7b-q4']['availability']['status']=='not_installed'
    assert models['deepseek']['availability']['status']=='configured'
    assert paths==['/api/tags','/api/version']


@pytest.mark.parametrize('version,status',[('0.8.0','unsupported_version'),(None,'unsupported_version'),('0.9.0','installed'),('0.12.3','installed')])
def test_qwen3_version_gate(version,status):
    p=registry.get_profile('qwen3-8b')
    assert registry.availability(p,{'reachable':True,'names':{p.model},'version':version})['status']==status


def test_unavailable_model_rejects_enqueue_without_job(client,auth,monkeypatch):
    session=create(client,auth);sid=session['id']
    with SessionLocal() as db:
        for kind in ('residual','requirement'):
            db.add(Document(session_id=sid,kind=kind,filename='x.txt',object_key=kind,sha256='0'*64,meta={}))
        db.commit()
    monkeypatch.setattr(registry,'preflight',real_preflight)
    monkeypatch.setattr(registry,'probe_ollama',lambda:{'reachable':True,'names':set(),'version':'0.9.0'})
    r=client.post(f'{API}/pipeline/{sid}/prepare',headers=auth)
    assert r.status_code==503 and 'ollama pull' in r.text
    with SessionLocal() as db:
        assert db.scalar(select(Job)) is None
        assert db.get(ProcurementSession,sid).status=='CREATED'
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY',SecretStr(''))
    with pytest.raises(llm.LLMError,match='DEEPSEEK_API_KEY'):real_preflight(registry.get_profile('deepseek'))


def test_selection_requires_editor_and_session_ownership(client,auth):
    s=create(client,auth)
    with SessionLocal() as db:
        u=db.scalar(select(User));u.role='viewer';db.commit();viewer={'Authorization':'Bearer '+create_access_token(u)}
    assert client.put(f'{API}/procurement/sessions/{s["id"]}/llm',headers=viewer,json={'llm_profile_id':'deepseek'}).status_code==403
    with SessionLocal() as db:
        o=Organization(name='Other');db.add(o);db.flush();u=db.scalar(select(User));u.role='buyer';u.organization_id=o.id;db.commit();other={'Authorization':'Bearer '+create_access_token(u)}
    assert client.put(f'{API}/procurement/sessions/{s["id"]}/llm',headers=other,json={'llm_profile_id':'deepseek'}).status_code==404


def test_queued_and_retried_job_keeps_snapshot(client,auth,monkeypatch):
    s=create(client,auth,'deepseek');sid=s['id']
    with SessionLocal() as db:
        for kind in ('residual','requirement'):
            db.add(Document(session_id=sid,kind=kind,filename='test.txt',object_key=kind,sha256='0'*64,meta={}))
        db.commit()
    jid=client.post(f'{API}/pipeline/{sid}/prepare',headers=auth).json()['task_id']
    monkeypatch.setattr(settings,'LLM_MODEL','different-server-default')
    monkeypatch.setattr(settings,'LLM_CONCURRENCY',1)
    assert client.put(f'{API}/procurement/sessions/{sid}/llm',headers=auth,json={'llm_profile_id':'qwen3-8b'}).status_code==409
    seen=[];fake_redis(monkeypatch)
    def run(*args):
        seen.append(llm.current_config().model_dump())
        if len(seen)==1:raise ValueError('temporary operator-fixable failure')
    monkeypatch.setattr(tasks,'prepare',run)
    tasks.run_job.run(jid)
    assert client.put(f'{API}/procurement/sessions/{sid}/llm',headers=auth,json={'llm_profile_id':'qwen3-8b'}).status_code==409
    assert client.post(f'{API}/pipeline/{sid}/jobs/{jid}/retry',headers=auth).status_code==202
    tasks.run_job.run(jid)
    assert seen==[s['llm_config'],s['llm_config']]
    with SessionLocal() as db:
        job=db.get(Job,jid)
        assert job.status=='COMPLETED' and job.params['llm_config']==s['llm_config']
        assert job.params['runtime']['model']==s['llm_config']['model']
    assert llm.current_config().model=='different-server-default' # Context restored.


@pytest.mark.parametrize('profile',[p[0] for p in registry.LOCAL_MODELS]+['deepseek'])
def test_selected_model_through_every_pipeline_stage(profile,client,auth,monkeypatch):
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY',SecretStr('fake-test-key'))
    config=registry.get_profile(profile);objects={};calls=[]
    monkeypatch.setattr(storage,'put',lambda k,b,*a,**kw:objects.__setitem__(k,b))
    monkeypatch.setattr(storage,'get',lambda k:objects[k])
    q=QdrantClient(':memory:');monkeypatch.setattr(search,'client',lambda:q)
    monkeypatch.setattr(llm,'embed',lambda texts:[[1.,.5,.2] for _ in texts])
    fake_redis(monkeypatch)
    def handler(req):
        p=json.loads(req.content)
        assert p['model']==config.model
        if req.url.path=='/api/generate':
            assert config.provider=='ollama' and p['keep_alive']==0
            return httpx.Response(200,json={'done':True})
        if config.provider=='ollama':
            assert req.url.path=='/api/chat' and 'authorization' not in req.headers
            assert isinstance(p['format'],dict) and p['options']['num_ctx']==config.num_ctx
            assert p['options']['num_predict']==config.max_tokens
            assert (p.get('think') is False) if profile=='qwen3-8b' else ('think' not in p)
        else:
            assert req.url.path=='/chat/completions' and req.headers['authorization']=='Bearer fake-test-key'
            assert p['thinking']=={'type':'enabled'}
        data=json.loads(p['messages'][1]['content']);schema=data['schema'];source=data['data']
        if 'header_row' in schema['properties']:
            phase='prepare';result={'header_row':1,'name_columns':['B'],'category_column':'A'}
        elif 'name_evidence' in schema['properties']:
            phase='extract';result={'name':'Кабель 3,3кВ','name_evidence':'Кабель 3,3кВ','values':[{'key':'voltage_v','label':'Напряжение','kind':'number','value':'3,3','unit':'кВ','target_unit':'V','evidence':'3,3кВ'}]}
        elif 'passports' in source:
            phase='taxonomy';result={'items':[{'item_id':x['item_id'],'material':'Кабель','subtype':None} for x in source['passports']]}
        elif 'match_id' in schema['properties']:
            phase='taxonomy';result={'match_id':None}
        else:
            phase='requirements';result={'items':[source['text']]}
        calls.append((phase,threading.get_ident()))
        content=json.dumps(result,ensure_ascii=False)
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':content}}]} if config.provider=='deepseek' else {'done_reason':'stop','message':{'content':content}})
    mock_http(monkeypatch,handler)
    sid=create(client,auth,profile,taxonomy_enabled=True)['id'];url=f'{API}/procurement/sessions/{sid}'
    wb=Workbook();wb.active.append(['Тип','Описание']);wb.active.append(['Кабель','Кабель 3,3кВ']);out=BytesIO();wb.save(out)
    assert client.post(url+'/upload-residuals',headers=auth,files={'file':('stock.xlsx',out.getvalue())}).status_code==201
    assert client.post(url+'/upload-requirement',headers=auth,files={'file':('need.txt','Кабель 3,3кВ'.encode())}).status_code==201
    first=client.post(f'{API}/pipeline/{sid}/prepare',headers=auth).json()['task_id'];tasks.run_job.run(first)
    assert client.post(f'{API}/pipeline/{sid}/approve-mapping',headers=auth).status_code==200
    normalization=client.post(f'{API}/pipeline/{sid}/run',headers=auth,json={}).json()['task_id'];tasks.run_job.run(normalization)
    with SessionLocal() as db:
        assert db.get(Job,normalization).status=='COMPLETED',db.get(Job,normalization).error
        assert db.get(ProcurementSession,sid).status=='TAXONOMY_REVIEW'
    assert client.post(f'{API}/taxonomy/{sid}/approve',headers=auth,json={'freeze_version':'test-v1'}).status_code==200
    last=client.post(f'{API}/pipeline/{sid}/run',headers=auth,json={}).json()['task_id'];tasks.run_job.run(last)
    with SessionLocal() as db:
        assert db.get(Job,last).status=='COMPLETED',db.get(Job,last).error
        s=db.get(ProcurementSession,sid);assert s.status=='MATCHED'
        assert s.schema_doc['mappings'][0]['name_columns']==['1']
        for item in db.scalars(select(Item).where(Item.passport.is_not(None))):
            assert item.passport['provenance']['llm_fingerprint']==config.fingerprint()
            assert item.passport['provenance']['model']==config.model
    assert [phase for phase,_ in calls].count('prepare')==1
    assert [phase for phase,_ in calls].count('requirements')==1
    assert [phase for phase,_ in calls].count('extract')==2
    assert all(tid!=threading.get_ident() for phase,tid in calls if phase=='extract')
    measured=client.get(f'{API}/metrics/{sid}',headers=auth).json()
    assert measured['llm']['selected']['profile_id']==profile
    assert measured['matching']['runtime']['model']==config.model
    assert all(j['llm_config']['profile_id']==profile for j in measured['llm']['jobs'])


def test_thread_contexts_do_not_leak_between_sessions():
    barrier=threading.Barrier(2)
    def run(profile):
        config=registry.get_profile(profile)
        with llm.using_config(config):
            barrier.wait(timeout=5)
            with llm.using_config(registry.get_profile('qwen25-14b')):
                assert llm.current_config().profile_id=='qwen25-14b'
            return llm.current_config().profile_id
    with ThreadPoolExecutor(2) as pool:
        a=pool.submit(run,'qwen25-7b-q4');b=pool.submit(run,'qwen3-8b')
        assert a.result()=='qwen25-7b-q4' and b.result()=='qwen3-8b'


@pytest.mark.parametrize('content,thinking,match',[
    ('{"value":"ok"}','unexpected thoughts','think=false'),
    ('{bad','', 'Некорректный JSON'),
    ('{"value":123}','','properties/value/type'),
    ('','', 'пустой ответ')])
def test_local_structured_failures_are_actionable(monkeypatch,content,thinking,match):
    mock_http(monkeypatch,lambda req:httpx.Response(200,json={'done_reason':'stop','message':{'content':content,'thinking':thinking}}))
    with llm.using_config(registry.get_profile('qwen3-8b')):
        with pytest.raises(llm.LLMError,match=match):llm.structured('extract',{},SCHEMA)


def test_changing_model_reuses_base_without_resurrecting_stock(client,auth,base,monkeypatch):
    source,objects,calls,_,old_item=base
    # Original XLSX still contains a second, already consumed row.
    with SessionLocal() as db:
        original=db.get(Item,old_item);doc=db.get(Document,original.document_id)
        wb=Workbook();wb.active.title='Stock';wb.active.append(['Описание']);wb.active.append(['Остаток Кабель 3,3кВ']);wb.active.append(['Уже использованный Кабель 3,3кВ']);out=BytesIO();wb.save(out);objects[doc.object_key]=out.getvalue()
        db.add(Item(session_id=source,document_id=doc.id,kind='residual',sheet='Stock',row_index=3,raw={'0':'Уже использованный Кабель 3,3кВ'},source_text='Уже использованный Кабель 3,3кВ',passport=original.passport,category='cable'))
        # Unnormalized blank rows must not become new stock extraction requests.
        db.add(Item(session_id=source,document_id=doc.id,kind='residual',sheet='Stock',row_index=4,raw={},source_text=''))
        from app.services.inventory import ensure_inventory
        db.flush();ensure_inventory(db,db.get(ProcurementSession,source));db.flush()
        used=db.scalar(select(Item).where(Item.session_id==source,Item.row_index==3));db.get(StockPosition,used.stock_id).state='USED';db.commit()
    target=create(client,auth,'qwen3-8b',base_session_id=source,taxonomy_enabled=True)
    assert target['approved'] is True and target['status']=='BASE_READY'
    assert target['base_snapshot']['reused_passports'] is True
    assert target['llm_config']['profile_id']=='qwen3-8b'
    assert target['llm_locked'] is False
    model_url=f"{API}/procurement/sessions/{target['id']}/llm"
    assert client.put(model_url,headers=auth,json={'llm_profile_id':'qwen25-7b-q4'}).status_code==200
    assert client.put(model_url,headers=auth,json={'llm_profile_id':'qwen3-8b'}).status_code==200
    with SessionLocal() as db:
        items=list(db.scalars(select(Item).where(Item.session_id==target['id'])))
        assert len(items)==2  # Header + available position; used position is excluded.
        row=next(i for i in items if i.row_index==2)
        original=db.get(Item,old_item)
        assert row.stock_id==original.stock_id
        expected={**original.passport,'reuse':{'session_id':source,'item_id':old_item}}
        assert row.passport==expected
    sid=target['id']
    assert client.post(f'{API}/pipeline/{sid}/prepare',headers=auth).status_code==409
    response=client.post(f'{API}/procurement/sessions/{sid}/upload-requirement',headers=auth,files={'file':('need.txt','Кабель 3,3кВ'.encode())})
    assert response.status_code==201,response.text
    jid=client.post(f'{API}/pipeline/{sid}/run',headers=auth,json={}).json()['task_id'];tasks.run_job.run(jid)
    assert client.put(model_url,headers=auth,json={'llm_profile_id':'qwen25-7b-q4'}).status_code==409
    with SessionLocal() as db:
        job=db.get(Job,jid)
        assert job.status=='COMPLETED',job.error
        assert job.params['normalization_counts']=={'requirement':1}
        assert job.params['index_stats']=={'vectors_reused':1,'vectors_created':0}
        row=db.scalar(select(Item).where(Item.session_id==sid,Item.kind=='residual',Item.row_index==2))
        assert row.passport==expected
        requirement=db.scalar(select(Item).where(Item.session_id==sid,Item.kind=='requirement'))
        assert requirement.passport['provenance']['profile_id']=='qwen3-8b'
    assert len(calls)==2  # Split/extract requirement; no mapping or stock extraction.


def test_legacy_session_exposes_uncertainty_and_retains_recorded_model(client,auth,monkeypatch):
    sid=create(client,auth)['id']
    with SessionLocal() as db:
        s=db.get(ProcurementSession,sid);s.llm_config={}
        db.add(Job(session_id=sid,kind='normalize',status='FAILED',params={'runtime':{'provider':'deepseek','model':'historic-model'}}));db.commit()
    monkeypatch.setattr(settings,'LLM_MODEL','different-default')
    r=client.get(f'{API}/procurement/sessions/{sid}',headers=auth).json()
    assert r['llm_legacy'] is True and r['llm_locked'] is True
    assert r['llm_config']['model']=='historic-model'


def test_long_local_request_keeps_exclusive_job_lease(client,auth,monkeypatch):
    from app.services.execution import Execution
    s=create(client,auth,'qwen25-14b')
    with SessionLocal() as db:
        job=Job(session_id=s['id'],kind='prepare',params={'llm_config':s['llm_config']})
        db.add(job);db.commit()
        lock=Mock()
        execution=Execution(db,job,lock);execution('SCHEMA_DISCOVERY')
        timeout=s['llm_config']['timeout']
        assert lock.extend.call_args.args[0]>timeout*(settings.LLM_RETRIES+1)
        assert lock.extend.call_args.kwargs['replace_ttl'] is True


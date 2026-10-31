"""H2 is independently optional for each H1 extractor, including worker/export paths."""
from copy import deepcopy
import json
import pytest
from sqlalchemy import select
from qdrant_client import QdrantClient
from app import tasks
from app.db import SessionLocal, ProcurementSession, Item, Job, Match, RunSnapshot
from app.services import llm, search, taxonomy
from app.services.model_registry import profiles
from test_h1_engine import upload_and_prepare, ROWS

API='/api/v1'
PROFILES=[p.profile_id for p in profiles()]
CASES=[('regex','qwen25-7b-q5'),*[('llm',p) for p in PROFILES]]

def model_response(task, data, schema):
    if 'name_evidence' in schema['properties']:
        text=data['source']
        return {'name':text,'name_evidence':text,'values':[{'key':'voltage_v','label':'Напряжение','kind':'number','value':'6','unit':'кВ','target_unit':'V','evidence':'6,0кВ'}]}
    if 'text' in data and 'items' in schema['properties']:
        return {'items':[data['text']]}
    pytest.fail('Unexpected category induction or taxonomy resolution call')

def prepare_mode(client,auth,monkeypatch,method,profile,enabled):
    sid,_=upload_and_prepare(client,auth,monkeypatch,rows=ROWS[:1])
    url=f'{API}/procurement/sessions/{sid}'
    assert client.put(url+'/taxonomy-mode',headers=auth,json={'taxonomy_enabled':enabled}).status_code==200
    assert client.put(url+'/llm',headers=auth,json={'llm_profile_id':profile}).status_code==200
    assert client.put(url+'/normalization-method',headers=auth,json={'normalization_method':method}).status_code==200
    state=client.get(url,headers=auth).json()
    assert state['taxonomy_enabled'] is enabled
    assert state['llm_config']['profile_id']==profile
    return sid

@pytest.mark.parametrize('method,profile',CASES)
def test_default_off_and_explicit_on_are_independent_of_model(client,auth,method,profile):
    payload={'title':'H2 choice','normalization_method':method,'llm_profile_id':profile}
    off=client.post(API+'/procurement/sessions',headers=auth,json=payload)
    on=client.post(API+'/procurement/sessions',headers=auth,json={**payload,'taxonomy_enabled':True})
    assert off.status_code==on.status_code==201
    assert off.json()['taxonomy_enabled'] is False
    assert on.json()['taxonomy_enabled'] is True
    url=f'{API}/procurement/sessions/'+off.json()['id']
    for p in PROFILES:
        changed=client.put(url+'/llm',headers=auth,json={'llm_profile_id':p})
        assert changed.status_code==200 and changed.json()['taxonomy_enabled'] is False

@pytest.mark.parametrize('method,profile',CASES)
@pytest.mark.parametrize('path',['start-normalization','run'])
def test_disabled_h2_skips_categories_in_worker_and_keeps_h1_and_h3(client,auth,monkeypatch,method,profile,path):
    sid=prepare_mode(client,auth,monkeypatch,method,profile,False)
    def response(*args):
        assert method=='llm', 'Regex called the generative model'
        assert llm.current_config().profile_id==profile
        return model_response(*args)
    monkeypatch.setattr(llm,'structured',response)
    monkeypatch.setattr(taxonomy,'build',lambda *a,**kw:pytest.fail('H2 called despite disabled mode'))
    monkeypatch.setattr(taxonomy,'propose',lambda *a,**kw:pytest.fail('Category proposals called despite disabled mode'))
    if method=='regex':monkeypatch.setattr('app.services.model_registry.preflight',lambda *a:pytest.fail('Regex without H2 requires no LLM preflight'))
    monkeypatch.setattr(llm,'unload',lambda:None)
    monkeypatch.setattr(llm,'embed',lambda texts:[[1.,.5,.2] for _ in texts])
    q=QdrantClient(':memory:');monkeypatch.setattr(search,'client',lambda:q)
    result=client.post(f'{API}/pipeline/{sid}/{path}',headers=auth,json={})
    assert result.status_code==202,result.text
    jid=result.json()['task_id'];tasks.run_job.run(jid)
    with SessionLocal() as db:
        job=db.get(Job,jid);s=db.get(ProcurementSession,sid)
        assert job.status=='COMPLETED',job.error
        assert s.status==('MATCHED' if path=='run' else 'NORMALIZED')
        assert s.approved and s.taxonomy_version is None
        assert s.schema_doc['taxonomy_status']=='skipped' and s.schema_doc['categories']==[]
        rows=list(db.scalars(select(Item).where(Item.session_id==sid,Item.passport.is_not(None))))
        assert len(rows)==2
        assert all(i.category is None and i.passport['category_id'] is None for i in rows)
        assert all(i.passport['normalized_attributes']['voltage_v']==6000 for i in rows)
        assert db.get(RunSnapshot,jid).payload['session']['taxonomy_enabled'] is False
    export=client.get(f'{API}/runs/{sid}/{jid}/export?stage=all&format=json',headers=auth)
    assert export.status_code==200,export.text
    data=export.json()
    assert data['run']['taxonomy_enabled'] is False and data['h2']['status']=='skipped'
    assert data['passports'][0]['attributes']['voltage_v']==6000
    assert client.post(f'{API}/taxonomy/{sid}/rebuild',headers=auth).status_code==409
    if path=='start-normalization':
        result=client.post(f'{API}/pipeline/{sid}/run',headers=auth,json={})
        jid=result.json()['task_id'];tasks.run_job.run(jid)
    with SessionLocal() as db:
        job=db.get(Job,jid);assert job.status=='COMPLETED',job.error
        assert db.get(ProcurementSession,sid).status=='MATCHED'
        pairs=list(db.scalars(select(Match).where(Match.job_id==jid)))
        assert len(pairs)==1 and pairs[0].result['is_shortlisted']
    q.close()

@pytest.mark.parametrize('profile',PROFILES)
def test_explicit_h1_stops_before_enabled_h2_for_every_llm(client,auth,monkeypatch,profile):
    sid=prepare_mode(client,auth,monkeypatch,'llm',profile,True)
    monkeypatch.setattr(llm,'structured',model_response)
    monkeypatch.setattr(taxonomy,'build',lambda *a:pytest.fail('H1-only request must not run H2'))
    result=client.post(f'{API}/pipeline/{sid}/start-normalization',headers=auth,json={})
    jid=result.json()['task_id'];tasks.run_job.run(jid)
    with SessionLocal() as db:
        job=db.get(Job,jid);s=db.get(ProcurementSession,sid)
        assert job.status=='COMPLETED',job.error
        assert s.status=='H1_READY' and not s.approved
        assert s.schema_doc['categories']==[]
        assert db.get(RunSnapshot,jid) is not None
    assert client.post(f'{API}/taxonomy/{sid}/rebuild',headers=auth).status_code==202


def test_disabled_h2_direct_guard_never_mutates_session(auth):
    from types import SimpleNamespace
    s=SimpleNamespace(taxonomy_enabled=False,schema_doc={'categories':[]})
    before=deepcopy(s.__dict__)
    with pytest.raises(ValueError,match='H2 отключён'):taxonomy.build(None,s,None,None)
    assert s.__dict__==before

def test_migration_preserves_existing_modes_and_snapshots(tmp_path):
    import os, sqlite3, subprocess, sys
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    path=tmp_path/'taxonomy_modes.db'
    env={**os.environ,'DATABASE_URL':'sqlite:///'+str(path),'PYTHONPATH':str(root)}
    def upgrade(target):
        subprocess.run([sys.executable,'-m','alembic','upgrade',target],cwd=root,env=env,check=True,capture_output=True)
    upgrade('0010')
    seed='''
from app.db import SessionLocal, Organization, User, ProcurementSession, Job, RunSnapshot
with SessionLocal() as db:
    org=Organization(name='Historic');db.add(org);db.flush()
    user=User(username='historic',password_hash='unused',role='admin',organization_id=org.id);db.add(user);db.flush()
    for enabled in (True,False):
        s=ProcurementSession(title='historic-'+str(enabled),organization_id=org.id,created_by=user.id,taxonomy_enabled=enabled)
        db.add(s);db.flush()
        job=Job(session_id=s.id,kind='match',status='COMPLETED',params={'taxonomy_enabled':enabled});db.add(job);db.flush()
        db.add(RunSnapshot(job_id=job.id,session_id=s.id,payload={'session':{'taxonomy_enabled':enabled},'preserved':['H1','H2','H3']}))
    db.commit()
'''
    subprocess.run([sys.executable,'-c',seed],cwd=root,env=env,check=True,capture_output=True)
    def contents():
        with sqlite3.connect(path) as db:
            return {t:db.execute('SELECT * FROM '+t+' ORDER BY 1').fetchall() for t in ['procurement_sessions','jobs','run_snapshots']}
    before=contents();upgrade('head');assert contents()==before
    with sqlite3.connect(path) as db:
        columns=[r[1] for r in db.execute('PRAGMA table_info(procurement_sessions)') if r[1] not in ('id','taxonomy_enabled')]
        cols=','.join(columns)
        db.execute(f"INSERT INTO procurement_sessions (id,{cols}) SELECT 'new-default',{cols} FROM procurement_sessions LIMIT 1")
        assert db.execute("SELECT taxonomy_enabled FROM procurement_sessions WHERE id='new-default'").fetchone()==(0,)
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]

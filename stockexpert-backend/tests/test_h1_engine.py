from copy import deepcopy
import hashlib
from io import BytesIO
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from openpyxl import Workbook
import pytest
from qdrant_client import QdrantClient
from sqlalchemy import select
from app import tasks
from app.db import SessionLocal, ProcurementSession, Item, Job, Document, Match, User, Organization, public
from app.core.security import create_access_token
from app.services import llm, search, storage, normalization_engine as engine
from app.services.expert_evaluation import calculate
from app.services.expert_reference import parse_reference
from test_model_selection import fake_redis
from test_expert_evaluation import populate
from test_reusable_bases import base
from migration_seed import seed_legacy, historic_rows

API = '/api/v1'
ROWS = ['Кабель КПвПпБП-150 3х70-6,0кВ',
        'Датчик давления ДД-400-4,0МПа-G1/2-4-20мА-ХЛ1',
        'Радиостанция РС-212-470МГц-10Вт',
        'Плита ПДН,м (6х1,75х0,16м)-АтVI;60; F250',
        'Термостабилизатор ТК25/14,5.М7-04 УХЛ1']


def upload_and_prepare(client, auth, monkeypatch, rows=ROWS, requirement=None):
    objects = {}
    monkeypatch.setattr(storage, 'put', lambda k, b, *a, **kw: objects.__setitem__(k, b))
    monkeypatch.setattr(storage, 'get', lambda k: objects[k])
    fake_redis(monkeypatch)
    r = client.post(API+'/procurement/sessions', headers=auth, json={
        'title': 'Regex in primary engine', 'llm_profile_id': 'qwen3-8b', 'normalization_method': 'regex', 'taxonomy_enabled': True})
    assert r.status_code == 201, r.text
    sid = r.json()['id']
    wb = Workbook(); wb.active.title = 'Лист 1'
    wb.active.append(['Предприятие', 'КИП и средства автоматизации', 'Система'])
    for source in rows:
        wb.active.append(['Company', 'Кабель 99х999 999В', source])
    stream = BytesIO(); wb.save(stream)
    data = stream.getvalue()
    for kind, filename, content in [('residuals', 'stock.xlsx', data), ('requirement', 'need.txt', (requirement or rows[0]).encode())]:
        r = client.post(f'{API}/procurement/sessions/{sid}/upload-{kind}', headers=auth, files={'file': (filename, content)})
        assert r.status_code == 201, r.text
    r = client.post(f'{API}/pipeline/{sid}/prepare', headers=auth)
    assert r.status_code == 202, r.text
    tasks.run_job.run(r.json()['task_id'])
    with SessionLocal() as db:
        job = db.get(Job, r.json()['task_id'])
        assert job.status == 'COMPLETED', job.error
        assert db.get(ProcurementSession, sid).schema_doc['mappings'][0]['name_columns'] == ['2']
    assert client.post(f'{API}/pipeline/{sid}/approve-mapping', headers=auth).status_code == 200
    return sid, data


def run_h1(client, auth, sid, expected_status="H1_READY"):
    r = client.post(f'{API}/pipeline/{sid}/start-normalization', headers=auth, json={})
    assert r.status_code == 202, r.text
    jid = r.json()['task_id']; tasks.run_job.run(jid)
    with SessionLocal() as db:
        job = db.get(Job, jid)
        assert job.status == 'COMPLETED', job.error
        assert db.get(ProcurementSession, sid).status == expected_status
    return jid


def no_model(*args, **kwargs):
    pytest.fail('H1 regular expressions called an LLM or its preflight')


def test_main_h1_no_qwen_and_existing_metrics_and_history(client, auth, monkeypatch):
    with SessionLocal() as db:
        old, _, _, _ = populate(db); old_id = old.id
        db.expire_all()  # Compare persisted SQLite values, including timestamp representation.
        historical = [deepcopy(public(i)) for i in db.scalars(select(Item).where(Item.session_id == old_id))]
        old_session = deepcopy(public(old))
        old_jobs = [deepcopy(public(j)) for j in db.scalars(select(Job).where(Job.session_id == old_id))]
    monkeypatch.setattr(llm, 'structured', no_model)
    monkeypatch.setattr('app.services.model_registry.preflight', no_model)
    monkeypatch.setattr('app.services.passports.extract', no_model)
    monkeypatch.setattr('app.services.requirement_extraction.split', no_model)
    sid, data = upload_and_prepare(client, auth, monkeypatch, rows=ROWS[:1])
    jid = run_h1(client, auth, sid)
    with SessionLocal() as db:
        session = db.get(ProcurementSession, sid)
        stock = db.scalar(select(Item).where(Item.session_id == sid, Item.kind == 'residual', Item.row_index == 2))
        assert stock.source_text == ROWS[0]  # No company/category/column header in H1 input.
        p = stock.passport
        assert p['normalized_attributes']['voltage_v'] == 6000
        assert p['normalized_attributes']['core_count'] == 3
        assert p['provenance']['provider'] == 'regex'
        assert p['provenance']['llm_calls'] == 0 and p['provenance']['pipeline_adapter_sha256'] == engine.ADAPTER_SHA256
        assert db.get(Job, jid).params['runtime']['provider'] == 'regex'
        assert db.get(Job, jid).params['normalization_counts'] == {'requirement': 1, 'residual': 1}
        unchanged = deepcopy(public(db.get(Job, jid)))
        assert public(db.get(ProcurementSession, old_id)) == old_session
        assert [public(i) for i in db.scalars(select(Item).where(Item.session_id == old_id))] == historical
        assert [public(j) for j in db.scalars(select(Job).where(Job.session_id == old_id))] == old_jobs
        ref = {'format': 'stockexpert-expert-v1', 'title': 'Manual cable fixture', 'version': '1',
               'sources': [{'filename': 'stock.xlsx', 'sha256': hashlib.sha256(data).hexdigest(), 'kind': 'residual'}],
               'features': [{'key': k, 'label': label, 'unit': unit} for k, label, unit in [
                   ('designation', 'Марка', 'none'), ('core_count', 'Число жил', 'pcs'),
                   ('core_cross_section_mm2', 'Сечение', 'mm2'), ('voltage_v', 'Напряжение', 'V')]],
               'passports': [{'id': 'r1', 'source_sha256': hashlib.sha256(data).hexdigest(), 'kind': 'residual', 'sheet': 'Лист 1', 'row': 2,
                             'source_name': ROWS[0], 'name': ROWS[0], 'category_path': ['Кабели'],
                             'attributes': {k: {'value': v, 'evidence': e} for k,v,e in [
                                 ('designation','КПвПпБП-150','КПвПпБП-150'), ('core_count',3,'3х70'),
                                 ('core_cross_section_mm2',70,'3х70'), ('voltage_v',6000,'6,0кВ')]}}], 'pairs': []}
        reference = parse_reference(json.dumps(ref).encode(), 'ref.json')[0]
        status, scored = calculate(db, session, reference)
        assert status == 'PARTIAL' and scored['metrics']['h1_f1'] == 1
        assert scored['metrics']['h2_leaf_accuracy'] is None and scored['metrics']['h3_recall_at_150'] is None
        assert scored['models'][0]['model'].startswith('regex-')
    tasks.run_job.run(jid)
    with SessionLocal() as db:
        assert public(db.get(Job, jid)) == unchanged
    assert client.put(f'{API}/procurement/sessions/{sid}/normalization-method', headers=auth, json={'normalization_method': 'llm'}).status_code == 409
    # H2 is independently selectable after CPU-only H1.
    assert client.put(f'{API}/procurement/sessions/{sid}/llm', headers=auth, json={'llm_profile_id':'qwen25-7b-q4'}).status_code == 200


def test_h1_passports_flow_into_real_h2_and_matching(client, auth, monkeypatch):
    monkeypatch.setattr(llm, 'structured', no_model)
    sid, _ = upload_and_prepare(client, auth, monkeypatch)
    run_h1(client, auth, sid)
    received = []
    def classify(task, data, schema):
        if 'passports' in data:
            received.extend(data['passports'])
            return {'items': [{'item_id': p['item_id'], 'material': p['name'].split()[0], 'subtype': None} for p in data['passports']]}
        if 'match_id' in schema['properties']:
            return {'match_id': None}
        pytest.fail('H2 called H1 extraction/splitting again')
    monkeypatch.setattr(llm, 'structured', classify)
    r = client.post(f'{API}/taxonomy/{sid}/rebuild', headers=auth)
    assert r.status_code == 202, r.text
    tasks.run_job.run(r.json()['task_id'])
    with SessionLocal() as db:
        job = db.get(Job, r.json()['task_id']); assert job.status == 'COMPLETED', job.error
        s = db.get(ProcurementSession, sid)
        assert s.status == 'TAXONOMY_REVIEW'
        assert len(received) == len(ROWS)  # Same cable requirement shares proposal.
        assert any(p['name'].startswith('Термостабилизатор') for p in received)
        assert {'A','kW','Hz'} <= {f['unit'] for f in s.schema_doc['features']}
        for item in db.scalars(select(Item).where(Item.session_id == sid, Item.passport.is_not(None))):
            assert item.passport['provenance']['provider'] == 'regex'
            assert item.passport['taxonomy_version'] == 'passport-taxonomy-v1'
        s.schema_doc = {**s.schema_doc, 'features': [{**f, 'comparison': 'eq' if f['label']=='Напряжение' else f['comparison']} for f in s.schema_doc['features']]}
        db.commit()
    assert client.post(f'{API}/taxonomy/{sid}/approve', headers=auth, json={'freeze_version': 'engine-test'}).status_code == 200
    monkeypatch.setattr(llm, 'structured', no_model)
    monkeypatch.setattr(llm, 'unload', lambda: None)
    monkeypatch.setattr(llm, 'embed', lambda texts: [[1.,.5,.2] for t in texts])
    q = QdrantClient(':memory:'); monkeypatch.setattr(search, 'client', lambda: q)
    r = client.post(f'{API}/matching/{sid}/run', headers=auth, json={})
    assert r.status_code == 202, r.text
    tasks.run_job.run(r.json()['task_id'])
    with SessionLocal() as db:
        job = db.get(Job, r.json()['task_id']); assert job.status == 'COMPLETED', job.error
        assert db.get(ProcurementSession, sid).status == 'MATCHED'
        assert len(list(db.scalars(select(Match).where(Match.session_id == sid)))) == len(ROWS)


def test_method_validation_and_snapshot_and_multiline_review(client, auth, monkeypatch):
    sid = client.post(API+'/procurement/sessions', headers=auth, json={'title':'old API default'}).json()['id']
    url = f'{API}/procurement/sessions/{sid}/normalization-method'
    assert client.put(url, headers=auth, json={'normalization_method':'custom'}).status_code == 422
    assert client.put(url, headers=auth, json={'normalization_method':'regex'}).status_code == 200
    legacy_job = Job(params={}); assert engine.pin_job(legacy_job) == {'method':'llm'}
    job = Job(params={'normalization_config': engine.config('regex')})
    monkeypatch.setattr(engine, 'ADAPTER_SHA256', 'changed')
    with pytest.raises(ValueError, match='изменились'): engine.check_rules(job)
    parts, issues = engine.split_requirement('Кабель\nНапряжение 6000 В\n3х70')
    assert len(parts) == 1 and issues[0]['code'] == 'REGEX_REQUIREMENT_SINGLE_POSITION'
    with SessionLocal() as db:
        user = db.get(User, 1); user.role='viewer'; db.commit(); token=create_access_token(user)
    assert client.put(url, headers={'Authorization':'Bearer '+token}, json={'normalization_method':'llm'}).status_code == 403
    with SessionLocal() as db:
        org=Organization(name='Other'); db.add(org); db.flush()
        user=db.get(User,1);user.role='admin';user.organization_id=org.id;db.commit();token=create_access_token(user)
    assert client.put(url, headers={'Authorization':'Bearer '+token}, json={'normalization_method':'llm'}).status_code == 404


def test_choosing_regex_never_renormalizes_a_reused_base(client, auth, base, monkeypatch):
    source, _, _, _, old_item = base
    with SessionLocal() as db:
        original = deepcopy(db.get(Item, old_item).passport)
    r = client.post(API+'/procurement/sessions', headers=auth, json={
        'title':'Keep saved stock', 'taxonomy_enabled':True,'base_session_id': source, 'normalization_method':'regex'})
    assert r.status_code == 201
    sid=r.json()['id']
    client.post(f'{API}/procurement/sessions/{sid}/upload-requirement', headers=auth, files={'file':('need.txt', ROWS[0].encode())})
    monkeypatch.setattr(llm,'structured',no_model)
    monkeypatch.setattr('app.services.model_registry.preflight',no_model)
    run_h1(client, auth, sid, expected_status="NORMALIZED")
    with SessionLocal() as db:
        stock = db.scalar(select(Item).where(Item.session_id==sid,Item.kind=='residual',Item.row_index==2))
        assert stock.passport == {**original, 'reuse': {'session_id':source, 'item_id':old_item}}
        requirement = db.scalar(select(Item).where(Item.session_id==sid,Item.kind=='requirement'))
        assert requirement.passport['provenance']['provider']=='regex'


def test_0009_preserves_completed_passports_and_queued_job(tmp_path):
    root=Path(__file__).resolve().parents[1]; path=tmp_path/'history.db'
    env={**os.environ,'DATABASE_URL':'sqlite:///'+str(path),'DB_HOST':'','PYTHONPATH':str(root/'tests')+os.pathsep+str(root)}
    def upgrade(target):
        subprocess.run([sys.executable,'-m','alembic','upgrade',target],cwd=root,env=env,check=True,capture_output=True)
    upgrade('0008')
    seed_legacy(path, '''
from app.db import SessionLocal,Organization,User,Job
from test_expert_evaluation import populate
with SessionLocal() as db:
    o=Organization(name='Historical');db.add(o);db.flush()
    db.add(User(username='keep',password_hash='keep',role='admin',organization_id=o.id));db.commit()
    s,_,_,_=populate(db)
    db.add(Job(session_id=s.id,kind='normalize',status='QUEUED',params={'llm_config':{'model':'old-qwen'}}));db.commit()
''',root,env)
    def read():
        with sqlite3.connect(path) as db:
            names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='alembic_version'")]
            return {name:historic_rows(db,name) for name in names}
    before=read();upgrade('head');upgrade('head');after=read()
    for name,rows in before.items():
        current=after[name]
        if name=='procurement_sessions':current=[row[:-1] for row in current]
        assert current==rows
    assert not after['run_snapshots'] and not after['metric_revisions'] and not after['lab_experiments']
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT normalization_method FROM procurement_sessions').fetchall()==[('llm',)]
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]

from copy import deepcopy
from datetime import timedelta
import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from app.db import (SessionLocal, ProcurementSession, Document, Item, Job, Match, ExpertEvaluation,
                    ExpertDataset, NormalizationExperiment, User, Organization, public, now)
from app.core.security import create_access_token
from app.services.regex_experiments import run_experiment, expire_abandoned, score
from app.tasks import run_regex_experiment, dispatch_regex_experiments
from test_expert_evaluation import populate


def setup(client, auth):
    with SessionLocal() as db:
        session, reference, items, job = populate(db)
        sid = session.id
    response = client.post('/api/v1/developer/references', headers=auth,
        files={'file': ('test.json', json.dumps(reference).encode(), 'application/json')})
    assert response.status_code == 200
    rid = response.json()['id']
    client.post(f'/api/v1/developer/references/{rid}/evaluate', headers=auth, json={'session_ids': [sid]})
    return sid, rid


def saved_state(db):
    return {model.__tablename__: [deepcopy(public(row)) for row in db.scalars(select(model).order_by(model.id))]
            for model in (ProcurementSession, Document, Item, Job, Match, ExpertEvaluation, ExpertDataset)}


def start(client, auth, sid, rid, request_id=None):
    return client.post('/api/v1/developer/regex-experiments', headers=auth, json={
        'source_session_id': sid, 'dataset_id': rid, 'request_id': request_id or str(uuid4())})


def test_run_isolated_no_llm_no_history_changes_and_duplicate_delivery(client, auth, monkeypatch):
    sid, rid = setup(client, auth)
    for path in ('app.services.llm.structured', 'app.services.llm.current_config', 'app.services.model_registry.preflight'):
        monkeypatch.setattr(path, lambda *a, **k: pytest.fail('Regex called model machinery'))
    with SessionLocal() as db:
        before = saved_state(db)
    request_id = str(uuid4())
    first = start(client, auth, sid, rid, request_id)
    assert first.status_code == 202, first.text
    ident = first.json()['id']
    assert start(client, auth, sid, rid, request_id).json()['id'] == ident
    run_regex_experiment.run(ident)
    with SessionLocal() as db:
        assert saved_state(db) == before
        run = db.get(NormalizationExperiment, ident)
        assert run.status == 'COMPLETED' and run.processed_rows == 98
        assert run.result['runtime']['llm_calls'] == 0
        assert run.result['metrics']['h1_f1'] is not None
        assert run.result['metrics']['h2_leaf_accuracy'] is None
        assert run.baseline['metrics']['h1_f1'] == 1
        original = deepcopy(public(run))
        inputs = run.input_snapshot['rows']
        ref = db.get(ExpertDataset, rid).payload
        # Both evaluation paths use identical field mapping/counting.
        from app.services.expert_evaluation import calculate
        for row, prediction in zip(inputs, run.predictions):
            db.get(Item, row['source_item_id']).passport = deepcopy(prediction['passport'])
        db.flush()
        _, ordinary = calculate(db, db.get(ProcurementSession, sid), ref)
        for key in ('h1_precision', 'h1_recall', 'h1_f1', 'name_accuracy'):
            assert run.result['metrics'][key] == ordinary['metrics'][key]
        db.rollback()  # Test-only substitutions are never persisted.
    run_experiment(ident)
    with SessionLocal() as db:
        assert public(db.get(NormalizationExperiment, ident)) == original
        assert saved_state(db) == before
    listing = client.get(f'/api/v1/developer/regex-experiments?dataset_id={rid}', headers=auth).json()
    assert listing['total'] == 1 and 'predictions' not in listing['items'][0]
    passports = client.get(f'/api/v1/developer/regex-experiments/{ident}/passports?limit=20', headers=auth).json()
    assert passports['total'] == 98 and len(passports['items']) == 20
    exported = client.get(f'/api/v1/developer/regex-experiments/{ident}/export', headers=auth)
    assert len(exported.json()['predictions']) == 98
    csv = client.get(f'/api/v1/developer/regex-experiments/{ident}/export?format=csv', headers=auth)
    assert csv.status_code == 200 and 'baseline_h1_f1' in csv.text


def test_snapshot_survives_later_input_changes(client, auth):
    sid, rid = setup(client, auth)
    ident = start(client, auth, sid, rid).json()['id']
    with SessionLocal() as db:
        run = db.get(NormalizationExperiment, ident)
        first = deepcopy(run.input_snapshot['rows'][0])
        db.get(Item, first['source_item_id']).source_text = 'Изменено после создания снимка'
        db.commit()
    run_experiment(ident)
    with SessionLocal() as db:
        run = db.get(NormalizationExperiment, ident)
        assert run.input_snapshot['rows'][0] == first
        assert run.predictions[0]['passport']['name_evidence'] == first['source_text']


def test_active_source_and_wrong_dataset_rejected(client, auth):
    sid, rid = setup(client, auth)
    with SessionLocal() as db:
        job = db.scalar(select(Job)); job.status = 'RUNNING'; db.commit()
    assert start(client, auth, sid, rid).status_code == 409
    with SessionLocal() as db:
        db.scalar(select(Job)).status = 'COMPLETED'
        db.scalar(select(Document).where(Document.kind == 'residual')).sha256 = '0' * 64
        db.commit()
    assert start(client, auth, sid, rid).status_code == 409


def test_failure_and_stale_worker_do_not_rewrite_session(client, auth, monkeypatch):
    sid, rid = setup(client, auth)
    ident = start(client, auth, sid, rid).json()['id']
    with SessionLocal() as db: before = saved_state(db)
    def broken(text): raise ValueError('Controlled failure')
    monkeypatch.setattr('app.services.regex_normalization.extract', broken)
    run_experiment(ident)
    with SessionLocal() as db:
        assert saved_state(db) == before
        run = db.get(NormalizationExperiment, ident)
        assert run.status == 'FAILED' and not run.result
        run.status = 'RUNNING'; run.heartbeat_at = now() - timedelta(minutes=15); db.commit()
        expire_abandoned(db); db.commit(); db.refresh(run)
        assert run.status == 'FAILED'


def test_tenant_and_role_boundaries(client, auth):
    sid, rid = setup(client, auth)
    ident = start(client, auth, sid, rid).json()['id']
    with SessionLocal() as db:
        other = Organization(name='Other'); db.add(other); db.flush()
        user = User(username='other', password_hash='unused', role='admin', organization_id=other.id)
        db.add(user); db.commit(); token = create_access_token(user)
    headers = {'Authorization': 'Bearer '+token}
    assert client.get(f'/api/v1/developer/regex-experiments/{ident}', headers=headers).status_code == 404
    assert start(client, headers, sid, rid).status_code == 404
    with SessionLocal() as db: db.get(User, 1).role = 'buyer'; db.commit()
    assert client.get('/api/v1/developer/regex-experiments', headers=auth).status_code == 403


def test_outbox_uses_only_cpu_queue(client, auth, monkeypatch):
    sid, rid = setup(client, auth)
    ident = start(client, auth, sid, rid).json()['id']
    calls = []
    monkeypatch.setattr(run_regex_experiment, 'apply_async', lambda **kwargs: calls.append(kwargs))
    dispatch_regex_experiments()
    dispatch_regex_experiments()
    assert calls == [{'args': [ident], 'queue': 'regex_experiments'}]


def test_row_recall_does_not_replace_micro_f1():
    ref = {'features': [{'key': 'voltage_v', 'label': 'Напряжение', 'unit': 'V', 'aliases': []}], 'passports': [
        {'id': 'r1', 'kind': 'residual', 'source_sha256': 'x', 'sheet': 'S', 'row': 1,
         'source_cells': {}, 'name': 'motor', 'attributes': {'voltage_v': {'value': 380, 'reviewed': True}}},
        {'id': 'r2', 'kind': 'residual', 'source_sha256': 'x', 'sheet': 'S', 'row': 2,
         'source_cells': {}, 'name': 'motor', 'attributes': {'voltage_v': {'value': 220, 'reviewed': True}}}]}
    rows = [{'source_item_id': str(i), 'source_sha256': 'x', 'sheet': 'S', 'row': i, 'raw': {}} for i in (1,2)]
    predictions = [{'passport': {'name': 'motor', 'attributes': {'voltage_v': 380}, 'normalized_features': [{'key': 'voltage_v', 'unit': 'V'}]}}, {'passport': {}}]
    result = score(rows, predictions, ref)
    assert result['coverage']['tp'] == result['coverage']['fn'] == 1
    assert result['metrics']['full_row_recall'] == .5
    assert result['metrics']['h1_f1'] == pytest.approx(2/3)

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app import tasks
from app.core.config import settings
from app.db import SessionLocal, Job, ProcurementSession, LabExperiment, now
from app.services.job_routing import queue_for_job, prepared_base_id, TASK_ROUTES


@pytest.mark.parametrize('kind', ['prepare', 'normalize', 'taxonomy', 'match', 'full'])
def test_shared_has_one_business_queue(kind):
    assert queue_for_job(kind, mode='shared') == 'shared'
    assert queue_for_job(kind, 'prepared-base', mode='shared') == 'shared'


@pytest.mark.parametrize('kind,base,queue', [
    ('prepare', None, 'batch'), ('normalize', None, 'batch'),
    ('taxonomy', None, 'batch'), ('full', None, 'batch'),
    ('match', None, 'interactive'), ('full', 'prepared-base', 'interactive'),
    ('prepare', 'prepared-base', 'batch'), ('normalize', 'prepared-base', 'batch'),
])
def test_split_routes_by_actual_work(kind, base, queue):
    assert queue_for_job(kind, base, mode='split') == queue


def test_only_validated_prepared_base_marks_full_interactive():
    assert prepared_base_id(SimpleNamespace(base_snapshot={})) is None
    assert prepared_base_id(SimpleNamespace(base_snapshot={'source_session_id': 'source'})) is None
    assert prepared_base_id(SimpleNamespace(base_snapshot={'source_session_id': 'source', 'reused_passports': True})) == 'source'
    with pytest.raises(ValueError):
        queue_for_job('match', mode='user-supplied-queue')


def test_control_and_experiments_are_not_business_tasks():
    assert TASK_ROUTES['stockexpert.dispatch']['queue'] == 'control'
    assert TASK_ROUTES['stockexpert.laboratory']['queue'] == 'laboratory'
    assert TASK_ROUTES['stockexpert.regex_experiment']['queue'] == 'regex_experiments'
    assert TASK_ROUTES['stockexpert.b1_experiment']['queue'] == 'b1_experiments'
    assert tasks.celery_app.conf.worker_prefetch_multiplier == 1
    assert tasks.celery_app.conf.beat_schedule['dispatch-outbox']['options']['queue'] == 'control'


def test_dispatch_honors_base_and_preserves_first_publication(client, auth, monkeypatch):
    monkeypatch.setattr(settings, 'JOB_ROUTING_MODE', 'split')
    sid = client.post('/api/v1/procurement/sessions', headers=auth, json={'title': 'Routing'}).json()['id']
    with SessionLocal() as db:
        session = db.get(ProcurementSession, sid)
        session.base_snapshot = {'source_session_id': 'prepared', 'reused_passports': True}
        job = Job(session_id=sid, kind='full')
        db.add(job); db.commit(); jid = job.id
    publisher = Mock()
    monkeypatch.setattr(tasks.run_job, 'apply_async', publisher)
    tasks.dispatch.run()
    assert publisher.call_args.kwargs == {'args': [jid], 'queue': 'interactive'}
    with SessionLocal() as db:
        job = db.get(Job, jid)
        first = job.params['execution_metrics']['first_published_at']
        job.dispatched_at = now() - timedelta(minutes=11)
        db.commit()
    tasks.dispatch.run()
    with SessionLocal() as db:
        metrics = db.get(Job, jid).params['execution_metrics']
        assert metrics['first_published_at'] == first
        assert metrics['publication_count'] == 2
        assert metrics['routing_mode'] == 'split'


def test_failed_send_does_not_claim_publication(client, auth, monkeypatch):
    sid = client.post('/api/v1/procurement/sessions', headers=auth, json={'title': 'Send failure'}).json()['id']
    with SessionLocal() as db:
        job = Job(session_id=sid, kind='match'); db.add(job); db.commit(); jid = job.id
    monkeypatch.setattr(tasks.run_job, 'apply_async', Mock(side_effect=ConnectionError('broker unavailable')))
    with pytest.raises(ConnectionError):
        tasks.dispatch.run()
    with SessionLocal() as db:
        job = db.get(Job, jid)
        assert job.dispatched_at is None
        assert 'execution_metrics' not in job.params


def test_dispatch_publishes_in_registration_order(client, auth, monkeypatch):
    sid = client.post('/api/v1/procurement/sessions', headers=auth, json={'title': 'FIFO'}).json()['id']
    stamp = now()
    with SessionLocal() as db:
        newer = Job(session_id=sid, kind='match', created_at=stamp)
        older = Job(session_id=sid, kind='normalize', created_at=stamp - timedelta(seconds=1))
        db.add_all([newer, older]); db.commit()
        expected = [older.id, newer.id]
    publisher = Mock()
    monkeypatch.setattr(tasks.run_job, 'apply_async', publisher)
    tasks.dispatch.run()
    assert [call.kwargs['args'][0] for call in publisher.call_args_list] == expected


def test_laboratory_dispatch_never_uses_interactive_queue(client, auth, monkeypatch):
    sid = client.post('/api/v1/procurement/sessions', headers=auth, json={'title': 'Lab route'}).json()['id']
    with SessionLocal() as db:
        job = Job(session_id=sid, kind='match', status='COMPLETED'); db.add(job); db.flush()
        session = db.get(ProcurementSession, sid)
        experiment = LabExperiment(session_id=sid, job_id=job.id, requested_by=session.created_by,
                                   source='manual', payload={})
        db.add(experiment); db.commit(); ident = experiment.id
    publisher = Mock()
    monkeypatch.setattr(tasks.run_laboratory, 'apply_async', publisher)
    tasks.dispatch_laboratory()
    assert publisher.call_args.kwargs == {'args': [ident], 'queue': 'laboratory'}

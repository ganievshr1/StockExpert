"""Controller tests use controlled HTTP responses; these are not B1 measurements."""
import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy import select

from app import tasks
from app.core.config import settings
from app.db import Item, Job, Match, Organization, ProcurementSession, RunSnapshot, SessionLocal, User
from app.services import b1_experiments as service
from test_b1_experiments_api import experiment_source
from test_reusable_bases import base


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'B1_ARTIFACT_DIR', str(tmp_path))
    monkeypatch.setattr(settings, 'JOB_ROUTING_MODE', 'split')
    monkeypatch.setattr(settings, 'B1_API_BASE_URL', 'http://test/api/v1')
    ident = str(uuid.uuid4())
    with SessionLocal() as db:
        org = Organization(name='B1 runner test'); db.add(org); db.flush()
        users = [User(username=f'test-b1-{i}', organization_id=org.id,
                      password_hash='unused', role='buyer') for i in range(10)]
        db.add_all(users); db.flush()
        sessions = [ProcurementSession(organization_id=org.id, created_by=users[i % 10].id,
            title=f'Slot {i}', base_snapshot={'b1_experiment_id': ident}) for i in range(51)]
        db.add_all(sessions); db.commit()
        value = dict(id=ident, title='Runner test', scope='confirmation', scenario='confirmation',
            status='RUNNING', created_at=service.utc(), organization_id=org.id,
            requested_by=users[0].id, actors=[u.id for u in users],
            pool=[dict(session_id=s.id, user_id=users[i % 10].id, account=i % 10,
                       ordinal=i, busy=False) for i, s in enumerate(sessions[:50])],
            background_pool=[sessions[50].id], base_session_id=sessions[0].id,
            requirement_session_id=sessions[1].id, background_session_id=sessions[50].id,
            cancel_requested=False, current_run=None,
            plan=[dict(index=0, pair_index=1, routing_mode='split', status='PENDING')])
        _, value['input_sha256'] = service.input_manifest(db, value)
    service.save(ident, initial=value)
    return ident, value


def run_metadata():
    return dict(index=0, pair_index=1, routing_mode='split', attempt_id=uuid.uuid4().hex)


def test_summary_uses_sent_denominator_and_nearest_rank():
    rows = [dict(outcome='COMPLETED', total_seconds=t, start_lag_ms=1) for t in range(1, 20)]
    rows += [dict(outcome='FAILED', total_seconds=99), dict(outcome='GENERATOR_MISSED')]
    result = service.summarize(rows)
    assert (result['scheduled'], result['started'], result['success'], result['missed']) == (21, 20, 19, 1)
    assert result['p95_seconds'] == 19
    assert result['error_rate'] == .05
    assert result['success_within_seconds']['5'] == 5 / 20
    assert result['success_fraction_denominator'] == 'sent'
    assert result['valid'] is False
    assert service.summarize([])['success_within_seconds']['5'] is None


@pytest.mark.parametrize('terminal,body,outcome', [
    ('COMPLETED', {'total': 0, 'items': []}, 'COMPLETED'),
    ('FAILED', None, 'FAILED'), ('CANCELLED', None, 'CANCELLED'),
    ('COMPLETED', {'items': []}, 'RESULT_CONTRACT_ERROR'),
    ('COMPLETED', [], 'RESULT_CONTRACT_ERROR'),
    ('COMPLETED', {'total': 1, 'items': [{'job_id': 'different'}]}, 'RESULT_JOB_MISMATCH'),
])
def test_http_result_contract_and_terminal_errors(runtime, terminal, body, outcome):
    ident, value = runtime
    slot = {**value['pool'][0], 'busy': True}
    paths = []
    def response(request):
        paths.append(request.url.path)
        if request.method == 'POST':
            assert request.headers['X-Test-Run-ID'] == ident
            return httpx.Response(202, json={'task_id': 'job-one'})
        if request.url.path.endswith('/status'):
            return httpx.Response(200, json={'id': 'job-one', 'status': terminal})
        return httpx.Response(200, json=body)
    async def invoke():
        async with httpx.AsyncClient(base_url='http://test/api/v1/', transport=httpx.MockTransport(response)) as client:
            return await service.request_job(client, slot, 0, run_metadata(), time.monotonic(), ident, 'MEASURING')
    result = asyncio.run(invoke())
    assert result['outcome'] == outcome
    assert all(path.startswith('/api/v1/matching/') for path in paths)
    assert any(path.endswith('/candidates') for path in paths) == (terminal == 'COMPLETED')
    assert slot['busy'] is False
    raw = json.loads((service.directory(ident) / 'raw.jsonl').read_text())
    assert raw['outcome'] == outcome
    assert 'Authorization' not in json.dumps(raw)


def test_result_clock_excludes_artifact_io(runtime, monkeypatch):
    ident, value = runtime
    monkeypatch.setitem(service.PROTOCOL, 'timeout_seconds', .04)
    original = service.append
    def slow_append(ident, name, value):
        if name == 'results.jsonl':
            time.sleep(.08)
        original(ident, name, value)
    monkeypatch.setattr(service, 'append', slow_append)
    def response(request):
        if request.method == 'POST': return httpx.Response(202, json={'task_id': 'one'})
        if request.url.path.endswith('/status'): return httpx.Response(200, json={'id': 'one', 'status': 'COMPLETED'})
        return httpx.Response(200, json={'total': 0, 'items': []})
    async def invoke():
        async with httpx.AsyncClient(base_url='http://test/api/v1/', transport=httpx.MockTransport(response)) as client:
            return await service.request_job(client, value['pool'][0], 0, run_metadata(), time.monotonic(), ident, 'MEASURING')
    start = time.monotonic(); result = asyncio.run(invoke())
    assert time.monotonic() - start >= .08
    assert result['outcome'] == 'COMPLETED'
    assert result['total_seconds'] < .04


def test_timeout_keeps_busy_slot_until_server_stops(runtime, monkeypatch):
    ident, value = runtime; slot = value['pool'][0]
    with SessionLocal() as db:
        job = Job(session_id=slot['session_id'], kind='match', status='RUNNING')
        db.add(job); db.commit(); jid = job.id
    monkeypatch.setitem(service.PROTOCOL, 'timeout_seconds', .015)
    monkeypatch.setitem(service.PROTOCOL, 'poll_seconds', .005)
    def response(request):
        return httpx.Response(202, json={'task_id': jid}) if request.method == 'POST' else httpx.Response(200, json={'id': jid, 'status': 'RUNNING'})
    async def invoke():
        async with httpx.AsyncClient(base_url='http://test/api/v1/', transport=httpx.MockTransport(response)) as client:
            return await service.request_job(client, slot, 0, run_metadata(), time.monotonic(), ident, 'MEASURING')
    result = asyncio.run(invoke())
    assert result['outcome'] == 'TIMEOUT'
    assert slot['busy'] and slot['awaiting_server']


def short_protocol(monkeypatch):
    for key, value in dict(warmup_seconds=.04, duration_seconds=.08,
                           requests_per_run=4, rate_per_minute=3000).items():
        monkeypatch.setitem(service.PROTOCOL, key, value)
    monkeypatch.setattr(service, 'MONITOR_SECONDS', .005)
    monkeypatch.setattr(service, 'ensure_background', Mock())
    monkeypatch.setattr(service, 'cancel_background', Mock())
    calls = []
    def snapshot(*args):
        calls.append(time.monotonic())
        return len(calls), 1
    monkeypatch.setattr(service, 'background_snapshot', snapshot)


def test_open_arrivals_do_not_wait_for_warmup_or_previous_completion(runtime, monkeypatch):
    ident, _ = runtime; short_protocol(monkeypatch)
    started, ended = [], []
    async def request(client, slot, index, run, scheduled, ident, phase):
        started.append((phase, index, time.monotonic(), scheduled, slot['session_id'], id(client)))
        await asyncio.sleep(.15)
        ended.append((phase, time.monotonic()))
        slot['busy'] = False
        return dict(phase=phase, outcome='COMPLETED', total_seconds=.15, start_lag_ms=1)
    monkeypatch.setattr(service, 'request_job', request)
    result = asyncio.run(service.execute_run(ident, run_metadata()))
    measured = [row for row in started if row[0] == 'MEASURING']
    assert len(started) == 6 and len(measured) == 4
    assert measured[0][2] < min(stamp for phase, stamp in ended if phase == 'WARMUP')
    assert measured[-1][2] - measured[0][2] < .10
    assert measured[0][4] != started[0][4]  # same account, another prepared slot
    assert measured[0][5] == started[0][5]  # per-account connection pool
    assert measured[1][5] != measured[0][5]  # isolated cookies/credentials
    assert result['scheduled'] == 4 and result['success'] == 4
    assert result['measurement_seconds'] < .13
    assert result['scenario_makespan_seconds'] > .20
    assert result['valid'] is True
    service.ensure_background.assert_called_once_with(ident)


def test_pool_exhaustion_is_recorded_not_backpressured(runtime, monkeypatch):
    ident, value = runtime; short_protocol(monkeypatch)
    for slot in value['pool']: slot['busy'] = True
    service.save(ident, {'pool': value['pool']})
    request = Mock(); monkeypatch.setattr(service, 'request_job', request)
    result = asyncio.run(service.execute_run(ident, run_metadata()))
    assert result['missed'] == 4 and result['started'] == 0
    assert result['valid'] is False
    assert result['measurement_seconds'] < .15
    request.assert_not_called()
    raw = [json.loads(line) for line in (service.directory(ident) / 'raw.jsonl').read_text().splitlines()]
    assert len(raw) == 6 and {row['reason'] for row in raw} == {'pool_exhausted'}


def test_finished_timeout_slots_are_reclaimed_by_monitor(runtime, monkeypatch):
    ident, value = runtime; short_protocol(monkeypatch)
    for slot in value['pool']: slot.update(busy=True, awaiting_server=True)
    service.save(ident, {'pool': value['pool']})
    async def request(client, slot, index, run, scheduled, ident, phase):
        slot['busy'] = False
        return dict(phase=phase, outcome='COMPLETED', total_seconds=.001, start_lag_ms=1)
    monkeypatch.setattr(service, 'request_job', request)
    result = asyncio.run(service.execute_run(ident, run_metadata()))
    assert result['started'] == 4 and result['missed'] == 0


def test_background_ending_early_invalidates_full_protocol(runtime, monkeypatch):
    ident, _ = runtime; short_protocol(monkeypatch)
    monkeypatch.setattr(service, 'background_snapshot', lambda *args: (0, 0))
    async def request(client, slot, index, run, scheduled, ident, phase):
        slot['busy'] = False
        return dict(phase=phase, outcome='COMPLETED', total_seconds=.001, start_lag_ms=1)
    monkeypatch.setattr(service, 'request_job', request)
    result = asyncio.run(service.execute_run(ident, run_metadata()))
    assert not result['valid'] and result['background_completed_early']
    service.ensure_background.assert_called_once()


def test_lease_loss_stops_new_arrivals_and_fixed_window_wait(runtime, monkeypatch):
    ident, _ = runtime; short_protocol(monkeypatch)
    monkeypatch.setitem(service.PROTOCOL, 'warmup_seconds', 0)
    monkeypatch.setitem(service.PROTOCOL, 'requests_per_run', 200)
    stop = False
    async def request(client, slot, index, run, scheduled, ident, phase):
        nonlocal stop
        stop = True; slot['busy'] = False
        return dict(phase=phase, outcome='COMPLETED', total_seconds=.001, start_lag_ms=1)
    monkeypatch.setattr(service, 'request_job', request)
    start = time.monotonic()
    result = asyncio.run(service.execute_run(ident, run_metadata(), should_stop=lambda: stop))
    assert time.monotonic() - start < 1
    assert result['scheduled'] == 1 and result['valid'] is False


def test_sequential_diagnostic_uses_ten_distinct_requirements(runtime, monkeypatch):
    ident, value = runtime
    for index, slot in enumerate(value['pool'][:10]):
        slot.update(account=0, user_id=value['actors'][0], ordinal=index)
    service.save(ident, dict(scope='diagnostic', scenario='sequential_10',
                            actors=value['actors'][:1], pool=value['pool'][:10], background_pool=[]))
    seen, active = [], 0
    async def request(client, slot, index, run, scheduled, ident, phase):
        nonlocal active
        active += 1; assert active == 1
        seen.append(slot['session_id']); await asyncio.sleep(.001)
        active -= 1; slot['busy'] = False
        return dict(phase=phase, outcome='COMPLETED', total_seconds=.001, start_lag_ms=1)
    monkeypatch.setattr(service, 'request_job', request)
    result = asyncio.run(service.execute_run(ident, run_metadata()))
    assert len(set(seen)) == 10
    assert result['scope'] == 'diagnostic' and result['confirmed'] is False
    assert result['scenario_makespan_seconds'] >= .01


def test_stale_controller_is_retryable_but_cannot_overwrite_new_controller(runtime):
    ident, value = runtime
    old = (datetime.now(timezone.utc) - timedelta(seconds=121)).isoformat()
    service.save(ident, dict(heartbeat_at=old, controller_token='old'))
    service.recover_stale()
    assert service.load(ident)['status'] == 'INTERRUPTED'
    assert service.active_runs() == []
    service.save(ident, dict(status='RUNNING', controller_token='new'))
    assert service.save(ident, dict(status='COMPLETED'), expected_controller='old') is None
    assert service.load(ident)['status'] == 'RUNNING'


@pytest.mark.parametrize('allocation,passes', [
    ({'a': 'interactive', 'b': 'batch'}, True),
    ({'a': 'shared', 'b': 'shared'}, False),
    ({'a': 'interactive', 'b': 'batch', 'extra': 'batch'}, False),
])
def test_preflight_checks_actual_worker_budget(runtime, monkeypatch, allocation, passes):
    ident, _ = runtime
    inspector = SimpleNamespace(
        active_queues=lambda: {name: [{'name': queue}] for name, queue in allocation.items()},
        stats=lambda: {name: {'pool': {'max-concurrency': 1}} for name in allocation})
    monkeypatch.setattr(tasks.celery_app.control, 'inspect', lambda **kwargs: inspector)
    if passes:
        assert len(service.preflight(ident)['actual_workers']) == 2
    else:
        with pytest.raises(ValueError, match='подписки'):
            service.preflight(ident)


def test_campaign_cannot_confirm_with_biased_baseline_failures():
    plan = []
    for pair in range(1, 6):
        for routing in ('shared', 'split'):
            summary = dict(valid=True, p95_seconds=10 if routing == 'shared' else 7,
                           batch_rows_per_minute=100, error_rate=0, success=600)
            plan.append(dict(pair_index=pair, routing_mode=routing, status='COMPLETED', summary=summary))
    assert service.campaign_summary(plan)['confirmed'] is True
    plan[0]['summary'].update(error_rate=.5, success=300)
    assert service.campaign_summary(plan)['confirmed'] is False


def test_prepared_ten_users_execute_real_api_and_pipeline_with_test_model_boundary(
    client, auth, experiment_source, monkeypatch,
):
    """Real SQL/API/worker code, tiny fixture and controlled model/vector dependencies."""
    from app.main import app
    source = experiment_source
    response = client.post('/api/v1/developer/b1/experiments', headers=auth, json=dict(
        scenario='concurrent_10', base_session_id=source['base_id'],
        requirement_session_id=source['requirements_id']))
    assert response.status_code == 202
    ident = response.json()['id']
    service.prepare_pool(ident)
    value = service.load(ident)
    assert len(value['actors']) == len(set(value['actors'])) == 10
    assert len(value['pool']) == len({row['session_id'] for row in value['pool']}) == 10
    with SessionLocal() as db:
        source_stock = db.scalar(select(Item.stock_id).where(Item.session_id == source['base_id'], Item.kind == 'residual', Item.row_index == 2))
        copies = list(db.scalars(select(Item).where(Item.session_id.in_([row['session_id'] for row in value['pool']]), Item.kind == 'residual', Item.row_index == 2)))
        assert len(copies) == 10
        assert len({row.stock_id for row in copies}) == 1
        assert copies[0].stock_id != source_stock
        needs = list(db.scalars(select(Item.source_text).where(Item.session_id.in_([row['session_id'] for row in value['pool']]), Item.kind == 'requirement')))
        assert len(set(needs)) == 10
    real_client = httpx.AsyncClient
    monkeypatch.setattr(service.httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.ASGITransport(app=app), **kw))
    monkeypatch.setitem(service.PROTOCOL, 'poll_seconds', .005)
    def pending_job():
        with SessionLocal() as db:
            return db.scalar(select(Job.id).where(Job.status == 'QUEUED').order_by(Job.created_at).limit(1))
    async def invoke():
        done = asyncio.Event()
        async def worker():
            while not done.is_set():
                jid = await asyncio.to_thread(pending_job)
                if jid:
                    await asyncio.to_thread(tasks.run_job.run, jid)
                else:
                    await asyncio.sleep(.002)
        worker_task = asyncio.create_task(worker())
        try:
            return await service.execute_run(ident, run_metadata())
        finally:
            done.set(); await worker_task
    result = asyncio.run(invoke())
    raw = [json.loads(row) for row in (service.directory(ident) / 'raw.jsonl').read_text().splitlines()]
    assert result['success'] == 10, raw
    assert result['scope'] == 'diagnostic' and result['confirmed'] is False
    assert all(row['result_count'] == 1 and row['result_sha256'] for row in raw)
    assert len({row['account'] for row in raw}) == 10
    service.clean_between_runs(ident)
    with SessionLocal() as db:
        assert not list(db.scalars(select(Match)))
        assert all(db.get(RunSnapshot, row['job_id']).payload['b1_archived'] for row in raw)
        assert db.get(ProcurementSession, source['base_id']).approved
        assert db.scalar(select(Item.stock_id).where(Item.session_id == source['base_id'], Item.row_index == 2)) == source_stock
    evidence = [json.loads(line) for line in (service.directory(ident) / 'cleanup.jsonl').read_text().splitlines()]
    assert len(evidence) == 10 and all(row['status'] == 'COMPLETED' for row in evidence)

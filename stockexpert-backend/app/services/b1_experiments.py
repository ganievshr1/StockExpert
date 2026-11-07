"""Real B1 load experiments. Persistent artifacts; no synthetic timing or model output."""
from __future__ import annotations

import asyncio
import csv
import fcntl
import hashlib
import io
import json
import math
import os
import secrets
import time
import uuid
import zipfile
from contextlib import AsyncExitStack, contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Thread

import httpx
from fastapi import HTTPException
from redis import Redis
from sqlalchemy import delete, func, select, update

from app.core.config import settings
from app.core.security import create_access_token, hash_password
from app.db import Document, Item, Job, Match, ProcurementSession, RunSnapshot, SessionLocal, User, StockPosition, LabExperiment, NormalizationExperiment
from app.services.execution import ACTIVE_STATUSES

PROTOCOL = dict(pairs=5, warmup_seconds=180, duration_seconds=1800, rate_per_minute=20,
                requests_per_run=600, poll_seconds=.25, timeout_seconds=120,
                max_start_lag_ms=100, pool_per_user=5)
TERMINAL = {'COMPLETED', 'FAILED', 'CANCELLED'}
ACTIVE = {'QUEUED', 'PREPARING', 'RUNNING', 'CANCEL_REQUESTED'}
MONITOR_SECONDS = 1


def utc():
    return datetime.now(timezone.utc).isoformat()


def mode():
    return getattr(settings, 'JOB_ROUTING_MODE', os.getenv('JOB_ROUTING_MODE', 'split'))


def root():
    path = Path(getattr(settings, 'B1_ARTIFACT_DIR', os.getenv('B1_ARTIFACT_DIR', '/data/b1')))
    path.mkdir(parents=True, exist_ok=True)
    return path


def directory(ident):
    return root() / str(uuid.UUID(ident))


def load(ident):
    try:
        return json.loads((directory(ident) / 'state.json').read_text())
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, 'Эксперимент B1 не найден') from None


def save(ident, patch=None, initial=None, expected_controller=None, expected_updated_at=None):
    folder = directory(ident)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / 'state.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        value = initial if initial is not None else load(ident)
        if expected_controller and value.get('controller_token') != expected_controller:
            return None
        if expected_updated_at and value.get('updated_at') != expected_updated_at:
            return None
        value.update(patch or {})
        value['updated_at'] = utc()
        temporary = folder / ('state.' + uuid.uuid4().hex + '.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
        os.replace(temporary, folder / 'state.json')
        return value


def append(ident, filename, value):
    with (directory(ident) / filename).open('a', encoding='utf-8') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps(value, ensure_ascii=False, default=str) + '\n')


def owned(ident, user):
    recover_stale()
    value = load(ident)
    if value['organization_id'] != user.organization_id:
        raise HTTPException(404, 'Эксперимент B1 не найден')
    return value


def read_public(value):
    return {key: val for key, val in value.items() if key not in {'actors', 'pool', 'background_pool', 'controller_token'}}


def list_experiments(user):
    recover_stale()
    values = []
    for path in root().glob('*/state.json'):
        try:
            value = json.loads(path.read_text())
            if value['organization_id'] == user.organization_id:
                values.append(read_public(value))
        except (ValueError, KeyError, OSError):
            continue
    return sorted(values, key=lambda item: item['created_at'], reverse=True)[:100]


def active_runs():
    recover_stale()
    values = []
    for path in root().glob('*/state.json'):
        try:
            value = json.loads(path.read_text())
            if value['status'] in ACTIVE:
                values.append({'id': value['id'], 'status': value['status']})
        except (ValueError, KeyError, OSError):
            continue
    return values


def recover_stale():
    for path in root().glob('*/state.json'):
        try:
            value = json.loads(path.read_text())
            stamp = value.get('heartbeat_at') or value['updated_at']
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds()
            if value['status'] in ('PREPARING', 'RUNNING', 'CANCEL_REQUESTED') and age > 120:
                append(value['id'], 'controller-events.jsonl', {'at': utc(), 'event': 'INTERRUPTED',
                       'current_run': value.get('current_run'), 'reason': 'stale heartbeat'})
                save(value['id'], dict(status='INTERRUPTED', cancel_requested=True, controller_token=None,
                    error='Контроллер не отвечает более 120 секунд. Прерванный запуск не засчитан. Отмените оставшиеся задания, дождитесь завершения и повторите Продолжить.'), expected_updated_at=value['updated_at'])
        except (ValueError, KeyError, OSError):
            continue


def residual_rows(db, session):
    mappings = {(row['document_id'], row['sheet']): row for row in session.schema_doc.get('mappings', [])}
    return [row for row in db.scalars(select(Item).where(Item.session_id == session.id, Item.kind == 'residual'))
            if (row.document_id, row.sheet) in mappings and row.row_index > mappings[row.document_id, row.sheet]['header_row']
            and row.source_text.strip()]


def input_manifest(db, value):
    rows = []
    for sid in dict.fromkeys([value['base_session_id'], value['requirement_session_id'], value.get('background_session_id')]):
        if not sid:
            continue
        session = db.get(ProcurementSession, sid)
        if session is None:
            raise ValueError('Исходная сессия удалена во время эксперимента')
        items = list(db.scalars(select(Item).where(Item.session_id == sid).order_by(Item.id)))
        rows.append({'session_id': sid, 'schema': session.schema_doc, 'approved': session.approved,
            'normalization_method': session.normalization_method, 'llm_config': session.llm_config,
            'items': [{'id': row.id, 'kind': row.kind, 'source_text': row.source_text, 'raw': row.raw,
                       'passport': row.passport, 'category': row.category,
                       'stock_state': db.get(StockPosition, row.stock_id).state if row.stock_id and db.get(StockPosition, row.stock_id) else None}
                      for row in items]})
    encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str).encode()
    return rows, hashlib.sha256(encoded).hexdigest()


def normalized_requirements(db, sid):
    return list(db.scalars(select(Item).where(Item.session_id == sid, Item.kind == 'requirement',
        Item.passport['attributes'].as_string().is_not(None)).order_by(Item.document_id, Item.row_index, Item.id)))


def session_options(db, user):
    from app.api.bases import bases
    sessions = list(db.scalars(select(ProcurementSession).where(
        ProcurementSession.organization_id == user.organization_id).order_by(ProcurementSession.created_at.desc())))
    requirements, backgrounds = [], []
    for session in sessions:
        if session.base_snapshot.get('b1_experiment_id'):
            continue
        count = len(normalized_requirements(db, session.id))
        if count >= 10 and session.approved:
            requirements.append(dict(session_id=session.id, title=session.title, normalized_requirements=count))
        residual = db.scalar(select(func.count()).select_from(Item).where(Item.session_id == session.id, Item.kind == 'residual')) or 0
        if residual and (session.approved or session.schema_doc.get('mapping_approved')):
            backgrounds.append(dict(session_id=session.id, title=session.title, residual_rows=residual))
    excluded = {session.id for session in sessions if session.base_snapshot.get('b1_experiment_id')}
    return dict(routing_mode=mode(), ready_bases=[row for row in bases(200, 0, db, user) if row['session_id'] not in excluded],
                requirement_sessions=requirements, background_sessions=backgrounds, protocol=PROTOCOL,
                note='Короткие сценарии диагностические. Полный протокол требует пяти пар shared/split и отдельной фоновой подготовки. Режим очередей меняется через развёртывание.')


def schema_signature(session):
    schema = session.schema_doc
    value = {'features': schema.get('features'), 'categories': [{key: row.get(key) for key in
             ('id', 'name', 'parent_id', 'feature_keys')} for row in schema.get('categories', [])]}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate_inputs(db, user, payload):
    from app.api.deps import own_session
    from app.services.bases import normalized_rows
    base = own_session(db, payload['base_session_id'], user)
    source = own_session(db, payload['requirement_session_id'], user)
    if payload.get('background_session_id'):
        own_session(db, payload['background_session_id'], user)
    if not base.approved or db.scalar(select(Job.id).where(Job.session_id == base.id, Job.status.in_(ACTIVE_STATUSES)).limit(1)):
        raise HTTPException(409, 'База должна быть утверждена и полностью готова')
    _, eligible = normalized_rows(db, base)
    available = []
    for item in eligible:
        stock = db.get(StockPosition, item.stock_id) if item.stock_id else None
        if not stock or stock.state == 'AVAILABLE':
            available.append(item)
    eligible = available
    for source_id in (base.id, source.id, payload.get('background_session_id')):
        if source_id and db.scalar(select(Job.id).where(Job.session_id == source_id, Job.status.in_(ACTIVE_STATUSES)).limit(1)):
            raise HTTPException(409, 'Дождитесь завершения заданий в исходных сессиях')
    if payload['scenario'] == 'confirmation' and len(eligible) not in (12000, 12533):
        raise HTTPException(422, 'Для полного протокола нужна готовая база ровно на 12 000 или 12 533 позиции')
    requirements = normalized_requirements(db, source.id)
    if len(requirements) < 10 or not source.approved:
        raise HTTPException(422, 'Нужна утверждённая сессия с не менее чем десятью готовыми паспортами потребностей')
    if schema_signature(base) != schema_signature(source):
        raise HTTPException(422, 'Паспорта потребностей должны использовать ту же схему и категории, что выбранная база')
    if payload['scenario'] == 'confirmation':
        background_id = payload.get('background_session_id')
        if not background_id or background_id in (base.id, source.id):
            raise HTTPException(422, 'Выберите отдельную сессию как шаблон фоновой подготовки каталога')
        background = own_session(db, background_id, user)
        if not (background.approved or background.schema_doc.get('mapping_approved')):
            raise HTTPException(422, 'У фонового шаблона должно быть утверждено сопоставление столбцов')
        if not db.scalar(select(Document.id).where(Document.session_id == background_id, Document.kind == 'residual').limit(1)):
            raise HTTPException(422, 'В фоновом шаблоне нет Excel каталога')
        if len(residual_rows(db, background)) != len(eligible):
            raise HTTPException(422, 'Фоновый каталог должен содержать столько же строк, сколько выбранная готовая база')
    return base, requirements[:10], len(eligible)


def create(db, user, payload):
    base, requirements, count = validate_inputs(db, user, payload)
    ident = str(uuid.uuid4())
    confirmation = payload['scenario'] == 'confirmation'
    plan = []
    for pair in range(1, 6) if confirmation else [1]:
        profiles = (['shared', 'split'] if pair % 2 else ['split', 'shared']) if confirmation else [mode()]
        for routing in profiles:
            plan.append(dict(index=len(plan), pair_index=pair, routing_mode=routing, status='PENDING'))
    value = dict(id=ident, title=payload.get('campaign_title') or 'B1 ' + utc()[:16],
                 organization_id=user.organization_id, requested_by=user.id, scenario=payload['scenario'],
                 scope='confirmation' if confirmation else 'diagnostic', status='AWAITING_PROFILE',
                 progress=0, created_at=utc(), error=None, current_run=None, plan=plan, summary={},
                 expected_routing_mode=plan[0]['routing_mode'], base_rows=count,
                 base_session_id=base.id, requirement_session_id=payload['requirement_session_id'],
                 requirement_ids=[row.id for row in requirements],
                 background_session_id=payload.get('background_session_id'),
                 schema_sha256=schema_signature(base), pool=[], actors=[], background_pool=[], cancel_requested=False,
                 protocol=PROTOCOL, measurement_kind='real_http_and_real_pipeline',
                 note='Диагностический сценарий не подтверждает B1' if not confirmation else
                 'Пять пар запусков. Между режимами оператор меняет профиль workers и нажимает Продолжить. Подготовка пула не входит в измерение.')
    save(ident, initial=value)
    if mode() == plan[0]['routing_mode']:
        queue(ident)
    return read_public(load(ident))


def queue(ident):
    recover_stale()
    value = load(ident)
    stale_queued = value['status'] == 'QUEUED' and (datetime.now(timezone.utc) - datetime.fromisoformat(value['updated_at'])).total_seconds() > 120
    if value['status'] in ACTIVE and not stale_queued:
        raise HTTPException(409, 'Эксперимент уже выполняется')
    pending = next((row for row in value['plan'] if row['status'] == 'PENDING'), None)
    if not pending:
        raise HTTPException(409, 'В плане нет следующих запусков')
    if mode() != pending['routing_mode']:
        raise HTTPException(409, f"Нужен профиль {pending['routing_mode']}; сейчас {mode()}. Переключите workers через Docker Compose")
    if save(ident, dict(status='QUEUED', error=None, cancel_requested=False,
                      expected_routing_mode=pending['routing_mode']), expected_updated_at=value['updated_at']) is None:
        raise HTTPException(409, 'Состояние эксперимента изменилось; обновите страницу')
    try:
        from app.tasks import celery_app
        result = celery_app.send_task('stockexpert.b1_experiment', args=[ident], queue='b1_experiments')
        save(ident, dict(controller_task_id=result.id))
    except Exception:
        save(ident, dict(status='AWAITING_PROFILE', error='Не удалось передать запуск контроллеру. Проверьте Redis и worker-b1; затем повторите Продолжить'))
        raise HTTPException(503, 'Контроллер B1 недоступен; эксперимент сохранён, запуск можно повторить') from None


def cancel(ident):
    value = load(ident)
    save(ident, dict(cancel_requested=True, status='CANCEL_REQUESTED' if value['status'] in ('PREPARING', 'RUNNING', 'CANCEL_REQUESTED') else 'CANCELLED'))
    with SessionLocal() as db:
        ids = [row['session_id'] for row in value['pool']] + value['background_pool']
        for job in db.scalars(select(Job).where(Job.session_id.in_(ids), Job.status.in_(ACTIVE_STATUSES))):
            job.status = 'CANCELLED' if job.status == 'QUEUED' else 'CANCEL_REQUESTED'
        db.commit()
    return read_public(load(ident))


def clone_requirements(db, target, source_item, ident):
    from app.services import storage
    source_document = db.get(Document, source_item.document_id)
    key = f'{target.organization_id}/{target.id}/{uuid.uuid4()}.txt'
    storage.put(key, source_item.source_text.encode())
    doc = Document(session_id=target.id, kind='requirement', filename='b1-requirement.txt', object_key=key,
                   sha256=hashlib.sha256(source_item.source_text.encode()).hexdigest(), meta={'text': source_item.source_text, 'b1_experiment_id': ident})
    db.add(doc); db.flush()
    db.add(Item(session_id=target.id, document_id=doc.id, kind='requirement', sheet='', row_index=1,
                raw=deepcopy(source_item.raw), source_text=source_item.source_text,
                passport=deepcopy(source_item.passport), category=source_item.category, issues=deepcopy(source_item.issues)))


def clone_background(db, source, user, ident):
    from app.services import storage
    target = ProcurementSession(organization_id=user.organization_id, created_by=user.id,
        title=f'B1 {ident[:8]} фоновая подготовка', normalization_method=source.normalization_method,
        taxonomy_enabled=source.taxonomy_enabled, llm_config=deepcopy(source.llm_config),
        approved=source.approved, taxonomy_version=source.taxonomy_version, status='APPROVED',
        base_snapshot={'b1_experiment_id': ident, 'b1_role': 'background'})
    db.add(target); db.flush()
    documents = list(db.scalars(select(Document).where(Document.session_id == source.id, Document.kind == 'residual')))
    mapping = {}
    for old in documents:
        key = f'{target.organization_id}/{target.id}/{uuid.uuid4()}{Path(old.filename).suffix}'
        storage.put(key, storage.get(old.object_key))
        document = Document(session_id=target.id, kind='residual', filename=old.filename, object_key=key,
                            sha256=old.sha256, meta=deepcopy(old.meta))
        db.add(document); db.flush(); mapping[old.id] = document.id
    schema = deepcopy(source.schema_doc)
    schema['mappings'] = [{**row, 'document_id': mapping[row['document_id']]} for row in schema.get('mappings', []) if row['document_id'] in mapping]
    target.schema_doc = schema
    for old in db.scalars(select(Item).where(Item.session_id == source.id, Item.kind == 'residual')):
        db.add(Item(session_id=target.id, document_id=mapping[old.document_id], kind='residual', sheet=old.sheet,
                    row_index=old.row_index, raw=deepcopy(old.raw), source_text=old.source_text,
                    passport=None, category=None, issues=[]))
    db.commit()
    return target.id


def prepare_pool(ident, should_stop=None):
    from app.services.bases import attach_base
    value = load(ident)
    count = 50 if value['scope'] == 'confirmation' else 10
    if len(value['pool']) == count and value.get('input_sha256') and (value['scope'] != 'confirmation' or value['background_pool']):
        return
    with SessionLocal() as db:
        admin = db.get(User, value['requested_by'])
        base = db.get(ProcurementSession, value['base_session_id'])
        users = 1 if value['scenario'] == 'sequential_10' else 10
        actors = list(value['actors'])
        for index in range(len(actors), users):
            actor = User(username=f'b1_{ident.replace("-", "")}_{index}',
                         password_hash=hash_password(secrets.token_urlsafe(32)), role='buyer',
                         organization_id=admin.organization_id, is_active=True)
            db.add(actor); db.flush(); actors.append(actor.id)
        db.commit(); save(ident, dict(actors=actors))
        pool = list(value['pool'])
        stock_map_path = directory(ident) / 'inventory-map.json'
        stock_map = json.loads(stock_map_path.read_text()) if stock_map_path.exists() else {}
        for index in range(len(pool), count):
            if load(ident).get('cancel_requested') or (should_stop and should_stop()):
                raise RuntimeError('Эксперимент отменён во время подготовки')
            account = index % users
            requirement_index = index if users == 1 else account
            target = ProcurementSession(organization_id=admin.organization_id, created_by=actors[account],
                title=f'B1 {ident[:8]} пользователь {account + 1} слот {index + 1}',
                normalization_method=base.normalization_method, taxonomy_enabled=base.taxonomy_enabled,
                llm_config=deepcopy(base.llm_config))
            db.add(target); db.flush()
            attach_base(db, target, base.id, admin)
            target.base_snapshot = {**target.base_snapshot, 'b1_experiment_id': ident, 'b1_role': 'interactive'}
            if target.base_snapshot['normalized_rows'] != value['base_rows']:
                raise ValueError('Количество доступных позиций изменилось во время подготовки пула')
            for item in db.scalars(select(Item).where(Item.session_id == target.id, Item.kind == 'residual', Item.stock_id.is_not(None))):
                original = item.stock_id
                if original not in stock_map:
                    position = StockPosition(organization_id=admin.organization_id, base_session_id=target.id)
                    db.add(position); db.flush(); stock_map[original] = position.id
                item.stock_id = stock_map[original]
            clone_requirements(db, target, db.get(Item, value['requirement_ids'][requirement_index]), ident)
            target.status = 'NORMALIZED'; db.commit()
            stock_map_path.write_text(json.dumps(stock_map))
            pool.append(dict(session_id=target.id, user_id=actors[account], account=account, ordinal=index, busy=False))
            save(ident, dict(pool=pool, current_run={'phase': 'SETUP', 'progress': round((index + 1) / count * 100),
                                                  'prepared_sessions': index + 1, 'pool_size': count}))
        if value['scope'] == 'confirmation':
            source = db.get(ProcurementSession, value['background_session_id'])
            background = value['background_pool'] or [clone_background(db, source, admin, ident)]
            save(ident, dict(background_pool=background))
        manifest, digest = input_manifest(db, value)
        (directory(ident) / 'inputs.json').write_text(json.dumps(manifest, ensure_ascii=False, default=str))
        save(ident, dict(input_sha256=digest))


def percentile(values, p):
    return sorted(values)[max(0, math.ceil(len(values) * p) - 1)] if values else None


def summarize(rows):
    sent = [row for row in rows if row['outcome'] != 'GENERATOR_MISSED']
    successes = [row for row in sent if row['outcome'] == 'COMPLETED']
    times = [row['total_seconds'] for row in successes]
    return dict(scheduled=len(rows), started=len(sent), success=len(successes),
                failed=len(sent) - len(successes), missed=len(rows) - len(sent),
                p50_seconds=percentile(times, .5), p95_seconds=percentile(times, .95),
                error_rate=(len(sent) - len(successes)) / len(sent) if sent else None,
                success_within_seconds={str(limit): sum(t <= limit for t in times) / len(sent) if sent else None for limit in (1, 5, 10, 120)},
                success_fraction_denominator='sent',
                start_lag_p95_ms=percentile([row.get('start_lag_ms', 0) for row in sent], .95),
                valid=len(sent) == len(rows) and all(row.get('start_lag_ms', 0) <= 100 for row in sent),
                latency_population='successful requests; failures and timeouts reported separately')


def background_snapshot(session_ids, since):
    rows, active = 0, 0
    with SessionLocal() as db:
        jobs = list(db.scalars(select(Job).where(Job.session_id.in_(session_ids), Job.created_at >= since)))
        for job in jobs:
            active += job.status in ACTIVE_STATUSES
            counts = job.params.get('normalization_counts', {})
            if isinstance(counts, dict):
                rows += int(counts.get('residual', 0))
    return rows, active


def ensure_background(ident):
    from app.api.routes import enqueue
    value = load(ident)
    if value.get('cancel_requested'):
        return
    with SessionLocal() as db:
        actor = db.get(User, value['requested_by'])
        for sid in value['background_pool']:
            session = db.get(ProcurementSession, sid)
            if session.base_snapshot.get('b1_experiment_id') != ident:
                raise ValueError('Фоновая сессия не принадлежит эксперименту')
            if db.scalar(select(Job.id).where(Job.session_id == sid, Job.status.in_(ACTIVE_STATUSES)).limit(1)):
                continue
            last = db.scalar(select(Job).where(Job.session_id == sid).order_by(Job.created_at.desc()).limit(1))
            if last and last.status == 'FAILED':
                raise ValueError('Фоновая подготовка завершилась ошибкой: ' + (last.error or '')[:200])
            db.execute(update(Item).where(Item.session_id == sid, Item.kind == 'residual').values(passport=None, category=None, issues=[]))
            session.status = 'APPROVED'; db.commit()
            result = enqueue(db, session, actor, 'normalize', {'b1_experiment_id': ident, 'b1_role': 'background'})
            append(ident, 'background.jsonl', dict(at=utc(), session_id=sid, job_id=result['task_id']))


def cancel_background(ident):
    value = load(ident)
    with SessionLocal() as db:
        for job in db.scalars(select(Job).where(Job.session_id.in_(value['background_pool']), Job.status.in_(ACTIVE_STATUSES))):
            job.status = 'CANCELLED' if job.status == 'QUEUED' else 'CANCEL_REQUESTED'
        db.commit()


def clean_between_runs(ident):
    """Only own completed test results, after exported evidence; keep jobs and audit."""
    value = load(ident)
    session_ids = [row['session_id'] for row in value['pool']] + value['background_pool']
    with SessionLocal() as db:
        if db.scalar(select(Job.id).where(Job.session_id.in_(session_ids), Job.status.in_(ACTIVE_STATUSES)).limit(1)):
            raise HTTPException(409, 'Остались активные задания эксперимента. Дождитесь их завершения перед продолжением')
        for session in db.scalars(select(ProcurementSession).where(ProcurementSession.id.in_(session_ids))):
            if session.base_snapshot.get('b1_experiment_id') != ident:
                raise ValueError('Очистка чужой сессии запрещена')
        for job in db.scalars(select(Job).where(Job.session_id.in_(session_ids), Job.status.in_(TERMINAL))):
            snapshot = db.get(RunSnapshot, job.id)
            if snapshot and not snapshot.payload.get('b1_archived'):
                digest = hashlib.sha256(json.dumps(snapshot.payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
                count = db.scalar(select(func.count()).select_from(Match).where(Match.job_id == job.id)) or 0
                append(ident, 'cleanup.jsonl', dict(at=utc(), job_id=job.id, status=job.status,
                    session_id=job.session_id, removed_match_rows=count, snapshot_sha256=digest,
                    preserved='Job, StageTiming, Audit; raw responses and result checksums in artifacts'))
                db.execute(delete(Match).where(Match.job_id == job.id))
                snapshot.payload = {'b1_archived': True, 'experiment_id': ident, 'sha256': digest,
                    'session': snapshot.payload.get('session', {}), 'job_ids': snapshot.payload.get('job_ids', []),
                    'items': [], 'documents': [], 'matches': []}
        for session in db.scalars(select(ProcurementSession).where(ProcurementSession.id.in_([row['session_id'] for row in value['pool']]))):
            session.status = 'NORMALIZED'; session.progress = 0
        db.commit()


async def request_job(client, slot, index, run, scheduled, ident, phase):
    start = time.monotonic()
    run_index, attempt_id = run['index'], run['attempt_id']
    request_id = f'{ident}:{attempt_id}:{phase}:{index}'
    sid = slot['session_id']
    row = dict(run_index=run_index, attempt_id=attempt_id, phase=phase, number=index, account=slot['account'], session_id=sid,
               request_id=request_id, started_at=utc(), start_lag_ms=(start - scheduled) * 1000,
               outcome='CLIENT_ERROR')
    try:
        async with asyncio.timeout(PROTOCOL['timeout_seconds']):
            response = await client.post(f'matching/{sid}/run', json={'target_shortlist_size': 150},
                                         headers={'X-Test-Run-ID': ident, 'X-Request-ID': request_id})
            row['accept_seconds'] = time.monotonic() - start
            if response.status_code != 202:
                row.update(outcome='HTTP_ERROR', http_status=response.status_code); return row
            jid = response.json()['task_id']; row['job_id'] = jid
            while True:
                response = await client.get(f'matching/{sid}/status')
                if response.status_code != 200:
                    row.update(outcome='HTTP_ERROR', http_status=response.status_code); return row
                status = response.json()
                if status.get('id') != jid:
                    row['outcome'] = 'JOB_ID_MISMATCH'; return row
                if status['status'] in TERMINAL:
                    row['outcome'] = status['status']
                    row['execution_metrics'] = status.get('params', {}).get('execution_metrics', {})
                    break
                await asyncio.sleep(PROTOCOL['poll_seconds'])
            if row['outcome'] == 'COMPLETED':
                response = await client.get(f'matching/{sid}/candidates', params={'limit': 150, 'offset': 0})
                if response.status_code != 200:
                    row.update(outcome='RESULT_HTTP_ERROR', http_status=response.status_code); return row
                body = response.json()
                if (not isinstance(body, dict) or not isinstance(body.get('items'), list)
                    or type(body.get('total')) is not int or body['total'] < 0
                    or any(not isinstance(entry, dict) for entry in body['items'])):
                    row['outcome'] = 'RESULT_CONTRACT_ERROR'; return row
                if any(entry.get('job_id') != jid for entry in body['items']):
                    row['outcome'] = 'RESULT_JOB_MISMATCH'; return row
                row['result_count'] = body['total']
                row['result_sha256'] = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                row['total_seconds'] = time.monotonic() - start
        if row['outcome'] == 'COMPLETED':
            await asyncio.to_thread(append, ident, 'results.jsonl', dict(run_index=run_index, attempt_id=attempt_id,
                phase=phase, request_id=request_id, session_id=sid, job_id=jid, response=body))
        return row
    except TimeoutError:
        row['outcome'] = 'TIMEOUT'; return row
    except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
        row.update(outcome='CLIENT_ERROR', error_type=type(exc).__name__); return row
    finally:
        row.setdefault('total_seconds', time.monotonic() - start)
        await asyncio.to_thread(append, ident, 'raw.jsonl', row)
        # Do not reuse a session while a timed-out server job is still executing.
        def server_active():
            with SessionLocal() as db:
                return db.scalar(select(Job.id).where(Job.session_id == sid, Job.status.in_(ACTIVE_STATUSES)).limit(1))
        active = await asyncio.to_thread(server_active)
        slot['busy'] = bool(active)
        slot['awaiting_server'] = bool(active)


def preflight(ident):
    from app.tasks import celery_app
    from app.services.job_routing import routing_metadata
    with SessionLocal() as db:
        if db.scalar(select(Job.id).where(Job.status.in_(ACTIVE_STATUSES)).limit(1)):
            raise ValueError('Перед запуском стенд должен быть свободен: обнаружено активное задание')
        if db.scalar(select(LabExperiment.id).where(LabExperiment.status.in_(['QUEUED', 'RUNNING'])).limit(1)) or db.scalar(select(NormalizationExperiment.id).where(NormalizationExperiment.status.in_(['QUEUED', 'RUNNING'])).limit(1)):
            raise ValueError('Остановите другие лабораторные эксперименты перед B1')
    inspector = celery_app.control.inspect(timeout=3)
    subscriptions, statistics = inspector.active_queues() or {}, inspector.stats() or {}
    allocation = {'shared': 0, 'interactive': 0, 'batch': 0}
    workers = []
    for name, queues in subscriptions.items():
        selected = [row['name'] for row in queues if row['name'] in allocation]
        if not selected:
            continue
        if len(selected) != 1 or name not in statistics:
            raise ValueError('Не удалось подтвердить однозначное распределение business workers')
        concurrency = statistics[name].get('pool', {}).get('max-concurrency')
        if concurrency != 1:
            raise ValueError('B1 требует два отдельных business workers с concurrency=1')
        allocation[selected[0]] += concurrency
        workers.append({'name': name, 'queue': selected[0], 'slots': concurrency})
    expected = {'shared': 2, 'interactive': 0, 'batch': 0} if mode() == 'shared' else {'shared': 0, 'interactive': 1, 'batch': 1}
    if allocation != expected:
        raise ValueError(f'Фактические подписки workers не соответствуют {mode()}: {allocation}; ожидается {expected}')
    evidence = dict(at=utc(), routing=routing_metadata(), actual_workers=workers)
    append(ident, 'environment.jsonl', evidence)
    return evidence


async def execute_run(ident, run, should_stop=None, controller_token=None):
    value = load(ident)
    slots = deepcopy(value['pool']); records = []
    created_at = datetime.now(timezone.utc)
    clients = {}
    origin = time.monotonic()
    confirmation = value['scope'] == 'confirmation'
    async with AsyncExitStack() as stack:
        with SessionLocal() as db:
            for user_id in value['actors']:
                token = create_access_token(db.get(User, user_id))
                clients[user_id] = await stack.enter_async_context(httpx.AsyncClient(
                    base_url=settings.B1_API_BASE_URL.rstrip('/') + '/',
                    headers={'Authorization': 'Bearer ' + token}, timeout=20, trust_env=False))
        if confirmation:
            await asyncio.to_thread(ensure_background, ident)
        origin = time.monotonic()
        def stopping():
            state = load(ident)
            return ((should_stop is not None and should_stop()) or state.get('cancel_requested')
                    or (controller_token and state.get('controller_token') != controller_token))
        tasks = []
        measured = False
        background_samples = []
        monitor_stop = asyncio.Event()
        async def monitor():
            while not monitor_stop.is_set():
                awaiting = [slot for slot in slots if slot.get('awaiting_server')]
                if awaiting:
                    def query_active():
                        with SessionLocal() as db:
                            return set(db.scalars(select(Job.session_id).where(
                                Job.session_id.in_([slot['session_id'] for slot in awaiting]), Job.status.in_(ACTIVE_STATUSES))))
                    active_ids = await asyncio.to_thread(query_active)
                    for slot in awaiting:
                        if slot['session_id'] not in active_ids:
                            slot['busy'] = False; slot['awaiting_server'] = False
                if confirmation:
                    count, active = await asyncio.to_thread(background_snapshot, value['background_pool'], created_at)
                    if measured:
                        background_samples.append({'at': utc(), 'rows': count, 'active': active})
                await asyncio.sleep(MONITOR_SECONDS)
        monitor_task = asyncio.create_task(monitor())
        async def one(slot, number, when, phase):
            row = await request_job(clients[slot['user_id']], slot, number, run, when, ident, phase)
            records.append(row)
        async def phase(name, count, interval, sequential=False):
            start = time.monotonic(); last_progress = start
            for number in range(count):
                if stopping():
                    break
                scheduled = start + number * interval if not sequential else time.monotonic()
                await asyncio.sleep(max(0, scheduled - time.monotonic()))
                if stopping():
                    break
                account = 0 if value['scenario'] == 'sequential_10' else number % 10
                choices = [slot for slot in slots if slot['account'] == account and not slot['busy']]
                lag = (time.monotonic() - scheduled) * 1000
                if not choices or lag > PROTOCOL['max_start_lag_ms']:
                    row = dict(run_index=run['index'], phase=name, number=number, outcome='GENERATOR_MISSED',
                               attempt_id=run['attempt_id'], reason='pool_exhausted' if not choices else 'start_lag', start_lag_ms=lag)
                    records.append(row); await asyncio.to_thread(append, ident, 'raw.jsonl', row)
                else:
                    slot = next((candidate for candidate in choices if candidate.get('ordinal') == number), choices[0]) if sequential else choices[0]
                    slot['busy'] = True
                    task = asyncio.create_task(one(slot, number, scheduled, name)); tasks.append(task)
                    if sequential:
                        await task
                measured = [row for row in records if row['phase'] == 'MEASURING']
                summary = summarize(measured)
                if time.monotonic() - last_progress >= 1 or number == count - 1:
                    await asyncio.to_thread(save, ident, dict(heartbeat_at=utc(), current_run={**run, 'phase': name,
                        **summary, 'completed': len(measured), 'elapsed_seconds': time.monotonic() - origin,
                        'progress': round((number + 1) / count * 100), 'summary': summary}), expected_controller=controller_token)
                    last_progress = time.monotonic()
            if interval:
                while time.monotonic() < start + count * interval and not stopping():
                    await asyncio.sleep(min(.25, max(0, start + count * interval - time.monotonic())))
        if confirmation:
            interval = 60 / PROTOCOL['rate_per_minute']
            await phase('WARMUP', round(PROTOCOL['warmup_seconds'] / interval), interval)
            before_rows, _ = await asyncio.to_thread(background_snapshot, value['background_pool'], created_at)
            measurement_start = time.monotonic()
            measured = True
            await phase('MEASURING', PROTOCOL['requests_per_run'], interval)
            measured_seconds = time.monotonic() - measurement_start
            after_rows, active = await asyncio.to_thread(background_snapshot, value['background_pool'], created_at)
            measured = False
        else:
            await phase('MEASURING', 10, 0, value['scenario'] == 'sequential_10')
            before_rows = after_rows = active = 0; measured_seconds = time.monotonic() - origin
        monitor_stop.set(); monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
        await asyncio.to_thread(save, ident, dict(current_run={**(load(ident)['current_run'] or run), 'phase': 'DRAINING'}), expected_controller=controller_token)
        if confirmation:
            cancel_background(ident)
        await asyncio.gather(*tasks)
        result = summarize([row for row in records if row['phase'] == 'MEASURING'])
        result.update(batch_rows_per_minute=(after_rows - before_rows) / (measured_seconds / 60) if confirmation else None,
                      batch_rows=after_rows - before_rows, measurement_seconds=measured_seconds,
                      scenario_makespan_seconds=time.monotonic() - origin,
                      scope=value['scope'], confirmed=False if not confirmation else None,
                      reason='Диагностический сценарий; не проверка гипотезы B1' if not confirmation else 'Ожидаются все пять пар')
        if confirmation:
            continuous = bool(background_samples) and all(sample['active'] for sample in background_samples) and bool(active)
            result.update(background_continuous=continuous, background_active_samples=sum(bool(sample['active']) for sample in background_samples),
                          background_samples=len(background_samples), background_completed_early=not continuous,
                          background_policy='one full catalog job; no hidden restart')
            append(ident, 'background.jsonl', {'run_index': run['index'], 'attempt_id': run['attempt_id'], 'measurement_samples': background_samples})
            result['valid'] = result['valid'] and result['scheduled'] == PROTOCOL['requests_per_run'] and after_rows > before_rows and continuous
            if not continuous:
                result['reason'] = 'Фоновая подготовка завершилась или прервалась до конца окна. Полная гипотеза не подтверждается.'
        with SessionLocal() as db:
            _, current_hash = input_manifest(db, value)
            own_ids = [row['session_id'] for row in value['pool']] + value['background_pool']
            foreign = db.scalar(select(Job.id).where(Job.created_at >= created_at, Job.session_id.not_in(own_ids)).limit(1))
        if current_hash != value.get('input_sha256'):
            result.update(valid=False, reason='Исходные данные изменились во время эксперимента')
        if foreign:
            result.update(valid=False, reason='Во время эксперимента запущена посторонняя обработка на стенде')
        if stopping():
            result.update(valid=False, reason='Запуск прерван оператором или остановкой контроллера')
        return result


def campaign_summary(plan):
    pairs = []
    for pair in range(1, 6):
        runs = [row for row in plan if row['pair_index'] == pair and row['status'] == 'COMPLETED']
        if len(runs) != 2:
            continue
        baseline = next(row['summary'] for row in runs if row['routing_mode'] == 'shared')
        variant = next(row['summary'] for row in runs if row['routing_mode'] == 'split')
        p95_ratio = variant['p95_seconds'] / baseline['p95_seconds'] if baseline.get('p95_seconds') and variant.get('p95_seconds') is not None else None
        batch_ratio = variant['batch_rows_per_minute'] / baseline['batch_rows_per_minute'] if baseline.get('batch_rows_per_minute') else None
        passed = bool(baseline['valid'] and variant['valid'] and baseline.get('error_rate') is not None and baseline['error_rate'] < .01
                      and baseline.get('success', 0) >= 594 and variant.get('success', 0) >= 594
                      and p95_ratio is not None and p95_ratio <= .7
                      and batch_ratio is not None and batch_ratio >= .9 and variant['error_rate'] is not None and variant['error_rate'] < .01)
        pairs.append(dict(pair_index=pair, p95_ratio=p95_ratio, batch_ratio=batch_ratio, passed=passed))
    complete = len(pairs) == 5
    return dict(pairs=pairs, confirmed=all(row['passed'] for row in pairs) if complete else None,
                valid=complete and all(row['summary']['valid'] for row in plan if 'summary' in row),
                reason='Критерий выполнен во всех пяти парах' if complete and all(row['passed'] for row in pairs) else
                       'Критерий не выполнен во всех пяти парах' if complete else 'Ожидаются все пять пар')


def run_experiment(ident):
    redis = Redis.from_url(settings.REDIS_URL)
    lock = redis.lock('b1:global-experiment', timeout=90, blocking_timeout=1, thread_local=False)
    if not lock.acquire(blocking=True):
        value = load(ident)
        if value['status'] == 'QUEUED':
            save(ident, dict(status='AWAITING_PROFILE', error='Другой эксперимент B1 занимает стенд. Повторите запуск после его завершения'),
                 expected_updated_at=value['updated_at'])
        return
    stop, lost = Event(), Event()
    controller_token = uuid.uuid4().hex
    def heartbeat():
        while not stop.wait(20):
            try:
                lock.reacquire()
                save(ident, dict(heartbeat_at=utc()), expected_controller=controller_token)
            except Exception:
                lost.set()
                return
    thread = Thread(target=heartbeat, daemon=True); thread.start()
    try:
        recover_stale()
        value = load(ident)
        if value['status'] != 'QUEUED':
            return
        run = {**next(row for row in value['plan'] if row['status'] == 'PENDING'), 'attempt_id': uuid.uuid4().hex}
        if run['routing_mode'] != mode():
            save(ident, dict(status='AWAITING_PROFILE', error='Режим очередей изменился до старта')); return
        save(ident, dict(status='PREPARING', controller_token=controller_token, heartbeat_at=utc(), current_run={**run, 'phase': 'SETUP'}))
        prepare_pool(ident, should_stop=lost.is_set)
        if lost.is_set():
            raise RuntimeError('Потеряна блокировка контроллера во время подготовки')
        clean_between_runs(ident)
        preflight(ident)
        save(ident, dict(status='RUNNING', current_run={**run, 'phase': 'WARMUP' if value['scope'] == 'confirmation' else 'MEASURING'}), expected_controller=controller_token)
        summary = asyncio.run(execute_run(ident, run, should_stop=lost.is_set, controller_token=controller_token))
        if lost.is_set():
            summary.update(valid=False, reason='Потеряна блокировка контроллера')
        value = load(ident)
        if value.get('controller_token') != controller_token:
            return
        interrupted = lost.is_set() or value.get('cancel_requested')
        plan = value['plan']
        if not interrupted:
            plan[run['index']].update(status='COMPLETED', summary=summary, finished_at=utc(), attempt_id=run['attempt_id'])
        else:
            append(ident, 'controller-events.jsonl', dict(at=utc(), event='INTERRUPTED', run=run, summary=summary))
        pending = next((row for row in plan if row['status'] == 'PENDING'), None)
        save(ident, dict(plan=plan, status='INTERRUPTED' if lost.is_set() else 'CANCELLED' if value.get('cancel_requested') else 'AWAITING_PROFILE' if pending else 'COMPLETED',
            progress=round(sum(row['status'] == 'COMPLETED' for row in plan) / len(plan) * 100),
            expected_routing_mode=pending['routing_mode'] if pending else None,
            current_run={**run, 'phase': 'COMPLETED', 'summary': summary},
            summary=campaign_summary(plan) if value['scope'] == 'confirmation' else summary), expected_controller=controller_token)
    except Exception as exc:
        value = load(ident)
        if value.get('controller_token') == controller_token:
            cancel_background(ident)
            save(ident, dict(status='INTERRUPTED' if lost.is_set() else 'CANCELLED' if value.get('cancel_requested') else 'FAILED',
                             error=f'{type(exc).__name__}: {str(exc)[:500]}'), expected_controller=controller_token)
    finally:
        stop.set(); thread.join(timeout=2)
        try:
            if lock.owned(): lock.release()
        except Exception:
            pass


def export(ident, format):
    value = read_public(load(ident))
    if format == 'json':
        return json.dumps(value, ensure_ascii=False, indent=2).encode(), 'application/json', 'json'
    if format == 'csv':
        output = io.StringIO(); fields = ['run_index', 'attempt_id', 'phase', 'number', 'account', 'session_id', 'job_id', 'outcome',
                                         'start_lag_ms', 'accept_seconds', 'total_seconds', 'result_count', 'result_sha256']
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction='ignore', delimiter=';'); writer.writeheader()
        path = directory(ident) / 'raw.jsonl'
        if path.exists():
            for line in path.read_text().splitlines(): writer.writerow(json.loads(line))
        return ('\ufeff' + output.getvalue()).encode(), 'text/csv; charset=utf-8', 'csv'
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('experiment.json', json.dumps(value, ensure_ascii=False, indent=2))
        for name in ('raw.jsonl', 'results.jsonl', 'background.jsonl', 'cleanup.jsonl', 'inputs.json', 'environment.jsonl', 'controller-events.jsonl'):
            path = directory(ident) / name
            if path.exists(): archive.write(path, name)
    return output.getvalue(), 'application/zip', 'zip'

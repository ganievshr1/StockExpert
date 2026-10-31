"""Admin-only reproducible H1 experiments; original sessions are read-only inputs."""
import csv
import io
import json
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import defer
from app.api.deps import own_session
from app.api.developer import developer, owned_reference
from app.db import NormalizationExperiment, get_db, public
from app.services.regex_experiments import snapshot
from app.services.regex_excel import parse_stock_xlsx, MAX_BYTES
from app.services import regex_normalization as rules

router = APIRouter(prefix='/developer/regex-experiments', tags=['Эксперимент: регулярные выражения'])


class StartRegexExperiment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_session_id: UUID
    dataset_id: UUID
    request_id: UUID


def owned(db, ident, user):
    experiment = db.scalar(select(NormalizationExperiment).where(
        NormalizationExperiment.id == ident, NormalizationExperiment.organization_id == user.organization_id))
    if not experiment:
        raise HTTPException(404, 'Эксперимент не найден')
    return experiment


def summary(experiment):
    data = {c.name: getattr(experiment, c.name) for c in experiment.__table__.columns
            if c.name not in ('input_snapshot', 'predictions')}
    data['result'] = {k: v for k,v in experiment.result.items() if k not in ('rows', 'fields')}
    data['source_type'] = 'session' if experiment.source_session_id else 'xlsx'
    return data


@router.post('', status_code=202)
def create(payload: StartRegexExperiment, db=Depends(get_db), user=Depends(developer)):
    sid, rid, request_id = map(str, (payload.source_session_id, payload.dataset_id, payload.request_id))
    existing = db.scalar(select(NormalizationExperiment).where(
        NormalizationExperiment.organization_id == user.organization_id, NormalizationExperiment.request_id == request_id))
    if existing:
        if (existing.source_session_id, existing.dataset_id) != (sid, rid):
            raise HTTPException(409, 'Этот ключ запроса уже использован для другого эксперимента')
        return summary(existing)
    session = own_session(db, sid, user, lock=True)
    reference = owned_reference(db, rid, user)
    active = db.scalar(select(func.count()).select_from(NormalizationExperiment).where(
        NormalizationExperiment.organization_id == user.organization_id,
        NormalizationExperiment.status.in_(['QUEUED', 'RUNNING'])))
    if active >= 5:
        raise HTTPException(409, 'Уже запущено пять экспериментов. Дождитесь завершения CPU-обработчика.')
    try:
        inputs, digest, baseline = snapshot(db, session, reference)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    experiment = NormalizationExperiment(
        organization_id=user.organization_id, requested_by=user.id,
        source_session_id=sid, source_title=session.title[:300], dataset_id=rid,
        request_id=request_id, rules_version=rules.VERSION, rules_sha256=rules.RULES_SHA256,
        input_sha256=digest, input_snapshot=inputs, baseline=baseline, row_count=len(inputs['rows']))
    db.add(experiment)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(NormalizationExperiment).where(
            NormalizationExperiment.organization_id == user.organization_id, NormalizationExperiment.request_id == request_id))
        if not existing or (existing.source_session_id, existing.dataset_id) != (sid, rid):
            raise HTTPException(409, 'Конфликт повторного запроса') from None
        return summary(existing)
    # Durable outbox: beat publishes this committed row; HTTP never calls a model.
    return summary(experiment)


@router.post('/xlsx', status_code=202)
async def create_from_xlsx(file: UploadFile = File(...), request_id: UUID = Form(...),
                           dataset_id: UUID | None = Form(None), db=Depends(get_db), user=Depends(developer)):
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, 'Размер XLSX должен быть не больше 20 МБ.')
    rid = str(dataset_id) if dataset_id else None
    reference = owned_reference(db, rid, user) if rid else None
    try:
        inputs, digest = await run_in_threadpool(parse_stock_xlsx, data, file.filename or '', reference)
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(422, str(exc)[:500]) from None
    key = str(request_id)
    def previous():
        return db.scalar(select(NormalizationExperiment).where(
            NormalizationExperiment.organization_id == user.organization_id,
            NormalizationExperiment.request_id == key))
    def repeat(existing):
        if existing.source_session_id or existing.dataset_id != rid or existing.input_sha256 != digest:
            raise HTTPException(409, 'Этот ключ запроса уже использован для другого файла или эталона')
        return summary(existing)
    existing = previous()
    if existing:
        return repeat(existing)
    active = db.scalar(select(func.count()).select_from(NormalizationExperiment).where(
        NormalizationExperiment.organization_id == user.organization_id,
        NormalizationExperiment.status.in_(['QUEUED', 'RUNNING'])))
    if active >= 5:
        raise HTTPException(409, 'Уже запущено пять экспериментов. Дождитесь завершения CPU-обработчика.')
    experiment = NormalizationExperiment(
        organization_id=user.organization_id, requested_by=user.id, source_session_id=None,
        source_title=inputs['sources'][0]['filename'], dataset_id=rid, request_id=key,
        rules_version=rules.VERSION, rules_sha256=rules.RULES_SHA256, input_sha256=digest,
        input_snapshot=inputs, baseline={}, row_count=len(inputs['rows']))
    db.add(experiment)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = previous()
        if not existing:
            raise HTTPException(409, 'Конфликт создания эксперимента') from None
        return repeat(existing)
    return summary(experiment)


@router.get('')
def list_experiments(dataset_id: str | None = None, limit: int = Query(30, ge=1, le=100),
                     offset: int = Query(0, ge=0), db=Depends(get_db), user=Depends(developer)):
    query = select(NormalizationExperiment).where(NormalizationExperiment.organization_id == user.organization_id)
    if dataset_id:
        owned_reference(db, dataset_id, user)
        query = query.where(NormalizationExperiment.dataset_id == dataset_id)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = db.scalars(query.options(defer(NormalizationExperiment.input_snapshot), defer(NormalizationExperiment.predictions))
                      .order_by(NormalizationExperiment.created_at.desc(), NormalizationExperiment.id).limit(limit).offset(offset))
    return {'total': total, 'items': [summary(e) for e in items]}


@router.get('/{ident}')
def detail(ident: str, db=Depends(get_db), user=Depends(developer)):
    return summary(owned(db, ident, user))


@router.get('/{ident}/passports')
def passports(ident: str, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), review_only: bool = False,
              db=Depends(get_db), user=Depends(developer)):
    experiment = owned(db, ident, user)
    errors = {(r['sheet'], r['row'], r.get('id')): r for r in experiment.result.get('rows', [])}
    # Align by gold id through source coordinates; the gold list is used only for display.
    reference = owned_reference(db, experiment.dataset_id, user) if experiment.dataset_id else None
    gold_ids = {(p['source_sha256'], p['sheet'], p['row']): p['id'] for p in reference.payload['passports'] if p['kind'] == 'residual'} if reference else {}
    source_rows = {r['source_item_id']: r for r in experiment.input_snapshot['rows']}
    predictions = [p for p in experiment.predictions if not review_only or p['issues']]
    items = []
    for p in predictions[offset:offset+limit]:
        source = source_rows.get(p['source_item_id'], {})
        gold_id = gold_ids.get((p['source_sha256'], p['sheet'], p['row']))
        items.append({**p, 'source_text': source.get('source_text', ''), 'company': source.get('company'), 'source_group': source.get('source_group'),
                      'evaluation': errors.get((p['sheet'], p['row'], gold_id))})
    return {'total': len(predictions), 'items': items}


@router.get('/{ident}/export')
def export(ident: str, format: str = Query('json', pattern='^(json|csv|passports_csv)$'),
           db=Depends(get_db), user=Depends(developer)):
    experiment = owned(db, ident, user)
    if format == 'json':
        data = json.dumps(public(experiment), ensure_ascii=False, indent=2, default=str)
        return Response(data, media_type='application/json', headers={'Content-Disposition': f'attachment; filename="regex-experiment-{ident}.json"'})
    if format == 'passports_csv':
        source_rows = {r['source_item_id']: r for r in experiment.input_snapshot['rows']}
        # Historical exports use the fields actually saved by that run, never today's catalog only.
        keys = sorted({k for p in experiment.predictions for k in p['passport']['attributes'] if k != 'name'})
        fields = ['sheet', 'row', 'company', 'source_group', 'source_text', 'rules_version', 'review_required', 'issues'] + keys
        output = io.StringIO(); writer = csv.DictWriter(output, fieldnames=fields, delimiter=';')
        writer.writeheader()
        for prediction in experiment.predictions:
            source = source_rows[prediction['source_item_id']]
            value = {k: source.get(k) for k in ('sheet', 'row', 'company', 'source_group', 'source_text')}
            value.update({k: prediction['passport']['attributes'].get(k) for k in keys})
            value.update(rules_version=experiment.rules_version, review_required=bool(prediction['issues']),
                         issues=json.dumps(prediction['issues'], ensure_ascii=False))
            writer.writerow({k: "'"+v if isinstance(v, str) and v.lstrip().startswith(('=','+','-','@')) else v for k, v in value.items()})
        return Response('\ufeff'+output.getvalue(), media_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition': f'attachment; filename="regex-passports-{ident}.csv"'})
    result = experiment.result
    fields = ['experiment_id','source_session_id','source_title','status','method','rules_version','rules_sha256',
              'input_sha256','dataset_id','reference_checksum','rows','tp','fp','fn','h1_precision','h1_recall','h1_f1',
              'full_row_recall','exact_row_accuracy','name_accuracy','normalization_seconds','p50_row_ms','p95_row_ms',
              'baseline_h1_f1','baseline_models','llm_calls','timing_scope','evaluation_status',
              'rows_with_attributes','rows_with_parameters','model_only_rows','review_rows','is_development_dataset']
    value = {'experiment_id': ident, **summary(experiment), **result.get('metrics', {}),
             **result.get('coverage', {}), **result.get('timing', {}), **result.get('extraction', {}), 'rows': experiment.row_count,
             'evaluation_status': result.get('evaluation_status'), 'is_development_dataset': result.get('is_development_dataset'),
             'baseline_h1_f1': experiment.baseline.get('metrics', {}).get('h1_f1'),
             'baseline_models': ', '.join(m['model'] for m in experiment.baseline.get('models', [])),
             'reference_checksum': experiment.input_snapshot['reference_checksum'],
             'llm_calls': result.get('runtime', {}).get('llm_calls'),
             'timing_scope': result.get('timing', {}).get('scope')}
    output = io.StringIO(); writer = csv.DictWriter(output, fieldnames=fields, delimiter=';', extrasaction='ignore')
    writer.writeheader()
    writer.writerow({k: "'"+v if isinstance(v, str) and v.lstrip().startswith(('=','+','-','@')) else v for k,v in value.items()})
    return Response('\ufeff'+output.getvalue(), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="regex-experiment-{ident}.csv"'})

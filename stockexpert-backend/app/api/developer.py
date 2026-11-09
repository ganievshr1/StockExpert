"""Organization-scoped developer evaluation tools. Admin authorization is mandatory."""
import csv
import io
import json
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app.api.deps import get_current_user, own_session
from app.db import ExpertDataset, ExpertEvaluation, Job, ProcurementSession, get_db, public
from app.services.execution import ACTIVE_STATUSES
from app.services.expert_evaluation import compatible, refresh_evaluation
from app.services.expert_reference import parse_reference

router = APIRouter(prefix='/developer', tags=['Для разработчиков'])


def developer(user=Depends(get_current_user)):
    if user.role != 'admin':
        raise HTTPException(403, 'Раздел доступен только администратору')
    return user


def owned_reference(db, ident, user):
    reference = db.scalar(select(ExpertDataset).where(ExpertDataset.id == ident, ExpertDataset.organization_id == user.organization_id))
    if not reference:
        raise HTTPException(404, 'Эталон не найден')
    return reference


def describe(reference):
    data = public(reference)
    payload = data.pop('payload')
    data.update(sources=payload['sources'], passport_count=len(payload['passports']), pair_count=len(payload['pairs']), notes=payload['notes'])
    return data


@router.get('/references')
def references(db=Depends(get_db), user=Depends(developer)):
    return [describe(r) for r in db.scalars(select(ExpertDataset).where(ExpertDataset.organization_id == user.organization_id).order_by(ExpertDataset.created_at.desc()))]


@router.post('/references')
async def upload_reference(file: UploadFile = File(...), db=Depends(get_db), user=Depends(developer)):
    data = await file.read(20 * 1024 * 1024 + 1)
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, 'Эталон должен быть меньше 20 МБ')
    try:
        payload, checksum = parse_reference(data, file.filename or '')
    except ValidationError as exc:
        locations = ', '.join('.'.join(map(str, e['loc'])) + ': ' + e['msg'] for e in exc.errors(include_input=False)[:8])
        raise HTTPException(422, 'Ошибка структуры эталона: ' + locations) from None
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise HTTPException(422, 'Ошибка эталона: ' + str(exc)[:500]) from None
    reference = db.scalar(select(ExpertDataset).where(ExpertDataset.organization_id == user.organization_id, ExpertDataset.checksum == checksum))
    if not reference:
        reference = ExpertDataset(organization_id=user.organization_id, created_by=user.id, title=payload['title'], version=payload['version'], checksum=checksum, payload=payload)
        db.add(reference)
        db.commit()
    return describe(reference)


@router.get('/references/{ident}/download')
def download_reference(ident: str, db=Depends(get_db), user=Depends(developer)):
    reference = owned_reference(db, ident, user)
    return Response(json.dumps(reference.payload, ensure_ascii=False, indent=2), media_type='application/json', headers={'Content-Disposition': 'attachment; filename="expert-reference.json"'})


@router.get('/references/{ident}/sessions')
def sessions(ident: str, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), db=Depends(get_db), user=Depends(developer)):
    reference = owned_reference(db, ident, user)
    # Include only matching Excel content, irrespective of upload filename/session UUID.
    matches = [s for s in db.scalars(select(ProcurementSession).where(ProcurementSession.organization_id == user.organization_id).order_by(ProcurementSession.created_at.desc(), ProcurementSession.id)) if compatible(db, s.id, reference.payload)]
    subscriptions = {e.session_id: e for e in db.scalars(select(ExpertEvaluation).where(ExpertEvaluation.dataset_id == ident))}
    return {'total': len(matches), 'items': [{'id': s.id, 'title': s.title, 'status': s.status, 'created_at': s.created_at, 'llm_config': s.llm_config, 'normalization_method': s.normalization_method, 'base_snapshot': s.base_snapshot, 'selected': s.id in subscriptions} for s in matches[offset:offset+limit]]}


class Selection(BaseModel):
    session_ids: list[str] = Field(min_length=1, max_length=500)


@router.post('/references/{ident}/evaluate')
def evaluate(ident: str, selection: Selection, db=Depends(get_db), user=Depends(developer)):
    reference = owned_reference(db, ident, user)
    sessions = [own_session(db, sid, user, lock=True) for sid in sorted(set(selection.session_ids))]
    if any(not compatible(db, s.id, reference.payload) for s in sessions):
        raise HTTPException(422, 'Выбрана сессия с другой базой Excel')
    result = []
    for session in sessions:
        evaluation = db.scalar(select(ExpertEvaluation).where(ExpertEvaluation.dataset_id == ident, ExpertEvaluation.session_id == session.id))
        if not evaluation:
            evaluation = ExpertEvaluation(dataset_id=ident, session_id=session.id, requested_by=user.id)
            db.add(evaluation)
            db.flush()
        refresh_evaluation(db, evaluation)
        result.append(evaluation.id)
    db.commit()
    return {'evaluation_ids': result, 'note': 'Готовые результаты оценены без вызова моделей. Активные сессии будут оценены автоматически после завершения.'}


@router.get('/references/{ident}/results')
def results(ident: str, db=Depends(get_db), user=Depends(developer)):
    owned_reference(db, ident, user)
    evaluations = list(db.scalars(select(ExpertEvaluation).where(ExpertEvaluation.dataset_id == ident).order_by(ExpertEvaluation.updated_at.desc())))
    rows = []
    active = set(db.scalars(select(Job.session_id).where(Job.session_id.in_([e.session_id for e in evaluations]), Job.status.in_(ACTIVE_STATUSES))))
    for e in evaluations:
        session = db.get(ProcurementSession, e.session_id)
        result = {k: v for k, v in e.result.items() if k not in ('rows', 'pairs')}
        waiting = e.session_id in active
        rows.append({**public(e), 'status': 'WAITING' if waiting else e.status,
                     'result': {'note': 'Ожидание завершения текущего расчёта'} if waiting else result,
                     'session_title': session.title})
    return rows


@router.get('/evaluations/{ident}')
def details(ident: str, db=Depends(get_db), user=Depends(developer)):
    evaluation = db.get(ExpertEvaluation, ident)
    if not evaluation:
        raise HTTPException(404, 'Оценка не найдена')
    owned_reference(db, evaluation.dataset_id, user)
    return public(evaluation)


@router.get('/references/{ident}/export')
def export(ident: str, db=Depends(get_db), user=Depends(developer)):
    rows = results(ident, db, user)
    fields = ['session_id', 'session_title', 'status', 'models', 'h1_precision', 'h1_recall', 'h1_f1', 'name_accuracy', 'h2_l1_accuracy', 'h2_leaf_accuracy', 'h2_l3_accuracy', 'h3_recall_retrieval', 'h3_recall_at_1', 'h3_recall_at_5', 'h3_recall_at_10', 'h3_recall_at_150', 'h3_precision_shortlist', 'h3_mrr', 'h3_ndcg10', 'session_processing_seconds', 'normalization_seconds', 'taxonomy_seconds', 'matching_seconds', 'p50_ms', 'p95_ms', 'query_samples', 'metric_version', 'dataset_id']
    for hypothesis in ('H1', 'H2', 'H3'):
        fields.extend(f'{hypothesis}_{key}' for key in ('value', 'numerator', 'denominator', 'substitution', 'scope', 'reason'))
    fields.extend(['h3_catalog_rows', 'h3_pairs_total', 'h3_top150', 'h3_retained_share'])
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields, delimiter=';', extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        result = row['result']
        value = {**row, **result.get('metrics', {}), **result.get('timing', {}), 'metric_version': result.get('metric_version'), 'models': ', '.join(m['model'] for m in result.get('models', []))}
        for metric in result.get('hypotheses', []):
            value.update({f"{metric['id']}_{key}": metric[key] for key in ('value', 'numerator', 'denominator', 'substitution', 'scope', 'reason')})
        value.update({f'h3_{key}': result.get('h3_population', {}).get(key) for key in ('catalog_rows', 'pairs_total', 'top150', 'retained_share')})
        # CSV must not execute source titles as formulas when opened in Excel.
        writer.writerow({k: ("'" + v if isinstance(v, str) and v.lstrip().startswith(('=', '+', '-', '@')) else v) for k, v in value.items()})
    return Response('\ufeff' + output.getvalue(), media_type='text/csv; charset=utf-8', headers={'Content-Disposition': 'attachment; filename="expert-model-comparison.csv"'})

from typing import Literal
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from app.api.developer import developer
from app.db import get_db, ProcurementSession, MetricRevision, LabExperiment, RunSnapshot, ExpertDataset
from app.services.hypothesis_reports import cohort, digest

router = APIRouter(prefix='/developer', tags=['Сравнение гипотез'])


@router.get('/comparisons')
def comparisons(hypothesis: Literal['H1','H2','H3'], source: Literal['manual','automatic'],
                dataset_id: str | None = None, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                db=Depends(get_db), user=Depends(developer)):
    sessions = {s.id:s for s in db.scalars(select(ProcurementSession).where(ProcurementSession.organization_id == user.organization_id))}
    refs = {r.id:r for r in db.scalars(select(ExpertDataset).where(ExpertDataset.organization_id == user.organization_id))}
    rows = []
    def base(row, snapshot, options=None):
        ref = refs.get(row.dataset_id)
        result = row.result or {}
        checksum = ref.checksum if ref else None
        if isinstance(row, MetricRevision):
            checksum = row.inputs.get('reference_checksum', checksum)
        labels = None
        if source == 'manual':
            # Manual reviews are intentionally a separate cohort per labelled run.
            labels = digest(row.inputs.get('reviews', [])) if isinstance(row, MetricRevision) else digest({'gold':row.payload.get('gold'), 'pairs':row.payload.get('pairs')})
        config = snapshot.get('session', {}).get('llm_config') or {}
        return {'id':row.id, 'session_id':row.session_id, 'session_title':sessions[row.session_id].title,
                'job_id':row.job_id, 'source':source, 'dataset_id':row.dataset_id,
                'reference':ref.title if ref else 'Ручные ответы', 'created_at':row.created_at,
                'taxonomy_enabled':snapshot.get('session', {}).get('taxonomy_enabled'),
                'model':result.get('model_label') or config.get('label') or config.get('model'),
                'cohort':cohort(snapshot,hypothesis,checksum,options,labels) if snapshot else 'legacy:' + row.id,
                'scope':'Закрытая тестовая часть' if options else 'Сохранённый запуск', 'hypothesis':hypothesis}
    revisions = db.scalars(select(MetricRevision).where(MetricRevision.session_id.in_(sessions), MetricRevision.source == source, MetricRevision.action == 'recalculate'))
    for row in revisions:
        if dataset_id and row.dataset_id != dataset_id:
            continue
        snap = db.get(RunSnapshot,row.job_id)
        snapshot = snap.payload if snap else {}
        r = row.result
        h = next((v for v in r.get('hypotheses', []) if v['id'] == hypothesis), {})
        manual_key = {'H1':'f1','H2':'accuracy','H3':'recall_at_150'}[hypothesis]
        value = h.get('value') if source == 'automatic' else r.get(manual_key)
        metrics = {k:v for k,v in r.get('metrics', {}).items() if k.startswith(hypothesis.lower()+'_')}
        if source == 'manual':
            metrics = {manual_key:value}
        entry = base(row,snapshot)
        if hypothesis == 'H1' and r.get('models'):
            entry['model'] = ', '.join(v.get('model','') for v in r['models'])
        if hypothesis == 'H1' and r.get('h1_mode') == 'name_exact':
            entry['cohort'] = digest([entry['cohort'],'name_exact'])
        rows.append({**entry,'kind':'revision','method':r.get('normalization_method','Сохранённый результат'), 'value':value,
                     'metrics':metrics, 'timing':r.get('timing',{}), 'note':h.get('reason') or r.get('note'),
                     'population':h.get('scope') or str(r.get('coverage',{})), 'status':'READY' if value is not None else 'NOT_AVAILABLE'})
    experiments = db.scalars(select(LabExperiment).where(LabExperiment.session_id.in_(sessions),LabExperiment.source == source))
    for row in experiments:
        if dataset_id and row.dataset_id != dataset_id:
            continue
        for method, result in (row.summary or row.result or {}).get('methods', {}).items():
            if result.get('hypothesis') != hypothesis:
                continue
            options = dict(row.payload.get('options',{}))
            options['h1_mode'] = (row.payload.get('reference') or {}).get('h1_mode', options.get('h1_mode','attributes'))
            metric = {'H1':'f1','H2':'accuracy','H3':'recall150'}[hypothesis]
            entry=base(row,row.payload['snapshot'],options)
            if method in ('h1_regex','h2_kmeans','h3_bm25'):entry['model']='CPU, без LLM'
            rows.append({**entry,'kind':'experiment','method':method,
                         'label':result.get('label'), 'status':result.get('status'), 'metrics':result.get('metrics',{}),
                         'value':result.get('metrics',{}).get(metric),'seconds':result.get('seconds'),
                         'population':str(result.get('population','')), 'note':result.get('note')})
    rows.sort(key=lambda r:(str(r['created_at']),r['id'],r['method']),reverse=True)
    return {'items':rows[offset:offset+limit], 'total':len(rows)}

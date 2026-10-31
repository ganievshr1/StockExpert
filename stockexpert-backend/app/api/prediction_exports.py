from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from app.api.deps import get_current_user, own_session
from app.api.laboratory import selected
from app.db import get_db, Match, LabExperiment, MetricRevision
from app.services.run_history import get_snapshot
from app.services.prediction_export import pipeline_export, experiment_export, revision_export, json_bytes, excel_bytes

router=APIRouter(tags=['Выгрузка результатов расчёта'])

def download(data, name, fmt):
    is_json=fmt=='json'
    return Response(json_bytes(data) if is_json else excel_bytes(data),
                    media_type='application/json' if is_json else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition':f'attachment; filename="{name}.{fmt}"','Cache-Control':'no-store'})

@router.get('/runs/{sid}/{job_id}/export')
def export_run(sid:str, job_id:str, stage:Literal['all','h1','h2','h3']='h1',
               format:Literal['json','xlsx']='json', item_kind:Literal['residual','requirement','all']='residual',
               db=Depends(get_db), user=Depends(get_current_user)):
    session=own_session(db,sid,user);job=selected(db,session,job_id)
    snap=get_snapshot(db,session,job)
    if not snap:raise HTTPException(409,'У этого старого запуска нет сохранённого снимка. Текущие паспорта не подставляются вместо исторических результатов.')
    if stage=='h2' and not snap['session'].get('taxonomy_enabled',True):raise HTTPException(409,'В выбранном расчёте H2 отключён. Доступны H1 и результаты поиска H3.')
    if stage=='h2' and not snap['session'].get('schema_doc',{}).get('categories'):raise HTTPException(409,'В выбранном расчёте ещё нет результатов H2.')
    if stage=='h3' and job.kind not in ('match','full'):raise HTTPException(409,'В выбранном расчёте поиск H3 не выполнялся. Выберите запуск подбора.')
    matches=None
    if stage in ('all','h3') and 'matches' not in snap:
        matches=[{'id':m.id,'requirement_id':m.requirement_id,'item_id':m.item_id,'result':m.result} for m in db.scalars(select(Match).where(Match.job_id==job.id,Match.session_id==sid))]
    data=pipeline_export(snap,job,stage,item_kind,matches)
    db.commit()
    return download(data,f'StockExpert_{stage}_{job.id}',format)

@router.get('/laboratory/{sid}/experiments/{experiment_id}/export')
def export_experiment(sid:str,experiment_id:str,format:Literal['json','xlsx']='json',
                      method:str|None=Query(None,max_length=100),stage:Literal['all','h1','h2','h3']='all',db=Depends(get_db),user=Depends(get_current_user)):
    own_session(db,sid,user)
    experiment=db.get(LabExperiment,experiment_id)
    if not experiment or experiment.session_id!=sid:raise HTTPException(404,'Эксперимент не найден')
    if experiment.status in ('QUEUED','RUNNING'):raise HTTPException(409,'Дождитесь завершения эксперимента')
    if method is not None and method not in experiment.result.get('methods',{}):raise HTTPException(404,'Результат метода не сохранён')
    data=experiment_export(experiment,method)
    from app.services.hypothesis_reports import select_hypothesis
    return download(select_hypothesis(data,stage),f'StockExpert_{stage}_experiment_{experiment.id}',format)

@router.get('/laboratory/{sid}/history/{revision_id}/export')
def export_revision(sid:str,revision_id:str,format:Literal['json','xlsx']='json',
                    stage:Literal['all','h1','h2','h3']='all',db=Depends(get_db),user=Depends(get_current_user)):
    own_session(db,sid,user)
    revision=db.get(MetricRevision,revision_id)
    if not revision or revision.session_id!=sid:raise HTTPException(404,'Версия оценки не найдена')
    from app.services.hypothesis_reports import select_hypothesis
    return download(select_hypothesis(revision_export(revision),stage),f'StockExpert_{stage}_evaluation_{revision.id}',format)

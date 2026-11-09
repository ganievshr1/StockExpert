"""Export a completed run: python scripts/export_predictions.py SESSION_UUID --job JOB_UUID > results.json"""
import argparse
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from app.db import SessionLocal,ProcurementSession,Job,Match
from app.services.run_history import get_snapshot
from app.services.prediction_export import pipeline_export,json_bytes,excel_bytes

def main():
    parser=argparse.ArgumentParser(description='Выгрузка сохранённых результатов запуска H1/H2/H3')
    parser.add_argument('session_id')
    parser.add_argument('--job',help='ID завершённого запуска; по умолчанию последний завершённый')
    parser.add_argument('--stage',choices=['h1','h2','h3','all'],default='h1')
    parser.add_argument('--format',choices=['json','xlsx'],default='json')
    parser.add_argument('--items',choices=['residual','requirement','all'],default='residual')
    args=parser.parse_args()
    with SessionLocal() as db:
        session=db.get(ProcurementSession,args.session_id)
        if not session:parser.error('Сессия не найдена')
        query=select(Job).where(Job.session_id==session.id,Job.status=='COMPLETED',Job.kind.in_(['normalize','taxonomy','match','full']))
        if args.job:query=query.where(Job.id==args.job)
        job=db.scalar(query.order_by(Job.created_at.desc(),Job.id.desc()).limit(1))
        if not job:parser.error('Завершённый запуск не найден')
        snap=get_snapshot(db,session,job)
        if not snap:parser.error('Снимок этого старого запуска отсутствует')
        if args.stage=='h2' and (not snap['session'].get('taxonomy_enabled',True) or not snap['session'].get('schema_doc',{}).get('categories')):
            parser.error('В выбранном запуске нет результатов H2')
        if args.stage=='h3' and job.kind not in ('match','full'):parser.error('В выбранном запуске H3 не выполнялся')
        matches=None
        if args.stage in ('all','h3') and 'matches' not in snap:
            matches=[{'id':m.id,'requirement_id':m.requirement_id,'item_id':m.item_id,'result':m.result} for m in db.scalars(select(Match).where(Match.session_id==session.id,Match.job_id==job.id))]
        data=pipeline_export(snap,job,args.stage,args.items,matches);db.commit()
        sys.stdout.buffer.write(json_bytes(data) if args.format=='json' else excel_bytes(data))

if __name__=='__main__':main()


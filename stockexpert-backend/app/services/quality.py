"""Expert-labelled evaluation scoped to one completed matching job."""
from collections import defaultdict
from sqlalchemy import select
from app.db import Job,Match,Item,QualityReview,QualityCompletion

def latest_job(db,sid):
    return db.scalar(select(Job).where(Job.session_id==sid,Job.kind.in_(['full','match']),Job.status=='COMPLETED').order_by(Job.created_at.desc(),Job.id.desc()).limit(1))

def evaluate(db,sid):
    job=latest_job(db,sid)
    empty={'job_id':None,'finalized':False,'finalized_at':None,'f1':None,'accuracy':None,'precision_shortlist':None,'recall_at_150':None,'coverage':{},'fields':[]}
    if not job:return empty
    reviews=list(db.scalars(select(QualityReview).where(QualityReview.job_id==job.id)))
    done=db.get(QualityCompletion,job.id)
    pairs=list(db.scalars(select(Match).where(Match.job_id==job.id)))
    by_pair={r.target_id:r for r in reviews if r.kind=='candidate'}
    groups=defaultdict(list)
    for m in pairs:groups[m.requirement_id].append(m)
    tp=fp=fn=correct=categories=fields_checked=0;field_counts=defaultdict(lambda:[0,0,0,0])
    passports=[r for r in reviews if r.kind=='passport']
    for r in passports:
        attrs=r.prediction.get('attributes',{})
        for key,expected in r.answer.get('attributes',{}).items():
            actual=attrs.get(key);counts=field_counts[key];counts[3]+=1;fields_checked+=1
            if expected is not None and actual==expected:tp+=1;counts[0]+=1
            else:
                if actual is not None:fp+=1;counts[1]+=1
                if expected is not None:fn+=1;counts[2]+=1
        if r.answer.get('category_reviewed') and r.answer.get('category_id') is not None:
            categories+=1;correct+=r.prediction.get('category_id')==r.answer['category_id']
    shortlist=[m for m in pairs if m.result.get('is_shortlisted')]
    checked=[m for m in shortlist if m.id in by_pair]
    suitable=sum(by_pair[m.id].answer['suitable'] for m in checked)
    full=[rows for rows in groups.values() if all(m.id in by_pair for m in rows)]
    relevant=hits=0
    for rows in full:
        for m in rows:
            if by_pair[m.id].answer['suitable']:
                relevant+=1;hits+=bool(m.result.get('rank') and 1<=m.result['rank']<=150)
    item_ids={x for m in pairs for x in (m.item_id,m.requirement_id)}
    return {'job_id':job.id,'finalized':bool(done),'finalized_at':done.finalized_at.isoformat() if done else None,
      'f1':2*tp/(2*tp+fp+fn) if done and (2*tp+fp+fn) else None,
      'accuracy':correct/categories if done and categories else None,
      'precision_shortlist':suitable/len(checked) if done and checked else None,
      'recall_at_150':hits/relevant if done and relevant else None,
      'coverage':{'passports_reviewed':len(passports),'passports_total':len(item_ids),'fields_checked':fields_checked,'tp':tp,'fp':fp,'fn':fn,'categories_checked':categories,'categories_correct':correct,'pairs_reviewed':len(by_pair),'pairs_total':len(pairs),'shortlist_reviewed':len(checked),'shortlist_total':len(shortlist),'shortlist_suitable':suitable,'requirements_fully_reviewed':len(full),'requirements_total':len(groups),'relevant_in_complete_requirements':relevant,'relevant_in_top150':hits},
      'fields':[{'key':k,'tp':c[0],'fp':c[1],'fn':c[2],'checked':c[3],'f1':2*c[0]/(2*c[0]+c[1]+c[2]) if done and (2*c[0]+c[1]+c[2]) else None} for k,c in field_counts.items()],
      'note':'F1: micro по явно проверенным параметрам, ошибочное значение даёт FP и FN. Accuracy: проверенные категории с заданным эталоном. Precision: проверенная часть shortlist. Recall@150: только потребности с проверкой всех пар данного запуска; не всей физической складской базы за пределами этого запуска. Неразмеченные позиции исключены.'}


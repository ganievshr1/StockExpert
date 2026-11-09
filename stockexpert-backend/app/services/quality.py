"""Expert-labelled evaluation scoped to one completed matching job."""
from collections import defaultdict
from sqlalchemy import select
from app.db import Job,Match,Item,QualityReview,QualityCompletion
from app.services.expert_reference import name_counts, equal_value, text_key

def latest_job(db,sid):
    return db.scalar(select(Job).where(Job.session_id==sid,Job.kind.in_(['full','match','normalize','taxonomy']),Job.status=='COMPLETED').order_by(Job.created_at.desc(),Job.id.desc()).limit(1))

def evaluate(db,sid,job_id=None):
    job=db.get(Job,job_id) if job_id else latest_job(db,sid)
    if job and (job.session_id!=sid or job.status!='COMPLETED'):job=None
    empty={'job_id':None,'finalized':False,'finalized_at':None,'f1':None,'text_f1':None,'text_precision':None,'text_recall':None,'accuracy':None,'precision_shortlist':None,'recall_at_150':None,'coverage':{},'fields':[]}
    if not job:return empty
    reviews=list(db.scalars(select(QualityReview).where(QualityReview.job_id==job.id)))
    done=db.get(QualityCompletion,job.id)
    pairs=list(db.scalars(select(Match).where(Match.job_id==job.id)))
    from app.db import RunSnapshot
    from types import SimpleNamespace
    snap=db.get(RunSnapshot,job.id)
    if snap and 'matches' in snap.payload:
        pairs=[SimpleNamespace(**m) for m in snap.payload['matches']]
    population=snap.payload['items'] if snap else [{'id':i.id,'kind':i.kind,'passport':i.passport} for i in db.scalars(select(Item).where(Item.session_id==sid))]
    schema=snap.payload['session'].get('schema_doc',{}) if snap else {}
    headers={(m['document_id'],m['sheet']):m.get('header_row',0) for m in schema.get('mappings',[])}
    catalog={i['id'] for i in population if i['kind']=='residual' and i.get('row_index',1)>headers.get((i.get('document_id'),i.get('sheet')),0)}
    requirements={i['id'] for i in population if i['kind']=='requirement'}
    category_names={c['id']:c['name'] for c in schema.get('categories',[])}
    by_pair={r.target_id:r for r in reviews if r.kind=='candidate'}
    groups=defaultdict(list)
    for m in pairs:groups[m.requirement_id].append(m)
    tp=fp=fn=correct=categories=fields_checked=0;field_counts=defaultdict(lambda:[0,0,0,0])
    passports=[r for r in reviews if r.kind=='passport']
    for r in passports:
        if r.target_id not in catalog:continue
        attrs=r.prediction.get('normalized_attributes',r.prediction.get('attributes',{}))
        for key,expected in r.answer.get('attributes',{}).items():
            if key=='name':continue
            actual=attrs.get(key);counts=field_counts[key];counts[3]+=1;fields_checked+=1
            if expected is not None and equal_value(actual,expected):tp+=1;counts[0]+=1
            else:
                if actual is not None:fp+=1;counts[1]+=1
                if expected is not None:fn+=1;counts[2]+=1
        if r.answer.get('category_reviewed') and r.answer.get('expected_category'):
            categories+=1
            correct+=text_key(category_names.get(r.prediction.get('category_id'),''))==text_key(r.answer['expected_category'])
        elif r.answer.get('category_reviewed') and r.answer.get('category_id') is not None:
            categories+=1;correct+=r.prediction.get('category_id')==r.answer['category_id']
    shortlist=[m for m in pairs if m.result.get('is_shortlisted')]
    checked=[m for m in shortlist if m.id in by_pair]
    suitable=sum(by_pair[m.id].answer['suitable'] for m in checked)
    full=[rows for rows in groups.values() if catalog and {m.item_id for m in rows}==catalog and all(m.id in by_pair for m in rows)]
    relevant=hits=0; query_recalls=[]
    for rows in full:
        qr=qh=0
        for m in rows:
            if by_pair[m.id].answer['suitable']:
                qr+=1
                qh+=bool(m.result.get('pre_rerank_rank',m.result.get('rank')) and 1<=m.result.get('pre_rerank_rank',m.result.get('rank'))<=150)
                relevant+=1;hits+=bool(m.result.get('pre_rerank_rank',m.result.get('rank')) and 1<=m.result.get('pre_rerank_rank',m.result.get('rank'))<=150)
        if qr:query_recalls.append(qh/qr)
    item_ids={i.id for i in db.scalars(select(Item).where(Item.session_id==sid)) if i.passport}
    from app.db import ProcurementSession,RunSnapshot
    snap=db.get(RunSnapshot,job.id)
    if snap:item_ids={i['id'] for i in snap.payload['items'] if i.get('passport')}
    stock_ids = catalog
    text_tp=text_fp=text_fn=text_reviewed=0
    for review in passports:
        if review.target_id not in stock_ids or 'name' not in review.answer.get('attributes',{}):continue
        text_reviewed+=1
        a,b,c=name_counts(review.prediction,review.answer['attributes']['name'])
        text_tp+=a;text_fp+=b;text_fn+=c
    text_den=2*text_tp+text_fp+text_fn
    enabled=snap.payload['session'].get('taxonomy_enabled',True) if snap else db.get(ProcurementSession,sid).taxonomy_enabled
    return {'taxonomy_enabled':enabled,'source':'manual','job_id':job.id,'finalized':bool(done),'finalized_at':done.finalized_at.isoformat() if done else None,
      'f1':2*tp/(2*tp+fp+fn) if done and (2*tp+fp+fn) else None,
      'text_f1':2*text_tp/text_den if done and text_den else None,
      'text_precision':text_tp/(text_tp+text_fp) if done and text_tp+text_fp else None,
      'text_recall':text_tp/(text_tp+text_fn) if done and text_tp+text_fn else None,
      'accuracy':correct/categories if done and categories and enabled else None,
      'precision_shortlist':suitable/len(checked) if done and checked else None,
      'recall_at_150':sum(query_recalls)/len(query_recalls) if done and query_recalls else None,
      'recall_micro_at_150':hits/relevant if done and relevant else None,
      'recall_stage':'before_cross_encoder' if pairs and all('pre_rerank_rank' in m.result for m in pairs) else 'legacy_after_cross_encoder',
      'coverage':{'text_reviewed':text_reviewed,'stock_total':len(stock_ids),'text_tp':text_tp,'text_fp':text_fp,'text_fn':text_fn,'passports_reviewed':len(passports),'passports_total':len(item_ids),'fields_checked':fields_checked,'tp':tp,'fp':fp,'fn':fn,'categories_checked':categories,'categories_correct':correct,'pairs_reviewed':len(by_pair),'pairs_total':len(catalog)*len(requirements),'pairs_saved':len(pairs),'shortlist_reviewed':len(checked),'shortlist_total':len(shortlist),'shortlist_suitable':suitable,'requirements_fully_reviewed':len(full),'requirements_total':len(requirements),'relevant_in_complete_requirements':relevant,'relevant_in_top150':hits},
      'fields':[{'key':k,'tp':c[0],'fp':c[1],'fn':c[2],'checked':c[3],'f1':2*c[0]/(2*c[0]+c[1]+c[2]) if done and (2*c[0]+c[1]+c[2]) else None} for k,c in field_counts.items()],
      'note':'F1: micro по явно проверенным параметрам, ошибочное значение даёт FP и FN. Accuracy: проверенные категории с заданным эталоном. Precision: проверенная часть shortlist. Recall@150: только потребности с проверкой всех пар данного запуска; не всей физической складской базы за пределами этого запуска. Неразмеченные позиции исключены.'}


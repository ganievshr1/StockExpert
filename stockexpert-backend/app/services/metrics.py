from collections import Counter,defaultdict
from datetime import timezone
from io import BytesIO
from sqlalchemy import select,func
from openpyxl import Workbook
from app.db import ProcurementSession,Item,Document,Job,Match,StageTiming,now
from app.services.execution import seconds
from app.services.quality import evaluate
from app.services.inventory import available_item_ids
from app.services.model_registry import session_config
from app.services.taxonomy import is_new,root_id

STAGES={'PARSING':'Распознавание и чтение файлов','SCHEMA_DISCOVERY':'Определение столбцов Excel',
'PASSPORT_NORMALIZATION':'Нормализация всех JSON-паспортов','TAXONOMY_BUILD':'Таксономия по всем нормализованным паспортам',
'REQUIREMENT_PARSING':'Разбор позиций потребности','EXTRACTION_CLASSIFICATION':'Извлечение, нормализация и классификация',
'VECTORIZATION':'Векторизация','HYBRID_SEARCH':'Гибридный поиск Dense + BM25', 'HARD_RULES':'Проверка технических ограничений',
'CROSS_ENCODER':'Ранжирование Cross-Encoder','PERSIST_MATCHES':'Сохранение результатов матчинга','EXPLANATIONS':'Подготовка объяснений','CANCELLING':'Ожидание остановки запросов'}
MATCH_STAGES={'VECTORIZATION','HYBRID_SEARCH','HARD_RULES','CROSS_ENCODER','PERSIST_MATCHES','EXPLANATIONS'}

def measurement(db,sid):
    s=db.get(ProcurementSession,sid);schema=s.schema_doc or {};features=schema.get('features',[])
    mappings={(m['document_id'],m['sheet']):m for m in schema.get('mappings',[])}
    items=list(db.scalars(select(Item).where(Item.session_id==sid,Item.kind=='residual')))
    eligible=[i for i in items if (i.document_id,i.sheet) in mappings and i.row_index>mappings[(i.document_id,i.sheet)]['header_row']]
    normalized=[i for i in eligible if i.passport is not None]
    issue_counts=Counter(x.get('code','UNKNOWN') for i in normalized for x in (i.issues or []))
    by_field=defaultdict(list)
    category_keys={c['id']:set(c.get('feature_keys',[])) for c in schema.get('categories',[])}
    if is_new(schema):
        for item in normalized:
            for key in category_keys.get(item.category,{'name'}):by_field[key].append(item)
    fields=[]
    for f in features:
        applicable=by_field[f['key']] if is_new(schema) else normalized
        filled=sum(i.passport.get('attributes',{}).get(f['key']) is not None for i in applicable)
        confirmed=sum(i.passport.get('attributes',{}).get(f['key']) is not None and bool(i.passport.get('evidence',{}).get(f['key'],{}).get('quote')) for i in applicable)
        fields.append({'key':f['key'],'label':f['label'],'unit':f['unit'],'filled':filled,'confirmed':confirmed,
                       'applicable_rows':len(applicable),'missing':len(applicable)-filled,'fill_rate':filled/len(applicable) if applicable else None,
                       'evidence_rate':confirmed/filled if filled else None})
    total_slots=sum(f['applicable_rows'] for f in fields);filled_total=sum(f['filled'] for f in fields)
    counts=Counter(i.category or '__unclassified__' for i in normalized)
    root_counts=Counter()
    if is_new(schema):
        roots={c['id']:root_id(schema,c['id']) for c in schema.get('categories',[])}
        for category,count in counts.items():root_counts[roots.get(category)]+=count
    categories=[{'id':c['id'],'label':c['name'],'count':root_counts[c['id']] if is_new(schema) and not c.get('parent_id') else counts.get(c['id'],0)} for c in schema.get('categories',[])]
    jobs=list(db.scalars(select(Job).where(Job.session_id==sid).order_by(Job.created_at,Job.id)))
    match_job=next((j for j in reversed(jobs) if j.kind in ('match','full') and j.status=='COMPLETED'),None)
    available=set(db.scalars(available_item_ids(db).where(Item.session_id==sid)))
    groups=defaultdict(lambda:{'before':0,'shortlisted':0,'top150':0,'retrieved':0,'passed':0,'contradicted':0,'review':0,'recommended':0,'rejected_manual':0,'known_rules_top150':0,'available_shortlisted':0})
    if match_job:
        for m in db.scalars(select(Match).where(Match.job_id==match_job.id)):
            g=groups[m.requirement_id];r=m.result
            g['before']+=1;g['shortlisted']+=bool(r.get('is_shortlisted'));g['top150']+=bool(r.get('rank') and 1<=r['rank']<=150)
            g['retrieved']+=bool(r.get('retrieved'));g['passed']+=r['state']=='PASSES_KNOWN_RULES';g['contradicted']+=r['state']=='CONTRADICTED';g['review']+=r['state']=='NEEDS_REVIEW'
            g['available_shortlisted']+=bool(r.get('is_shortlisted') and m.item_id in available)
            g['known_rules_top150']+=bool(r.get('rank') and 1<=r['rank']<=150 and r['state']=='PASSES_KNOWN_RULES')
            g['recommended']+=m.decision=='RECOMMENDED';g['rejected_manual']+=m.decision=='REJECTED_MANUAL'
    requirements=[]
    for rid,g in groups.items():
        requirements.append({'requirement_id':rid,**g,'compression_factor':g['before']/g['top150'] if g['top150'] else None,
                             'reduction_rate':1-g['top150']/g['before'] if g['before'] else None})
    segments=list(db.scalars(select(StageTiming).where(StageTiming.session_id==sid).order_by(StageTiming.started_at,StageTiming.id)))
    durations=[];totals=defaultdict(float);job_times=defaultdict(float)
    for row in segments:
        elapsed=row.elapsed_seconds if row.elapsed_seconds is not None else seconds(row.started_at,now())
        totals[row.stage]+=elapsed;job_times[row.job_id]+=elapsed
        durations.append({'id':row.id,'job_id':row.job_id,'attempt':row.attempt,'stage':row.stage,'label':STAGES.get(row.stage,row.stage),
                          'started_at':row.started_at.isoformat(),'finished_at':row.finished_at.isoformat() if row.finished_at else None,
                          'seconds':round(elapsed,3),'outcome':row.outcome})
    all_seconds=sum(totals.values())
    requirement_count=db.scalar(select(func.count()).select_from(Item).where(Item.session_id==sid,Item.kind=='requirement')) or 0
    top150=sum(g['top150'] for g in groups.values());pairs=sum(g['before'] for g in groups.values())
    reused=min((s.base_snapshot or {}).get('normalized_rows',0),len(normalized))
    legacy_counts=any(j.kind in ('full','normalize') and j.status!='QUEUED' and 'normalization_counts' not in j.params for j in jobs)
    efficiency={'ready':bool(match_job),'job_id':match_job.id if match_job else None,
        'base_copy_seconds':(s.base_snapshot or {}).get('copy_seconds'),
        'base_source_session_id':(s.base_snapshot or {}).get('source_session_id'),
        'available_stock_rows':sum(i.id in available for i in normalized),'unavailable_stock_rows':sum(i.id not in available for i in normalized),
        'available_shortlisted_pairs':sum(g['available_shortlisted'] for g in groups.values()),
        'stock_rows':len(normalized),'reused_passports':reused,'reuse_rate':reused/len(normalized) if normalized else None,
        'new_stock_passports':None if legacy_counts else sum(j.params.get('normalization_counts',{}).get('residual',0) for j in jobs),
        'new_requirement_passports':None if legacy_counts else sum(j.params.get('normalization_counts',{}).get('requirement',0) for j in jobs),
        'requirements':requirement_count,'requirements_with_candidates':sum(g['shortlisted']>0 for g in groups.values()),
        'requirement_coverage':sum(g['shortlisted']>0 for g in groups.values())/requirement_count if requirement_count and match_job else None,
        'pairs_before':pairs,'pairs_top150':top150,'shortlisted_pairs':sum(g['shortlisted'] for g in groups.values()),
        'reduction_rate':1-top150/pairs if pairs else None,'compression_factor':pairs/top150 if top150 else None,
        'contradiction_rate':sum(g['contradicted'] for g in groups.values())/pairs if pairs else None,
        'known_rules_top150':sum(g['known_rules_top150'] for g in groups.values()),
        'vectors_reused':match_job.params.get('index_stats',{}).get('vectors_reused') if match_job else None,
        'vectors_created':match_job.params.get('index_stats',{}).get('vectors_created') if match_job else None,
        'latest_job_seconds':round(job_times[match_job.id],3) if match_job and match_job.id in job_times else None,
        'session_processing_seconds':round(all_seconds,3) if segments else None,
        'note':'Операционные показатели, не F1/Accuracy/Recall. Число новых паспортов — сохранённые результаты извлечения с версии 3.3; затраченные токены и денежная экономия не оцениваются.'}
    quality=evaluate(db,sid)
    missing_reason='Нужна независимая эталонная разметка; в этой версии не подключена.'
    return {'session_id':sid,'session_title':s.title,'session_status':s.status,'generated_at':now().isoformat(),
      'quality':quality,'efficiency':efficiency,'base_snapshot':s.base_snapshot or {},
      'targets':[{'metric':'F1','label':'F1 извлечения параметров','target':0.94,'value':quality['f1'],'reason':'По проверенным параметрам после завершения проверки.'},
                 {'metric':'Accuracy','label':'Accuracy категорий','target':0.85,'value':quality['accuracy'],'reason':'По проверенным категориям после завершения проверки.'},
                 {'metric':'Recall@150','label':'Recall@150','target':0.985,'value':quality['recall_at_150'],'reason':'Только по потребностям с проверкой всех пар запуска и хотя бы одним подходящим запасом.'}],
      'extraction':{'source_files':len(list(db.scalars(select(Document.id).where(Document.session_id==sid)))),
                    'eligible_rows':len(eligible),'normalized_rows':len(normalized),'pending_rows':len(eligible)-len(normalized),
                    'normalization_rate':len(normalized)/len(eligible) if eligible else None,
                    'filled_parameters':filled_total,'parameter_slots':total_slots,
                    'fill_rate':filled_total/total_slots if total_slots else None,
                    'evidence_rate':sum(f['confirmed'] for f in fields)/filled_total if filled_total else None,
                    'fields':fields,'issues':dict(issue_counts)},
      'taxonomy':{'schema_categories':len(categories),'used_categories':sum(c['count']>0 for c in categories),
                  'classified_rows':len(normalized)-counts.get('__unclassified__',0),'unclassified_rows':counts.get('__unclassified__',0),
                  'coverage':(len(normalized)-counts.get('__unclassified__',0))/len(normalized) if normalized else None,'categories':categories,
                  'method':'All normalized JSON passports → material families from name → material-specific subcategories and feature schemas.' if is_new(schema) else 'Legacy: LLM taxonomy from source samples before normalization.',
                  'analyzed_items':schema.get('analyzed_items',0),'material_types':sum(not c.get('parent_id') for c in schema.get('categories',[]))},
      'llm':{'selected':session_config(db,s).model_dump(),'legacy':not bool(s.llm_config),
             'jobs':[{'job_id':j.id,'kind':j.kind,'status':j.status,'llm_config':j.params.get('llm_config'),'runtime':j.params.get('runtime',{})} for j in jobs]},
      'matching':{'source_job_id':match_job.id if match_job else None,'runtime':match_job.params.get('runtime',{}) if match_job else {},
                  'method':'Dense + BM25 + RRF → Hard-Rules → Cross-Encoder (if enabled). SPLADE is not implemented.',
                  'requirements':requirements,'pairs_evaluated':sum(g['before'] for g in groups.values()),
                  'shortlisted_pairs':sum(g['shortlisted'] for g in groups.values()),'top150_pairs':sum(g['top150'] for g in groups.values()),
                  'contradicted_pairs':sum(g['contradicted'] for g in groups.values()),'needs_review_pairs':sum(g['review'] for g in groups.values())},
      'timing':{'measured':bool(segments),'processing_seconds':round(all_seconds,3),
                'parsing_seconds':round(totals.get('PARSING',0)+totals.get('REQUIREMENT_PARSING',0),3),
                'normalization_seconds':round(totals.get('EXTRACTION_CLASSIFICATION',0)+totals.get('PASSPORT_NORMALIZATION',0),3),
                'taxonomy_seconds':round(totals.get('TAXONOMY_BUILD',0) if is_new(schema) else totals.get('SCHEMA_DISCOVERY',0),3),
                'matching_seconds':round(sum(totals.get(k,0) for k in MATCH_STAGES),3),
                'normalization_rows_per_second':len(normalized)/totals['EXTRACTION_CLASSIFICATION'] if totals.get('EXTRACTION_CLASSIFICATION') else None,
                'stages':[{'stage':k,'label':STAGES.get(k,k),'seconds':round(v,3)} for k,v in totals.items()],
                'attempts':len({d['attempt'] for d in durations}),'segments':durations,
                'jobs':[{'job_id':j.id,'kind':j.kind,'status':j.status,'seconds':round(job_times[j.id],3) if j.id in job_times else None} for j in jobs],
                'note':'Sum of measured wall-clock stage intervals across all attempts, including failures/cancellation; concurrent model requests are not added together. Old 3.1 runs have no timings.'}}

def add_sheets(wb,data):
    sid=data['session_id'];ws=wb.create_sheet('Метрики сессии')
    ws.append(['ID сессии','Раздел','Метрика','Значение','Цель / пояснение'])
    ws.append([sid,'Сессия','Название',data['session_title'],''])
    for key,value in data['llm']['selected'].items():ws.append([sid,'Модель сессии',key,value,''])
    for t in data['targets']:ws.append([sid,'Качество',t['metric'],t['value'] if t['value'] is not None else 'Не измерено',f"Цель {t['target']:.1%}. {t['reason']}"])
    q=data['quality']
    for key in ('job_id','finalized','finalized_at','f1','accuracy','precision_shortlist','recall_at_150'):ws.append([sid,'Экспертная оценка',key,q.get(key),'Доля 0–1; только проверенная выборка'])
    for key,value in q['coverage'].items():ws.append([sid,'Объём проверки',key,value,''])
    for f in q['fields']:ws.append([sid,'F1 параметра',f['key'],f['f1'],f"TP={f['tp']}; FP={f['fp']}; FN={f['fn']}; проверено={f['checked']}"])
    for section in ('efficiency','extraction','taxonomy','matching'):
        for key,value in data[section].items():
            if isinstance(value,(str,int,float)) or value is None:ws.append([sid,section,key,value,''])
    for f in data['extraction']['fields']:
        for key in ('filled','confirmed','missing','fill_rate','evidence_rate'):ws.append([sid,'Параметры',f"{f['label']} / {key}",f[key],f['key']])
    for c in data['taxonomy']['categories']:ws.append([sid,'Категории',c['label'],c['count'],c['id']])
    for key in ('processing_seconds','parsing_seconds','taxonomy_seconds','normalization_seconds','matching_seconds','attempts'):
        ws.append([sid,'Время',key,data['timing'][key] if data['timing']['measured'] else None,'секунды' if key.endswith('seconds') else ''])
    t=wb.create_sheet('Время этапов');t.append(['ID сессии','ID задания','Попытка','Этап','Начало UTC','Окончание UTC','Секунды','Исход'])
    for r in data['timing']['segments']:t.append([sid,r['job_id'],r['attempt'],r['label'],r['started_at'],r['finished_at'],r['seconds'],r['outcome']])
    p=wb.create_sheet('Сжатие кандидатов');p.append(['ID сессии','Потребность ID','До отбора','Найдено поиском','Shortlist','Top 150','Сжатие раз','Сокращение доля','Противоречия','Нужна проверка'])
    for r in data['matching']['requirements']:p.append([sid,r['requirement_id'],r['before'],r['retrieved'],r['shortlisted'],r['top150'],r['compression_factor'],r['reduction_rate'],r['contradicted'],r['review']])
    for sheet in (ws,t,p):
        sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
        for row in sheet:
            for cell in row:
                # Source-derived strings must never be spreadsheet formulas.
                if isinstance(cell.value,str):cell.data_type='s'
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width=min(70,max(16,max(len(str(c.value or '')) for c in column)+2))

def workbook(data):
    wb=Workbook();wb.remove(wb.active);add_sheets(wb,data);out=BytesIO();wb.save(out);return out.getvalue()


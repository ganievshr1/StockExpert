from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextvars import copy_context
import json,hashlib,re
from sqlalchemy import select,delete
from app.db import SessionLocal, ProcurementSession, Document, Item, Job, Match
from app.services import llm,storage,search,passports,taxonomy,requirement_extraction
from app.services.inventory import ensure_inventory,available_item_ids
from app.services.parsing import sheets,document_text,json_row
from app.services.contracts import SchemaDraft, Extraction, Requirements, ColumnMapping
from app.services.normalization import normalized,compare,explanation
from app.services.audit import record
from app.core.config import settings

def prepare(db,s,job,tick):
    docs=list(db.scalars(select(Document).where(Document.session_id==s.id)))
    samples=[];req_text=[]
    for d in docs:
        tick('PARSING')
        data=storage.get(d.object_key)
        if d.kind=='residual':
            if s.base_snapshot:
                # A copied base contains only available stock. Reading all rows
                # from its original XLSX would resurrect consumed positions.
                grouped={}
                for item in db.scalars(select(Item).where(Item.document_id==d.id).order_by(Item.row_index)):
                    grouped.setdefault(item.sheet,[]).append({'row_index':item.row_index,'cells':item.raw})
                source_sheets=grouped.items()
            else:
                source_sheets=sheets(data)
            for sheet,rows in source_sheets:
                samples.append({'document_id':d.id,'sheet':sheet,'first_rows':rows[:10]})
                for row in rows:
                    if row['row_index'] % 100 == 0:tick('PARSING')
                    existing=db.scalar(select(Item).where(Item.document_id==d.id,Item.sheet==sheet,Item.row_index==row['row_index']))
                    if not existing: db.add(Item(session_id=s.id,document_id=d.id,kind='residual',sheet=sheet,row_index=row['row_index'],raw=row['cells']))
        else:
            text=document_text(data,d.filename)
            d.meta={'text':text}
            req_text.append({'document_id':d.id,'text':text})
        db.commit();tick('PARSING')
    if not samples or not req_text: raise ValueError('Upload residuals and requirement first')
    # Previews are used ONLY for locating columns. H2 sees every normalized row later.
    mappings=[]
    for sample in samples:
        if len(json.dumps(sample,ensure_ascii=False))>14000:raise ValueError('Column mapping context too large; check the first rows of this sheet')
        tick('SCHEMA_DISCOVERY')
        result=ColumnMapping.model_validate(llm.structured(
            'Определи только расположение данных одного листа Excel. Категории и признаки не создавай. '
            'Индексы столбцов — строки с номером от 0. header_row=0, если заголовка нет. '
            'name_columns включают наименование, описание и все технические поля, необходимые для паспорта. '
            'Не включай складской номер и цену без необходимости. category_column можно указать как контекст; '
            'готовая классификация Excel не определяет будущую таксономию.',sample,ColumnMapping.model_json_schema())).model_dump()
        mappings.append({'document_id':sample['document_id'],'sheet':sample['sheet'],**result})
    draft=SchemaDraft(mappings=mappings,taxonomy_mode='passports').model_dump()
    tick('SCHEMA_DISCOVERY');validate_mapping(db,s.id,draft)
    s.schema_doc=draft;s.status='REVIEW_REQUIRED';s.progress=20
    record(db,s.id,'SCHEMA_PROPOSED',{'job_id':job.id,'model':llm.current_config().model,'llm_config':llm.current_config().model_dump(),'schema':draft});db.commit()

def validate_mapping(db,sid,schema):
    documents=list(db.scalars(select(Document).where(Document.session_id==sid,Document.kind=='residual')))
    required={(d.id,i.sheet) for d in documents for i in db.scalars(select(Item).where(Item.document_id==d.id))}
    actual=[(m['document_id'],m['sheet']) for m in schema['mappings']]
    if set(actual)!=required or len(actual)!=len(set(actual)): raise ValueError('Mapping must cover each Excel sheet once')
    for m in schema['mappings']:
        rows=list(db.scalars(select(Item).where(Item.document_id==m['document_id'],Item.sheet==m['sheet'])))
        cols=set().union(*(set(i.raw) for i in rows))
        # Accept Excel letters from any model, then validate the resolved column.
        def column(value):
            text=str(value).strip()
            if text in cols:return text
            if re.fullmatch(r'[A-Za-z]+',text):
                number=0
                for char in text.upper():number=number*26+ord(char)-ord('A')+1
                return str(number-1)
            return text
        m['name_columns']=[column(c) for c in m['name_columns']]
        if m.get('category_column') is not None:m['category_column']=column(m['category_column'])
        if not set(m['name_columns'])<=cols or (m.get('category_column') is not None and m['category_column'] not in cols): raise ValueError(f'Unknown mapped column: sheet={m["sheet"]}; requested={m["name_columns"]}, category={m.get("category_column")}; available={sorted(cols)} (zero-based indexes)')
        if not any(i.row_index>m['header_row'] for i in rows): raise ValueError('No data after header')

def extract_one(text,schema):
    if taxonomy.is_new(schema):return passports.extract(text)
    for attempt in range(1,settings.NORMALIZATION_MAX_ATTEMPTS+1):
        try:
            return extract_legacy(text,schema,attempt)
        except (ValueError,llm.LLMResponseError):
            continue
    passport,issues=passports.source_fallback(text)
    passport['normalization']['attempts']=attempt
    issues.append({'code':'NORMALIZATION_NEEDS_REVIEW','attempts':attempt,
                   'message':'Не удалось извлечь свойства по прежней схеме. Сохранён исходный текст.'})
    return passport,issues

def extract_legacy(text,schema,attempt=1):
    schema_hash=hashlib.sha256(json.dumps(schema,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    result=llm.structured('Извлеки только явно указанные свойства по схеме. value — строка с исходным значением, unit — исходная единица, evidence — точная цитата. Отсутствующие значения null. Категория только из предложенного списка. Не расшифровывай неизвестные марки. Для кабеля запись NхS означает число жил N и сечение S мм2; не извлекай температуру из марки. Не приравнивай наружный диаметр к DN.',{'source':text,'features':schema['features'],'categories':schema['categories'],
        'repair_instruction':'Повтори извлечение: только дословные цитаты и допустимые ключи схемы.' if attempt>1 else None},Extraction.model_json_schema())
    passport,issues=normalized(result,text,schema)
    config=llm.current_config()
    passport['provenance']={'provider':config.provider,'model':config.model,'profile_id':config.profile_id,'llm_config':config.model_dump(),'llm_fingerprint':config.fingerprint(),'prompt_version':'extraction-v1','schema_hash':schema_hash,'raw_response':result}
    return passport,issues

def normalize_all(db,s,job,tick):
    if not (s.schema_doc.get('mapping_approved') if taxonomy.is_new(s.schema_doc) else s.approved):
        raise ValueError('Approve column mapping before normalization')
    for d in db.scalars(select(Document).where(Document.session_id==s.id,Document.kind=='requirement')):
        if db.scalar(select(Item).where(Item.document_id==d.id)): continue
        tick('REQUIREMENT_PARSING')
        text=d.meta['text']
        parts,split_issues=requirement_extraction.split(text)
        tick('REQUIREMENT_PARSING')
        for idx,part in enumerate(parts):
            if not part.strip() or part not in text: raise ValueError('Requirement item not grounded in source')
            db.add(Item(session_id=s.id,document_id=d.id,kind='requirement',row_index=idx+1,raw={'text':part},source_text=part,issues=split_issues))
        db.commit()
    documents={d.id:d for d in db.scalars(select(Document).where(Document.session_id==s.id))}
    items=list(db.scalars(select(Item).where(Item.session_id==s.id).order_by(Item.id)))
    mappings={(m['document_id'],m['sheet']):m for m in s.schema_doc['mappings']}
    headers={(i.document_id,i.sheet):i.raw for i in items if i.kind=='residual' and i.row_index==mappings[(i.document_id,i.sheet)]['header_row']}
    stage='PASSPORT_NORMALIZATION' if taxonomy.is_new(s.schema_doc) else 'EXTRACTION_CLASSIFICATION'
    tick(stage)
    pending_items=[]
    for item in items:
        if item.kind=='residual' and item.passport is not None:
            continue
        if item.kind=='residual':
            m=mappings[(item.document_id,item.sheet)]
            if item.row_index<=m['header_row']: continue
            keys=list(m['name_columns'])
            if m.get('category_column') and m['category_column'] not in keys: keys.append(m['category_column'])
            labels=headers.get((item.document_id,item.sheet),{})
            item.source_text=' | '.join(f'{labels[k]}: {item.raw[k]}' if k in labels else item.raw[k] for k in keys if k in item.raw)
            if not item.source_text.strip():
                item.source_text=json_row(item.raw)
                item.issues=[{'code':'EMPTY_MAPPED_SOURCE','message':'Выбранные столбцы пусты. Сохранена вся исходная строка; проверьте сопоставление столбцов.'}]
        if item.passport is None: pending_items.append(item)
    db.commit()
    # Only model calls run in threads. SQLAlchemy objects are accessed in this thread only.
    workers=llm.current_config().concurrency
    completed=0
    iterator=iter(pending_items)
    executor=ThreadPoolExecutor(max_workers=workers)
    futures={}
    def submit_next():
        tick(stage)
        item=next(iterator,None)
        if item is not None:
            if item.issues and any(x['code'] in ('EMPTY_MAPPED_SOURCE','REQUIREMENT_SPLIT_NEEDS_REVIEW') for x in item.issues):
                futures[executor.submit(copy_context().run,passports.source_fallback,item.source_text)]=item
            else:
                futures[executor.submit(copy_context().run,extract_one,item.source_text,s.schema_doc)]=item
    try:
        for _ in range(workers): submit_next()
        while futures:
            done,_=wait(futures,timeout=10,return_when=FIRST_COMPLETED)
            tick(stage)
            for future in done:
                tick(stage)
                item=futures.pop(future)
                passport,issues=future.result()
                document=documents[item.document_id]
                location={'document_id':document.id,'document':document.filename,'sheet':item.sheet,
                          'row':item.row_index,'row_kind':'excel_row' if item.kind=='residual' else 'requirement_position'}
                if item.kind=='requirement' and item.source_text in document.meta.get('text',''):
                    location['source_line']=document.meta['text'][:document.meta['text'].index(item.source_text)].count('\n')+1
                passport['source_location']=location
                item.passport=passport;item.category=passport['category_id']
                item.issues=[{**issue,**location} for issue in [*(item.issues or []),*issues]]
                counts=dict(job.params.get('normalization_counts',{}));counts[item.kind]=counts.get(item.kind,0)+1
                job.params={**job.params,'normalization_counts':counts}
                completed+=1
                s.progress=20+int(45*completed/max(1,len(pending_items)))
                db.commit()
                submit_next()
    finally:
        # Stop scheduling after failure; keep the lease alive while outstanding calls finish.
        for future in futures: future.cancel()
        outstanding=set(futures)
        while outstanding:
            _,outstanding=wait(outstanding,timeout=10)
            if outstanding: getattr(tick,'drain',tick)(stage)
        executor.shutdown(wait=True,cancel_futures=True)
    tick(stage)
    job.params={**job.params,'normalization_review_count':sum(passports.needs_normalization_review(i.passport) for i in items)}
    ensure_inventory(db,s)
    if taxonomy.is_new(s.schema_doc):
        taxonomy.build(db,s,job,tick)
        if not s.approved:return
    s.status='NORMALIZED';s.progress=65
    record(db,s.id,'NORMALIZED',{'job_id':job.id,'model':llm.current_config().model,'llm_config':llm.current_config().model_dump()});db.commit()

def match_all(db,s,job,tick):
    if taxonomy.is_new(s.schema_doc) and not s.approved:raise ValueError('Approve the passport taxonomy before matching')
    ensure_inventory(db,s);db.commit()
    items=list(db.scalars(select(Item).where(Item.session_id==s.id,Item.kind=='residual',Item.passport['attributes'].as_string().is_not(None),Item.id.in_(available_item_ids(db)))))
    reqs=list(db.scalars(select(Item).where(Item.session_id==s.id,Item.kind=='requirement',Item.passport['attributes'].as_string().is_not(None))))
    if not items or not reqs: raise ValueError('Нет доступных нормализованных остатков или потребностей. Запасы могли быть использованы либо база очищена.')
    llm.unload();tick('VECTORIZATION');index_stats=search.index_items(s.id,items,lambda:tick('VECTORIZATION'))
    job.params={**job.params,'index_stats':index_stats};db.commit()
    s.progress=75;db.commit()
    root_map={c['id']:taxonomy.root_id(s.schema_doc,c['id']) for c in s.schema_doc['categories']}
    for req in reqs:
        tick('HYBRID_SEARCH')
        fused=search.retrieve(s.id,req.source_text,items,min(500,len(items)))
        tick('HARD_RULES')
        features=taxonomy.features_for(s.schema_doc,req.category)
        comparisons={i.id:compare(req.passport['attributes'],i.passport['attributes'],features) for i in items}
        for i in items:
            if taxonomy.is_new(s.schema_doc):
                req_root=root_map.get(req.category);item_root=root_map.get(i.category)
                if req_root and item_root and req_root!=item_root and not (passports.needs_review(req.passport) or passports.needs_review(i.passport)):
                    comparisons[i.id]={'state':'CONTRADICTED','diff':[{'field':'material','label':'Тип материала','required':req_root,'actual':item_root,'unit':'none','operator':'eq','tolerance':0}],'missing_fields':[],'category_mismatch':True}
                    continue
                if any(x['code']=='UNMAPPED_REQUIREMENT_FIELD' for x in req.issues):
                    if comparisons[i.id]['state']!='CONTRADICTED':comparisons[i.id]['state']='NEEDS_REVIEW'
                    comparisons[i.id]['missing_fields'].append('requirement:unmapped_attributes')
            if passports.needs_review(req.passport) or passports.needs_review(i.passport):
                if comparisons[i.id]['state']!='CONTRADICTED':comparisons[i.id]['state']='NEEDS_REVIEW'
                comparisons[i.id]['normalization_review']=True
                comparisons[i.id]['missing_fields'].append('normalization:source_review')
            if not req.category or not i.category or req.category!=i.category:
                if comparisons[i.id]['state']!='CONTRADICTED': comparisons[i.id]['state']='NEEDS_REVIEW'
                comparisons[i.id]['category_review']=True
        candidates=[i for i in items if i.id in fused and comparisons[i.id]['state']!='CONTRADICTED']
        tick('CROSS_ENCODER');ce=search.rerank(req.source_text,candidates)
        ordered=sorted(candidates,key=lambda i:(ce.get(i.id,fused[i.id]),fused[i.id]),reverse=True)
        rank={i.id:n for n,i in enumerate(ordered,1)}
        limit=job.params.get('target_shortlist_size',150)
        tick('PERSIST_MATCHES')
        # Replace only this run/requirement on redelivery; previous runs are retained.
        db.execute(delete(Match).where(Match.job_id==job.id,Match.requirement_id==req.id))
        for index,item in enumerate(items):
            if index % 100 == 0:tick('PERSIST_MATCHES')
            result=comparisons[item.id]
            result.update({'rrf_score':fused.get(item.id),'cross_encoder_score':ce.get(item.id),'rank':rank.get(item.id),'is_shortlisted':rank.get(item.id,10**9)<=limit,'retrieved':item.id in fused})
            result['reason']=explanation(result)
            db.add(Match(session_id=s.id,job_id=job.id,requirement_id=req.id,item_id=item.id,result=result))
        db.commit()
    tick('EXPLANATIONS');s.status='MATCHED';s.progress=100
    record(db,s.id,'MATCHED',{'job_id':job.id,'retrieval':'dense+BM25+RRF','reranker':settings.RERANKER_MODEL if settings.RERANKER_ENABLED else None,'explanations':'deterministic templates; not legal opinion'})
    db.commit()


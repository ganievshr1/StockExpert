"""Independent snapshots of approved, fully normalized stock. No LLM calls."""
from copy import deepcopy
from time import perf_counter
from pathlib import Path
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import select,func
from app.db import ProcurementSession,Document,Item,Job,StockPosition,now
from app.api.deps import own_session
from app.services.execution import ACTIVE_STATUSES
from app.services import storage
from app.services.model_registry import session_config
from app.services.inventory import ensure_inventory
from app.services.audit import record

def normalized_rows(db,s):
    mappings={(m['document_id'],m['sheet']):m for m in s.schema_doc.get('mappings',[])}
    rows=list(db.scalars(select(Item).where(Item.session_id==s.id,Item.kind=='residual')))
    eligible=[i for i in rows if (i.document_id,i.sheet) in mappings and i.row_index>mappings[(i.document_id,i.sheet)]['header_row'] and i.source_text.strip()]
    if not eligible or any(not isinstance(i.passport,dict) or not isinstance(i.passport.get('attributes'),dict) for i in eligible):
        raise HTTPException(409,'База ещё не нормализована полностью. Завершите нормализацию исходной сессии.')
    return rows,eligible

def attach_base(db,target,source_id,user):
    started=perf_counter()
    source=own_session(db,source_id,user,lock=True)
    if not source.approved:raise HTTPException(409,'Выберите базу с утверждённой схемой.')
    if db.scalar(select(Job.id).where(Job.session_id==source_id,Job.status.in_(ACTIVE_STATUSES))):
        raise HTTPException(409,'Дождитесь завершения обработки исходной базы.')
    rows,eligible=normalized_rows(db,source)
    ensure_inventory(db,source)
    available=set(db.scalars(select(StockPosition.id).join(Item,Item.stock_id==StockPosition.id).where(Item.session_id==source.id,StockPosition.state=='AVAILABLE')))
    eligible=[i for i in eligible if i.stock_id in available]
    if not eligible:raise HTTPException(409,'В выбранной базе не осталось доступных позиций')
    source_config=session_config(db,source)
    target_config=session_config(db,target)
    reuse=source_config.fingerprint()==target_config.fingerprint()
    # A known conflicting extractor in a passport always prevents mixed-model reuse.
    for item in eligible:
        provenance=(item.passport or {}).get('provenance',{})
        if not source.llm_config and not (provenance.get('provider') and provenance.get('model')):
            reuse=False  # An unrecorded historic extractor cannot be verified.
        if provenance.get('model') and (provenance.get('model')!=target_config.model or provenance.get('provider')!=target_config.provider):
            reuse=False
        if provenance.get('llm_fingerprint') and provenance['llm_fingerprint']!=target_config.fingerprint():
            reuse=False
    active_sheets={(i.document_id,i.sheet) for i in eligible}
    rows=[i for i in rows if (i.document_id,i.sheet) in active_sheets and (i.stock_id is None or i.stock_id in available)]
    docs=list(db.scalars(select(Document).where(Document.session_id==source_id,Document.kind=='residual')))
    copied=[];doc_map={}
    try:
        for d in docs:
            key=f'{target.organization_id}/{target.id}/{uuid4()}{Path(d.filename).suffix.lower()}'
            # Track before PUT, which may fail after the object was accepted.
            copied.append(key);storage.put(key,storage.get(d.object_key))
            new=Document(session_id=target.id,kind='residual',filename=d.filename,object_key=key,sha256=d.sha256,meta={**deepcopy(d.meta),'reused_from_session':source_id})
            db.add(new);db.flush();doc_map[d.id]=new.id
        for old in rows:
            passport=deepcopy(old.passport) if reuse else None
            if passport is not None:
                passport['reuse']={'session_id':source_id,'item_id':old.id}
            db.add(Item(session_id=target.id,document_id=doc_map[old.document_id],kind='residual',stock_id=old.stock_id,sheet=old.sheet,row_index=old.row_index,raw=deepcopy(old.raw),source_text=old.source_text,passport=passport,category=old.category if reuse else None,issues=deepcopy(old.issues) if reuse else []))
        schema=deepcopy(source.schema_doc)
        schema['mappings']=[m for m in schema['mappings'] if (m['document_id'],m['sheet']) in active_sheets]
        for mapping in schema['mappings']:mapping['document_id']=doc_map[mapping['document_id']]
        if reuse and schema.get('taxonomy_mode')=='passports':
            for category in schema['categories']:
                category['item_count']=sum(i.category==category['id'] or (category['parent_id'] is None and i.passport.get('material_id')==category['id']) for i in eligible)
                category['requirement_count']=0
            schema['analyzed_items']=len(eligible)
        target.schema_doc=schema if reuse else {};target.approved=reuse;target.taxonomy_version=source.taxonomy_version if reuse else None;target.status='BASE_READY' if reuse else 'UPLOADED';target.progress=0
        target.base_snapshot={'source_session_id':source.id,'source_title':source.title,'normalized_rows':len(eligible) if reuse else 0,'source_rows':len(eligible),'reused_passports':reuse,'source_llm_config':source_config.model_dump(),'copied_at':now().isoformat(),'taxonomy_version':source.taxonomy_version,'copy_seconds':round(perf_counter()-started,3)}
        record(db,target.id,'NORMALIZED_BASE_REUSED' if reuse else 'BASE_SOURCES_COPIED_FOR_MODEL',target.base_snapshot,user.id)
        db.flush()
    except Exception:
        for key in copied:
            try:storage.delete_key(key)
            except Exception:pass
        raise


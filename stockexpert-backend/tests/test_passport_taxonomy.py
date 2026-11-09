"""Behavior checks for all-row taxonomy, scoped attributes, and resumable runs."""
from copy import deepcopy
from io import BytesIO
import re
import pytest
from openpyxl import Workbook
from sqlalchemy import select
from app.db import SessionLocal,ProcurementSession,Item,Job,Match
from app.services import workflow,llm,storage,search,taxonomy,passports
from app.services.contracts import SchemaDraft

API='/api/v1'

@pytest.fixture
def mixed(client,auth,monkeypatch):
    objects={};calls={'extract':[],'taxonomy':[],'resolve':[]}
    monkeypatch.setattr(storage,'put',lambda k,v,*a,**kw:objects.__setitem__(k,v))
    monkeypatch.setattr(storage,'get',lambda k:objects[k])
    def model(task,data,schema):
        props=schema['properties']
        if 'header_row' in props:return {'header_row':1,'name_columns':['A'],'category_column':None}
        if 'name_evidence' in props:
            source=data['source'];name=source.removeprefix('Описание: ');calls['extract'].append(name)
            bolt=name.startswith(('Болт','Болты'));value=re.search(r'\d+(?=мм)' if bolt else r'\d+(?=В)',name)[0]
            return {'name':name,'name_evidence':name,'values':[{'key':'size','label':'Диаметр' if bolt else 'Напряжение','kind':'number','value':value,'unit':'мм' if bolt else 'В','target_unit':'mm' if bolt else 'V','evidence':value+('мм' if bolt else 'В')}]}
        if 'passports' in data:
            calls['taxonomy'].extend(data['passports'])
            result=[]
            for x in data['passports']:
                family=x['name'].split()[0];sub='Насос погружной' if 'погружной' in x['name'] else None
                result.append({'item_id':x['item_id'],'material':family,'subtype':sub})
            return {'items':result}
        if 'match_id' in props:
            calls['resolve'].append(data)
            synonyms={'Болты':'Болт','Помпа':'Насос'}
            name=synonyms.get(data['name'],data['name'])
            match=next((x['id'] for x in data['catalog'] if synonyms.get(x['name'],x['name'])==name),None)
            return {'match_id':match}
        return {'items':[data['text']]}
    monkeypatch.setattr(llm,'structured',model)
    sid=client.post(API+'/procurement/sessions',headers=auth,json={'title':'Mixed stock','taxonomy_enabled':True}).json()['id']
    names=[f'Насос погружной P{x} 220В' for x in range(14)]+['Болт М10 10мм','Болты М10 10мм','Помпа A 220В','Датчик D 24В','Датчик D 24В']
    wb=Workbook();wb.active.append(['Описание'])
    for n in names:wb.active.append([n])
    out=BytesIO();wb.save(out)
    assert client.post(f'{API}/procurement/sessions/{sid}/upload-residuals',headers=auth,files={'file':('stock.xlsx',out.getvalue())}).status_code==201
    assert client.post(f'{API}/procurement/sessions/{sid}/upload-requirement',headers=auth,files={'file':('need.txt','Насос погружной R 220В'.encode())}).status_code==201
    return sid,names,calls,model


def execute(client,auth,sid,path,function):
    # These integration fixtures exercise H1 + H2 together. Explicit H1-only
    # behavior is covered separately in test_optional_taxonomy.
    if function is workflow.normalize_all and path.endswith('/start-normalization'):
        path=f'pipeline/{sid}/run'
    response=client.post(f'{API}/{path}',headers=auth,json={});assert response.status_code==202,response.text
    with SessionLocal() as db:
        s=db.get(ProcurementSession,sid);j=db.get(Job,response.json()['task_id'])
        function(db,s,j,lambda stage:None);j.status='COMPLETED';db.commit()
    return response.json()['task_id']


def ready(client,auth,mixed):
    sid,_,_,_=mixed
    execute(client,auth,sid,f'pipeline/{sid}/prepare',workflow.prepare)
    assert client.post(f'{API}/pipeline/{sid}/approve-mapping',headers=auth).status_code==200
    execute(client,auth,sid,f'pipeline/{sid}/start-normalization',workflow.normalize_all)
    return client.get(f'{API}/pipeline/{sid}/schema',headers=auth).json()


def test_all_positions_form_material_trees_after_normalization(client,auth,mixed):
    sid,names,calls,_=mixed
    execute(client,auth,sid,f'pipeline/{sid}/prepare',workflow.prepare)
    schema=client.get(f'{API}/pipeline/{sid}/schema',headers=auth).json()
    assert schema['categories']==[] and schema['features']==[] and not calls['taxonomy']
    assert client.post(f'{API}/taxonomy/{sid}/approve',headers=auth,json={'freeze_version':'v1'}).status_code==409
    assert client.post(f'{API}/pipeline/{sid}/start-normalization',headers=auth,json={}).status_code==409
    assert client.post(f'{API}/pipeline/{sid}/approve-mapping',headers=auth).status_code==200
    execute(client,auth,sid,f'pipeline/{sid}/start-normalization',workflow.normalize_all)
    schema=client.get(f'{API}/pipeline/{sid}/schema',headers=auth).json()
    roots=[c for c in schema['categories'] if c['parent_id'] is None]
    assert len(roots)==3
    assert {alias for c in roots for alias in c['aliases']}=={'Насос','Помпа','Болт','Болты','Датчик'}
    assert schema['analyzed_items']==len(names)+1
    assert sum(c['item_count'] for c in roots)==len(names)
    assert len(calls['extract'])==len(names)+1
    assert sum(x['source_rows'] for x in calls['taxonomy'])==len(names)+1
    assert any(x['name']=='Датчик D 24В' and x['source_rows']==2 for x in calls['taxonomy'])
    with SessionLocal() as db:
        rows=list(db.scalars(select(Item).where(Item.session_id==sid,Item.passport.is_not(None))))
        assert len(rows)==len(names)+1
        assert all(i.category in {c['id'] for c in schema['categories']} for i in rows)
        bolt=next(i for i in rows if i.passport['name']=='Болт М10 10мм')
        pump=next(i for i in rows if i.passport['name'].startswith('Насос'))
        bolt_fields={f['label'] for f in taxonomy.features_for(schema,bolt.category)}
        pump_fields={f['label'] for f in taxonomy.features_for(schema,pump.category)}
        assert 'Диаметр' in bolt_fields and 'Напряжение' not in bolt_fields
        assert 'Напряжение' in pump_fields and 'Диаметр' not in pump_fields
        assert set(bolt.passport['attributes'])&set(pump.passport['attributes'])=={'name'}
    root=next(c for c in roots if 'Насос' in c['aliases'])
    page=client.get(f'{API}/taxonomy/{sid}/categories/{root["id"]}/items',headers=auth).json()
    assert page['total']==16  # 14 pumps, the synonymous pump, and one requirement
    metrics=client.get(f'{API}/metrics/{sid}',headers=auth).json()
    diameter=next(f for f in metrics['extraction']['fields'] if f['label']=='Диаметр')
    assert diameter['applicable_rows']==2 and diameter['missing']==0
    assert metrics['extraction']['fill_rate']==1


def test_scoped_matching_and_safe_schema_review(client,auth,mixed,monkeypatch):
    sid,names,_,_=mixed;schema=ready(client,auth,mixed)
    old=deepcopy(schema);schema['features'][1]['unit']='kg'
    assert client.put(f'{API}/pipeline/{sid}/schema',headers=auth,json=schema).status_code==422
    schema=deepcopy(old);schema['categories'][0]['id']='invented'
    assert client.put(f'{API}/pipeline/{sid}/schema',headers=auth,json=schema).status_code==422
    schema=deepcopy(old);schema['mapping_approved']=False
    assert client.put(f'{API}/pipeline/{sid}/schema',headers=auth,json=schema).status_code==422
    schema=deepcopy(old)
    for f in schema['features']:
        if f['key']!='name':f['comparison']='eq'
    assert client.put(f'{API}/pipeline/{sid}/schema',headers=auth,json=schema).status_code==200
    assert client.post(f'{API}/taxonomy/{sid}/approve',headers=auth,json={'freeze_version':'v2'}).status_code==200
    monkeypatch.setattr(llm,'unload',lambda:None)
    monkeypatch.setattr(search,'index_items',lambda sid,items,heartbeat:{})
    monkeypatch.setattr(search,'retrieve',lambda sid,query,items,limit:{i.id:1. for i in items})
    monkeypatch.setattr(search,'rerank',lambda *args:{})
    execute(client,auth,sid,f'matching/{sid}/run',workflow.match_all)
    with SessionLocal() as db:
        matches=list(db.scalars(select(Match).where(Match.session_id==sid)))
        assert len(matches)==len(names)
        for m in matches:
            item=db.get(Item,m.item_id)
            if item.passport['name'].startswith(('Болт','Датчик')):
                assert m.result['state']=='CONTRADICTED' and not m.result['is_shortlisted']
            if item.passport['name'].startswith('Насос'):
                assert m.result['state']=='PASSES_KNOWN_RULES'
    assert client.post(f'{API}/taxonomy/{sid}/rebuild',headers=auth).status_code==409


def test_retry_reuses_passports_and_completed_classification_batches(client,auth,mixed,monkeypatch):
    sid,names,calls,model=mixed
    execute(client,auth,sid,f'pipeline/{sid}/prepare',workflow.prepare)
    client.post(f'{API}/pipeline/{sid}/approve-mapping',headers=auth)
    batches=0
    def flaky(task,data,schema):
        nonlocal batches
        if 'passports' in data:
            batches+=1
            if batches==2:raise RuntimeError('simulated LLM failure')
        return model(task,data,schema)
    monkeypatch.setattr(llm,'structured',flaky)
    jid=client.post(f'{API}/pipeline/{sid}/run',headers=auth,json={}).json()['task_id']
    with SessionLocal() as db:
        s=db.get(ProcurementSession,sid);j=db.get(Job,jid)
        with pytest.raises(RuntimeError,match='simulated'):workflow.normalize_all(db,s,j,lambda _:None)
        db.rollback()
    assert len(calls['extract'])==len(names)+1
    with SessionLocal() as db:
        rows=list(db.scalars(select(Item).where(Item.session_id==sid,Item.passport.is_not(None))))
        assert len(rows)==len(names)+1
        assert any(i.passport.get('taxonomy_proposal') for i in rows)
        assert db.get(ProcurementSession,sid).schema_doc['categories']==[]
    monkeypatch.setattr(llm,'structured',model)
    with SessionLocal() as db:
        s=db.get(ProcurementSession,sid);j=db.get(Job,jid)
        workflow.normalize_all(db,s,j,lambda _:None)
        assert s.schema_doc['analyzed_items']==len(names)+1
    assert len(calls['extract'])==len(names)+1
    assert sum(x['source_rows'] for x in calls['taxonomy'])==len(names)+1


def test_bad_classification_cannot_publish_partial_taxonomy(client,auth,mixed,monkeypatch):
    sid,_,_,model=mixed
    execute(client,auth,sid,f'pipeline/{sid}/prepare',workflow.prepare)
    client.post(f'{API}/pipeline/{sid}/approve-mapping',headers=auth)
    def omit(task,data,schema):
        result=model(task,data,schema)
        if 'passports' in data:result['items']=result['items'][:-1]
        return result
    monkeypatch.setattr(llm,'structured',omit)
    jid=client.post(f'{API}/pipeline/{sid}/run',headers=auth,json={}).json()['task_id']
    with SessionLocal() as db:
        workflow.normalize_all(db,db.get(ProcurementSession,sid),db.get(Job,jid),lambda _:None)
        session=db.get(ProcurementSession,sid)
        assert session.status=='TAXONOMY_REVIEW'
        rows=list(db.scalars(select(Item).where(Item.session_id==sid,Item.passport['attributes'].as_string().is_not(None))))
        assert session.schema_doc['analyzed_items']==len(rows)
        assert all(i.category for i in rows)
        review=[i for i in rows if i.passport.get('classification',{}).get('requires_review')]
        assert review and all(i.category==passports.REVIEW_CATEGORY_ID for i in review)


def test_resolver_checks_later_catalogue_pages(monkeypatch):
    catalog=[{'id':f'mat_{i}','name':f'Вид {i}'} for i in range(55)];seen=[]
    def model(task,data,schema):
        seen.extend(x['id'] for x in data['catalog'])
        return {'match_id':'mat_54' if any(x['id']=='mat_54' for x in data['catalog']) else None}
    monkeypatch.setattr(llm,'structured',model)
    assert taxonomy.equivalent('Синоним последнего',catalog,lambda _:None,'type')['id']=='mat_54'
    assert len(seen)==55


def test_name_is_explicit_and_evidence_checked(monkeypatch):
    # JSON key order is immaterial: name comes last in the model response.
    monkeypatch.setattr(llm,'structured',lambda *args:{'values':[],'name_evidence':'  Датчик   уровня ','name':'Датчик уровня'})
    p,issues=passports.extract('Позиция:  Датчик   уровня ')
    assert p['name']=='Датчик уровня' and p['attributes']['name']=='Датчик уровня' and not issues
    monkeypatch.setattr(llm,'structured',lambda *args:{'values':[],'name_evidence':'Датчик уровня','name':'Насос'})
    p,issues=passports.extract('Датчик уровня')
    assert p['name']=='Датчик уровня' and p['normalization']['requires_review']
    assert p['evidence']['name']['method']=='source-copy'
    assert issues[-1]['code']=='NORMALIZATION_NEEDS_REVIEW'


def test_more_than_40_materials_allowed_and_bad_hierarchy_rejected():
    schema=SchemaDraft(taxonomy_mode='passports',taxonomy_status='review',mappings=[],
                       features=[passports.NAME_FEATURE],categories=[{'id':f'mat_{i}','name':f'Вид {i}','feature_keys':['name']} for i in range(80)])
    assert len(schema.categories)==80
    bad=schema.model_dump();bad['categories'][0]['parent_id']='mat_0'
    with pytest.raises(ValueError,match='hierarchy'):SchemaDraft.model_validate(bad)

@pytest.mark.parametrize('new_name,known',[('Помпа X 220В',True),('Реле K 24В',False)])
@pytest.mark.parametrize('historic',[False,True])
def test_new_requirements_use_frozen_taxonomy_without_renormalizing_stock(client,auth,mixed,new_name,known,historic):
    sid,names,calls,_=mixed;schema=ready(client,auth,mixed)
    assert client.post(f'{API}/taxonomy/{sid}/approve',headers=auth,json={'freeze_version':'v1'}).status_code==200
    with SessionLocal() as db:
        saved={}
        for item in db.scalars(select(Item).where(Item.session_id==sid,Item.kind=='residual',Item.passport['attributes'].as_string().is_not(None))):
            if historic:
                passport=deepcopy(item.passport)
                passport.pop('taxonomy_version',None)
                passport.pop('taxonomy_proposal',None)
                item.passport=passport
            saved[item.id]=deepcopy(item.passport)
        db.commit()
    before_taxonomy=len(calls['taxonomy'])
    result=client.post(API+'/procurement/sessions',headers=auth,json={'title':'Reuse new taxonomy','taxonomy_enabled':True,'base_session_id':sid,'llm_profile_id':'qwen3-8b'})
    assert result.status_code==201,result.text
    target=result.json();tid=target['id'];before=len(calls['extract'])
    assert target['approved'] and target['schema_doc']['analyzed_items']==len(names)
    assert client.post(f'{API}/procurement/sessions/{tid}/upload-requirement',headers=auth,files={'file':('need.txt',new_name.encode())}).status_code==201
    execute(client,auth,tid,f'pipeline/{tid}/start-normalization',workflow.normalize_all)
    assert len(calls['extract'])==before+1
    with SessionLocal() as db:
        session=db.get(ProcurementSession,tid)
        for item in db.scalars(select(Item).where(Item.session_id==tid,Item.kind=='residual',Item.passport['attributes'].as_string().is_not(None))):
            original_id=item.passport['reuse']['item_id']
            assert item.passport=={**saved[original_id],'reuse':{'session_id':sid,'item_id':original_id}}
        assert len(calls['taxonomy'])==before_taxonomy+1  # Only the new requirement.
        assert {c['id'] for c in session.schema_doc['categories']}=={c['id'] for c in schema['categories']}
        assert session.schema_doc['analyzed_items']==len(names)+1
        req=db.scalar(select(Item).where(Item.session_id==tid,Item.kind=='requirement'))
        assert bool(req.category)==known
        if known:
            fields=taxonomy.features_for(session.schema_doc,req.category)
            key=next(f['key'] for f in fields if f['label']=='Напряжение')
            assert req.passport['attributes'][key]==220
        else:
            assert any(i['code']=='CATEGORY_NEEDS_REVIEW' for i in req.issues)


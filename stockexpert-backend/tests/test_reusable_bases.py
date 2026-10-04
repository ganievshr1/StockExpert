from copy import deepcopy
from io import BytesIO
import pytest
from sqlalchemy import select
from qdrant_client import QdrantClient
from openpyxl import load_workbook
from app.db import SessionLocal,ProcurementSession,Document,Item,Job,Organization
from app.services import storage,llm,search
from app import tasks

@pytest.fixture
def base(client,auth,monkeypatch):
    sid=client.post('/api/v1/procurement/sessions',headers=auth,json={'title':'Готовый склад'}).json()['id']
    objects={};monkeypatch.setattr(storage,'put',lambda k,data,*a,**kw:objects.__setitem__(k,data));monkeypatch.setattr(storage,'get',lambda k:objects[k]);monkeypatch.setattr(storage,'delete_key',lambda k:objects.pop(k,None))
    monkeypatch.setattr(storage,'delete_session_files',lambda org,s:[objects.pop(k) for k in list(objects) if k.startswith(f'{org}/{s}/')])
    q=QdrantClient(':memory:');monkeypatch.setattr(search,'client',lambda:q)
    embeds=[]
    def embed(texts):embeds.extend(texts);return [[1.,0.5,0.2] for t in texts]
    monkeypatch.setattr(llm,'embed',embed);monkeypatch.setattr(llm,'unload',lambda:None)
    model_calls=[]
    def model(task,data,schema):
        model_calls.append(data)
        if 'items' in schema['properties']:return {'items':[data['text']]}
        return {'category_id':'cable','category_evidence':'Кабель','values':[{'key':'voltage_v','value':'3,3','unit':'кВ','evidence':'3,3кВ'}]}
    monkeypatch.setattr(llm,'structured',model)
    with SessionLocal() as db:
        s=db.get(ProcurementSession,sid)
        key=f'{s.organization_id}/{sid}/stock.xlsx';objects[key]=b'preserved original spreadsheet'
        d=Document(session_id=sid,kind='residual',filename='stock.xlsx',object_key=key,sha256='a'*64,meta={});db.add(d);db.flush()
        s.schema_doc={'mappings':[{'document_id':d.id,'sheet':'Stock','header_row':1,'name_columns':['0'],'category_column':None}],'features':[{'key':'voltage_v','label':'Напряжение','kind':'number','unit':'V','comparison':'eq','tolerance':0}],'categories':[{'id':'cable','name':'Кабель','parent_id':None}]}
        s.approved=True;s.taxonomy_version='test-v1';s.status='NORMALIZED'
        db.add(Item(session_id=sid,document_id=d.id,kind='residual',sheet='Stock',row_index=1,raw={'0':'Описание'},source_text=''))
        i=Item(session_id=sid,document_id=d.id,kind='residual',sheet='Stock',row_index=2,raw={'0':'Остаток Кабель 3,3кВ'},source_text='Остаток Кабель 3,3кВ',passport={'attributes':{'voltage_v':3300},'category_id':'cable','evidence':{'voltage_v':{'quote':'3,3кВ'}}},category='cable');db.add(i);db.commit()
        search.index_items(sid,[i]);source_item=i.id
    class Lock:
        def acquire(self,**kw):return True
        def extend(self,*a,**kw):pass
        def owned(self):return True
        def release(self):pass
    class Redis:
        @staticmethod
        def from_url(url):return Redis()
        def lock(self,*a,**kw):return Lock()
    monkeypatch.setattr(tasks,'Redis',Redis)
    return sid,objects,model_calls,embeds,source_item

def test_reuse_full_matching_without_stock_llm_or_embeddings(client,auth,base):
    sid,objects,calls,embeds,old_item=base
    bases=client.get('/api/v1/normalized-bases',headers=auth).json();assert bases[0]['normalized_rows']==1
    r=client.post('/api/v1/procurement/sessions',headers=auth,json={'title':'Новый опросный лист','base_session_id':sid});assert r.status_code==201,r.text
    target=r.json()['id'];assert r.json()['approved'] is True;assert r.json()['base_snapshot']['normalized_rows']==1
    assert client.post(f'/api/v1/pipeline/{target}/run',headers=auth,json={}).status_code==409
    url=f'/api/v1/procurement/sessions/{target}'
    d=client.post(url+'/upload-requirement',headers=auth,files={'file':('new.txt','Кабель 3х16-3,3кВ'.encode())});assert d.status_code==201
    jid=client.post(f'/api/v1/pipeline/{target}/run',headers=auth,json={}).json()['task_id'];tasks.run_job.run(jid)
    with SessionLocal() as db:
        job=db.get(Job,jid);assert job.status=='COMPLETED',job.error
        assert job.params['normalization_counts']=={'requirement':1}
        assert job.params['index_stats']=={'vectors_reused':1,'vectors_created':0}
        originals=db.get(Item,old_item);assert 'reuse' not in originals.passport
        new=db.scalar(select(Item).where(Item.session_id==target,Item.kind=='residual',Item.row_index==2));assert new.id!=old_item;assert new.passport['attributes']==originals.passport['attributes']
    assert len(calls)==2 # split new questionnaire + extract its passport; no stock extraction
    assert embeds.count('Остаток Кабель 3,3кВ')==1 # original indexing only
    report=client.get(f'/api/v1/metrics/{target}',headers=auth).json();e=report['efficiency']
    assert e['ready'] and e['reused_passports']==1 and e['reuse_rate']==1
    assert e['new_stock_passports']==0 and e['new_requirement_passports']==1
    assert e['requirement_coverage']==1 and e['latest_job_seconds'] is not None
    assert e['vectors_reused']==1
    wb=load_workbook(BytesIO(client.get(f'/api/v1/metrics/{target}/export',headers=auth).content))
    assert any(row[2].value=='reused_passports' and row[3].value==1 for row in wb['Метрики сессии'])
    # Source cleanup must not invalidate already created independent snapshots.
    assert client.delete(f'/api/v1/procurement/sessions/{sid}/files',headers=auth).status_code==200
    assert any(f'/{target}/' in key for key in objects)
    calls.clear();jid2=client.post(f'/api/v1/matching/{target}/run',headers=auth,json={}).json()['task_id'];tasks.run_job.run(jid2)
    with SessionLocal() as db:assert db.get(Job,jid2).status=='COMPLETED'
    assert not calls

def test_reuse_rejects_active_incomplete_and_foreign(client,auth,base):
    sid,*_=base
    with SessionLocal() as db:
        job=Job(session_id=sid,kind='normalize',status='RUNNING');db.add(job);db.commit();jid=job.id
    assert client.get('/api/v1/normalized-bases',headers=auth).json()==[]
    payload={'title':'Reuse','base_session_id':sid}
    assert client.post('/api/v1/procurement/sessions',headers=auth,json=payload).status_code==409
    with SessionLocal() as db:
        db.get(Job,jid).status='COMPLETED'
        row=db.scalar(select(Item).where(Item.session_id==sid,Item.row_index==2));row.passport=None;db.commit()
    assert client.post('/api/v1/procurement/sessions',headers=auth,json=payload).status_code==409
    with SessionLocal() as db:
        org=Organization(name='Other');db.add(org);db.flush();db.get(ProcurementSession,sid).organization_id=org.id;db.commit()
    assert client.post('/api/v1/procurement/sessions',headers=auth,json=payload).status_code==404

def test_requirement_delete_before_run_and_cleanup_resets_base(client,auth,base):
    sid,*_=base
    target=client.post('/api/v1/procurement/sessions',headers=auth,json={'title':'Reuse','base_session_id':sid}).json()['id'];url=f'/api/v1/procurement/sessions/{target}'
    did=client.post(url+'/upload-requirement',headers=auth,files={'file':('new.txt',b'new need')}).json()['id']
    assert client.delete(url+'/documents/'+did,headers=auth).status_code==200
    result=client.delete(url+'/files',headers=auth);assert result.json()['session']['base_snapshot']=={}
    assert client.get('/api/v1/normalized-bases',headers=auth).json()[0]['session_id']==sid

def test_vector_model_change_forces_reindex(client,auth,base,monkeypatch):
    sid,_,_,embeds,item_id=base
    from app.core.config import settings
    monkeypatch.setattr(settings,'EMBEDDING_MODEL','another-model')
    with SessionLocal() as db:
        stats=search.index_items(sid,[db.get(Item,item_id)])
    assert stats=={'vectors_reused':0,'vectors_created':1}
    assert embeds.count('Остаток Кабель 3,3кВ')==2

def test_source_deleted_before_first_matching_rebuilds_only_vectors(client,auth,base):
    sid,objects,calls,embeds,_=base
    target=client.post('/api/v1/procurement/sessions',headers=auth,json={'title':'Independent','base_session_id':sid}).json()['id']
    assert client.delete(f'/api/v1/procurement/sessions/{sid}/files',headers=auth).status_code==200
    assert client.post(f'/api/v1/procurement/sessions/{target}/upload-requirement',headers=auth,files={'file':('need.txt','Кабель 3,3кВ'.encode())}).status_code==201
    jid=client.post(f'/api/v1/pipeline/{target}/run',headers=auth,json={}).json()['task_id'];tasks.run_job.run(jid)
    with SessionLocal() as db:
        job=db.get(Job,jid);assert job.status=='COMPLETED',job.error
        assert job.params['normalization_counts']=={'requirement':1}
        assert job.params['index_stats']=={'vectors_created':1,'vectors_reused':0}
    assert len(calls)==2


from io import BytesIO
from openpyxl import Workbook,load_workbook
from sqlalchemy import select
from qdrant_client import QdrantClient
from app.db import SessionLocal,Job,Item,ProcurementSession,Match,Audit
from app.services import workflow,llm,storage,search
from app.services.audit import verify

def test_full_flow_real_parsers_and_qdrant_with_fake_model(client,auth,monkeypatch):
    objects={}
    monkeypatch.setattr(storage,'put',lambda key,data,*args,**kw:objects.__setitem__(key,data))
    monkeypatch.setattr(storage,'get',lambda key:objects[key])
    q=QdrantClient(':memory:');monkeypatch.setattr(search,'client',lambda:q)
    monkeypatch.setattr(llm,'embed',lambda texts:[[1.0,0.5,float(len(t)%7)/10] for t in texts])
    monkeypatch.setattr(llm,'unload',lambda:None)
    def model(task,data,schema):
        if 'header_row' in schema['properties']:
            return {'header_row':1,'name_columns':['1'],'category_column':'0'}
        if 'passports' in data:
            return {'items':[{'item_id':x['item_id'],'material':'Кабель','subtype':None} for x in data['passports']]}
        if 'match_id' in schema['properties']:return {'match_id':None}
        if 'items' in schema['properties']:return {'items':[data['text']]}
        source=data['source'];v='6' if '6кВ' in source else '3,3'
        name=source.split('Описание: ')[-1].split(' | ')[0]
        return {'name':name,'name_evidence':name,'values':[{'key':'voltage_v','label':'Напряжение','kind':'number','value':v,'unit':'кВ','target_unit':'V','evidence':v+'кВ'}]}
    monkeypatch.setattr(llm,'structured',model)
    base='/api/v1';sid=client.post(base+'/procurement/sessions',headers=auth,json={'title':'Cable test'}).json()['id']
    wb=Workbook();ws=wb.active;ws.append(['Категория','Описание']);ws.append(['Кабель','Кабель КПпБП-120 3х16-3,3кВ']);ws.append(['Кабель','Кабель 3х16-6кВ']);buf=BytesIO();wb.save(buf)
    url=f'{base}/procurement/sessions/{sid}'
    assert client.post(url+'/upload-residuals',headers=auth,files={'file':('test.xlsx',buf.getvalue())}).status_code==201
    assert client.post(url+'/upload-requirement',headers=auth,files={'file':('need.txt','Кабель 3х16-3,3кВ'.encode())}).status_code==201
    assert client.post(f'{base}/pipeline/{sid}/run',headers=auth,json={}).status_code==409
    r=client.post(f'{base}/pipeline/{sid}/prepare',headers=auth);assert r.status_code==202
    jid=r.json()['task_id']
    with SessionLocal() as db:
        s=db.get(ProcurementSession,sid);j=db.get(Job,jid);workflow.prepare(db,s,j,lambda _:None);j.status='COMPLETED';db.commit()
    schema=client.get(f'{base}/pipeline/{sid}/schema',headers=auth).json()
    assert schema['mappings'][0]['header_row']==1
    assert client.post(f'{base}/taxonomy/{sid}/approve',headers=auth,json={'freeze_version':'test-v1'}).status_code==409
    assert client.post(f'{base}/pipeline/{sid}/approve-mapping',headers=auth).status_code==200
    r=client.post(f'{base}/pipeline/{sid}/start-normalization',headers=auth,json={});assert r.status_code==202
    with SessionLocal() as db:
        s=db.get(ProcurementSession,sid);j=db.get(Job,r.json()['task_id'])
        workflow.normalize_all(db,s,j,lambda _:None);j.status='COMPLETED';db.commit()
        assert s.status=='TAXONOMY_REVIEW' and not s.approved
    schema=client.get(f'{base}/pipeline/{sid}/schema',headers=auth).json()
    next(f for f in schema['features'] if f['label']=='Напряжение')['comparison']='eq'
    assert client.put(f'{base}/pipeline/{sid}/schema',headers=auth,json=schema).status_code==200
    assert client.post(f'{base}/taxonomy/{sid}/approve',headers=auth,json={'freeze_version':'test-v1'}).status_code==200
    r=client.post(f'{base}/pipeline/{sid}/run',headers=auth,json={'target_shortlist_size':1});assert r.status_code==202
    assert client.post(f'{base}/pipeline/{sid}/run',headers=auth,json={}).status_code==409
    with SessionLocal() as db:
        s=db.get(ProcurementSession,sid);j=db.get(Job,r.json()['task_id'])
        workflow.normalize_all(db,s,j,lambda _:None);workflow.match_all(db,s,j,lambda _:None)
        # Redelivery must not duplicate persisted pairs.
        workflow.match_all(db,s,j,lambda _:None);j.status='COMPLETED';db.commit()
        matches=list(db.scalars(select(Match)));assert len(matches)==2
        assert sum(m.result['is_shortlisted'] for m in matches)==1
        assert sum(m.result['state']=='CONTRADICTED' for m in matches)==1
        rows=list(db.scalars(select(Audit).order_by(Audit.id)));assert verify(rows)[0]
        rows[0].event_details={'tampered':True};assert not verify(rows)[0];db.rollback()
    r=client.get(f'{base}/matching/{sid}/candidates',headers=auth);assert r.json()['total']==1
    measured=client.get(f'{base}/metrics/{sid}',headers=auth).json()
    assert measured['extraction']['normalized_rows']==2
    assert measured['extraction']['fill_rate']==1
    assert measured['matching']['pairs_evaluated']==2
    assert measured['matching']['requirements'][0]['compression_factor']==2
    assert all(t['value'] is None for t in measured['targets'])
    r=client.get(f'{base}/reports/{sid}/export/excel',headers=auth)
    assert r.status_code==200 and load_workbook(BytesIO(r.content)).active.max_row==3
    pdf=client.get(f'{base}/reports/{sid}/export/pdf',headers=auth)
    assert pdf.status_code==200 and pdf.content.startswith(b'%PDF')
    assert client.post(f'{base}/reports/{sid}/send-to-sed',headers=auth).status_code==501
    assert client.put(f'{base}/pipeline/{sid}/schema',headers=auth,json=schema).status_code==409

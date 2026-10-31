from copy import deepcopy
from datetime import timedelta
from io import BytesIO
import pytest
from sqlalchemy import select
from openpyxl import load_workbook
from app.db import SessionLocal,ProcurementSession,Job,Item,Match,ExpertDataset,RunSnapshot,MetricRevision,QualityReview,now
from app.services import workflow,search,llm
from app.services.run_history import capture
from app.services.lab import split_rows,h2,h3
from test_h1_engine import upload_and_prepare
from test_expert_evaluation import populate

API='/api/v1'

def test_regex_h1_without_h2_is_ready_and_never_calls_generative_model(client,auth,monkeypatch):
    sid,_=upload_and_prepare(client,auth,monkeypatch)
    assert client.put(f'{API}/procurement/sessions/{sid}/taxonomy-mode',headers=auth,json={'taxonomy_enabled':False}).status_code==200
    monkeypatch.setattr(llm,'structured',lambda *a,**k:pytest.fail('H2 or H1 LLM called'))
    monkeypatch.setattr('app.services.model_registry.preflight',lambda *a:pytest.fail('LLM preflight for regex without H2'))
    from app import tasks
    response=client.post(f'{API}/pipeline/{sid}/start-normalization',headers=auth,json={});assert response.status_code==202,response.text
    tasks.run_job.run(response.json()['task_id'])
    with SessionLocal() as db:
        s=db.get(ProcurementSession,sid)
        assert s.status=='NORMALIZED' and s.approved and s.taxonomy_version is None
        assert s.schema_doc['taxonomy_status']=='skipped' and not s.schema_doc['categories']
        assert s.schema_doc['features']
        assert db.get(RunSnapshot,response.json()['task_id'])
    assert client.post(f'{API}/taxonomy/{sid}/rebuild',headers=auth).status_code==409
    assert client.put(f'{API}/procurement/sessions/{sid}/taxonomy-mode',headers=auth,json={'taxonomy_enabled':True}).status_code==409
    response=client.post(f'{API}/pipeline/{sid}/run',headers=auth,json={});assert response.status_code==202
    with SessionLocal() as db:assert db.get(Job,response.json()['task_id']).kind=='match'


def fixture_session(auth):
    with SessionLocal() as db:
        s,ref,items,job=populate(db)
        s.schema_doc={**s.schema_doc,'features':[{**f,'kind':'number'} for f in s.schema_doc['features']]}
        for item in items.values():item.passport={**item.passport,'normalized_features':s.schema_doc['features']}
        dataset=ExpertDataset(organization_id=s.organization_id,created_by=s.created_by,title='Frozen gold',version='1',checksum='e'*64,payload=ref)
        db.add(dataset);capture(db,s,job);db.commit()
        return s.id,job.id,dataset.id,items['R008'].id

def test_sources_reset_restore_and_old_run_use_frozen_predictions(client,auth,monkeypatch):
    sid,jid,did,iid=fixture_session(auth)
    monkeypatch.setattr(llm,'structured',lambda *a,**k:pytest.fail('Recalculation must not call models'))
    save=client.put(f'{API}/quality/{sid}/passport/{iid}',headers=auth,json={'job_id':jid,'attributes':{'core_count':999}})
    assert save.status_code==200,save.text
    manual={'source':'manual','job_id':jid};auto={'source':'automatic','job_id':jid,'dataset_id':did}
    assert client.post(f'{API}/laboratory/{sid}/recalculate',headers=auth,json=manual).status_code==200
    assert client.post(f'{API}/laboratory/{sid}/recalculate',headers=auth,json=auto).status_code==200
    data=client.get(f'{API}/laboratory/{sid}',headers=auth).json()
    assert data['manual']['f1']==0
    assert data['automatic'][0]['result']['hypotheses'][0]['value']==1
    historical=next(r for r in data['history'] if r['source']=='manual' and r['action']=='recalculate')
    assert client.post(f'{API}/laboratory/{sid}/reset',headers=auth,json=auto).status_code==200
    assert client.get(f'{API}/quality/{sid}',headers=auth).json()['f1']==0
    assert client.post(f'{API}/laboratory/{sid}/reset',headers=auth,json=manual).status_code==200
    assert client.get(f'{API}/quality/{sid}',headers=auth).json()['f1'] is None
    with SessionLocal() as db:
        item=db.get(Item,iid);p=deepcopy(item.passport);p['attributes']['core_count']=999;p['normalized_attributes']['core_count']=999;item.passport=p
        newer=Job(session_id=sid,kind='normalize',status='COMPLETED',created_at=now()+timedelta(seconds=1));db.add(newer);db.commit()
    assert client.post(f'{API}/laboratory/{sid}/history/{historical["id"]}/recalculate',headers=auth).status_code==200
    old=client.get(f'{API}/laboratory/{sid}?job_id={jid}',headers=auth).json()
    assert old['manual']['f1']==0  # Original prediction is still 3, not current 999.
    assert client.post(f'{API}/laboratory/{sid}/recalculate',headers=auth,json=auto).status_code==200
    old=client.get(f'{API}/laboratory/{sid}?job_id={jid}',headers=auth).json()
    assert old['automatic'][0]['result']['hypotheses'][0]['value']==1
    targets=client.get(f'{API}/quality/{sid}/targets?kind=passport&job_id={jid}&limit=100',headers=auth).json()
    assert next(r['target'] for r in targets['items'] if r['target']['id']==iid)['passport']['attributes']['core_count']!=999
    export=client.get(f'{API}/laboratory/{sid}/export?job_id={jid}',headers=auth)
    book=load_workbook(BytesIO(export.content));assert {'H1 слайд 11','H2 слайд 16','H3 слайд 21','Ручная проверка','Автоматическое сравнение'}<=set(book.sheetnames)


def synthetic():
    rows=[];gold={}
    for group,name in enumerate(['электродвигатель асинхронный','кран шаровой']):
        for n in range(30):
            row={'id':f'{group}-{n}','source_text':f'{name} серия {n}','kind':'residual','passport':{'name':name,'attributes':{},'normalized_attributes':{}}}
            rows.append(row);gold[row['id']]={'category_path':['root',str(group)],'attributes':{}}
    return {'options':{'seed':42,'train_fraction':.7,'clusters':2,'min_cluster_size':3,'repeats':1},'gold':gold,'reference':None,'snapshot':{'items':rows,'session':{'taxonomy_enabled':False,'schema_doc':{'features':[],'categories':[]}}}},rows


def complete_h2_reference(did):
    # The older mixed H1/H3 fixture intentionally left 11 categories unlabelled.
    # The closed H2 protocol requires a complete synthetic category fixture.
    with SessionLocal() as db:
        ref=db.get(ExpertDataset,did);payload=deepcopy(ref.payload)
        for passport in payload['passports']:
            if passport['kind']=='residual' and not passport.get('category_path'):
                passport['category_path']=['Тестовые изделия','Прочие']
        ref.payload=payload;db.commit()


def test_kmeans_closed_test_and_train_mapping_do_not_use_test_gold():
    data,rows=synthetic();train,test=split_rows(rows,data['options'])
    first=h2('h2_kmeans',data,rows);assert first['metrics']['accuracy']==1
    altered=deepcopy(data)
    for row in test:altered['gold'][row['id']]['category_path']=['never seen',row['id']]
    second=h2('h2_kmeans',altered,rows)
    assert first['mapping']==second['mapping'] and second['metrics']['accuracy']==0
    duplicate={**rows[0],'id':'duplicate'}
    train2,test2=split_rows(rows+[duplicate],data['options'])
    assert (rows[0] in train2)==(duplicate in train2)


def test_dense_cascade_limits_cross_encoder_before_scoring_and_full_ce_uses_all(monkeypatch):
    import numpy as np
    data,rows=synthetic()
    rows=[{**rows[0],'id':str(i),'source_text':f'позиция {i}'} for i in range(180)]
    req={'id':'req','kind':'requirement','source_text':'запрос','passport':{'attributes':{}}}
    data['snapshot']['items']=[*rows,req];data['pairs']=[{'requirement_id':'req','item_id':r['id'],'relevant':int(r['id'])<2} for r in rows]
    monkeypatch.setattr('app.services.lab.embeddings',lambda texts:np.array([[1.,0.] for _ in texts]))
    monkeypatch.setattr(search,'reranker',lambda:None);calls=[]
    monkeypatch.setattr(search,'rerank',lambda q,items:(calls.append(len(items)) or {i.id:float(-int(i.id)) for i in items}))
    monkeypatch.setattr('app.core.config.settings.RERANKER_ENABLED',True)
    cascade=h3('h3_cascade',data,rows);assert max(calls)==150 and cascade['metrics']['recall150']==1
    calls.clear();full=h3('h3_full_ce',data,rows);assert max(calls)==180 and full['metrics']['recall150']==1
    assert len(cascade['predictions']['req']['pre_rerank_ids'])==150
    data['pairs'].pop()
    with pytest.raises(ValueError,match='разметка каждой позиции'):h3('h3_bm25',data,rows)

def test_hdbscan_passports_and_raw_are_real_frozen_cluster_predictions(monkeypatch):
    import numpy as np
    data,rows=synthetic()
    def embed(texts):
        return np.array([[1 if 'электродвигатель' in t else 0,0 if 'электродвигатель' in t else 1,(sum(map(ord,t))%13)*.0001] for t in texts],dtype=float)
    monkeypatch.setattr('app.services.lab.embeddings',embed)
    raw=h2('h2_raw',data,rows);structured=h2('h2_passports',data,rows)
    assert raw['metrics']['accuracy']==1 and structured['metrics']['accuracy']==1
    assert raw['metrics']['ari']==1
    assert raw['train_count']+raw['test_count']==60


def test_new_recall_uses_pre_rerank_cutoff(auth):
    from app.services.expert_evaluation import calculate
    with SessionLocal() as db:
        s,ref,items,job=populate(db)
        for m in db.scalars(select(Match).where(Match.job_id==job.id)):
            m.result={**m.result,'pre_rerank_rank':1 if m.result.get('is_shortlisted') else None,'rank':200 if m.result.get('is_shortlisted') else None}
        db.commit()
        _,result=calculate(db,s,ref)
        assert result['metrics']['h3_recall_at_150']==1
        assert result['metrics']['h3_ndcg10']==0
        assert result['recall_stage']=='before_cross_encoder'

def test_experiment_api_runs_real_cpu_methods_without_mutating_session(client,auth,monkeypatch):
    from app.db import LabExperiment
    from app.services.lab import run,summarize
    sid,jid,did,iid=fixture_session(auth)
    complete_h2_reference(did)
    monkeypatch.setattr(llm,'structured',lambda *a,**k:pytest.fail('CPU benchmark called LLM'))
    response=client.post(f'{API}/laboratory/{sid}/experiments',headers=auth,json={'job_id':jid,'source':'automatic','dataset_id':did,'methods':['h1_saved','h2_kmeans'],'clusters':3})
    assert response.status_code==202,response.text
    with SessionLocal() as db:
        exp=db.get(LabExperiment,response.json()['id']);original=deepcopy(db.get(Item,iid).passport)
        exp.result=run(exp.payload);exp.summary=summarize(exp.result);exp.status='COMPLETED';db.commit()
        assert exp.result['methods']['h1_saved']['status']=='COMPLETED'
        assert exp.result['methods']['h2_kmeans']['status']=='COMPLETED'
        assert db.get(Item,iid).passport==original
    data=client.get(f'{API}/laboratory/{sid}',headers=auth).json()
    assert 'predictions' not in data['experiments'][0]['result']['methods']['h2_kmeans']
    assert 'payload' not in data['experiments'][0]

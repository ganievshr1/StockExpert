"""Evaluate all 98 source rows using controlled predictions, never real LLM calls."""
from copy import deepcopy
from pathlib import Path
import json
from itertools import permutations
import pytest
from sqlalchemy import select

from app.db import Document, ExpertDataset, ExpertEvaluation, Item, Job, Match, Organization, ProcurementSession, SessionLocal, User
from app.services.expert_reference import parse_reference
from app.services.expert_evaluation import calculate, hungarian_score, evaluate_subscriptions

EXAMPLE = Path(__file__).resolve().parents[1] / 'examples' / 'expert_reference_98.json'


def reference():
    return parse_reference(EXAMPLE.read_bytes(), 'reference.json')[0]


def populate(db, org_id=1, ref=None):
    ref = deepcopy(ref) if ref is not None else reference()
    session = ProcurementSession(organization_id=org_id, created_by=1, title='Controlled test', status='MATCHED', taxonomy_enabled=True, llm_config={'model':'fixture-model'}, schema_doc={})
    db.add(session); db.flush()
    docs = {}
    for source in ref['sources']:
        d = Document(session_id=session.id, kind=source['kind'], filename=source['filename'], object_key=source['sha256'], sha256=source['sha256'], meta={})
        db.add(d); db.flush(); docs[source['sha256']] = d
    categories, mapped = {}, {}
    for p in ref['passports']:
        parent = None
        for n, part in enumerate(p['category_path']):
            path = '/'.join(p['category_path'][:n+1])
            if path not in categories:
                categories[path] = {'id': 'c' + str(len(categories)), 'name': part, 'parent_id':parent, 'feature_keys':[]}
            parent = categories[path]['id']
        attrs = {k:v['value'] for k,v in p['attributes'].items()}
        passport = {'name': p['name'], 'attributes':attrs, 'normalized_attributes':attrs, 'normalized_features':ref['features'], 'category_id':parent, 'provenance':{'model':'fixture-model'}}
        item = Item(session_id=session.id, document_id=docs[p['source_sha256']].id, kind=p['kind'], sheet=p['sheet'], row_index=p['row'], raw=p['source_cells'], source_text=p['source_name'], passport=passport, category=parent, issues=[])
        db.add(item); db.flush(); mapped[p['id']]=item
    session.schema_doc={'features':ref['features'],'categories':list(categories.values())}
    job=Job(session_id=session.id, kind='match',status='COMPLETED',params={})
    db.add(job);db.flush()
    rank=0
    for pair in ref['pairs']:
        if pair['relevant']:rank+=1
        db.add(Match(session_id=session.id,job_id=job.id,requirement_id=mapped[pair['requirement_id']].id,item_id=mapped[pair['item_id']].id,result={'rank':rank if pair['relevant'] else None,'retrieved':pair['relevant'],'is_shortlisted':pair['relevant']}))
    db.commit()
    return session, ref, mapped, job


def test_all_98_positions_perfect_fixture_and_exact_h3_denominator(auth):
    with SessionLocal() as db:
        s, ref, items, _ = populate(db)
        status,result=calculate(db,s,ref)
        assert status=='PARTIAL'  # 11 reference rows still lack expert L3 labels.
        assert result['coverage']['normalized_stock']==98
        assert result['coverage']['categories']==87
        assert result['coverage']['relevant']==3
        assert result['metrics']['h1_f1']==1
        assert result['metrics']['h2_l3_accuracy']==1
        assert result['metrics']['h3_recall_at_150']==1
        assert result['metrics']['h3_recall_at_1']==pytest.approx(1/3)
        assert result['metrics']['h3_mrr']==1
        assert result['metrics']['h3_ndcg10']==1
        assert result['timing']['p95_ms'] is None
        # A missing relevant passport still contributes to the Recall denominator.
        db.execute(Match.__table__.delete().where(Match.item_id==items['R008'].id))
        db.delete(items['R008']);db.commit()
        _, changed=calculate(db,s,ref)
        assert changed['metrics']['h3_recall_at_150']==pytest.approx(2/3)
        assert changed['metrics']['h1_recall']<1
        assert changed['coverage']['missing_ids']==['R008']


def test_wrong_value_counts_fp_fn_and_unknown_field_is_fp(auth):
    with SessionLocal() as db:
        s,ref,items,_=populate(db)
        p=deepcopy(items['R008'].passport)
        p['normalized_attributes']['core_count']=4
        p['normalized_attributes']['invented_attribute']='not in source'
        items['R008'].passport=p;db.commit()
        _,result=calculate(db,s,ref)
        assert result['coverage']['fp']==2
        assert result['coverage']['fn']==1
        assert result['metrics']['h1_f1']<1


def test_hungarian_is_one_to_one_not_majority_per_cluster():
    assert hungarian_score([('a','X'),('a','X'),('b','X'),('b','Y'),(None,'Y')])[0]==3
    pairs=[('a','X'),('a','Y'),('b','X'),('c','Z'),('c','Z')]
    expected=max(sum(mapping[p]==e for p,e in pairs) for order in permutations(['X','Y','Z']) for mapping in [dict(zip(['a','b','c'],order))])
    assert hungarian_score(pairs)[0]==expected


def test_upload_once_evaluate_saved_sessions_and_preserve_predictions(client,auth,monkeypatch):
    monkeypatch.setattr('app.services.llm.structured', lambda *a,**k: pytest.fail('Evaluation called an LLM'))
    with SessionLocal() as db:
        s,ref,items,job=populate(db);sid=s.id;jid=job.id
        before={i.id:deepcopy(i.passport) for i in items.values()}
    upload=lambda:client.post('/api/v1/developer/references',headers=auth,files={'file':('reference.json',json.dumps(ref,ensure_ascii=False).encode(),'application/json')})
    first=upload();assert first.status_code==200,first.text
    rid=first.json()['id'];assert upload().json()['id']==rid
    eligible=client.get(f'/api/v1/developer/references/{rid}/sessions',headers=auth).json()
    assert eligible['total']==1
    response=client.post(f'/api/v1/developer/references/{rid}/evaluate',headers=auth,json={'session_ids':[sid]})
    assert response.status_code==200,response.text
    rows=client.get(f'/api/v1/developer/references/{rid}/results',headers=auth).json()
    assert rows[0]['result']['metrics']['h1_f1']==1
    assert rows[0]['result']['models'][0]['model']=='fixture-model'
    assert 'rows' not in rows[0]['result']
    full=client.get('/api/v1/developer/evaluations/'+rows[0]['id'],headers=auth).json()
    assert len(full['result']['rows'])==98
    with SessionLocal() as db:
        assert {i.id:i.passport for i in db.scalars(select(Item))}==before
        assert db.get(Job,jid).status=='COMPLETED'
        assert len(list(db.scalars(select(Match))))==98
    exported=client.get(f'/api/v1/developer/references/{rid}/export',headers=auth)
    assert exported.status_code==200 and 'h1_f1' in exported.text


def test_roles_organization_isolation_and_all_manual_passports(client,auth):
    with SessionLocal() as db:
        s,ref,items,job=populate(db);sid=s.id
        # An item outside candidate results must still be manually reviewable.
        db.execute(Match.__table__.delete().where(Match.item_id==items['R004'].id));db.commit()
    rows=client.get(f'/api/v1/quality/{sid}/targets?kind=passport&limit=100',headers=auth)
    assert rows.status_code==200 and rows.json()['total']==99
    filtered=client.get(f'/api/v1/quality/{sid}/targets?kind=passport&passport_kind=requirement',headers=auth)
    assert filtered.json()['total']==1
    with SessionLocal() as db:
        user=db.get(User,1);user.role='buyer';db.commit()
    assert client.get('/api/v1/developer/references',headers=auth).status_code==403


def test_waiting_subscription_becomes_ready_without_llm(auth):
    with SessionLocal() as db:
        s,ref,_,job=populate(db)
        r=ExpertDataset(organization_id=1,created_by=1,title='Expert',version='1',checksum='f'*64,payload=ref)
        db.add(r);db.flush()
        e=ExpertEvaluation(dataset_id=r.id,session_id=s.id,requested_by=1);db.add(e)
        job.status='RUNNING';db.commit()
        evaluate_subscriptions(db,s.id);assert e.status=='WAITING'
        job.status='COMPLETED';db.commit()
        evaluate_subscriptions(db,s.id);assert e.status=='PARTIAL'


def test_incomplete_h3_reference_rejected():
    ref=reference();ref['pairs'].pop()
    with pytest.raises(ValueError,match='Для H3'):
        parse_reference(json.dumps(ref).encode(),'reference.json')


def test_changed_requirement_and_lack_of_l3_do_not_produce_fake_scores(auth):
    with SessionLocal() as db:
        s,ref,items,job=populate(db)
        schema=deepcopy(s.schema_doc)
        for category in schema['categories']:
            category['parent_id']=None
        s.schema_doc=schema
        doc=db.get(Document,items['Q001'].document_id);doc.sha256='0'*64;db.commit()
        status,result=calculate(db,s,ref)
        assert status=='PARTIAL'
        assert result['metrics']['h3_recall_at_150'] is None
        assert result['metrics']['h2_l3_accuracy'] is None


def test_three_metrics_show_exact_counts_and_entire_catalog_recall(auth):
    with SessionLocal() as db:
        s,ref,items,job=populate(db)
        # A positive row absent from all matching results remains in the gold
        # population, even though the candidate table cannot display it.
        db.execute(Match.__table__.delete().where(Match.item_id==items['R008'].id))
        db.commit()
        _,result=calculate(db,s,ref)
        h1,h2,h3=result['hypotheses']
        assert h1['value']==1
        assert h1['numerator']==h1['denominator']>0
        assert h2['value'] is None and h2['denominator']==98
        assert '87 из 98' in h2['reason']
        assert h3['value']==pytest.approx(2/3)
        assert h3['substitution']=='2 / 3'
        assert h3['denominator']==3  # Single-query macro recall equals its own relevant denominator
        population=result['h3_population']
        assert population['catalog_rows']==98
        assert population['pairs_reviewed']==population['pairs_total']==98
        assert population['retained_share']==pytest.approx(2/98)
        assert result['h3_queries'][0]['missed_relevant']==1


def test_all_three_metrics_available_with_full_l3_and_noise_counts_as_error(auth):
    ref=reference()
    # Controlled, fully labelled test fixture; these labels are not user data.
    for p in ref['passports']:
        if not p['category_path']:
            p['category_path']=['Fixture L1','Fixture L2','Fixture L3']
    with SessionLocal() as db:
        s,ref,items,_=populate(db,ref=ref)
        p=deepcopy(items['R008'].passport);p['category_id']=None
        items['R008'].passport=p;db.commit()
        status,result=calculate(db,s,ref)
        assert status=='READY'
        h2=result['hypotheses'][1]
        assert h2['numerator']==97 and h2['denominator']==98
        assert h2['value']==pytest.approx(97/98)


def test_unknown_pair_prevents_claiming_full_catalog_recall(auth):
    with SessionLocal() as db:
        s,ref,_,_=populate(db)
        ref['pairs'][0]['relevant']=None
        status,result=calculate(db,s,ref)
        assert status=='PARTIAL'
        assert result['metrics']['h3_recall_at_150'] is None
        h3=result['hypotheses'][2]
        assert h3['value'] is None
        assert '1 пар' in h3['reason']
        assert result['h3_population']['pairs_reviewed']==97


def test_metrics_page_and_excel_use_same_expert_counts_without_llm(client,auth,monkeypatch):
    from io import BytesIO
    from openpyxl import load_workbook
    monkeypatch.setattr('app.services.llm.structured', lambda *a,**k: pytest.fail('Evaluation called an LLM'))
    with SessionLocal() as db:
        s,ref,_,_=populate(db);sid=s.id
        s.llm_config={}
        for match in db.scalars(select(Match).where(Match.session_id==sid)):
            match.result={**match.result,'state':'PASSES_KNOWN_RULES'}
        db.commit()
    rid=client.post('/api/v1/developer/references',headers=auth,files={'file':('ref.json',json.dumps(ref).encode(),'application/json')}).json()['id']
    client.post(f'/api/v1/developer/references/{rid}/evaluate',headers=auth,json={'session_ids':[sid]})
    response=client.get(f'/api/v1/metrics/{sid}',headers=auth)
    assert response.status_code==200,response.text
    expert=response.json()['expert_evaluations'][0]
    assert expert['result']['hypotheses'][2]['substitution']=='3 / 3'
    assert 'pairs' not in expert['result']
    exported=client.get(f'/api/v1/metrics/{sid}/export',headers=auth)
    wb=load_workbook(BytesIO(exported.content))
    rows=list(wb['H1 H2 H3 по эталону'].values)
    assert rows[3][6:9]==(3,3,'3 / 3')
    csv=client.get(f'/api/v1/developer/references/{rid}/export',headers=auth)
    assert 'H3_denominator' in csv.text and 'h3_catalog_rows' in csv.text
    # Active processing must not display a previous score as a current result.
    with SessionLocal() as db:
        db.add(Job(session_id=sid,kind='match',status='QUEUED',params={}));db.commit()
    pending=client.get(f'/api/v1/metrics/{sid}',headers=auth).json()['expert_evaluations'][0]
    assert pending['status']=='WAITING' and 'hypotheses' not in pending['result']

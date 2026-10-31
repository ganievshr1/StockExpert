from copy import deepcopy
from io import BytesIO
from pathlib import Path
import json
from openpyxl import load_workbook
from sqlalchemy import select
from app.db import SessionLocal,MetricRevision,ExpertDataset,User,Match,RunSnapshot,Item
from app.services.expert_reference import parse_reference
from app.services.hypothesis_reports import cohort
from app.services.normalization_engine import column_mapping
from app.services.regex_excel import parse_stock_xlsx
from test_laboratory import fixture_session

API='/api/v1'

def test_new_reference_round_trip_and_all_source_quotes():
    path=Path(__file__).resolve().parents[2]/'reference'
    a,ca=parse_reference((path/'StockExpert_reference_99.json').read_bytes(),'ref.json')
    b,cb=parse_reference((path/'StockExpert_reference_99.xlsx').read_bytes(),'ref.xlsx')
    assert ca==cb and a==b
    assert len([p for p in a['passports'] if p['kind']=='residual'])==99
    assert sum(len(p['attributes']) for p in a['passports'])==169
    assert len(a['pairs'])==792 and sum(p['relevant'] for p in a['pairs'])==22
    assert 'ChatGPT' in a['author']

def test_automatic_revisions_keep_details_and_reports_are_independent(client,auth):
    sid,jid,did,_=fixture_session(auth)
    for _ in range(2):
        r=client.post(f'{API}/laboratory/{sid}/recalculate',headers=auth,json={'job_id':jid,'source':'automatic','dataset_id':did})
        assert r.status_code==200,r.text
    with SessionLocal() as db:
        versions=list(db.scalars(select(MetricRevision).where(MetricRevision.session_id==sid)))
        assert len(versions)==2 and all(len(v.result['rows'])==98 for v in versions)
        assert versions[0].inputs['reference_checksum']
        rid=versions[0].id
    for h in ['h1','h2','h3']:
        r=client.get(f'{API}/laboratory/{sid}/history/{rid}/export?stage={h}',headers=auth)
        assert r.status_code==200,r.text
        report=r.json();assert [x['id'] for x in report['result']['hypotheses']]==[h.upper()]
        assert all(k.startswith(h+'_') or k=='name_accuracy' for k in report['result']['metrics'])
        if h=='h1':assert 'pairs' not in report['result'] and 'expected_attributes' in report['result']['rows'][0]
        if h=='h2':assert 'expected_attributes' not in report['result']['rows'][0]
        if h=='h3':assert 'rows' not in report['result'] and report['result']['pairs']
        book=load_workbook(BytesIO(client.get(f'{API}/laboratory/{sid}/history/{rid}/export?stage={h}&format=xlsx',headers=auth).content))
        assert 'Данные_JSON' in book.sheetnames
    result=client.get(f'{API}/developer/comparisons?hypothesis=H1&source=automatic',headers=auth).json()
    assert result['total']==2 and len({r['cohort'] for r in result['items']})==1
    assert client.get(f'{API}/developer/comparisons?hypothesis=H1&source=manual',headers=auth).json()['total']==0
    with SessionLocal() as db:
        user=db.scalar(select(User));user.role='buyer';db.commit()
    assert client.get(f'{API}/developer/comparisons?hypothesis=H1&source=automatic',headers=auth).status_code==403

def test_missing_catalog_pair_cannot_create_perfect_manual_recall(client,auth):
    sid,jid,did,_=fixture_session(auth)
    with SessionLocal() as db:
        snap=db.get(RunSnapshot,jid);data=deepcopy(snap.payload)
        # Simulate a historical retrieval implementation that only persisted one hit.
        data['matches']=data['matches'][:1];snap.payload=data
        first=db.get(Match,data['matches'][0]['id']);mid=first.id;db.commit()
    assert client.put(f'{API}/quality/{sid}/candidate/{mid}',headers=auth,json={'job_id':jid,'suitable':True}).status_code==200
    response=client.post(f'{API}/quality/{sid}/finalize',headers=auth,json={'job_id':jid})
    assert response.status_code==200
    assert response.json()['recall_at_150'] is None
    assert response.json()['coverage']['pairs_total']==98

def test_manual_missing_feature_is_fn_and_is_saved_separately(client,auth):
    sid,jid,_,iid=fixture_session(auth)
    response=client.put(f'{API}/quality/{sid}/passport/{iid}',headers=auth,json={
        'job_id':jid,'attributes':{'omitted_power_kw':5.5},
        'custom_features':[{'key':'omitted_power_kw','label':'Пропущенная мощность','unit':'kW','aliases':[]}],
        'comment':'Контролируемая тестовая метка'})
    assert response.status_code==200,response.text
    q=client.post(f'{API}/quality/{sid}/finalize',headers=auth,json={'job_id':jid}).json()
    assert q['f1']==0 and q['coverage']['fn']==1 and q['coverage']['fp']==0

def test_first_system_row_is_data_and_does_not_feed_source_category():
    sample={'first_rows':[{'row_index':1,'cells':{'0':'Предприятие','1':'КИП и средства автоматизации','2':'Система ЕхСО-35.2А081206-З(С)С(К)-DC24-D'}}]}
    assert column_mapping(sample)=={'header_row':0,'name_columns':['2'],'category_column':None}
    path=Path(__file__).resolve().parents[2]/'reference/Baza_ish_99.xlsx'
    parsed,_=parse_stock_xlsx(path.read_bytes(),'source.xlsx')
    assert len(parsed['rows'])==99 and parsed['rows'][0]['row']==1

def test_cohorts_allow_different_models_but_not_different_labels():
    snap={'documents':[{'id':'d','sha256':'a'*64}], 'items':[{'document_id':'d','kind':'residual','sheet':'s','row_index':1}], 'session':{'llm_config':{'model':'one'}}}
    other=deepcopy(snap);other['session']['llm_config']['model']='two'
    assert cohort(snap,'H1','gold')==cohort(other,'H1','gold')
    assert cohort(snap,'H1','gold')!=cohort(snap,'H1','changed')
    assert cohort(snap,'H1','gold')!=cohort(snap,'H2','gold')

def test_hybrid_only_fills_missing_values_and_preserves_conflicts(monkeypatch):
    from app.services.lab_h1 import extract,paired_interval
    from app.services import passports,normalization_engine
    predicted={'name':'x','attributes':{'power':5},'normalized_attributes':{'power':5},'normalized_features':[{'key':'power','unit':'kW'}]}
    rules={'normalized_attributes':{'power':10,'voltage':220},'normalized_features':[{'key':'power','unit':'kW'},{'key':'voltage','unit':'V'}],'normalized_evidence':{'power':{'quote':'10 кВт'},'voltage':{'quote':'220 В'}}}
    monkeypatch.setattr(passports,'extract',lambda t:(deepcopy(predicted),[]))
    monkeypatch.setattr(normalization_engine,'extract',lambda t:(rules,[]))
    result=extract('h1_hybrid','10 кВт 220 В')
    assert result['normalized_attributes']=={'power':5,'voltage':220}
    assert predicted['normalized_attributes']=={'power':5}
    rows=[{'id':str(i),'source_text':str(i)} for i in range(3)]
    counts={str(i):{'tp':1,'fp':0,'fn':0} for i in range(3)}
    assert paired_interval(counts,counts,rows,42)==(0,[0,0])

def test_missing_manual_category_is_an_error(client,auth):
    sid,jid,_,iid=fixture_session(auth)
    r=client.put(f'{API}/quality/{sid}/passport/{iid}',headers=auth,json={'job_id':jid,'category_reviewed':True,'expected_category':'Категория вне дерева'})
    assert r.status_code==200,r.text
    q=client.post(f'{API}/quality/{sid}/finalize',headers=auth,json={'job_id':jid}).json()
    assert q['accuracy']==0 and q['coverage']['categories_checked']==1

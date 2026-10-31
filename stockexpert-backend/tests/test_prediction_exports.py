from copy import deepcopy
from datetime import timedelta
from io import BytesIO
import json
from openpyxl import load_workbook
from sqlalchemy import select
from app.db import (SessionLocal, Item, Document, Job, Match, ProcurementSession,
                    LabExperiment, MetricRevision, RunSnapshot, Organization, User, now)
from app.core.security import create_access_token
from app.services.run_history import capture
from test_expert_evaluation import populate
from test_h1_text import reference

API='/api/v1'

def test_h1_export_keeps_99_stock_rows_missing_predictions_and_frozen_names(client,auth):
    with SessionLocal() as db:
        session,ref,items,job=populate(db,ref=reference())
        session.taxonomy_enabled=False
        items['R001'].passport=None
        original=items['R002'].passport['name']
        long_text='=SUM(1,1)'+(' Текст МТР'*4000)
        items['R003'].passport={**items['R003'].passport,'name':long_text,'category_id':'not-in-H1'}
        doc=Document(session_id=session.id,kind='requirement',filename='queries.txt',object_key='q',sha256='1'*64,meta={})
        db.add(doc);db.flush()
        for n in range(30):
            db.add(Item(session_id=session.id,document_id=doc.id,kind='requirement',sheet='',row_index=n+1,raw={},source_text=f'Q{n}',passport={'name':f'Q{n}','attributes':{}},issues=[]))
        capture(db,session,job);sid,jid=session.id,job.id
        items['R002'].passport={**items['R002'].passport,'name':'Changed after snapshot'}
        db.add(Job(session_id=sid,kind='normalize',status='COMPLETED',created_at=now()+timedelta(seconds=1)))
        db.commit()
    url=f'{API}/runs/{sid}/{jid}/export'
    response=client.get(url,headers=auth)
    assert response.status_code==200,response.text
    data=response.json();rows=data['passports']
    assert data['population']=={'rows':99,'with_passport':98,'missing':1}
    assert data['run']['job_id']==jid and data['run']['taxonomy_enabled'] is False
    assert rows[0]['name'] is None and rows[0]['status']=='missing'
    assert rows[1]['name']==original
    assert rows[2]['name']==long_text
    assert len({r['id'] for r in rows})==99
    assert all('category_id' not in r and 'category_path' not in r for r in rows)
    assert 'h2' not in data and 'h3' not in data
    assert [(r['source_sha256'],r['sheet'],r['row'],r['kind']) for r in rows]==[(r['source_sha256'],r['sheet'],r['row'],r['kind']) for r in ref['passports']]
    assert client.get(url+'?item_kind=all',headers=auth).json()['population']['rows']==129
    assert client.get(url+'?item_kind=requirement',headers=auth).json()['population']['rows']==30
    assert client.get(url+'?stage=h2',headers=auth).status_code==409
    assert client.get(url+'?stage=all',headers=auth).json()['h2']['status']=='skipped'
    excel=client.get(url+'?format=xlsx',headers=auth)
    assert excel.status_code==200 and 'attachment' in excel.headers['content-disposition']
    book=load_workbook(BytesIO(excel.content));sheet=book['H1_тексты']
    assert sheet.max_row==100 and 'H2_позиции' not in book.sheetnames
    name_column=next(c.column for c in sheet[1] if c.value=='name')
    cell=sheet.cell(4,name_column);assert cell.data_type=='s' and cell.value.startswith('=SUM(1,1)')
    rebuilt=json.loads(''.join(r[1] for r in book['Данные_JSON'].iter_rows(min_row=2,values_only=True)))
    assert rebuilt==data


def test_h2_and_h3_export_saved_categories_and_exact_run_pairs(client,auth):
    with SessionLocal() as db:
        s,ref,items,job=populate(db)
        capture(db,s,job);sid,jid=s.id,job.id
        count=len(list(db.scalars(select(Match).where(Match.job_id==jid))))
        match=db.scalar(select(Match).where(Match.job_id==jid));old_result=deepcopy(match.result);mid=match.id
        match.result={'rank':999,'is_shortlisted':False}
        s.schema_doc={**s.schema_doc,'categories':[]}
        newer=Job(session_id=sid,kind='normalize',status='COMPLETED',created_at=now()+timedelta(seconds=1));db.add(newer);db.flush()
        capture(db,s,newer);nid=newer.id;db.commit()
    url=f'{API}/runs/{sid}/{jid}/export'
    h2=client.get(url+'?stage=h2',headers=auth).json()
    assert h2['h2']['status']=='ready' and h2['h2']['categories']
    assert len(h2['h2']['assignments'])==98
    h3=client.get(url+'?stage=h3',headers=auth).json()['h3']
    assert h3['source']=='frozen_snapshot' and h3['pair_count']==count
    assert next(p for p in h3['pairs'] if p['id']==mid)['result']==old_result
    assert all(p['stock']['kind']=='residual' and p['requirement']['kind']=='requirement' for p in h3['pairs'])
    assert client.get(f'{API}/runs/{sid}/{nid}/export?stage=h3',headers=auth).status_code==409
    for stage,sheets in [('h2',{'H2_дерево','H2_позиции'}),('h3',{'H3_пары'})]:
        response=client.get(url+f'?stage={stage}&format=xlsx',headers=auth)
        assert response.status_code==200
        assert sheets<=set(load_workbook(BytesIO(response.content)).sheetnames)


def test_export_refuses_missing_historical_snapshot_and_cross_organization(client,auth):
    with SessionLocal() as db:
        s,ref,items,job=populate(db)
        sid,jid=s.id,job.id
        db.add(Job(session_id=sid,kind='normalize',status='COMPLETED',created_at=now()+timedelta(seconds=1)))
        other=Organization(name='Other');db.add(other);db.flush()
        user=User(username='other',password_hash='unused',role='admin',organization_id=other.id);db.add(user);db.flush()
        token=create_access_token(user);db.commit()
    url=f'{API}/runs/{sid}/{jid}/export'
    assert client.get(url,headers=auth).status_code==409
    assert client.get(url,headers={'Authorization':'Bearer '+token}).status_code==404
    assert client.get(url).status_code==401
    assert client.get(url+'?format=csv',headers=auth).status_code==422


def test_experiment_exports_predictions_by_method_without_payload_secrets(client,auth):
    with SessionLocal() as db:
        s,ref,items,job=populate(db,ref=reference())
        snap=capture(db,s,job).payload
        first=items['R001']
        result={'models':{'llm':{'model':'test','api_key':'secret'}},'methods':{
            'h1_saved':{'hypothesis':'H1','status':'COMPLETED','metrics':{'f1':1},'predictions':{first.id:first.passport}},
            'h1_regex':{'hypothesis':'H1','status':'COMPLETED','metrics':{'f1':0},'predictions':{first.id:{'name':'Different'}}}}}
        exp=LabExperiment(session_id=s.id,job_id=job.id,requested_by=1,source='automatic',status='COMPLETED',payload={'snapshot':snap,'gold':{'secret_reference':'not exported'}},result=result)
        db.add(exp);db.commit();sid,eid,iid=s.id,exp.id,first.id
    url=f'{API}/laboratory/{sid}/experiments/{eid}/export'
    response=client.get(url,headers=auth)
    assert response.status_code==200,response.text
    data=response.json();assert len(data['result']['methods'])==2
    assert data['result']['methods']['h1_saved']['predictions'][iid]['name']==ref['passports'][0]['name']
    assert 'secret' not in response.text and len(data['rows'])==99
    assert data['experiment']['source']=='automatic'
    one=client.get(url+'?method=h1_regex',headers=auth).json()
    assert list(one['result']['methods'])==['h1_regex']
    assert client.get(url+'?method=unknown',headers=auth).status_code==404
    excel=client.get(url+'?format=xlsx',headers=auth)
    assert load_workbook(BytesIO(excel.content))['Предсказания'].max_row==3
    with SessionLocal() as db:
        db.get(LabExperiment,eid).status='RUNNING';db.commit()
    assert client.get(url,headers=auth).status_code==409


def test_saved_manual_and_automatic_evaluations_export_independently(client,auth):
    with SessionLocal() as db:
        s,_,items,job=populate(db,ref=reference())
        revisions=[]
        for source,value in [('manual',.5),('automatic',1.)]:
            row=MetricRevision(session_id=s.id,job_id=job.id,source=source,action='recalculate',result={'text_f1':value},inputs={'reviews':[{'kind':'passport','target_id':items['R001'].id,'prediction':{'name':'model'},'answer':{'name':'expert'}}]} if source=='manual' else {})
            db.add(row);db.flush();revisions.append((row.id,source,value))
        db.commit();sid=s.id
    for rid,source,value in revisions:
        url=f'{API}/laboratory/{sid}/history/{rid}/export'
        data=client.get(url,headers=auth).json()
        assert data['evaluation']['source']==source and data['result']['text_f1']==value
        assert bool(data['reviews'])==(source=='manual')
        response=client.get(url+'?format=xlsx',headers=auth)
        assert response.status_code==200 and 'Оценка' in load_workbook(BytesIO(response.content)).sheetnames

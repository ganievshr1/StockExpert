from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
import numpy as np
import pytest
from openpyxl import load_workbook
from sklearn.metrics import adjusted_rand_score
from app.services import lab, lab_h2, llm
from app.services.expert_evaluation import hungarian_score
from app.services.prediction_export import experiment_export, excel_bytes
from app.services.hypothesis_reports import select_hypothesis, cohort
from test_laboratory import synthetic, fixture_session, complete_h2_reference


def test_all_test_rows_and_individual_noise_clusters():
    rows=[{'id':str(i)} for i in range(4)]
    gold={str(i):{'category_path':['root','family','A' if i<2 else 'B']} for i in range(4)}
    tree=lab_h2.catalogue(gold,rows);a=lab_h2.category_id(['root','family','A'])
    m,p=lab_h2.score(rows,['x','x',None,None],[a,a,None,None],gold,tree,lab.bootstrap,42,hungarian_score)
    assert m['accuracy']==.5 and m['n_test']==4 and m['n_correct']==2
    assert m['unassigned_rate']==.5 and len(p)==4
    assert m['ari']==adjusted_rand_score(['a','a','b','b'],['x','x','noise2','noise3'])
    assert m['ari']<1 and m['edge_accuracy'] is None
    assert 0<=m['cluster_accuracy_hungarian']<=1


def test_path_ids_distinguish_equal_leaf_names_and_parent_errors():
    rows=[{'id':'r'}];gold={'r':{'category_path':['А','X','Кран']},'other':{'category_path':['Б','X','Кран']}}
    tree=lab_h2.catalogue(gold,rows+[{'id':'other'}])
    wrong=lab_h2.category_id(['Б','X','Кран'])
    m,p=lab_h2.score(rows,['c'],[wrong],gold,tree,lab.bootstrap,42,hungarian_score)
    assert m['accuracy']==0 and m['parent_errors']==1 and p['r']['error']=='ошибка родителя'


def test_missing_gold_does_not_shrink_denominator():
    data,rows=synthetic();data['gold'][rows[0]['id']]['category_path']=[]
    with pytest.raises(ValueError,match='Заполните все категории'):lab.h2('h2_kmeans',data,rows)


def test_category_outside_approved_tree_is_counted_as_unassigned(monkeypatch):
    data,rows=synthetic()
    def structured(prompt,payload,schema):
        return {'labels':[{'id':r['id'],'category':'invented_category'} for r in payload['items']]}
    monkeypatch.setattr(llm,'structured',structured)
    result=lab.h2('h2_llm',data,rows)
    assert result['metrics']['accuracy']==0
    assert result['metrics']['unassigned_count']==result['test_count']
    assert len(result['predictions'])==result['test_count']


def test_llm_receives_passports_and_fixed_three_level_catalogue(monkeypatch):
    data,rows=synthetic()
    for r in rows:
        p=data['gold'][r['id']]['category_path'];data['gold'][r['id']]['category_path']=[p[0],'family',p[1]]
    calls=[]
    def structured(prompt,payload,schema):
        calls.append(payload)
        assert all(len(c['path'])==3 for c in payload['tree'])
        assert 'gold' not in payload
        assert all('серия' not in item['text'] for item in payload['items'])
        return {'labels':[{'id':r['id'],'category':lab_h2.category_id(['root','family','0' if 'электродвигатель' in r['text'] else '1'])} for r in payload['items']]}
    monkeypatch.setattr(llm,'structured',structured)
    result=lab.h2('h2_llm',data,rows)
    assert result['metrics']['accuracy']==1 and result['metrics']['accuracy_l3']==1
    assert result['protocol']==lab_h2.PROTOCOL and calls
    assert result['metrics']['noise_rate'] is None


def test_group_llm_can_change_category_not_only_name():
    train=[{'id':'a','source_text':'name','passport':{'name':'one'}}]
    tree=[{'id':'catA','path':['A']},{'id':'catB','path':['B']}]
    mapping,proposal=lab_h2.name_and_map_groups(train,['cluster'],tree,
        lambda *args:{'groups':[{'id':'cluster','category':'catB','l1':'new root','l2':'new parent','l3':'new leaf'}]})
    assert mapping=={'cluster':'catB'}
    edges=lab_h2.proposed_edges(proposal)
    assert len(edges)==2 and all(e['expert_correct'] is None for e in edges)


def test_weighted_passports_and_empty_attributes_fallback():
    rows=[{'source_text':'raw','passport':{'name':'named','normalized_attributes':{'dn':50}}},
          {'source_text':'fallback','passport':{}}]
    def embed(texts):return np.array([[1.,0.] if t in ('named','fallback') else [0.,1.] for t in texts])
    vectors=lab_h2.passport_vectors(rows,embed,{'h2_name_weight':1,'h2_attribute_weight':2})
    assert np.allclose(vectors[0],[1/np.sqrt(5),2/np.sqrt(5)]) and np.allclose(vectors[1],[1,0])


def test_paired_differences_and_refusal_of_different_test_sets():
    base={'predictions':{'a':{'correct':False},'b':{'correct':True}},'metrics':{'accuracy':.5},'note':''}
    full={'predictions':{'a':{'correct':True},'b':{'correct':True}},'metrics':{'accuracy':1},'note':''}
    methods={'h2_kmeans':base,'h2_hybrid':full}
    c=lab_h2.conclusions(methods,{'seed':42,'h2_reference_confirmed':False},lab.bootstrap)
    assert c['criteria_met'] is True and not c['reference_confirmed']
    assert full['metrics']['delta_accuracy']==.5
    full['predictions'].pop('b');c=lab_h2.conclusions(methods,{'seed':42},lab.bootstrap)
    assert full['metrics']['delta_accuracy'] is None and c['criteria_met'] is None


def test_h2_export_has_flat_rows_and_no_source_formula_execution():
    data,rows=synthetic();result=lab.h2('h2_kmeans',data,rows)
    for n,r in enumerate(rows):r.update(document_id='d',sheet='Лист1',row_index=n+1,raw={'0':r['source_text']})
    rows[0]['source_text']='=1+1'
    data['snapshot']['documents']=[{'id':'d','sha256':'a'*64,'filename':'base.xlsx','kind':'residual'}]
    exp=SimpleNamespace(id='e',job_id='j',session_id='s',source='automatic',dataset_id='ref',status='COMPLETED',created_at=datetime.now(timezone.utc),payload=data,
        result={'methods':{'h2_kmeans':{**result,'hypothesis':'H2','label':'baseline','status':'COMPLETED'}},'options':data['options'],'protocol':'lab-v3-h2-fixed-tree'})
    export=select_hypothesis(experiment_export(exp),'h2')
    wb=load_workbook(BytesIO(excel_bytes(export)),read_only=True,data_only=False)
    assert {'H2_сводка','H2_сравнения','H2_построчно','H2_разделение','H2_эталонное_дерево','H2_связи','H2_методика'}<=set(wb.sheetnames)
    assert wb['H2_построчно'].max_row is None or wb['H2_построчно'].max_row==result['population']+1
    detail=list(wb['H2_построчно'].values);assert len(detail)-1==result['population']
    assert sum(row[11] for row in detail[1:])==result['metrics']['n_correct']
    assert len(list(wb['H2_разделение'].values))-1==60
    formula_cells=[c for row in wb['Строки эксперимента'] for c in row if c.value=='=1+1']
    assert formula_cells and all(c.data_type=='s' for c in formula_cells)
    wb.close()


def test_h2_preview_and_upload_route(client,auth):
    sid,jid,did,_=fixture_session(auth)
    payload={'job_id':jid,'source':'automatic','dataset_id':did,'methods':['h2_kmeans'],'clusters':3}
    response=client.post(f'/api/v1/laboratory/{sid}/h2-check',headers=auth,json=payload)
    assert response.status_code==422 and 'Заполните все категории' in response.text
    complete_h2_reference(did)
    response=client.post(f'/api/v1/laboratory/{sid}/h2-check',headers=auth,json=payload)
    assert response.status_code==200,response.text
    value=response.json();assert value['rows']==value['train_count']+value['test_count']
    assert value['classes']>0
    response=client.post(f'/api/v1/laboratory/{sid}/experiments',headers=auth,json={**payload,'h2_name_weight':0})
    assert response.status_code==422


def test_legacy_and_fixed_tree_protocols_are_not_comparable_cohorts():
    snapshot={'documents':[],'items':[]}
    old=cohort(snapshot,'H2','ref',{'seed':42,'train_fraction':.7})
    new=cohort(snapshot,'H2','ref',{'seed':42,'train_fraction':.7,'h2_protocol':lab_h2.PROTOCOL})
    assert old!=new
def test_semantic_and_exact_duplicates_stay_together_without_changing_inference_text():
    from app.services.lab import split_rows
    from app.services.lab_h2 import split_h2
    rows = [{'id':str(i),'source_text':text} for i,text in enumerate(['Плита ПДН, м-АтV, 55','Плита ПДН,м-Ат-V, 55','Плита ПДН,м-Ат-V, 55','Кабель'])]
    data = {'gold':{'0':{'split_group':'slab'},'1':{'split_group':'slab'}},'options':{'seed':42,'train_fraction':.7}}
    for seed in range(10):
        data['options']['seed']=seed
        train,test=split_h2(data,rows,split_rows)
        assert any({'0','1','2'} <= {r['id'] for r in part} for part in (train,test))
        assert sorted(train+test,key=lambda r:r['id'])==rows



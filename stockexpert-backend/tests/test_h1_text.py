"""One stock row is one H1 text comparison; no category/parameter leakage."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
from app.db import SessionLocal, Document, Item
from app.services.expert_reference import parse_reference
from app.services.expert_evaluation import calculate
from app.services.lab import h1, make_input
from app.services.run_history import capture
from test_expert_evaluation import populate

EXAMPLE = Path(__file__).resolve().parents[1] / 'examples' / 'expert_reference_H1_text_99.json'

def reference():
    return parse_reference(EXAMPLE.read_bytes(), EXAMPLE.name)[0]

def test_reference_99_includes_first_row_and_only_original_text():
    ref = reference()
    assert ref['h1_mode'] == 'name_exact'
    assert len(ref['passports']) == 99
    assert [p['row'] for p in ref['passports']] == list(range(1, 100))
    assert all(p['name'] == p['source_cells']['2'] for p in ref['passports'])
    assert all(p['kind'] == 'residual' and not p['attributes'] and not p['category_path'] for p in ref['passports'])
    assert not ref['features'] and not ref['pairs']

def test_exact_name_f1_ignores_arbitrary_h2_and_technical_attributes(auth):
    with SessionLocal() as db:
        s, ref, items, job = populate(db, ref=reference())
        for item in items.values():
            item.passport = {**item.passport, 'category_id':'wrong-category', 'normalized_attributes':{'invented':'wrong', 'diameter':999}}
        db.commit()
        status, result = calculate(db, s, ref)
        assert status == 'READY'
        assert result['coverage']['expected_stock'] == 99
        assert (result['coverage']['tp'], result['coverage']['fp'], result['coverage']['fn']) == (99, 0, 0)
        assert result['metrics']['h1_f1'] == 1
        assert result['metrics']['h2_leaf_accuracy'] is None
        assert result['metrics']['h3_recall_at_150'] is None
        assert [f['key'] for f in result['fields']] == ['name']
        before = deepcopy(items['R002'].passport)
        items['R001'].passport = None
        items['R002'].passport = {**before, 'name':before['name'].lower()}
        db.commit()
        _, changed = calculate(db, s, ref)
        assert (changed['coverage']['tp'], changed['coverage']['fp'], changed['coverage']['fn']) == (97, 1, 2)
        assert changed['metrics']['h1_precision'] == pytest.approx(97/98)
        assert changed['metrics']['h1_recall'] == pytest.approx(97/99)
        assert changed['metrics']['h1_f1'] == pytest.approx(194/197)
        assert changed['coverage']['missing_ids'] == ['R001']

def test_manual_text_excludes_30_requirement_passports_and_is_separate(client, auth):
    with SessionLocal() as db:
        s, ref, items, job = populate(db, ref=reference())
        doc = Document(session_id=s.id,kind='requirement',filename='queries.txt',object_key='q',sha256='1'*64,meta={})
        db.add(doc);db.flush()
        for n in range(30):
            db.add(Item(session_id=s.id,document_id=doc.id,kind='requirement',sheet='',row_index=n+1,raw={},source_text=f'Query {n}',passport={'name':f'Query {n}','attributes':{}},issues=[]))
        capture(db,s,job);db.commit()
        sid,jid,iid=s.id,job.id,items['R001'].id
        expected=ref['passports'][0]['name']
    path=f'/api/v1/quality/{sid}'
    targets=client.get(path+'/targets?kind=passport&passport_kind=residual&review_mode=text&limit=100',headers=auth).json()
    assert targets['total'] == 99
    saved=client.put(path+f'/passport/{iid}',headers=auth,json={'job_id':jid,'attributes':{'name':expected}})
    assert saved.status_code == 200, saved.text
    finished=client.post(path+'/finalize',headers=auth,json={'job_id':jid}).json()
    assert finished['text_f1'] == 1
    assert finished['f1'] is None and finished['accuracy'] is None
    assert finished['coverage']['stock_total'] == 99
    assert finished['coverage']['passports_total'] == 129
    assert finished['coverage']['text_reviewed'] == 1
    left=client.get(path+'/targets?kind=passport&passport_kind=residual&review_mode=text&unreviewed=true',headers=auth).json()
    assert left['total'] == 98
    with SessionLocal() as db:
        assert db.get(Item,iid).passport['name'] == expected

def test_laboratory_uses_same_text_rule_on_test_subset(auth):
    from types import SimpleNamespace
    with SessionLocal() as db:
        s,ref,items,job=populate(db,ref=reference())
        snap=capture(db,s,job)
        # capture returns a DB row; evaluation consumes its frozen payload.
        payload=snap.payload
        options={'seed':42,'train_fraction':.7}
        data=make_input(db,payload,job,SimpleNamespace(payload=ref),options)
        result=h1('h1_saved',data,payload['items'])
        assert result['h1_mode']=='name_exact'
        assert result['metrics']['f1']==1
        assert result['metrics']['tp']==result['population']
        assert 0 < result['population'] < 99

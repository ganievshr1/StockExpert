"""H2 must never collapse different H1 properties or lose their evidence."""
from copy import deepcopy
from collections import defaultdict
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import tasks
from app.db import Item, Job, Match, ProcurementSession, SessionLocal, public
from app.services import llm, normalization_engine, passports, search, taxonomy
from test_h1_engine import API, no_model, run_h1, upload_and_prepare


ROWS = [
    'Кабель ВВГ 3х70+1х35 6000В',
    'Кабель ВВГ 3х70+3х70 6000В',
    'Кабель ВВГ 3х70 6000В IP54',
    'Кабель ВВГ 3х70 6000В УХЛ1',
]
H1_KEYS = ('name', 'name_evidence', 'normalized_features', 'normalized_attributes',
           'normalized_evidence', 'normalization', 'provenance')


def overmerge_model(task, data, schema):
    if 'passports' in data:
        return {'items': [{'item_id': p['item_id'], 'material': 'Кабель', 'subtype': None}
                          for p in data['passports']]}
    if 'match_id' in schema['properties']:
        # A valid but semantically wrong model reply. The engine must constrain it.
        return {'match_id': data['catalog'][0]['id'] if data['catalog'] else None}
    pytest.fail('H2 unexpectedly called H1')


def test_regex_h2_keeps_distinct_keys_even_equal_or_on_different_rows(client, auth, monkeypatch):
    monkeypatch.setattr(llm, 'structured', no_model)
    sid, _ = upload_and_prepare(client, auth, monkeypatch, rows=ROWS)
    h1_id = run_h1(client, auth, sid)
    with SessionLocal() as db:
        original = {i.id: {k: deepcopy(i.passport[k]) for k in H1_KEYS}
                    for i in db.scalars(select(Item).where(Item.session_id == sid, Item.passport.is_not(None)))}
        h1_job = deepcopy(public(db.get(Job, h1_id)))
    monkeypatch.setattr(llm, 'structured', overmerge_model)
    r = client.post(f'{API}/taxonomy/{sid}/rebuild', headers=auth)
    assert r.status_code == 202, r.text
    tasks.run_job.run(r.json()['task_id'])
    with SessionLocal() as db:
        job = db.get(Job, r.json()['task_id'])
        assert job.status == 'COMPLETED', job.error
        s = db.get(ProcurementSession, sid)
        assert s.status == 'TAXONOMY_REVIEW'
        assert public(db.get(Job, h1_id)) == h1_job
        fields = {f['label']: f['key'] for f in s.schema_doc['features']}
        expected = {f['label'] for p in original.values() for f in p['normalized_features']}
        assert set(fields) == expected | {'Наименование'}
        for item in db.scalars(select(Item).where(Item.session_id == sid, Item.passport.is_not(None))):
            p = item.passport
            assert {k: p[k] for k in H1_KEYS} == original[item.id]
            for f in p['normalized_features']:
                key = fields[f['label']]
                assert p['attributes'][key] == p['normalized_attributes'][f['key']]
                assert p['evidence'].get(key) == p['normalized_evidence'].get(f['key'])


def simple_item(values, labels=None, provider='ollama'):
    features = [{'key': key, 'label': (labels or {}).get(key, key), 'kind': 'number', 'unit': 'mm'}
                for key in values]
    return SimpleNamespace(id='conflict-item', issues=[], category=None, passport={
        'name': 'Труба', 'name_evidence': 'Труба',
        'normalized_features': features, 'normalized_attributes': {'name': 'Труба', **values},
        'normalized_evidence': {'name': {'quote': 'Труба'}, **{k: {'quote': str(v)} for k, v in values.items()}},
        'provenance': {'provider': provider},
    })


def test_frozen_mapping_conflict_is_reviewable_and_not_an_arbitrary_value():
    item = simple_item({'outer': 40, 'inner': 30, 'wall': 40})
    original = deepcopy(item.passport)
    mapping = {taxonomy.descriptor(f): 'f_size' for f in item.passport['normalized_features']}
    taxonomy.assign(item, {'id': 'mat_pipe'}, {'id': 'mat_pipe'}, mapping)
    assert item.passport['attributes']['f_size'] is None
    assert 'f_size' not in item.passport['evidence']
    assert item.passport['normalized_attributes'] == original['normalized_attributes']
    assert item.passport['normalized_evidence'] == original['normalized_evidence']
    assert passports.needs_review(item.passport)
    issue = next(x for x in item.issues if x['code'] == 'TAXONOMY_FIELD_CONFLICT')
    assert {x['source_key']: x['value'] for x in issue['sources']} == {'outer': 40, 'inner': 30, 'wall': 40}
    assert all(x['evidence'] == original['normalized_evidence'][x['source_key']] for x in issue['sources'])
    # A later successful mapping must clear only its own review marker.
    taxonomy.assign(item, {'id': 'mat_pipe'}, {'id': 'mat_pipe'},
                    {taxonomy.descriptor(f): f['key'] for f in item.passport['normalized_features']})
    assert not passports.needs_review(item.passport)
    assert not any(x['code'] == 'TAXONOMY_FIELD_CONFLICT' for x in item.issues)


def test_equal_values_keep_evidence_of_non_null_source():
    item = simple_item({'diameter': 40, 'diameter_alias': 40, 'missing': None})
    taxonomy.assign(item, {'id': 'mat_pipe'}, {'id': 'mat_pipe'},
                    {taxonomy.descriptor(f): 'f_diameter' for f in item.passport['normalized_features']})
    assert item.passport['attributes']['f_diameter'] == 40
    assert item.passport['evidence']['f_diameter']['quote'] == '40'
    assert not passports.needs_review(item.passport)


def test_llm_alias_group_cannot_hide_a_conflicting_member(monkeypatch):
    # a and b can be synonyms; c disagrees with b on a different row.
    rows = [simple_item({'a': 40, 'b': 40}), simple_item({'b': 40, 'c': 5})]
    monkeypatch.setattr(llm, 'structured', overmerge_model)
    fields, mapping = taxonomy.build_fields({'id': 'mat_pipe', 'name': 'Труба'}, rows, lambda _: None)
    keys = {f['key']: mapping[taxonomy.descriptor(f)] for i in rows for f in i.passport['normalized_features']}
    assert keys['a'] == keys['b'] and keys['c'] != keys['b']
    assert len(fields) == 2
    # Identical display labels are also subject to observed-value constraints.
    fields, _ = taxonomy.build_fields({'id': 'mat_pipe', 'name': 'Труба'},
        [simple_item({'a': 40, 'b': 5}, labels={'a': 'Размер', 'b': 'Размер'})], lambda _: None)
    assert len(fields) == 2


def test_retry_failed_h2_uses_saved_h1_and_proposals(client, auth, monkeypatch):
    monkeypatch.setattr(llm, 'structured', no_model)
    sid, _ = upload_and_prepare(client, auth, monkeypatch, rows=ROWS)
    h1_id = run_h1(client, auth, sid)
    monkeypatch.setattr(normalization_engine, 'extract', no_model)
    calls = []
    def model(task, data, schema):
        if 'passports' in data: calls.extend(data['passports'])
        return overmerge_model(task, data, schema)
    monkeypatch.setattr(llm, 'structured', model)
    original_assign = taxonomy.assign
    def legacy_assign(item, root, category, mapping):
        attrs = {}
        for f in item.passport['normalized_features']:
            key = mapping.get(taxonomy.descriptor(f))
            value = item.passport['normalized_attributes'].get(f['key'])
            if key in attrs and attrs[key] is not None and value is not None and attrs[key] != value:
                raise ValueError(f'Conflicting normalized attributes merged by taxonomy: item={item.id}; field={key}')
            if value is not None or key not in attrs: attrs[key] = value
        original_assign(item, root, category, mapping)
    with monkeypatch.context() as old:
        # Reproduce 3.9.5's unchecked field merge and fatal assignment guard.
        old.setattr(taxonomy, 'field_constraints', lambda _: (set(), defaultdict(set)))
        old.setattr(taxonomy, 'assign', legacy_assign)
        r = client.post(f'{API}/taxonomy/{sid}/rebuild', headers=auth)
        jid = r.json()['task_id']; tasks.run_job.run(jid)
        with SessionLocal() as db:
            assert db.get(Job, jid).status == 'FAILED'
            assert 'Conflicting normalized attributes merged by taxonomy' in db.get(Job, jid).error
            assert db.get(ProcurementSession, sid).schema_doc['categories'] == []
            saved = {i.id: deepcopy(i.passport) for i in db.scalars(select(Item).where(
                Item.session_id == sid, Item.passport.is_not(None)))}
            h1_job = deepcopy(public(db.get(Job, h1_id)))
    classified_count = len(calls)
    r = client.post(f'{API}/pipeline/{sid}/jobs/{jid}/retry', headers=auth)
    assert r.status_code == 202, r.text
    tasks.run_job.run(jid)
    with SessionLocal() as db:
        assert db.get(Job, jid).status == 'COMPLETED', db.get(Job, jid).error
        assert db.get(ProcurementSession, sid).status == 'TAXONOMY_REVIEW'
        assert public(db.get(Job, h1_id)) == h1_job
        for i in db.scalars(select(Item).where(Item.session_id == sid, Item.passport.is_not(None))):
            for key in (*H1_KEYS, 'taxonomy_proposal', 'classification'):
                assert i.passport[key] == saved[i.id][key]
    assert len(calls) == classified_count


def test_frozen_conflict_visible_in_api_and_requires_review_in_matching(client, auth, monkeypatch):
    sid, _ = upload_and_prepare(client, auth, monkeypatch, rows=['Труба 40х5'])
    run_h1(client, auth, sid)
    with SessionLocal() as db:
        s = db.get(ProcurementSession, sid)
        # Historic approved base with only one broad dimension field.
        s.schema_doc = {**s.schema_doc, 'taxonomy_status': 'approved',
            'features': [passports.NAME_FEATURE, {'key': 'f_size', 'label': 'Размер', 'kind': 'number',
                'unit': 'mm', 'comparison': 'eq', 'tolerance': 0}],
            'categories': [{'id': 'mat_pipe', 'name': 'Труба', 'parent_id': None,
                'aliases': [], 'feature_keys': ['name', 'f_size']}]}
        s.approved = True; s.taxonomy_version = 'historic-v1'; s.status = 'NORMALIZED'
        for i in db.scalars(select(Item).where(Item.session_id == sid, Item.passport.is_not(None))):
            i.passport = {**i.passport, 'attributes': {'name': i.passport['name'], 'f_size': 40},
                'category_id': 'mat_pipe', 'material_id': 'mat_pipe', 'taxonomy_version': 'passport-taxonomy-v1'}
            i.category = 'mat_pipe'
        db.commit()
        originals = {i.id: deepcopy(i.passport) for i in db.scalars(select(Item).where(Item.session_id == sid, Item.passport.is_not(None)))}
    r = client.post(API+'/procurement/sessions', headers=auth,
        json={'title': 'Frozen conflict', 'taxonomy_enabled':True,'base_session_id': sid, 'normalization_method': 'regex'})
    assert r.status_code == 201, r.text
    target = r.json()['id']
    r = client.post(f'{API}/procurement/sessions/{target}/upload-requirement', headers=auth,
        files={'file': ('need.txt', 'Труба 50х6'.encode())})
    assert r.status_code == 201, r.text
    def model(task, data, schema):
        if 'passports' in data:
            return {'items': [{'item_id': p['item_id'], 'material': 'Труба', 'subtype': None}
                              for p in data['passports']]}
        return overmerge_model(task, data, schema)
    monkeypatch.setattr(llm, 'structured', model)
    monkeypatch.setattr(llm, 'unload', lambda: None)
    monkeypatch.setattr(search, 'index_items', lambda *args: {})
    monkeypatch.setattr(search, 'retrieve', lambda sid, query, items, limit: {i.id: 1. for i in items})
    monkeypatch.setattr(search, 'rerank', lambda *args: {})
    r = client.post(f'{API}/pipeline/{target}/run', headers=auth, json={})
    assert r.status_code == 202, r.text
    tasks.run_job.run(r.json()['task_id'])
    with SessionLocal() as db:
        job = db.get(Job, r.json()['task_id'])
        assert job.status == 'COMPLETED', job.error
        req = db.scalar(select(Item).where(Item.session_id == target, Item.kind == 'requirement'))
        assert req.passport['attributes']['f_size'] is None
        assert req.passport['normalized_attributes']['outside_diameter_mm'] == 50
        assert req.passport['normalized_attributes']['wall_thickness_mm'] == 6
        result = db.scalar(select(Match).where(Match.session_id == target))
        assert result.result['state'] == 'NEEDS_REVIEW'
        for ident, original in originals.items(): assert db.get(Item, ident).passport == original
        stock = db.scalar(select(Item).where(Item.session_id == target, Item.kind == 'residual', Item.row_index > 1))
        reuse = stock.passport['reuse']
        assert stock.passport == {**originals[reuse['item_id']], 'reuse': reuse}
        req_id = req.id
    review = client.get(f'{API}/pipeline/{target}/normalization-review', headers=auth).json()
    assert review['total'] == 1 and review['items'][0]['id'] == req_id
    assert any(x['code'] == 'TAXONOMY_FIELD_CONFLICT' for x in review['items'][0]['issues'])

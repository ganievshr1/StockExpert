from copy import deepcopy
from io import BytesIO

import pytest
from jsonschema import validate, ValidationError
from openpyxl import Workbook
from sqlalchemy import select

from app import tasks
from app.db import SessionLocal, ProcurementSession, Job, Item
from app.services import column_mapping, llm, storage
from app.services.execution import Cancelled
from app.services.model_registry import profiles
from test_model_selection import fake_redis

API = '/api/v1'
GROUPS = ['Арматура промышленная трубопроводная',
          'Оборуд. и материалы электротехнические', 'Продукция кабельная',
          'Техника, оборуд бытовое и специальное', 'Продукция пищевой промышленности',
          'Оборудование буровое и нефтепромысловое', 'Оборудование химич, нефтегазоперерабат.',
          'КИП и средства автоматизации', 'Трубы металлические', 'Абсорбенты, сорбенты, адсорбенты']
BAD = {'header_row': 0, 'name_columns': GROUPS, 'category_column': None}
GOOD = {'header_row': 0, 'name_columns': ['0', '1'], 'category_column': None}


def sample():
    return {'document_id': 'source', 'sheet': 'Лист1',
            'available_columns': ['0', '1'], 'last_row_index': 100,
            'first_rows': [{'row_index': n+1, 'cells': {'0': group, '1': f'Изделие {n+1}'}}
                           for n, group in enumerate(GROUPS)]}


def test_schema_restricts_columns_to_real_indexes_and_letters():
    schema = column_mapping.response_schema(['0', '1'], 100)
    validate(GOOD, schema)
    validate({**GOOD, 'name_columns': ['B'], 'category_column': 'A'}, schema)
    for wrong in [BAD, {**GOOD, 'name_columns': ['2']},
                  {**GOOD, 'category_column': GROUPS[0]},
                  {**GOOD, 'header_row': 100}, {**GOOD, 'name_columns': ['1', '1']}]:
        with pytest.raises(ValidationError):
            validate(wrong, schema)


def test_semantic_failure_is_repaired_with_allowed_columns(monkeypatch):
    calls = []
    def model(task, data, schema):
        calls.append(deepcopy(data))
        return BAD if len(calls) == 1 else GOOD
    monkeypatch.setattr(llm, 'structured', model)
    result, diagnostic = column_mapping.discover(sample(), lambda _: None)
    assert result == GOOD and diagnostic['method'] == 'llm'
    assert len(calls) == diagnostic['attempts'] == 2
    assert calls[1]['allowed_column_ids'] == ['0', '1']
    assert 'Арматура' in calls[1]['previous_response']
    assert calls[1]['validation_error'] and calls[1]['repair_instruction']


def test_json_schema_error_is_repaired(monkeypatch):
    calls = []
    def model(*args):
        calls.append(1)
        if len(calls) == 1:
            raise llm.LLMResponseError('Invalid JSON schema', BAD)
        return GOOD
    monkeypatch.setattr(llm, 'structured', model)
    result, diagnostic = column_mapping.discover(sample(), lambda _: None)
    assert result == GOOD and diagnostic['attempts'] == 2
    assert diagnostic['method'] == 'llm'


@pytest.mark.parametrize('answer', [BAD, None, {}, {**GOOD, 'category_column': 'C'},
                                    {**GOOD, 'header_row': 100}, {**GOOD, 'name_columns': ['B', '1']}])
def test_repeated_bad_output_uses_reviewable_headerless_fallback(monkeypatch, answer):
    monkeypatch.setattr(llm, 'structured', lambda *args: answer)
    result, diagnostic = column_mapping.discover(sample(), lambda _: None)
    assert result == GOOD
    assert diagnostic['method'] == 'deterministic_fallback'
    assert diagnostic['requires_review'] and diagnostic['attempts'] == 2


def test_fallback_preserves_known_header_and_late_columns(monkeypatch):
    monkeypatch.setattr(llm, 'structured', lambda *args: BAD)
    data = sample()
    data['first_rows'][0]['cells'] = {'0': 'Группа', '1': 'Описание'}
    mapping, _ = column_mapping.discover(data, lambda _: None)
    assert mapping == {**GOOD, 'header_row': 1, 'name_columns': ['1']}
    data = sample(); data['available_columns'] = ['0', '1', '2']
    mapping, _ = column_mapping.discover(data, lambda _: None)
    assert mapping['name_columns'] == ['0', '1', '2']


@pytest.mark.parametrize('error', [llm.LLMError('Credentials'), llm.LLMTemporaryError('Service unavailable'),
                                  RuntimeError('Code defect'), Cancelled()])
def test_infrastructure_and_cancellation_do_not_become_successful_mapping(monkeypatch, error):
    calls = []
    def fail(*args):
        calls.append(1)
        raise error
    monkeypatch.setattr(llm, 'structured', fail)
    with pytest.raises(type(error)):
        column_mapping.discover(sample(), lambda _: None)
    assert len(calls) == 1


def upload(client, auth, monkeypatch, profile):
    objects = {}
    monkeypatch.setattr(storage, 'put', lambda key, data, *args, **kwargs: objects.__setitem__(key, data))
    monkeypatch.setattr(storage, 'get', lambda key: objects[key])
    fake_redis(monkeypatch)
    sid = client.post(API+'/procurement/sessions', headers=auth, json={
        'title': 'Mapping recovery', 'llm_profile_id': profile, 'normalization_method': 'llm',
        'taxonomy_enabled': False}).json()['id']
    book = Workbook(); book.active.title = 'Лист1'
    for n in range(100):
        book.active.append([GROUPS[n % len(GROUPS)], f'Кабель {n+1} 3х16'])
    out = BytesIO(); book.save(out)
    for kind, name, data in [('residuals', 'stock.xlsx', out.getvalue()), ('requirement', 'need.txt', 'Кабель'.encode())]:
        response = client.post(f'{API}/procurement/sessions/{sid}/upload-{kind}', headers=auth, files={'file': (name, data)})
        assert response.status_code == 201
    return sid


@pytest.mark.parametrize('profile', [p.profile_id for p in profiles()])
def test_worker_fallback_keeps_all_100_rows_and_waits_for_manual_approval(client, auth, monkeypatch, profile):
    sid = upload(client, auth, monkeypatch, profile)
    calls = []
    def wrong(task, data, schema):
        assert llm.current_config().profile_id == profile
        calls.append(1)
        raise llm.LLMResponseError('Invalid mapped column', BAD)
    monkeypatch.setattr(llm, 'structured', wrong)
    response = client.post(f'{API}/pipeline/{sid}/prepare', headers=auth)
    assert response.status_code == 202
    jid = response.json()['task_id']; tasks.run_job.run(jid)
    with SessionLocal() as db:
        job = db.get(Job, jid); session = db.get(ProcurementSession, sid)
        assert job.status == 'COMPLETED', job.error
        assert job.params['column_mapping'][0]['method'] == 'deterministic_fallback'
        assert session.status == 'REVIEW_REQUIRED' and not session.schema_doc['mapping_approved']
        assert session.normalization_method == 'llm' and session.taxonomy_enabled is False
        assert session.llm_config['profile_id'] == profile
        mapping = session.schema_doc['mappings'][0]
        assert {k: mapping[k] for k in GOOD} == GOOD
        rows = list(db.scalars(select(Item).where(Item.session_id == sid)))
        assert len(rows) == 100 and min(i.row_index for i in rows) == 1
        assert all(i.passport is None for i in rows)
    assert len(calls) == 2
    assert client.post(f'{API}/pipeline/{sid}/start-normalization', headers=auth, json={}).status_code == 409
    # Keep validation active for manual input too; invalid edits cannot be saved.
    draft = client.get(f'{API}/pipeline/{sid}/schema', headers=auth).json()
    draft['mappings'][0]['name_columns'] = [GROUPS[0]]
    assert client.put(f'{API}/pipeline/{sid}/schema', headers=auth, json=draft).status_code == 422
    assert client.post(f'{API}/pipeline/{sid}/approve-mapping', headers=auth).status_code == 200


def test_failed_prepare_can_be_retried_without_duplicate_or_deleted_rows(client, auth, monkeypatch):
    sid = upload(client, auth, monkeypatch, 'qwen25-7b-q4')
    def credentials_error(*args):
        raise llm.LLMError('Credentials')
    monkeypatch.setattr(llm, 'structured', credentials_error)
    jid = client.post(f'{API}/pipeline/{sid}/prepare', headers=auth).json()['task_id']
    tasks.run_job.run(jid)
    with SessionLocal() as db:
        before = {i.id: deepcopy(i.raw) for i in db.scalars(select(Item).where(Item.session_id == sid))}
        assert len(before) == 100 and db.get(Job, jid).status == 'FAILED'
    monkeypatch.setattr(llm, 'structured', lambda *args: BAD)
    assert client.post(f'{API}/pipeline/{sid}/jobs/{jid}/retry', headers=auth).status_code == 202
    tasks.run_job.run(jid)
    with SessionLocal() as db:
        after = {i.id: i.raw for i in db.scalars(select(Item).where(Item.session_id == sid))}
        assert after == before and db.get(Job, jid).status == 'COMPLETED'

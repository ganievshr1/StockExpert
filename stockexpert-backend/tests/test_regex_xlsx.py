from copy import deepcopy
import csv
import hashlib
from io import BytesIO, StringIO
import json
from types import SimpleNamespace
from uuid import uuid4

from openpyxl import Workbook
import pytest
from sqlalchemy import select
from app.db import SessionLocal, NormalizationExperiment, User, Organization, public
from app.core.security import create_access_token
from app.services.regex_excel import parse_stock_xlsx, HEADERS
from app.services.regex_experiments import run_experiment
from test_regex_experiments import setup, saved_state


def workbook(rows=None, headers=HEADERS):
    w = Workbook(); w.active.title = 'Лист 1'
    w.active.append(headers)
    for row in rows or [('Компания 1', 'Группа из Excel', 'Кабель КПвПпБП-150 3х70-6,0кВ'),
                        ('Компания 2', 'Кабель 99х999 999В', 'Изделие неизвестное')]:
        w.active.append(row)
        if isinstance(row[0], str) and row[0].startswith('='):
            w.active.cell(w.active.max_row, 1).data_type = 's'
    data = BytesIO(); w.save(data); w.close()
    return data.getvalue()


def upload(client, auth, data, request_id=None, dataset_id=None):
    form = {'request_id': request_id or str(uuid4())}
    if dataset_id: form['dataset_id'] = dataset_id
    return client.post('/api/v1/developer/regex-experiments/xlsx', headers=auth,
                       data=form, files={'file': ('stock.xlsx', data, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})


def test_parser_preserves_row_coordinates_and_only_uses_description():
    data = workbook()
    p, digest = parse_stock_xlsx(data, 'stock.xlsx')
    assert len(p['rows']) == 2 and p['rows'][0]['row'] == 2
    assert p['rows'][1]['source_text'] == 'Изделие неизвестное'
    assert p['rows'][1]['raw']['1'] == 'Кабель 99х999 999В'
    assert p['rows'][0]['source_sha256'] == hashlib.sha256(data).hexdigest()
    assert p['mappings'][0]['name_columns'] == ['2']
    assert parse_stock_xlsx(data, 'stock.xlsx') == (p, digest)


@pytest.mark.parametrize('data', [b'not xlsx', workbook(headers=['Компания', 'Группа', 'Описание']),
    workbook([('A', 'B', None)]), workbook([('A', 'B', '=1+1')])])
def test_bad_excel_is_rejected(data):
    with pytest.raises(ValueError): parse_stock_xlsx(data, 'stock.xlsx')


def test_upload_without_reference_keeps_history_and_no_models(client, auth, monkeypatch):
    setup(client, auth)
    with SessionLocal() as db: before = saved_state(db)
    for path in ('app.services.llm.structured', 'app.services.llm.current_config', 'app.services.model_registry.preflight'):
        monkeypatch.setattr(path, lambda *a, **k: pytest.fail('XLSX regex used a model'))
    data = workbook(); key = str(uuid4())
    response = upload(client, auth, data, key)
    assert response.status_code == 202, response.text
    ident = response.json()['id']
    assert response.json()['dataset_id'] is None and response.json()['source_type'] == 'xlsx'
    assert upload(client, auth, data, key).json()['id'] == ident
    assert upload(client, auth, workbook([('A', 'B', 'Changed')]), key).status_code == 409
    run_experiment(ident)
    with SessionLocal() as db:
        assert saved_state(db) == before
        e = db.get(NormalizationExperiment, ident)
        assert e.status == 'COMPLETED' and e.processed_rows == 2
        assert all(v is None for v in e.result['metrics'].values())
        assert e.result['evaluation_status'] == 'NO_REFERENCE'
        assert e.result['runtime']['llm_calls'] == 0
        assert e.predictions[1]['passport']['attributes'] == {'name': 'Изделие неизвестное'}
        assert e.result['extraction']['rows_with_parameters'] == 1
        original = deepcopy(public(e))
    run_experiment(ident)
    with SessionLocal() as db: assert public(db.get(NormalizationExperiment, ident)) == original
    rows = client.get(f'/api/v1/developer/regex-experiments/{ident}/passports', headers=auth).json()
    assert rows['total'] == 2 and rows['items'][0]['company'] == 'Компания 1'
    assert rows['items'][0]['evaluation'] is None
    reviewed = client.get(f'/api/v1/developer/regex-experiments/{ident}/passports?review_only=true', headers=auth).json()
    assert reviewed['total'] == 1
    for fmt in ('json', 'csv', 'passports_csv'):
        assert client.get(f'/api/v1/developer/regex-experiments/{ident}/export?format={fmt}', headers=auth).status_code == 200


def test_optional_reference_checks_hash_and_uses_shared_scoring(client, auth):
    data = workbook(); sha = hashlib.sha256(data).hexdigest()
    ref = {'format': 'stockexpert-expert-v1', 'title': 'Manual test reference', 'version': '1',
           'sources': [{'filename': 'stock.xlsx', 'sha256': sha, 'kind': 'residual'}],
           'features': [{'key': 'voltage_v', 'label': 'Напряжение', 'unit': 'V'}],
           'passports': [{'id': 'r1', 'source_sha256': sha, 'kind': 'residual', 'sheet': 'Лист 1', 'row': 2,
                         'source_name': 'Кабель КПвПпБП-150 3х70-6,0кВ', 'name': 'Кабель КПвПпБП-150 3х70-6,0кВ',
                         'attributes': {'voltage_v': {'value': 6000, 'evidence': '6,0кВ'}}}], 'pairs': [], 'notes': []}
    response = client.post('/api/v1/developer/references', headers=auth, files={'file': ('ref.json', json.dumps(ref).encode(), 'application/json')})
    assert response.status_code == 200, response.text
    rid = response.json()['id']
    assert upload(client, auth, workbook([('A','B','other')]), dataset_id=rid).status_code == 422
    response = upload(client, auth, data, dataset_id=rid)
    assert response.status_code == 202, response.text
    ident = response.json()['id']; run_experiment(ident)
    result = client.get(f'/api/v1/developer/regex-experiments/{ident}', headers=auth).json()['result']
    assert result['evaluation_status'] == 'EVALUATED'
    assert result['metrics']['h1_recall'] == 1 and result['coverage']['expected_stock'] == 1


def test_xlsx_role_tenant_and_csv_boundaries(client, auth):
    data = workbook([('=DANGER()', '+group', 'Кабель КПпБП-150 3х70-6кВ')])
    ident = upload(client, auth, data).json()['id']
    run_experiment(ident)
    csv_data = client.get(f'/api/v1/developer/regex-experiments/{ident}/export?format=passports_csv', headers=auth).text
    row = next(csv.DictReader(StringIO(csv_data.lstrip('\ufeff')), delimiter=';'))
    assert row['company'] == "'=DANGER()" and row['source_group'] == "'+group"
    with SessionLocal() as db:
        org = Organization(name='other'); db.add(org); db.flush()
        user = User(username='other', password_hash='test', role='admin', organization_id=org.id)
        db.add(user); db.commit(); token = create_access_token(user)
    assert client.get(f'/api/v1/developer/regex-experiments/{ident}', headers={'Authorization': 'Bearer '+token}).status_code == 404
    with SessionLocal() as db: db.get(User, 1).role = 'buyer'; db.commit()
    assert upload(client, auth, data).status_code == 403

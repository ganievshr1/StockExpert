# ruff: noqa: F401,F811 -- imported pytest fixtures are discovered by pytest
"""Regressions for the redesigned UI contract and two-file workflow."""

from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from openpyxl import Workbook, load_workbook
from sqlalchemy import select

from app.db import (
    Document,
    Item,
    Job,
    Organization,
    ProcurementSession,
    SessionLocal,
    User,
)
from app.services import storage
from app.services.parsing import document_text
from test_quality_review import run, save
from test_reusable_bases import base


def new_session(client, auth, title="Новая проверка"):
    response = client.post(
        "/api/v1/procurement/sessions", headers=auth, json={"title": title}
    )
    assert response.status_code == 201
    return response.json()["id"]


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "cp1251"])
def test_russian_questionnaire_encodings(encoding):
    text = "Нужен кабель 3х16, напряжение 3,3 кВ"
    assert document_text(text.encode(encoding), "опрос.txt") == text


def test_workspace_paging_filters_and_tenant_boundary(client, auth):
    first = new_session(client, auth, "Склад 100%")
    new_session(client, auth, "Склад 1000")
    foreign = new_session(client, auth, "Чужой склад")
    with SessionLocal() as db:
        organization = Organization(name="Другая организация")
        db.add(organization)
        db.flush()
        db.get(ProcurementSession, foreign).organization_id = organization.id
        db.get(ProcurementSession, first).created_at = datetime.now(
            timezone.utc
        ) - timedelta(days=40)
        db.commit()
    endpoint = "/api/v1/workspace/sessions"
    page = client.get(endpoint + "?limit=1", headers=auth).json()
    assert page["total"] == 2 and len(page["items"]) == 1
    assert (
        client.get(endpoint, params={"q": "100%"}, headers=auth).json()["items"][0][
            "id"
        ]
        == first
    )
    assert client.get(endpoint + "?days=30", headers=auth).json()["total"] == 1
    assert client.get(endpoint + "?status=COMPLETED", headers=auth).json()["total"] == 0
    assert client.get(endpoint + "?offset=2", headers=auth).json()["items"] == []
    overview = client.get("/api/v1/workspace/overview", headers=auth).json()
    assert overview["total_sessions"] == 2
    assert (
        client.get(f"/api/v1/audit/{foreign}/search", headers=auth).status_code == 404
    )
    assert (
        client.get(f"/api/v1/reports/{foreign}/document", headers=auth).status_code
        == 404
    )


def test_audit_search_by_event_author_and_date(client, auth):
    sid = new_session(client, auth, "Проверка фильтров")
    endpoint = f"/api/v1/audit/{sid}/search"
    result = client.get(
        endpoint,
        params={"q": "Проверка фильтров", "event_type": "SESSION_CREATED"},
        headers=auth,
    ).json()
    assert result["total"] == 1
    author = result["events"][0]["user_id"]
    assert (
        client.get(endpoint, params={"author_id": author}, headers=auth).json()["total"]
        == 1
    )
    assert client.get(endpoint + "?author_id=0", headers=auth).json()["total"] == 0
    assert (
        client.get(endpoint + "?date_from=2100-01-01", headers=auth).json()["total"]
        == 0
    )
    assert (
        client.get(
            endpoint + "?date_from=2026-02-01&date_to=2026-01-01", headers=auth
        ).status_code
        == 422
    )


def test_atomic_source_replacement_and_processing_lock(client, auth, monkeypatch):
    objects = {}
    monkeypatch.setattr(
        storage, "put", lambda key, data, *a, **kw: objects.__setitem__(key, data)
    )
    monkeypatch.setattr(storage, "delete_key", lambda key: objects.pop(key, None))
    sid = new_session(client, auth)
    endpoint = f"/api/v1/procurement/sessions/{sid}"

    def spreadsheet(value):
        workbook = Workbook()
        workbook.active.append(["Наименование"])
        workbook.active.append([value])
        output = BytesIO()
        workbook.save(output)
        return output.getvalue()

    response = client.post(
        endpoint + "/upload-residuals",
        headers=auth,
        files={"file": ("stock.xlsx", spreadsheet("Старый кабель"))},
    )
    old = response.json()
    assert response.status_code == 201
    replace = endpoint + "/documents/" + old["id"] + "/replace"
    assert (
        client.post(
            replace, headers=auth, files={"file": ("bad.xlsx", b"broken")}
        ).status_code
        == 422
    )
    assert (
        client.get(endpoint + "/documents", headers=auth).json()[0]["id"] == old["id"]
    )
    result = client.post(
        replace, headers=auth, files={"file": ("new.xlsx", spreadsheet("Новый кабель"))}
    )
    assert result.status_code == 200, result.text
    with SessionLocal() as db:
        assert db.get(Document, old["id"]) is None
        rows = list(db.scalars(select(Item).where(Item.session_id == sid)))
        assert len(rows) == 2 and rows[1].raw["0"] == "Новый кабель"
        db.add(Job(session_id=sid, kind="prepare", status="COMPLETED"))
        db.commit()
    assert len(objects) == 1
    new_replace = endpoint + "/documents/" + result.json()["id"] + "/replace"
    assert (
        client.post(
            new_replace,
            headers=auth,
            files={"file": ("third.xlsx", spreadsheet("Третий"))},
        ).status_code
        == 409
    )


def test_taxonomy_additions_validate_and_lock_after_approval(client, auth):
    sid = new_session(client, auth)
    schema = {
        "taxonomy_mode": "passports",
        "taxonomy_status": "review",
        "mapping_approved": True,
        "features": [
            {
                "key": "name",
                "label": "Наименование",
                "kind": "string",
                "unit": "none",
                "comparison": "informational",
                "tolerance": 0,
            }
        ],
        "categories": [
            {
                "id": "cable",
                "name": "Кабель",
                "parent_id": None,
                "feature_keys": ["name"],
            }
        ],
        "mappings": [],
    }
    with SessionLocal() as db:
        db.get(ProcurementSession, sid).schema_doc = schema
        db.commit()
    endpoint = f"/api/v1/taxonomy/{sid}"
    result = client.post(
        endpoint + "/categories",
        headers=auth,
        json={"name": "Силовой", "parent_id": "cable"},
    )
    assert result.status_code == 201, result.text
    assert (
        client.post(
            endpoint + "/categories",
            headers=auth,
            json={"name": "Силовой", "parent_id": "cable"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            endpoint + "/categories",
            headers=auth,
            json={"name": "Ошибка", "parent_id": "unknown"},
        ).status_code
        == 422
    )
    payload = {
        "category_id": "cable",
        "feature": {
            "key": "voltage",
            "label": "Напряжение",
            "kind": "number",
            "unit": "V",
            "comparison": "eq",
            "tolerance": 0,
        },
    }
    result = client.post(endpoint + "/features", headers=auth, json=payload)
    assert result.status_code == 201, result.text
    assert all(
        "voltage" in category["feature_keys"]
        for category in result.json()["categories"]
    )
    assert (
        client.post(endpoint + "/features", headers=auth, json=payload).status_code
        == 409
    )
    with SessionLocal() as db:
        user = db.scalar(select(User))
        user.role = "buyer"
        db.commit()
    assert (
        client.post(
            endpoint + "/categories", headers=auth, json={"name": "Запрет"}
        ).status_code
        == 403
    )
    with SessionLocal() as db:
        user = db.scalar(select(User))
        user.role = "admin"
        db.get(ProcurementSession, sid).approved = True
        db.commit()
    assert (
        client.post(
            endpoint + "/categories", headers=auth, json={"name": "Запрет"}
        ).status_code
        == 409
    )


def test_quality_queue_search_and_saved_review_filter(client, auth, run):
    sid, jid, mid, stock, requirement = run
    endpoint = f"/api/v1/quality/{sid}/targets"
    assert (
        client.get(endpoint + "?kind=candidate&unreviewed=true", headers=auth).json()[
            "total"
        ]
        == 1
    )
    assert save(client, auth, run, "candidate", mid, suitable=True).status_code == 200
    assert (
        client.get(endpoint + "?kind=candidate&unreviewed=true", headers=auth).json()[
            "total"
        ]
        == 0
    )
    result = client.get(
        endpoint,
        headers=auth,
        params={"kind": "candidate", "q": "Кабель", "requirement_id": requirement},
    ).json()
    assert result["total"] == 1 and result["items"][0]["review"]["answer"]["suitable"]
    assert (
        client.get(endpoint + "?kind=passport&q=Missing", headers=auth).json()["total"]
        == 0
    )


def test_report_sections_and_spreadsheet_literal_values(client, auth, run):
    sid, _, _, stock, _ = run
    with SessionLocal() as db:
        db.get(Item, stock).source_text = '=HYPERLINK("http://invalid")'
        db.commit()
    endpoint = f"/api/v1/reports/{sid}/document"
    params = {
        "format": "xlsx",
        "summary": False,
        "candidates": True,
        "evidence": False,
        "history": False,
    }
    response = client.get(endpoint, headers=auth, params=params)
    assert response.status_code == 200
    workbook = load_workbook(BytesIO(response.content))
    assert workbook.sheetnames == ["Кандидаты и решения"]
    assert (
        workbook.active["B2"].value.startswith("=HYPERLINK")
        and workbook.active["B2"].data_type == "s"
    )
    pdf = client.get(
        endpoint, headers=auth, params={**params, "format": "pdf", "inline": True}
    )
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
    assert pdf.headers["content-disposition"].startswith("inline")
    assert (
        client.get(
            endpoint, headers=auth, params={**params, "candidates": False}
        ).status_code
        == 422
    )

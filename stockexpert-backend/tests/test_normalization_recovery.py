from copy import deepcopy

import pytest
from sqlalchemy import select
from test_passport_taxonomy import execute
from test_passport_taxonomy import (
    mixed as mixed,  # noqa: PLC0414 - register the shared pytest fixture
)

from app.db import Item, Job, Match, ProcurementSession, SessionLocal
from app.services import (
    llm,
    passports,
    requirement_extraction,
    search,
    workflow,
)


def answer(name="Датчик уровня", **updates):
    return {"name": name, "name_evidence": name, "values": [], **updates}


def voltage(value="220", evidence="220 В"):
    return {
        "key": "voltage",
        "label": "Напряжение",
        "kind": "number",
        "value": value,
        "unit": "В",
        "target_unit": "V",
        "evidence": evidence,
    }


def test_repair_supplies_source_previous_answer_and_diagnostic(monkeypatch):
    calls = []

    def model(task, data, schema):
        calls.append(deepcopy(data))
        return (
            answer("Насос", name_evidence="Датчик уровня")
            if len(calls) == 1
            else answer()
        )

    monkeypatch.setattr(llm, "structured", model)
    p, issues = passports.extract("Датчик уровня")
    assert not issues and not passports.needs_review(p)
    assert p["normalization"]["attempts"] == 2
    assert calls[1]["source"] == "Датчик уровня"
    assert calls[1]["previous_response"]["name"] == "Насос"
    assert calls[1]["validation_error"]["disputed_name"] == "Насос"
    assert "repair_instruction" in calls[1]


def test_exhausted_name_repair_retains_only_supported_properties(monkeypatch):
    source = "Датчик уровня 220 В"
    raw = answer(
        "Придуманный насос",
        name_evidence=source,
        values=[
            voltage(),
            {
                "key": "brand",
                "label": "Марка",
                "kind": "string",
                "value": "Imaginary",
                "unit": "none",
                "target_unit": "none",
                "evidence": source,
            },
        ],
    )
    calls = []
    monkeypatch.setattr(llm, "structured", lambda *args: calls.append(1) or raw)
    p, issues = passports.extract(source)
    assert len(calls) == 3 and passports.needs_review(p)
    assert p["name"] == source and p["evidence"]["name"]["quote"] == source
    assert p["normalized_attributes"]["voltage"] == 220
    assert p["normalized_attributes"]["brand"] is None and "brand" not in p["evidence"]
    assert p["normalization"]["status"] == "partial"
    assert issues[-1]["disputed_name"] == "Придуманный насос"


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {},
        answer(" "),
        answer(values=[{}]),
        answer(values=[voltage(), voltage("24", "24 В")]),
    ],
)
def test_malformed_responses_and_duplicate_fields_use_grounded_fallback(
    monkeypatch, raw
):
    monkeypatch.setattr(llm, "structured", lambda *args: raw)
    p, issues = passports.extract("Датчик уровня 220 В")
    assert (
        passports.needs_review(p) and issues[-1]["code"] == "NORMALIZATION_NEEDS_REVIEW"
    )
    assert p["name"] in passports.clean(p["name_evidence"])
    assert p["name_evidence"] in "Датчик уровня 220 В"
    assert p["normalized_attributes"].get("voltage") is None


def test_json_schema_error_preserves_valid_subset(monkeypatch):
    def model(*args):
        raise llm.LLMResponseError("Invalid schema", answer(values=[voltage(), {}]))

    monkeypatch.setattr(llm, "structured", model)
    p, _ = passports.extract("Датчик уровня 220 В")
    assert passports.needs_review(p) and p["normalized_attributes"]["voltage"] == 220


def test_invalid_property_causes_repair_even_with_valid_name(monkeypatch):
    calls = []

    def model(*args):
        calls.append(1)
        return (
            answer(values=[voltage("999", "220 В")])
            if len(calls) == 1
            else answer(values=[voltage()])
        )

    monkeypatch.setattr(llm, "structured", model)
    p, _ = passports.extract("Датчик уровня 220 В")
    assert len(calls) == 2 and not passports.needs_review(p)
    assert p["normalized_attributes"]["voltage"] == 220


@pytest.mark.parametrize(
    "error",
    [
        llm.LLMError("Wrong credentials"),
        llm.LLMTemporaryError("Unavailable"),
        RuntimeError("Programming error"),
    ],
)
def test_infrastructure_and_programming_errors_are_not_silently_swallowed(
    monkeypatch, error
):
    def model(*args):
        raise error

    monkeypatch.setattr(llm, "structured", model)
    with pytest.raises(type(error), match=str(error)):
        passports.extract("Датчик уровня")


def test_untrusted_requirement_split_retains_whole_original(monkeypatch):
    calls = []
    monkeypatch.setattr(
        llm, "structured", lambda *args: calls.append(1) or {"items": ["invented"]}
    )
    source = "Строка 1\nСтрока 2"
    parts, issues = requirement_extraction.split(source)
    assert parts == [source] and len(calls) == 3
    assert issues[0]["code"] == "REQUIREMENT_SPLIT_NEEDS_REVIEW"


def prepare(client, auth, mixed):
    sid = mixed[0]
    execute(client, auth, sid, f"pipeline/{sid}/prepare", workflow.prepare)
    assert (
        client.post(f"/api/v1/pipeline/{sid}/approve-mapping", headers=auth).status_code
        == 200
    )
    return sid


def test_bad_row_continues_with_location_review_api_and_safe_matching(
    client, auth, mixed, monkeypatch
):
    sid = prepare(client, auth, mixed)
    _, names, calls, model = mixed

    def bad_row(task, data, schema):
        raw = model(task, data, schema)
        if "source" in data and "Насос погружной P0 " in data["source"]:
            raw["name"] = "Неверное имя"
        return raw

    monkeypatch.setattr(llm, "structured", bad_row)
    jid = execute(
        client, auth, sid, f"pipeline/{sid}/start-normalization", workflow.normalize_all
    )
    review = client.get(
        f"/api/v1/pipeline/{sid}/normalization-review", headers=auth
    ).json()
    assert review["total"] == 1
    row = review["items"][0]
    location = row["passport"]["source_location"]
    assert (
        location["document"] == "stock.xlsx"
        and location["sheet"] == "Sheet"
        and location["row"] == 2
    )
    assert row["issues"][-1]["disputed_name"] == "Неверное имя"
    assert row["issues"][-1]["document"] == "stock.xlsx"
    assert row["category"] == passports.REVIEW_CATEGORY_ID
    assert all(x["name"] != row["passport"]["name"] for x in calls["taxonomy"])
    with SessionLocal() as db:
        assert db.get(Job, jid).params["normalization_review_count"] == 1
        assert (
            len(
                list(
                    db.scalars(
                        select(Item).where(
                            Item.passport["attributes"].as_string().is_not(None)
                        )
                    )
                )
            )
            == len(names) + 1
        )
    assert (
        client.get(
            f"/api/v1/pipeline/{sid}/normalization-review?offset=1", headers=auth
        ).json()["items"]
        == []
    )
    assert client.get(f"/api/v1/pipeline/{sid}/normalization-review").status_code == 401
    assert (
        client.post(
            f"/api/v1/taxonomy/{sid}/approve",
            headers=auth,
            json={"freeze_version": "v1"},
        ).status_code
        == 200
    )
    monkeypatch.setattr(llm, "unload", lambda: None)
    monkeypatch.setattr(search, "index_items", lambda *args: {})
    monkeypatch.setattr(
        search, "retrieve", lambda sid, query, items, limit: {i.id: 1.0 for i in items}
    )
    monkeypatch.setattr(search, "rerank", lambda *args: {})
    execute(client, auth, sid, f"matching/{sid}/run", workflow.match_all)
    with SessionLocal() as db:
        match = db.scalar(select(Match).where(Match.item_id == row["id"]))
        assert (
            match.result["state"] == "NEEDS_REVIEW"
            and match.result["normalization_review"]
        )
        assert match.result["is_shortlisted"]

    # Resume never calls extraction again for either a successful or fallback passport.
    def no_extraction(*args):
        raise AssertionError("Already saved passport was re-extracted")

    monkeypatch.setattr(workflow, "extract_one", no_extraction)
    with SessionLocal() as db:
        workflow.normalize_all(
            db, db.get(ProcurementSession, sid), db.get(Job, jid), lambda _: None
        )


def test_all_bad_rows_still_produce_complete_review_taxonomy(
    client, auth, mixed, monkeypatch
):
    sid = prepare(client, auth, mixed)

    def broken(task, data, schema):
        if "text" in data:
            return {"items": ["invented fragment"]}
        if "source" in data:
            raise llm.LLMResponseError("Invalid JSON")
        raise AssertionError("Fallback rows must not invoke taxonomy LLM")

    monkeypatch.setattr(llm, "structured", broken)
    execute(
        client, auth, sid, f"pipeline/{sid}/start-normalization", workflow.normalize_all
    )
    page = client.get(
        f"/api/v1/pipeline/{sid}/normalization-review", headers=auth
    ).json()
    assert page["total"] == len(mixed[1]) + 1
    requirement = next(i for i in page["items"] if i["kind"] == "requirement")
    assert requirement["source_text"] == "Насос погружной R 220В"
    assert requirement["passport"]["source_location"]["source_line"] == 1
    with SessionLocal() as db:
        session = db.get(ProcurementSession, sid)
        assert session.status == "TAXONOMY_REVIEW"
        assert [c["id"] for c in session.schema_doc["categories"]] == [
            passports.REVIEW_CATEGORY_ID
        ]


def test_empty_mapped_cell_keeps_row_for_review(client, auth, mixed, monkeypatch):
    sid = prepare(client, auth, mixed)
    with SessionLocal() as db:
        item = db.scalar(
            select(Item).where(
                Item.session_id == sid, Item.kind == "residual", Item.row_index == 2
            )
        )
        item.raw = {"1": "Оригинальная строка в другом столбце"}
        db.commit()
    execute(
        client, auth, sid, f"pipeline/{sid}/start-normalization", workflow.normalize_all
    )
    page = client.get(
        f"/api/v1/pipeline/{sid}/normalization-review", headers=auth
    ).json()
    assert page["total"] == 1
    assert page["items"][0]["source_text"] == "1: Оригинальная строка в другом столбце"
    assert page["items"][0]["issues"][0]["code"] == "EMPTY_MAPPED_SOURCE"


def test_frozen_taxonomy_keeps_failed_new_requirement_without_invented_category(
    client, auth, mixed, monkeypatch
):
    sid = prepare(client, auth, mixed)
    execute(
        client, auth, sid, f"pipeline/{sid}/start-normalization", workflow.normalize_all
    )
    assert (
        client.post(
            f"/api/v1/taxonomy/{sid}/approve",
            headers=auth,
            json={"freeze_version": "v1"},
        ).status_code
        == 200
    )
    tid = client.post(
        "/api/v1/procurement/sessions",
        headers=auth,
        json={"title": "Reuse", "taxonomy_enabled": True, "base_session_id": sid},
    ).json()["id"]
    client.post(
        f"/api/v1/procurement/sessions/{tid}/upload-requirement",
        headers=auth,
        files={"file": ("new.txt", "Датчик уровня".encode())},
    )

    def broken(task, data, schema):
        if "text" in data:
            return {"items": [data["text"]]}
        if "source" in data:
            return answer("Придуманное название")
        raise AssertionError("Failed requirement must bypass classification")

    monkeypatch.setattr(llm, "structured", broken)
    execute(
        client, auth, tid, f"pipeline/{tid}/start-normalization", workflow.normalize_all
    )
    with SessionLocal() as db:
        session = db.get(ProcurementSession, tid)
        req = db.scalar(
            select(Item).where(Item.session_id == tid, Item.kind == "requirement")
        )
        assert session.status == "NORMALIZED" and req.category is None
        assert (
            passports.needs_review(req.passport)
            and req.passport["name"] == "Датчик уровня"
        )
        original = db.get(ProcurementSession, sid).schema_doc["categories"]
        structure = lambda cats: [
            {k: v for k, v in c.items() if k not in ("item_count", "requirement_count")}
            for c in cats
        ]
        assert structure(session.schema_doc["categories"]) == structure(original)


def test_normalization_review_rejects_other_organization(client, auth, mixed):
    from app.core.security import create_access_token
    from app.db import Organization, User

    with SessionLocal() as db:
        org = Organization(name="Other")
        db.add(org)
        db.flush()
        user = User(
            username="other",
            password_hash="unused",
            role="admin",
            organization_id=org.id,
        )
        db.add(user)
        db.commit()
        other = {"Authorization": "Bearer " + create_access_token(user)}
    response = client.get(
        f"/api/v1/pipeline/{mixed[0]}/normalization-review", headers=other
    )
    assert response.status_code in (403, 404)

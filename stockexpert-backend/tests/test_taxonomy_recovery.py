"""Classification recovery must preserve identity, row coverage and resumability."""

from collections import Counter
from copy import deepcopy

import pytest
from sqlalchemy import select
from test_passport_taxonomy import execute
from test_passport_taxonomy import mixed as mixed  # noqa: PLC0414

from app.core.config import settings
from app.db import Item, Job, Match, ProcurementSession, SessionLocal
from app.services import llm, passports, search, taxonomy, taxonomy_recovery, workflow


def inputs():
    return [
        {"item_id": "row-a", "name": "Насос 220В", "attributes": {}, "source_rows": 1},
        {"item_id": "row-b", "name": "Болт 10мм", "attributes": {}, "source_rows": 2},
    ]


def label(ident, material="Насос"):
    return {"item_id": ident, "material": material, "subtype": None}


def test_shuffled_answer_is_matched_by_id_and_schema_restricts_ids(monkeypatch):
    def model(task, data, schema):
        assert schema["$defs"]["MaterialLabel"]["properties"]["item_id"]["enum"] == [
            "row-a",
            "row-b",
        ]
        assert (
            schema["properties"]["items"]["minItems"]
            == schema["properties"]["items"]["maxItems"]
            == 2
        )
        return {"items": [label("row-b", "Болт"), label("row-a")]}

    monkeypatch.setattr(llm, "structured", model)
    rows = list(taxonomy_recovery.classify(inputs(), lambda _: None))
    assert {r.item_id: r.material for r in rows} == {"row-a": "Насос", "row-b": "Болт"}
    assert all(not r.requires_review and r.attempts == 1 for r in rows)


@pytest.mark.parametrize(
    "bad_entries",
    [
        [],
        [label("row-b"), label("row-b", "Труба")],
        [label("different-id")],
        [label("row-b", "...")],
        [{"item_id": "row-b", "material": 12}],
    ],
)
def test_retry_only_unresolved_rows(monkeypatch, bad_entries):
    calls = []

    def model(task, data, schema):
        calls.append(deepcopy(data))
        if len(calls) == 1:
            return {"items": [label("row-a"), *bad_entries]}
        return {"items": [label("row-b", "Болт")]}

    monkeypatch.setattr(llm, "structured", model)
    result = list(taxonomy_recovery.classify(inputs(), lambda _: None))
    assert len(calls) == 2 and calls[1]["required_item_ids"] == ["row-b"]
    assert calls[1]["passports"] == [inputs()[1]]
    assert calls[1]["previous_errors"] and calls[1]["repair_instruction"]
    assert {x.item_id for x in result} == {"row-a", "row-b"}
    assert all(not x.requires_review for x in result)
    assert next(x for x in result if x.item_id == "row-b").material == "Болт"


def test_foreign_extra_id_never_creates_a_row_or_replaces_valid_entries(monkeypatch):
    monkeypatch.setattr(
        llm,
        "structured",
        lambda *args: {
            "items": [label("row-a"), label("row-b", "Болт"), label("foreign", "Труба")]
        },
    )
    result = list(taxonomy_recovery.classify(inputs(), lambda _: None))
    assert {x.item_id for x in result} == {"row-a", "row-b"}
    assert all(x.attempts == 1 and not x.requires_review for x in result)
    assert result[0].errors[0]["unexpected_ids"] == ["foreign"]


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {},
        {"items": []},
        {"items": "invalid"},
        {"items": [{"item_id": "0", "material": "Труба"}]},
    ],
)
def test_exhaustion_uses_review_for_every_original_id(monkeypatch, raw):
    calls = []

    def model(task, data, schema):
        calls.extend(data["required_item_ids"])
        raise llm.LLMResponseError("Invalid schema", raw)

    monkeypatch.setattr(llm, "structured", model)
    result = list(taxonomy_recovery.classify(inputs(), lambda _: None))
    assert Counter(calls) == {"row-a": 3, "row-b": 3}
    assert len(result) == 2 and all(x.requires_review for x in result)
    assert all(
        x.material == passports.REVIEW_MATERIAL and x.attempts == 3 for x in result
    )
    assert all(len(x.errors) == 3 for x in result)


def test_schema_invalid_response_can_still_salvage_unambiguous_entries(monkeypatch):
    calls = []

    def model(task, data, schema):
        calls.append(data["required_item_ids"])
        if len(calls) == 1:
            raise llm.LLMResponseError(
                "Invalid schema", {"items": [label("row-a"), {"item_id": "row-b"}]}
            )
        return {"items": [label("row-b", "Болт")]}

    monkeypatch.setattr(llm, "structured", model)
    result = list(taxonomy_recovery.classify(inputs(), lambda _: None))
    assert calls == [["row-a", "row-b"], ["row-b"]]
    assert all(not x.requires_review for x in result)


def test_attempt_limit_is_configurable(monkeypatch):
    monkeypatch.setattr(settings, "TAXONOMY_MAX_ATTEMPTS", 1)
    calls = []
    monkeypatch.setattr(
        llm, "structured", lambda *args: calls.append(1) or {"items": []}
    )
    result = list(taxonomy_recovery.classify(inputs(), lambda _: None))
    assert len(calls) == 1 and all(
        x.requires_review and x.attempts == 1 for x in result
    )


@pytest.mark.parametrize(
    "error",
    [llm.LLMTemporaryError("outage"), llm.LLMError("auth"), RuntimeError("bug")],
)
def test_infrastructure_and_programming_errors_propagate(monkeypatch, error):
    def fail(*args):
        raise error

    monkeypatch.setattr(llm, "structured", fail)
    with pytest.raises(type(error), match=str(error)):
        list(taxonomy_recovery.classify(inputs(), lambda _: None))


def prepare(client, auth, mixed):
    sid = mixed[0]
    execute(client, auth, sid, f"pipeline/{sid}/prepare", workflow.prepare)
    assert (
        client.post(f"/api/v1/pipeline/{sid}/approve-mapping", headers=auth).status_code
        == 200
    )
    return sid


def test_duplicate_passports_keep_both_source_rows_and_review_flags(
    client, auth, mixed, monkeypatch
):
    sid = prepare(client, auth, mixed)
    model = mixed[3]

    def omit_sensor(task, data, schema):
        result = model(task, data, schema)
        if "passports" in data:
            omitted = {
                x["item_id"]
                for x in data["passports"]
                if x["name"].startswith("Датчик")
            }
            result["items"] = [
                x for x in result["items"] if x["item_id"] not in omitted
            ]
        return result

    monkeypatch.setattr(llm, "structured", omit_sensor)
    jid = execute(
        client, auth, sid, f"pipeline/{sid}/start-normalization", workflow.normalize_all
    )
    page = client.get(
        f"/api/v1/pipeline/{sid}/normalization-review", headers=auth
    ).json()
    assert page["total"] == 2
    assert {x["row_index"] for x in page["items"]} == {19, 20}
    assert all(x["category"] == passports.REVIEW_CATEGORY_ID for x in page["items"])
    assert all(
        x["passport"]["normalization"]["requires_review"] is False
        for x in page["items"]
    )
    assert all(
        x["passport"]["normalized_attributes"]["size"] == 24 for x in page["items"]
    )
    for row in page["items"]:
        issue = next(x for x in row["issues"] if x["code"] == "TAXONOMY_NEEDS_REVIEW")
        assert issue["document"] == "stock.xlsx" and issue["sheet"] == "Sheet"
        assert issue["row"] == row["row_index"] and issue["item_id"] == row["id"]
    with SessionLocal() as db:
        session = db.get(ProcurementSession, sid)
        assert session.schema_doc["analyzed_items"] == len(mixed[1]) + 1
        assert sum(
            c["item_count"]
            for c in session.schema_doc["categories"]
            if c["parent_id"] is None
        ) == len(mixed[1])
        assert db.get(Job, jid).params["taxonomy_review_count"] == 2
    # A normal retry reuses both successes and review fallbacks; explicit rebuild retries failures.
    extraction_count = len(mixed[2]["extract"])
    taxonomy_before = len(mixed[2]["taxonomy"])
    with SessionLocal() as db:
        workflow.normalize_all(
            db, db.get(ProcurementSession, sid), db.get(Job, jid), lambda _: None
        )
    assert len(mixed[2]["taxonomy"]) == taxonomy_before
    monkeypatch.setattr(llm, "structured", model)
    execute(client, auth, sid, f"taxonomy/{sid}/rebuild", taxonomy.build)
    assert len(mixed[2]["extract"]) == extraction_count
    assert len(mixed[2]["taxonomy"]) == taxonomy_before + 1
    assert (
        client.get(f"/api/v1/pipeline/{sid}/normalization-review", headers=auth).json()[
            "total"
        ]
        == 0
    )
    with SessionLocal() as db:
        sensors = list(
            db.scalars(
                select(Item).where(Item.session_id == sid, Item.row_index.in_([19, 20]))
            )
        )
        assert all(
            not any(x["code"] == "TAXONOMY_NEEDS_REVIEW" for x in i.issues)
            for i in sensors
        )


def test_all_failed_classifications_finish_and_cannot_pass_matching(
    client, auth, mixed, monkeypatch
):
    sid = prepare(client, auth, mixed)
    model = mixed[3]

    def fail_classification(task, data, schema):
        if "passports" in data:
            return {"items": []}
        if "catalog" in data:
            raise AssertionError("Review group must not request equivalence")
        return model(task, data, schema)

    monkeypatch.setattr(llm, "structured", fail_classification)
    execute(
        client, auth, sid, f"pipeline/{sid}/start-normalization", workflow.normalize_all
    )
    with SessionLocal() as db:
        session = db.get(ProcurementSession, sid)
        assert session.status == "TAXONOMY_REVIEW"
        assert [x["id"] for x in session.schema_doc["categories"]] == [
            passports.REVIEW_CATEGORY_ID
        ]
        assert session.schema_doc["analyzed_items"] == len(mixed[1]) + 1
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
        matches = list(db.scalars(select(Match).where(Match.session_id == sid)))
        assert len(matches) == len(mixed[1])
        assert all(x.result["state"] == "NEEDS_REVIEW" for x in matches)
        assert all(x.result["is_shortlisted"] for x in matches)


def test_accepted_partial_batch_is_committed_before_later_outage(
    client, auth, mixed, monkeypatch
):
    sid = prepare(client, auth, mixed)
    model = mixed[3]
    seen, omitted = [], set()

    def fail_on_repair(task, data, schema):
        if "passports" not in data:
            return model(task, data, schema)
        seen.extend(x["item_id"] for x in data["passports"])
        if len(data["passports"]) == 1:
            raise llm.LLMTemporaryError("model outage")
        result = model(task, data, schema)
        omitted.add(result["items"][-1]["item_id"])
        result["items"] = result["items"][:-1]
        return result

    monkeypatch.setattr(llm, "structured", fail_on_repair)
    jid = client.post(
        f"/api/v1/pipeline/{sid}/start-normalization", headers=auth, json={}
    ).json()["task_id"]
    with SessionLocal() as db:
        with pytest.raises(llm.LLMTemporaryError):
            workflow.normalize_all(
                db, db.get(ProcurementSession, sid), db.get(Job, jid), lambda _: None
            )
        db.rollback()
        saved = {
            i.id
            for i in db.scalars(select(Item).where(Item.session_id == sid))
            if i.passport and i.passport.get("taxonomy_proposal", {}).get("signature")
        }
        assert saved and not saved & omitted
    requested = []

    def recovered(task, data, schema):
        if "passports" in data:
            requested.extend(x["item_id"] for x in data["passports"])
        return model(task, data, schema)

    monkeypatch.setattr(llm, "structured", recovered)
    extraction_count = len(mixed[2]["extract"])
    with SessionLocal() as db:
        workflow.normalize_all(
            db, db.get(ProcurementSession, sid), db.get(Job, jid), lambda _: None
        )
    assert not saved & set(requested) and omitted <= set(requested)
    assert len(mixed[2]["extract"]) == extraction_count


def test_oversized_passport_is_preserved_without_sending_large_context(
    client, auth, mixed, monkeypatch
):
    sid = prepare(client, auth, mixed)
    execute(
        client, auth, sid, f"pipeline/{sid}/start-normalization", workflow.normalize_all
    )
    with SessionLocal() as db:
        item = db.scalar(
            select(Item).where(
                Item.session_id == sid, Item.kind == "residual", Item.row_index == 2
            )
        )
        p = deepcopy(item.passport)
        p["normalized_attributes"]["long_description"] = "Описание " * 2000
        item.passport = p
        db.commit()

        def no_model(*args):
            raise AssertionError("Oversized input must not reach the model")

        monkeypatch.setattr(llm, "structured", no_model)
        taxonomy.propose(db, [item], lambda _: None)
        assert item.passport["classification"]["requires_review"]
        assert item.passport["classification"]["attempts"] == 0
        assert (
            item.passport["classification"]["errors"][0]["code"]
            == "TAXONOMY_INPUT_TOO_LARGE"
        )
        assert (
            item.passport["normalized_attributes"]["long_description"]
            == "Описание " * 2000
        )


def test_failed_classification_of_new_requirement_uses_frozen_base_safely(
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
        json={"title": "Reuse", "base_session_id": sid},
    ).json()["id"]
    assert (
        client.post(
            f"/api/v1/procurement/sessions/{tid}/upload-requirement",
            headers=auth,
            files={"file": ("new.txt", "Насос погружной R 220В".encode())},
        ).status_code
        == 201
    )
    model = mixed[3]

    def broken(task, data, schema):
        if "passports" in data:
            return {"items": []}
        if "catalog" in data:
            raise AssertionError(
                "Unclassified requirement must not be semantically assigned"
            )
        return model(task, data, schema)

    monkeypatch.setattr(llm, "structured", broken)
    execute(
        client, auth, tid, f"pipeline/{tid}/start-normalization", workflow.normalize_all
    )
    page = client.get(
        f"/api/v1/pipeline/{tid}/normalization-review", headers=auth
    ).json()
    assert page["total"] == 1 and page["items"][0]["kind"] == "requirement"
    assert page["items"][0]["category"] is None
    assert page["items"][0]["passport"]["normalized_attributes"]["size"] == 220
    with SessionLocal() as db:
        assert db.get(ProcurementSession, tid).status == "NORMALIZED"

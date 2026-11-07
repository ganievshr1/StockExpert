"""Functional multi-account coverage for the existing shared-organization model.

These tests use SQLite and controlled model/Qdrant/Redis boundaries. They verify
identity, routing and result scoping; they are not throughput measurements and
do not prove PostgreSQL row-lock or broker behavior under concurrent workers.
"""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app import tasks
from app.core.security import create_access_token
from app.db import (
    Audit, Document, Item, Job, Match, Organization, ProcurementSession,
    SessionLocal, User,
)
from test_reusable_bases import base
from app.services import inventory as inventory_service


def _actors(organization_id, count, prefix="b1"):
    """Create genuine distinct JWT subjects; no shared authentication override."""
    with SessionLocal() as db:
        users = [
            User(username=f"{prefix}-{i}", password_hash="unused-by-token-test",
                 role="buyer", organization_id=organization_id)
            for i in range(count)
        ]
        db.add_all(users)
        db.commit()
        return [
            (u.id, {"Authorization": "Bearer " + create_access_token(u)})
            for u in users
        ]


def _ready_snapshot(client, actor, source_id, number):
    uid, headers = actor
    response = client.post(
        "/api/v1/procurement/sessions", headers=headers,
        json={"title": f"B1 request {number}", "taxonomy_enabled": True,
              "base_session_id": source_id},
    )
    assert response.status_code == 201, response.text
    sid = response.json()["id"]
    assert response.json()["created_by"] == uid
    with SessionLocal() as db:
        session = db.get(ProcurementSession, sid)
        document = Document(
            session_id=sid, kind="requirement", filename=f"need-{number}.txt",
            object_key=f"test/{sid}/need.txt", sha256=f"{number:064x}", meta={},
        )
        db.add(document)
        db.flush()
        text = f"Кабель 3,3кВ; независимая потребность {number}"
        requirement = Item(
            session_id=sid, document_id=document.id, kind="requirement",
            sheet="", row_index=1, raw={"text": text}, source_text=text,
            passport={"attributes": {"voltage_v": 3300},
                      "category_id": "cable",
                      "evidence": {"voltage_v": {"quote": "3,3кВ"}}},
            category="cable", issues=[],
        )
        db.add(requirement)
        session.status = "NORMALIZED"
        db.commit()
        residual = db.scalar(select(Item).where(
            Item.session_id == sid, Item.kind == "residual", Item.row_index == 2,
        ))
        return {"sid": sid, "user_id": uid, "headers": headers,
                "requirement_id": requirement.id, "item_id": residual.id,
                "text": text}


@pytest.mark.parametrize("user_count", [1, 10], ids=["one-user-ten-jobs", "ten-users-one-job-each"])
def test_ten_independent_jobs_keep_users_sessions_and_results_separate(
    client, auth, base, user_count,
):
    source_id, _, model_calls, _, source_item_id = base
    with SessionLocal() as db:
        org_id = db.get(ProcurementSession, source_id).organization_id
    actors = _actors(org_id, user_count)
    assert len({uid for uid, _ in actors}) == user_count
    requests = [
        _ready_snapshot(client, actors[i % user_count], source_id, i + 1)
        for i in range(10)
    ]

    # Independent sessions can be submitted concurrently with distinct subjects.
    # SQLite serializes its writes; worker execution below is deliberately serial.
    def submit(request):
        response = client.post(
            f"/api/v1/matching/{request['sid']}/run",
            headers=request["headers"], json={},
        )
        assert response.status_code == 202, response.text
        return response.json()["task_id"]

    with ThreadPoolExecutor(max_workers=10) as pool:
        job_ids = list(pool.map(submit, requests))
    assert len(set(job_ids)) == 10
    assert len({request["sid"] for request in requests}) == 10
    assert len({request["requirement_id"] for request in requests}) == 10
    assert len({request["item_id"] for request in requests}) == 10

    for request, jid in zip(requests, job_ids):
        tasks.run_job.run(jid)
        # Redelivery after completion must not create another business result.
        tasks.run_job.run(jid)
        with SessionLocal() as db:
            job = db.get(Job, jid)
            assert job.status == "COMPLETED", job.error
            assert job.session_id == request["sid"]
            assert job.params["index_stats"]["vectors_reused"] == 1
            matches = list(db.scalars(select(Match).where(Match.job_id == jid)))
            assert len(matches) == 1
            result = matches[0]
            assert (result.session_id, result.requirement_id, result.item_id) == (
                request["sid"], request["requirement_id"], request["item_id"],
            )
            assert result.item_id != source_item_id
            request["match_id"] = result.id
            queued = db.scalar(select(Audit).where(
                Audit.session_id == request["sid"], Audit.event_type == "JOB_QUEUED",
            ))
            assert queued.user_id == request["user_id"]

        response = client.get(
            f"/api/v1/matching/{request['sid']}/candidates?shortlisted=false",
            headers=request["headers"],
        )
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 1
        result = response.json()["items"][0]
        assert result["job_id"] == jid
        assert result["requirement"]["source_text"] == request["text"]
        assert result["residual"]["id"] == request["item_id"]

    # A match UUID from another session must never resolve inside this session,
    # even though both sessions intentionally belong to the same organization.
    first, second = requests[:2]
    mismatch = client.get(
        f"/api/v1/matching/{first['sid']}/candidates/{second['match_id']}",
        headers=first["headers"],
    )
    assert mismatch.status_code == 404
    assert not model_calls, "Ready passports must not trigger generative extraction"
    with SessionLocal() as db:
        assert len(list(db.scalars(select(Match)))) == 10


def test_organization_shared_base_and_sessions_remain_accessible_to_colleagues(
    client, auth, base,
):
    source_id, *_ = base
    with SessionLocal() as db:
        org_id = db.get(ProcurementSession, source_id).organization_id
    owner, colleague = _actors(org_id, 2)
    target = _ready_snapshot(client, owner, source_id, 1)
    assert client.get(
        f"/api/v1/procurement/sessions/{target['sid']}", headers=colleague[1],
    ).status_code == 200
    bases = client.get("/api/v1/normalized-bases", headers=colleague[1])
    assert bases.status_code == 200
    assert source_id in {row["session_id"] for row in bases.json()}


def test_foreign_organization_cannot_read_start_or_reuse_a_shared_base(
    client, auth, base,
):
    source_id, *_ = base
    with SessionLocal() as db:
        foreign = Organization(name="Separate B1 organization")
        db.add(foreign)
        db.commit()
        foreign_id = foreign.id
    _, headers = _actors(foreign_id, 1, "foreign")[0]
    for path in (
        f"/api/v1/procurement/sessions/{source_id}",
        f"/api/v1/matching/{source_id}/status",
        f"/api/v1/matching/{source_id}/candidates",
    ):
        assert client.get(path, headers=headers).status_code == 404
    assert client.post(
        f"/api/v1/matching/{source_id}/run", headers=headers, json={},
    ).status_code == 404
    assert client.post(
        "/api/v1/procurement/sessions", headers=headers,
        json={"title": "Forbidden copy", "base_session_id": source_id},
    ).status_code == 404
    assert client.get("/api/v1/normalized-bases", headers=headers).json() == []
    with SessionLocal() as db:
        assert db.scalar(select(ProcurementSession).where(
            ProcurementSession.organization_id == foreign_id,
        )) is None


def test_active_job_rejects_second_colleague_submission(client, auth, base):
    source_id, *_ = base
    with SessionLocal() as db:
        org_id = db.get(ProcurementSession, source_id).organization_id
    owner, colleague = _actors(org_id, 2)
    target = _ready_snapshot(client, owner, source_id, 1)
    path = f"/api/v1/matching/{target['sid']}/run"
    first = client.post(path, headers=owner[1], json={})
    assert first.status_code == 202, first.text
    second = client.post(path, headers=colleague[1], json={})
    assert second.status_code == 409, second.text
    with SessionLocal() as db:
        jobs = list(db.scalars(select(Job).where(Job.session_id == target["sid"])))
        assert [job.id for job in jobs] == [first.json()["task_id"]]


def test_snapshots_use_the_same_inventory_origin_guard(client, auth, base):
    source_id, *_ = base
    with SessionLocal() as db:
        org_id = db.get(ProcurementSession, source_id).organization_id
    owner = _actors(org_id, 1)[0]
    first = _ready_snapshot(client, owner, source_id, 1)
    second = _ready_snapshot(client, owner, source_id, 2)
    with SessionLocal() as db:
        for sid in (source_id, first["sid"], second["sid"]):
            session = db.get(ProcurementSession, sid)
            assert inventory_service.stock_origin_ids(db, session) == {source_id}


def test_postgresql_inventory_guard_uses_sorted_transaction_locks(monkeypatch):
    """Verify lock scope/order, not PostgreSQL's runtime locking implementation."""
    roots = {"origin-a", "origin-b", "origin-c"}
    monkeypatch.setattr(inventory_service, "stock_origin_ids", lambda *_: roots)
    db = Mock()
    db.get_bind.return_value = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    session = SimpleNamespace(id="snapshot", organization_id=7)
    assert inventory_service.lock_stock_origins(db, session) == roots
    statements = [call.args[0] for call in db.execute.call_args_list]
    assert len(statements) == 3
    assert all("pg_advisory_xact_lock(" in str(statement) for statement in statements)
    keys = [next(iter(statement.compile().params.values())) for statement in statements]
    assert keys == sorted(set(keys))
    assert all(-(2**63) <= key < 2**63 for key in keys)
    db.commit.assert_not_called()  # Caller owns the transaction and lock lifetime.
    db.rollback.assert_not_called()


def test_clear_checks_jobs_after_inventory_guard_acquisition(client, auth, base, monkeypatch):
    """Model a job committed before the shared guard is obtained by a clearer."""
    source_id, *_ = base
    with SessionLocal() as db:
        org_id = db.get(ProcurementSession, source_id).organization_id
    actor = _actors(org_id, 1)[0]
    target = _ready_snapshot(client, actor, source_id, 1)
    original_guard = inventory_service.lock_stock_origins
    calls = []

    def admission_before_guard_returns(db, session):
        roots = original_guard(db, session)
        calls.append(roots)
        db.add(Job(session_id=target["sid"], kind="match", status="QUEUED"))
        db.flush()
        return roots

    monkeypatch.setattr("app.api.inventory.lock_stock_origins", admission_before_guard_returns)
    response = client.delete(f"/api/v1/normalized-bases/{source_id}/stock", headers=auth)
    assert response.status_code == 409, response.text
    assert calls == [{source_id}]
    with SessionLocal() as db:
        from app.db import StockPosition
        assert db.scalar(select(StockPosition.state)) == "AVAILABLE"


def test_enqueue_checks_active_jobs_after_inventory_guard(client, auth, base, monkeypatch):
    source_id, *_ = base
    with SessionLocal() as db:
        org_id = db.get(ProcurementSession, source_id).organization_id
    actor = _actors(org_id, 1)[0]
    target = _ready_snapshot(client, actor, source_id, 1)
    original_guard = inventory_service.lock_stock_origins
    calls = []

    def preceding_admission(db, session):
        roots = original_guard(db, session)
        calls.append(roots)
        db.add(Job(session_id=session.id, kind="match", status="QUEUED"))
        db.flush()
        return roots

    monkeypatch.setattr("app.api.routes.lock_stock_origins", preceding_admission)
    response = client.post(
        f"/api/v1/matching/{target['sid']}/run", headers=actor[1], json={},
    )
    assert response.status_code == 409, response.text
    assert calls == [{source_id}]


def test_retry_checks_active_jobs_after_inventory_guard(client, auth, base, monkeypatch):
    source_id, *_ = base
    with SessionLocal() as db:
        org_id = db.get(ProcurementSession, source_id).organization_id
    actor = _actors(org_id, 1)[0]
    target = _ready_snapshot(client, actor, source_id, 1)
    with SessionLocal() as db:
        failed = Job(session_id=target["sid"], kind="match", status="FAILED")
        db.add(failed)
        db.commit()
        failed_id = failed.id
    calls = []

    def preceding_admission(db, session):
        calls.append(session.id)
        db.add(Job(session_id=session.id, kind="match", status="QUEUED"))
        db.flush()
        return {source_id}

    monkeypatch.setattr("app.api.routes.lock_stock_origins", preceding_admission)
    response = client.post(
        f"/api/v1/pipeline/{target['sid']}/jobs/{failed_id}/retry", headers=actor[1],
    )
    assert response.status_code == 409, response.text
    assert calls == [target["sid"]]
    with SessionLocal() as db:
        assert db.get(Job, failed_id).status == "FAILED"

import pytest
from sqlalchemy import event, select

from app import tasks
from app.core.security import create_access_token
from app.db import (
    Audit, Document, Item, Job, Match, Organization, ProcurementSession,
    QualityCompletion, QualityReview, SessionLocal, StageTiming, StockPosition,
    User, engine,
)
from app.services import search, storage
from test_reusable_bases import base
from test_shared_inventory import create_match

URL = "/api/v1/procurement/sessions"


@pytest.fixture(autouse=True)
def enforce_foreign_keys(database):
    def enable(connection, *_):
        connection.execute("PRAGMA foreign_keys=ON")
    event.listen(engine, "checkout", enable)
    yield
    event.remove(engine, "checkout", enable)
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")


@pytest.fixture
def cleanup_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(storage, "delete_session_files", lambda org, sid: calls.append(("files", org, sid)))
    monkeypatch.setattr(search, "delete_session_index", lambda sid: calls.append(("index", sid)))
    return calls


def create(client, auth, title="Delete me"):
    response = client.post(URL, headers=auth, json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_deletes_full_graph_and_updates_lists(client, auth, base):
    source, objects, *_ = base
    target, mid = create_match(client, auth, source)
    with SessionLocal() as db:
        match = db.get(Match, mid)
        jid = match.job_id
        db.add(QualityReview(session_id=target, job_id=jid, kind="candidate",
                             target_id=mid, prediction={}, answer={}, reviewer_id=1))
        db.add(QualityCompletion(session_id=target, job_id=jid, reviewer_id=1))
        db.commit()
    response = client.delete(f"{URL}/{target}", headers=auth)
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": True, "session_id": target}
    for path in (f"{URL}/{target}", f"{URL}/{target}/documents", f"/api/v1/metrics/{target}"):
        assert client.get(path, headers=auth).status_code == 404
    with SessionLocal() as db:
        for model in (QualityCompletion, QualityReview, Match, StageTiming, Job, Item, Document, Audit):
            assert db.scalar(select(model).where(model.session_id == target)) is None
        assert db.get(ProcurementSession, source)
    assert not any(f"/{target}/" in key for key in objects)
    assert any(f"/{source}/" in key for key in objects)
    assert client.get("/api/v1/workspace/sessions", headers=auth).json()["total"] == 1
    assert client.get("/api/v1/workspace/overview", headers=auth).json()["total_sessions"] == 1
    assert client.delete(f"{URL}/{target}", headers=auth).status_code == 404
    # Old Celery deliveries cannot resurrect a deleted session/job.
    tasks.run_job.run(jid)


@pytest.mark.parametrize("status", ["QUEUED", "RUNNING", "CANCEL_REQUESTED"])
def test_active_job_blocks_single_and_bulk_before_cleanup(client, auth, cleanup_calls, status):
    sid = create(client, auth)
    other = create(client, auth)
    with SessionLocal() as db:
        db.add(Job(session_id=sid, kind="normalize", status=status))
        db.commit()
    assert client.delete(f"{URL}/{sid}", headers=auth).status_code == 409
    assert client.delete(URL, headers=auth).status_code == 409
    assert not cleanup_calls
    assert client.get(f"{URL}/{other}", headers=auth).status_code == 200


@pytest.mark.parametrize("boundary", ["files", "index"])
def test_external_failure_is_retryable_and_blocks_mutations(client, auth, cleanup_calls, monkeypatch, boundary):
    sid = create(client, auth)
    def fail(*_):
        raise RuntimeError("offline")
    service, name = (storage, "delete_session_files") if boundary == "files" else (search, "delete_session_index")
    original = getattr(service, name)
    monkeypatch.setattr(service, name, fail)
    assert client.delete(f"{URL}/{sid}", headers=auth).status_code == 503
    assert client.get(f"{URL}/{sid}", headers=auth).json()["status"] == "CLEANUP_FAILED"
    assert client.post(f"{URL}/{sid}/upload-requirement", headers=auth, files={"file": ("need.txt", b"need")}).status_code == 409
    monkeypatch.setattr(service, name, original)
    assert client.delete(f"{URL}/{sid}", headers=auth).status_code == 200


def test_deleting_source_preserves_snapshot_and_used_stock(client, auth, base):
    source, objects, *_ = base
    target, mid = create_match(client, auth, source)
    assert client.post(f"/api/v1/matching/{target}/candidates/{mid}/use", headers=auth).status_code == 200
    assert client.delete(f"{URL}/{source}", headers=auth).status_code == 200
    assert any(f"/{target}/" in key for key in objects)
    assert client.get(f"/api/v1/matching/{target}/candidates?shortlisted=false", headers=auth).json()["total"] == 1
    assert client.delete(f"{URL}/{target}", headers=auth).status_code == 200
    with SessionLocal() as db:
        stock = db.scalar(select(StockPosition))
        assert stock.state == "USED"
        assert stock.used_by_session_id == target


def test_deleting_source_before_matching_keeps_snapshot_usable(client, auth, base):
    source, *_ = base
    target = client.post(URL, headers=auth, json={"title": "Independent", "base_session_id": source}).json()["id"]
    assert client.delete(f"{URL}/{source}", headers=auth).status_code == 200
    client.post(f"{URL}/{target}/upload-requirement", headers=auth, files={"file": ("need.txt", "Кабель 3,3кВ".encode())})
    jid = client.post(f"/api/v1/pipeline/{target}/run", headers=auth, json={}).json()["task_id"]
    tasks.run_job.run(jid)
    with SessionLocal() as db:
        assert db.get(Job, jid).status == "COMPLETED"
        assert db.scalar(select(StockPosition.state)) == "AVAILABLE"


def test_auth_roles_and_tenant_isolation(client, auth, cleanup_calls):
    sid = create(client, auth)
    with SessionLocal() as db:
        org = Organization(name="Other")
        db.add(org)
        db.flush()
        user = User(username="other", password_hash="unused", role="admin", organization_id=org.id)
        db.add(user)
        db.commit()
        foreign_auth = {"Authorization": "Bearer " + create_access_token(user)}
    foreign = create(client, foreign_auth)
    assert client.delete(f"{URL}/{sid}").status_code == 401
    assert client.delete(URL).status_code == 401
    assert client.delete(f"{URL}/{foreign}", headers=auth).status_code == 404
    with SessionLocal() as db:
        db.get(User, 1).role = "viewer"
        db.commit()
    assert client.delete(f"{URL}/{sid}", headers=auth).status_code == 403
    assert client.delete(URL, headers=auth).status_code == 403
    assert not cleanup_calls
    with SessionLocal() as db:
        db.get(User, 1).role = "buyer"
        db.commit()
    result = client.delete(URL, headers=auth).json()
    assert result == {"deleted_session_ids": [sid], "failed": []}
    assert client.get(f"{URL}/{foreign}", headers=foreign_auth).status_code == 200


def test_bulk_deletes_beyond_pagination_and_empty_is_idempotent(client, auth, cleanup_calls):
    ids = {create(client, auth, f"Session {index}") for index in range(12)}
    assert len(client.get("/api/v1/workspace/sessions?limit=10", headers=auth).json()["items"]) == 10
    response = client.delete(URL, headers=auth)
    assert response.status_code == 200, response.text
    assert set(response.json()["deleted_session_ids"]) == ids
    assert response.json()["failed"] == []
    assert client.get(URL, headers=auth).json() == []
    assert client.delete(URL, headers=auth).json() == {"deleted_session_ids": [], "failed": []}


def test_bulk_reports_partial_failure_and_retries_remaining(client, auth, cleanup_calls, monkeypatch):
    good, bad = create(client, auth), create(client, auth)
    def clean(_, sid):
        if sid == bad:
            raise RuntimeError("object cleanup failed")
    monkeypatch.setattr(storage, "delete_session_files", clean)
    response = client.delete(URL, headers=auth)
    assert response.status_code == 200
    assert response.json()["deleted_session_ids"] == [good]
    assert response.json()["failed"][0]["session_id"] == bad
    assert client.get(f"{URL}/{bad}", headers=auth).json()["status"] == "CLEANUP_FAILED"
    monkeypatch.setattr(storage, "delete_session_files", lambda *_: None)
    assert client.delete(URL, headers=auth).json() == {"deleted_session_ids": [bad], "failed": []}


def test_removing_last_snapshot_prunes_unused_stock(client, auth, base):
    source, *_ = base
    target = client.post(URL, headers=auth, json={"title": "Copy", "base_session_id": source}).json()["id"]
    assert client.delete(f"{URL}/{source}", headers=auth).status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(StockPosition.id)) is not None
    assert client.delete(f"{URL}/{target}", headers=auth).status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(StockPosition.id)) is None

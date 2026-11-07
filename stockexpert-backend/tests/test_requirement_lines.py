"""Explicit TXT position lists through upload, preparation, H1 and B1 selection."""
from copy import deepcopy
from contextlib import closing
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook
import pytest
from qdrant_client import QdrantClient
from sqlalchemy import select

from app import tasks
from app.db import Audit, Document, Item, Job, Match, ProcurementSession, SessionLocal, User
from app.services import b1_experiments, column_mapping, llm, normalization_engine, requirement_extraction, search, storage, workflow
from test_model_selection import fake_redis


API = "/api/v1"
THERMOCOLUMN_FILE = Path(__file__).resolve().parents[2] / "fixtures" / "B1_thermocolumn_10.txt"
POSITIONS = [f"Термоколонка ТКС.В.32-11/1,2-Ф(У)-И{i}" for i in range(4, 14)]


@pytest.fixture
def objects(monkeypatch):
    files = {}
    monkeypatch.setattr(storage, "put", lambda key, data: files.__setitem__(key, data))
    monkeypatch.setattr(storage, "get", lambda key: files[key])
    monkeypatch.setattr(storage, "delete_key", lambda key: files.pop(key))
    return files


def new_session(client, auth, method="regex"):
    response = client.post(f"{API}/procurement/sessions", headers=auth, json={
        "title": "Десять потребностей из одного TXT", "normalization_method": method,
        "taxonomy_enabled": False,
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def upload(client, auth, sid, content, mode=None, filename="need.txt", replace=None):
    action = f"documents/{replace}/replace" if replace else "upload-requirement"
    return client.post(f"{API}/procurement/sessions/{sid}/{action}", headers=auth,
                       files={"file": (filename, content)},
                       data={} if mode is None else {"requirement_mode": mode})


def run_job(client, auth, sid, action):
    response = client.post(f"{API}/pipeline/{sid}/{action}", headers=auth, json={})
    assert response.status_code == 202, response.text
    jid = response.json()["task_id"]
    tasks.run_job.run(jid)
    with SessionLocal() as db:
        job = db.get(Job, jid)
        assert job.status == "COMPLETED", job.error
    return jid


def prepare_and_normalize(client, auth, sid, monkeypatch):
    fake_redis(monkeypatch)
    workbook = Workbook()
    workbook.active.append(["Описание"])
    workbook.active.append([POSITIONS[0]])
    stream = BytesIO()
    workbook.save(stream)
    response = client.post(f"{API}/procurement/sessions/{sid}/upload-residuals", headers=auth,
                           files={"file": ("stock.xlsx", stream.getvalue())})
    assert response.status_code == 201, response.text
    run_job(client, auth, sid, "prepare")
    response = client.post(f"{API}/pipeline/{sid}/approve-mapping", headers=auth)
    assert response.status_code == 200, response.text
    return run_job(client, auth, sid, "start-normalization")


@pytest.mark.parametrize("method", ["regex", "llm"])
def test_actual_ten_position_file_becomes_ten_ready_requirements_and_b1_option(
    client, auth, objects, monkeypatch, method
):
    def forbidden(*args, **kwargs):
        pytest.fail("Explicit line mode must not call either automatic requirement splitter")

    monkeypatch.setattr(requirement_extraction, "split", forbidden)
    monkeypatch.setattr(normalization_engine, "split_requirement", forbidden)
    monkeypatch.setattr(llm, "structured", forbidden)
    if method == "llm":
        # Substitute external extraction/mapping only; parsing, jobs, DB and H1
        # dispatch are real. No LLM splitter can merge the ten source positions.
        monkeypatch.setattr(column_mapping, "discover", lambda sample, tick: (
            normalization_engine.column_mapping(sample), {"method": "test", "attempts": 0}))
        monkeypatch.setattr(workflow, "extract_one", lambda text, schema: normalization_engine.extract(text))
    sid = new_session(client, auth, method)
    content = THERMOCOLUMN_FILE.read_bytes()
    response = upload(client, auth, sid, content, "lines", THERMOCOLUMN_FILE.name)
    assert response.status_code == 201, response.text
    document = response.json()
    assert document["meta"]["requirement_positions"] == 10
    jid = prepare_and_normalize(client, auth, sid, monkeypatch)
    with SessionLocal() as db:
        session = db.get(ProcurementSession, sid)
        assert session.approved and session.status == "NORMALIZED"
        metadata = db.get(Document, document["id"]).meta
        assert metadata["requirement_mode"] == "lines"
        assert metadata["requirement_positions"] == 10
        assert metadata["size_bytes"] == len(content)  # prepare retains upload metadata
        rows = list(db.scalars(select(Item).where(Item.document_id == document["id"]).order_by(Item.row_index)))
        assert [row.source_text for row in rows] == POSITIONS
        assert [row.row_index for row in rows] == list(range(1, 11))
        assert all(isinstance(row.passport["attributes"], dict) for row in rows)
        assert all(not any(issue["code"] == "REGEX_REQUIREMENT_SINGLE_POSITION" for issue in row.issues) for row in rows)
        assert db.get(Job, jid).params["normalization_counts"]["requirement"] == 10
    response = client.get(f"{API}/developer/b1/config", headers=auth)
    assert response.status_code == 200, response.text
    assert {"session_id": sid, "title": "Десять потребностей из одного TXT", "normalized_requirements": 10} in response.json()["requirement_sessions"]


def test_line_split_ignores_blank_lines_and_preserves_unicode_and_source_fragments():
    text = " \r\n  Термоколонка ТКС.В.32-11/1,2-Ф(У)-И4  \r\n\r\n\tНасос Ø32 — 1,2 МПа\t\r\n \nКабель 3×2,5 мм²"
    assert requirement_extraction.split_lines(text) == [POSITIONS[0], "Насос Ø32 — 1,2 МПа", "Кабель 3×2,5 мм²"]
    assert all(part in text for part in requirement_extraction.split_lines(text))


def test_copied_thermocolumn_base_full_matching_keeps_schema_and_accepts_ten_line_b1_source(
    client, auth, objects, monkeypatch
):
    # A small genuine regex base exercises the same copy/run path as a large
    # ready catalogue. External embeddings are substituted; retrieval and
    # persisted matching results use real in-memory Qdrant and SQL.
    base_id = new_session(client, auth)
    assert upload(client, auth, base_id, POSITIONS[0].encode()).status_code == 201
    prepare_and_normalize(client, auth, base_id, monkeypatch)
    with SessionLocal() as db:
        original_schema = deepcopy(db.get(ProcurementSession, base_id).schema_doc)
        original_item = db.scalar(select(Item).where(
            Item.session_id == base_id, Item.kind == "residual", Item.passport.is_not(None)))
        original_id, original_passport = original_item.id, deepcopy(original_item.passport)
    response = client.post(f"{API}/procurement/sessions", headers=auth, json={
        "title": "B1 из готовой термоколонки", "base_session_id": base_id,
        "normalization_method": "regex", "taxonomy_enabled": False,
    })
    assert response.status_code == 201, response.text
    sid = response.json()["id"]
    assert response.json()["base_snapshot"]["reused_passports"]
    response = upload(client, auth, sid, THERMOCOLUMN_FILE.read_bytes(), "lines")
    assert response.status_code == 201, response.text
    document_id = response.json()["id"]

    def forbidden(*args, **kwargs):
        pytest.fail("Regex ready-base matching must not call generative models or an automatic splitter")

    monkeypatch.setattr(llm, "structured", forbidden)
    monkeypatch.setattr(requirement_extraction, "split", forbidden)
    monkeypatch.setattr(normalization_engine, "split_requirement", forbidden)
    with closing(QdrantClient(":memory:")) as qdrant:
        monkeypatch.setattr(search, "client", lambda: qdrant)
        monkeypatch.setattr(llm, "embed", lambda texts: [[1.0, 0.5, 0.2] for _ in texts])
        jid = run_job(client, auth, sid, "run")

    with SessionLocal() as db:
        base = db.get(ProcurementSession, base_id)
        session = db.get(ProcurementSession, sid)
        job = db.get(Job, jid)
        assert job.kind == "full" and session.status == "MATCHED" and session.approved
        assert job.params["normalization_counts"] == {"requirement": 10}
        assert db.get(Document, document_id).meta["requirement_mode"] == "lines"
        assert db.get(Document, document_id).meta["requirement_positions"] == 10
        assert len(list(db.scalars(select(Match).where(Match.job_id == jid)))) == 10
        assert base.schema_doc == original_schema
        assert db.get(Item, original_id).passport == original_passport
        assert b1_experiments.schema_signature(base) == b1_experiments.schema_signature(session)
        for scenario in ("concurrent_10", "sequential_10"):
            validated_base, requirements, count = b1_experiments.validate_inputs(db, db.scalar(select(User)), {
                "scenario": scenario, "base_session_id": base_id, "requirement_session_id": sid,
            })
            assert validated_base.id == base_id and count == 1
            assert [row.source_text for row in requirements] == POSITIONS
    response = client.get(f"{API}/developer/b1/config", headers=auth)
    assert response.status_code == 200, response.text
    sources = {row["session_id"]: row for row in response.json()["requirement_sessions"]}
    assert sources[sid]["normalized_requirements"] == 10


@pytest.mark.parametrize("legacy_metadata", [False, True])
def test_default_and_old_documents_keep_multiline_questionnaire_one_position(
    client, auth, objects, monkeypatch, legacy_metadata
):
    sid = new_session(client, auth)
    text = "Кабель\nНапряжение 6000 В\n3х70"
    response = upload(client, auth, sid, text.encode())
    assert response.status_code == 201
    document = response.json()
    assert document["meta"]["requirement_mode"] == "auto"
    if legacy_metadata:
        with SessionLocal() as db:
            db.get(Document, document["id"]).meta = {"text": text}
            db.commit()
    prepare_and_normalize(client, auth, sid, monkeypatch)
    with SessionLocal() as db:
        rows = list(db.scalars(select(Item).where(Item.document_id == document["id"])))
        assert len(rows) == 1 and rows[0].source_text == text
        assert "REGEX_REQUIREMENT_SINGLE_POSITION" in {issue["code"] for issue in rows[0].issues}


@pytest.mark.parametrize("mode,filename,content", [
    ("invalid", "need.txt", b"position"),
    ("lines", "need.pdf", b"not a PDF"),
    ("lines", "need.xlsx", b"not an XLSX"),
    ("lines", "need.docx", b"not a DOCX"),
    ("lines", "empty.txt", b" \r\n\t\n"),
])
@pytest.mark.parametrize("replace", [False, True])
def test_invalid_list_mode_or_file_rejected_before_mutation(client, auth, objects, mode, filename, content, replace):
    sid = new_session(client, auth)
    original = upload(client, auth, sid, b"original").json() if replace else None
    before_objects = dict(objects)
    with SessionLocal() as db:
        before_audits = list(db.scalars(select(Audit.id)))
        before_status = db.get(ProcurementSession, sid).status
    response = upload(client, auth, sid, content, mode, filename, original["id"] if original else None)
    assert response.status_code == 422, response.text
    assert objects == before_objects
    with SessionLocal() as db:
        documents = list(db.scalars(select(Document)))
        assert len(documents) == int(replace)
        if original:
            assert documents[0].id == original["id"] and documents[0].meta == original["meta"]
        assert list(db.scalars(select(Audit.id))) == before_audits
        assert db.get(ProcurementSession, sid).status == before_status


def test_replacement_can_change_same_bytes_mode_then_replace_file_and_clear_position_count(client, auth, objects):
    sid = new_session(client, auth)
    content = THERMOCOLUMN_FILE.read_bytes()
    original = upload(client, auth, sid, content).json()
    response = upload(client, auth, sid, content, "lines")
    assert response.status_code == 409 and "Replace" in response.text
    assert len(objects) == 1
    response = upload(client, auth, sid, content, "lines", replace=original["id"])
    assert response.status_code == 200, response.text
    changed = response.json()
    assert changed["id"] == original["id"] and len(objects) == 1
    assert changed["meta"]["requirement_mode"] == "lines" and changed["meta"]["requirement_positions"] == 10
    response = upload(client, auth, sid, b"one\r\n\r\ntwo", "lines", replace=changed["id"])
    assert response.status_code == 200, response.text
    replaced = response.json()
    assert replaced["id"] != original["id"] and len(objects) == 1
    assert replaced["meta"]["requirement_positions"] == 2
    assert original["object_key"] not in objects
    response = upload(client, auth, sid, b"one\r\n\r\ntwo", replace=replaced["id"])
    assert response.status_code == 200
    assert response.json()["meta"]["requirement_mode"] == "auto"
    assert "requirement_positions" not in response.json()["meta"]

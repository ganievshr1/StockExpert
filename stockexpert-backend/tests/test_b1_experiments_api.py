"""B1 API contracts and authorization, without starting a load experiment."""

from copy import deepcopy
from io import BytesIO
import json
from types import SimpleNamespace
from unittest.mock import Mock
from zipfile import ZipFile

import pytest
from sqlalchemy import select

from app import tasks
from app.core.config import settings
from app.core.security import create_access_token
from app.db import Document, Item, Job, Organization, ProcurementSession, SessionLocal, User
from app.services import b1_experiments as service
from test_reusable_bases import base


PREFIX = "/api/v1/developer/b1"


@pytest.fixture
def experiment_source(client, auth, base, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "B1_ARTIFACT_DIR", str(tmp_path / "b1"))
    monkeypatch.setattr(settings, "JOB_ROUTING_MODE", "split")
    publish = Mock(return_value=SimpleNamespace(id="test-controller-task"))
    monkeypatch.setattr(tasks.celery_app, "send_task", publish)
    source_id, *_ = base
    response = client.post(
        "/api/v1/procurement/sessions", headers=auth,
        json={"title": "Ten ready requirements", "taxonomy_enabled": True},
    )
    assert response.status_code == 201, response.text
    requirement_sid = response.json()["id"]
    with SessionLocal() as db:
        catalog = db.get(ProcurementSession, source_id)
        requirements = db.get(ProcurementSession, requirement_sid)
        requirements.schema_doc = deepcopy(catalog.schema_doc)
        requirements.approved = True
        requirements.status = "NORMALIZED"
        document = Document(
            session_id=requirement_sid, kind="requirement", filename="ten-needs.txt",
            object_key=f"test/{requirement_sid}/ten-needs.txt", sha256="b" * 64,
            meta={"text": "Ten prepared requirements"},
        )
        db.add(document)
        db.flush()
        for index in range(10):
            text = f"Кабель 3,3кВ, потребность {index + 1}"
            db.add(Item(
                session_id=requirement_sid, document_id=document.id,
                kind="requirement", row_index=index + 1, raw={"text": text},
                source_text=text, category="cable", issues=[],
                passport={"attributes": {"voltage_v": 3300},
                          "category_id": "cable",
                          "evidence": {"voltage_v": {"quote": "3,3кВ"}}},
            ))
        db.commit()
        org_id = catalog.organization_id
    return dict(base_id=source_id, requirements_id=requirement_sid,
                organization_id=org_id, publish=publish, root=tmp_path / "b1")


def _payload(source, scenario="concurrent_10", **changes):
    return {"scenario": scenario, "base_session_id": source["base_id"],
            "requirement_session_id": source["requirements_id"], **changes}


def _token(organization_id, role, username):
    with SessionLocal() as db:
        user = User(username=username, password_hash="unused-by-JWT-test",
                    organization_id=organization_id, role=role)
        db.add(user)
        db.commit()
        return {"Authorization": "Bearer " + create_access_token(user)}


def _create(client, auth, source, scenario="concurrent_10"):
    response = client.post(PREFIX + "/experiments", headers=auth,
                           json=_payload(source, scenario))
    assert response.status_code == 202, response.text
    return response.json()


def test_config_lists_ready_sources_and_fixed_protocol(client, auth, experiment_source):
    source = experiment_source
    response = client.get(PREFIX + "/config", headers=auth)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["routing_mode"] == "split"
    assert source["base_id"] in {row["session_id"] for row in value["ready_bases"]}
    requirements = {row["session_id"]: row for row in value["requirement_sessions"]}
    assert requirements[source["requirements_id"]]["normalized_requirements"] == 10
    assert value["protocol"]["pairs"] == 5
    assert value["protocol"]["requests_per_run"] == 600
    assert value["protocol"]["timeout_seconds"] == 120
    source["publish"].assert_not_called()


@pytest.mark.parametrize("scenario", ["concurrent_10", "sequential_10", "single_user_parallel"])
def test_diagnostic_create_persists_plan_and_publishes_controller(
    client, auth, experiment_source, scenario,
):
    source = experiment_source
    value = _create(client, auth, source, scenario)
    assert value["scope"] == "diagnostic"
    assert value["scenario"] == scenario
    assert value["status"] == "QUEUED"
    assert value["summary"] == {}
    assert value["measurement_kind"] == "real_http_and_real_pipeline"
    assert value["base_rows"] == 1
    assert len(value["requirement_ids"]) == 10
    assert len(set(value["requirement_ids"])) == 10
    assert len(value["plan"]) == 1
    assert value["plan"][0]["routing_mode"] == "split"
    assert not {"actors", "pool", "background_pool"}.intersection(value)
    source["publish"].assert_called_once_with(
        "stockexpert.b1_experiment", args=[value["id"]], queue="b1_experiments",
    )
    assert (source["root"] / value["id"] / "state.json").is_file()
    details = client.get(PREFIX + "/experiments/" + value["id"], headers=auth)
    assert details.json()["id"] == value["id"]
    listed = client.get(PREFIX + "/experiments", headers=auth).json()["items"]
    assert [row["id"] for row in listed] == [value["id"]]


def test_create_rejects_fewer_than_ten_ready_requirements(client, auth, experiment_source):
    source = experiment_source
    with SessionLocal() as db:
        row = db.scalar(select(Item).where(
            Item.session_id == source["requirements_id"], Item.row_index == 10,
        ))
        row.passport = None
        db.commit()
    response = client.post(PREFIX + "/experiments", headers=auth, json=_payload(source))
    assert response.status_code == 422, response.text
    assert "десятью" in response.json()["detail"]
    source["publish"].assert_not_called()
    assert not list(source["root"].glob("*/state.json"))


@pytest.mark.parametrize("busy_source", ["base_id", "requirements_id"])
def test_create_rejects_active_source_jobs(client, auth, experiment_source, busy_source):
    source = experiment_source
    with SessionLocal() as db:
        db.add(Job(session_id=source[busy_source], kind="normalize", status="RUNNING"))
        db.commit()
    response = client.post(PREFIX + "/experiments", headers=auth, json=_payload(source))
    assert response.status_code == 409, response.text
    source["publish"].assert_not_called()


def test_create_rejects_incompatible_requirement_schema(client, auth, experiment_source):
    source = experiment_source
    with SessionLocal() as db:
        session = db.get(ProcurementSession, source["requirements_id"])
        schema = deepcopy(session.schema_doc)
        schema["features"][0]["unit"] = "kV"
        session.schema_doc = schema
        db.commit()
    response = client.post(PREFIX + "/experiments", headers=auth, json=_payload(source))
    assert response.status_code == 422, response.text
    assert "схему" in response.json()["detail"]
    source["publish"].assert_not_called()


def test_confirmation_rejects_small_catalog_without_starting_any_load(
    client, auth, experiment_source,
):
    source = experiment_source
    response = client.post(PREFIX + "/experiments", headers=auth,
                           json=_payload(source, "confirmation"))
    assert response.status_code == 422, response.text
    assert "12 000" in response.json()["detail"]
    source["publish"].assert_not_called()


@pytest.mark.parametrize("extra", [{"users": 10}, {"rate_per_minute": 10000}])
def test_client_cannot_silently_override_fixed_protocol(client, auth, experiment_source, extra):
    response = client.post(PREFIX + "/experiments", headers=auth,
                           json=_payload(experiment_source, **extra))
    assert response.status_code == 422, response.text
    experiment_source["publish"].assert_not_called()


def test_diagnostic_timeout_is_saved_per_experiment_without_mutating_protocol(client, auth, experiment_source):
    response = client.post(PREFIX + '/experiments', headers=auth,
        json=_payload(experiment_source, 'single_user_parallel', diagnostic_timeout_seconds=600))
    assert response.status_code == 202, response.text
    assert response.json()['protocol']['timeout_seconds'] == 600
    assert service.PROTOCOL['timeout_seconds'] == 120
    default = _create(client, auth, experiment_source, 'sequential_10')
    assert default['protocol']['timeout_seconds'] == 120


@pytest.mark.parametrize('timeout', [0, 29, 3601, 120.5, '600', True])
def test_invalid_diagnostic_timeout_rejected(client, auth, experiment_source, timeout):
    response = client.post(PREFIX + '/experiments', headers=auth,
        json=_payload(experiment_source, 'single_user_parallel', diagnostic_timeout_seconds=timeout))
    assert response.status_code == 422
    experiment_source['publish'].assert_not_called()


def test_confirmation_rejects_extended_timeout_before_preparing_inputs(client, auth, experiment_source):
    response = client.post(PREFIX + '/experiments', headers=auth,
        json=_payload(experiment_source, 'confirmation', diagnostic_timeout_seconds=600))
    assert response.status_code == 422
    assert 'фиксированный таймаут' in response.text
    experiment_source['publish'].assert_not_called()


def test_parallel_runtime_is_reported_and_confirmation_rejected_before_setup(client, auth, experiment_source, monkeypatch):
    monkeypatch.setenv('B1_RUNTIME_PROFILE', 'parallel')
    options = client.get(PREFIX + '/config', headers=auth)
    assert options.status_code == 200
    assert options.json()['runtime_profile'] == 'parallel'
    response = client.post(PREFIX + '/experiments', headers=auth,
        json=_payload(experiment_source, 'confirmation'))
    assert response.status_code == 422 and 'профиль standard' in response.text
    experiment_source['publish'].assert_not_called()


@pytest.mark.parametrize("role", ["buyer", "dmto_expert"])
def test_b1_developer_endpoints_require_admin(client, auth, experiment_source, role):
    source = experiment_source
    headers = _token(source["organization_id"], role, "b1-" + role)
    for path in ("/config", "/experiments"):
        assert client.get(PREFIX + path, headers=headers).status_code == 403
    assert client.post(PREFIX + "/experiments", headers=headers,
                       json=_payload(source)).status_code == 403
    assert client.get(PREFIX + "/config").status_code == 401
    source["publish"].assert_not_called()


def test_foreign_admin_cannot_use_sources_or_manage_experiment(client, auth, experiment_source):
    source = experiment_source
    value = _create(client, auth, source)
    with SessionLocal() as db:
        organization = Organization(name="Foreign B1 organization")
        db.add(organization)
        db.commit()
        foreign_id = organization.id
    headers = _token(foreign_id, "admin", "foreign-b1-admin")
    assert client.post(PREFIX + "/experiments", headers=headers,
                       json=_payload(source)).status_code == 404
    for method, suffix in (("get", ""), ("post", "/continue"),
                           ("post", "/cancel"), ("get", "/export")):
        response = getattr(client, method)(
            PREFIX + "/experiments/" + value["id"] + suffix, headers=headers,
        )
        assert response.status_code == 404, response.text
    assert client.get(PREFIX + "/experiments", headers=headers).json()["items"] == []
    options = client.get(PREFIX + "/config", headers=headers).json()
    assert options["ready_bases"] == options["requirement_sessions"] == []
    assert service.load(value["id"])["status"] == "QUEUED"
    assert source["publish"].call_count == 1


@pytest.mark.parametrize("scenario", ["concurrent_10", "sequential_10", "single_user_parallel"])
def test_diagnostic_cannot_reference_a_foreign_background_manifest(
    client, auth, experiment_source, scenario,
):
    source = experiment_source
    with SessionLocal() as db:
        organization = Organization(name="Foreign background owner")
        db.add(organization)
        db.commit()
        foreign_id = organization.id
    foreign_headers = _token(foreign_id, "admin", "foreign-background-admin")
    foreign_session = client.post(
        "/api/v1/procurement/sessions", headers=foreign_headers,
        json={"title": "Foreign confidential catalog"},
    )
    assert foreign_session.status_code == 201
    response = client.post(
        PREFIX + "/experiments", headers=auth,
        json=_payload(source, scenario,
                      background_session_id=foreign_session.json()["id"]),
    )
    assert response.status_code == 404, response.text
    source["publish"].assert_not_called()
    assert not list(source["root"].glob("*/state.json"))


def test_continue_requires_matching_routing_profile(client, auth, experiment_source, monkeypatch):
    source = experiment_source
    value = _create(client, auth, source)
    service.save(value["id"], {"status": "AWAITING_PROFILE"})
    monkeypatch.setattr(settings, "JOB_ROUTING_MODE", "shared")
    response = client.post(PREFIX + "/experiments/" + value["id"] + "/continue", headers=auth)
    assert response.status_code == 409, response.text
    assert "split" in response.json()["detail"]
    assert service.load(value["id"])["status"] == "AWAITING_PROFILE"
    assert source["publish"].call_count == 1
    monkeypatch.setattr(settings, "JOB_ROUTING_MODE", "split")
    resumed = client.post(PREFIX + "/experiments/" + value["id"] + "/continue", headers=auth)
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["status"] == "QUEUED"
    assert source["publish"].call_count == 2


def test_cancel_queued_experiment_does_not_start_a_controller(client, auth, experiment_source):
    source = experiment_source
    value = _create(client, auth, source)
    response = client.post(PREFIX + "/experiments/" + value["id"] + "/cancel", headers=auth)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "CANCELLED"
    assert response.json()["cancel_requested"] is True
    assert service.load(value["id"])["status"] == "CANCELLED"
    assert source["publish"].call_count == 1


def test_broker_failure_preserves_a_resumable_experiment(client, auth, experiment_source):
    source = experiment_source
    source["publish"].side_effect = ConnectionError("controlled broker outage")
    response = client.post(PREFIX + "/experiments", headers=auth, json=_payload(source))
    assert response.status_code == 503, response.text
    items = client.get(PREFIX + "/experiments", headers=auth).json()["items"]
    assert len(items) == 1
    assert items[0]["status"] == "AWAITING_PROFILE"
    source["publish"].side_effect = None
    resumed = client.post(PREFIX + "/experiments/" + items[0]["id"] + "/continue", headers=auth)
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["id"] == items[0]["id"]


def test_exports_preserve_observations_without_actor_credentials(client, auth, experiment_source):
    source = experiment_source
    value = _create(client, auth, source)
    ident = value["id"]
    service.save(ident, {"actors": [{"token": "do-not-export-this-token"}],
                         "pool": [{"session_id": "internal-pool"}]})
    observation = {"run_index": 0, "phase": "MEASURING", "number": 1,
                   "account": 0, "session_id": "observed-session",
                   "job_id": "observed-job", "outcome": "SUCCESS",
                   "start_lag_ms": 2, "accept_seconds": .03,
                   "total_seconds": .25, "result_count": 1,
                   "result_sha256": "c" * 64}
    service.append(ident, "raw.jsonl", observation)
    path = PREFIX + "/experiments/" + ident + "/export"
    public = client.get(path + "?format=json", headers=auth)
    assert public.status_code == 200
    assert public.json()["id"] == ident
    assert "actors" not in public.json() and "pool" not in public.json()
    assert "do-not-export-this-token" not in public.text
    assert public.headers["content-disposition"].endswith(f'b1-{ident}.json"')
    csv = client.get(path + "?format=csv", headers=auth)
    assert csv.status_code == 200
    assert "observed-job" in csv.text and "0.25" in csv.text
    archive = client.get(path + "?format=zip", headers=auth)
    assert archive.status_code == 200
    with ZipFile(BytesIO(archive.content)) as zipped:
        assert set(zipped.namelist()) == {"experiment.json", "raw.jsonl"}
        assert json.loads(zipped.read("raw.jsonl")) == observation
        metadata = zipped.read("experiment.json")
        assert b"do-not-export-this-token" not in metadata
        assert json.loads(metadata)["scope"] == "diagnostic"
    assert client.get(path + "?format=xml", headers=auth).status_code == 422

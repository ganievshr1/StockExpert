import os,tempfile
os.environ.update(SECRET_KEY='test-key-which-is-not-used-in-production',MINIO_SECRET_KEY='test-secret',ADMIN_PASSWORD='test-password',DATABASE_URL='sqlite:///'+tempfile.mktemp(suffix='.db'),RERANKER_ENABLED='false',LLM_DEFAULT_PROFILE='',DEEPSEEK_MODEL='',DB_HOST='')
import pytest
from fastapi.testclient import TestClient
from app.db import Base,engine,SessionLocal,Organization,User
from app.core.security import hash_password,create_access_token
from app.main import app
@pytest.fixture(autouse=True)
def database():
    Base.metadata.drop_all(engine);Base.metadata.create_all(engine)
    yield
@pytest.fixture(autouse=True)
def model_service_boundary(monkeypatch):
    # API/workflow tests do not contact external services. Dedicated model tests
    # exercise availability and the real preflight with controlled HTTP responses.
    monkeypatch.setattr('app.services.model_registry.preflight',lambda config: None)
@pytest.fixture
def client(): return TestClient(app)
@pytest.fixture
def auth():
    with SessionLocal() as db:
        o=Organization(name='Test');db.add(o);db.flush()
        u=User(username='test',password_hash=hash_password('test-password'),role='admin',organization_id=o.id);db.add(u);db.commit()
        return {'Authorization':'Bearer '+create_access_token(u)}


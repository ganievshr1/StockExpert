from app.db import SessionLocal,User,Organization
from app.core.security import hash_password,create_access_token

def test_fake_jwt_prefix_rejected(client):
    r=client.get('/api/v1/auth/me',headers={'Authorization':'Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.fake.fake'})
    assert r.status_code==401

def test_password_login(client,auth):
    assert client.post('/api/v1/auth/token',data={'username':'test','password':'wrong'}).status_code==401
    r=client.post('/api/v1/auth/token',data={'username':'test','password':'test-password'})
    assert r.status_code==200 and r.json()['access_token']

def test_tenant_isolation_and_protected_sse(client,auth):
    sid=client.post('/api/v1/procurement/sessions',json={'title':'Private'},headers=auth).json()['id']
    with SessionLocal() as db:
        o=Organization(name='Other');db.add(o);db.flush()
        u=User(username='other',password_hash=hash_password('pw'),role='admin',organization_id=o.id);db.add(u);db.commit();token=create_access_token(u)
    assert client.get('/api/v1/procurement/sessions/'+sid,headers={'Authorization':'Bearer '+token}).status_code==404
    assert client.get('/api/v1/procurement/sessions/'+sid+'/stream-progress').status_code==401


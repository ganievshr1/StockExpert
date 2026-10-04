import json
import httpx
import pytest
from pydantic import SecretStr
from app.core.config import settings
from app.services import llm

SCHEMA={'type':'object','properties':{'value':{'type':'string'}},'required':['value'],'additionalProperties':False}

@pytest.fixture
def deepseek(monkeypatch):
    monkeypatch.setattr(settings,'LLM_PROVIDER','deepseek')
    monkeypatch.setattr(settings,'LLM_MODEL','deepseek-v4-pro')
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY',SecretStr('fake-test-key'))
    monkeypatch.setattr(settings,'LLM_RETRIES',2)
    monkeypatch.setattr(llm.time,'sleep',lambda _:None)
    original=httpx.Client
    def use(handler):
        monkeypatch.setattr(llm.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    return use

def answer(content='{"value":"Кабель"}',finish='stop'):
    return httpx.Response(200,json={'choices':[{'finish_reason':finish,'message':{'content':content,'reasoning_content':'not retained'}}]})

def test_deepseek_contract_and_key_stays_server_side(deepseek,client,auth):
    requests=[]
    def handle(req):
        requests.append(req)
        data=json.loads(req.content)
        assert data['model']=='deepseek-v4-pro'
        assert data['response_format']=={'type':'json_object'}
        assert data['thinking']=={'type':'enabled'}
        assert 'temperature' not in data
        assert 'JSON' in data['messages'][0]['content']
        assert req.headers['authorization']=='Bearer fake-test-key'
        assert str(req.url)=='https://api.deepseek.com/chat/completions'
        return answer()
    deepseek(handle)
    assert llm.structured('extract',{'source':'Кабель'},SCHEMA)=={'value':'Кабель'}
    assert len(requests)==1
    response=client.get('/api/v1/system/config',headers=auth)
    assert response.status_code==200
    assert 'fake-test-key' not in response.text
    assert response.json()['api_key_configured'] is True

def test_transient_retry(deepseek):
    calls=[]
    def handle(req):
        calls.append(req)
        return httpx.Response(429,headers={'retry-after':'0'}) if len(calls)<3 else answer()
    deepseek(handle)
    assert llm.structured('extract',{},SCHEMA)['value']=='Кабель'
    assert len(calls)==3

@pytest.mark.parametrize('response', [answer('{bad json}'),answer('{"value":123}'),answer(finish='length'),answer('', 'stop')])
def test_bad_response_never_accepted(deepseek,response):
    deepseek(lambda req:response)
    with pytest.raises(llm.LLMError): llm.structured('extract',{},SCHEMA)

def test_auth_failure_not_retried_or_leaked(deepseek):
    calls=[]
    def handle(req):
        calls.append(req)
        return httpx.Response(401,text='secret source and fake-test-key')
    deepseek(handle)
    with pytest.raises(llm.LLMError) as error: llm.structured('extract',{},SCHEMA)
    assert len(calls)==1
    assert 'secret source' not in str(error.value) and 'fake-test-key' not in str(error.value)

def test_missing_key_fails_before_network(deepseek,monkeypatch):
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY',SecretStr(''))
    deepseek(lambda req:pytest.fail('Must not send a request'))
    with pytest.raises(llm.LLMError,match='DEEPSEEK_API_KEY'): llm.structured('extract',{},SCHEMA)

def test_ollama_fallback(deepseek,monkeypatch):
    monkeypatch.setattr(settings,'LLM_PROVIDER','ollama')
    def handle(req):
        body=json.loads(req.content)
        assert req.url.path=='/api/chat' and body['format']==SCHEMA
        assert 'authorization' not in req.headers
        return httpx.Response(200,json={'message':{'content':'{"value":"ok"}'},'done_reason':'stop'})
    deepseek(handle)
    assert llm.structured('extract',{},SCHEMA)=={'value':'ok'}

def test_api_is_separate_and_config_requires_auth(client):
    assert client.get('/').status_code==200
    assert client.get('/').json()['service']=='StockExpert API'
    assert client.get('/assets/app.js').status_code==404
    assert client.get('/api/v1/system/config').status_code==401


import asyncio
import json

import httpx
import pytest

from backend.evidence import fact_model
from backend.evidence.facts import FactWorksheet
from tests.test_evidence import store


@pytest.mark.parametrize('mode,stage',[('empty','empty_content'),('truncated','output_truncated'),('bad_json','json_parse'),('http_error','http_status'),('network','request')])
def test_response_failure_preserves_safe_diagnostics_without_retry(monkeypatch,mode,stage):
    monkeypatch.setenv('MODEL_API_KEY','fixture-secret-not-to-be-recorded')
    monkeypatch.setenv('MODEL_NAME','fixture-model')
    monkeypatch.setenv('MODEL_BASE_URL','https://fixture.invalid/v1')
    requests=[]
    def handle(request):
        requests.append(request)
        if mode=='network':raise httpx.ConnectError('fixture-secret-not-to-be-recorded')
        if mode=='http_error':return httpx.Response(503,text='fixture-secret-not-to-be-recorded')
        return httpx.Response(200,json={'choices':[{'message':{'content':'' if mode=='empty' else 'invalid' if mode=='bad_json' else '{"ok":true}'},
                      'finish_reason':'length' if mode=='truncated' else 'stop'}],
                      'usage':{'prompt_tokens':12,'completion_tokens':20,'total_tokens':32}})
    original=fact_model.httpx.AsyncClient
    monkeypatch.setattr(fact_model.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))
    with pytest.raises(fact_model.ModelFailure) as error:asyncio.run(fact_model.generate({'target':{'text':'fixture only'}}))
    assert error.value.diagnostics['stage']==stage and len(requests)==1
    assert 'fixture-secret' not in json.dumps(error.value.diagnostics) and 'fixture-secret' not in error.value.message
    if mode!='network':assert error.value.diagnostics['http_status'] in (200,503)
    if mode in ('empty','truncated','bad_json'):assert error.value.diagnostics['usage']['total_tokens']==32


def test_only_official_deepseek_request_adds_json_mode(monkeypatch):
    monkeypatch.setenv('MODEL_NAME','deepseek-flash')
    monkeypatch.setenv('MODEL_BASE_URL','https://api.deepseek.com')
    assert fact_model.request_body({})['response_format']=={'type':'json_object'}
    monkeypatch.setenv('MODEL_BASE_URL','https://other.invalid/v1')
    assert 'response_format' not in fact_model.request_body({})


def test_rate_limit_explains_budget_is_not_provider_quota():
    error = fact_model.ModelFailure({'stage': 'http_status', 'http_status': 429})
    assert '429' in error.message and '本地请求预算不能解除限流' in error.message


def test_report_does_not_immediately_retry_provider_rate_limit():
    from backend.evidence.paper_reports import PaperReports
    service = object.__new__(PaperReports)
    service.get = lambda did, rid: {'automatic_recovery': True, 'state': 'failed',
                                   'last_failure': {'http_status': 429}}
    assert service.recover_automatically('synthetic-doc', 'synthetic-run', 'fact_model_failed', 'synthetic-worker') is None


def test_diagnostic_storage_excludes_unknown_fields_and_response_text(store):
    service=FactWorksheet(store)
    service.save_diagnostics('ledger','request',{'stage':'json_parse','http_status':200,'response_text':'secret',
        'authorization':'secret','error_message':'secret','error_type':'JSONDecodeError',
        'usage':{'prompt_tokens':12,'secret':'secret'}})
    files=list((store.root/'diagnostics').glob('*.json'))
    saved=json.loads(files[0].read_text(encoding='utf-8'))
    assert 'secret' not in json.dumps(saved)
    assert saved['usage']=={'prompt_tokens':12} and saved['http_status']==200


def test_review_transport_uses_role_prompt_without_changing_paragraph_prompt(monkeypatch):
    from backend.evidence import workflow_review
    monkeypatch.setenv('MODEL_API_KEY','fixture-secret-never-sent')
    monkeypatch.setenv('MODEL_NAME','fixture-model')
    monkeypatch.setenv('MODEL_BASE_URL','https://fixture.invalid/v1')
    requests=[]
    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"assessments":[],"issues":[]}'},'finish_reason':'stop'}], 'usage':{'total_tokens':10}})
    original=fact_model.httpx.AsyncClient
    monkeypatch.setattr(fact_model.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))
    result=asyncio.run(workflow_review.generate('review_rigor',{'facts':[],'source':[]}))
    assert result=={'assessments':[],'issues':[]} and result.usage['total_tokens']==10
    assert '第一轮独立方法审查' in requests[0]['messages'][0]['content']
    assert 'JSON Schema' in requests[0]['messages'][0]['content'] and requests[0]['max_tokens']==6000
    assert fact_model.request_body({})['messages'][0]['content']==fact_model.SYSTEM
    assert 'fixture-secret' not in json.dumps(requests)


@pytest.mark.parametrize('content,finish,stage',[
    ('','length','output_truncated'),(None,'length','output_truncated'),
    ('partial json','length','output_truncated'),('', 'stop','reasoning_only')])
def test_truncation_wins_over_empty_and_reasoning_is_never_a_result(monkeypatch,store,content,finish,stage):
    monkeypatch.setenv('MODEL_API_KEY','fixture-secret-not-recorded')
    monkeypatch.setenv('MODEL_NAME','deepseek-flash')
    monkeypatch.setenv('MODEL_BASE_URL','https://api.deepseek.com')
    calls=[]
    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':content,'reasoning_content':'private-fixture-reasoning-do-not-persist'},'finish_reason':finish}],
            'usage':{'completion_tokens':6000,'completion_tokens_details':{'reasoning_tokens':5900}}})
    original=fact_model.httpx.AsyncClient
    monkeypatch.setattr(fact_model.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handle),**kw))
    with pytest.raises(fact_model.ModelFailure) as exc:
        asyncio.run(fact_model.generate({'source':'synthetic only'},thinking_mode='disabled'))
    d=exc.value.diagnostics
    assert d['stage']==stage and d['reasoning_content_present'] and d['usage']['reasoning_tokens']==5900
    assert len(calls)==1 and calls[0]['thinking']=={'type':'disabled'} and calls[0]['max_tokens']==6000
    FactWorksheet(store).save_diagnostics('report','test',d)
    saved=json.loads(next((store.root/'diagnostics').glob('*.json')).read_text(encoding='utf-8'))
    assert saved['stage']==stage and saved['thinking_mode']=='disabled'
    assert saved['reasoning_content_present'] and saved['usage']['reasoning_tokens']==5900
    assert 'private-fixture' not in json.dumps(saved) and 'fixture-secret' not in json.dumps(saved)


@pytest.mark.parametrize('base,model,expected',[
    ('https://api.deepseek.com','deepseek-flash',True),
    ('https://api.deepseek.com/v1','deepseek-v4-pro',True),
    ('https://api.deepseek.com.evil.invalid','deepseek-flash',False),
    ('https://other.invalid/v1','deepseek-flash',False),
    ('https://api.deepseek.com','unrelated-model',False)])
def test_report_thinking_option_is_vendor_and_model_scoped(monkeypatch,base,model,expected):
    monkeypatch.setenv('MODEL_API_KEY','fixture-key')
    monkeypatch.setenv('MODEL_NAME',model);monkeypatch.setenv('MODEL_BASE_URL',base)
    calls=[]
    def handle(request):
        calls.append(json.loads(request.content));return httpx.Response(200,json={'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}]})
    original=fact_model.httpx.AsyncClient
    monkeypatch.setattr(fact_model.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handle),**kw))
    from backend.evidence import report_model
    assert asyncio.run(report_model.read_chunk({'source':[]}))=={}
    assert ('thinking' in calls[0]) is expected
    assert calls[0]['max_tokens']==6000

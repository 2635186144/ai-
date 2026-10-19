"""Explicit reviewed revision uses one call and retains every other checkpoint."""
import asyncio
import copy
import pytest
from backend.evidence.paper_reports import ReportControl,PaperReports
from backend.evidence.parser import EvidenceError
from tests.test_evidence import client,store
from tests.test_paper_reports import setup_run,ctl,token,mock_models,synthetic_only


def completed(client,store,monkeypatch):
    service,did,run=setup_run(client,store);calls=[];mock_models(monkeypatch,calls)
    active=ctl(service,did,run,'start');asyncio.run(service.work(did,run['id'],token(service,did,active)))
    return service,did,service.get(did,run['id']),calls


def test_review_revision_preserves_source_and_other_outputs(client,store,monkeypatch):
    service,did,run,calls=completed(client,store,monkeypatch)
    saved=copy.deepcopy(run);eid=run['report']['evidence'][0]['id']
    command=ReportControl(revision=run['revision'],action='revise_part',part='guidance',feedback='模拟数据：核对分母与因果边界。',feedback_evidence_ids=[eid],accept_new_request=True,max_calls=run['max_calls']+1)
    active=service.control(did,run['id'],command);before=len(calls)
    asyncio.run(service.work(did,run['id'],token(service,did,active)))
    done=service.get(did,run['id'])
    assert done['state']=='completed' and done['calls_reserved']==run['calls_reserved']+1
    assert len(calls)==before+1 and calls[-1][1]['part']=='guidance'
    assert calls[-1][1]['review_feedback']['evidence_ids']==[eid]
    assert done['report_history'][-1]['report']==saved['report']
    for old,new in zip(saved['tasks'],done['tasks']):
        if old.get('part')!='guidance':assert new==old
    assert done['tasks'][-1]['revision_history'][-1]['output']==saved['tasks'][-1]['output']


@pytest.mark.parametrize('change',[{'accept_new_request':False},{'feedback_evidence_ids':['invented']},{'max_calls':1}])
def test_revision_requires_consent_budget_and_real_evidence(client,store,monkeypatch,change):
    service,did,run,calls=completed(client,store,monkeypatch)
    args=dict(revision=run['revision'],action='revise_part',part='guidance',feedback='模拟复核',feedback_evidence_ids=[run['report']['evidence'][0]['id']],accept_new_request=True,max_calls=run['max_calls']+1);args.update(change)
    with pytest.raises(EvidenceError):service.control(did,run['id'],ReportControl(**args))
    assert service.get(did,run['id'])['tasks']==run['tasks']
    assert service.get(did,run['id'])['report']==run['report']


def test_revision_http_endpoint_schedules_pending_part(client,store,monkeypatch):
    service,did,run,calls=completed(client,store,monkeypatch);launched=[]
    monkeypatch.setattr(PaperReports,'launch',lambda self,d,r:launched.append((d,r)))
    response=client.post(f'/api/v2/documents/{did}/reports/{run["id"]}/control',json={
        'revision':run['revision'],'action':'revise_part','part':'guidance','feedback':'模拟数据：核对口径',
        'feedback_evidence_ids':[run['report']['evidence'][0]['id']],
        'accept_new_request':True,'max_calls':run['max_calls']+1})
    assert response.status_code==200,response.text
    assert response.json()['state']=='running'
    assert launched==[(did,run['id'])]

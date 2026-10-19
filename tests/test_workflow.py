"""All models below are synthetic stand-ins, not evidence of scientific quality."""
import asyncio
import copy
import json
import time

import pytest

from backend.evidence import fact_model, workflow_review
from backend.evidence.workflow import SectionWorkflow, CreateWorkflow, WorkflowControl
from backend.evidence.fact_models import FactCommand
from backend.evidence.parser import EvidenceError
from backend.evidence.store import EvidenceStore
from tests.test_evidence import client, store
from tests.test_facts import ready, draft_for, command, save


@pytest.fixture(autouse=True)
def synthetic_only(monkeypatch):
    monkeypatch.setenv('MODEL_API_KEY', 'synthetic-never-sent')
    monkeypatch.setenv('MODEL_NAME', 'synthetic-workflow-not-real')
    async def forbidden(*a, **kw):
        raise AssertionError('Test attempted an unmocked model call')
    monkeypatch.setattr(fact_model, 'generate', forbidden)


def context(client, store, extra=2):
    _, plan, path, sheet = ready(client)
    wf = SectionWorkflow(store)
    did, sid = sheet['document_version_id'], sheet['section']['id']
    args = CreateWorkflow(reading_revision=plan['revision'], fact_revision=sheet['revision'], max_calls=len(sheet['units'])+5+extra, consent_to_send=True)
    run = wf.create(did, sid, args, 'synthetic-run')
    return wf, did, sid, run, sheet, path, args


def ctl(wf, did, run, action, accept=False):
    return wf.control(did, run['id'], WorkflowControl(revision=run['revision'], action=action, accept_new_request=accept))


def token(wf, did, run):
    with wf.store.connection() as db:
        return wf.load(db, did, run['id'])['lease_token']


def fake_review(kind, p):
    quote = {'block_id': p['source'][0]['block_id'], 'quote': p['source'][0]['text']}
    if kind.startswith('review_'):
        assert 'prior_stages' not in p  # independent first pass
        return {'assessments': [{'fact_id': f['id'], 'judgment': 'partial', 'reason': 'Synthetic opinion; not validated.', 'citations': f['citations']} for f in p['facts']],
                'issues': [{'fact_id': p['facts'][0]['id'], 'concern': 'Synthetic test issue.', 'needed_evidence': 'A real evaluation is still needed.', 'citations': [quote]}] if p['facts'] else []}
    rows = []
    for i in p['issues']:
        row = {'issue_id': i['id'], 'citations': [quote]}
        if kind == 'challenge': row.update(question='What evidence resolves this test issue?', alternative_explanation='Synthetic alternative only.')
        if kind == 'response': row.update(status='unresolved', answer='The fixture cannot resolve this.', missing_evidence='Real experiments absent from test fixture.')
        if kind == 'verdict': row.update(decision='undetermined', reason='Keep the test issue unresolved.', remaining_evidence='Real evaluation needed.')
        rows.append(row)
    return {'items': rows}


def mocks(monkeypatch, calls):
    async def read(p):
        calls.append(('reading', p['target']['id']))
        return fact_model.ModelResult(draft_for(p['target']), {'total_tokens': 12}, 'stop')
    async def review(kind, p):
        calls.append((kind, copy.deepcopy(p)))
        return fact_model.ModelResult(fake_review(kind, p), {'total_tokens': 12}, 'stop')
    monkeypatch.setattr(fact_model, 'generate', read)
    monkeypatch.setattr(workflow_review, 'generate', review)


def test_full_pipeline_exact_locators_independent_reviews_and_reopen(client, store, monkeypatch):
    wf, did, sid, run, sheet, _, args = context(client, store)
    calls=[];mocks(monkeypatch,calls)
    assert wf.create(did,sid,args,'synthetic-run')['id']==run['id']
    running=ctl(wf,did,run,'start')
    asyncio.run(wf.work(did,run['id'],token(wf,did,running)))
    result=wf.get(did,run['id'])
    assert result['state']=='completed' and result['progress']['percent']==100
    assert len(calls)==len(sheet['units'])+5==result['calls_reserved']
    assert result['usage']['reported_total_tokens']==12*len(calls)
    assert not result['semantic_verified'] and not result['stale']
    verdict=result['tasks'][-1]['output'];assert len(verdict['items'])==2
    for row in verdict['items']:
        assert row['decision']=='undetermined' and row['remaining_evidence']
        c=row['citations'][0];b=store.block(did,c['block_id'])
        assert b.text[c['start']:c['end']]==c['quote'] and c['pdf_page']==b.region.pdf_page
    reopened=SectionWorkflow(EvidenceStore(store.root)).get(did,run['id'])
    assert reopened==result
    assert 'configuration_signature' not in result and 'lease_token' not in result
    # A duplicate create returns the current run, with no repeat calls.
    assert wf.create(did,sid,args,'synthetic-run')['state']=='completed'


def test_pause_finishes_inflight_and_resume_skips_checkpoint(client,store,monkeypatch):
    wf,did,_,run,sheet,_,_=context(client,store);calls=[];mocks(monkeypatch,calls)
    original=fact_model.generate
    async def scenario():
        entered,release=asyncio.Event(),asyncio.Event()
        async def read(p):
            entered.set();await release.wait();return await original(p)
        monkeypatch.setattr(fact_model,'generate',read)
        current=ctl(wf,did,run,'start')
        worker=asyncio.create_task(wf.work(did,run['id'],token(wf,did,current)))
        await asyncio.wait_for(entered.wait(),5)
        waiting=ctl(wf,did,wf.get(did,run['id']),'pause');assert waiting['state']=='pausing'
        release.set();await asyncio.wait_for(worker,5)
        paused=wf.get(did,run['id']);assert paused['state']=='paused'
        assert paused['progress']['completed']==1 and len(calls)==1
        current=ctl(wf,did,paused,'resume')
        await wf.work(did,run['id'],token(wf,did,current))
    asyncio.run(scenario())
    result=wf.get(did,run['id']);assert result['state']=='completed'
    assert len({c[1] for c in calls if c[0]=='reading'})==len(sheet['units'])
    assert len(calls)==len(sheet['units'])+5


def test_failure_no_retry_without_consent_and_budget_is_not_expanded(client,store,monkeypatch):
    wf,did,_,run,_,_,_=context(client,store,extra=0);calls=[]
    async def fail(p):calls.append(1);raise EvidenceError(502,'synthetic_failure','Synthetic failure, no real call.')
    monkeypatch.setattr(fact_model,'generate',fail)
    current=ctl(wf,did,run,'start');asyncio.run(wf.work(did,run['id'],token(wf,did,current)))
    failed=wf.get(did,run['id']);assert failed['state']=='failed' and len(calls)==1
    with pytest.raises(EvidenceError,match='新增一次请求'):ctl(wf,did,failed,'retry')
    mocks(monkeypatch,calls)
    current=ctl(wf,did,failed,'retry',True);asyncio.run(wf.work(did,run['id'],token(wf,did,current)))
    final=wf.get(did,run['id']);assert final['state']=='failed'
    assert final['error']['code']=='workflow_budget_exhausted' and final['calls_reserved']==final['max_calls']
    assert final['tasks'][-1]['state']=='pending'


def test_changed_facts_stops_old_work_and_retains_export(client,store,monkeypatch):
    wf,did,_,run,sheet,path,_=context(client,store)
    updated=save(client,path,command(sheet)).json();assert updated['revision']==2
    with pytest.raises(EvidenceError) as err:ctl(wf,did,run,'start')
    assert err.value.code=='workflow_stale_facts'
    assert wf.get(did,run['id'])['stale']
    assert wf.get(did,run['id'])['calls_reserved']==0


def test_lease_recovery_does_not_send_or_accept_old_result(client,store,monkeypatch):
    wf,did,_,run,_,_,_=context(client,store)
    current=ctl(wf,did,run,'start');oldtoken=token(wf,did,current)
    claim=wf.claim(did,run['id'],oldtoken);assert claim
    with wf.transaction(did,run['id']) as (db,r):r['lease_until']=time.time()-1
    before=wf.get(did,run['id']);assert before['interrupted']
    recovered=ctl(wf,did,before,'pause')
    assert recovered['state']=='failed' and recovered['calls_reserved']==1
    assert recovered['tasks'][0]['error']['code']=='call_outcome_unknown'
    assert not wf.finish(did,run['id'],oldtoken,recovered['tasks'][0]['id'],{},None,{})
    assert recovered['tasks'][0]['state']=='failed'


@pytest.mark.parametrize('failure',['foreign_quote','missing_fact','duplicate_fact','missing_issue','fake_verification','confident_without_evidence'])
def test_review_contract_rejects_unbound_or_unsupported_output(client,store,failure):
    wf,did,sid,_,sheet,path,_=context(client,store)
    sheet=save(client,path,command(sheet)).json()
    packet=wf.facts.packet(did,sid,sheet['reading_revision'])
    p=workflow_review.payload_for('review_rigor',packet,sheet,[])
    raw=fake_review('review_rigor',p)
    if failure=='foreign_quote':raw['assessments'][0]['citations'][0]['block_id']='foreign'
    if failure=='missing_fact':raw['assessments']=[]
    if failure=='duplicate_fact':raw['assessments']*=2
    if failure=='fake_verification':raw['semantic_verified']=True
    if failure=='confident_without_evidence':raw['assessments'][0].update(judgment='supported',citations=[])
    if failure=='missing_issue':
        tasks=[{'kind':'review_rigor','output':workflow_review.validate('review_rigor',raw,packet,sheet,[])}]
        with pytest.raises(EvidenceError):workflow_review.validate('challenge',{'items':[]},packet,sheet,tasks)
    else:
        with pytest.raises((EvidenceError,ValueError)):workflow_review.validate('review_rigor',raw,packet,sheet,[])


def test_api_budget_consent_idempotency_and_controls(client,store,monkeypatch):
    _,plan,path,sheet=ready(client);url=path.removesuffix('/facts')+'/runs'
    data={'reading_revision':plan['revision'],'fact_revision':sheet['revision'],'max_calls':len(sheet['units'])+5,'consent_to_send':False}
    def post(d,key='create'):return client.post(url,headers={'Idempotency-Key':key},json=d)
    assert post(data).json()['code']=='workflow_consent_required'
    data['consent_to_send']=True
    assert post({**data,'max_calls':1}).json()['code']=='workflow_budget_insufficient'
    created=post(data);assert created.status_code==201,created.text
    run=created.json();assert post(data).json()['id']==run['id']
    assert post({**data,'max_calls':data['max_calls']+1}).json()['code']=='idempotency_conflict'
    control=f'/api/v2/documents/{sheet["document_version_id"]}/runs/{run["id"]}/control'
    monkeypatch.setattr(SectionWorkflow,'launch',lambda *args:None)
    r=client.post(control,json={'revision':run['revision'],'action':'pause'});assert r.json()['state']=='paused'
    assert client.post(control,json={'revision':run['revision'],'action':'resume'}).json()['code']=='stale_workflow_revision'
    r=client.post(control,json={'revision':r.json()['revision'],'action':'cancel'});assert r.json()['state']=='cancelled'
    listing=client.get(url,params={'reading_revision':plan['revision']}).json();assert listing['items'][0]['state']=='cancelled'


def test_api_dispatches_background_worker_with_synthetic_models(client,store,monkeypatch):
    wf,did,_,run,sheet,_,_=context(client,store);calls=[];mocks(monkeypatch,calls)
    url=f'/api/v2/documents/{did}/runs/{run["id"]}'
    r=client.post(url+'/control',json={'revision':run['revision'],'action':'start'})
    assert r.status_code==200,r.text
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        result=client.get(url).json()
        if result['state'] in ('completed','failed'):break
        time.sleep(.02)
    assert result['state']=='completed',result.get('error')
    assert len(calls)==len(sheet['units'])+5


def test_source_edit_during_review_fences_result_and_stops_future_calls(client,store,monkeypatch):
    wf,did,sid,run,sheet,_,_=context(client,store);calls=[];mocks(monkeypatch,calls)
    async def modify_then_review(kind,p):
        calls.append((kind,None))
        ledger=wf.facts.get(did,sid,sheet['reading_revision'])
        data=command(ledger);data['draft']['explanation']='Synthetic concurrent user edit.'
        wf.facts.update(did,sid,FactCommand.model_validate(data),'concurrent-edit')
        return fake_review(kind,p)
    monkeypatch.setattr(workflow_review,'generate',modify_then_review)
    current=ctl(wf,did,run,'start');asyncio.run(wf.work(did,run['id'],token(wf,did,current)))
    result=wf.get(did,run['id'])
    assert result['state']=='failed' and result['stale']
    assert result['error']['code']=='workflow_stale_facts'
    assert len(calls)==len(sheet['units'])+1
    assert not any(t.get('output') for t in result['tasks'] if t['kind'].startswith('review_'))


def test_review_quote_respects_partial_original_block_scope(client,store):
    wf,did,sid,_,sheet,path,_=context(client,store)
    sheet=save(client,path,command(sheet)).json();packet=wf.facts.packet(did,sid,sheet['reading_revision'])
    p=workflow_review.payload_for('review_rigor',packet,sheet,[]);raw=fake_review('review_rigor',p)
    b=packet['blocks'][0]
    packet['source_spans']=[{'block_id':b['id'],'start':2,'end':len(b['text']),'text':b['text'][2:]}]
    with pytest.raises(EvidenceError) as err:workflow_review.validate('review_rigor',raw,packet,sheet,[])
    assert err.value.code=='review_quote_invalid'


def test_v3_migration_preserves_existing_worksheet(client,store):
    _,_,path,sheet=ready(client);saved=save(client,path,command(sheet)).json()
    with store.connection() as db:
        db.execute('DROP TABLE paper_report_runs')
        db.execute('DROP TABLE workflow_events');db.execute('DROP TABLE workflow_runs')
        db.execute('PRAGMA user_version=3');db.commit()
    reopened=EvidenceStore(store.root)
    assert wf_sheet(reopened,sheet)==saved
    with reopened.connection() as db:assert db.execute('PRAGMA user_version').fetchone()[0]==5


def wf_sheet(store,sheet):
    return SectionWorkflow(store).facts.get(sheet['document_version_id'],sheet['section']['id'],sheet['reading_revision'])

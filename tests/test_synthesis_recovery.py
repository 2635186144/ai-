"""Keep paid reading checkpoints; split synthesis only after explicit budget consent."""
import asyncio
import copy
import json

import pytest

from backend.evidence import report_model
from backend.evidence.paper_reports import ReportControl, validate_report
from backend.evidence.parser import EvidenceError
from backend.evidence.parser import digest
from tests.test_evidence import client,store
from tests.test_paper_reports import setup_run,ctl,token,mock_models,synthetic_only,report_for,paper_report_part_fields


def legacy_completed_reading(client,store,monkeypatch):
    service,did,run=setup_run(client,store);calls=[];mock_models(monkeypatch,calls)
    with service.transaction(did,run['id']) as (db,r):
        r['tasks']=[t for t in r['tasks'] if t['kind']=='reading']+[{'id':'synthesis','kind':'synthesis','state':'pending','attempts':0}]
    fresh=service.get(did,run['id']);active=ctl(service,did,fresh,'start')
    asyncio.run(service.work(did,run['id'],token(service,did,active)))
    failed=service.get(did,run['id'])
    assert failed['error']['code']=='report_split_required'
    assert failed['tasks'][0]['state']=='succeeded'
    with service.transaction(did,run['id']) as (db,r):
        r['calls_reserved']=2;r['max_calls']=3
        r['tasks'][-1].update(state='failed',attempts=1,error='output_truncated',input_sha256='synthetic-old-request')
    return service,did,service.get(did,run['id']),calls


def split(service,did,run,limit,accept=True):
    return service.control(did,run['id'],ReportControl(revision=run['revision'],action='split_synthesis',max_calls=limit,accept_new_request=accept))


def test_split_requires_budget_consent_and_reuses_every_reading_checkpoint(client,store,monkeypatch):
    service,did,run,calls=legacy_completed_reading(client,store,monkeypatch)
    assert run['split_synthesis_available'] and run['synthesis_recovery']['minimum_new_limit']==5
    original=copy.deepcopy(run['tasks'][0])
    for accept,limit in [(False,5),(True,4)]:
        with pytest.raises(EvidenceError):split(service,did,run,limit,accept)
        saved=service.get(did,run['id']);assert saved['max_calls']==3 and saved['calls_reserved']==2
    with pytest.raises(EvidenceError) as exc:ctl(service,did,run,'retry',True)
    assert exc.value.code=='report_split_required'
    active=split(service,did,run,5)
    assert active['calls_reserved']==2 and active['max_calls']==5
    assert active['superseded_tasks'][0]['attempts']==1
    assert active['tasks'][0]==original
    before=len(calls)
    asyncio.run(service.work(did,run['id'],token(service,did,active)))
    result=service.get(did,run['id'])
    assert result['state']=='completed' and result['calls_reserved']==5
    assert len(calls)-before==3 and all(k=='synthesis' for k,p in calls[before:])
    assert result['tasks'][0]==original and result['report']['reading_route']
    assert result['budget_changes']==[{'from':3,'to':5,'at':result['budget_changes'][0]['at'],'reason':'explicit_split_synthesis'}]


def test_failed_part_retry_never_repeats_completed_overview_or_reading(client,store,monkeypatch):
    service,did,run,calls=legacy_completed_reading(client,store,monkeypatch)
    seen=[]
    async def generate(payload):
        part=payload['part'];seen.append(part)
        if part=='core' and seen.count('core')==1:raise EvidenceError(502,'fixture_failure','Synthetic core failure.')
        full=report_for(payload['all_batch_notes']);return {k:full[k] for k in paper_report_part_fields(part)}
    monkeypatch.setattr(report_model,'synthesize',generate)
    active=split(service,did,run,6);asyncio.run(service.work(did,run['id'],token(service,did,active)))
    failed=service.get(did,run['id']);assert failed['state']=='failed' and failed['report'] is None
    assert next(t for t in failed['tasks'] if t.get('part')=='overview')['state']=='succeeded'
    active=ctl(service,did,failed,'retry',True);asyncio.run(service.work(did,run['id'],token(service,did,active)))
    done=service.get(did,run['id']);assert done['state']=='completed' and done['calls_reserved']==6
    assert seen==['overview','core','core','guidance']


def test_synthesis_part_does_not_allow_unrelated_report_fields(client,store,monkeypatch):
    service,did,run,calls=legacy_completed_reading(client,store,monkeypatch)
    notes=[t['output'] for t in run['tasks'] if t['kind']=='reading']
    full=report_for(notes)
    with pytest.raises(ValueError):validate_report(full,notes,'overview')
    part={k:full[k] for k in paper_report_part_fields('overview')}
    assert validate_report(part,notes,'overview')['title']


def test_source_change_prevents_budget_change(client,store,monkeypatch):
    service,did,run,calls=legacy_completed_reading(client,store,monkeypatch)
    original=service.source
    def changed(*args,**kwargs):
        eid,*rest=original(*args,**kwargs);return ('different-extraction',*rest)
    monkeypatch.setattr(service,'source',changed)
    with pytest.raises(EvidenceError) as exc:split(service,did,run,5)
    assert exc.value.code=='report_source_changed'
    after=service.get(did,run['id'])
    assert after['max_calls']==3 and after['calls_reserved']==2
    assert after['tasks']==run['tasks']


def test_truncated_split_part_keeps_notes_and_does_not_suggest_splitting_again(client,store,monkeypatch):
    service,did,run,calls=legacy_completed_reading(client,store,monkeypatch)
    active=split(service,did,run,5)
    with service.transaction(did,run['id']) as (db,r):
        r.update(state='failed',calls_reserved=3)
        failed=next(t for t in r['tasks'] if t.get('part')=='overview')
        failed.update(state='failed',attempts=1)
        path=service.store.root/'diagnostics'/(digest(f'{r["id"]}:{failed["id"]}:1'.encode())+'.json')
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({'finish_reason':'length','usage':{'completion_tokens':6000}}),encoding='utf-8')
    before=len(calls)
    result=service.get(did,run['id'])
    assert '当前已采用分段综合' in result['error']['message']
    assert '不应原样重试' in result['error']['message']
    assert '请仅将综合改为分段生成' not in result['error']['message']
    assert result['tasks'][0]==active['tasks'][0]
    assert result['calls_reserved']==3 and len(calls)==before
    assert result['budget_extension_available'] and not result['split_synthesis_available']


def test_short_report_budget_can_be_explicitly_extended_without_rereading(client,store,monkeypatch):
    service,did,run,calls=legacy_completed_reading(client,store,monkeypatch);seen=[]
    async def generate(payload):
        part=payload['part'];seen.append(part)
        if part=='core' and seen.count('core')==1:raise EvidenceError(502,'fixture_failure','Synthetic one-time failure.')
        full=report_for(payload['all_batch_notes']);return {k:full[k] for k in paper_report_part_fields(part)}
    monkeypatch.setattr(report_model,'synthesize',generate)
    active=split(service,did,run,5);asyncio.run(service.work(did,run['id'],token(service,did,active)))
    failed=service.get(did,run['id']);assert failed['budget_extension_available']
    assert failed['report_continuation']=={'additional_calls':2,'new_limit':6}
    with pytest.raises(EvidenceError):
        service.control(did,run['id'],ReportControl(revision=failed['revision'],action='extend_budget',max_calls=6))
    assert service.get(did,run['id'])['max_calls']==5
    active=service.control(did,run['id'],ReportControl(revision=failed['revision'],action='extend_budget',max_calls=6,accept_new_request=True))
    assert active['calls_reserved']==4
    asyncio.run(service.work(did,run['id'],token(service,did,active)))
    assert service.get(did,run['id'])['state']=='completed'
    assert seen==['overview','core','core','guidance']

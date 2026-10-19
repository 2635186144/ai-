"""Synthetic transport regressions, never proof of real model quality."""
import asyncio
import copy
import pytest
from backend.evidence import report_model
from backend.evidence.paper_reports import validate_notes,synthesis_notes,ReportControl
from backend.evidence.parser import EvidenceError
from tests.test_evidence import client,store
from tests.test_paper_reports import setup_run,ctl,token,mock_models,synthetic_only,note_for


def test_one_table_topic_can_cite_64_fragments_but_missing_read_evidence_still_fails():
    spans=[{'span_id':f's{i}','block_id':f'b{i}','pdf_page':5,'start':0,'end':4,'text':f'表格{i}'} for i in range(64)]
    raw=note_for({'source':[spans[0]]})
    raw['coverage']=[{'span_id':s['span_id'],'disposition':'read','reason':'表格片段'} for s in spans]
    raw['topics'][0]['citations']=[{'span_id':s['span_id'],'quote':s['text']} for s in spans]
    result=validate_notes(raw,spans,0)
    assert len(result['evidence'])==64 and len(result['topics'][0]['evidence_ids'])==64
    before=copy.deepcopy(result);compact=synthesis_notes([result])[0]
    assert result==before and compact['topics']==result['topics'] and compact['unknowns']==result['unknowns']
    assert [(e['id'],e['quote'],e['pdf_page']) for e in compact['evidence']]==[(e['id'],e['quote'],e['pdf_page']) for e in result['evidence']]
    assert 'coverage' not in compact and 'block_id' not in compact['evidence'][0]
    raw['topics'][0]['citations'].pop()
    with pytest.raises(EvidenceError) as failure:validate_notes(raw,spans,0)
    assert failure.value.code=='report_reading_evidence_missing'


def test_failed_reading_needs_explicit_extra_budget_and_keeps_original_tasks(client,store,monkeypatch):
    service,did,run=setup_run(client,store);calls=[];mock_models(monkeypatch,calls)
    original=report_model.read_chunk;attempts=[]
    async def once(payload):
        attempts.append(1)
        if len(attempts)==1:raise EvidenceError(422,'fixture_missing_reference','Synthetic failure')
        return await original(payload)
    monkeypatch.setattr(report_model,'read_chunk',once)
    with service.transaction(did,run['id']) as (db,current):current['max_calls']=len(current['tasks'])
    current=service.get(did,run['id']);old_limit=current['max_calls'];source_spans=copy.deepcopy(current['tasks'][0]['source_spans'])
    active=ctl(service,did,current,'start');asyncio.run(service.work(did,run['id'],token(service,did,active)))
    failed=service.get(did,run['id'])
    assert failed['budget_extension_available'] and failed['report_continuation']['remaining_reading']
    assert failed['report_continuation']['new_limit']==old_limit+1
    with pytest.raises(EvidenceError):ctl(service,did,failed,'retry',True)
    with pytest.raises(EvidenceError):service.control(did,run['id'],ReportControl(revision=failed['revision'],action='extend_budget',max_calls=old_limit+1))
    assert service.get(did,run['id'])['calls_reserved']==1
    active=service.control(did,run['id'],ReportControl(revision=failed['revision'],action='extend_budget',max_calls=old_limit+1,accept_new_request=True))
    asyncio.run(service.work(did,run['id'],token(service,did,active)))
    completed=service.get(did,run['id'])
    assert completed['state']=='completed' and completed['calls_reserved']==old_limit+1
    assert completed['tasks'][0]['source_spans']==source_spans and completed['tasks'][0]['attempts']==2


def test_compact_model_input_still_validates_mappings_using_full_local_positions(client,store,monkeypatch):
    service,did,run=setup_run(client,store);calls=[];mock_models(monkeypatch,calls)
    original=report_model.synthesize
    async def mapped(payload):
        assert all('coverage' not in n for n in payload['all_batch_notes'])
        assert all('block_id' not in e for n in payload['all_batch_notes'] for e in n['evidence'])
        result=await original(payload)
        if payload['part']=='overview':
            eid,sid=next((e,s) for e,s in payload['evidence_sections'].items() if s)
            result['argument_map'][0]['source_section_ids']=[sid]
            result['argument_map'][0]['content']['evidence_ids']=[eid]
        return result
    monkeypatch.setattr(report_model,'synthesize',mapped)
    active=ctl(service,did,run,'start');asyncio.run(service.work(did,run['id'],token(service,did,active)))
    complete=service.get(did,run['id'])
    assert complete['state']=='completed',complete['error']
    assert complete['report']['argument_map'][0]['source_section_ids']

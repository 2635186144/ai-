"""Source-selection, conservative legacy mapping and zero-call recovery."""
import asyncio
import json

import pytest

from backend.evidence import paper_reports, report_model
from backend.evidence.paper_reports import validate_notes
from backend.evidence.report_quotes import quote_candidates, locate, QuoteValidationError, CONTRACT
from backend.evidence.parser import EvidenceError
from tests.test_evidence import store, client
from tests.test_paper_reports import setup_run, ctl, token, note_for, mock_models, synthetic_only


def selected_note(payload):
    raw=note_for(payload)
    for topic,span in zip(raw['topics'],payload['source']):
        topic['citations']=[{'span_id':span['span_id'],'quote_id':quote_candidates(span)[0]['quote_id']}]
    return raw


def test_numbered_source_quotes_disambiguate_repeated_original_text():
    span={'span_id':'s','block_id':'b','start':17,'end':657,'pdf_page':3,'text':'甲乙丙丁。'*128}
    choices=quote_candidates(span);assert len(choices)==2 and choices[0]['text']==choices[1]['text']
    raw=note_for({'source':[span]})
    raw['topics'][0]['citations']=[{'span_id':'s','quote_id':choices[1]['quote_id']}]
    result=validate_notes(raw,[span],0);c=result['evidence'][0]
    assert c['start']==17+choices[1]['start'] and c['quote']==choices[1]['text']
    assert c['locator_method']=='supplied_quote_id'
    with pytest.raises(QuoteValidationError):locate({'span_id':'s','quote':choices[0]['text']},span,0,0)


@pytest.mark.parametrize('source,quote,accepted',[
    ('智能体\n  系统提供解释','智能体系统',True),
    ('A method\r\n uses data.','A method uses data.',True),
    ('now here','nowhere',False),('1 0','10',False),('0.8','0.9',False),
    ('甲乙\n 丙丁 / 甲乙丙丁','甲乙 丙丁',False)])
def test_legacy_mapping_preserves_words_numbers_and_ambiguity(source,quote,accepted):
    span={'span_id':'s','text':source}
    if accepted:
        start,end,method=locate({'span_id':'s','quote':quote},span,0,0)
        assert method=='unique_whitespace_mapping' and source[start:end]
    else:
        with pytest.raises(QuoteValidationError):locate({'span_id':'s','quote':quote},span,0,0)


def test_foreign_source_selection_cannot_cross_span_boundary():
    span={'span_id':'s1','text':'Original source.'}
    with pytest.raises(QuoteValidationError) as exc:locate({'span_id':'s1','quote_id':'s2:q0'},span,0,0)
    assert exc.value.safe_details['reason']=='unknown_quote_id'


def test_invalid_model_response_retained_privately_without_publishing(client,store,monkeypatch):
    service,did,run=setup_run(client,store);calls=[]
    async def bad(payload):
        calls.append(1);raw=selected_note(payload);raw['topics'][0]['citations'][0]['quote_id']='nonexistent:q999';return raw
    monkeypatch.setattr(report_model,'read_chunk',bad)
    current=ctl(service,did,run,'start');asyncio.run(service.work(did,run['id'],token(service,did,current)))
    failed=service.get(did,run['id'])
    assert failed['state']=='failed' and failed['candidate_available'] and failed['report'] is None
    assert failed['tasks'][0]['validation_failure']['reason']=='unknown_quote_id'
    assert 'nonexistent:q999' not in json.dumps(failed)
    data=json.loads(service.candidate_path(run['id'],failed['tasks'][0]).read_text(encoding='utf-8'))
    assert data['validation_state']=='unvalidated_not_published' and data['response']
    assert 'reasoning_content' not in data and 'MODEL_API_KEY' not in json.dumps(data)
    with pytest.raises(EvidenceError):ctl(service,did,failed,'revalidate')
    assert len(calls)==1 and service.get(did,run['id'])['calls_reserved']==1


def test_revalidation_via_api_is_local_and_resume_does_not_reread(client,store,monkeypatch):
    service,did,run=setup_run(client,store);calls=[];mock_models(monkeypatch,calls)
    async def selected(payload):calls.append(('read',payload));return selected_note(payload)
    monkeypatch.setattr(report_model,'read_chunk',selected)
    original=paper_reports.validate_notes
    def old_bug(*args):raise EvidenceError(422,'fixture_locator_bug','Synthetic previous validator failure.')
    monkeypatch.setattr(paper_reports,'validate_notes',old_bug)
    current=ctl(service,did,run,'start');asyncio.run(service.work(did,run['id'],token(service,did,current)))
    failed=service.get(did,run['id']);assert failed['candidate_available']
    monkeypatch.setattr(paper_reports,'validate_notes',original)
    r=client.post(f'/api/v2/documents/{did}/reports/{run["id"]}/control',json={'revision':failed['revision'],'action':'revalidate'})
    assert r.status_code==200,r.text
    restored=r.json();assert restored['state']=='paused' and restored['calls_reserved']==1
    assert restored['tasks'][0]['revalidated_locally'] and len(calls)==1
    assert restored['tasks'][0]['prompt_version']==report_model.PROMPT_VERSION
    assert restored['tasks'][0]['citation_contract']==CONTRACT
    resumed=ctl(service,did,restored,'resume');asyncio.run(service.work(did,run['id'],token(service,did,resumed)))
    assert service.get(did,run['id'])['state']=='completed'
    assert sum(c[0]=='read' for c in calls)==1


def test_retry_current_v2_batch_plan_uses_new_locator_without_new_plan(client,store,monkeypatch):
    service,did,run=setup_run(client,store)
    with service.transaction(did,run['id']) as (db,r):
        r.update(state='failed',calls_reserved=2,prompt_version='whole-paper-reader-v2-bounded-json',policy_version='report-batches-v2-7000-16')
        r['tasks'][0].update(state='failed',attempts=2,input_sha256='old-request-hash',error='report_quote_invalid')
    failed=service.get(did,run['id']);assert not failed['requires_replan'] and not failed['candidate_available']
    current=ctl(service,did,failed,'retry',True)
    task,payload=service.claim(did,run['id'],token(service,did,current))
    assert task['attempts']==3 and task['attempt_history'][0]['attempt']==2
    assert task['prompt_version']==report_model.PROMPT_VERSION
    assert payload['citation_contract']==CONTRACT and all(s['quote_candidates'] for s in payload['source'])
    assert validate_notes(selected_note(payload),payload['source'],0)['evidence']

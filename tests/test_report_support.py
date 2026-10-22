"""Synthetic contract checks, never an entailment accuracy benchmark."""
import asyncio
import copy

import pytest

from backend.evidence.parser import EvidenceError
from backend.evidence.report_support import source_pack, support_view, validate_checks
from backend.evidence import report_model
from backend.evidence.report_model import synthesize as actual_synthesize
from tests.test_paper_reports import (client, store, setup_run, ctl, token,
                                     mock_models, synthetic_only)


def block(id, text, page=1):
    return {'id': id, 'text': text, 'order_in_page': 0, 'region': {'pdf_page': page}}


def guidance(question='应该如何理解实验的适用范围？'):
    return {'conclusion': {'text': '作者在给定样本上报告了观察结果。', 'evidence_ids': ['e1']},
            'limitations': {'text': '只观察了给定样本。', 'evidence_ids': ['e1']},
            'open_questions': [question]}


def checks(report, sources=None):
    return [dict(target=p, kind='reading_question' if p.startswith('open_questions') else 'explanation',
                 judgment='supported', reason='模拟数据：原文明确限定为给定样本。', evidence_ids=['e1'],source_ids=sources or [])
            for p in ['conclusion','limitations']+[f'open_questions.{i}' for i in range(len(report['open_questions']))]]


def test_original_context_selection_is_bounded_and_exact_not_a_full_document_review():
    blocks = [block('a', 'IssuesClosureRate = issues_closed / issues_new。'),
              block('b', '模拟原文：三阶实践是从工具应用到社区参与，再到项目贡献。',2),
              block('c', 'irrelevant original text. '*1000,3)]
    original = copy.deepcopy(blocks)
    notes = [{'unknowns':['作者是否给出IssuesClosureRate公式？'],'topics':[{'heading':'三阶实践','limitations':[]}]}]
    pack = source_pack(blocks,notes,max_characters=120)
    assert pack['source_characters'] <= 120 and not pack['all_document_text_checked']
    assert any('issues_closed' in s['quote'] for s in pack['sources'])
    for s in pack['sources']:
        b = next(b for b in blocks if b['id']==s['block_id'])
        assert b['text'][s['start']:s['end']]==s['quote']
    assert blocks == original


def test_model_claim_of_support_does_not_confirm_absence_or_conflict():
    report = guidance('作者未给出公式，两处定义是否不一致？')
    report['guidance_checks'] = checks(report)
    report['evidence']=[{'id':'e1','quote':'原文已经给出计算公式。','pdf_page':5}]
    original=copy.deepcopy(report)
    view=support_view(report)
    q=view['entries'][-1]
    assert q['status']=='needs_check' and q['model_judgment']=='supported'
    assert not view['semantic_verified'] and not view['independent_review']
    assert report==original


def test_withdrawn_and_unreviewed_questions_are_preserved_not_silently_deleted():
    report=guidance('两个年份不同是否就是矛盾？')
    old=support_view(report)
    assert old['entries'][-1]['status']=='not_reviewed'
    report['guidance_checks']=checks(report)
    report['guidance_checks'][-1].update(kind='criticism',judgment='withdrawn',reason='模拟数据：两个年份本身不能证明矛盾。')
    assert support_view(report)['entries'][-1]['status']=='withdrawn'
    assert report['open_questions']==['两个年份不同是否就是矛盾？']


@pytest.mark.parametrize('invalid',['foreign_source','foreign_evidence','foreign_target','missing_target','no_basis'])
def test_bad_support_records_cannot_publish(invalid):
    report=guidance();report['guidance_checks']=checks(report)
    if invalid=='foreign_source':report['guidance_checks'][0]['source_ids']=['invented']
    elif invalid=='foreign_evidence':report['guidance_checks'][0]['evidence_ids']=['invented']
    elif invalid=='foreign_target':report['guidance_checks'][0]['target']='not_a_report_field'
    elif invalid=='missing_target':report['guidance_checks'].pop()
    else:report['guidance_checks'][0]['evidence_ids']=[]
    with pytest.raises(EvidenceError) as error:
        validate_checks(report,{'e1':{}},{'sources':[]})
    assert error.value.code=='report_support_invalid'


def test_mixed_parts_of_same_field_are_preserved_and_aggregated_conservatively():
    report=guidance();report['guidance_checks']=checks(report)
    extra={**report['guidance_checks'][1],'judgment':'unresolved','kind':'extraction_gap','reason':'模拟数据：另一分项仍需核对。'}
    report['guidance_checks'].append(extra)
    report['evidence']=[{'id':'e1','quote':'模拟原文','pdf_page':1}]
    validate_checks(report,{'e1':{}},{'sources':[]})
    entry=support_view(report)['entries'][1]
    assert entry['status']=='needs_check' and len(entry['parts'])==2
    assert entry['model_judgment']=='mixed'


def test_keyword_candidates_are_marked_as_candidates_not_counterproof():
    report=guidance('作者未给出IssuesClosureRate公式？')
    view=support_view(report,blocks=[block('formula','IssuesClosureRate = issues_closed / issues_new')])
    q=view['entries'][-1]
    assert q['status']=='not_reviewed' and q['candidate_sources']
    assert not view['source_selection_is_exhaustive']


def test_generation_keeps_same_call_count_and_checkpointed_source_context(client,store,monkeypatch):
    service,did,run=setup_run(client,store);calls=[];mock_models(monkeypatch,calls)
    current=ctl(service,did,run,'start')
    asyncio.run(service.work(did,run['id'],token(service,did,current)))
    done=service.get(did,run['id']);assert done['state']=='completed',done['error']
    assert len(calls)==len(run['tasks'])
    payload=next(p for kind,p in calls if kind=='synthesis' and p['part']=='guidance')
    task=next(t for t in done['tasks'] if t.get('part')=='guidance')
    assert task['guidance_source_pack']==payload['support_source_pack']
    assert all(e['status']=='not_reviewed' for e in done['support_review']['entries'])
    assert not done['support_review']['semantic_verified']


def test_invalid_auxiliary_review_does_not_retry_or_destroy_valid_report(client,store,monkeypatch):
    service,did,run=setup_run(client,store);calls=[];mock_models(monkeypatch,calls)
    original=report_model.synthesize
    async def invalid_review(payload):
        result=await original(payload)
        if payload.get('part')=='guidance':
            result['guidance_checks']=[{'target':'conclusion','kind':'explanation','judgment':'supported',
                'reason':'模拟数据：这个外来编号不应被采纳。','evidence_ids':[],'source_ids':['foreign-source']}]
        return result
    monkeypatch.setattr(report_model,'synthesize',invalid_review)
    active=ctl(service,did,run,'start')
    asyncio.run(service.work(did,run['id'],token(service,did,active)))
    done=service.get(did,run['id'])
    assert done['state']=='completed' and len(calls)==len(run['tasks'])
    assert done['report']['guidance_checks']==[]
    assert done['support_review']['reviewer']=='none'


def test_new_guidance_schema_explicitly_requires_review_but_old_reports_can_load(monkeypatch):
    captured={}
    async def generated(payload,**kwargs):captured.update(kwargs);return {}
    monkeypatch.setattr(report_model.fact_model,'generate',generated)
    asyncio.run(actual_synthesize({'part':'guidance'}))
    assert '"guidance_checks"' in captured['system']
    assert '"minItems": 2' in captured['system']

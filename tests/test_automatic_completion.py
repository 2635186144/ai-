"""Synthetic completion checks; no live model or research-accuracy claims."""
import asyncio
import copy
import json
import pytest
from backend.evidence import report_model
from backend.evidence.paper_reports import compact_section_context,recommended_budget,synthesis_tasks
from backend.evidence.parser import EvidenceError
from tests.test_evidence import client,store
from tests.test_paper_reports import setup_run,ctl,token,mock_models,synthetic_only


def test_chapter_mapping_compression_is_lossless_and_preserves_all_notes():
    payload={'all_batch_notes':[{'text':'模拟数据：原有方法、数字及否定条件保持不变。'}],
             'evidence_sections':{f'e{i}':'section_a' if i%2 else None for i in range(100)},
             'evidence_section_ancestors':{f'e{i}':['root','section_a'] if i%2 else [] for i in range(100)}}
    original=copy.deepcopy(payload);compact=compact_section_context(payload)
    sections={e:g['section_id'] for g in compact['section_evidence_groups'] for e in g['evidence_ids']}
    ancestors={e:g['ancestor_ids'] for g in compact['section_evidence_groups'] for e in g['evidence_ids']}
    assert sections==payload['evidence_sections'] and ancestors==payload['evidence_section_ancestors']
    assert compact['all_batch_notes']==payload['all_batch_notes'] and payload==original
    assert len(json.dumps(compact))<len(json.dumps(payload))*.6
    assert recommended_budget(15)>=36 and recommended_budget(200)<=500


def test_opted_in_completion_recovers_once_without_rereading(client,store,monkeypatch):
    service,did,run=setup_run(client,store);calls=[];mock_models(monkeypatch,calls)
    original=report_model.synthesize;seen=[]
    async def flaky(payload):
        seen.append(payload['part'])
        if len(seen)==1:raise EvidenceError(502,'fact_model_failed','Synthetic format failure')
        return await original(payload)
    monkeypatch.setattr(report_model,'synthesize',flaky)
    with service.transaction(did,run['id']) as (db,r):r['automatic_recovery']=True
    active=ctl(service,did,service.get(did,run['id']),'start')
    asyncio.run(service.work(did,run['id'],token(service,did,active)))
    done=service.get(did,run['id'])
    assert done['state']=='completed',done.get('error')
    assert done['automatic_retries']==1 and seen==['overview','overview','core','guidance']
    assert sum(t['attempts'] for t in done['tasks'] if t['kind']=='reading')==len([t for t in done['tasks'] if t['kind']=='reading'])
    assert done['calls_reserved']==len(done['tasks'])+1


def test_repeated_failure_stops_at_one_retry(client,store,monkeypatch):
    service,did,run=setup_run(client,store);calls=[];mock_models(monkeypatch,calls)
    async def bad(payload):raise EvidenceError(502,'fact_model_failed','Synthetic persistent failure')
    monkeypatch.setattr(report_model,'synthesize',bad)
    with service.transaction(did,run['id']) as (db,r):r['automatic_recovery']=True
    active=ctl(service,did,service.get(did,run['id']),'start')
    asyncio.run(service.work(did,run['id'],token(service,did,active)))
    done=service.get(did,run['id']);failed=next(t for t in done['tasks'] if t['state']=='failed')
    assert done['state']=='failed' and failed['attempts']==2 and done['automatic_retries']==1
    assert done['calls_reserved']<=done['max_calls']


def test_large_notes_are_hierarchically_consolidated_with_originals_retained(client,store,monkeypatch):
    service,did,run=setup_run(client,store);calls=[];mock_models(monkeypatch,calls)
    active=ctl(service,did,run,'start');asyncio.run(service.work(did,run['id'],token(service,did,active)))
    done=service.get(did,run['id']);base=next(t for t in done['tasks'] if t['kind']=='reading')
    with service.transaction(did,run['id']) as (db,r):
        reads=[]
        for i in range(12):
            task=copy.deepcopy(base);task.update(id=f'read_{i}',index=i)
            renames={e['id']:f'large_{i}_{j}' for j,e in enumerate(task['output']['evidence'])}
            for e in task['output']['evidence']:e['id']=renames[e['id']]
            for topic in task['output']['topics']:
                topic['explanation']='模拟数据：保留统计口径和不确定性。'*200
                topic['evidence_ids']=[renames[e] for e in topic['evidence_ids']]
            reads.append(task)
        originals=copy.deepcopy(reads)
        r.update(tasks=reads+synthesis_tasks(),report=None,state='paused',calls_reserved=12,max_calls=60,automatic_recovery=True)
    consolidated=[]
    async def digest(payload):
        consolidated.append(payload)
        assert len(json.dumps(payload,ensure_ascii=False))<140000
        eid=payload['all_batch_notes'][0]['evidence'][0]['id']
        return {'overview':'模拟数据：分层整合。','topics':[{'heading':'模拟整合','role':'保留机制与限定',
                'explanation':'模拟数据：整合后的说明，不代表真实语义质量。','method_details':[],'findings':[],
                'author_contributions':[],'limitations':['尚未视觉核验。'],'reading_focus':[],'evidence_ids':[eid]}],'unknowns':[]}
    monkeypatch.setattr(report_model,'consolidate',digest)
    active=ctl(service,did,service.get(did,run['id']),'resume')
    asyncio.run(service.work(did,run['id'],token(service,did,active)))
    final=service.get(did,run['id'])
    assert final['state']=='completed',final.get('error')
    assert consolidated and final['consolidation_level']>=1
    assert final['tasks'][:12]==originals
    assert final['synthesis_context']['characters']<=140000
    assert final['calls_reserved']==12+len(consolidated)+3

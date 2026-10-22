"""Synthetic reading/report models; these tests do not measure research quality."""
import asyncio
import copy
import json
import time

import pytest

from backend.evidence import report_model
from backend.evidence.paper_reports import PaperReports, ReportRequest, ReportControl, split_source, validate_notes, validate_report
from backend.evidence.parser import EvidenceError
from backend.evidence.store import EvidenceStore
from tests.test_evidence import store, client, upload, fixture_pdf


@pytest.fixture(autouse=True)
def synthetic_only(monkeypatch):
    monkeypatch.setenv('MODEL_API_KEY','synthetic-key-never-sent')
    monkeypatch.setenv('MODEL_NAME','synthetic-report-not-real')
    async def forbidden(*a,**kw):raise AssertionError('Unmocked model request')
    monkeypatch.setattr(report_model,'read_chunk',forbidden)
    monkeypatch.setattr(report_model,'synthesize',forbidden)


def note_for(payload):
    return {'overview':'模拟数据：本批用于验证全文阅读机制。',
            'coverage':[{'span_id':s['span_id'],'disposition':'read','reason':'Synthetic text only.'} for s in payload['source']],
            'topics':[{'heading':f'Synthetic topic {i}','role':'Fixture narrative role.','explanation':'模拟数据：解释原文，不是科研评估。',
                       'method_details':['Synthetic mechanism.'],'findings':['Author claim not validated.'],
                       'author_contributions':[],'limitations':['Synthetic only.'],'reading_focus':['Verify source scope.'],
                       'citations':[{'span_id':s['span_id'],'quote':s['text']}]} for i,s in enumerate(payload['source'])],
            'unknowns':['图表未视觉核验。']}


def report_for(notes):
    ids=[e['id'] for n in notes for e in n['evidence']]
    def insight(text):return {'text':'模拟数据：'+text,'evidence_ids':[ids[0]]}
    return {'title':insight('全文解读测试论文'),'research_direction':insight('面向论文理解的计算机系统'),
            'one_sentence':insight('用全文覆盖与原文依据帮助读者理解方法。'),
            'abstract_explanation':insight('先读全文笔记，再解释背景、方法与结果。'), 'abstract_evidence_ids':[],
            'argument_map':[{'heading':'Introduction and method','role':'提出问题并建立方法。','content':insight('解释研究问题与方法依赖。'),'connects_to':'方法随后由实验检验。'}],
            'core_content':[{'title':'理解方法机制','explanation':insight('输入是全文文字，输出是有依据的解读；实验用于检验边界。')}],
            'innovations':[{'title':'Synthetic contribution','author_claim':insight('作者声称改善阅读过程。'),'explanation':insight('把全文笔记与原文定位连接。'),'boundary':insight('测试夹具不能证明真实优势。')}],
            'reading_route':[{'priority':'first','section':'Method','why':'先理解输入输出再看实验能否支持主张。','questions':['实验支持哪些方法假设？'],
                              'prerequisites':['基础计算机知识'],'evidence_ids':[ids[-1]]}],
            'conclusion':insight('读者应理解方法与证据关系。'),'limitations':insight('视觉、数学与科研结论未核验。'),'open_questions':['真实效果尚待评估。'],'guidance_checks':[]}


def setup_run(client,store):
    did=upload(client)['id'];service=PaperReports(store)
    run=service.create(did,ReportRequest(max_calls=20,consent_to_send=True),'report-fixture')
    return service,did,run


def ctl(service,did,run,action,accept=False):
    return service.control(did,run['id'],ReportControl(revision=run['revision'],action=action,accept_new_request=accept))


def token(service,did,run):
    with service.store.connection() as db:return service.load(db,did,run['id'])['lease_token']


def paper_report_part_fields(part):
    from backend.evidence.report_models import REPORT_PART_MODELS
    return REPORT_PART_MODELS[part].model_fields if part else None


def mock_models(monkeypatch,calls):
    async def reading(payload):calls.append(('read',copy.deepcopy(payload)));return note_for(payload)
    async def synthesis(payload):
        calls.append(('synthesis',copy.deepcopy(payload)))
        report=report_for(payload['all_batch_notes'])
        schema=paper_report_part_fields(payload.get('part'))
        return {k:report[k] for k in schema} if schema else report
    monkeypatch.setattr(report_model,'read_chunk',reading);monkeypatch.setattr(report_model,'synthesize',synthesis)


def test_split_never_omits_long_blocks_or_non_bmp_offsets():
    text=('方法🌱\n'*4000)+'final appendix sentinel'
    blocks=[{'id':'long','text':text,'region':{'pdf_page':1}},{'id':'last','text':'References end','region':{'pdf_page':2}}]
    chunks=split_source(blocks);spans=[s for c in chunks for s in c]
    assert len(chunks)>1 and ''.join(s['text'] for s in spans if s['block_id']=='long')==text
    assert spans[-1]['text']=='References end'
    for s in spans:
        b=next(b for b in blocks if b['id']==s['block_id'])
        assert b['text'][s['start']:s['end']]==s['text']
    assert all(sum(len(s['text']) for s in c)<=7000 and len(c)<=64 for c in chunks)
    assert all(len(s['text'])<=3500 for s in spans)


def test_fragmented_pages_pack_short_blocks_without_omitting_source():
    blocks=[{'id':f'b{i}','text':f'第{i}段原文。','region':{'pdf_page':i//40+1}} for i in range(466)]
    chunks=split_source(blocks)
    assert len(chunks)==8
    assert [s['block_id'] for chunk in chunks for s in chunk]==[b['id'] for b in blocks]
    assert ''.join(s['text'] for c in chunks for s in c)==''.join(b['text'] for b in blocks)


def test_full_report_reads_entire_source_without_manual_preparation(client,store,monkeypatch):
    service,did,run=setup_run(client,store);calls=[];mock_models(monkeypatch,calls)
    current=ctl(service,did,run,'start');asyncio.run(service.work(did,run['id'],token(service,did,current)))
    result=service.get(did,run['id']);assert result['state']=='completed',result['error']
    assert result['coverage']['all_text_batches_processed'] and result['report']['reading_route']
    assert not result['semantic_verified'] and not result['report']['semantic_verified']
    _,blocks,*_=service.source(did)
    received=[s for kind,p in calls if kind=='read' for s in p['source']]
    assert ''.join(s['text'] for s in received)==''.join(b['text'] for b in blocks)
    assert 'appendix' in received[-1]['text'].lower()
    assert calls[-1][0]=='synthesis' and len(calls)==len(run['tasks'])
    for kind,payload in calls:
        if kind=='synthesis':
            assert payload['source_sections']
            valid={s['id'] for s in payload['source_sections']}
            assert all(sid is None or sid in valid for sid in payload['evidence_sections'].values())
    for e in result['report']['evidence']:
        b=store.block(did,e['block_id']);assert b.text[e['start']:e['end']]==e['quote']
    with store.connection() as db:
        assert db.execute('SELECT count(*) FROM reading_versions').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM fact_versions').fetchone()[0]==0
    assert PaperReports(EvidenceStore(store.root)).get(did,run['id'])==result
    assert 'lease_token' not in result and 'configuration_signature' not in result


def test_pause_and_resume_does_not_repeat_successful_reading(client,store,monkeypatch):
    service,did,run=setup_run(client,store);calls=[];mock_models(monkeypatch,calls)
    original=report_model.read_chunk
    async def scenario():
        entered,release=asyncio.Event(),asyncio.Event()
        async def wait(p):entered.set();await release.wait();return await original(p)
        monkeypatch.setattr(report_model,'read_chunk',wait)
        current=ctl(service,did,run,'start')
        worker=asyncio.create_task(service.work(did,run['id'],token(service,did,current)))
        await asyncio.wait_for(entered.wait(),5)
        pausing=ctl(service,did,service.get(did,run['id']),'pause');assert pausing['state']=='pausing'
        release.set();await asyncio.wait_for(worker,5)
        paused=service.get(did,run['id']);assert paused['state']=='paused' and paused['progress']['completed']==1
        resumed=ctl(service,did,paused,'resume');await service.work(did,run['id'],token(service,did,resumed))
    asyncio.run(scenario());assert service.get(did,run['id'])['state']=='completed'
    assert sum(k=='read' for k,p in calls)==1


@pytest.mark.parametrize('mode',['omitted_span','foreign_quote','no_citation','fake_verified'])
def test_notes_reject_omission_false_locator_and_forged_verification(mode):
    spans=[{'span_id':'s1','block_id':'b','start':10,'end':30,'pdf_page':2,'text':'unique source phrase'}]
    raw=note_for({'source':spans})
    if mode=='omitted_span':raw['coverage'][0]['span_id']='not-provided'
    if mode=='foreign_quote':raw['topics'][0]['citations'][0]['quote']='invented evidence'
    if mode=='no_citation':raw['topics']=[]
    if mode=='fake_verified':raw['semantic_verified']=True
    with pytest.raises((EvidenceError,ValueError)):validate_notes(raw,spans,0)


def test_synthesis_cannot_invent_evidence_or_claim_verified():
    spans=[{'span_id':'s','block_id':'b','start':0,'end':6,'pdf_page':1,'text':'source'}]
    notes=[validate_notes(note_for({'source':spans}),spans,0)]
    raw=report_for(notes);raw['reading_route'][0]['evidence_ids']=['invented']
    with pytest.raises(EvidenceError):validate_report(raw,notes)
    raw=report_for(notes);raw['semantic_verified']=True
    with pytest.raises(ValueError):validate_report(raw,notes)


def test_failure_stops_and_requires_explicit_new_request(client,store,monkeypatch):
    service,did,run=setup_run(client,store);calls=[]
    async def fail(p):calls.append(1);raise EvidenceError(502,'fixture_failure','Synthetic failure.')
    monkeypatch.setattr(report_model,'read_chunk',fail)
    current=ctl(service,did,run,'start');asyncio.run(service.work(did,run['id'],token(service,did,current)))
    failed=service.get(did,run['id']);assert failed['state']=='failed' and len(calls)==1
    assert failed['calls_reserved']==1 and failed['report'] is None
    with pytest.raises(EvidenceError):ctl(service,did,failed,'retry')
    mock_models(monkeypatch,calls)
    current=ctl(service,did,failed,'retry',True);asyncio.run(service.work(did,run['id'],token(service,did,current)))
    assert service.get(did,run['id'])['state']=='completed'


def test_api_plan_consent_idempotency_and_background_pipeline(client,store,monkeypatch):
    calls=[];mock_models(monkeypatch,calls)
    did=upload(client)['id'];base=f'/api/v2/documents/{did}'
    assert client.get(base+'/report-plan').json()['parsed'] is False
    data={'max_calls':20,'consent_to_send':False}
    assert client.post(base+'/reports',headers={'Idempotency-Key':'one'},json=data).json()['code']=='report_consent_required'
    data['consent_to_send']=True
    created=client.post(base+'/reports',headers={'Idempotency-Key':'one'},json=data);assert created.status_code==201,created.text
    run=created.json();assert client.post(base+'/reports',headers={'Idempotency-Key':'one'},json=data).json()['id']==run['id']
    assert client.post(base+'/reports',headers={'Idempotency-Key':'one'},json={**data,'max_calls':21}).json()['code']=='idempotency_conflict'
    url=base+'/reports/'+run['id'];r=client.post(url+'/control',json={'revision':run['revision'],'action':'start'});assert r.status_code==200
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        result=client.get(url).json()
        if result['state'] in ('completed','failed'):break
        time.sleep(.02)
    assert result['state']=='completed',result.get('error')
    assert client.get(base+'/report-plan').json()['parsed']
    assert client.get(base+'/reports').json()['items'][0]['report']


def test_lease_recovery_fences_late_response(client,store):
    service,did,run=setup_run(client,store);current=ctl(service,did,run,'start');old=token(service,did,current)
    assert service.claim(did,run['id'],old)
    with service.transaction(did,run['id']) as (db,r):r['lease_until']=time.time()-1
    interrupted=service.get(did,run['id']);assert interrupted['interrupted']
    recovered=ctl(service,did,interrupted,'pause');assert recovered['state']=='failed' and recovered['calls_reserved']==1
    assert not service.finish(did,run['id'],old,run['tasks'][0]['id'],{}, {})


def test_v4_migration_preserves_source_without_touching_reading(client,store):
    did=upload(client)['id'];before=store.file_path(did).read_bytes()
    with store.connection() as db:
        db.execute('DROP TABLE paper_report_runs');db.execute('PRAGMA user_version=4');db.commit()
    reopened=EvidenceStore(store.root)
    assert reopened.file_path(did).read_bytes()==before
    with reopened.connection() as db:assert db.execute('PRAGMA user_version').fetchone()[0]==5


def test_unreadable_content_remains_a_visible_gap(client,store,monkeypatch):
    service,did,run=setup_run(client,store);calls=[];mock_models(monkeypatch,calls)
    async def partial(payload):
        raw=note_for(payload);raw['coverage'][0].update(disposition='unreadable',reason='Synthetic damaged formula.')
        raw['unknowns'].append('Synthetic damaged formula remains unread.');return raw
    monkeypatch.setattr(report_model,'read_chunk',partial)
    current=ctl(service,did,run,'start');asyncio.run(service.work(did,run['id'],token(service,did,current)))
    result=service.get(did,run['id']);assert result['state']=='completed_with_gaps'
    assert result['coverage']['unreadable_spans'][0]['reason']=='Synthetic damaged formula.'
    assert 'Synthetic damaged formula remains unread.' in result['report']['reading_unknowns']


def test_small_budget_and_missing_key_cannot_dispatch(client,store,monkeypatch):
    did=upload(client)['id'];service=PaperReports(store)
    with pytest.raises(EvidenceError) as err:service.create(did,ReportRequest(max_calls=1,consent_to_send=True),'budget')
    assert err.value.code=='report_budget_insufficient' and not service.list(did)['items']
    monkeypatch.setenv('MODEL_API_KEY','')
    with pytest.raises(EvidenceError) as err:service.create(did,ReportRequest(max_calls=20,consent_to_send=True),'nokey')
    assert err.value.code=='model_not_configured'


def test_source_tampering_during_reading_cannot_publish_report(client,store,monkeypatch):
    service,did,run=setup_run(client,store)
    original=store.file_path(did)
    async def tamper(payload):
        original.write_bytes(b'changed synthetic source only')
        return note_for(payload)
    monkeypatch.setattr(report_model,'read_chunk',tamper)
    current=ctl(service,did,run,'start');asyncio.run(service.work(did,run['id'],token(service,did,current)))
    result=service.get(did,run['id'])
    assert result['state']=='failed' and result['report'] is None
    assert result['tasks'][0]['state']=='failed' and result['calls_reserved']==1


def test_legacy_failure_is_explained_from_safe_diagnostics_and_never_retried(client,store):
    service,did,run=setup_run(client,store)
    with service.transaction(did,run['id']) as (db,r):
        r.pop('policy_version');r['state']='failed';r['calls_reserved']=2
        r['tasks'][0].update(state='failed',attempts=2)
        r['error']={'code':'fact_model_failed','message':'Old empty content error.'}
    from backend.evidence.facts import FactWorksheet
    FactWorksheet(store).save_diagnostics(run['id'],'read_0:2',{'stage':'empty_content','finish_reason':'length','http_status':200,'content_characters':0,'usage':{'completion_tokens':6000}})
    legacy=service.get(did,run['id'])
    assert legacy['requires_replan'] and legacy['error']['code']=='report_output_truncated'
    assert legacy['last_failure']['usage']['completion_tokens']==6000 and legacy['calls_reserved']==2
    assert service.list(did)['items'][0]['last_failure']==legacy['last_failure']
    with pytest.raises(EvidenceError) as exc:ctl(service,did,legacy,'retry',True)
    assert exc.value.code=='report_replan_required'
    # Replanning is local only, and cannot silently inherit/expand the old budget.
    new=service.create(did,ReportRequest(max_calls=20,consent_to_send=True),'new-plan')
    assert new['id']!=run['id'] and new['state']=='queued' and new['calls_reserved']==0
    assert not new['requires_replan'] and service.get(did,run['id'])['calls_reserved']==2

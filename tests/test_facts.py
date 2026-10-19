import json
import sqlite3

import pytest

from backend.evidence import fact_model
from backend.evidence.fact_models import FactCommand
from backend.evidence.facts import FactWorksheet, paragraph_units
from backend.evidence.parser import EvidenceError
from backend.evidence.reading import ReadingAction, ReadingPreparation
from backend.evidence.store import EvidenceStore
from tests.test_evidence import client, store
from tests.test_reading import setup, finish


def ready(client,reading=None):
    reading=reading or setup(client)
    plan=finish(client,reading,client.get(reading).json())
    # Use one continuous section so tests exercise target/neighbor context and all blocks.
    r=client.post(reading,headers={'Idempotency-Key':'merge'},json={'revision':plan['revision'],'action':'set_sections','sections':[plan['sections'][0]]})
    plan=r.json()
    plan=client.post(reading,headers={'Idempotency-Key':'confirm-merged'},json={'revision':plan['revision'],'action':'confirm_sections'}).json()
    path=f"{reading}/sections/{plan['sections'][0]['start_block_id']}/facts"
    sheet=client.get(path,params={'reading_revision':plan['revision']}).json()
    return reading,plan,path,sheet


def draft_for(unit):
    return {'disposition':'analysed','explanation':'This unit explains the research setup; source support remains unverified.',
            'facts':[{'category':'method','basis':'author_statement','statement':'A source-grounded fixture reading.',
                      'reasoning':'','conditions':['Synthetic test fixture only'],
                      'citations':[{'block_id':unit['block_id'],'start':unit['start'],'end':unit['end'],'quote':unit['text']}]}],
            'unknowns':[],'unresolved_dependencies':[]}


def command(sheet,unit=None,draft=None):
    unit=unit or sheet['units'][0]
    return {'reading_revision':sheet['reading_revision'],'revision':sheet['revision'],'unit_id':unit['id'],
            'draft':draft or draft_for(unit)}


def save(client,path,payload,key='save'):
    return client.post(path,headers={'Idempotency-Key':key},json=payload)


def enable_model(monkeypatch):
    monkeypatch.setenv('MODEL_API_KEY','fixture-only-never-sent')
    monkeypatch.setenv('MODEL_NAME','fixture-model-not-real')


def test_units_cover_nonblank_source_with_exact_offsets():
    text='  First paragraph.\nLine two.\n\n Second paragraph.  \n\nThird.'
    units=paragraph_units({'blocks':[{'id':'block','text':text,'text_sha256':'fixture','region':{'pdf_page':1}}]})
    assert len(units)==3
    assert ''.join(''.join(u['text'].split()) for u in units)==''.join(text.split())
    assert all(text[u['start']:u['end']]==u['text'] for u in units)
    assert len({u['id'] for u in units})==3


def test_fact_gating_manual_save_idempotency_persistence_export_fields(client,store):
    reading=setup(client);plan=client.get(reading).json()
    path=f"{reading}/sections/{plan['sections'][0]['start_block_id']}/facts"
    assert client.get(path,params={'reading_revision':plan['revision']}).json()['code']=='preparation_incomplete'
    # Reuse already registered fixture; ready() also verifies registration idempotency.
    reading,plan,path,sheet=ready(client,reading)
    assert sheet['coverage']['pending']==len(sheet['units']) and not sheet['coverage']['semantic_verified']
    payload=command(sheet);r=save(client,path,payload);assert r.status_code==200,r.text
    updated=r.json();assert updated['coverage']['analysed']==1
    assert updated['units'][0]['semantic_check']=='not_verified'
    assert updated['units'][0]['producer']['kind']=='local_user'
    assert save(client,path,payload).json()==updated
    assert save(client,path,{**payload,'revision':updated['revision']}).json()['code']=='idempotency_conflict'
    assert save(client,path,payload,key='stale').json()['code']=='stale_fact_revision'
    reopened=FactWorksheet(EvidenceStore(store.root)).get(sheet['document_version_id'],sheet['section']['id'],sheet['reading_revision'])
    assert reopened['units'][0]['draft']==payload['draft']
    with store.connection() as db:
        with pytest.raises(sqlite3.IntegrityError):db.execute("UPDATE fact_versions SET payload_json='{}'")


@pytest.mark.parametrize('kind',['fabricated','wrong_offsets','foreign_block','wrong_unit','inference_without_reason','unknown_only_analysed','fake_verified'])
def test_invalid_fact_does_not_publish_partial_unit(client,store,kind):
    _,_,path,sheet=ready(client);payload=command(sheet);d=payload['draft'];citation=d['facts'][0]['citations'][0]
    if kind=='fabricated':citation['quote']='Not present in the paper'
    elif kind=='wrong_offsets':citation['start']+=1
    elif kind=='foreign_block':citation['block_id']='other-document-block'
    elif kind=='wrong_unit':payload['unit_id']=sheet['units'][1]['id']
    elif kind=='inference_without_reason':d['facts'][0]['basis']='reader_inference'
    elif kind=='unknown_only_analysed':d['facts']=[]
    else:d['semantic_verified']=True
    assert save(client,path,payload).status_code==422
    fresh=client.get(path,params={'reading_revision':sheet['reading_revision']}).json()
    assert fresh['revision']==1 and fresh['coverage']['analysed']==0
    with store.connection() as db:assert db.execute('SELECT count(*) FROM fact_versions').fetchone()[0]==1


def test_blocked_and_non_substantive_are_separate_from_analysed(client):
    _,_,path,sheet=ready(client)
    d={'disposition':'blocked','explanation':'Need readable equation.','facts':[],
       'unknowns':[{'question':'What does the equation mean?','why_unresolved':'Unreadable extraction','needed_evidence':'Original equation image'}],
       'unresolved_dependencies':['Equation definition in another section']}
    sheet=save(client,path,command(sheet,draft=d)).json()
    assert sheet['coverage']['blocked']==1 and sheet['coverage']['analysed']==0
    d={'disposition':'non_substantive','explanation':'Fixture title only.','facts':[],'unknowns':[],'unresolved_dependencies':[]}
    sheet=save(client,path,command(sheet,unit=sheet['units'][1],draft=d),key='title').json()
    assert sheet['coverage']['non_substantive']==1 and not sheet['coverage']['text_recording_complete']


def test_no_configuration_no_model_request_or_fake_draft(client,monkeypatch,store):
    monkeypatch.delenv('MODEL_API_KEY',raising=False);monkeypatch.delenv('MODEL_NAME',raising=False)
    _,_,path,sheet=ready(client);payload=command(sheet);payload.pop('draft')
    r=client.post(path+'/generate',headers={'Idempotency-Key':'not-configured'},json=payload)
    assert r.status_code==409 and r.json()['code']=='model_not_configured'
    with store.connection() as db:assert db.execute('SELECT count(*) FROM fact_requests').fetchone()[0]==0


def test_model_draft_context_provenance_and_retry_no_second_call(client,store,monkeypatch):
    enable_model(monkeypatch);_,_,path,sheet=ready(client);calls=[]
    async def fake(payload):
        calls.append(payload)
        return draft_for(sheet['units'][0])
    monkeypatch.setattr(fact_model,'generate',fake)
    payload=command(sheet);payload.pop('draft')
    r=client.post(path+'/generate',headers={'Idempotency-Key':'generate-once'},json=payload)
    assert r.status_code==200,r.text
    generated=r.json();producer=generated['units'][0]['producer']
    assert producer['kind']=='model' and producer['context_sha256']
    assert len(calls[0]['context_blocks'])==2 and calls[0]['scope']=='target_and_adjacent_blocks_only'
    assert client.post(path+'/generate',headers={'Idempotency-Key':'generate-once'},json=payload).json()==generated
    assert len(calls)==1
    newer={**payload,'revision':generated['revision']}
    assert client.post(path+'/generate',headers={'Idempotency-Key':'do-not-overwrite'},json=newer).json()['code']=='draft_already_exists'
    with store.connection() as db:assert db.execute("SELECT state FROM fact_requests").fetchone()[0]=='succeeded'


@pytest.mark.parametrize('error',['malformed','fake_quote','network','unseen_context'])
def test_failed_model_response_never_counts_as_read(client,store,monkeypatch,error):
    enable_model(monkeypatch);_,_,path,sheet=ready(client)
    async def fake(payload):
        if error=='network':raise EvidenceError(502,'fact_model_failed','Fixture timeout')
        if error=='malformed':return {'summary':'Not the required schema'}
        d=draft_for(sheet['units'][0])
        if error=='fake_quote':d['facts'][0]['citations'][0]['quote']='fabricated'
        else:
            last=sheet['units'][-1]
            d['facts'][0]['citations'].append({'block_id':last['block_id'],'start':last['start'],'end':last['end'],'quote':last['text']})
        return d
    monkeypatch.setattr(fact_model,'generate',fake)
    payload=command(sheet);payload.pop('draft')
    r=client.post(path+'/generate',headers={'Idempotency-Key':'invalid-model'},json=payload)
    assert r.status_code in (422,502)
    with store.connection() as db:
        assert db.execute('SELECT state FROM fact_requests').fetchone()[0]=='failed'
        assert db.execute('SELECT count(*) FROM fact_versions').fetchone()[0]==1
    repeat=client.post(path+'/generate',headers={'Idempotency-Key':'invalid-model'},json=payload)
    assert repeat.json()['code']=='fact_request_failed'


@pytest.mark.parametrize('edit',['reading','manual'])
def test_inflight_changes_reject_model_overwrite_and_preserve_history(client,store,monkeypatch,edit):
    enable_model(monkeypatch);reading,plan,path,sheet=ready(client)
    async def fake(payload):
        if edit=='reading':
            ReadingPreparation(store).update(sheet['document_version_id'],ReadingAction(revision=plan['revision'],action='confirm_page',page=1,status='pending'),'inflight-reading')
        else:
            FactWorksheet(store).update(sheet['document_version_id'],sheet['section']['id'],FactCommand.model_validate(command(sheet)),'manual-inflight')
        return draft_for(sheet['units'][0])
    monkeypatch.setattr(fact_model,'generate',fake)
    payload=command(sheet);payload.pop('draft')
    r=client.post(path+'/generate',headers={'Idempotency-Key':'racing'},json=payload)
    assert r.status_code==409
    with store.connection() as db:
        assert db.execute("SELECT state FROM fact_requests WHERE request_key='racing'").fetchone()[0]=='failed'
        latest=json.loads(db.execute('SELECT payload_json FROM fact_versions ORDER BY revision DESC LIMIT 1').fetchone()[0])
        if edit=='manual':assert latest['units'][0]['producer']['kind']=='local_user'
        else:assert latest['revision']==1


def test_v2_migration_preserves_reading_snapshots_and_sources(tmp_path):
    from tests.test_evidence import fixture_pdf
    s=EvidenceStore(tmp_path/'v2');did=s.register(fixture_pdf(),'fixture.pdf','main','one').id;s.parse(did,'parse')
    before=ReadingPreparation(s).get(did)
    with s.connection() as db:
        db.execute('DROP TABLE paper_report_runs')
        db.execute('DROP TABLE workflow_events');db.execute('DROP TABLE workflow_runs')
        db.execute('DROP TRIGGER fact_versions_no_update');db.execute('DROP TRIGGER fact_versions_no_delete')
        db.execute('DROP TABLE fact_requests');db.execute('DROP TABLE fact_versions');db.execute('PRAGMA user_version=2');db.commit()
    upgraded=EvidenceStore(s.root)
    assert ReadingPreparation(upgraded).get(did)==before
    assert upgraded.file_path(did).read_bytes()==s.file_path(did).read_bytes()
    with upgraded.connection() as db:assert db.execute('PRAGMA user_version').fetchone()[0]==5


def test_unique_model_quote_offsets_repaired_but_ambiguous_quotes_rejected(client,monkeypatch):
    enable_model(monkeypatch);_,_,path,sheet=ready(client)
    async def fake(payload):
        d=draft_for(sheet['units'][0]);d['facts'][0]['citations'][0]['start']=1
        return d
    monkeypatch.setattr(fact_model,'generate',fake)
    payload=command(sheet);payload.pop('draft')
    r=client.post(path+'/generate',headers={'Idempotency-Key':'repair-unique'},json=payload)
    assert r.status_code==200,r.text
    u=r.json()['units'][0]
    assert u['producer']['citation_offset_corrections'][0]['new_start']==sheet['units'][0]['start']
    assert u['semantic_check']=='not_verified'
    # Identical repeated text without a valid supplied offset must remain unresolved.
    from backend.evidence.fact_models import ParagraphDraft
    packet={'blocks':[{'id':'repeated','text':'abc abc'}]}
    d=draft_for({'block_id':'repeated','start':1,'end':3,'text':'abc'})
    parsed=ParagraphDraft.model_validate(d)
    assert FactWorksheet.locate_model_quotes(parsed,packet,{'repeated'})==[]
    assert parsed.facts[0].citations[0].start==1


def test_pending_model_reservation_prevents_duplicate_and_has_interrupted_recovery(client,store,monkeypatch):
    from backend.evidence.fact_models import GenerateFactCommand
    enable_model(monkeypatch);_,_,path,sheet=ready(client)
    payload=command(sheet);payload.pop('draft');c=GenerateFactCommand.model_validate(payload)
    service=FactWorksheet(store)
    reservation=service.reserve(sheet['document_version_id'],sheet['section']['id'],c,'reserved')
    same=client.post(path+'/generate',headers={'Idempotency-Key':'reserved'},json=payload)
    assert same.json()['code']=='fact_request_in_progress'
    different=client.post(path+'/generate',headers={'Idempotency-Key':'competing'},json=payload)
    assert different.json()['code']=='fact_generation_busy'
    with store.connection() as db:
        db.execute("UPDATE fact_requests SET created_at='2020-01-01T00:00:00+00:00' WHERE request_key='reserved'");db.commit()
    recovery=client.post(path+'/generate',headers={'Idempotency-Key':'recover'},json=payload)
    assert recovery.json()['code']=='call_outcome_unknown'
    with store.connection() as db:
        assert db.execute("SELECT state FROM fact_requests WHERE request_key='reserved'").fetchone()[0]=='failed'


@pytest.mark.parametrize('mode',['success','invalid_content','http_failure'])
def test_compatible_model_adapter_with_local_mock_transport(monkeypatch,mode):
    import asyncio
    import httpx
    enable_model(monkeypatch)
    monkeypatch.setenv('MODEL_BASE_URL','https://fixture.invalid/v1')
    requests=[]
    def transport(request):
        requests.append(request)
        if mode=='http_failure':return httpx.Response(503,json={'error':'internal server details never exposed'})
        return httpx.Response(200,json={'choices':[{'message':{'content':'```json\n{"fixture":true}\n```' if mode=='success' else None}}]})
    original=fact_model.httpx.AsyncClient
    monkeypatch.setattr(fact_model.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(transport),**kwargs))
    if mode=='success':assert asyncio.run(fact_model.generate({'target':{'text':'fixture only'}}))=={'fixture':True}
    else:
        with pytest.raises(EvidenceError) as exc:asyncio.run(fact_model.generate({'target':{}}))
        assert exc.value.code=='fact_model_failed' and 'internal server details' not in exc.value.message
    assert len(requests)==1 and str(requests[0].url)=='https://fixture.invalid/v1/chat/completions'
    payload=json.loads(requests[0].content)
    assert payload['max_tokens']==6000 and payload['messages'][0]['role']=='system'

import json
import sqlite3
from pathlib import Path

import pytest

from backend.evidence.reading import ReadingAction, ReadingPreparation
from backend.evidence.store import EvidenceStore
from backend.evidence.parser import EvidenceError
from tests.test_evidence import fixture_pdf, store, client, upload


def setup(client, content=None):
    did = upload(client, content)['id']
    client.post(f'/api/v2/documents/{did}/parse', headers={'Idempotency-Key':'parse'})
    return f'/api/v2/documents/{did}/reading'


def change(client, path, plan, action, key=None, **fields):
    return client.post(path, headers={'Idempotency-Key':key or f"{plan['revision']}-{action}"},
                       json={'revision':plan['revision'], 'action':action, **fields})


def finish(client, path, plan):
    plan = change(client,path,plan,'confirm_sections').json()
    for p in plan['pages']:
        plan = change(client,path,plan,'confirm_page',page=p['page'],status='confirmed').json()
    for m in plan['materials']:
        plan = change(client,path,plan,'mark_material',material_id=m['id'],status='indexed').json()
    return plan


def test_preparation_partition_gates_and_source_packet(client, store):
    path = setup(client)
    plan = client.get(path).json()
    assert plan['revision']==1 and not plan['readiness']['ready']
    ids = [bid for s in plan['section_index'] for bid in s['block_ids']]
    assert len(ids)==len(set(ids))==len(plan['block_previews'])
    assert any('Appendix' in s['title'] for s in plan['sections'])
    sid=plan['section_index'][0]['id']
    assert client.get(f'{path}/sections/{sid}/packet?revision=1').status_code==409
    plan=finish(client,path,plan)
    assert plan['readiness']['ready'] and plan['model_calls']==0
    packet=client.get(f"{path}/sections/{sid}/packet?revision={plan['revision']}").json()
    assert packet['status']=='source_packet_ready' and not packet['semantic_reading_performed']
    assert packet['blocks'][0]['region']['document_version_id'] in path
    assert packet['blocks'][0]['text_sha256']
    reopened=ReadingPreparation(EvidenceStore(store.root)).get(packet['document_version_id'])
    assert reopened['revision']==plan['revision']
    with store.connection() as db:
        assert db.execute('SELECT count(*) FROM reading_versions').fetchone()[0]==plan['revision']
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE reading_versions SET payload_json='{}'")


def test_revision_idempotency_order_and_invalidation(client):
    path=setup(client);plan=client.get(path).json()
    result=change(client,path,plan,'confirm_sections',key='stable')
    assert result.status_code==200
    current=result.json()
    assert change(client,path,plan,'confirm_sections',key='stable').json()==current
    assert change(client,path,plan,'confirm_sections',key='other').json()['code']=='stale_reading_revision'
    assert change(client,path,current,'confirm_sections',key='stable').json()['code']=='idempotency_conflict'
    p=current['pages'][0];ids=p['block_ids']
    for invalid in [ids[:-1],ids+[ids[0]],ids[:-1]+[current['pages'][1]['block_ids'][0]]]:
        assert change(client,path,current,'set_order',block_ids=invalid,page=1).status_code==422
    current=finish(client,path,current)
    changed=change(client,path,current,'set_order',block_ids=list(reversed(ids)),page=1).json()
    assert not changed['sections_confirmed'] and not changed['readiness']['ready']
    assert changed['pages'][0]['status']=='pending'
    assert set(b for s in changed['section_index'] for b in s['block_ids'])==set(b['id'] for b in changed['block_previews'])
    assert client.get(f"{path}/sections/{changed['sections'][0]['start_block_id']}/packet?revision={current['revision']}").json()['code']=='stale_reading_revision'


def test_section_edits_reject_foreign_omitted_or_duplicate_anchor(client):
    path=setup(client);plan=client.get(path).json()
    original=plan['sections']
    for invalid in [original[1:],original+[original[0]],[{**original[0],'start_block_id':'foreign'}]]:
        assert change(client,path,plan,'set_sections',sections=invalid).status_code==422
    changed=change(client,path,plan,'set_sections',sections=[{**original[0],'title':'Front matter'}]).json()
    assert len(changed['section_index'][0]['block_ids'])==len(changed['block_previews'])
    assert not changed['sections_confirmed']
    confirmed=change(client,path,changed,'confirm_sections').json()
    refreshed=change(client,path,confirmed,'refresh_sections').json()
    assert not refreshed['sections_confirmed'] and refreshed['last_action']['actor']=='tool'
    assert refreshed['pages']==confirmed['pages']
    assert len(refreshed['sections'])==len(original)


def test_sparse_page_gap_exclusion_and_material_gate(client):
    path=setup(client,fixture_pdf(image_page=True));plan=client.get(path).json()
    assert change(client,path,plan,'confirm_page',page=3,status='confirmed').json()['code']=='text_gap_unresolved'
    assert change(client,path,plan,'confirm_page',page=3,status='excluded').status_code==422
    plan=change(client,path,plan,'confirm_page',page=3,status='needs_source',note='OCR needed').json()
    assert 3 in plan['readiness']['unconfirmed_pages']
    plan=change(client,path,plan,'confirm_page',page=3,status='excluded',note='Image-only page outside text-only pilot; not fully read').json()
    for page in [1,2]:
        plan=change(client,path,plan,'confirm_page',page=page,status='confirmed').json()
    plan=change(client,path,plan,'confirm_sections').json()
    assert not plan['readiness']['ready']  # Even excluded pages do not silently clear material candidates.
    for m in plan['materials']:
        plan=change(client,path,plan,'mark_material',material_id=m['id'],status='indexed').json()
    assert plan['readiness']['ready'] and plan['readiness']['excluded_pages']==[3]
    mid=plan['materials'][0]['id']
    plan=change(client,path,plan,'mark_material',material_id=mid,status='needs_source',note='Need a readable crop').json()
    assert not plan['readiness']['ready']


def test_migrate_v1_preserves_source_and_unknown_database_rejected(tmp_path):
    root=tmp_path/'legacy';s=EvidenceStore(root)
    did=s.register(fixture_pdf(),'paper.pdf','main','one').id;s.parse(did,'two')
    with s.connection() as db:
        db.execute('DROP TABLE paper_report_runs')
        db.execute('DROP TABLE workflow_events');db.execute('DROP TABLE workflow_runs')
        db.execute('DROP TRIGGER fact_versions_no_update');db.execute('DROP TRIGGER fact_versions_no_delete')
        db.execute('DROP TABLE fact_requests');db.execute('DROP TABLE fact_versions')
        db.execute('DROP TRIGGER reading_versions_no_update');db.execute('DROP TRIGGER reading_versions_no_delete')
        db.execute('DROP TABLE reading_requests');db.execute('DROP TABLE reading_versions')
        db.execute('PRAGMA user_version=1');db.commit()
    upgraded=EvidenceStore(root)
    assert upgraded.detail(did).extraction.block_count>0
    with upgraded.connection() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==5
    assert ReadingPreparation(upgraded).get(did)['revision']==1


def test_real_lora_structure_is_unconfirmed_and_lossless(tmp_path):
    sample=Path(__file__).resolve().parents[1]/'knowledge/lora/paper.pdf'
    if not sample.exists():
        pytest.skip('Optional real-paper fixture is not distributed with the source repository')
    s=EvidenceStore(tmp_path/'lora');did=s.register(sample.read_bytes(),'LoRA.pdf','main','lora').id;s.parse(did,'parse')
    plan=ReadingPreparation(s).get(did)
    titles=[s['title'] for s in plan['sections']]
    assert any('OUR METHOD' in s for s in titles)
    assert any('REFERENCES' in s for s in titles)
    assert any('MEASURING SIMILARITY' in s for s in titles)
    assert any('7.2' in s for s in titles)
    assert any(s['level']==2 for s in plan['sections'])
    assert len(plan['pages'])==26
    assert sum(len(s['block_ids']) for s in plan['section_index'])==628
    assert not plan['readiness']['ready'] and not plan['sections_confirmed']
    assert all(p['status']=='pending' for p in plan['pages'])

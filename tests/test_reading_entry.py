"""Unverified extraction can be read explicitly; preview never claims verification."""
from backend.evidence.reading import ReadingPreparation
from tests.test_evidence import store, client
from tests.test_reading import setup, change


def test_preview_is_read_only_and_does_not_unlock_facts(client):
    path=setup(client);plan=client.get(path).json();sid=plan['section_index'][0]['id']
    packet=client.get(f'{path}/sections/{sid}/packet',params={'revision':plan['revision'],'preview':True})
    assert packet.status_code==200
    assert packet.json()['preview_only'] and not packet.json()['source_preparation']['human_structure_verified']
    assert client.get(path).json()['revision']==plan['revision']
    assert client.get(f'{path}/sections/{sid}/facts',params={'reading_revision':plan['revision']}).json()['code']=='preparation_incomplete'


def test_accept_extraction_retains_pending_and_transfers_uncertainty(client):
    path=setup(client);plan=client.get(path).json()
    accepted=change(client,path,plan,'use_extracted').json()
    assert accepted['readiness']['can_read'] and not accepted['readiness']['ready']
    assert not accepted['sections_confirmed'] and all(p['status']=='pending' for p in accepted['pages'])
    assert all(m['status']=='pending' for m in accepted['materials'])
    sid=accepted['section_index'][0]['id']
    sheet=client.get(f'{path}/sections/{sid}/facts',params={'reading_revision':accepted['revision']}).json()
    assert sheet['source_preparation']['accepted_unverified_extraction']
    assert sheet['source_preparation']['unconfirmed_pages']
    assert not sheet['coverage']['semantic_verified']
    edited=change(client,path,accepted,'set_sections',sections=accepted['sections']).json()
    assert not edited['readiness']['can_read']


def test_known_missing_source_is_not_silently_bypassed(client):
    path=setup(client);plan=client.get(path).json()
    plan=change(client,path,plan,'confirm_page',page=1,status='needs_source',note='Synthetic missing source').json()
    r=change(client,path,plan,'use_extracted')
    assert r.status_code==409 and r.json()['code']=='source_gap_unresolved'


def test_repeated_margin_headers_and_years_are_not_chapters():
    def block(i,text,page,y):return {'id':str(i),'text':text,'max_font_size':11,'region':{'pdf_page':page,'page_height':800,'bbox':[20,y,500,y+15]}}
    blocks=[block(0,'Title',1,100),block(1,'68 政法论坛 2026 年',2,20),block(2,'2026 年',2,60),block(3,'2 Method',2,150),block(4,'69 政法论坛 2026 年',3,20)]
    class Summary:pages=[]
    plan=ReadingPreparation.propose('synthetic',blocks,Summary(),[])
    assert [s['title'] for s in plan['sections']]==['文首与题录','2 Method']

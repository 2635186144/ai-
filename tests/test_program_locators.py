import pytest

from backend.evidence import fact_model
from backend.evidence.facts import FactWorksheet
from backend.evidence.parser import EvidenceError
from tests.test_evidence import client, store
from tests.test_facts import ready, command, draft_for, enable_model


def without_offsets(draft):
    for f in draft['facts']:
        for c in f['citations']:
            c.pop('start',None);c.pop('end',None)
    return draft


def test_unicode_quote_locations_are_program_generated():
    text='含有😀字符的段落，引用必须逐字匹配。'
    raw=without_offsets(draft_for({'block_id':'b1','start':0,'end':len(text),'text':text}))
    parsed,locations=FactWorksheet.parse_model_draft(raw,{'blocks':[{'id':'b1','text':text}]},{'b1'})
    assert parsed.facts[0].citations[0].end==len(text)
    assert locations==[{'block_id':'b1','start':0,'end':len(text),'method':'unique_exact_match'}]


@pytest.mark.parametrize('mode',['repeated','missing','foreign'])
def test_no_offset_quote_never_guesses_or_accepts_unprovided_source(mode):
    raw=without_offsets(draft_for({'block_id':'b1','start':0,'end':3,'text':'abc'}))
    packet={'blocks':[{'id':'b1','text':'abc abc' if mode=='repeated' else 'different' if mode=='missing' else 'abc'}]}
    with pytest.raises(EvidenceError) as error:
        FactWorksheet.parse_model_draft(raw,packet,set() if mode=='foreign' else {'b1'})
    assert error.value.code==('ambiguous_fact_citation' if mode=='repeated' else 'invalid_fact_citation')


def test_missing_offsets_generated_then_verified_in_normal_pipeline(client,store,monkeypatch):
    enable_model(monkeypatch);_,_,path,sheet=ready(client)
    async def fake(payload):return without_offsets(draft_for(sheet['units'][0]))
    monkeypatch.setattr(fact_model,'generate',fake)
    payload=command(sheet);payload.pop('draft')
    response=client.post(path+'/generate',headers={'Idempotency-Key':'program-locator'},json=payload)
    assert response.status_code==200,response.text
    unit=response.json()['units'][0]
    assert unit['producer']['program_located_citations'][0]['method']=='unique_exact_match'
    assert unit['citation_check']=='exact_text_and_offsets_located'
    assert unit['semantic_check']=='not_verified'

import copy

import pymupdf
import pytest

from backend.evidence.fact_models import ParagraphDraft
from backend.evidence.facts import FactWorksheet, paragraph_units
from backend.evidence.parser import EvidenceError
from backend.evidence.source_scope import quote_positions
from backend.evidence.support import SupportCommand, check_review
from tests.test_evidence import client, store
from tests.test_facts import command, draft_for, save
from tests.test_reading import change, finish, setup


def merged_pdf():
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 70), 'Algorithm tail outside method.\n2.1 Method\n'
                         'The method has unique method evidence. It must remain source grounded and precisely scoped.\n'
                         'The implementation description is a synthetic test fixture and not a research claim.\n'
                         '2.2 Evaluation\nThe next section contains evaluation-only sentinel.', fontsize=11)
        return doc.tobytes()


def prepared(client):
    path = setup(client, merged_pdf())
    plan = client.get(path).json()
    assert len(plan['block_previews']) == 1  # Both section boundaries are in one canonical block.
    assert [s['title'] for s in plan['sections']][1:] == ['2.1 Method', '2.2 Evaluation']
    plan = finish(client, path, plan)
    method = next(s for s in plan['section_index'] if s['title'] == '2.1 Method')
    packet = client.get(f"{path}/sections/{method['id']}/packet", params={'revision': plan['revision']}).json()
    return path, plan, packet


def test_same_block_sections_partition_and_canonical_hashes_survive(client, store):
    path, plan, packet = prepared(client)
    spans = [span for s in plan['section_index'] for span in s['source_spans']]
    spans.sort(key=lambda s: s['start'])
    assert spans[0]['start'] == 0
    assert all(a['end'] == b['start'] for a, b in zip(spans, spans[1:]))
    original = packet['blocks'][0]
    assert spans[-1]['end'] == len(original['text'])
    assert 'Algorithm tail' in original['text'] and 'evaluation-only' in original['text']
    assert 'Algorithm tail' not in packet['source_spans'][0]['text']
    assert 'evaluation-only' not in packet['source_spans'][0]['text']
    assert '~' in packet['section']['id']
    did = packet['document_version_id']
    with store.connection() as db:
        row = db.execute('SELECT payload_json FROM evidence_blocks WHERE id=?', (original['id'],)).fetchone()
    import json
    assert json.loads(row[0])['text_sha256'] == original['text_sha256']
    import hashlib
    raw = store.file_path(did).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == store.detail(did).document.sha256


@pytest.mark.parametrize('mode', ['duplicate', 'first_nonzero', 'end_of_block', 'out_of_order'])
def test_invalid_character_boundaries_do_not_publish(client, mode):
    path = setup(client, merged_pdf()); plan = client.get(path).json()
    sections = copy.deepcopy(plan['sections'])
    if mode == 'duplicate':
        sections.append(copy.deepcopy(sections[-1]))
    elif mode == 'first_nonzero':
        sections[0]['start_offset'] = 1
    elif mode == 'end_of_block':
        sections[-1]['start_offset'] = plan['block_previews'][0]['text_length']
    else:
        sections[1], sections[2] = sections[2], sections[1]
    r = change(client, path, plan, 'set_sections', sections=sections)
    assert r.status_code == 422, r.text
    assert client.get(path).json()['revision'] == plan['revision']


def test_section_scope_reaches_units_preview_manual_and_model_locators(client):
    path, plan, packet = prepared(client)
    units = paragraph_units(packet)
    assert len(units) == 1
    unit = units[0]
    raw = packet['blocks'][0]['text']
    assert raw[unit['start']:unit['end']] == unit['text']
    assert unit['start'] > 0 and 'Algorithm tail' not in unit['text'] and 'evaluation-only' not in unit['text']
    payload, allowed = FactWorksheet.build_payload(packet, unit)
    assert all('Algorithm tail' not in b['text'] and 'evaluation-only' not in b['text'] for b in payload['context_blocks'])
    assert payload['context_blocks'][0]['source_start'] == packet['source_spans'][0]['start']
    d = draft_for(unit)
    FactWorksheet.validate_draft(ParagraphDraft.model_validate(d), unit, packet)
    # An auxiliary quote outside the section cannot piggyback on a valid target quote.
    bad = copy.deepcopy(d)
    bad['facts'][0]['citations'].append({'block_id': unit['block_id'], 'start': 0, 'end': 14, 'quote': raw[:14]})
    with pytest.raises(EvidenceError):
        FactWorksheet.validate_draft(ParagraphDraft.model_validate(bad), unit, packet)
    citation = d['facts'][0]['citations'][0]
    citation.pop('start'); citation.pop('end')
    parsed, locations = FactWorksheet.parse_model_draft(d, packet, allowed)
    assert parsed.facts[0].citations[0].start == unit['start'] and locations[0]['start'] > 0
    bad = copy.deepcopy(d); bad['facts'][0]['citations'][0]['quote'] = 'evaluation-only'
    with pytest.raises(EvidenceError):
        FactWorksheet.parse_model_draft(bad, packet, allowed)
    # Persist through the real API, with absolute source offsets.
    fact_path = path + '/sections/' + packet['section']['id'] + '/facts'
    sheet = client.get(fact_path, params={'reading_revision': plan['revision']}).json()
    r = save(client, fact_path, command(sheet), 'offset-save')
    assert r.status_code == 200, r.text
    assert r.json()['units'][0]['draft']['facts'][0]['citations'][0]['start'] == unit['start']


def test_unicode_and_quote_uniqueness_are_within_provided_ranges():
    text = '😀outside repeated\n2 方法\nrepeated\n3 后续 repeated'
    lo, hi = text.index('2 方法'), text.index('3 后续')
    packet = {'blocks': [{'id': 'b', 'text': text, 'text_sha256': 'fixture', 'region': {'pdf_page': 1}}],
              'source_spans': [{'block_id': 'b', 'start': lo, 'end': hi}]}
    assert quote_positions(packet, 'b', 'repeated') == [text.index('repeated', lo)]
    unit = paragraph_units(packet)[0]
    assert unit['start'] == lo and unit['text'].startswith('2 方法')
    assert text[unit['start']:unit['end']] == unit['text']
    assert quote_positions({'blocks': [{'id': 'b', 'text': 'aaaa'}]}, 'b', 'aaa') == [0, 1]


def test_support_review_cannot_claim_checked_scope_outside_section(client):
    _, plan, packet = prepared(client)
    unit = paragraph_units(packet)[0]
    fact = draft_for(unit)['facts'][0]
    raw = packet['blocks'][0]['text']
    c = SupportCommand(reading_revision=plan['revision'], revision=1, unit_id=unit['id'], fact_index=0,
        parts=[{'start': 0, 'end': len(fact['statement']), 'text': fact['statement'], 'verdict': 'supported',
                'reason': 'Synthetic fixture review only.', 'citation_indices': [0],
                'checked_scope': [{'block_id': unit['block_id'], 'start': 0, 'end': len(raw), 'quote': raw}]}])
    with pytest.raises(EvidenceError) as error:
        check_review(fact, unit, packet, c)
    assert error.value.code == 'invalid_checked_scope'

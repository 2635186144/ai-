import copy
import json

import pytest

from backend.evidence.fact_models import FactCommand
from backend.evidence.facts import FactWorksheet
from backend.evidence.parser import EvidenceError
from backend.evidence.support import SupportCommand, check_review
from tests.test_evidence import client, store
from tests.test_facts import command, ready, save


def review_command(sheet, **part_changes):
    unit = next(u for u in sheet['units'] if u['draft'])
    statement = unit['draft']['facts'][0]['statement']
    return {'reading_revision': sheet['reading_revision'], 'revision': sheet['revision'], 'unit_id': unit['id'],
            'fact_index': 0, 'parts': [{'start': 0, 'end': len(statement), 'text': statement, 'kind': 'positive',
                'verdict': 'supported', 'reason': 'Local fixture review only, not expert validation.',
                'citation_indices': [0], 'checked_scope': [], **part_changes}]}


def saved(client):
    _, _, path, sheet = ready(client)
    sheet = save(client, path, command(sheet)).json()
    return path, sheet


def test_support_history_idempotency_and_edit_invalidates(client, store):
    path, sheet = saved(client)
    unit = sheet['units'][0]
    assert unit['support_checks'][0]['status'] == 'not_reviewed'
    payload = review_command(sheet)
    r = save(client, path + '/support-review', payload, 'review')
    assert r.status_code == 200, r.text
    reviewed = r.json()
    check = reviewed['units'][0]['support_checks'][0]
    assert check['status'] == 'recorded_supported' and check['claim_coverage_complete']
    assert not check['semantic_verified'] and not reviewed['coverage']['semantic_verified']
    assert reviewed['units'][0]['draft'] == unit['draft']
    assert reviewed['units'][0]['producer'] == unit['producer']
    assert save(client, path + '/support-review', payload, 'review').json() == reviewed
    assert save(client, path + '/support-review', {**payload, 'actor': 'assistant_curated'}, 'review').json()['code'] == 'idempotency_conflict'
    assert save(client, path + '/support-review', payload, 'stale').json()['code'] == 'stale_fact_revision'
    updated = save(client, path, command(reviewed), 'edit').json()
    assert updated['units'][0]['support_checks'][0]['status'] == 'not_reviewed'
    with store.connection() as db:
        old = json.loads(db.execute('SELECT payload_json FROM fact_versions WHERE ledger_id=? AND revision=?', (sheet['id'], reviewed['revision'])).fetchone()[0])
    assert old['units'][0]['support_reviews']['0']['status'] == 'recorded_supported'
    reloaded = client.get(path, params={'reading_revision': sheet['reading_revision']}).json()
    assert reloaded['revision'] == updated['revision']


@pytest.mark.parametrize('changes,code', [
    ({'text': 'Fabricated claim'}, 'invalid_claim_part'),
    ({'reason': '  '}, 'invalid_claim_part'),
    ({'citation_indices': [1]}, 'invalid_support_reference'),
    ({'citation_indices': [-1]}, 'invalid_support_reference'),
    ({'citation_indices': []}, 'support_evidence_required'),
    ({'kind': 'absence'}, 'absence_scope_incomplete'),
    ({'checked_scope': [{'block_id': 'foreign', 'start': 0, 'end': 1, 'quote': 'x'}]}, 'invalid_checked_scope'),
])
def test_invalid_review_does_not_publish(client, changes, code):
    path, sheet = saved(client)
    r = save(client, path + '/support-review', review_command(sheet, **changes), 'bad')
    assert r.status_code == 422 and r.json()['code'] == code, r.text
    assert client.get(path, params={'reading_revision': sheet['reading_revision']}).json()['revision'] == sheet['revision']


def test_claim_gaps_and_mixed_verdict_never_become_supported(client):
    path, sheet = saved(client)
    payload = review_command(sheet)
    part = payload['parts'][0]
    part['end'] -= 8
    part['text'] = part['text'][:-8]
    sheet = save(client, path + '/support-review', payload, 'gap').json()
    check = sheet['units'][0]['support_checks'][0]
    assert check['status'] == 'incomplete_claim_coverage' and not check['claim_coverage_complete']
    payload = review_command(sheet, verdict='partial')
    sheet = save(client, path + '/support-review', payload, 'partial').json()
    assert sheet['units'][0]['support_checks'][0]['status'] == 'needs_revision'
    assert sheet['coverage']['facts_not_recorded_supported'] == 1


def test_absence_needs_every_source_span_and_remains_local_scope():
    fact = {'statement': 'No parameters in this paragraph.', 'citations': [{'quote': 'A'}]}
    unit = {'source_spans': [{'block_id': 'a', 'start': 0, 'end': 1}, {'block_id': 'b', 'start': 0, 'end': 1}]}
    packet = {'blocks': [{'id': 'a', 'text': 'A'}, {'id': 'b', 'text': 'B'}]}
    c = SupportCommand(reading_revision=1, revision=1, unit_id='unit', fact_index=0,
        parts=[{'start': 0, 'end': len(fact['statement']), 'text': fact['statement'], 'kind': 'absence',
                'verdict': 'supported', 'reason': 'Read entire target paragraph; only here.', 'citation_indices': [0],
                'checked_scope': [{'block_id': 'a', 'start': 0, 'end': 1, 'quote': 'A'}]}])
    with pytest.raises(EvidenceError, match='完整目标段'):
        check_review(fact, unit, packet, c)
    c.parts[0].checked_scope.append(c.parts[0].checked_scope[0].model_copy(update={'block_id': 'b', 'quote': 'B'}))
    result = check_review(fact, unit, packet, c)
    assert result['status'] == 'recorded_supported' and not result['semantic_verified']


def test_overlap_and_model_identity_rejected(client):
    path, sheet = saved(client)
    payload = review_command(sheet)
    payload['parts'].append(copy.deepcopy(payload['parts'][0]))
    assert save(client, path + '/support-review', payload, 'overlap').json()['code'] == 'overlapping_claim_parts'
    payload = review_command(sheet)
    payload['actor'] = 'model'
    assert save(client, path + '/support-review', payload, 'spoof').status_code == 422


def test_review_rejects_obsolete_reading_revision(client):
    path, sheet = saved(client)
    reading = path.split('/sections/')[0]
    plan = client.get(reading).json()
    client.post(reading, headers={'Idempotency-Key': 'change-reading'}, json={
        'revision': plan['revision'], 'action': 'confirm_page', 'page': 1, 'status': 'pending'})
    assert save(client, path + '/support-review', review_command(sheet), 'old-reading').status_code == 409

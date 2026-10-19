import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.evidence import routes
from backend.evidence.parser import EvidenceError, inspect_pdf, parse_document
from backend.evidence.store import EvidenceStore


@pytest.fixture
def store(tmp_path):
    return EvidenceStore(tmp_path / 'evidence')


@pytest.fixture
def client(store):
    app.dependency_overrides[routes.get_store] = lambda: store
    with TestClient(app) as session:
        yield session
    app.dependency_overrides.pop(routes.get_store, None)


def fixture_pdf(rotated=False, image_page=False, altered=False):
    with pymupdf.open() as doc:
        page = doc.new_page(width=612, height=792)
        page.insert_text((45, 65), 'Evidence foundation integration fixture', fontsize=17)
        page.insert_textbox(pymupdf.Rect(45, 95, 560, 180),
            ('Changed content. ' if altered else '') + 'The method uses a controlled dataset. This paragraph supports page and coordinate checks. ' * 3)
        page.insert_text((45, 210), 'Table 1: Evaluation results are a candidate caption, not parsed cells.')
        page.insert_text((45, 260), 'Figure 1: Architecture overview.')
        page.draw_rect(pymupdf.Rect(45, 275, 180, 325), color=(0, 0, 0))
        if rotated:
            page.set_rotation(90)
        page = doc.new_page(width=612, height=792)
        page.insert_text((45, 65), 'Appendix A: Additional details', fontsize=16)
        page.insert_textbox(pymupdf.Rect(45, 95, 560, 180), 'The appendix reports the seed settings and dataset split. ' * 4)
        if image_page:
            image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 50, 50))
            image.clear_with(220)
            page = doc.new_page()
            page.insert_image(pymupdf.Rect(30, 30, 200, 200), pixmap=image)
        return doc.tobytes()


def upload(client, data=None, key='upload-one', role='main'):
    response = client.post('/api/v2/documents', headers={'Idempotency-Key': key},
        files={'file': ('fixture.pdf', data or fixture_pdf(), 'application/pdf')}, data={'material_role': role})
    assert response.status_code == 201, response.text
    return response.json()


def test_upload_parse_locate_preview_and_restart(client, store):
    content = fixture_pdf(rotated=True)
    document = upload(client, content)
    did = document['id']
    assert document['sha256'] == hashlib.sha256(content).hexdigest()
    assert client.get(f'/api/v2/documents/{did}/blocks').status_code == 409
    parsed = client.post(f'/api/v2/documents/{did}/parse', headers={'Idempotency-Key': 'parse-one'})
    assert parsed.status_code == 200, parsed.text
    summary = parsed.json()
    assert summary['pages'][0]['original_rotation'] == 90
    assert summary['pages'][0]['width'] == 612
    assert summary['semantic_reading_performed'] is False
    assert summary['model_calls'] == 0
    blocks = client.get(f'/api/v2/documents/{did}/blocks', params={'page': 2, 'q': 'seed settings'}).json()
    assert blocks['total'] == 1
    block = blocks['items'][0]
    bid = block['id']
    assert block['region']['pdf_page'] == 2
    assert block['text_sha256'] == hashlib.sha256(block['text'].encode()).hexdigest()
    assert block['region']['visual_checked'] is False
    region = client.get(f'/api/v2/documents/{did}/blocks/{bid}/region').json()
    assert region['precision'] == 'text_block'
    preview = client.get(f'/api/v2/documents/{did}/blocks/{bid}/image')
    assert preview.status_code == 200
    pixmap = pymupdf.Pixmap(preview.content)
    assert pixmap.width > 0 and pixmap.height > 0
    page_image = client.get(f'/api/v2/documents/{did}/pages/1/image', params={'scale': 1}).content
    page_pixmap = pymupdf.Pixmap(page_image)
    assert (page_pixmap.width, page_pixmap.height) == (612, 792)
    source = client.get(f'/api/v2/documents/{did}/file')
    assert source.content == content
    materials = client.get(f'/api/v2/documents/{did}/materials').json()
    assert {m['kind'] for m in materials['items']} >= {'figure_caption', 'table_caption', 'appendix_heading'}
    assert materials['table_cells_extracted'] is False
    reopened = EvidenceStore(store.root)
    assert reopened.block(did, bid).text == block['text']
    assert reopened.detail(did).extraction.id == summary['id']


def test_idempotency_deduplication_and_atomic_conflict(client):
    content = fixture_pdf()
    first = upload(client, content)
    assert upload(client, content)['id'] == first['id']
    assert upload(client, content, key='different-key')['id'] == first['id']
    assert client.get('/api/v2/documents').json()['total'] == 1
    conflict = client.post('/api/v2/documents', headers={'Idempotency-Key': 'upload-one'},
        files={'file': ('different.pdf', fixture_pdf(altered=True), 'application/pdf')})
    assert conflict.status_code == 409
    assert conflict.json()['code'] == 'idempotency_conflict'
    assert client.get('/api/v2/documents').json()['total'] == 1
    did = first['id']
    a = client.post(f'/api/v2/documents/{did}/parse', headers={'Idempotency-Key': 'p1'})
    b = client.post(f'/api/v2/documents/{did}/parse', headers={'Idempotency-Key': 'p2'})
    assert a.json()['id'] == b.json()['id']


def test_image_page_is_not_silently_treated_as_read(client):
    document = upload(client, fixture_pdf(image_page=True))
    did = document['id']
    summary = client.post(f'/api/v2/documents/{did}/parse', headers={'Idempotency-Key': 'scan-parse'}).json()
    assert summary['status'] == 'partial'
    assert summary['pages'][2]['text_status'] == 'no_text'
    assert summary['pages'][2]['image_count'] == 1
    material = client.get(f'/api/v2/documents/{did}/materials').json()['items']
    assert any(m['kind'] == 'image' and m['pdf_page'] == 3 for m in material)
    assert client.get(f'/api/v2/documents/{did}/blocks', params={'page': 3}).json()['total'] == 0


def test_cross_version_blocks_and_corrupt_source_are_blocked(client, store):
    first = upload(client)
    second = upload(client, fixture_pdf(altered=True), key='second')
    for index, document in enumerate([first, second]):
        assert client.post(f"/api/v2/documents/{document['id']}/parse", headers={'Idempotency-Key': f'p{index}'}).status_code == 200
    block = client.get(f"/api/v2/documents/{first['id']}/blocks").json()['items'][0]
    assert client.get(f"/api/v2/documents/{second['id']}/blocks/{block['id']}").status_code == 404
    store.file_path(first['id']).write_bytes(b'corrupted fixture')
    response = client.get(f"/api/v2/documents/{first['id']}/file")
    assert response.status_code == 409
    assert response.json()['code'] == 'source_integrity_failed'
    assert client.get(f"/api/v2/documents/{first['id']}/blocks").status_code == 409
    assert client.post(f"/api/v2/documents/{first['id']}/parse", headers={'Idempotency-Key': 'after-corruption'}).status_code == 409


def test_invalid_input_never_registers_a_document(client):
    invalid = client.post('/api/v2/documents', headers={'Idempotency-Key': 'bad'}, files={'file': ('bad.pdf', b'bad', 'application/pdf')})
    assert invalid.status_code == 422
    assert invalid.json()['code'] == 'invalid_pdf'
    missing_key = client.post('/api/v2/documents', files={'file': ('f.pdf', fixture_pdf(), 'application/pdf')})
    assert missing_key.status_code == 422
    assert missing_key.json()['code'] == 'invalid_request'
    assert client.get('/api/v2/documents').json()['total'] == 0
    with pytest.raises(EvidenceError, match='20 MB'):
        inspect_pdf(b'%PDF-' + b' ' * (20 * 1024 * 1024))


def test_concurrent_retries_converge_to_one_version(store):
    content = fixture_pdf()
    def register(_):
        return store.register(content, 'same.pdf', 'main', 'same-request').id
    with ThreadPoolExecutor(max_workers=3) as pool:
        ids = list(pool.map(register, range(3)))
    assert len(set(ids)) == 1
    assert store.list_documents(100, 0)['total'] == 1


def test_stored_text_tampering_and_cropped_coordinates(client, store):
    with pymupdf.open(stream=fixture_pdf(), filetype='pdf') as doc:
        page = doc[0]
        page.set_cropbox(pymupdf.Rect(20, 20, 590, 770))
        content = doc.tobytes()
    document = upload(client, content)
    did = document['id']
    summary = client.post(f'/api/v2/documents/{did}/parse', headers={'Idempotency-Key': 'crop'}).json()
    assert summary['pages'][0]['width'] == 570
    blocks = client.get(f'/api/v2/documents/{did}/blocks').json()['items']
    for block in blocks:
        x0, y0, x1, y1 = block['region']['bbox']
        assert 0 <= x0 < x1 <= block['region']['page_width']
        assert 0 <= y0 < y1 <= block['region']['page_height']
    target = blocks[0]
    target['text'] = 'Injected replacement, not in the source'
    with store.connection() as db:
        db.execute('UPDATE evidence_blocks SET payload_json=? WHERE id=?', (json.dumps(target), target['id']))
        db.commit()
    response = client.get(f"/api/v2/documents/{did}/blocks/{target['id']}")
    assert response.status_code == 409
    assert response.json()['code'] == 'block_integrity_failed'


def test_real_lora_retains_formula_and_appendix_locations():
    path = Path(__file__).resolve().parents[1] / 'knowledge/lora/paper.pdf'
    if not path.exists():
        pytest.skip('LoRA source fixture unavailable')
    result = parse_document(path.read_bytes(), 'doc_lora_fixture')
    assert len(result.pages) == 26
    assert any(b.region.pdf_page == 24 and 'similarity' in b.text.lower() for b in result.blocks)
    assert any(m.kind == 'table_caption' and m.pdf_page == 8 for m in result.materials)
    assert any(m.kind == 'appendix_heading' and m.pdf_page >= 16 for m in result.materials)
    assert result.model_calls == 0


def test_failed_parse_transaction_does_not_publish_partial_records(store, monkeypatch):
    from backend.evidence import store as store_module
    import sqlite3
    content = fixture_pdf()
    document = store.register(content, 'atomic.pdf', 'main', 'register-atomic')
    original = store_module.parse_document
    def duplicate_material(*args):
        result = original(*args)
        result.materials.append(result.materials[0])
        return result
    monkeypatch.setattr(store_module, 'parse_document', duplicate_material)
    with pytest.raises(sqlite3.IntegrityError):
        store.parse(document.id, 'atomic-parse')
    assert store.detail(document.id).extraction is None
    with store.connection() as db:
        assert db.execute('SELECT count(*) FROM evidence_blocks').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM evidence_pages').fetchone()[0] == 0
    monkeypatch.setattr(store_module, 'parse_document', original)
    assert store.parse(document.id, 'atomic-parse').block_count > 0


def test_unrelated_database_is_not_overwritten(tmp_path):
    import sqlite3
    root = tmp_path / 'existing'
    root.mkdir()
    with sqlite3.connect(root / 'evidence.sqlite3') as db:
        db.execute('CREATE TABLE existing_user_data (value TEXT)')
        db.execute("INSERT INTO existing_user_data VALUES ('preserve me')")
    with pytest.raises(RuntimeError, match='unrelated'):
        EvidenceStore(root)
    with sqlite3.connect(root / 'evidence.sqlite3') as db:
        assert db.execute('SELECT value FROM existing_user_data').fetchone()[0] == 'preserve me'

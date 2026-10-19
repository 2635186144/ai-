import json
import mimetypes

import pymupdf
import pytest
from fastapi.testclient import TestClient

from backend import app as module


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'DATA', tmp_path)
    monkeypatch.delenv('MODEL_API_KEY', raising=False)
    monkeypatch.delenv('MODEL_NAME', raising=False)
    return TestClient(module.app)


def pdf_bytes():
    with pymupdf.open() as doc:
        for text in [
            'Introduction. We study evidence grounded research assistants. The method uses retrieval augmented generation. ' * 3,
            'Experiments. We evaluate on SyntheticQA dataset. Recall at ten is 78 percent. Limitation: only English documents. ' * 3,
        ]:
            page = doc.new_page()
            page.insert_textbox(pymupdf.Rect(40, 40, 550, 750), text)
        return doc.tobytes()


def upload(client):
    r = client.post('/api/papers', files={'files': ('study.pdf', pdf_bytes(), 'application/pdf')})
    assert r.status_code == 200, r.text
    return r.json()[0]


def test_upload_retrieve_source_and_delete(client):
    paper = upload(client)
    assert paper['page_count'] == 2
    assert 'pages' not in paper
    assert len(client.get('/api/papers').json()) == 1
    r = client.post('/api/ask', json={'paper_ids': [paper['id']], 'question': 'SyntheticQA dataset'})
    assert r.status_code == 200
    assert r.json()['mode'] == 'retrieval'
    assert r.json()['evidence'][0]['page'] == 2
    source = client.get(f"/api/papers/{paper['id']}/pdf")
    assert source.content.startswith(b'%PDF')
    assert client.delete(f"/api/papers/{paper['id']}").status_code == 200
    assert client.get(f"/api/papers/{paper['id']}/pdf").status_code == 404


def test_module_assets_have_browser_compatible_mime(client):
    assert mimetypes.guess_type('app.js')[0] == 'application/javascript'
    if (module.ROOT / 'dist').exists():
        asset = next((module.ROOT / 'dist' / 'assets').glob('*.js'))
        response = client.get('/assets/' + asset.name)
        assert response.status_code == 200
        assert response.headers['content-type'].startswith('application/javascript')


def test_reject_invalid_and_scanned_pdf(client):
    r = client.post('/api/papers', files={'files': ('bad.pdf', b'not a pdf', 'application/pdf')})
    assert r.status_code == 400
    with pymupdf.open() as doc:
        doc.new_page()
        blank = doc.tobytes()
    assert client.post('/api/papers', files={'files': ('scan.pdf', blank, 'application/pdf')}).status_code == 400
    assert client.get('/api/papers').json() == []


def test_batch_validation_is_atomic(client):
    r = client.post('/api/papers', files=[('files', ('good.pdf', pdf_bytes(), 'application/pdf')), ('files', ('bad.pdf', b'bad', 'application/pdf'))])
    assert r.status_code == 400
    assert client.get('/api/papers').json() == []


def test_missing_model_and_no_evidence(client):
    paper = upload(client)
    assert client.get('/api/status').json()['model_configured'] is False
    assert client.post(f"/api/papers/{paper['id']}/analyze").status_code == 409
    r = client.post('/api/ask', json={'paper_ids': [paper['id']], 'question': 'zzzzzznonexistent'})
    assert r.json()['evidence'] == []
    assert client.post('/api/ask', json={'paper_ids': [], 'question': 'dataset'}).status_code == 422


def test_extraction_discards_fabricated_quote_and_page(client, monkeypatch):
    paper = upload(client)

    async def fake_model(*args):
        return {
            '数据集': {'text': 'SyntheticQA', 'page': 2, 'quote': 'We evaluate on SyntheticQA dataset.'},
            '实验结果': {'text': '99%', 'page': 2, 'quote': 'This fabricated sentence does not occur in the original paper.'},
            '方法': {'text': 'retrieval', 'page': 999, 'quote': 'The method uses retrieval augmented generation.'},
        }

    monkeypatch.setattr(module, 'model_json', fake_model)
    r = client.post(f"/api/papers/{paper['id']}/analyze")
    assert r.status_code == 200
    assert list(r.json()['analysis']) == ['数据集']
    assert client.get('/api/papers').json()[0]['analysis']['数据集']['evidence']['page'] == 2


def test_model_answer_without_valid_citation_falls_back(client, monkeypatch):
    paper = upload(client)
    monkeypatch.setattr(module, 'configured', lambda: True)

    async def fake_model(*args):
        return {'answer': 'unsupported assertion', 'citations': [999, '1', True]}

    monkeypatch.setattr(module, 'model_json', fake_model)
    r = client.post('/api/ask', json={'paper_ids': [paper['id']], 'question': 'dataset'})
    assert r.json()['mode'] == 'retrieval'
    assert 'unsupported assertion' not in r.json()['answer']


def test_model_answer_preserves_original_reference_numbers(client, monkeypatch):
    paper = upload(client)
    monkeypatch.setattr(module, 'configured', lambda: True)

    async def fake_model(*args):
        return {'answer': 'Supported answer [2]', 'citations': [2, 2]}

    monkeypatch.setattr(module, 'model_json', fake_model)
    r = client.post('/api/ask', json={'paper_ids': [paper['id']], 'question': 'research dataset'})
    assert r.json()['mode'] == 'model'
    assert len(r.json()['evidence']) == 1
    assert r.json()['evidence'][0]['index'] == 2

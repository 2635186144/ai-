"""Synthetic layouts test title behavior; not a research accuracy benchmark."""
import asyncio
import copy
import json

from backend.evidence.titles import identify_title, key
from tests.test_paper_reports import store, client, setup_run, ctl, token, mock_models


def block(id, text, size=18, y=90, page=1, x=50, bottom=None):
    return {'id': id, 'text': text, 'max_font_size': size,
            'region': {'pdf_page': page, 'page_height': 800,
                       'bbox': [x, y, x+450, bottom or y+size+2]}}


def body(page=1):
    return block('body'+str(page), 'Synthetic source paragraph. ' * 10, size=10, y=400, page=page)


def test_recovers_title_despite_extraction_order_and_larger_journal_header():
    title = '开源项目中的子社区发现与评价：以Ａｐａｃｈｅ　ＩｏＴＤＢ为例'
    blocks = [body(), block('journal', 'CHINESE JOURNAL OF COMPUTERS', size=30, y=0),
              block('url', 'http://www.example.org', y=45), block('title', title, size=15),
              block('authors', '张三，李四', size=11, y=125)]
    original = copy.deepcopy(blocks)
    result = identify_title(blocks, 'random.pdf', 'Microsoft Word - document')
    assert key(result['text']) == key(title)
    assert result['status'] == 'source_located'
    assert result['sources'][0]['block_id'] == 'title'
    assert result['sources'][0]['quote'] == title
    assert blocks == original


def test_multiline_title_without_filename_hint_and_with_english_hyphenation():
    blocks = [block('a', 'Efficient Adaptation of Large Lan-', bottom=110),
              block('b', 'guage Models for Research', y=112), body()]
    result = identify_title(blocks, 'paper.pdf')
    assert result['text'] == 'Efficient Adaptation of Large Language Models for Research'
    assert [s['block_id'] for s in result['sources']] == ['a', 'b']


def test_labeled_cover_title_continues_but_does_not_include_authors():
    blocks = [block('journal', '《某期刊》网络首发论文', size=20, y=150),
              block('t1', '题目：\n人工智能开源社区技术影响力的涌现路径研究——基于动态数字创新生态系', size=10, y=245, bottom=260),
              block('t2', '统框架', size=10, y=261),
              block('authors', '作者：张三，李四', size=10, y=280), body()]
    result = identify_title(blocks, 'paper.pdf')
    assert result['method'] == 'labeled_title'
    assert result['text'].endswith('生态系统框架')
    assert [s['block_id'] for s in result['sources']] == ['t1', 't2']


def test_side_by_side_mixed_size_title_keeps_technical_name():
    blocks = [block('name', 'ＯｐｅｎＰｅｒ\n\ue5e5ｆ', size=13, y=64, x=70, bottom=80),
              block('subtitle', '：面向开源生态可持续发展的\n\ue5d2\n数据科学基准测试体系', size=18, y=62, x=147, bottom=106), body()]
    result = identify_title(blocks, 'OpenPerf_面向开源生态可持续发展的数据科学基准测试体系_张三.pdf')
    assert result['text'] == 'OpenPerf:面向开源生态可持续发展的数据科学基准测试体系'
    assert len(result['sources']) == 2


def test_fragmented_body_does_not_overweight_one_larger_english_abstract():
    blocks = [block('name', 'ＯｐｅｎＰｅｒ\n\ue5e5ｆ', size=13, y=64, x=70, bottom=80),
              block('subtitle', '：面向开源生态可持续发展的\n数据科学基准测试体系', size=18, y=62, x=147, bottom=106),
              block('english', 'Abstract: ' + 'Synthetic English source. '*6, size=11.3, y=600)]
    blocks += [block('fragment'+str(i), '模拟数据：这是拆分为小块的正文，用于字体中位数测试。', size=7.5, y=240+i*12) for i in range(20)]
    result = identify_title(blocks, 'OpenPerf_面向开源生态可持续发展的数据科学基准测试体系_张三.pdf')
    assert result['text'].startswith('OpenPerf:')
    assert len(result['sources']) == 2


def test_filename_and_unrelated_metadata_cannot_become_source_evidence():
    result = identify_title([body()], '研究主题_李四.pdf', 'An Unrelated Academic Title')
    assert result['status'] == 'filename_only'
    assert result['text'] == '研究主题'
    assert result['sources'] == []
    assert '尚未' in result['note']
    # No text / scanned cover with a generic filename must stay unresolved.
    assert identify_title([], 'paper.pdf', 'Generated PDF')['status'] == 'unresolved'


def test_matching_filename_does_not_promote_body_paragraph_to_title():
    text = 'A paragraph about scientific methods and reproducibility'
    blocks = [block('paragraph', text, size=10, y=200), body()]
    result = identify_title(blocks, text+'.pdf', text)
    assert result['status'] == 'filename_only'
    assert result['sources'] == []


def test_front_matter_on_second_page_has_its_own_locator():
    blocks = [block('cover', 'DOI: 10.0000/example', size=20),
              block('title', 'Structured Reading of Scientific Articles', page=2), body(2)]
    result = identify_title(blocks, 'paper.pdf')
    assert result['text'] == 'Structured Reading of Scientific Articles'
    assert result['sources'][0]['pdf_page'] == 2


def test_distinct_equally_plausible_titles_require_confirmation():
    blocks = [block('a', 'Alpha Methods for Source Reading', x=40),
              block('b', 'Different Concepts for Data Maps', x=40, y=90), body()]
    result = identify_title(blocks, 'paper.pdf')
    assert result['status'] == 'unresolved'
    assert result['sources'] == []


def test_historical_report_title_is_read_only_and_local_locator_is_independent(client, store, monkeypatch):
    service, did, run = setup_run(client, store)
    calls = []
    mock_models(monkeypatch, calls)
    current = ctl(service, did, run, 'start')
    asyncio.run(service.work(did, run['id'], token(service, did, current)))
    with store.connection() as db:
        raw = json.loads(db.execute('select payload_json from paper_report_runs where id=?', (run['id'],)).fetchone()[0])
        raw['report']['title']['text'] = '未能从所提供材料中识别原文题名'
        payload = json.dumps(raw, ensure_ascii=False)
        db.execute('update paper_report_runs set payload_json=? where id=?', (payload, run['id']))
        db.commit()
    response = client.get(f'/api/v2/documents/{did}/reports/{run["id"]}').json()
    assert response['report']['title']['text'] == raw['report']['title']['text']
    identity = response['title_identity']
    assert identity['text'] == 'Evidence foundation integration fixture'
    assert identity['status'] == 'source_located'
    for s in identity['sources']:
        source = store.block(did, s['block_id'])
        assert source.text[s['start']:s['end']] == s['quote']
    assert client.get(f'/api/v2/documents/{did}/report-plan').json()['title_identity'] == identity
    with store.connection() as db:
        assert db.execute('select payload_json from paper_report_runs where id=?', (run['id'],)).fetchone()[0] == payload

"""Mechanical/source regressions; synthetic text is not a quality evaluation."""
import copy
import pytest
from backend.evidence.report_structure import build_outline
from backend.evidence.paper_reports import validate_report, PaperReports
from backend.evidence.parser import EvidenceError
from tests.test_paper_reports import report_for
from tests.test_evidence import store, client, upload


def block(text,page=1,bid='b'):
    return {'id':bid,'text':text,'region':{'pdf_page':page,'page_height':800,'bbox':[30,100,500,650]}}


@pytest.mark.parametrize('text,titles,levels',[
 ('1 Introduction\nBackground.\n2 Method\n2.1 Training\nMechanism.', ['1 Introduction','2 Method','2.1 Training'],[1,1,2]),
 ('一、研究问题\n正文。\n（一）责任边界\n正文。\n二、论证依据\n正文。', ['一、研究问题','（一）责任边界','二、论证依据'],[1,2,1]),
 ('摘要\n正文。\n1 分类框架\n正文。\n2 研究分歧\n正文。\n参考文献', ['摘要','1 分类框架','2 研究分歧','参考文献'],[1,1,1,1]),
])
def test_different_paper_heading_styles_preserve_exact_anchors(text,titles,levels):
    result=build_outline('e',[block(text)])
    assert [s['title'] for s in result['sections']]==titles
    assert [s['level'] for s in result['sections']]==levels
    assert not result['semantic_verified'] and result['status']=='automatic_unconfirmed'
    for s in result['sections']:
        a=s['anchor'];assert text[a['start']:a['end']]==a['quote']==s['title']
    assert result['sections'][0]['end_position']==result['sections'][1]['start_position']


def test_footers_years_numeric_results_and_contents_are_not_chapter_starts():
    blocks=[block('2026 年\n3 倍\n1 Intro ...... 2\nNormal prose.'),block('No recognised heading',2,'b2')]
    for i in range(2):
        blocks.append({'id':f'footer{i}','text':f'{60+i} 政法论坛 2026 年','region':{'pdf_page':i+1,'page_height':800,'bbox':[0,760,500,790]}})
    assert build_outline('e',blocks)['sections']==[]


def test_multiline_headings_preserve_number_and_reject_tables_code_and_citations():
    blocks=[block('4\nOUR METHOD',bid='one'),block('4.1\nLOW-RANK UPDATE',bid='two'),block('A\nAPPENDIX DETAILS',bid='three')]
    result=build_outline('e',blocks)
    assert [s['title'] for s in result['sections']]==['4\nOUR METHOD','4.1\nLOW-RANK UPDATE','A\nAPPENDIX DETAILS']
    for s in result['sections']:
        a=s['anchor'];text=next(b['text'] for b in blocks if b['id']==a['block_id']);assert text[a['start']:a['end']]==s['title']
    false='5. FOR tool IN tools\n6. IF tool.name response THEN\n9. 根据输出进行筛选\n0.4 M\n2101.00121. arXiv: 2101.00121.\nr. We test two adapter designs.\n175 billion parameters.'
    assert build_outline('e',[block(false)])['sections']==[]
    assert build_outline('e',[{**block('32\nBatch Size'),'max_font_size':6}])['sections']==[]


def test_spaced_chinese_labels_and_glyph_lines_keep_original_text():
    blocks=[block('4\n结\n语',bid='one'),block('参考文献:',bid='two'),block('[ 参　考　文　献]',bid='three'),block('摘  要',bid='four')]
    result=build_outline('e',blocks)
    assert [s['title'] for s in result['sections']]==[b['text'] for b in blocks]
    for section,b in zip(result['sections'],blocks):
        a=section['anchor'];assert b['text'][a['start']:a['end']]==a['quote']
    assert build_outline('e',[block('[1] 参考条目不是标题')])['sections']==[]


def notes_and_report():
    notes=[{'unknowns':[],'evidence':[{'id':'e1','block_id':'b','start':0,'end':10,'pdf_page':1,'quote':'1 Method'}]}]
    return notes,report_for(notes)


def test_mapping_requires_existing_section_and_citation_location():
    notes,raw=notes_and_report();outline=build_outline('e',[block('1 Method\nMechanism.\n2 Results\nResults.')])
    raw['argument_map'][0]['source_section_ids']=[outline['sections'][0]['id']]
    assert validate_report(raw,notes,outline=outline)['argument_map'][0]['source_section_ids']
    for ids in [['invented'],[outline['sections'][1]['id']],[outline['sections'][0]['id']]*2]:
        invalid=copy.deepcopy(raw);invalid['argument_map'][0]['source_section_ids']=ids
        with pytest.raises(EvidenceError) as failure:validate_report(invalid,notes,outline=outline)
        assert failure.value.code=='report_section_mapping_invalid'


def test_child_quote_can_locate_parent_but_not_a_sibling_or_later_chapter():
    text='2 Method\n2.1 Data\nObserved sample.\n2.2 Analysis\nOther data.\n3 Results\nFindings.'
    outline=build_outline('e',[block(text)])
    notes,raw=notes_and_report();e=notes[0]['evidence'][0];e.update(start=text.index('Observed'),end=text.index('Observed')+16,quote='Observed sample.')
    ids=[s['id'] for s in outline['sections']]
    raw['argument_map'][0]['source_section_ids']=ids[:2]
    assert validate_report(raw,notes,outline=outline)['argument_map'][0]['source_section_ids']==ids[:2]
    for bad in ids[2:]:
        raw['argument_map'][0]['source_section_ids']=[bad]
        with pytest.raises(EvidenceError):validate_report(raw,notes,outline=outline)


def test_legacy_comparison_unknown_is_not_invented_and_new_fields_check_citations():
    notes,raw=notes_and_report();report=validate_report(raw,notes)
    assert report['innovations'][0]['baseline'] is None
    assert report['argument_map'][0]['source_section_ids']==[]
    raw['innovations'][0]['baseline']={'text':'模拟数据：原有方法','evidence_ids':['e1']}
    report=validate_report(raw,notes);assert not report['semantic_verified']
    raw['innovations'][0]['support']={'text':'模拟数据：伪造支持','evidence_ids':['absent']}
    with pytest.raises(EvidenceError):validate_report(raw,notes)


def test_report_plan_structure_is_read_only_without_manual_preparation(client,store):
    did=upload(client)['id'];store.parse(did,'structure-test')
    with store.connection() as db:before=db.execute('select count(*) from reading_versions').fetchone()[0]
    response=client.get(f'/api/v2/documents/{did}/report-plan');assert response.status_code==200
    outline=response.json()['source_outline'];assert outline['sections']
    for section in outline['sections']:
        a=section['anchor'];assert store.block(did,a['block_id']).text[a['start']:a['end']]==section['title']
    with store.connection() as db:assert db.execute('select count(*) from reading_versions').fetchone()[0]==before

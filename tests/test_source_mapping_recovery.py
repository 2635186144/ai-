"""Local recovery changes chapter links only; prose and evidence stay immutable."""
import copy
import pytest
from backend.evidence.paper_reports import repair_source_mapping,validate_report
from backend.evidence.parser import EvidenceError


def fixture():
    outline={'block_ids':['b1','b2'],'sections':[
        {'id':'s1','title':'方法','level':1,'start_position':[0,0],'end_position':[1,0]},
        {'id':'s2','title':'结果','level':1,'start_position':[1,0],'end_position':[2,0]}]}
    notes=[{'evidence':[{'id':'e1','block_id':'b1','start':3,'end':8,'pdf_page':1,'quote':'模拟方法原文'}]}]
    insight={'text':'模拟数据：方法解释。','evidence_ids':['e1']}
    raw={'title':insight,'research_direction':insight,'one_sentence':insight,'abstract_explanation':insight,
         'argument_map':[{'heading':'模拟方法','role':'说明方法','content':insight,'connects_to':'接着检验结果','source_section_ids':['s1','s2']}]}
    return raw,notes,outline


def test_explicit_projection_retains_prose_quotes_and_original_candidate():
    raw,notes,outline=fixture();original=copy.deepcopy(raw)
    with pytest.raises(EvidenceError):validate_report(raw,notes,'overview',outline)
    output,changes=repair_source_mapping(raw,notes,'overview',outline)
    assert raw==original
    assert output['argument_map'][0]['content']==raw['argument_map'][0]['content']
    assert output['argument_map'][0]['source_section_ids']==['s1']
    assert changes[0]['removed_section_ids']==['s2']
    assert changes[0]['semantic_verified'] is False


@pytest.mark.parametrize('field,value',[('source_section_ids',['invented']),('source_section_ids',['s1','s1'])])
def test_fabricated_or_duplicate_sections_are_not_repaired(field,value):
    raw,notes,outline=fixture();raw['argument_map'][0][field]=value
    with pytest.raises(EvidenceError):repair_source_mapping(raw,notes,'overview',outline)


def test_unknown_evidence_still_blocks_projection():
    raw,notes,outline=fixture();raw['argument_map'][0]['content']['evidence_ids']=['invented']
    with pytest.raises(EvidenceError):repair_source_mapping(raw,notes,'overview',outline)

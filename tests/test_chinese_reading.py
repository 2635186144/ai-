import pytest

from backend.evidence.facts import FactWorksheet, paragraph_units
from backend.evidence.fact_models import ParagraphDraft
from backend.evidence.reading import ReadingPreparation
from backend.evidence.models import PageInfo
from tests.test_facts import draft_for


def block(bid,text,y,x0=56,font=10.06,page=5,x1=545):
    return {'id':bid,'text':text,'text_sha256':bid,'max_font_size':font,
            'region':{'pdf_page':page,'bbox':[x0,y,x1,y+13],'page_width':595}}


def test_chinese_lines_group_with_individual_source_spans_and_citations():
    blocks=[block('heading','2 基于智能体与模型微调的文档生成',430,font=11.69),
            block('a','本节提出基于智能体与模型微调的格式化文档生成方法。核心思想是首先基于智能体实现新闻自动化获取与数据',454,x0=77),
            block('b','处理，赋予智能体意图识别和工具选择的能力，并实现相应的工具；然后面向事故简报生成对大模型进行微调，',472),
            block('c','自主构建数据集并进行模型微调；最后设计格式化文档生成算法。',490,x1=350),
            block('next','2.1 基于智能体与模型微调的文档生成框架设计',508,font=10.216)]
    packet={'blocks':blocks};units=paragraph_units(packet)
    assert len(units)==3 and len(units[1]['source_spans'])==3
    assert [s['block_id'] for s in units[1]['source_spans']]==['a','b','c']
    assert units[1]['text']=='\n'.join(b['text'] for b in blocks[1:4])
    assert sorted(s['block_id'] for u in units for s in u['source_spans'])==sorted(b['id'] for b in blocks)
    d=draft_for({'block_id':'c','start':0,'end':len(blocks[3]['text']),'text':blocks[3]['text']})
    FactWorksheet.validate_draft(ParagraphDraft.model_validate(d),units[1],packet)


@pytest.mark.parametrize('boundary',['indent','page','column','gap','font','punctuation'])
def test_chinese_grouping_stops_at_layout_or_sentence_boundary(boundary):
    a=block('a','这是一个尚未读完的中文段落，后面还有内容',100)
    b=block('b','这是另外一个需要保留边界的中文文本单元。',118)
    if boundary=='indent':b['region']['bbox'][0]=78
    elif boundary=='page':b['region']['pdf_page']=6
    elif boundary=='column':b['region']['bbox']=[320,20,540,33]
    elif boundary=='gap':b['region']['bbox'][1:4:2]=[150,163]
    elif boundary=='font':b['max_font_size']=11.7
    else:a['text']+='。'
    assert len(paragraph_units({'blocks':[a,b]}))==2


def test_chinese_heading_candidates_reject_numeric_result_sentences():
    blocks=[block('front','刊物题录',10),block('main','2 基于智能体与模型微调的文档生成',50,font=11.69),
            block('sub','2.1 基于智能体与模型微调的文档生成框架设计',80,font=10.216),
            block('third','2.2.1 构建智能体大脑',110,font=10.196),
            block('false','3 倍。Benjamin 等进一步探索多个模型。',140),
            block('false2','2.6 平稳降至1.8，训练损失有所下降。',160)]
    class Summary:
        pages=[PageInfo(pdf_page=5,pdf_page_label=None,width=595,height=842,original_rotation=0,
                        text_characters=200,block_count=6,image_count=0,text_status='text_available',warnings=[])]
    proposal=ReadingPreparation.propose('eid',blocks,Summary(),[])
    assert [s['start_block_id'] for s in proposal['sections']]==['front','main','sub','third']
    assert [s['level'] for s in proposal['sections']]==[1,1,2,3]

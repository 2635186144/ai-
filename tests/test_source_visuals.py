"""Synthetic source-layout checks, not a paper-understanding evaluation."""
import pymupdf
import pytest
from backend.evidence.visuals import _index,source_visuals,render_visual,captions
from backend.evidence.parser import EvidenceError
from tests.test_evidence import client,store


def source_pdf(kind='panels',rotated=False):
    with pymupdf.open() as doc:
        page=doc.new_page(width=600,height=800)
        if kind=='panels':
            for y in [120,300]:
                for x in [70,320]:
                    page.draw_rect(pymupdf.Rect(x,y,x+160,y+100))
                    page.insert_text((x+10,y+40),'Synthetic panel')
            page.insert_text((160,450),'Figure 1: Synthetic overview flowchart',fontsize=11)
            page.insert_text((50,600),'Figure 1 shows simulated results in the following paragraph.',fontsize=10)
        elif kind=='table':
            page.insert_text((150,110),'Table 1: Synthetic measurements',fontsize=11)
            for y,weight in [(140,.8),(165,.3),(260,.8)]:
                for x in [60,180,300,420]:page.draw_line((x,y),(x+120,y),width=weight)
            page.insert_text((75,155),'A              B              C              D')
            page.insert_text((75,200),'1              2              3              4')
        else:page.insert_text((130,450),'Figure 1: Image not localized')
        if rotated:page.set_rotation(90)
        return doc.tobytes()


def register(store,kind='panels',rotated=False):
    return store.register(source_pdf(kind,rotated),'synthetic-'+kind+'.pdf','main','source-visual-'+kind+str(rotated))


def test_stacked_panels_and_rotation_keep_upper_row_and_caption(store):
    doc=register(store,rotated=True);result=source_visuals(store,doc.id)
    assert len(result['items'])==1  # prose mentioning Figure 1 is not another caption
    item=result['items'][0]
    assert item['crop_method']=='graphics_band' and item['bbox'][1]<120 and item['bbox'][3]>450
    assert item['is_overview_candidate'] and result['model_calls']==0
    assert not item['visual_understanding_performed']
    image=pymupdf.Pixmap(render_visual(store,doc.id,item['id']))
    assert image.width>image.height  # unrotated 480 x 350 area, despite PDF rotation


def test_segmented_three_line_table_preserves_full_body(store):
    doc=register(store,'table');item=source_visuals(store,doc.id)['items'][0]
    assert item['kind']=='table' and item['crop_method']=='table_rules'
    assert item['bbox'][0]<60 and item['bbox'][2]>540
    assert item['bbox'][1]<110 and item['bbox'][3]>260


def test_filled_table_rules_with_null_stroke_width_are_rendered(store):
    with pymupdf.open() as pdf:
        page=pdf.new_page(width=600,height=800)
        page.insert_text((150,110),'Table 1: Synthetic filled rules')
        for y,thickness in ((140,0.8),(165,0.3),(260,0.8)):
            page.draw_rect(pymupdf.Rect(60,y,540,y+thickness),color=None,fill=(0,0,0))
        page.insert_text((75,200),'Synthetic data: 1 2 3 4')
        assert any(d.get('width') is None for d in page.get_drawings())
        content=pdf.tobytes()
    document=store.register(content,'synthetic-filled-rules.pdf','main','filled-rule-regression')
    data=source_visuals(store,document.id)
    assert len(data['items'])==1
    item=data['items'][0]
    assert item['kind']=='table' and item['crop_method']=='table_rules'
    assert item['bbox'][3]>260 and data['model_calls']==0
    assert render_visual(store,document.id,item['id']).startswith(b'\x89PNG')


def test_chinese_caption_title_and_panel_prose_are_distinguished():
    with pymupdf.open() as pdf:
        page=pdf.new_page()
        page.insert_text((60,100),'图4 中国政策演化',fontname='china-s')
        page.insert_text((60,150),'图3（a）呈现了模拟数据',fontname='china-s')
        page.insert_text((60,200),'图1中的模拟数据如下',fontname='china-s')
        page.insert_text((60,250),'图4（a）囊括的模拟结果如下',fontname='china-s')
        found=captions(page)
        assert len(found)==1 and found[0]['number']=='4'


def test_one_page_failure_keeps_other_pages_and_original_page_entry(store,monkeypatch):
    from backend.evidence import visuals
    with pymupdf.open() as pdf:
        for _ in range(2):
            page=pdf.new_page();page.insert_text((60,100),'Table 1: Synthetic table')
        content=pdf.tobytes()
    doc=store.register(content,'synthetic-layout-failure.pdf','main','page-isolation')
    original=visuals.page_items
    def broken_page(page,number,document_id):
        if number==1:raise TypeError('synthetic malformed layout')
        return original(page,number,document_id)
    monkeypatch.setattr(visuals,'page_items',broken_page)
    result=source_visuals(store,doc.id)
    assert result['review_pages']==[1]
    assert [(i['pdf_page'],i['kind']) for i in result['items']]==[(1,'image'),(2,'table')]
    assert '待核对' in result['items'][0]['caption']
    assert render_visual(store,doc.id,result['items'][0]['id']).startswith(b'\x89PNG')


def test_uncaptioned_embedded_image_is_visible_without_invented_figure_label(store):
    with pymupdf.open() as pdf:
        page=pdf.new_page(width=600,height=800)
        pix=pymupdf.Pixmap(pymupdf.csRGB,pymupdf.IRect(0,0,160,100),False);pix.clear_with(180)
        page.insert_image(pymupdf.Rect(60,150,540,450),pixmap=pix)
        # A tiny logo should not become an image candidate.
        page.insert_image(pymupdf.Rect(20,20,50,40),pixmap=pix)
        content=pdf.tobytes()
    doc=store.register(content,'synthetic-uncaptioned-image.pdf','main','uncaptioned-image')
    result=source_visuals(store,doc.id)
    assert len(result['items'])==1 and result['items'][0]['kind']=='image'
    assert result['items'][0]['label']=='原图待核对'
    assert '未识别图题' in result['items'][0]['caption'] and result['model_calls']==0
    assert render_visual(store,doc.id,result['items'][0]['id']).startswith(b'\x89PNG')


def test_uncertain_boundary_falls_back_to_page_and_ids_are_document_scoped(store):
    first=register(store,'fallback');other=register(store,'table')
    item=source_visuals(store,first.id)['items'][0]
    assert item['crop_method']=='page_fallback' and item['bbox']==[0,0,600,800]
    with pytest.raises(EvidenceError) as exc:render_visual(store,other.id,item['id'])
    assert exc.value.code=='visual_not_found'


def test_visual_http_index_and_image_work_without_parse_or_model(client,store):
    doc=register(store,'table')
    response=client.get(f'/api/v2/documents/{doc.id}/visuals')
    assert response.status_code==200,response.text
    data=response.json();item=data['items'][0]
    assert data['model_calls']==0 and 'objects' not in response.text
    image=client.get(f'/api/v2/documents/{doc.id}/visuals/{item["id"]}/image')
    assert image.status_code==200 and image.content.startswith(b'\x89PNG')
    assert client.get(f'/api/v2/documents/{doc.id}/visuals/{item["id"]}/image?scale=50').status_code==422
    assert client.get(f'/api/v2/documents/{doc.id}/visuals/nonexistent/image').status_code==404

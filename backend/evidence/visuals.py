"""Local source figures/tables, separate from immutable text extraction and reports."""
import re
from functools import lru_cache

import pymupdf

from backend.evidence.parser import EvidenceError,digest

VERSION='source-visuals-v3-page-isolation'
CAPTION=re.compile(r'^(图|表|Figure|Fig\.?|Table|Tab\.?)\s*(\d+)(?!\d)\s*[.:：、]?\s*(.*)$',re.I)


def captions(page):
    found=[]
    for block in page.get_text('dict',flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)['blocks']:
        for line in block.get('lines',[]):
            text=''.join(s['text'] for s in line['spans']).strip()
            match=CAPTION.match(text)
            if not match or len(text)>160:continue
            # "图4 中国…" is a caption; the single character 中 must not
            # reject every title beginning with 中国. Panel prose is not a caption.
            if re.match(r'^(?:所示|中(?:的|[，,。；;：:]|显示|可见|可以)|为|可见|所列|显示|可以|shows\b|illustrates\b|is\b|depicts\b|can\b|presents\b)',match[3],re.I):continue
            if re.match(r'^[（(][a-z0-9]+[）)]\s*(?:呈现|显示|表明|说明|展示|shows\b)',match[3],re.I):continue
            kind='table' if match[1].lower().startswith(('表','tab')) else 'figure'
            box=pymupdf.Rect(line['bbox']) & page.rect
            if box.is_empty:continue
            alias=next((c for c in found if c['kind']==kind and c['number']==match[2] and abs(c['bbox'].y1-box.y0)<25),None)
            if alias:
                alias['bbox']|=box
                alias['caption']+='\n'+text
            elif not any(c['kind']==kind and c['number']==match[2] for c in found):
                # Later prose such as 图3（a）囊括… is not a second figure.
                found.append({'kind':kind,'number':match[2],'caption':text,'bbox':box})
    return sorted(found,key=lambda c:c['bbox'].y0)


def horizontal_rules(drawings,page):
    groups={}
    for d in drawings:
        rect=d['rect']
        if rect.height>1.5 or rect.width<8:continue
        key=round(rect.y0)
        group=groups.setdefault(key,{'x0':rect.x0,'x1':rect.x1,'y':rect.y0,'weight':0})
        group['x0']=min(group['x0'],rect.x0);group['x1']=max(group['x1'],rect.x1)
        # Filled rectangles have width=None in PyMuPDF; they are valid rules.
        # One such line must not abort the entire paper's visual index.
        weight=d.get('width')
        group['weight']=max(group['weight'],rect.height if weight is None else weight)
    return sorted((r for r in groups.values() if r['x1']-r['x0']>page.rect.width*.25),key=lambda r:r['y'])


def region_for(page,caption,all_captions,drawings,graphics,rules):
    box=caption['bbox'];height=page.rect.height
    if caption['kind']=='table':
        limit=min((c['bbox'].y0 for c in all_captions if c['bbox'].y0>box.y1+1),default=height*.94)
        candidates=[r for r in rules if box.y1<=r['y']<limit and r['y']-box.y1<height*.7]
        if candidates and candidates[0]['y']-box.y1<55:
            top=candidates[0]
            bottom=next((r for r in candidates[1:] if r['y']-top['y']>20 and r['weight']>=top['weight']*.85
                         and abs(r['x0']-top['x0'])<6 and abs(r['x1']-top['x1'])<6),None)
            if bottom:
                region=pymupdf.Rect(min(box.x0,top['x0'])-6,box.y0-5,max(box.x1,top['x1'])+6,bottom['y']+5)
                # Preserve adjacent table notes; never infer or rebuild cell values.
                for b in page.get_text('blocks'):
                    if bottom['y']<=b[1]<bottom['y']+16 and re.match(r'^(?:注[:：]|Note\b)',b[4].strip(),re.I):region|=pymupdf.Rect(b[:4])
                return region & page.rect,'table_rules'
        candidates=[g for g in graphics if box.y1<=g.y0<min(box.y1+65,limit) and g.height>30]
        if candidates:
            region=box | min(candidates,key=lambda g:g.y0)
            return (region+(-6,-5,6,5)) & page.rect,'caption_image'
    else:
        previous=max((c['bbox'].y1 for c in all_captions if c['bbox'].y1<box.y0),default=height*.06)
        candidates=[g for g in graphics if g.y1<=box.y0+4 and g.y1>previous and g.y0>=previous
                    and box.y0-g.y1<height*.45 and g.width>25 and g.height>20]
        if candidates:
            nearest=max(candidates,key=lambda g:g.y1)
            # Expand through adjacent rows as well as columns of a multi-panel plot.
            # Starting from the lowest panel alone would cut off the upper row.
            group=pymupdf.Rect(nearest)
            panels=[]
            for g in sorted(candidates,key=lambda g:g.y1,reverse=True):
                if g.y1>=group.y0-min(100,height*.12):panels.append(g);group|=g
            region=pymupdf.Rect(nearest)
            for g in panels:region|=g
            top=region.y0-8
            for b in page.get_text('blocks'):
                if b[1]>=top and b[3]<=box.y1+2:region|=pymupdf.Rect(b[:4])
            region|=box
            return (region+(-8,-6,8,6)) & page.rect,'graphics_band'
    return page.rect,'page_fallback'


def visual_item(document_id,number,caption,region,method):
    key=f'{VERSION}|{document_id}|{number}|{caption["kind"]}|{caption["number"]}|{caption["caption"]}'
    return {'id':'vis_'+digest(key.encode())[:24],'kind':caption['kind'],'number':caption['number'],
            'label':('图' if caption['kind']=='figure' else '表')+caption['number'] if caption['kind']!='image' else '原图待核对',
            'caption':caption['caption'],'pdf_page':number,'caption_bbox':list(caption['bbox']),'bbox':list(region),
            'width':region.width,'height':region.height,'crop_method':method,
            'is_overview_candidate':caption['kind']=='figure' and bool(re.search(r'框架|流程|总览|架构|总体|framework|overview|flowchart|architecture',caption['caption'],re.I)),
            'visual_understanding_performed':False}


def page_items(page,number,document_id):
    caps=captions(page)
    embedded=[pymupdf.Rect(g['bbox']) & page.rect for g in page.get_image_info()]
    if not caps:
        # No invented figure number/title: these are original-image candidates.
        regions=[]
        for box in embedded:
            if box.width<80 or box.height<50 or box.get_area()<page.rect.get_area()*.03:continue
            if box.y1<page.rect.height*.1 or box.y0>page.rect.height*.9:continue
            if any(abs(box.x0-b.x0)<2 and abs(box.y0-b.y0)<2 and abs(box.x1-b.x1)<2 and abs(box.y1-b.y1)<2 for b in regions):continue
            regions.append(box)
        return [visual_item(document_id,number,{'kind':'image','number':str(i),
                'caption':f'PDF 第{number}页原图 {i}（未识别图题，内容待核对）','bbox':box},box,'image_candidate')
                for i,box in enumerate(regions,1)]
    drawings=page.get_drawings()
    graphics=embedded+[g for g in page.cluster_drawings(drawings=drawings) if g.y0>page.rect.height*.055 and g.y1<page.rect.height*.94]
    rules=horizontal_rules(drawings,page)
    items=[]
    for c in caps:
        region,method=region_for(page,c,caps,drawings,graphics,rules)
        items.append(visual_item(document_id,number,c,region,method))
    return items


@lru_cache(maxsize=16)
def _index(path,document_id,mtime,size):
    items=[];review_pages=[]
    with pymupdf.open(path) as doc:
        for number,page in enumerate(doc,1):
            page.set_rotation(0)
            try:items.extend(page_items(page,number,document_id))
            except Exception:
                # Isolate malformed page layouts; do not lose the entire gallery.
                # A full-page source is explicitly uncertain, never a fabricated figure.
                review_pages.append(number)
                items.append(visual_item(document_id,number,{'kind':'image','number':'page',
                    'caption':f'PDF 第{number}页图表识别未完成（原页待核对）','bbox':page.rect},page.rect,'page_fallback'))
    return {'version':VERSION,'document_id':document_id,'items':items,'review_pages':review_pages,'model_calls':0,
            'notice':'原文图表的本地预览；自动裁切边界可能不完整，图表含义与数值未因此获得核验。'}


def source_visuals(store,document_id):
    path=store.file_path(document_id);stat=path.stat()
    return _index(str(path),document_id,stat.st_mtime_ns,stat.st_size)


def render_visual(store,document_id,visual_id,scale=1.5):
    item=next((i for i in source_visuals(store,document_id)['items'] if i['id']==visual_id),None)
    if not item:raise EvidenceError(404,'visual_not_found','该图表不属于当前论文')
    with pymupdf.open(store.file_path(document_id)) as doc:
        page=doc[item['pdf_page']-1];page.set_rotation(0);clip=pymupdf.Rect(item['bbox'])
        if clip.width*clip.height*scale*scale>16000000:raise EvidenceError(422,'render_limit','预览过大，请降低缩放比例')
        return page.get_pixmap(matrix=pymupdf.Matrix(scale,scale),clip=clip,alpha=False).tobytes('png')

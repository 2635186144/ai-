"""Read-only source headings. Exact anchors are not semantic verification."""
import re
from backend.evidence.parser import digest

VERSION = 'source-headings-v3-spaced-text'


def build_outline(extraction_id, blocks):
    nodes=[]
    margins={}
    for b in blocks:
        region=b['region'];height=region.get('page_height',0)
        if height and (region['bbox'][1]<height*.12 or region['bbox'][3]>height*.90):
            key=re.sub(r'\d+', '#', re.sub(r'\s+', ' ', b['text']).strip())
            if len(key)<100:margins.setdefault(key,set()).add(region['pdf_page'])
    for order,b in enumerate(blocks):
        region=b['region'];height=region.get('page_height',0)
        key=re.sub(r'\d+', '#', re.sub(r'\s+', ' ', b['text']).strip())
        if height and (region['bbox'][1]<height*.12 or region['bbox'][3]>height*.90) and len(margins.get(key,[]))>1:continue
        lines=b['text'].splitlines(keepends=True);offsets=[];offset=0
        for line in lines:offsets.append(offset);offset+=len(line)
        skip=-1
        for i,line in enumerate(lines):
            if i<=skip:continue
            raw=line
            if len(lines)==2 and re.fullmatch(r'(?:\d+(?:\.\d+)*|[A-Z])',line.strip()) and i+1<len(lines):
                following=lines[i+1].strip()
                letters=re.findall(r'[A-Za-z]',following)
                caps=sum(c.isupper() for c in letters)/max(1,len(letters))
                typographic_heading=caps>.75 or b.get('max_font_size',12)>=10.5
                if re.match(r'[A-Z\u4e00-\u9fff]',following) and len(following)>2 and typographic_heading:
                    raw=line+lines[i+1];skip=i+1
            elif i==0 and 3<=len(lines)<=5 and re.fullmatch(r'\d+(?:\.\d+)*',line.strip()):
                # Some journals put each glyph of a short heading on its own line.
                fragments=[part.strip() for part in lines[1:]]
                if (all(re.fullmatch(r'[\u4e00-\u9fff]{1,3}',part) for part in fragments)
                        and len(''.join(fragments))<=12 and b.get('max_font_size',12)>=10.5):
                    raw=''.join(lines);skip=len(lines)-1
            title=raw.strip();start=offsets[i]+len(raw)-len(raw.lstrip())
            compact_label=re.sub(r'[\s\[\]【】]', '',title).rstrip(':：')
            named_label=bool(re.fullmatch(r'abstract|摘要|内容提要|关键词|keywords|references|bibliography|参考文献|致谢|结论|结语|引言|introduction|conclusions?|discussion',compact_label,re.I))
            if not title or len(title)>110 or (re.search(r'[，。；;!！\t\\\[\]]',title) and not named_label):continue
            if re.fullmatch(r'(?:19|20)\d{2}\s*年?',title):continue
            # Avoid contents-page dot leaders/page numbers: those aren't body starts.
            if re.search(r'\.{3,}|…{2,}|·{3,}',title):continue
            numbered=re.match(r'^(\d+(?:\.\d+)*)(?:\.?\s+)([A-Za-z\u4e00-\u9fff].*)$',title,re.S)
            chinese=re.match(r'^[一二三四五六七八九十百]+[、．.]\s*\S',title)
            sub=re.match(r'^[（(][一二三四五六七八九十]+[）)]\s*\S',title)
            named=re.fullmatch(r'(?:abstract|摘要|内容提要|关键词|keywords|references|bibliography|参考文献|acknowledg(?:e)?ments|致谢|结论|结语|引言|introduction|conclusions?|discussion|附录(?:\s*\S.*)?|appendix(?:\s+\S.*)?)',title,re.I)
            lettered=re.fullmatch(r'[A-Z](?:\.\s+|\s*\n\s*)[A-Z][\s\S]{2,95}',title)
            if not (numbered or chinese or sub or named or named_label or lettered):continue
            if numbered:
                content=numbered[2]
                if (len(title)>100 or int(numbered[1].split('.')[0])>99 or len(content.strip())<2
                        or re.search(r'[.;=<>_\[\]]',content)
                        or re.match(r'^(?:FOR|IF|ELSE|WHILE|RETURN|END|BREAK|CONTINUE)\b',content)
                        or re.match(r'^\d+\.\s+[\u4e00-\u9fff]',title)
                        or re.match(r'^(?:倍|个|次|年|%|samples?\b)',content,re.I)):continue
            level=min(3,numbered[1].count('.')+1) if numbered else 2 if sub else 1
            nodes.append({'id':'section_'+digest(f'{extraction_id}:{b["id"]}:{start}'.encode())[:18],
                          'title':title,'level':level,'pdf_page':region['pdf_page'],
                          'anchor':{'block_id':b['id'],'pdf_page':region['pdf_page'],'start':start,'end':start+len(title),'quote':title},
                          'start_position':[order,start]})
    for i,node in enumerate(nodes):
        node['end_position']=nodes[i+1]['start_position'] if i+1<len(nodes) else [len(blocks),0]
    return {'version':VERSION,'extraction_id':extraction_id,'status':'automatic_unconfirmed','semantic_verified':False,
            'notice':'标题取自提取原文，顺序和层级为自动候选；可能漏识别，不等于作者提供的正式目录。',
            'block_ids':[b['id'] for b in blocks],'sections':nodes}


def containing_section(outline, evidence):
    if not outline:return None
    try:position=[outline['block_ids'].index(evidence['block_id']),evidence['start']]
    except (ValueError,KeyError):return None
    return next((s for s in outline['sections'] if s['start_position']<=position<s['end_position']),None)


def section_ids_for_evidence(outline, evidence):
    """A child section's source also lies within its structural ancestors."""
    leaf=containing_section(outline,evidence)
    if leaf is None:return []
    stack=[]
    for section in sorted(outline['sections'],key=lambda s:s['start_position']):
        while stack and stack[-1]['level']>=section['level']:stack.pop()
        stack.append(section)
        if section['id']==leaf['id']:return [s['id'] for s in stack]
    return []

"""Local front-matter title candidates, separate from model interpretation.

No OCR, API calls, source mutation or claim of semantic verification. Every
located candidate retains unmodified source blocks; filenames are hints only.
"""
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

TITLE_VERSION = 'front-matter-title-v1'
LABEL = re.compile(r'^(?:题\s*[目名]|论文题目|Title)\s*[:：]\s*', re.I)
NOISE = re.compile(
    r'^(?:摘\s*要|abstract\b|关键词|key\s*words\b|作者|authors?\b|收稿|基金|'
    r'通信作者|引用格式|参考文献|references\b|引言|introduction\b|doi\b|issn\b|'
    r'https?[:：]|arxiv[:：]|第\s*\d+\s*[卷期]|总第|\d+\s*[.、]\s*\S)', re.I)


def clean_title(text):
    text = unicodedata.normalize('NFKC', text)
    text = ''.join(c for c in text if unicodedata.category(c) != 'Co')
    lines = [re.sub(r'\s+', ' ', s).strip() for s in text.splitlines()]
    result = ''
    for line in filter(None, lines):
        if not result:
            result = line
        elif result.endswith('-') and re.search(r'[A-Za-z]$', result[:-1]) and re.match(r'[A-Za-z]', line):
            result = result[:-1] + line
        elif len(line) == 1 and re.fullmatch(r'[A-Za-z]', line):
            result += line
        elif re.search(r'[A-Za-z0-9]$', result) and re.match(r'[A-Za-z0-9]', line):
            result += ' ' + line
        else:
            result += line
    result = LABEL.sub('', result).strip()
    return re.sub(r'(?<=[\u4e00-\u9fff]) +(?=[\u4e00-\u9fff])', '', result)


def key(text):
    return ''.join(c for c in clean_title(text).casefold() if c.isalnum())


def filename_title(filename):
    name = Path(filename.replace('\\', '/')).stem
    # Common download naming: full_title_Author.pdf. Do not strip English
    # technical suffixes or internal underscores such as AI_Agent / Apache_IoTDB.
    name = re.sub(r'_[\u4e00-\u9fff]{2,5}$', '', name)
    return clean_title(name.replace('_', ' '))[:250]


def _usable(text):
    if not 8 <= len(key(text)) <= 200 or len(text) > 300:
        return False
    return not (NOISE.search(text) or '@' in text or '://' in text
                or re.search(r'网络首发|网络出版|CHINESE\s+JOURNAL|Vol\s*\.|ISSN|CN\s*\d', text, re.I))


def _body_font(blocks):
    # Character-weighted median also works when Chinese body paragraphs have
    # been broken into many small blocks but the English abstract is one block.
    weighted = sorted((b['max_font_size'], len(key(b['text']))) for b in blocks
                      if len(key(b['text'])) >= 8 and b['max_font_size'] > 0)
    halfway = sum(weight for _, weight in weighted) / 2
    cumulative = 0
    for size, weight in weighted:
        cumulative += weight
        if cumulative >= halfway:
            return size
    return 10


def identify_title(blocks, filename, metadata_title=''):
    fallback = filename_title(filename)
    meaningful_filename = len(key(fallback)) >= (4 if re.search(r'[\u4e00-\u9fff]', fallback) else 8) and not re.fullmatch(r'(?:paper|document|article|download|论文|文档)\d*', key(fallback))
    hints = [key(fallback)] if meaningful_filename else []
    if _usable(clean_title(metadata_title)):
        hints.append(key(metadata_title))
    front = sorted([b for b in blocks if b['region']['pdf_page'] <= 2],
                   key=lambda b: (b['region']['pdf_page'], b['region']['bbox'][1], b['region']['bbox'][0]))
    candidates = []
    for page in (1, 2):
        page_blocks = [b for b in front if b['region']['pdf_page'] == page]
        if not page_blocks:
            continue
        body = _body_font(page_blocks)
        abstract_y = min((b['region']['bbox'][1] for b in page_blocks
                          if re.match(r'^(?:摘\s*要|abstract\b)', clean_title(b['text']), re.I)), default=float('inf'))
        possible = [b for b in page_blocks if .045 * b['region']['page_height'] <= b['region']['bbox'][1] <= .62 * b['region']['page_height']]
        for seed in possible:
            text = clean_title(seed['text'])
            labeled = bool(LABEL.match(unicodedata.normalize('NFKC', seed['text'])))
            # A matching filename cannot promote a normal paragraph into a
            # title. Require front-matter typography or an explicit label.
            if not labeled and (seed['region']['bbox'][1] >= abstract_y or seed['max_font_size'] < body * 1.2):
                continue
            if not _usable(text) and not (len(key(text)) >= 5 and seed['max_font_size'] >= body * 1.3):
                continue
            group = [seed]
            variants = [group[:]]
            # The same title can span blocks, including a small English name
            # beside a larger Chinese subtitle (OpenPerf-style typography).
            for other in possible:
                if other is seed or other in group:
                    continue
                text2 = clean_title(other['text'])
                box, prev = other['region']['bbox'], group[-1]['region']['bbox']
                same_row = abs(box[1] - seed['region']['bbox'][1]) <= seed['max_font_size'] * .65
                gap = box[1] - prev[3]
                similar_font = abs(other['max_font_size'] - seed['max_font_size']) <= seed['max_font_size'] * .13
                inline = same_row and box[0] > seed['region']['bbox'][0] and other['max_font_size'] >= body * 1.3
                continuation = 0 <= gap <= seed['max_font_size'] * 1.3 and similar_font
                if not (inline or continuation) or NOISE.search(text2) or len(key(text2)) < 3:
                    continue
                if not labeled and not inline and other['max_font_size'] < body * 1.25:
                    continue
                if sum(len(key(b['text'])) for b in group) + len(key(text2)) > 200:
                    break
                group.append(other)
                variants.append(group[:])
                if len(group) == 4:
                    break
            for variant in variants:
                # Original block quotes remain untouched; normalization is
                # exclusively for title display and candidate comparison.
                value = clean_title('\n'.join(b['text'] for b in variant))
                if not _usable(value):
                    continue
                similarity = max((SequenceMatcher(None, key(value), h).ratio() for h in hints), default=0)
                ratio = seed['max_font_size'] / max(body, 1)
                if not (labeled or similarity >= .72 or (ratio >= 1.35 and len(key(value)) >= 12)):
                    continue
                score = min(ratio, 2.7) * 12 + (1-seed['region']['bbox'][1]/seed['region']['page_height']) * 10
                score += similarity * 55 + (20 if similarity > .98 else 0) + (65 if labeled else 0) + (8 if len(key(value)) >= 14 else 0) - (10 if page == 2 else 0)
                score += min(len(key(value)), 80) * .12
                candidates.append((score, value, variant, labeled))
    candidates.sort(key=lambda c: -c[0])
    if candidates:
        best = candidates[0]
        # Different, equally plausible headings must not be silently settled.
        ambiguous = any(abs(c[0]-best[0]) < 3 and SequenceMatcher(None,key(c[1]),key(best[1])).ratio() < .6 for c in candidates[1:])
        if not ambiguous:
            return {'text': best[1], 'status': 'source_located', 'method': 'labeled_title' if best[3] else 'front_matter_layout',
                    'sources': [{'block_id': b['id'], 'pdf_page': b['region']['pdf_page'], 'start': 0,
                                 'end': len(b['text']), 'quote': b['text']} for b in best[2]],
                    'note': '依据首页或题录页文字与排版识别，可回查原文；尚未经人工确认。', 'version': TITLE_VERSION}
    return {'text': fallback if meaningful_filename else '论文题名待确认',
            'status': 'filename_only' if meaningful_filename else 'unresolved', 'method': 'filename', 'sources': [],
            'note': '暂按文件名显示，尚未与原文题名核对。' if meaningful_filename else '未取得可靠题名，请核对首页；图像题名需文字识别。', 'version': TITLE_VERSION}

import hashlib
import re
import unicodedata

import pymupdf

from backend.evidence.models import Extraction, MaterialCandidate, PageInfo, SourceBlock
from design.contracts import Producer, SourceRegion

# Include library version because text grouping can change between library releases.
PARSER_VERSION = f'evidence-v1-pymupdf-{pymupdf.VersionBind}'
MAX_PAGES = 100
MAX_BYTES = 20 * 1024 * 1024
MAX_BLOCKS = 20000
MAX_CHARACTERS = 2000000


class EvidenceError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


def digest(content):
    return hashlib.sha256(content).hexdigest()


def normalize(text):
    return ' '.join(unicodedata.normalize('NFKC', text).split()).casefold()


def inspect_pdf(content):
    if len(content) > MAX_BYTES:
        raise EvidenceError(413, 'pdf_too_large', 'PDF 请控制在 20 MB 以内')
    if not content.startswith(b'%PDF-'):
        raise EvidenceError(422, 'invalid_pdf', '文件不是有效 PDF')
    try:
        with pymupdf.open(stream=content, filetype='pdf') as doc:
            if doc.needs_pass:
                raise EvidenceError(422, 'encrypted_pdf', '请使用未加密的 PDF')
            if not 1 <= len(doc) <= MAX_PAGES:
                raise EvidenceError(422, 'page_limit', 'PDF 必须为 1—100 页')
            return len(doc), (doc.metadata.get('title') or '').strip()[:250]
    except EvidenceError:
        raise
    except Exception as exc:
        raise EvidenceError(422, 'invalid_pdf', 'PDF 无法读取') from exc


def clipped_box(raw, page):
    rect = pymupdf.Rect(raw) & page.rect
    if rect.is_empty or rect.is_infinite:
        return None
    return tuple(float(x) for x in rect)


def parse_document(content, document_id):
    inspect_pdf(content)
    blocks, pages, materials, warnings = [], [], [], []
    section, total_chars = 'main', 0
    producer = Producer(kind='tool', actor_id='pymupdf_evidence_parser')

    def candidate(kind, page_number, bbox, label, block_id=None):
        key = f'{document_id}|{PARSER_VERSION}|{page_number}|{kind}|{bbox}|{block_id}|{label}'
        material = MaterialCandidate(id='mat_' + digest(key.encode())[:24], kind=kind,
            pdf_page=page_number, bbox=bbox, block_id=block_id, label=label[:500],
            limitations=['仅为位置候选，未理解图表内容、未提取表格单元格，也未完成公式语义核验'])
        materials.append(material)

    try:
        with pymupdf.open(stream=content, filetype='pdf') as doc:
            for page_number, page in enumerate(doc, 1):
                original_rotation = page.rotation
                page_label = page.get_label() or None
                # PyMuPDF returns unrotated coordinates. Render previews in that frame too.
                page.set_rotation(0)
                flags = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES
                layout = page.get_text('dict', flags=flags, sort=False)
                page_blocks, page_warnings = [], []
                for raw in layout['blocks']:
                    if raw.get('type') != 0:
                        continue
                    lines = [''.join(span['text'] for span in line['spans']) for line in raw['lines']]
                    text = '\n'.join(lines).strip()
                    if not text:
                        continue
                    bbox = clipped_box(raw['bbox'], page)
                    if bbox is None:
                        page_warnings.append('text_outside_visible_page')
                        continue
                    if tuple(raw['bbox']) != bbox:
                        page_warnings.append('text_region_clipped_to_visible_page')
                    normalized = normalize(text)
                    appendix = bool(re.match(r'^(appendix|appendices|supplementary material|附录)(?:\s|$)', normalized))
                    if not appendix and len(lines) >= 2:
                        appendix = bool(re.fullmatch(r'[A-Z]', lines[0].strip()) and re.fullmatch(r'[A-Z][A-Z0-9 ,:()\-–]{5,}', lines[1].strip()))
                    if re.fullmatch(r'references|bibliography|参考文献', normalized):
                        section = 'references_candidate'
                    if appendix:
                        section = 'appendix_candidate'
                    text_hash = digest(text.encode())
                    key = f'{document_id}|{PARSER_VERSION}|{page_number}|{raw["number"]}|{text_hash}'
                    block_id = 'blk_' + digest(key.encode())[:24]
                    region = SourceRegion(id=block_id, document_version_id=document_id,
                        pdf_page=page_number, page_width=float(page.rect.width), page_height=float(page.rect.height),
                        bbox=bbox, label=text[:120], precision='text_block', text_sha256=text_hash,
                        locator_status='located', visual_checked=False, producer=producer)
                    block = SourceBlock(id=block_id, region=region, text=text, text_sha256=text_hash,
                        order_in_page=int(raw['number']), section_hint=section,
                        max_font_size=float(max(span['size'] for line in raw['lines'] for span in line['spans'])))
                    page_blocks.append(block)
                    if appendix:
                        candidate('appendix_heading', page_number, bbox, text, block_id)
                    first_line = normalize(lines[0])
                    if re.match(r'^(?:figure|fig\.|图)\s*\d+[a-z]?\s*[:.：、]', first_line):
                        candidate('figure_caption', page_number, bbox, text, block_id)
                    if re.match(r'^(?:table|表)\s*\d+[a-z]?\s*[:.：、]', first_line):
                        candidate('table_caption', page_number, bbox, text, block_id)
                    if len(re.findall(r'[=∑∏∈∥√⊤Δ∆φσα]', text)) >= 3:
                        candidate('equation_candidate', page_number, bbox, text, block_id)
                image_info = page.get_image_info()
                if len(materials) + len(image_info) > MAX_BLOCKS:
                    raise EvidenceError(422, 'extraction_limit', '文档材料候选过多，请拆分后上传')
                for index, info in enumerate(image_info):
                    bbox = clipped_box(info['bbox'], page)
                    if bbox:
                        candidate('image', page_number, bbox, f'嵌入图像 {index + 1}（非图表语义判断）')
                char_count = sum(len(b.text.strip()) for b in page_blocks)
                total_chars += char_count
                if len(blocks) + len(page_blocks) > MAX_BLOCKS or total_chars > MAX_CHARACTERS:
                    raise EvidenceError(422, 'extraction_limit', '文档结构超出本阶段解析上限，请拆分后上传')
                text_status = 'no_text' if char_count == 0 else 'sparse_text' if char_count < 40 else 'text_available'
                if text_status != 'text_available':
                    page_warnings.append('文字不足：可能为空白页或图像页；当前未执行 OCR，不判断论文缺内容')
                left = sum(b.region.bbox[2] < page.rect.width * .60 for b in page_blocks)
                right = sum(b.region.bbox[0] > page.rect.width * .40 for b in page_blocks)
                if left >= 2 and right >= 2:
                    page_warnings.append('possible_multicolumn_reading_order_unverified')
                pages.append(PageInfo(pdf_page=page_number, pdf_page_label=page_label,
                    width=float(page.rect.width), height=float(page.rect.height), original_rotation=original_rotation,
                    text_characters=char_count, block_count=len(page_blocks), image_count=len(image_info),
                    text_status=text_status, warnings=list(dict.fromkeys(page_warnings))))
                blocks.extend(page_blocks)
        if any(p.text_status != 'text_available' for p in pages):
            warnings.append('部分页面文字不足，后续深读需核查；当前解析状态为 partial')
        warnings.extend(['保留 PDF 提取顺序，尚未验证双栏阅读顺序',
                         '图表/附录/公式使用启发式候选识别；未检测到不等于论文没有',
                         'PDF 页标签不等于识别出的印刷页码；预览统一采用未旋转坐标'])
        return Extraction(id='parse_' + digest(f'{document_id}|{PARSER_VERSION}'.encode())[:24],
            document_version_id=document_id, parser_version=PARSER_VERSION,
            status='partial' if any(p.text_status != 'text_available' for p in pages) else 'parsed',
            pages=pages, blocks=blocks, materials=materials, warnings=warnings)
    except EvidenceError:
        raise
    except Exception as exc:
        raise EvidenceError(422, 'extraction_failed', '结构解析失败，原始文档已保留，可重试或补充材料') from exc

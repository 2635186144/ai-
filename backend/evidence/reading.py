"""Source-grounded preparation; no model calls and no claims of semantic reading."""
import json
import re
from typing import Literal

from pydantic import Field

from backend.evidence.parser import EvidenceError, digest
from backend.evidence.store import now
from design.contracts import Contract


class Section(Contract):
    start_block_id: str
    start_offset: int = Field(default=0, ge=0)
    title: str = Field(min_length=1, max_length=300)
    level: int = Field(default=1, ge=1, le=3)


class ReadingAction(Contract):
    revision: int = Field(ge=1)
    actor: Literal['local_user', 'assistant_curated'] = 'local_user'
    action: Literal['set_sections', 'refresh_sections', 'confirm_sections', 'set_order', 'confirm_page', 'mark_material', 'use_extracted']
    sections: list[Section] | None = Field(default=None, max_length=500)
    page: int | None = Field(default=None, ge=1)
    block_ids: list[str] | None = Field(default=None, max_length=20000)
    status: Literal['confirmed', 'indexed', 'excluded', 'needs_source', 'pending'] | None = None
    material_id: str | None = None
    note: str = Field(default='', max_length=2000)


class ReadingPreparation:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def section_id(section):
        offset = section.get('start_offset', 0)
        return section['start_block_id'] + (f'~{offset}' if offset else '')

    def source(self, did):
        self.store.file_path(did)
        with self.store.connection() as db:
            eid = self.store._extraction_id(db, did)
            rows = db.execute('SELECT payload_json FROM evidence_blocks WHERE extraction_id=? ORDER BY pdf_page,order_in_page,id', (eid,))
            blocks = [self.store._verified_block(r[0], did).model_dump(mode='json') for r in rows]
        return eid, blocks, self.store.detail(did).extraction, [m.model_dump(mode='json') for m in self.store.materials(did)]

    @staticmethod
    def propose(eid, blocks, summary, materials):
        sections = []
        def margin_key(b):
            region = b['region']
            height = region.get('page_height')
            edge = height and (region['bbox'][1] < height*.09 or region['bbox'][3] > height*.91)
            text = re.sub(r'\s+', ' ', b['text']).strip()
            return re.sub(r'\d+', '#', text) if edge and len(text) < 100 else None
        repeated = {}
        for b in blocks:
            k = margin_key(b)
            if k:
                repeated.setdefault(k, set()).add(b['region']['pdf_page'])
        for i, b in enumerate(blocks):
            text = re.sub(r'\s+', ' ', b['text']).strip()
            if i and (len(repeated.get(margin_key(b), set())) > 1 or re.fullmatch(r'(?:19|20)\d{2}\s*年?', text)):
                continue  # Retain source text; omit recurring running headers from chapter proposals.
            # Numbered headings, explicit appendix headings and references. Font/length
            # limits reduce false positives; every result remains a human-editable candidate.
            numbered = re.match(r'^(?:\d+(?:\.\d+)*|[A-Z])[.\s]+[A-Z]', text)
            # Many papers use body-size type for subsection headings; require a
            # separate numbering line rather than assuming every small numeric row is a heading.
            subsection = re.match(r'^(?:\d+|[A-Z])(?:\.\d+)+\s*\n\s*[A-Z]', b['text'])
            named = re.match(r'^(?:abstract|references|bibliography|appendix\b|附录|参考文献|摘要)', text, re.I)
            chinese = re.match(r'^\d+(?:\.\d+)*\s+[\u4e00-\u9fff]', text)
            # Chinese numbered headings often use body-sized bold type; avoid
            # numeric results such as "3 倍。" or "2.6 平稳降至1.8，...".
            chinese = bool(chinese and len(text) < 75 and not re.search(r'[，。；%\[\]]', text)
                           and b['max_font_size'] >= 10)
            if i == 0 or (len(text) < 200 and ((numbered and b['max_font_size'] >= 10.5) or subsection or named or chinese)):
                level = min(3, text.split()[0].count('.')+1) if subsection or chinese else 1
                sections.append({'start_block_id': b['id'], 'title': '文首与题录' if i == 0 else text[:300], 'level': level})
            # A PDF text block can contain multiple headings or an algorithm tail
            # followed by a heading. Keep immutable blocks; anchor exact characters.
            offset = 0
            for line in b['text'].splitlines(keepends=True):
                heading = line.strip()
                match = re.match(r'^(\d+(?:\.\d+)*)\s+([A-Z\u4e00-\u9fff].*)$', heading)
                is_chinese = bool(match and re.search(r'[\u4e00-\u9fff]', match[2]))
                valid = (match and len(heading) < 75 and not re.search(r'[，。；%\[\]]', heading)
                         and b['max_font_size'] >= (10 if is_chinese else 10.5))
                start = offset + len(line)-len(line.lstrip())
                if valid:
                    if re.fullmatch(r'(?:19|20)\d{2}\s*年?', heading):
                        offset += len(line)
                        continue
                    candidate = {'start_block_id': b['id'], 'title': heading, 'level': min(3, match[1].count('.')+1)}
                    if start:
                        candidate['start_offset'] = start
                        sections.append(candidate)
                    elif sections and sections[-1]['start_block_id'] == b['id'] and i != 0:
                        sections[-1] = candidate
                    elif not any(s['start_block_id'] == b['id'] and s.get('start_offset', 0) == 0 for s in sections):
                        sections.append(candidate)
                offset += len(line)
        return {'extraction_id': eid, 'revision': 1, 'sections': sections, 'sections_confirmed': False,
                'pages': [{'page': p.pdf_page, 'block_ids': [b['id'] for b in blocks if b['region']['pdf_page'] == p.pdf_page],
                           'status': 'pending', 'note': '', 'order_method': 'native_unverified'} for p in summary.pages],
                'materials': [{'id': m['id'], 'status': 'pending', 'note': ''} for m in materials],
                'last_action': {'action': 'propose', 'actor': 'tool', 'at': now()},
                'semantic_reading_performed': False, 'model_calls': 0}

    @staticmethod
    def save(db, plan):
        db.execute('INSERT INTO reading_versions VALUES (?,?,?,?)',
                   (plan['extraction_id'], plan['revision'], json.dumps(plan, ensure_ascii=False), now()))

    def get(self, did):
        eid, blocks, summary, materials = self.source(did)
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload_json FROM reading_versions WHERE extraction_id=? ORDER BY revision DESC LIMIT 1', (eid,)).fetchone()
            plan = json.loads(row[0]) if row else self.propose(eid, blocks, summary, materials)
            if row is None:
                self.save(db, plan)
            db.commit()
        return self.present(plan, blocks, summary, materials)

    @staticmethod
    def ordered(plan):
        return [bid for p in plan['pages'] for bid in p['block_ids']]

    @classmethod
    def validate_sections(cls, sections, plan, blocks=None):
        ordered = cls.ordered(plan)
        anchors = [(s['start_block_id'], s.get('start_offset', 0)) for s in sections]
        if not ordered:
            if sections:
                raise EvidenceError(422, 'invalid_sections', '无文本的文档不能创建文本章节')
            return
        if (not anchors or anchors[0] != (ordered[0], 0) or len(set(anchors)) != len(anchors)
                or any(b not in ordered or type(offset) is not int or offset < 0 for b, offset in anchors)):
            raise EvidenceError(422, 'invalid_sections', '首章必须从首块字符0开始，章节起点必须属于当前版本且不得重复')
        if blocks is not None:
            lengths = {b['id']: len(b['text']) for b in blocks}
            if any(offset >= lengths.get(bid, 0) for bid, offset in anchors):
                raise EvidenceError(422, 'invalid_section_offset', '章节起点必须位于原始文本块内，不能越界或等于块末尾')
        positions = [(ordered.index(b), offset) for b, offset in anchors]
        if positions != sorted(positions):
            raise EvidenceError(422, 'invalid_sections', '章节锚点必须按当前阅读顺序排列')

    @classmethod
    def present(cls, plan, blocks, summary, materials):
        cls.validate_sections(plan['sections'], plan, blocks)
        by_id = {b['id']: b for b in blocks}
        ordered = cls.ordered(plan)
        if len(ordered) != len(by_id) or set(ordered) != set(by_id):
            raise EvidenceError(409, 'reading_integrity_failed', '阅读清单与文本块不一致，已阻止继续')
        sections = []
        for i, s in enumerate(plan['sections']):
            start = ordered.index(s['start_block_id'])
            end = ordered.index(plan['sections'][i+1]['start_block_id']) if i+1 < len(plan['sections']) else len(ordered)
            end_offset = plan['sections'][i+1].get('start_offset', 0) if i+1 < len(plan['sections']) else 0
            spans = []
            for j in range(start, min(end+1, len(ordered))):
                bid = ordered[j]
                lo = s.get('start_offset', 0) if j == start else 0
                hi = end_offset if j == end else len(by_id[bid]['text'])
                if hi > lo:
                    spans.append({'block_id': bid, 'start': lo, 'end': hi})
            ids = [span['block_id'] for span in spans]
            sections.append({**s, 'id': cls.section_id(s), 'block_ids': ids, 'source_spans': spans, 'start_page': by_id[ids[0]]['region']['pdf_page'],
                             'end_page': by_id[ids[-1]]['region']['pdf_page']})
        unresolved_pages = [p['page'] for p in plan['pages'] if p['status'] not in ('confirmed', 'excluded')]
        unresolved_materials = [m['id'] for m in plan['materials'] if m['status'] not in ('indexed', 'excluded')]
        excluded = [p['page'] for p in plan['pages'] if p['status'] == 'excluded']
        readable = any(p['status'] == 'confirmed' and p['block_ids'] for p in plan['pages'])
        ready = plan['sections_confirmed'] and not unresolved_pages and not unresolved_materials and readable
        previews = []
        for b in blocks:
            lines = []
            cursor = 0
            for line in b['text'].splitlines(keepends=True):
                if line.strip() and len(lines) < 200:
                    lines.append({'offset': cursor+len(line)-len(line.lstrip()), 'text': line.strip()[:120]})
                cursor += len(line)
            previews.append({'id': b['id'], 'page': b['region']['pdf_page'], 'text': b['text'][:240],
                             'text_length': len(b['text']), 'line_starts': lines, 'bbox': b['region']['bbox']})
        return {**plan, 'section_index': sections, 'block_previews': previews,
                'page_info': [p.model_dump(mode='json') for p in summary.pages], 'material_index': materials,
                'readiness': {'ready': ready, 'can_read': ready or plan.get('use_extracted', False), 'unconfirmed_pages': unresolved_pages, 'unconfirmed_materials': unresolved_materials,
                              'excluded_pages': excluded, 'included_text_available': readable,
                              'next_stage': 'section_packets_ready_model_not_configured' if ready else 'preparation_required'}}

    def update(self, did, command, key):
        eid, blocks, summary, materials = self.source(did)
        # Ensure initial proposal exists, then serialize mutations and compare revisions.
        self.get(did)
        fingerprint = digest(command.model_dump_json().encode())
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM reading_requests WHERE extraction_id=? AND request_key=?', (eid, key)).fetchone()
            if previous:
                if previous['fingerprint'] != fingerprint:
                    raise EvidenceError(409, 'idempotency_conflict', '请求标识已用于其他修改')
                row = db.execute('SELECT payload_json FROM reading_versions WHERE extraction_id=? AND revision=?', (eid, previous['revision'])).fetchone()
                return self.present(json.loads(row[0]), blocks, summary, materials)
            row = db.execute('SELECT payload_json FROM reading_versions WHERE extraction_id=? ORDER BY revision DESC LIMIT 1', (eid,)).fetchone()
            plan = json.loads(row[0])
            if command.revision != plan['revision']:
                raise EvidenceError(409, 'stale_reading_revision', '阅读结构已更新，请刷新后重新核对')
            # Any later correction revokes the prior acceptance of unverified extraction.
            plan.pop('use_extracted', None)
            if command.action == 'refresh_sections':
                candidates = self.propose(eid, blocks, summary, materials)['sections']
                ordered = self.ordered(plan)
                candidates.sort(key=lambda s: (ordered.index(s['start_block_id']), s.get('start_offset', 0)))
                if candidates and (candidates[0]['start_block_id'] != ordered[0] or candidates[0].get('start_offset', 0) != 0):
                    candidates.insert(0, {'start_block_id': ordered[0], 'title': '待核对的文首', 'level': 1})
                plan['sections'] = candidates
                plan['sections_confirmed'] = False
            else:
                self.apply(plan, command, summary, blocks)
            plan['revision'] += 1
            plan['last_action'] = {'action': command.action, 'actor': 'tool' if command.action == 'refresh_sections' else command.actor, 'at': now(), 'note': command.note}
            result = self.present(plan, blocks, summary, materials)
            self.save(db, plan)
            db.execute('INSERT INTO reading_requests VALUES (?,?,?,?)', (eid, key, fingerprint, plan['revision']))
            db.commit()
        return result

    @classmethod
    def apply(cls, plan, c, summary, blocks):
        if c.action == 'use_extracted':
            if not blocks or any(x['status'] == 'needs_source' for x in [*plan['pages'], *plan['materials']]):
                raise EvidenceError(409, 'source_gap_unresolved', '资料没有可读文字或已标记待补材料，请先处理已知缺口')
            plan['use_extracted'] = True
        elif c.action == 'set_sections':
            if c.sections is None:
                raise EvidenceError(422, 'missing_sections', '请提交章节列表')
            sections = [s.model_dump() for s in c.sections]
            cls.validate_sections(sections, plan, blocks)
            plan['sections'] = sections
            plan['sections_confirmed'] = False
        elif c.action == 'confirm_sections':
            cls.validate_sections(plan['sections'], plan, blocks)
            plan['sections_confirmed'] = True
        elif c.action in ('set_order', 'confirm_page'):
            p = next((p for p in plan['pages'] if p['page'] == c.page), None)
            if p is None:
                raise EvidenceError(422, 'invalid_page', '请选择当前文档中的页面')
            if c.action == 'set_order':
                if c.block_ids is None or len(c.block_ids) != len(p['block_ids']) or set(c.block_ids) != set(p['block_ids']):
                    raise EvidenceError(422, 'invalid_reading_order', '阅读顺序必须恰好包含本页全部文本块，不得遗漏、重复或跨页')
                p.update(block_ids=c.block_ids, status='pending', note='', order_method='manual')
                # Re-sort boundaries to the edited reading order. Always invalidate confirmation.
                order = cls.ordered(plan)
                plan['sections'].sort(key=lambda s: (order.index(s['start_block_id']), s.get('start_offset', 0)))
                if plan['sections'] and (plan['sections'][0]['start_block_id'] != order[0] or plan['sections'][0].get('start_offset', 0) != 0):
                    plan['sections'].insert(0, {'start_block_id': order[0], 'title': '待核对的文首', 'level': 1})
                plan['sections_confirmed'] = False
            else:
                if c.status not in ('confirmed', 'excluded', 'needs_source', 'pending'):
                    raise EvidenceError(422, 'invalid_page_status', '页面状态不正确')
                if c.status in ('excluded', 'needs_source') and not c.note.strip():
                    raise EvidenceError(422, 'reason_required', '排除或缺失材料需要填写原因')
                info = next(p for p in summary.pages if p.pdf_page == c.page)
                if c.status == 'confirmed' and info.text_status != 'text_available':
                    raise EvidenceError(409, 'text_gap_unresolved', '该页文字稀疏或无文本，请先补充可读材料，或说明排除理由；不能直接确认为可深读')
                p.update(status=c.status, note=c.note)
        else:
            m = next((m for m in plan['materials'] if m['id'] == c.material_id), None)
            if m is None or c.status not in ('indexed', 'excluded', 'needs_source', 'pending'):
                raise EvidenceError(422, 'invalid_material', '请选择本版本材料及有效状态')
            if c.status in ('excluded', 'needs_source') and not c.note.strip():
                raise EvidenceError(422, 'reason_required', '排除或缺失材料需要填写原因')
            m.update(status=c.status, note=c.note)

    def packet(self, did, section_id, revision, preview=False):
        result = self.get(did)
        if revision != result['revision']:
            raise EvidenceError(409, 'stale_reading_revision', '阅读结构已更新，请重新打开分节材料包')
        if not preview and not result['readiness']['can_read']:
            raise EvidenceError(409, 'preparation_incomplete', '请先完成章节、逐页阅读顺序与材料覆盖确认')
        section = next((s for s in result['section_index'] if s['id'] == section_id), None)
        if section is None:
            raise EvidenceError(404, 'section_not_found', '章节不属于当前版本')
        excluded = set(result['readiness']['excluded_pages'])
        # Re-verify hashes before returning full source text.
        _, blocks, _, _ = self.source(did)
        by_id = {b['id']: b for b in blocks}
        included = [by_id[bid] for bid in section['block_ids'] if by_id[bid]['region']['pdf_page'] not in excluded]
        included_ids = {b['id'] for b in included}
        spans = [{**span, 'text': by_id[span['block_id']]['text'][span['start']:span['end']]}
                 for span in section['source_spans'] if span['block_id'] in included_ids]
        if not included:
            raise EvidenceError(409, 'section_excluded', '本节已全部排除，无可深读文本')
        material_states = {m['id']: m for m in result['materials']}
        # For unanchored images use page-level association, explicitly approximate.
        partial_blocks = {s['block_id'] for s in section['source_spans'] if s['start'] or s['end'] != len(by_id[s['block_id']]['text'])}
        relevant = [{**m, 'coverage': material_states[m['id']], 'association':
                     ('shared_block_overlap_unverified' if m['block_id'] in partial_blocks else 'caption_block') if m['block_id'] else 'page_overlap_unverified'}
                    for m in result['material_index'] if (m['block_id'] in section['block_ids'] if m['block_id'] else section['start_page'] <= m['pdf_page'] <= section['end_page'])]
        return {'document_version_id': did, 'extraction_id': result['extraction_id'], 'reading_revision': revision,
                'section': section, 'blocks': included, 'source_spans': spans, 'materials': relevant,
                'preview_only': preview and not result['readiness']['can_read'],
                'source_preparation': {'human_structure_verified': result['readiness']['ready'],
                                       'accepted_unverified_extraction': result.get('use_extracted', False),
                                       'unconfirmed_pages': result['readiness']['unconfirmed_pages'],
                                       'unconfirmed_materials': result['readiness']['unconfirmed_materials'],
                                       'page_gaps': [p for p in result['page_info'] if p['text_status'] != 'text_available']},
                'source_contract': 'original_blocks_with_section_character_ranges_v1',
                'exclusions': [p for p in result['pages'] if p['status'] == 'excluded'],
                'status': 'source_packet_ready', 'semantic_reading_performed': False, 'model_calls': 0,
                'reading_tasks': ['逐段解释问题、方法、推导与限定条件，逐项绑定原文块', '图表和公式对照原图核查，不以图注替代图像理解', '区分作者主张、直接证据、推断与未知；记录跨节依赖', '完成事实底稿后再提出专家审阅和对抗质询'],
                'limitations': ['blocks保留整块原文供定位，只有source_spans范围属于当前分节', '位置确认不等于语义已读或证据正确', '材料候选可能有漏检，须对照整页检查；未实现 OCR/表格单元格/视觉理解', '仅含当前文档版本；其他补充文件须单独登记并核对', '尚未调用模型，不能将材料包作为深读结论']}

"""Versioned paragraph worksheets. Citation existence never implies entailment."""
import json
import re
from datetime import datetime, timezone

from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from backend.evidence import fact_model
from backend.evidence.fact_models import ModelParagraphDraft, ParagraphDraft
from backend.evidence.parser import EvidenceError, digest
from backend.evidence.reading import ReadingPreparation
from backend.evidence.store import now
from backend.evidence.support import check_review, support_checks
from backend.evidence.source_scope import contains_span, quote_positions, scoped_blocks

UNITIZATION_VERSION = 'paragraph-v2-cjk-lines'


def can_join_lines(previous, current):
    """Conservative same-page CJK line grouping; never claims verified boundaries."""
    a, b = previous, current
    if any('\n' in x['text'] or len(re.findall(r'[\u4e00-\u9fff]', x['text'])) < 8 for x in (a, b)):
        return False
    if a['region']['pdf_page'] != b['region']['pdf_page'] or abs(a.get('max_font_size', 0)-b.get('max_font_size', 0)) > .08:
        return False
    A, B = a['region']['bbox'], b['region']['bbox']
    font = max(a.get('max_font_size', 10), 1)
    # Headings, captions, lists, indented new paragraphs and column changes stop joins.
    if any(re.match(r'^(?:\d+(?:\.\d+)*\s|[（(]\d|图\s*\d|表\s*\d)', x['text']) for x in (a, b)):
        return False
    return (0 <= B[1]-A[3] <= (A[3]-A[1])*.7 and B[0] <= A[0]+font
            and A[2] >= a['region']['page_width']*.75 and B[2] > A[0]
            and not re.search(r'[。！？；]\s*$', a['text']))


def paragraph_units(packet):
    groups = []
    for block in scoped_blocks(packet):
        if groups and can_join_lines(groups[-1][-1], block):
            groups[-1].append(block)
        else:
            groups.append([block])
    units = []
    for group in groups:
        if len(group) > 1:
            spans = [{'block_id': b['id'], 'start': b['_source_start']+len(b['text'])-len(b['text'].lstrip()),
                      'end': b['_source_start']+len(b['text'].rstrip()), 'text': b['text'].strip()} for b in group]
            text = '\n'.join(s['text'] for s in spans)
            uid = 'para_' + digest(json.dumps(spans, sort_keys=True, ensure_ascii=False).encode())[:32]
            units.append({'id': uid, 'block_id': spans[0]['block_id'], 'start': spans[0]['start'], 'end': spans[0]['end'],
                          'text': text, 'text_sha256': digest(text.encode()), 'region': group[0]['region'],
                          'source_spans': spans, 'boundary_status': 'cjk_geometry_group_unverified', 'draft': None, 'producer': None})
            continue
        block = group[0]
        cursor = 0
        for piece in re.split(r'(\n[ \t]*\n+)', block['text']):
            local_start = cursor + len(piece) - len(piece.lstrip())
            local_end = cursor + len(piece.rstrip())
            start = block['_source_start'] + local_start
            end = block['_source_start'] + local_end
            cursor += len(piece)
            if end <= start:
                continue
            text = block['text'][local_start:local_end]
            uid = 'para_' + digest(f"{block['id']}:{start}:{end}:{block['text_sha256']}".encode())[:32]
            units.append({'id': uid, 'block_id': block['id'], 'start': start, 'end': end, 'text': text,
                          'text_sha256': digest(text.encode()), 'region': block['region'],
                          'source_spans': [{'block_id': block['id'], 'start': start, 'end': end, 'text': text}],
                          'boundary_status': 'blank_line_or_pdf_block_unverified', 'draft': None, 'producer': None})
    return units


class FactWorksheet:
    def __init__(self, store):
        self.store = store

    def packet(self, did, sid, revision):
        return ReadingPreparation(self.store).packet(did, sid, revision)

    @staticmethod
    def ledger_id(packet):
        return 'facts_' + digest(f"{UNITIZATION_VERSION}:{packet['document_version_id']}:{packet['extraction_id']}:{packet['reading_revision']}:{packet['section']['id']}".encode())

    @staticmethod
    def save(db, ledger):
        db.execute('INSERT INTO fact_versions VALUES (?,?,?,?,?,?)', (ledger['id'], ledger['revision'],
            ledger['extraction_id'], ledger['reading_revision'], json.dumps(ledger, ensure_ascii=False), now()))

    @staticmethod
    def current_reading(db, packet):
        latest = db.execute('SELECT max(revision) FROM reading_versions WHERE extraction_id=?', (packet['extraction_id'],)).fetchone()[0]
        if latest != packet['reading_revision']:
            raise EvidenceError(409, 'stale_reading_revision', '阅读结构已变更，旧底稿保留在历史中；请基于新分节包建立底稿')

    @staticmethod
    def present(ledger):
        units = [{**u, 'support_checks': support_checks(u)} for u in ledger['units']]
        counts = {s: sum((u['draft']['disposition'] if u['draft'] else 'pending') == s for u in ledger['units'])
                  for s in ['pending', 'analysed', 'non_substantive', 'blocked']}
        return {**ledger, 'units': units, 'coverage': {**counts, 'total': len(ledger['units']),
                    'facts_not_recorded_supported': sum(c['status'] != 'recorded_supported' for u in units for c in u['support_checks']),
                    'text_recording_complete': counts['pending'] == 0 and counts['blocked'] == 0,
                    'semantic_verified': False},
                'model': fact_model.status(), 'limitations': ['原文单元按空行或 PDF 文本块分隔，段落边界未经语义核验',
                    '引文匹配仅验证文字和位置，不能证明陈述被原文支持', '作者报告与阅读推断均为待核验底稿，不是审稿结论',
                    '当前为文本研读；未执行图像理解、表格单元格解析或外部实验复现']}

    def get(self, did, sid, reading_revision):
        packet = self.packet(did, sid, reading_revision)
        lid = self.ledger_id(packet)
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.current_reading(db, packet)
            row = db.execute('SELECT payload_json FROM fact_versions WHERE ledger_id=? ORDER BY revision DESC LIMIT 1', (lid,)).fetchone()
            if row:
                ledger = json.loads(row[0])
            else:
                ledger = {'id': lid, 'revision': 1, 'document_version_id': did, 'extraction_id': packet['extraction_id'],
                          'unitization_version': UNITIZATION_VERSION,
                          'reading_revision': reading_revision, 'section': packet['section'], 'units': paragraph_units(packet),
                          'source_preparation': packet.get('source_preparation', {}),
                          'materials': [{'id': m['id'], 'label': m['label'], 'pdf_page': m['pdf_page'],
                                         'coverage': m['coverage'], 'semantic_status': 'not_reviewed'} for m in packet['materials']],
                          'exclusions': packet['exclusions'], 'created_at': now()}
                self.save(db, ledger)
            db.commit()
        return self.present(ledger)

    @staticmethod
    def latest(db, lid):
        return json.loads(db.execute('SELECT payload_json FROM fact_versions WHERE ledger_id=? ORDER BY revision DESC LIMIT 1', (lid,)).fetchone()[0])

    @staticmethod
    def unit(ledger, uid):
        result = next((u for u in ledger['units'] if u['id'] == uid), None)
        if result is None:
            raise EvidenceError(422, 'unit_not_in_section', '原文单元不属于当前分节和阅读版本')
        return result

    @staticmethod
    def validate_draft(draft, unit, packet, allowed_ids=None):
        blocks = {b['id']: b for b in packet['blocks']}
        for fact in draft.facts:
            primary = False
            for citation in fact.citations:
                b = blocks.get(citation.block_id)
                if (b is None or (allowed_ids is not None and b['id'] not in allowed_ids) or citation.end <= citation.start
                        or citation.end > len(b['text']) or b['text'][citation.start:citation.end] != citation.quote
                        or not contains_span(packet, citation.block_id, citation.start, citation.end)):
                    raise EvidenceError(422, 'invalid_fact_citation', '引文必须逐字匹配本次提供的原文块、起止位置与文档版本；整段保存已拒绝')
                spans = unit.get('source_spans', [unit])
                if any(b['id'] == span['block_id'] and span['start'] <= citation.start < citation.end <= span['end'] for span in spans):
                    primary = True
            if not primary:
                raise EvidenceError(422, 'target_unit_not_supported', '每条事实至少需要一条位于当前原文单元内的引文')

    @staticmethod
    def locate_model_quotes(draft, packet, allowed_ids):
        """Repair model character counting only when the exact quote has one location.

        Never repair quote text, change block ownership or guess among repeated text.
        """
        blocks = {b['id']: b for b in packet['blocks'] if b['id'] in allowed_ids}
        corrections = []
        for fact in draft.facts:
            for c in fact.citations:
                b = blocks.get(c.block_id)
                if b is None or b['text'][c.start:c.end] == c.quote:
                    continue
                positions = quote_positions(packet, c.block_id, c.quote)
                if len(positions) != 1:
                    continue  # Strict validation will reject the unresolved locator.
                start = positions[0]
                corrections.append({'block_id': c.block_id, 'old_start': c.start, 'old_end': c.end,
                                    'new_start': start, 'new_end': start+len(c.quote)})
                c.start, c.end = start, start+len(c.quote)
        return corrections

    @staticmethod
    def parse_model_draft(raw, packet, allowed_ids):
        """Locate missing model offsets without asking the model to count text.

        Duplicate occurrences, foreign blocks and non-verbatim quotes remain errors.
        Explicit legacy offsets still pass through the existing exact/repair checks.
        """
        candidate = ModelParagraphDraft.model_validate(raw)
        blocks = {b['id']: b for b in packet['blocks'] if b['id'] in allowed_ids}
        data = candidate.model_dump(mode='json')
        locations = []
        for fact in data['facts']:
            for c in fact['citations']:
                if c['start'] is not None and c['end'] is not None:
                    continue
                b = blocks.get(c['block_id'])
                if b is None:
                    raise EvidenceError(422, 'invalid_fact_citation', '模型引文不属于实际提供的来源块')
                positions = quote_positions(packet, c['block_id'], c['quote'])
                if not positions:
                    raise EvidenceError(422, 'invalid_fact_citation', '模型引文未逐字出现在本次提供的分节范围中')
                if len(positions) > 1:
                    raise EvidenceError(422, 'ambiguous_fact_citation', '模型引文有多个位置，未自动选择；请提供更完整的唯一原文')
                start = positions[0]
                c.update(start=start, end=start+len(c['quote']))
                locations.append({'block_id': c['block_id'], 'start': c['start'], 'end': c['end'], 'method': 'unique_exact_match'})
        return ParagraphDraft.model_validate(data), locations

    @staticmethod
    def previous(db, lid, key, fingerprint):
        request = db.execute('SELECT * FROM fact_requests WHERE ledger_id=? AND request_key=?', (lid, key)).fetchone()
        if not request:
            return None
        if request['fingerprint'] != fingerprint:
            raise EvidenceError(409, 'idempotency_conflict', '同一请求标识已经用于不同内容')
        if request['state'] == 'succeeded':
            row = db.execute('SELECT payload_json FROM fact_versions WHERE ledger_id=? AND revision=?', (lid, request['result_revision'])).fetchone()
            return json.loads(row[0])
        if request['state'] == 'pending':
            raise EvidenceError(409, 'fact_request_in_progress', '该生成请求仍在处理或结果未知，请先刷新；不会自动重复模型调用')
        raise EvidenceError(409, 'fact_request_failed', '该请求已失败，既有底稿未覆盖；请核查错误后以新操作重试，服务商可能已经计费')

    @staticmethod
    def apply_draft(ledger, uid, draft, producer):
        unit = FactWorksheet.unit(ledger, uid)
        unit['draft'] = draft.model_dump(mode='json')
        unit['producer'] = {**producer, 'at': now()}
        unit['citation_check'] = 'exact_text_and_offsets_located' if draft.facts else 'no_fact_citations'
        unit['semantic_check'] = 'not_verified'
        unit.pop('support_reviews', None)  # Old review remains only in immutable prior revisions.
        ledger['revision'] += 1

    def review_support(self, did, sid, command, key):
        self.get(did, sid, command.reading_revision)
        packet = self.packet(did, sid, command.reading_revision)
        lid = self.ledger_id(packet)
        fingerprint = digest(('support:' + command.model_dump_json()).encode())
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.current_reading(db, packet)
            previous = self.previous(db, lid, key, fingerprint)
            if previous:
                return self.present(previous)
            ledger = self.latest(db, lid)
            if ledger['revision'] != command.revision:
                raise EvidenceError(409, 'stale_fact_revision', '底稿已更新，请刷新后重新核对陈述与证据')
            unit = self.unit(ledger, command.unit_id)
            facts = (unit.get('draft') or {}).get('facts', [])
            if command.fact_index >= len(facts):
                raise EvidenceError(422, 'fact_not_found', '当前段落没有该事实，不能保存复核')
            review = check_review(facts[command.fact_index], unit, packet, command)
            review['base_revision'] = command.revision
            unit.setdefault('support_reviews', {})[str(command.fact_index)] = review
            ledger['revision'] += 1
            self.save(db, ledger)
            db.execute('INSERT INTO fact_requests VALUES (?,?,?,?,?,?,?,?)',
                       (lid, key, fingerprint, 'succeeded', command.revision, ledger['revision'], None, now()))
            db.commit()
        return self.present(ledger)

    def update(self, did, sid, command, key):
        self.get(did, sid, command.reading_revision)
        packet = self.packet(did, sid, command.reading_revision)
        lid = self.ledger_id(packet)
        fingerprint = digest(('manual:' + command.model_dump_json()).encode())
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.current_reading(db, packet)
            previous = self.previous(db, lid, key, fingerprint)
            if previous:
                return self.present(previous)
            ledger = self.latest(db, lid)
            if ledger['revision'] != command.revision:
                raise EvidenceError(409, 'stale_fact_revision', '底稿已更新，请刷新后重新编辑')
            unit = self.unit(ledger, command.unit_id)
            self.validate_draft(command.draft, unit, packet)
            self.apply_draft(ledger, unit['id'], command.draft, {'kind': 'local_user', 'identity_authenticated': False})
            self.save(db, ledger)
            db.execute('INSERT INTO fact_requests VALUES (?,?,?,?,?,?,?,?)',
                       (lid, key, fingerprint, 'succeeded', command.revision, ledger['revision'], None, now()))
            db.commit()
        return self.present(ledger)

    def reserve(self, did, sid, command, key):
        self.get(did, sid, command.reading_revision)
        packet = self.packet(did, sid, command.reading_revision)
        lid = self.ledger_id(packet)
        fingerprint = digest(('generate:' + command.model_dump_json()).encode())
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.current_reading(db, packet)
            previous = self.previous(db, lid, key, fingerprint)
            if previous:
                return {'previous': previous}
            ledger = self.latest(db, lid)
            if ledger['revision'] != command.revision:
                raise EvidenceError(409, 'stale_fact_revision', '底稿已更新，请刷新后再生成')
            unit = self.unit(ledger, command.unit_id)
            if unit['draft'] is not None:
                raise EvidenceError(409, 'draft_already_exists', '本段已有底稿，模型生成不会覆盖已有记录；请先阅读或手动修订')
            if not fact_model.status()['configured']:
                raise EvidenceError(409, 'model_not_configured', '尚未配置模型；可先手动建立事实底稿，当前没有发出模型请求')
            pending = db.execute("SELECT * FROM fact_requests WHERE ledger_id=? AND state='pending'", (lid,)).fetchone()
            if pending:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(pending['created_at'])).total_seconds()
                if age > 180:
                    db.execute("UPDATE fact_requests SET state='failed',error_code='call_outcome_unknown' WHERE ledger_id=? AND request_key=?", (lid, pending['request_key']))
                    db.commit()
                    raise EvidenceError(409, 'call_outcome_unknown', '上次请求中断或超时，远端结果与计费未知；已解除占用，请核查后再发起新操作')
                raise EvidenceError(409, 'fact_generation_busy', '本节已有生成请求进行中，请等待完成或稍后刷新')
            payload, allowed_ids = self.build_payload(packet, unit)
            db.execute('INSERT INTO fact_requests VALUES (?,?,?,?,?,?,?,?)', (lid, key, fingerprint, 'pending', ledger['revision'], None, None, now()))
            db.commit()
        return {'packet': packet, 'ledger_id': lid, 'payload': payload, 'allowed_ids': allowed_ids,
                'profile': fact_model.status()}

    @staticmethod
    def build_payload(packet, unit):
        """Pure preview builder: no model request, reservation or state mutation."""
        blocks = scoped_blocks(packet)
        target_ids = {s['block_id'] for s in unit.get('source_spans', [unit])}
        indices = [i for i, b in enumerate(blocks) if b['id'] in target_ids]
        context = blocks[max(0, min(indices)-1):max(indices)+2]
        if len(unit['text']) > 12000 or sum(len(b['text']) for b in context) > 26000:
            raise EvidenceError(422, 'paragraph_context_too_large', '本段及相邻文本过长，当前不静默截断，请先手动研读或改善分段')
        payload = {'section_title': packet['section']['title'], 'target': {k: unit[k] for k in ['id', 'block_id', 'start', 'end', 'text', 'source_spans']},
                   'source_preparation': packet.get('source_preparation', {}),
                   'context_blocks': [{'id': b['id'], 'pdf_page': b['region']['pdf_page'], 'text': b['text'],
                                       'source_start': b['_source_start'], 'source_end': b['_source_end']} for b in context],
                   'scope': 'target_and_adjacent_blocks_only', 'materials': packet['materials'],
                   'notice': '材料只包含位置及图注，未提供原图视觉语义；其他节没有提供，跨节推断必须保留未知'}
        if len(json.dumps(payload, ensure_ascii=False)) > 65000:
            raise EvidenceError(422, 'paragraph_context_too_large', '本次来源与材料上下文过长，未截断或发送，请先手动研读')
        return payload, {b['id'] for b in context}

    def fail(self, lid, key, code):
        with self.store.connection() as db:
            db.execute("UPDATE fact_requests SET state='failed',error_code=? WHERE ledger_id=? AND request_key=? AND state='pending'", (code, lid, key))
            db.commit()

    def save_diagnostics(self, lid, key, diagnostics):
        # Values are an allowlisted transport summary; never include response text,
        # request headers, exception messages, URLs or credentials.
        safe = {k: diagnostics[k] for k in ['stage', 'error_type', 'finish_reason', 'thinking_mode'] if isinstance(diagnostics.get(k), str)
                and re.fullmatch(r'[A-Za-z_]{1,60}', diagnostics[k])}
        safe.update({k: diagnostics[k] for k in ['http_status', 'content_characters', 'retry_attempts', 'max_output_tokens', 'reasoning_content_characters'] if type(diagnostics.get(k)) is int})
        if type(diagnostics.get('reasoning_content_present')) is bool:
            safe['reasoning_content_present'] = diagnostics['reasoning_content_present']
        if type(diagnostics.get('elapsed_seconds')) in (int, float):
            safe['elapsed_seconds'] = diagnostics['elapsed_seconds']
        usage = diagnostics.get('usage') or {}
        safe['usage'] = {k: usage[k] for k in ['prompt_tokens', 'completion_tokens', 'total_tokens', 'reasoning_tokens'] if type(usage.get(k)) is int}
        path = self.store.root / 'diagnostics' / f'{digest((lid+":"+key).encode())}.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding='utf-8')

    def complete(self, did, sid, command, key, reservation, draft):
        packet = self.packet(did, sid, command.reading_revision)  # Reject changed reading structure and corrupt sources after network wait.
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.current_reading(db, packet)
            ledger = self.latest(db, reservation['ledger_id'])
            if ledger['revision'] != command.revision:
                raise EvidenceError(409, 'stale_fact_revision', '生成期间底稿发生修改，模型结果未覆盖已有记录')
            request = db.execute('SELECT state FROM fact_requests WHERE ledger_id=? AND request_key=?', (ledger['id'], key)).fetchone()
            if request is None or request[0] != 'pending':
                raise EvidenceError(409, 'fact_request_not_active', '生成请求已失效，结果未写入')
            unit = self.unit(ledger, command.unit_id)
            corrections = self.locate_model_quotes(draft, packet, reservation['allowed_ids'])
            self.validate_draft(draft, unit, packet, reservation['allowed_ids'])
            self.apply_draft(ledger, unit['id'], draft, {'kind': 'model', 'model_name': reservation['profile']['model_name'],
                'prompt_version': fact_model.PROMPT_VERSION, 'context_block_ids': sorted(reservation['allowed_ids']),
                'context_sha256': digest(json.dumps(reservation['payload'], ensure_ascii=False, sort_keys=True).encode()),
                'usage': reservation.get('usage', {}), 'finish_reason': reservation.get('finish_reason', 'not_recorded'),
                'program_located_citations': reservation.get('program_located_citations', []),
                'citation_offset_corrections': corrections})
            self.save(db, ledger)
            db.execute("UPDATE fact_requests SET state='succeeded',result_revision=? WHERE ledger_id=? AND request_key=?", (ledger['revision'], ledger['id'], key))
            db.commit()
        return self.present(ledger)

    async def generate(self, did, sid, command, key):
        reservation = await run_in_threadpool(self.reserve, did, sid, command, key)
        if 'previous' in reservation:
            return self.present(reservation['previous'])
        try:
            raw = await fact_model.generate(reservation['payload'])
            reservation['usage'] = getattr(raw, 'usage', {})
            reservation['finish_reason'] = getattr(raw, 'finish_reason', 'not_recorded')
            draft, locations = self.parse_model_draft(raw, reservation['packet'], reservation['allowed_ids'])
            reservation['program_located_citations'] = locations
            return await run_in_threadpool(self.complete, did, sid, command, key, reservation, draft)
        except ValidationError:
            await run_in_threadpool(self.fail, reservation['ledger_id'], key, 'invalid_fact_model_schema')
            raise EvidenceError(502, 'invalid_fact_model_schema', '模型输出不符合事实底稿结构，未保存该段；不会把格式错误当成阅读完成')
        except EvidenceError as exc:
            await run_in_threadpool(self.fail, reservation['ledger_id'], key, exc.code)
            if isinstance(exc, fact_model.ModelFailure):
                await run_in_threadpool(self.save_diagnostics, reservation['ledger_id'], key, exc.diagnostics)
            raise

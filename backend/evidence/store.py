import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pymupdf

from backend.evidence.models import BlockPage, EvidenceDocument, ExtractionSummary, MaterialCandidate, SourceBlock
from backend.evidence.parser import PARSER_VERSION, EvidenceError, digest, inspect_pdf, normalize, parse_document
from backend.evidence.titles import identify_title
from design.contracts import DocumentVersion

PROJECT = Path(__file__).resolve().parents[2]
APPLICATION_ID = 0x454D4150


def now():
    return datetime.now(timezone.utc).isoformat()


class EvidenceStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.objects = self.root / 'objects'
        self.objects.mkdir(exist_ok=True)
        self.db_path = self.root / 'evidence.sqlite3'
        self._initialize()

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
        finally:
            db.close()

    @staticmethod
    def _statements(db, source):
        statement = ''
        for line in source.splitlines(keepends=True):
            statement += line
            if sqlite3.complete_statement(statement):
                db.execute(statement)
                statement = ''
        if statement.strip():
            raise RuntimeError('Incomplete SQL migration')

    def _initialize(self):
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('BEGIN IMMEDIATE')
            try:
                version = db.execute('PRAGMA user_version').fetchone()[0]
                app_id = db.execute('PRAGMA application_id').fetchone()[0]
                if version == 0 and app_id == 0:
                    if db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchone():
                        raise RuntimeError('Refusing to initialize an unrelated database')
                    self._statements(db, (PROJECT / 'design' / 'schema.sql').read_text(encoding='utf-8'))
                    self._statements(db, (Path(__file__).parent / 'storage.sql').read_text(encoding='utf-8'))
                    db.execute(f'PRAGMA application_id={APPLICATION_ID}')
                    db.execute('PRAGMA user_version=1')
                    version = 1
                elif version not in (1, 2, 3, 4, 5) or app_id != APPLICATION_ID:
                    raise RuntimeError('Unsupported evidence database version; no automatic overwrite performed')
                if version == 1:
                    self._statements(db, (Path(__file__).parent / 'reading.sql').read_text(encoding='utf-8'))
                    db.execute('PRAGMA user_version=2')
                    version = 2
                if version == 2:
                    self._statements(db, (Path(__file__).parent / 'facts.sql').read_text(encoding='utf-8'))
                    db.execute('PRAGMA user_version=3')
                    version = 3
                if version == 3:
                    self._statements(db, (Path(__file__).parent / 'workflow.sql').read_text(encoding='utf-8'))
                    db.execute('PRAGMA user_version=4')
                    version = 4
                if version == 4:
                    self._statements(db, (Path(__file__).parent / 'paper_reports.sql').read_text(encoding='utf-8'))
                    db.execute('PRAGMA user_version=5')
                db.commit()
            except Exception:
                db.rollback()
                raise

    @staticmethod
    def _request(db, scope, key, fingerprint):
        row = db.execute('SELECT * FROM evidence_requests WHERE scope=? AND request_key=?', (scope, key)).fetchone()
        if row and row['fingerprint'] != fingerprint:
            raise EvidenceError(409, 'idempotency_conflict', '同一请求标识已用于不同内容，请使用新标识')
        return row

    @staticmethod
    def _document(row):
        return DocumentVersion(id=row['id'], sha256=row['sha256'], page_count=row['page_count'],
                               source_ref=row['source_ref'], material_role=row['material_role'])

    def _row(self, db, document_id):
        if not re.fullmatch(r'doc_[a-f0-9]{64}_(main|supplement)', document_id):
            raise EvidenceError(404, 'document_not_found', '文档版本不存在')
        row = db.execute('SELECT * FROM document_versions WHERE id=?', (document_id,)).fetchone()
        if row is None:
            raise EvidenceError(404, 'document_not_found', '文档版本不存在')
        return row

    def register(self, content, filename, role, key):
        if role not in {'main', 'supplement'}:
            raise EvidenceError(422, 'invalid_material_role', '材料类型必须为主论文或补充材料')
        page_count, title = inspect_pdf(content)
        sha = digest(content)
        document_id = f'doc_{sha}_{role}'
        fingerprint = digest(f'{sha}|{role}'.encode())
        filename = (filename or 'paper.pdf').replace('\\', '/').split('/')[-1][:250] or 'paper.pdf'
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                previous = self._request(db, 'register', key, fingerprint)
                if previous:
                    result = self._document(self._row(db, previous['document_version_id']))
                    db.commit()
                    return result
                destination = self.objects / f'{sha}.pdf'
                if destination.exists():
                    if digest(destination.read_bytes()) != sha:
                        raise EvidenceError(409, 'source_integrity_failed', '已有原件校验失败，请先核查存储，当前不覆盖证据')
                else:
                    temp = self.objects / f'{uuid.uuid4().hex}.tmp'
                    try:
                        with temp.open('xb') as stream:
                            stream.write(content)
                            stream.flush()
                            os.fsync(stream.fileno())
                        temp.replace(destination)
                    finally:
                        temp.unlink(missing_ok=True)
                db.execute('INSERT OR IGNORE INTO document_versions VALUES (?,?,?,?,?,?)',
                    (document_id, sha, page_count, 'local-sha256:' + sha, role, now()))
                db.execute('INSERT OR IGNORE INTO evidence_metadata VALUES (?,?,?)', (document_id, filename, title or Path(filename).stem))
                db.execute('INSERT INTO evidence_requests VALUES (?,?,?,?)', ('register', key, fingerprint, document_id))
                result = self._document(self._row(db, document_id))
                db.commit()
                return result
            except Exception:
                db.rollback()
                raise

    def file_path(self, document_id):
        with self.connection() as db:
            row = self._row(db, document_id)
            path = self.objects / f"{row['sha256']}.pdf"
            expected = row['sha256']
        if not path.is_file() or digest(path.read_bytes()) != expected:
            raise EvidenceError(409, 'source_integrity_failed', '原始文件缺失或哈希不一致，已阻止读取该证据')
        return path

    @staticmethod
    def _summary(row):
        return ExtractionSummary.model_validate_json(row['summary_json']) if row else None

    def detail(self, document_id):
        with self.connection() as db:
            row = self._row(db, document_id)
            metadata = db.execute('SELECT * FROM evidence_metadata WHERE document_version_id=?', (document_id,)).fetchone()
            parsed = db.execute('SELECT * FROM evidence_extractions WHERE document_version_id=? AND parser_version=?', (document_id, PARSER_VERSION)).fetchone()
            front = [json.loads(r[0]) for r in db.execute(
                'SELECT payload_json FROM evidence_blocks WHERE extraction_id=? AND pdf_page<=2', (parsed['id'],))] if parsed else []
            identity = identify_title(front, metadata['filename'], metadata['title'])
            return EvidenceDocument(document=self._document(row), filename=metadata['filename'], title=identity['text'],
                                    extraction=self._summary(parsed), title_identity=identity)

    def list_documents(self, limit, offset):
        with self.connection() as db:
            ids = [row[0] for row in db.execute('SELECT id FROM document_versions ORDER BY created_at DESC,id LIMIT ? OFFSET ?', (limit, offset))]
            total = db.execute('SELECT count(*) FROM document_versions').fetchone()[0]
        return {'items': [self.detail(did).model_dump(mode='json') for did in ids], 'total': total}

    def parse(self, document_id, key):
        fingerprint = digest(f'{document_id}|{PARSER_VERSION}'.encode())
        with self.connection() as db:
            self._row(db, document_id)
            self._request(db, 'parse', key, fingerprint)
        # Always verify source integrity, including requests that reuse a cached extraction.
        content = self.file_path(document_id).read_bytes()
        with self.connection() as db:
            cached = db.execute('SELECT * FROM evidence_extractions WHERE document_version_id=? AND parser_version=?', (document_id, PARSER_VERSION)).fetchone()
        extraction = None if cached else parse_document(content, document_id)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                self._request(db, 'parse', key, fingerprint)
                existing = db.execute('SELECT * FROM evidence_extractions WHERE document_version_id=? AND parser_version=?', (document_id, PARSER_VERSION)).fetchone()
                if not existing:
                    summary = ExtractionSummary(id=extraction.id, parser_version=extraction.parser_version,
                        status=extraction.status, pages=extraction.pages, block_count=len(extraction.blocks),
                        material_count=len(extraction.materials), warnings=extraction.warnings)
                    db.execute('INSERT INTO evidence_extractions VALUES (?,?,?,?)', (extraction.id, document_id, PARSER_VERSION, summary.model_dump_json()))
                    db.executemany('INSERT INTO evidence_pages VALUES (?,?,?)', [(extraction.id, p.pdf_page, p.model_dump_json()) for p in extraction.pages])
                    db.executemany('INSERT INTO evidence_blocks VALUES (?,?,?,?,?,?,?)',
                        [(b.id, extraction.id, document_id, b.region.pdf_page, b.order_in_page, normalize(b.text), b.model_dump_json()) for b in extraction.blocks])
                    db.executemany('INSERT INTO evidence_materials VALUES (?,?,?,?,?)',
                        [(m.id, extraction.id, m.pdf_page, m.kind, m.model_dump_json()) for m in extraction.materials])
                db.execute('INSERT OR IGNORE INTO evidence_requests VALUES (?,?,?,?)', ('parse', key, fingerprint, document_id))
                saved = db.execute('SELECT * FROM evidence_extractions WHERE document_version_id=? AND parser_version=?', (document_id, PARSER_VERSION)).fetchone()
                result = self._summary(saved)
                db.commit()
                return result
            except Exception:
                db.rollback()
                raise

    def _extraction_id(self, db, document_id):
        self._row(db, document_id)
        row = db.execute('SELECT id FROM evidence_extractions WHERE document_version_id=? AND parser_version=?', (document_id, PARSER_VERSION)).fetchone()
        if row is None:
            raise EvidenceError(409, 'not_parsed', '该版本尚未完成结构解析')
        return row[0]

    def blocks(self, document_id, page, query, limit, offset):
        self.file_path(document_id)
        with self.connection() as db:
            extraction_id = self._extraction_id(db, document_id)
            clauses, args = ['extraction_id=?'], [extraction_id]
            if page is not None:
                clauses.append('pdf_page=?')
                args.append(page)
            if query:
                clauses.append('instr(normalized_text,?)>0')
                args.append(normalize(query))
            where = ' AND '.join(clauses)
            total = db.execute(f'SELECT count(*) FROM evidence_blocks WHERE {where}', args).fetchone()[0]
            rows = db.execute(f'SELECT payload_json FROM evidence_blocks WHERE {where} ORDER BY pdf_page,order_in_page LIMIT ? OFFSET ?', [*args, limit, offset]).fetchall()
            items = [self._verified_block(r[0], document_id) for r in rows]
            return BlockPage(items=items, total=total, limit=limit, offset=offset)

    @staticmethod
    def _verified_block(payload, document_id):
        block = SourceBlock.model_validate_json(payload)
        if (digest(block.text.encode()) != block.text_sha256 or block.region.text_sha256 != block.text_sha256
                or block.region.document_version_id != document_id or block.region.id != block.id):
            raise EvidenceError(409, 'block_integrity_failed', '文本块内容、哈希或来源版本不一致')
        return block

    def block(self, document_id, block_id):
        self.file_path(document_id)
        with self.connection() as db:
            extraction_id = self._extraction_id(db, document_id)
            row = db.execute('SELECT payload_json FROM evidence_blocks WHERE id=? AND extraction_id=?', (block_id, extraction_id)).fetchone()
            if not row:
                raise EvidenceError(404, 'block_not_found', '该文本块不属于此文档版本')
            return self._verified_block(row[0], document_id)

    def materials(self, document_id):
        self.file_path(document_id)
        with self.connection() as db:
            extraction_id = self._extraction_id(db, document_id)
            return [MaterialCandidate.model_validate_json(r[0]) for r in db.execute('SELECT payload_json FROM evidence_materials WHERE extraction_id=? ORDER BY pdf_page,id', (extraction_id,))]

    def render(self, document_id, page_number, scale, block_id=None):
        path = self.file_path(document_id)
        block = self.block(document_id, block_id) if block_id else None
        if block:
            page_number = block.region.pdf_page
        with pymupdf.open(path) as doc:
            if not 1 <= page_number <= len(doc):
                raise EvidenceError(404, 'page_not_found', '页码不存在')
            page = doc[page_number - 1]
            page.set_rotation(0)
            clip = pymupdf.Rect(block.region.bbox) if block else page.rect
            # Bound bitmap allocations even for unusual PDF page dimensions.
            if clip.width * clip.height * scale * scale > 16000000:
                raise EvidenceError(422, 'render_limit', '该区域预览过大，请降低缩放比例')
            return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip, alpha=False).tobytes('png')

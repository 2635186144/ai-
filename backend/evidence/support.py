"""Local claim/evidence review records, never an automatic entailment oracle."""
import json
import unicodedata
from typing import Literal

from pydantic import Field

from design.contracts import Contract
from backend.evidence.fact_models import FactCitation
from backend.evidence.parser import EvidenceError, digest
from backend.evidence.store import now
from backend.evidence.source_scope import contains_span


class SupportPart(Contract):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=3000)
    kind: Literal['positive', 'absence'] = 'positive'
    verdict: Literal['pending', 'supported', 'partial', 'unsupported', 'contradicted']
    reason: str = Field(min_length=1, max_length=3000)
    citation_indices: list[int] = Field(default_factory=list, max_length=12)
    checked_scope: list[FactCitation] = Field(default_factory=list, max_length=100)


class SupportCommand(Contract):
    reading_revision: int = Field(ge=1)
    revision: int = Field(ge=1)
    unit_id: str
    fact_index: int = Field(ge=0)
    actor: Literal['local_user', 'assistant_curated'] = 'local_user'
    parts: list[SupportPart] = Field(min_length=1, max_length=30)


def fact_hash(fact):
    return digest(json.dumps(fact, sort_keys=True, ensure_ascii=False).encode())


def meaningful(character):
    return not character.isspace() and not unicodedata.category(character).startswith('P')


def check_review(fact, unit, packet, command):
    """Validate review coverage/anchors, not whether a reviewer is scientifically right."""
    blocks = {b['id']: b for b in packet['blocks']}
    covered = set()
    for part in command.parts:
        if not part.reason.strip() or part.end <= part.start or fact['statement'][part.start:part.end] != part.text or part.end > len(fact['statement']):
            raise EvidenceError(422, 'invalid_claim_part', '陈述分项必须逐字对应当前陈述范围，且必须填写判断理由')
        if not any(meaningful(c) for c in part.text):
            raise EvidenceError(422, 'invalid_claim_part', '陈述分项不能只有标点或空白')
        indices = part.citation_indices
        if len(set(indices)) != len(indices) or any(i < 0 or i >= len(fact['citations']) for i in indices):
            raise EvidenceError(422, 'invalid_support_reference', '复核只能关联当前事实中已保存的引文编号')
        if part.verdict in ('supported', 'partial', 'contradicted') and not indices:
            raise EvidenceError(422, 'support_evidence_required', '支持、部分支持或矛盾判断必须关联已有引文；引用存在仍不证明语义支持')
        for citation in part.checked_scope:
            b = blocks.get(citation.block_id)
            if (b is None or citation.end <= citation.start or citation.end > len(b['text'])
                    or b['text'][citation.start:citation.end] != citation.quote
                    or not contains_span(packet, citation.block_id, citation.start, citation.end)):
                raise EvidenceError(422, 'invalid_checked_scope', '已检查范围必须逐字对应本节来源块')
        if part.kind == 'absence' and part.verdict != 'pending':
            # Checking absence in this paragraph requires its complete text, not two end quotes.
            for span in unit.get('source_spans', [unit]):
                positions = set()
                for c in part.checked_scope:
                    if c.block_id == span['block_id']:
                        positions.update(range(max(span['start'], c.start), min(span['end'], c.end)))
                if not set(range(span['start'], span['end'])).issubset(positions):
                    raise EvidenceError(422, 'absence_scope_incomplete', '缺失性判断必须核对完整目标段，不能只凭首尾引文；结论仅限本段')
        positions = set(range(part.start, part.end))
        if covered & positions:
            raise EvidenceError(422, 'overlapping_claim_parts', '陈述分项范围不可重叠，请拆成独立可核对的部分')
        covered.update(positions)
    required = {i for i, c in enumerate(fact['statement']) if meaningful(c)}
    complete = required.issubset(covered)
    all_supported = complete and all(p.verdict == 'supported' for p in command.parts)
    verdict = ('recorded_supported' if all_supported else 'pending' if any(p.verdict == 'pending' for p in command.parts)
               else 'needs_revision')
    if not complete:
        verdict = 'incomplete_claim_coverage'
    return {'fact_sha256': fact_hash(fact), 'claim_coverage_complete': complete, 'status': verdict,
            'parts': [p.model_dump(mode='json') for p in command.parts],
            'producer': {'kind': command.actor, 'identity_authenticated': False, 'at': now()},
            'semantic_verified': False, 'scope': 'current_paragraph_and_cited_section_text_only'}


def support_checks(unit):
    result = []
    for index, fact in enumerate((unit.get('draft') or {}).get('facts', [])):
        review = unit.get('support_reviews', {}).get(str(index))
        if review and review['fact_sha256'] == fact_hash(fact):
            result.append({'fact_index': index, **review})
        else:
            result.append({'fact_index': index, 'status': 'not_reviewed', 'claim_coverage_complete': False,
                           'parts': [], 'semantic_verified': False})
    return result

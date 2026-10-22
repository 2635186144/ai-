"""Source-backed guidance checks; model self-review is not independent proof."""
import math
import re
import unicodedata

from backend.evidence.parser import EvidenceError, digest
from functools import lru_cache

VERSION = 'guidance-support-v1'
ABSENCE = re.compile(r'(?:作者|论文|文中|原文).{0,16}(?:未(?:给出|说明|定义|报告|交代|提供)|没有|缺少)|未(?:给出|说明|定义|报告|交代|提供)|缺少|未明确')
CONFLICT = re.compile(r'矛盾|不一致|冲突|相反')


@lru_cache(maxsize=4096)
def terms(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    english = re.findall(r'[a-z][a-z0-9_]{2,}', text)
    chinese = re.findall(r'[\u4e00-\u9fff]+', text)
    return frozenset(english + [s[i:i+2] for s in chinese for i in range(len(s)-1)])


def related_sources(text, blocks, limit=3):
    query = terms(text)
    formula_query = bool(re.search(r'公式|计算|指标|比率|关闭率|definition|rate',text,re.I))
    hits = []
    for block in blocks:
        shared = query & terms(block['text'])
        formula = formula_query and len(block['text'])<350 and bool(re.search(r'issues_|fork|star|=|÷|∑', block['text'],re.I))
        if shared or formula:
            score = len(shared) / math.sqrt(max(len(terms(block['text'])), 8))
            # Explicit formula questions also inspect table-like formula blocks,
            # even if the selected reading note omitted that table.
            if formula:
                score += 1
            hits.append((score, block))
    hits.sort(key=lambda item: (-item[0], item[1]['region']['pdf_page'], item[1]['order_in_page']))
    return [b for _, b in hits[:limit]]


def excerpt(block, query='', maximum=1800):
    text = block['text']
    start = 0
    if len(text) > maximum and query:
        matches = [text.casefold().find(t) for t in terms(query) if t in text.casefold()]
        start = max(0, min(matches, default=0)-120)
    end = min(len(text), start+maximum)
    return {'id': 'support_'+digest(f'{block["id"]}:{start}:{end}'.encode())[:20],
            'block_id': block['id'], 'pdf_page': block['region']['pdf_page'],
            'start': start, 'end': end, 'quote': text[start:end]}


def source_pack(blocks, notes, previous_parts=(), max_characters=18000):
    queries = []
    for note in notes:
        queries.extend(note.get('unknowns', []))
        for topic in note.get('topics', []):
            queries.extend(topic.get('limitations', []))
            queries.append(topic.get('heading', ''))
    for part in previous_parts:
        queries.extend(q for q in part.get('open_questions', []))
        queries.extend(item['title'] for item in part.get('core_content', []))
    # Each query gets a turn before adding second matches. No silent claim that
    # this lexical selection searched every definition semantically.
    queries = list(dict.fromkeys(q for q in queries if q.strip()))
    hits = [(q, related_sources(q, blocks, 2)) for q in queries[:80]]
    selected, seen, size = [], set(), 0
    for rank in range(2):
        for query, matches in hits:
            if len(matches) <= rank:
                continue
            value = excerpt(matches[rank], query)
            if value['id'] in seen or size+len(value['quote']) > max_characters:
                continue
            selected.append(value);seen.add(value['id']);size += len(value['quote'])
    return {'version': VERSION, 'scope': 'selected_original_text_only',
            'selection': 'lexical_matches_to_reading_unknowns_and_topics', 'sources': selected,
            'source_characters': size, 'total_blocks': len(blocks),
            'selected_blocks': len({s['block_id'] for s in selected}),
            'query_count':min(80,len(queries)), 'omitted_query_count':max(0,len(queries)-80),
            'all_document_text_checked': False,
            'notice': '仅相关原文片段，不是完整全文，不提供图像；未命中不能证明原文没有。'}


def guidance_targets(report):
    result = {k: report[k]['text'] for k in ('conclusion', 'limitations') if k in report}
    result.update({f'open_questions.{i}': q for i, q in enumerate(report.get('open_questions', []))})
    return result


def validate_checks(result, evidence, pack=None):
    checks = result.get('guidance_checks', [])
    targets = guidance_targets(result)
    seen = set()
    available = {s['id'] for s in (pack or {}).get('sources', [])}
    for check in checks:
        path = check['target']
        if path not in targets:
            raise EvidenceError(422, 'report_support_invalid', '支持核查目标不存在，候选保留但不发布')
        seen.add(path)
        if any(eid not in evidence for eid in check['evidence_ids']):
            raise EvidenceError(422, 'report_support_invalid', '支持核查引用了不存在的笔记依据')
        if pack is not None and any(sid not in available for sid in check['source_ids']):
            raise EvidenceError(422, 'report_support_invalid', '支持核查引用了未提供的原文片段')
        if check['judgment'] in ('supported', 'partial') and not (check['evidence_ids'] or check['source_ids']):
            raise EvidenceError(422, 'report_support_invalid', '支持判断需要原文依据；位置存在仍不代表语义正确')
    if checks and seen != set(targets):
        raise EvidenceError(422, 'report_support_invalid', '关键结论、局限与疑问的核查记录不完整')


def support_view(report, pack=None, blocks=()):
    """Read-only view; never rewrite report prose or claim semantic approval."""
    by_target = {}
    for check in report.get('guidance_checks', []):
        by_target.setdefault(check['target'], []).append(check)
    sources = {s['id']: s for s in (pack or {}).get('sources', [])}
    entries = []
    evidence = {e['id']: e for e in report.get('evidence', [])}
    for path, text in guidance_targets(report).items():
        checks = by_target.get(path, [])
        risk = bool(ABSENCE.search(text) or CONFLICT.search(text))
        declared_criticism = any(c['kind']=='criticism' for c in checks)
        locations = [sources[s] for c in checks for s in c['source_ids'] if s in sources]
        locations += [evidence[e] for c in checks for e in c['evidence_ids'] if e in evidence]
        locations = list({(s.get('block_id'),s.get('pdf_page'),s.get('start'),s.get('end'),s.get('quote')):s for s in locations}.values())
        judgments={c['judgment'] for c in checks}
        kinds={c['kind'] for c in checks}
        if 'withdrawn' in judgments:
            status = 'withdrawn'
        elif not checks:
            status = 'not_reviewed'
        elif risk or declared_criticism or 'unresolved' in judgments:
            status = 'needs_check'
        elif judgments <= {'supported','partial'} and locations:
            status = 'model_partial' if 'partial' in judgments else 'model_supported'
        else:
            status = 'needs_check'
        candidates = [excerpt(b, text, 1000) for b in related_sources(text, blocks, 2)] if risk and not locations else []
        entries.append({'target': path, 'text': text, 'status': status,
                        'kind': next(iter(kinds)) if len(kinds)==1 else 'mixed' if kinds else 'unclassified',
                        'reason': '；'.join(c['reason'] for c in checks) if checks else '此项尚无有效的支持核查记录；保留生成文字，尚未作语义判断。',
                        'absence_or_conflict_language': risk,
                        'locations': locations, 'candidate_sources': candidates,
                        'model_judgment': next(iter(judgments)) if len(judgments)==1 else 'mixed' if judgments else None,
                        'parts':checks})
    return {'version': VERSION, 'reviewer': 'generating_model_self_review' if by_target else 'none',
            'scope': 'conclusion_limitations_and_discussion_questions_only',
            'semantic_verified': False, 'independent_review': False,
            'source_selection_is_exhaustive': False, 'entries': entries,
            'recorded_targets':len(by_target), 'total_targets':len(entries),
            'notice': '程序只核对目标与原文位置；支持关系为模型自查，不是独立语义验证。缺失或冲突性判断仍需回查。' if by_target else
                      '当前报告尚无有效自查记录；相关原文线索仅供回查，不据此判断解释正确或错误。'}

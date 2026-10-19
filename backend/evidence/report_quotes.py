"""Select immutable source ranges, never guess a position or rewrite source text."""
from backend.evidence.parser import EvidenceError, digest

CONTRACT = 'supplied-source-quote-ids-v1'


def quote_candidates(span):
    text=span['text'];cursor=0;items=[]
    while cursor<len(text):
        end=min(cursor+320,len(text))
        if end<len(text):
            boundaries=[text.rfind(char,cursor+100,end) for char in '\n。！？!?；;.']
            boundary=max(boundaries)
            if boundary>=cursor+100:end=boundary+1
        piece=text[cursor:end]
        lo=cursor+len(piece)-len(piece.lstrip());hi=cursor+len(piece.rstrip())
        if hi>lo:
            items.append({'quote_id':f'{span["span_id"]}:q{len(items)}','start':lo,'end':hi,'text':text[lo:hi]})
        cursor=end
    return items


def compact_whitespace(text):
    chars=[];positions=[];i=0
    def cjk(char):return '\u3400'<=char<='\u9fff' or '\U00020000'<=char<='\U0002FA1F'
    while i<len(text):
        if not text[i].isspace():chars.append(text[i]);positions.append(i);i+=1;continue
        end=i+1
        while end<len(text) and text[end].isspace():end+=1
        # Collapse runs, but never join English words or separate numeric tokens.
        if i and end<len(text) and not (cjk(text[i-1]) and cjk(text[end])):
            chars.append(' ');positions.append(i)
        i=end
    return ''.join(chars),positions


class QuoteValidationError(EvidenceError):
    def __init__(self,reason,span_id,quote,topic,citation):
        super().__init__(422,'report_quote_invalid','引文定位未通过，结果未发布；已保留待核验回复。可先本地重新校验，不会调用模型')
        self.safe_details={'reason':reason,'span_id':span_id[:120],'quote_characters':len(quote or ''),
                           'quote_sha256':digest((quote or '').encode()),'topic_index':topic,'citation_index':citation}


def locate(citation,span,topic_index,citation_index):
    def fail(reason):raise QuoteValidationError(reason,citation['span_id'],citation.get('quote') or '',topic_index,citation_index)
    if span is None:fail('unknown_span')
    if citation.get('quote_id'):
        candidate=next((q for q in quote_candidates(span) if q['quote_id']==citation['quote_id']),None)
        if candidate is None:fail('unknown_quote_id')
        return candidate['start'],candidate['end'],'supplied_quote_id'
    quote=citation['quote']
    if not quote.strip():fail('empty_quote')
    offset=span['text'].find(quote)
    if offset>=0:
        if span['text'].find(quote,offset+1)>=0:fail('ambiguous_exact_quote')
        return offset,offset+len(quote),'exact_quote'
    # Backward compatibility: only whitespace may differ. No punctuation, digit,
    # simplified/traditional, edit-distance or semantic replacements are accepted.
    target,_=compact_whitespace(quote);source,positions=compact_whitespace(span['text'])
    if not target:fail('empty_quote')
    offset=source.find(target)
    if offset<0:fail('quote_not_found')
    if source.find(target,offset+1)>=0:fail('ambiguous_whitespace_quote')
    return positions[offset],positions[offset+len(target)-1]+1,'unique_whitespace_mapping'

"""Section ranges use original Python/Unicode offsets; canonical blocks stay intact."""


def source_spans(packet):
    if 'source_spans' in packet:
        return packet['source_spans']
    return [{'block_id': b['id'], 'start': 0, 'end': len(b['text'])} for b in packet['blocks']]


def contains_span(packet, bid, start, end):
    return any(s['block_id'] == bid and s['start'] <= start < end <= s['end'] for s in source_spans(packet))


def scoped_blocks(packet):
    originals = {b['id']: b for b in packet['blocks']}
    return [{**originals[s['block_id']], 'text': originals[s['block_id']]['text'][s['start']:s['end']],
             '_source_start': s['start'], '_source_end': s['end']} for s in source_spans(packet)]


def quote_positions(packet, bid, quote):
    originals = {b['id']: b for b in packet['blocks']}
    if bid not in originals:
        return []
    raw = originals[bid]['text']
    positions = []
    for s in source_spans(packet):
        if s['block_id'] != bid:
            continue
        pos = raw.find(quote, s['start'], s['end'])
        while pos >= 0:
            positions.append(pos)
            if len(positions) > 1:
                return positions
            pos = raw.find(quote, pos+1, s['end'])
    return positions

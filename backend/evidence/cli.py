"""Import an explicitly chosen local PDF into the evidence development store."""
import argparse
import json
import uuid
from pathlib import Path

from backend.evidence.routes import get_store


def main():
    parser = argparse.ArgumentParser(description='EvidenceMap local PDF structure import, no model calls')
    parser.add_argument('pdf', type=Path)
    parser.add_argument('--role', choices=['main', 'supplement'], default='main')
    args = parser.parse_args()
    store = get_store()
    document = store.register(args.pdf.read_bytes(), args.pdf.name, args.role, 'cli_' + uuid.uuid4().hex)
    result = store.parse(document.id, 'cli_parse_' + uuid.uuid4().hex)
    print(json.dumps({'document_id': document.id, 'pages': len(result.pages), 'blocks': result.block_count,
                      'materials': result.material_count, 'status': result.status, 'model_calls': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()

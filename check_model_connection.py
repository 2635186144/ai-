"""One explicit, tiny DeepSeek request; never prints credentials or raw errors."""
import asyncio
import json
import os
import re
import time
from urllib.parse import urlsplit

import httpx

from backend import app as application  # Reuse the application's local configuration loading.
from backend.evidence.store import now


async def main():
    base = os.getenv('MODEL_BASE_URL', '').rstrip('/')
    parsed = urlsplit(base)
    model = os.getenv('MODEL_NAME', '')
    report = {'checked_at': now(), 'provider': 'deepseek', 'model': model if model == 'deepseek-flash' else 'other',
              'request_attempts': 0, 'purpose': 'connection_and_small_json_only', 'paper_content_sent': False,
              'thinking_mode': 'disabled_for_connection_probe_only', 'success': False}
    allowed = (parsed.scheme == 'https' and parsed.hostname == 'api.deepseek.com' and parsed.port in (None, 443)
               and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
               and parsed.path in ('', '/v1') and model == 'deepseek-flash' and bool(os.getenv('MODEL_API_KEY')))
    if not allowed:
        report['error_code'] = 'configuration_check_failed_no_request_sent'
    else:
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=40, follow_redirects=False, trust_env=False) as client:
                report['request_attempts'] = 1
                response = await client.post(base + '/chat/completions',
                    headers={'Authorization': 'Bearer ' + os.environ['MODEL_API_KEY']},
                    json={'model': model, 'max_tokens': 128, 'thinking': {'type': 'disabled'},
                          'messages': [{'role': 'system', 'content': 'Connection test. Return only the JSON object {"ok":true}.'},
                                       {'role': 'user', 'content': 'Return the requested JSON.'}]})
                report['http_status'] = response.status_code
                if response.status_code != 200:
                    report['error_code'] = 'provider_http_error'
                else:
                    payload = response.json()
                    raw = payload['choices'][0]['message']['content'].strip()
                    value = json.loads(re.sub(r'^```(?:json)?\s*|\s*```$', '', raw))
                    report['success'] = value == {'ok': True}
                    report['json_response_valid'] = report['success']
                    report['finish_reason'] = 'stop' if payload['choices'][0].get('finish_reason') == 'stop' else 'other'
                    usage = payload.get('usage', {})
                    report['usage'] = {k: usage[k] for k in ['prompt_tokens', 'completion_tokens', 'total_tokens'] if type(usage.get(k)) is int}
                    if not report['success']:
                        report['error_code'] = 'unexpected_json_response'
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            report['error_code'] = 'network_or_response_error'
            report['error_type'] = type(exc).__name__
        report['elapsed_seconds'] = round(time.monotonic() - start, 2)
    destination = application.ROOT / 'dev_data' / 'verification' / 'model-connection.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))

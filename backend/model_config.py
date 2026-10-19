"""Backend-only model environment configuration."""
import os
from urllib.parse import urlsplit

def read_env(path):
    values = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_environment(root, environ=None):
    env = os.environ if environ is None else environ
    for key, value in read_env(root / '.env').items():
        env.setdefault(key, value)


def provider_options(model, base):
    if urlsplit(base).hostname == 'api.deepseek.com':
        return {'response_format': {'type': 'json_object'}}
    return {}

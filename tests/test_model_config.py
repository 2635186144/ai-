from backend.model_config import load_environment, provider_options


def test_saved_config_and_environment_priority(tmp_path):
    (tmp_path / '.env').write_text('\ufeffMODEL_API_KEY=synthetic-local\nMODEL_NAME=deepseek-flash\n', encoding='utf-8')
    env = {'MODEL_API_KEY': 'synthetic-os'}
    load_environment(tmp_path, env)
    assert env['MODEL_API_KEY'] == 'synthetic-os'
    assert env['MODEL_NAME'] == 'deepseek-flash'


def test_deepseek_options_do_not_leak_to_other_providers():
    assert provider_options('fixture', 'https://fixture.invalid/v1') == {}
    assert provider_options('deepseek-flash', 'https://api.deepseek.com') == {'response_format': {'type': 'json_object'}}

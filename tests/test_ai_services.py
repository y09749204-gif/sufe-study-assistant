import json
from unittest.mock import patch
import httpx
import pytest
from fastapi import HTTPException
from personal_os_api.config import save_settings, get_settings
from personal_os_api.ai_provider import chat_json, list_models, service_for, key_path
from personal_os_api.setup_api import AIConfig, configure_ai, status, test_ai as check_ai


def cloud(url='https://example.test/v1', **extra):
    return dict(provider='openai', base_url=url, text_model='text-test', vision_model='vision-test', cloud_consent=True, **extra)


def test_legacy_shared_service(isolated_settings):
    save_settings({'ai': cloud()})
    assert service_for('vision')['base_url'] == service_for()['base_url']
    key_path().write_bytes(b'fake-encrypted')
    configure_ai(AIConfig(**cloud()))
    assert status()['settings']['ai']['api_key_set']


def test_endpoint_change_requires_new_secret(isolated_settings):
    save_settings({'ai': cloud()})
    key_path().write_bytes(b'fake-encrypted')
    with pytest.raises(HTTPException, match='') as exc:
        configure_ai(AIConfig(**cloud('https://other.test/v1')))
    assert exc.value.status_code == 422
    assert get_settings().values['ai']['base_url'] == 'https://example.test/v1'


def test_independent_keys_not_exposed(isolated_settings):
    with patch('personal_os_api.setup_api.store_key') as store:
        configure_ai(AIConfig(**cloud(api_key='fake-text'), vision_reuse=False, vision_service=cloud('https://vision.test/v1', api_key='fake-vision')))
    assert len(store.call_args_list) == 2
    assert store.call_args_list[0].args[1] != store.call_args_list[1].args[1]
    public = json.dumps(status())
    assert 'fake-text' not in public and 'fake-vision' not in public and 'key_ref' not in public


def test_routing_and_json_fallback(isolated_settings):
    save_settings({'ai': {**cloud(), 'vision_reuse': False, 'vision_service': cloud('https://vision.test/v1/chat/completions')}})
    requests = []
    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(400, json={'error': 'response_format not supported'})
        return httpx.Response(200, json={'choices':[{'message':{'content':'{"ok":true}'}}]})
    real = httpx.Client
    with patch('personal_os_api.ai_provider.read_key', return_value='fake'), patch('personal_os_api.ai_provider.httpx.Client', side_effect=lambda **kw: real(transport=httpx.MockTransport(handle), **kw)):
        assert chat_json('JSON', image='data:image/png;base64,AA==') == {'ok': True}
    assert str(requests[0].url) == 'https://vision.test/v1/chat/completions'
    assert json.loads(requests[0].content)['model'] == 'vision-test'
    assert 'response_format' not in json.loads(requests[1].content)


def test_local_models_and_vision_without_text(isolated_settings):
    save_settings({'ai': {'provider':'none', 'vision_reuse':False, 'vision_service': {'provider':'ollama', 'base_url':'http://127.0.0.1:11434', 'vision_model':'local-vision'}}})
    real = httpx.Client
    def handle(request):
        assert request.url.path == '/api/tags'
        assert 'authorization' not in request.headers
        return httpx.Response(200, json={'models':[{'name':'local-vision'}]})
    with patch('personal_os_api.ai_provider.httpx.Client', side_effect=lambda **kw: real(transport=httpx.MockTransport(handle), **kw)):
        assert list_models('vision') == ['local-vision']


def test_vision_test_requires_image_content(isolated_settings):
    with patch('personal_os_api.setup_api.chat_json', return_value={'ok':True}) as chat:
        with pytest.raises(HTTPException): check_ai('vision')
    assert chat.call_args.kwargs['image'].startswith('data:image/png;base64,')


def test_no_consent_no_request(isolated_settings):
    cfg = cloud(); cfg['cloud_consent'] = False
    save_settings({'ai': cfg})
    with patch('personal_os_api.ai_provider.httpx.Client') as client:
        with pytest.raises(RuntimeError): chat_json('test')
        client.assert_not_called()

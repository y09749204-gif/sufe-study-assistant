"""Explicit opt-in model calls. Secrets never appear in status responses."""
import base64
import json
import os
import re
from urllib.parse import urlparse
import httpx
from .config import data_root, get_settings


def key_path(ref=None):
    if ref is not None and not re.fullmatch(r'[a-f0-9]{32}', ref):
        raise ValueError('Invalid secret reference')
    return data_root() / (f'api-key-{ref}.dpapi' if ref else 'api-key.dpapi')


def store_key(value, ref=None):
    if os.name != 'nt':
        raise ValueError('API key storage requires Windows DPAPI')
    import win32crypt
    root = data_root(); root.mkdir(parents=True, exist_ok=True)
    key_path(ref).write_bytes(win32crypt.CryptProtectData(value.encode(), 'SufeStudyAssistant', None, None, None, 0))


def read_key(ref=None):
    path = key_path(ref)
    if not path.exists(): return ''
    import win32crypt
    return win32crypt.CryptUnprotectData(path.read_bytes(), None, None, None, 0)[1].decode()


def validate_config(cfg):
    kind = cfg.get('provider', 'none')
    if kind not in ('none', 'ollama', 'openai'): raise ValueError('Unknown AI provider')
    if kind == 'none': return
    url = urlparse(cfg.get('base_url', ''))
    if url.username or url.password or url.query or url.fragment: raise ValueError('Invalid model service URL')
    if kind == 'ollama':
        if url.scheme != 'http' or url.hostname not in ('localhost', '127.0.0.1', '::1'):
            raise ValueError('Ollama must be local')
    elif url.scheme != 'https' or not url.hostname: raise ValueError('Remote API requires HTTPS')
    if kind == 'openai' and not cfg.get('cloud_consent'):
        raise ValueError('请先同意向所选服务发送课堂文本或课表截图')


def model_identity():
    c = get_settings().values.get('ai', {})
    return '|'.join(str(c.get(k, '')) for k in ('provider', 'base_url', 'text_model'))


def service_for(purpose='text'):
    cfg = get_settings().values.get('ai', {'provider': 'none'})
    if purpose == 'vision' and not cfg.get('vision_reuse', True):
        return cfg.get('vision_service') or {'provider': 'none'}
    return cfg


def normalized_url(value):
    return value.strip().rstrip('/').removesuffix('/chat/completions').removesuffix('/models')


def headers_for(cfg):
    if cfg.get('provider') == 'ollama': return {}
    key = read_key(cfg.get('key_ref'))
    if not key: raise RuntimeError('请配置该服务的 API Key')
    return {'Authorization': 'Bearer ' + key}


def list_models(purpose='text'):
    cfg = service_for(purpose)
    try:
        validate_config(cfg)
        if cfg.get('provider', 'none') == 'none': raise RuntimeError('请先配置该用途的 AI 服务')
        local = cfg['provider'] == 'ollama'
        with httpx.Client(timeout=30, trust_env=False, follow_redirects=False) as client:
            response = client.get(normalized_url(cfg['base_url']) + ('/api/tags' if local else '/models'), headers=headers_for(cfg))
            response.raise_for_status()
        body = response.json()
        return sorted({x['name' if local else 'id'] for x in body['models' if local else 'data']})
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise RuntimeError('无法获取模型列表，可手动填写服务商提供的模型 ID') from None


def chat_json(prompt, schema=None, image=None):
    cfg = service_for('vision' if image else 'text')
    try: validate_config(cfg)
    except ValueError as exc: raise RuntimeError(str(exc)) from None
    kind = cfg.get('provider', 'none')
    if kind == 'none': raise RuntimeError('尚未配置 AI，原文仍可阅读')
    model = cfg.get('vision_model' if image else 'text_model')
    if not model: raise RuntimeError('请配置支持看图的模型' if image else '请配置文字模型')
    base = normalized_url(cfg['base_url'])
    headers = {}
    if kind == 'ollama':
        message = {'role': 'user', 'content': prompt}
        if image: message['images'] = [image.split(',', 1)[1]]
        body = {'model': model, 'messages': [message], 'stream': False, 'format': schema or 'json', 'options': {'temperature': 0.1}}
        url = base + '/api/chat'
    else:
        content = prompt if not image else [{'type': 'text', 'text': prompt}, {'type': 'image_url', 'image_url': {'url': image}}]
        body = {'model': model, 'messages': [{'role': 'user', 'content': content}], 'response_format': {'type': 'json_object'}}
        headers = headers_for(cfg)
        url = base + '/chat/completions'
    try:
        with httpx.Client(timeout=600, trust_env=False, follow_redirects=False) as client:
            response = client.post(url, json=body, headers=headers)
            if kind != 'ollama' and response.status_code in (400, 422):
                # Retry only an explicit unsupported JSON-mode response, never auth/network errors.
                error = response.text.lower()
                if 'response_format' in error and any(word in error for word in ('unsupported', 'not supported', 'not support')):
                    body.pop('response_format')
                    response = client.post(url, json=body, headers=headers)
            if response.status_code in (401, 403): raise RuntimeError('密钥无效或没有访问该模型的权限')
            if response.status_code == 404: raise RuntimeError('服务地址或模型不存在，请核对 API 地址和模型 ID')
            response.raise_for_status()
        result = response.json()
        text = result['message']['content'] if kind == 'ollama' else result['choices'][0]['message']['content']
        parsed = json.loads(text)
        if not isinstance(parsed, dict): raise ValueError('Expected JSON object')
        return parsed
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise RuntimeError('模型请求失败或输出无效，请检查服务和模型能力；未保存为成功结果') from None

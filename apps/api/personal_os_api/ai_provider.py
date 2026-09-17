"""Explicit opt-in model calls. Secrets never appear in status responses."""
import base64
import json
import os
from urllib.parse import urlparse
import httpx
from .config import data_root, get_settings


def store_key(value):
    if os.name != 'nt':
        raise ValueError('API key storage requires Windows DPAPI')
    import win32crypt
    root = data_root(); root.mkdir(parents=True, exist_ok=True)
    (root / 'api-key.dpapi').write_bytes(win32crypt.CryptProtectData(value.encode(), 'SufeStudyAssistant', None, None, None, 0))


def read_key():
    path = data_root() / 'api-key.dpapi'
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
    elif url.scheme != 'https': raise ValueError('Remote API requires HTTPS')
    if kind == 'openai' and not cfg.get('cloud_consent'):
        raise ValueError('请先同意向所选服务发送课堂文本或课表截图')


def model_identity():
    c = get_settings().values.get('ai', {})
    return '|'.join(str(c.get(k, '')) for k in ('provider', 'base_url', 'text_model'))


def chat_json(prompt, schema=None, image=None):
    cfg = get_settings().values.get('ai', {'provider': 'none'})
    validate_config(cfg)
    kind = cfg.get('provider', 'none')
    if kind == 'none': raise RuntimeError('尚未配置 AI，原文仍可阅读')
    model = cfg.get('vision_model' if image else 'text_model')
    if not model: raise RuntimeError('请配置支持看图的模型' if image else '请配置文字模型')
    base = cfg['base_url'].rstrip('/')
    headers = {}
    if kind == 'ollama':
        message = {'role': 'user', 'content': prompt}
        if image: message['images'] = [image.split(',', 1)[1]]
        body = {'model': model, 'messages': [message], 'stream': False, 'format': schema or 'json', 'options': {'temperature': 0.1}}
        url = base + '/api/chat'
    else:
        content = prompt if not image else [{'type': 'text', 'text': prompt}, {'type': 'image_url', 'image_url': {'url': image}}]
        body = {'model': model, 'messages': [{'role': 'user', 'content': content}], 'temperature': 0.1, 'response_format': {'type': 'json_object'}}
        key = read_key()
        if not key: raise RuntimeError('请配置 API Key')
        headers['Authorization'] = 'Bearer ' + key
        url = base + '/chat/completions'
    try:
        with httpx.Client(timeout=600, trust_env=False, follow_redirects=False) as client:
            response = client.post(url, json=body, headers=headers)
            response.raise_for_status()
        result = response.json()
        text = result['message']['content'] if kind == 'ollama' else result['choices'][0]['message']['content']
        parsed = json.loads(text)
        if not isinstance(parsed, dict): raise ValueError('Expected JSON object')
        return parsed
    except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
        raise RuntimeError('模型请求失败或输出无效，请检查服务和模型能力；未保存为成功结果') from None

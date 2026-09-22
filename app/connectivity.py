"""Minimal Responses API probe. Never runs an agent or exposes credentials to the UI."""
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import ConfigError


def connection(home, effective):
    provider_id = effective.get('model_provider', 'openai')
    provider = effective.get('model_providers', {}).get(provider_id, {})
    if provider_id != 'openai' and not provider:
        raise ConfigError(f'未找到服务商配置：{provider_id}')
    if provider.get('wire_api', 'responses') != 'responses':
        raise ConfigError('此 Codex 版本使用 Responses API；当前服务商协议不受支持')
    base = provider.get('base_url', 'https://api.openai.com/v1' if provider_id == 'openai' else '')
    if not base or urllib.parse.urlsplit(base).scheme not in ('http', 'https'):
        raise ConfigError('服务商缺少有效的 base_url')
    auth = {}
    auth_path = home.path / 'auth.json'
    if auth_path.is_file():
        try:
            auth = json.loads(auth_path.read_text('utf-8-sig'))
        except (ValueError, OSError):
            raise ConfigError('auth.json 无法读取') from None
    headers = {str(k): str(v) for k, v in provider.get('http_headers', {}).items()}
    for key, variable in provider.get('env_http_headers', {}).items():
        if os.environ.get(variable):
            headers[key] = os.environ[variable]
    env_key = provider.get('env_key')
    if env_key:
        token = os.environ.get(env_key)
        if not token:
            raise ConfigError(f'当前面板进程缺少环境变量 {env_key}，设置后重新启动面板')
    else:
        token = provider.get('experimental_bearer_token')
        if provider_id == 'openai' or provider.get('requires_openai_auth'):
            token = auth.get('OPENAI_API_KEY') or os.environ.get('OPENAI_API_KEY') or token
            if not token:
                raise ConfigError('当前认证不是可直接调用的 API Key。此版不执行 ChatGPT OAuth 刷新，请使用已有 API Key 服务商配置')
    if token:
        headers['Authorization'] = f'Bearer {token}'
    headers['Content-Type'] = 'application/json'
    url = base.rstrip('/') + '/responses'
    query = provider.get('query_params', {})
    if query:
        url += '?' + urllib.parse.urlencode(query)
    model = effective.get('model', '')
    if not model:
        raise ConfigError('未配置模型，无法执行连通性测试')
    body = {'model': model, 'input': 'Reply with OK only.', 'store': False,
            'stream': False, 'max_output_tokens': 256}
    if effective.get('model_reasoning_effort'):
        body['reasoning'] = {'effort': effective['model_reasoning_effort']}
    fingerprint = hashlib.sha256(json.dumps([url, headers, body], sort_keys=True).encode()).hexdigest()
    return url, headers, body, fingerprint


def probe(home, effective):
    url, headers, body, fingerprint = connection(home, effective)
    started = time.monotonic()
    request = urllib.request.Request(url, json.dumps(body).encode(), headers, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.loads(response.read(2_000_000))
    except urllib.error.HTTPError as exc:
        # Do not echo upstream bodies: some proxies reflect authorization headers.
        hints = {401: '认证失败', 403: '无权限访问', 404: '模型或 Responses 接口不存在',
                 429: '额度不足或请求限流', 400: '模型、推理强度或请求参数不受支持'}
        code = exc.code
        exc.close()
        raise ConfigError(f'HTTP {code}：{hints.get(code, "服务商返回错误")}') from None
    except (OSError, ValueError, urllib.error.URLError):
        raise ConfigError('请求失败或响应无法解析，请检查服务地址、网络及 Responses API 支持情况') from None
    if not isinstance(payload, dict) or not isinstance(payload.get('output'), list):
        raise ConfigError('返回内容不符合 Responses API 输出格式，未通过测试')
    if payload.get('error') or payload.get('status') in ('failed', 'cancelled', 'in_progress', 'queued'):
        raise ConfigError('服务商未完成请求，未通过连通性测试')
    texts = [c.get('text', '') for item in payload.get('output', []) if isinstance(item, dict)
             for c in (item.get('content') or []) if isinstance(c, dict) and c.get('type') == 'output_text']
    if not any(isinstance(t, str) and t.strip() for t in texts):
        raise ConfigError('接口已响应，但未生成文本；可能不兼容或推理耗尽输出预算，未通过测试')
    return {'fingerprint': fingerprint, 'latency_ms': round((time.monotonic() - started) * 1000),
            'model': body['model'], 'message': '已生成文本；未验证 Codex 工具调用兼容性'}

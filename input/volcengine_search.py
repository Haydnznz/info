"""Doubao/Volcengine Networked Search adapter.

The endpoint and model are configurable because Volcengine product regions and
API versions can differ. Credentials are read only from an environment variable.
"""
import hashlib
import os
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

USER_AGENT = 'OpinionAnalysisPlatform/0.2'


def _safe_error(exc, secret):
    return str(exc).replace(secret or '', '[REDACTED_VOLCENGINE_API_KEY]')


def _source(title, url, summary, published_at=None):
    title = (title or '豆包联网搜索结果').strip()
    url = (url or '').strip()
    raw = f'volcengine|{url}|{title}'.encode('utf-8')
    return {
        'source_id': hashlib.sha256(raw).hexdigest()[:20],
        'type': 'news',
        'title': title[:500],
        'url': url,
        'summary': (summary or '').strip()[:2000],
        'published_at': published_at,
        'collected_at': datetime.now(timezone.utc).isoformat(),
        'input_family': 'public_info',
        'provider': 'volcengine_doubao_search',
    }


def _text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ''.join(_text(x) for x in value)
    if isinstance(value, dict):
        return _text(value.get('text') or value.get('content') or value.get('value') or '')
    return ''


def _extract(payload, limit):
    """Extract citations from Responses API, Chat Completions, or result arrays."""
    results = []
    candidates = payload.get('results') or payload.get('web_results') or payload.get('data') or []
    if isinstance(candidates, dict):
        candidates = candidates.get('results') or candidates.get('items') or []
    if isinstance(candidates, list):
        for item in candidates:
            if not isinstance(item, dict):
                continue
            url = item.get('url') or item.get('link') or item.get('source_url')
            title = item.get('title') or item.get('name')
            summary = item.get('snippet') or item.get('summary') or item.get('content') or item.get('text')
            if url or title:
                results.append(_source(title, url, _text(summary), item.get('published_at') or item.get('date')))

    output = payload.get('output') or []
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            for content in item.get('content') or []:
                if not isinstance(content, dict):
                    continue
                annotations = content.get('annotations') or []
                text = _text(content.get('text') or content.get('content'))
                for ann in annotations:
                    if not isinstance(ann, dict):
                        continue
                    url = ann.get('url') or ann.get('source_url')
                    if url:
                        results.append(_source(ann.get('title') or urlparse(url).netloc, url, text))

    choices = payload.get('choices') or []
    if isinstance(choices, list):
        for choice in choices:
            message = choice.get('message', {}) if isinstance(choice, dict) else {}
            content = message.get('content', '') if isinstance(message, dict) else ''
            if isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    for ann in part.get('annotations') or part.get('citations') or []:
                        if isinstance(ann, dict) and (ann.get('url') or ann.get('link')):
                            url = ann.get('url') or ann.get('link')
                            results.append(_source(ann.get('title') or urlparse(url).netloc, url, _text(part.get('text'))))

    unique = {}
    for item in results:
        unique[item['source_id']] = item
    return list(unique.values())[:limit]


def search(query, settings=None):
    settings = settings or {}
    cfg = settings.get('collection', {}).get('volcengine_search', {})
    secret = os.getenv('VOLCENGINE_API_KEY', '')
    if not secret:
        return [], '未配置豆包搜索 API Key（环境变量：VOLCENGINE_API_KEY）'
    endpoint = os.getenv('VOLCENGINE_SEARCH_ENDPOINT', '')
    model = os.getenv('VOLCENGINE_SEARCH_MODEL', '')
    if not model:
        return [], '未配置豆包搜索模型（VOLCENGINE_SEARCH_MODEL）'
    limit = max(1, min(int(settings.get('collection', {}).get('max_records', 20)), 50))
    mode = cfg.get('mode', 'responses')
    prompt = f'搜索并整理与“{query}”相关的最新公开信息。只返回有来源链接的事实，不要猜测。'
    if mode == 'chat':
        body = {'model': model, 'messages': [{'role': 'user', 'content': prompt}], 'tools': [{'type': 'web_search'}]}
    else:
        body = {'model': model, 'input': prompt, 'tools': [{'type': 'web_search'}]}
    try:
        response = requests.post(endpoint, headers={'Authorization': f'Bearer {secret}', 'Content-Type': 'application/json', 'User-Agent': USER_AGENT}, json=body, timeout=int(cfg.get('timeout_seconds', 30)))
        response.raise_for_status()
        items = _extract(response.json(), limit)
        return items, None
    except requests.RequestException as exc:
        return [], '豆包搜索请求失败：' + _safe_error(exc, secret)
    except (TypeError, ValueError, KeyError) as exc:
        return [], '豆包搜索响应解析失败：' + _safe_error(exc, secret)

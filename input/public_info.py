"""公开信息输入：GDELT 新闻 API 和可配置 RSS 源。"""
import hashlib
import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import quote
import requests
import os
from .mcp_search import search as doubao_search

UA = os.getenv('OPINION_HTTP_USER_AGENT', 'OpinionAnalysisPlatform/0.2')

def _item(kind, title, url, summary, published=None, source='public'):
    raw = f'{kind}|{url}|{title}'.encode()
    return {'source_id': hashlib.sha256(raw).hexdigest()[:20], 'type': kind, 'title': html.unescape(re.sub('<[^>]+>', '', title or '')).strip(), 'url': url, 'summary': html.unescape(re.sub('<[^>]+>', '', summary or '')).strip()[:1000], 'published_at': published, 'collected_at': datetime.now(timezone.utc).isoformat(), 'input_family': source}

def _gdelt(query, limit):
    endpoint = os.getenv('GDELT_API_URL', '')
    if not endpoint:
        raise RuntimeError('未配置 GDELT_API_URL')
    response = requests.get(endpoint, params={'query': query, 'mode': 'artlist', 'format': 'json', 'maxrecords': limit, 'sort': 'datedesc'}, headers={'User-Agent': UA}, timeout=int(os.getenv('OPINION_HTTP_TIMEOUT', '15')))
    response.raise_for_status()
    values = []
    for row in response.json().get('articles', []):
        values.append(_item('news', row.get('title'), row.get('url'), row.get('seendate') or row.get('snippet', ''), row.get('seendate'), 'public_info'))
    return values

def _rss(url, kind, limit):
    response = requests.get(url, headers={'User-Agent': UA}, timeout=int(os.getenv('OPINION_HTTP_TIMEOUT', '15')))
    response.raise_for_status()
    root = ET.fromstring(response.content)
    values = []
    for node in root.findall('.//item')[:limit]:
        text = lambda name: next((x.text or '' for x in node if x.tag.lower().endswith(name)), '')
        values.append(_item(kind, text('title'), text('link'), text('description'), text('pubdate'), 'public_info'))
    return values

def collect(query, source_types, settings=None):
    settings = settings or {}
    collection = settings.get('collection', {})
    limit = max(1, min(int(collection.get('max_records', 20)), 100))
    values = []
    errors = []
    if 'news' in source_types and collection.get('volcengine_search', {}).get('enabled', True):
        doubao_items, doubao_error = doubao_search(query, settings)
        values.extend(doubao_items)
        if doubao_error:
            errors.append(doubao_error)
    if 'news' in source_types and os.getenv('GDELT_ENABLED', 'false').lower() in ('1', 'true', 'yes', 'on'):
        try:
            values.extend(_gdelt(query, limit))
        except requests.RequestException as exc:
            errors.append('GDELT 新闻请求失败：' + str(exc))
    if 'forum_post' in source_types:
        feeds = [x.strip() for x in os.getenv('FORUM_RSS_URLS', '').split(',') if x.strip()]
        for feed in feeds[:5]:
            try:
                values.extend(_rss(feed, 'forum_post', limit))
            except (requests.RequestException, ET.ParseError) as exc:
                errors.append(f'RSS 请求失败（{feed}）：{exc}')
    return values, errors

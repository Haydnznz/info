"""多媒体输入：Wikimedia Commons 图片和 YouTube 搜索元数据。"""
import hashlib
from datetime import datetime, timezone
import requests
import os

UA = os.getenv('OPINION_HTTP_USER_AGENT', 'OpinionAnalysisPlatform/0.2')

def _item(kind, title, url, summary):
    return {'source_id': hashlib.sha256(url.encode()).hexdigest()[:20], 'type': kind, 'title': title, 'url': url, 'summary': summary[:1000], 'published_at': None, 'collected_at': datetime.now(timezone.utc).isoformat(), 'input_family': 'multimedia'}

def _commons(query, limit):
    endpoint = os.getenv('WIKIMEDIA_API_URL', '')
    if not endpoint:
        raise RuntimeError('未配置 WIKIMEDIA_API_URL')
    r = requests.get(endpoint, params={'action':'query','generator':'search','gsrsearch':query,'gsrnamespace':6,'gsrlimit':limit,'prop':'imageinfo','iiprop':'url|extmetadata','format':'json'}, headers={'User-Agent':UA}, timeout=int(os.getenv('OPINION_HTTP_TIMEOUT', '15')))
    r.raise_for_status()
    pages = r.json().get('query', {}).get('pages', {}).values()
    return [_item('image', p.get('title',''), p.get('imageinfo',[{}])[0].get('descriptionurl',''), 'Wikimedia Commons 图片元数据') for p in pages]

def _youtube(query, limit, key):
    try:
        endpoint = os.getenv('YOUTUBE_API_URL', '')
        if not endpoint:
            raise RuntimeError('未配置 YOUTUBE_API_URL')
        r = requests.get(endpoint, params={'part':'snippet','q':query,'type':'video','maxResults':limit,'key':key}, headers={'User-Agent':UA}, timeout=int(os.getenv('OPINION_HTTP_TIMEOUT', '15')))
        r.raise_for_status()
    except requests.RequestException as exc:
        # Never persist or display a URL containing the API key.
        safe = str(exc).replace(key, '[REDACTED_YOUTUBE_API_KEY]')
        raise RuntimeError(safe) from None
    return [_item('video', x.get('snippet',{}).get('title',''), os.getenv('YOUTUBE_WATCH_URL_TEMPLATE', '').replace('{video_id}', x.get('id',{}).get('videoId','')), x.get('snippet',{}).get('description','')) for x in r.json().get('items', [])]

def collect(query, source_types, settings=None):
    settings = settings or {}; multimedia = settings.get('collection', {}).get('multimedia', {}); limit = min(int(settings.get('collection', {}).get('max_records', 20)), 50); values = []
    if 'image' in source_types and multimedia.get('wikimedia_enabled', False) and os.getenv('WIKIMEDIA_ENABLED', 'false').lower() in ('1', 'true', 'yes', 'on'):
        values.extend(_commons(query, limit))
    if 'video' in source_types and os.getenv('YOUTUBE_API_KEY'):
        values.extend(_youtube(query, limit, os.environ['YOUTUBE_API_KEY']))
    return values

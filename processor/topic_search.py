"""主题检索后处理：规范化、去重、相关性排序和时间线整理。"""
import re
from datetime import datetime

def _terms(query):
    return [x.casefold() for x in re.findall(r'[\w\u4e00-\u9fff]{2,}', query)]

def _score(query, source):
    text = ' '.join(str(source.get(k, '')) for k in ('title', 'summary')).casefold()
    terms = _terms(query)
    return round(sum(1 for term in terms if term in text) / max(1, len(terms)), 3)

def run(query, sources):
    unique = {}
    for source in sources:
        key = source.get('source_id') or source.get('url') or (source.get('title'), source.get('summary'))
        item = dict(source)
        item['relevance_score'] = _score(query, item)
        unique[key] = item
    ordered = sorted(unique.values(), key=lambda x: (x['relevance_score'], x.get('published_at') or ''), reverse=True)
    timeline = [{'time': s.get('published_at') or s.get('collected_at'), 'event': s.get('title', ''), 'source_id': s.get('source_id')} for s in ordered if s.get('published_at') or s.get('collected_at')]
    timeline.insert(0, {'time': datetime.now().astimezone().isoformat(timespec='seconds'), 'event': f'完成主题“{query}”检索、去重和相关性排序'})
    return {'query': query, 'sources': ordered, 'deduplicated_count': len(ordered), 'query_terms': _terms(query), 'timeline': timeline}

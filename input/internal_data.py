"""内部沉淀数据输入：本地 JSON/JSONL/CSV/Markdown 和可选 MongoDB。"""
import csv
import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone

def _item(title, text, url):
    return {'source_id': hashlib.sha256((url + text).encode()).hexdigest()[:20], 'type': 'internal_data', 'title': title, 'url': url, 'summary': text[:1000], 'published_at': None, 'collected_at': datetime.now(timezone.utc).isoformat(), 'input_family': 'internal_data'}

def _local(query, directory, limit):
    values = []; root = Path(directory)
    if not root.exists(): return values
    for path in list(root.glob('*.json')) + list(root.glob('*.jsonl')) + list(root.glob('*.csv')) + list(root.glob('*.md')):
        if len(values) >= limit: break
        try:
            if path.suffix == '.csv':
                rows = list(csv.DictReader(path.open(encoding='utf-8')))
                for row in rows:
                    text = json.dumps(row, ensure_ascii=False)
                    if query.casefold() in text.casefold(): values.append(_item(path.name, text, 'internal://' + str(path)))
            elif path.suffix == '.jsonl':
                rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
                for row in rows:
                    text = json.dumps(row, ensure_ascii=False)
                    if query.casefold() in text.casefold(): values.append(_item(path.name, text, 'internal://' + str(path)))
            elif path.suffix == '.json':
                text = path.read_text(encoding='utf-8')
                if query.casefold() in text.casefold(): values.append(_item(path.name, text, 'internal://' + str(path)))
            else:
                text = path.read_text(encoding='utf-8')
                if query.casefold() in text.casefold(): values.append(_item(path.name, text, 'internal://' + str(path)))
        except (OSError, ValueError, UnicodeError):
            continue
    return values[:limit]

def collect(query, source_types, settings=None):
    if 'internal_data' not in source_types: return []
    settings = settings or {}; cfg = settings.get('collection', {}).get('internal', {}); values = _local(query, cfg.get('directory', 'data/internal'), int(settings.get('collection', {}).get('max_records', 20)))
    if values or not cfg.get('demo_fallback', False): return values
    return [_item('内部演示数据', f'未配置内部数据目录，主题 {query} 暂无匹配记录。', 'internal://demo')]

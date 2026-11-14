"""Deterministic text preprocessing and lightweight information extraction.

This module deliberately does not invent facts. Every extracted entity/event keeps
the source_id that produced it, so a later LLM step can review rather than replace
the evidence.
"""
import hashlib
import html
import re
from datetime import datetime

_TAG_RE = re.compile(r'<[^>]+>')
_SPACE_RE = re.compile(r'\s+')
_URL_RE = re.compile(r'https?://[^\s<>"\u3002，。；;）)]+', re.I)
_DATE_RE = re.compile(r'(?:20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?|\d{1,2}月\d{1,2}日|20\d{2})')
_NUMBER_RE = re.compile(r'\d+(?:\.\d+)?\s*(?:人|架|名|公里|英尺|美元|万元|%|percent)?')
_EVENT_RE = re.compile(r'(坠毁|失事|事故|爆炸|起飞|降落|迫降|失踪|遇难|死亡|受伤|获救|调查|发布|宣布|crash|accident|missing|killed|injured|rescued|investigation)', re.I)


def clean_text(value):
    value = html.unescape(str(value or ''))
    value = _TAG_RE.sub(' ', value)
    value = value.replace('\u200b', '').replace('\xa0', ' ')
    return _SPACE_RE.sub(' ', value).strip()


def detect_language(text):
    cjk = len(re.findall(r'[\u3400-\u9fff]', text))
    latin = len(re.findall(r'[A-Za-z]', text))
    if cjk and latin:
        return 'zh-en'
    if cjk:
        return 'zh'
    if latin:
        return 'en'
    return 'unknown'


def segment_text(text, max_chars=500):
    sentences = [x.strip() for x in re.split(r'(?<=[。！？!?；;])|\n+', text) if x.strip()]
    segments = []
    for sentence in sentences:
        while len(sentence) > max_chars:
            cut = sentence.rfind(' ', 0, max_chars)
            cut = cut if cut >= max_chars // 2 else max_chars
            segments.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if sentence:
            segments.append(sentence)
    return segments


def _entity(entity_type, value, source_id, evidence):
    return {'entity_id': hashlib.sha256(f'{entity_type}|{value}'.encode()).hexdigest()[:16], 'type': entity_type, 'value': value, 'source_id': source_id, 'evidence': evidence[:300]}


def extract_entities(text, source_id):
    found = []
    for value in _URL_RE.findall(text):
        found.append(_entity('url', value, source_id, value))
    for value in _DATE_RE.findall(text):
        found.append(_entity('date', value, source_id, value))
    for value in _NUMBER_RE.findall(text):
        found.append(_entity('number', value, source_id, value))
    # Quoted names and common proper-name spans are safer than guessing every noun.
    for value in re.findall(r'[“「《]([^”」》]{2,40})[”」》]', text):
        found.append(_entity('named_topic', value, source_id, value))
    unique = {}
    for item in found:
        unique[(item['type'], item['value'])] = item
    return list(unique.values())


def extract_events(text, source_id):
    events = []
    for sentence in segment_text(text):
        match = _EVENT_RE.search(sentence)
        if not match:
            continue
        dates = _DATE_RE.findall(sentence)
        event_id = hashlib.sha256(f'{source_id}|{sentence}'.encode()).hexdigest()[:16]
        events.append({'event_id': event_id, 'trigger': match.group(1), 'description': sentence[:500], 'dates': dates, 'source_id': source_id})
    return events


def process_source(source):
    item = dict(source)
    source_id = item.get('source_id') or hashlib.sha256(str(item).encode()).hexdigest()[:20]
    raw = clean_text(' '.join(str(item.get(k, '')) for k in ('title', 'summary', 'content')))
    item['source_id'] = source_id
    item['clean_text'] = raw[:10000]
    item['language'] = detect_language(raw)
    item['segments'] = segment_text(raw)
    item['entities'] = extract_entities(raw, source_id)
    item['events'] = extract_events(raw, source_id)
    item['processing'] = {'cleaned': True, 'segment_count': len(item['segments']), 'entity_count': len(item['entities']), 'event_count': len(item['events'])}
    return item


def run(sources):
    unique = {}
    duplicates = 0
    for source in sources:
        item = process_source(source)
        fingerprint = hashlib.sha256(item['clean_text'].casefold().encode()).hexdigest()
        if fingerprint in unique:
            duplicates += 1
            unique[fingerprint]['duplicate_source_ids'] = unique[fingerprint].get('duplicate_source_ids', []) + [item['source_id']]
        else:
            item['content_fingerprint'] = fingerprint
            unique[fingerprint] = item
    processed = list(unique.values())
    timeline = []
    for source in processed:
        timestamp = source.get('published_at') or source.get('collected_at')
        if timestamp:
            timeline.append({'time': timestamp, 'event': source.get('title', ''), 'source_id': source['source_id'], 'event_ids': [e['event_id'] for e in source['events']]})
        for event in source['events']:
            for date in event['dates']:
                timeline.append({'time': date, 'event': event['description'], 'source_id': source['source_id'], 'event_id': event['event_id']})
    timeline.sort(key=lambda x: str(x.get('time', '')))
    return {'sources': processed, 'deduplicated_count': duplicates, 'timeline': timeline, 'stats': {'input_count': len(sources), 'output_count': len(processed), 'duplicate_count': duplicates, 'segment_count': sum(len(x['segments']) for x in processed), 'entity_count': sum(len(x['entities']) for x in processed), 'event_count': sum(len(x['events']) for x in processed), 'languages': sorted({x['language'] for x in processed})}}

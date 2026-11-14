import json


def run(query, sources, insight=None):
    insight = insight or {}
    refs = sources[0]['url'] if sources else 'internal://dataset/demo'
    nodes = [{'id': 'topic', 'label': query, 'type': 'topic'}, {'id': 'verification', 'label': '待核验', 'type': 'status'}]
    edges = [{'source': 'topic', 'target': 'verification', 'type': '证据状态', 'source_ref': refs, 'source_id': '', 'confidence': 0.5}]
    for index, source in enumerate(sources):
        sid = source.get('source_id', f'source-{index}')
        nodes.append({'id': sid, 'label': source.get('title', sid), 'type': source.get('type', 'source')})
        edges.append({'source': 'topic', 'target': sid, 'type': '来源证据', 'source_ref': source.get('url', ''), 'source_id': sid, 'confidence': source.get('confidence', 0.5)})
        for entity in source.get('entities', []):
            eid = entity['entity_id']
            nodes.append({'id': eid, 'label': entity['value'], 'type': 'entity', 'entity_type': entity['type']})
            edges.append({'source': sid, 'target': eid, 'type': '实体提及', 'source_ref': source.get('url', ''), 'source_id': sid, 'confidence': entity.get('confidence', 0.5)})
        for event in source.get('events', []):
            eid = event['event_id']
            nodes.append({'id': eid, 'label': event['description'], 'type': 'event', 'trigger': event['trigger']})
            edges.append({'source': sid, 'target': eid, 'type': '事件证据', 'source_ref': source.get('url', ''), 'source_id': sid, 'confidence': event.get('confidence', 0.5)})
    if sources:
        nodes.extend([{'id': 'risk', 'label': '未经核验的信息扩散', 'type': 'risk'}, {'id': 'official', 'label': '官方通报待补充', 'type': 'evidence_gap'}])
        edges.extend([{'source': 'topic', 'target': 'risk', 'type': '关注风险', 'source_ref': refs, 'source_id': '', 'confidence': 0.5}, {'source': 'topic', 'target': 'official', 'type': '缺失证据', 'source_ref': refs, 'source_id': '', 'confidence': 0.5}])
    # Analysis artifacts are first-class graph entities and retain source refs.
    for index, opinion in enumerate(insight.get('opinions', []) + insight.get('opinion_clusters', [])):
        oid = opinion.get('id', f'opinion-{index}') if isinstance(opinion, dict) else f'opinion-{index}'
        label = opinion.get('label', opinion.get('text', str(opinion))) if isinstance(opinion, dict) else str(opinion)
        source_id = opinion.get('source_id', '') if isinstance(opinion, dict) else ''
        nodes.append({'id': oid, 'label': label, 'type': 'opinion', 'source_id': source_id})
        edges.append({'source': 'topic', 'target': oid, 'type': '观点', 'source_ref': source_id, 'source_id': source_id, 'confidence': opinion.get('confidence', 0.0) if isinstance(opinion, dict) else 0.0})
    sentiment = insight.get('sentiment')
    if sentiment:
        nodes.append({'id': 'sentiment', 'label': json.dumps(sentiment, ensure_ascii=False), 'type': 'sentiment'})
        edges.append({'source': 'topic', 'target': 'sentiment', 'type': '情绪倾向', 'source_ref': '', 'source_id': '', 'confidence': 0.5})
    for index, risk in enumerate(insight.get('risk_points', [])):
        rid = f'risk-{index}'
        nodes.append({'id': rid, 'label': str(risk), 'type': 'risk'})
        edges.append({'source': 'topic', 'target': rid, 'type': '风险关联', 'source_ref': '', 'source_id': '', 'confidence': 0.5})
    return {'nodes': nodes, 'edges': edges}

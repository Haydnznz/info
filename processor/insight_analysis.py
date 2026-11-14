import re

POSITIVE = ('成功', '增长', '支持', '获救', '改善', '安全')
NEGATIVE = ('坠毁', '事故', '死亡', '伤亡', '失踪', '爆炸', '故障', '风险', '调查')
RISK_TERMS = ('原因', '伤亡', '失踪', '责任', '安全', '未经证实', '谣言', '调查')

def _claims(sources):
    claims = []
    for source in sources:
        text = f"{source.get('title', '')} {source.get('summary', '')}"
        for term in RISK_TERMS:
            if term in text:
                claims.append({'claim': f'来源涉及“{term}”信息', 'term': term, 'source_id': source.get('source_id'), 'source_ref': source.get('url')})
    return claims

def run(sources, input_errors=None):
    input_errors = input_errors or []
    if not sources:
        return {'sentiment': {'positive': 0, 'neutral': 0, 'negative': 0}, 'risk_points': ['外部来源不可达', '事件事实尚未核验'], 'confidence': 'low', 'claims': [], 'source_quality': {'total': 0, 'families': 0}, 'summary': '当前没有获得可验证来源，不能对事故原因、伤亡或传播趋势下结论。应先恢复数据源访问并补充官方通报。', 'input_errors': input_errors}
    family_count = len({s.get('input_family') for s in sources if s.get('input_family')})
    all_events = [event for source in sources for event in source.get('events', [])]
    all_entities = [entity for source in sources for entity in source.get('entities', [])]
    text = ' '.join(f"{s.get('title','')} {s.get('summary','')}" for s in sources)
    pos = sum(text.count(x) for x in POSITIVE); neg = sum(text.count(x) for x in NEGATIVE)
    total = max(1, pos + neg); claims = _claims(sources)
    risk_points = sorted({f'需要核验：{c["term"]}' for c in claims}) or ['来源数量不足', '未经核验的信息扩散']
    confidence = 'high' if len(sources) >= 5 and family_count >= 2 and not input_errors else 'medium' if len(sources) >= 2 and family_count >= 2 else 'low'
    return {'sentiment': {'positive': round(pos / total * 100), 'neutral': round(max(0, 100 - pos / total * 100 - neg / total * 100)), 'negative': round(neg / total * 100)}, 'risk_points': risk_points, 'confidence': confidence, 'claims': claims, 'events': all_events, 'entities': all_entities, 'source_quality': {'total': len(sources), 'families': family_count, 'with_url': sum(bool(s.get('url')) for s in sources), 'with_time': sum(bool(s.get('published_at')) for s in sources)}, 'summary': f'已完成证据分析：{len(sources)} 条来源、{family_count} 类来源、{len(all_events)} 个事件、{len(all_entities)} 个实体。当前结论置信度为 {confidence}，仍需官方或独立来源交叉验证。', 'input_errors': input_errors}

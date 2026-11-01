"""Pure analysis adapter; no coordinator filesystem access. Demo data only."""
from .settings import now

def analyze(query, source_types, report_id):
    t = now(); source_types = source_types or ['news','image','video','forum_post','internal_data']
    sources = [
      {'type':'news','title':f'示例新闻：{query}产业应用持续升温','url':'https://example.com/news/ai-001','summary':f'围绕{query}的政策与产业动态受到关注。'},
      {'type':'forum_post','title':'论坛讨论：用户如何看待该主题','url':'https://example.com/forum/42','summary':'用户讨论集中在落地价值、数据安全和成本。'},
      {'type':'internal_data','title':'内部运营数据摘要','url':'internal://dataset/demo','summary':'内部样本显示相关咨询量环比增长 31%。'}]
    sources = [s for s in sources if s['type'] in source_types]
    timeline = [{'time': t, 'event': f'完成主题“{query}”多源检索'}, {'time': t, 'event': '完成情绪与风险分析'}]
    report = f'''# {query} 舆情分析报告\n\n- 报告编号：{report_id}\n- 执行时间：{t}\n\n## 事件概述\n本报告围绕“{query}”汇总公开信息、多媒体线索、论坛内容与内部数据，形成可追溯分析。\n\n## 时间线\n- {t}：完成多源检索与去重。\n- {t}：完成观点、情绪和风险分析。\n\n## 传播渠道\n新闻 {sum(1 for s in sources if s['type']=='news')} 条；论坛 {sum(1 for s in sources if s['type']=='forum_post')} 条；内部数据 {sum(1 for s in sources if s['type']=='internal_data')} 条；多媒体 {sum(1 for s in sources if s['type'] in ('image','video'))} 条。\n\n## 主要观点\n1. 正向观点：技术应用和产业机会受到关注。\n2. 中性观点：用户希望看到更多可验证数据和案例。\n3. 负向观点：数据安全、误用和成本是主要担忧。\n\n## 情绪倾向\n整体为中性偏正向，正向 45%、中性 35%、负向 20%。\n\n## 风险判断\n- 高风险：未经核验的信息扩散、隐私与合规问题。\n- 关注事项：论坛负面话题的二次传播和关键节点回应时效。\n\n## 重点证据\n''' + '\n'.join(f"- [{s['type']}] {s['title']}｜{s['url']}｜{s['summary']}" for s in sources) + f'''\n\n## 结论摘要\n建议持续监测论坛与新闻源，建立事实核验清单，并在高热度节点发布透明说明。结论可追溯至上述来源及图谱节点。\n'''
    graph = {'report_id': report_id, 'created_at': t, 'nodes': [{'id':'topic','label':query,'type':'topic'}, {'id':'risk','label':'数据安全','type':'risk'}, {'id':'opportunity','label':'产业机会','type':'opportunity'}], 'edges':[{'source':'topic','target':'risk','type':'关联风险','source_ref': sources[0]['url'] if sources else 'internal://dataset/demo'}, {'source':'topic','target':'opportunity','type':'正向观点','source_ref': sources[0]['url'] if sources else 'internal://dataset/demo'}]}
    result = {'report_id': report_id, 'query': query, 'executed_at': t, 'sources': sources, 'timeline': timeline, 'sentiment': {'positive': 45, 'neutral': 35, 'negative': 20}, 'summary': '演示分析：整体中性偏正向，需通过真实数据验证。', 'graph_path': f'/api/graph/{report_id}'}
    return {'result': result, 'report': report, 'graph': graph}

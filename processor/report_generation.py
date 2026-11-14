def run(query, report_id, executed_at, sources, insight, stages=None):
    stages = stages or {}
    evidence = '\n'.join(f"- [{s['type']}] {s['title']}｜证据 {s.get('source_id','')}｜{s['url']}｜{s['summary']}" for s in sources)
    errors = '\n'.join(f"- {e['input']}: {e['error']}" for e in insight.get('input_errors', [])) or '- 无'
    evidence = evidence or '- 当前没有可验证来源。'
    stats = ', '.join(f'{k}={v}' for k,v in (stages.get('topic_search', {}).get('sources') and {kind: sum(1 for s in sources if s.get('type') == kind) for kind in ('news','image','video','forum_post','internal_data')} or {}).items()) or '无有效来源'
    claims = '\n'.join(f"- {c['claim']}｜证据 {c.get('source_id','')}｜{c.get('source_ref','')}" for c in insight.get('claims', [])) or '- 未抽取到可核验事实主张。'
    risks = '\n'.join(f'- {x}' for x in insight.get('risk_points', []))
    timeline = '\n'.join(f"- {x.get('time','')}：{x.get('event','')}" for x in stages.get('topic_search', {}).get('timeline', []))
    text_stats = stages.get('text_processing', {}).get('stats', {})
    entities = '\n'.join(f"- {x['type']}: {x['value']}（来源 {x.get('source_id','')}）" for x in insight.get('entities', [])) or '- 未抽取到实体。'
    events = '\n'.join(f"- {x['trigger']}: {x['description']}（来源 {x.get('source_id','')}）" for x in insight.get('events', [])) or '- 未抽取到事件。'
    report = f'''# {query} 舆情分析报告\n\n- 报告编号：{report_id}\n- 执行时间：{executed_at}\n- 分析置信度：{insight.get('confidence', 'low')}\n\n## 事件概述\n本报告围绕“{query}”执行来源采集和文本处理。\n\n## 文本处理结果\n- 清洗：已完成。\n- 去重：输入 {text_stats.get('input_count', 0)} 条，输出 {text_stats.get('output_count', 0)} 条，重复 {text_stats.get('duplicate_count', 0)} 条。\n- 语言识别：{', '.join(text_stats.get('languages', [])) or 'unknown'}。\n- 分段：{text_stats.get('segment_count', 0)} 段。\n- 实体识别：{text_stats.get('entity_count', 0)} 个。\n- 事件抽取：{text_stats.get('event_count', 0)} 个。\n- 时间线抽取：{len(stages.get('topic_search', {}).get('timeline', []))} 条。\n\n## 分析流程\n- 主题检索：{len(stages.get('topic_search', {}).get('sources', []))} 条输入，已去重并排序。\n- 多媒体检索：{stages.get('multimedia_search', {}).get('count', 0)} 条。\n- 论坛采集：{stages.get('forum_collection', {}).get('count', 0)} 条。\n- 洞察分析：已执行，置信度 {insight.get('confidence', 'low')}。\n- 报告生成：已执行。\n- 图谱查询：已生成报告图谱。\n\n## 时间线\n{timeline}\n\n## 实体识别\n{entities}\n\n## 事件抽取\n{events}\n\n## 传播渠道\n来源统计：{stats}。\n\n## 主要观点与事实主张\n{claims}\n\n## 情绪倾向\n{insight['sentiment']}。证据不足时不将标题或单条内容视为整体舆情。\n\n## 风险判断\n{risks}\n\n## 重点证据\n{evidence}\n\n## 采集异常\n{errors}\n\n## 结论摘要\n{insight['summary']}\n'''
    return report

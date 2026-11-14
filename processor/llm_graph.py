"""LangGraph orchestration for optional LLM insight analysis.

Supports OpenAI-compatible endpoints (including DashScope / 百炼) and always
returns an explicit ``unavailable`` status when credentials are absent, so
rule-based insight is never mislabeled as LLM work. A model instance can be
injected so the six-node graph is testable offline.
"""
import json
import os
from typing import Any, Dict, List, TypedDict


class InsightState(TypedDict, total=False):
    query: str
    sources: List[dict]
    rule_insight: Dict[str, Any]
    context: str
    model: Any
    llm_result: Dict[str, Any]
    llm_status: str
    model_name: str
    opinion_clusters: List[dict]
    sentiment: Dict[str, Any]
    risks: List[str]
    conclusion: str
    error: str


def _extract_json(text):
    """Pull the first JSON object/array out of an LLM text reply."""
    if not text:
        return {}
    text = text.strip()
    if text.startswith('```'):
        text = text.strip('`')
        if text.startswith('json'):
            text = text[4:]
    starts = [i for i in (text.find('{'), text.find('[')) if i >= 0]
    if not starts:
        return {'raw': text[:2000]}
    start = min(starts)
    depth = 0
    for i in range(start, len(text)):
        if text[i] in '{[':
            depth += 1
        elif text[i] in '}]':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except Exception:
                    return {'raw': text[start:i + 1][:2000]}
    return {'raw': text[start:][:2000]}


def _llm_call(model, system, user):
    response = model.invoke([('system', system), ('user', user)])
    content = getattr(response, 'content', str(response))
    if isinstance(content, list):
        content = ''.join(str(x) for x in content)
    return content


def _resolve_model():
    from langchain_openai import ChatOpenAI
    timeout = float(os.getenv('LLM_TIMEOUT_SECONDS', '15'))
    if os.getenv('DASHSCOPE_API_KEY'):
        return ChatOpenAI(
            model=os.getenv('DASHSCOPE_MODEL', 'qwen-plus'),
            api_key=os.getenv('DASHSCOPE_API_KEY'),
            base_url='https://dashscope.aliyuncs.com/compatible-mode/v1',
            temperature=0,
            timeout=timeout,
            max_retries=1,
        )
    return ChatOpenAI(
        model=os.getenv('OPENAI_MODEL', 'gpt-4o-mini'),
        api_key=os.getenv('OPENAI_API_KEY'),
        base_url=os.getenv('OPENAI_BASE_URL') or None,
        temperature=0,
        timeout=timeout,
        max_retries=1,
    )


def _has_llm():
    enabled = str(os.getenv('LLM_ENABLED', 'false')).lower() == 'true'
    key = bool(os.getenv('OPENAI_API_KEY') or os.getenv('DASHSCOPE_API_KEY'))
    return enabled and key


def _fallback(state):
    state['llm_status'] = 'unavailable'
    state['error'] = 'LLM_ENABLED=false 或未配置 LLM 凭据（OPENAI_API_KEY / DASHSCOPE_API_KEY）'
    return state


def _retrieve_context(state):
    state['context'] = json.dumps(state.get('sources', []), ensure_ascii=False)[:24000]
    return state


def _topic_summary(state):
    model = state.get('model')
    if model is None:
        return state
    instruction = '输出 JSON，字段：topic_summary(字符串), opinion_clusters(数组，每项含 label 与 source_id), sentiment(对象含 positive/neutral/negative), risks(字符串数组), conclusion(字符串)。只基于来源，不要猜测。'
    content = _llm_call(model, '你是舆情分析师。', f"主题：{state['query']}\n来源：{state['context']}\n{instruction}")
    state['llm_result'] = _extract_json(content)
    state['llm_status'] = 'available'
    state['model_name'] = os.getenv('DASHSCOPE_MODEL') or os.getenv('OPENAI_MODEL', 'gpt-4o-mini')
    return state


def _opinion_clustering(state):
    rule = state.get('rule_insight', {})
    llm = state.get('llm_result', {})
    clusters = llm.get('opinion_clusters')
    if clusters:
        state['opinion_clusters'] = clusters
    else:
        state['opinion_clusters'] = [
            {'id': c.get('source_id', ''), 'label': c.get('claim', ''), 'source_id': c.get('source_id', '')}
            for c in rule.get('claims', [])
        ]
    return state


def _sentiment_analysis(state):
    state['sentiment'] = state.get('llm_result', {}).get('sentiment') or state.get('rule_insight', {}).get('sentiment', {})
    return state


def _risk_detection(state):
    state['risks'] = state.get('llm_result', {}).get('risks') or state.get('rule_insight', {}).get('risk_points', [])
    return state


def _conclusion_generation(state):
    state['conclusion'] = state.get('llm_result', {}).get('conclusion') or state.get('rule_insight', {}).get('summary', '')
    return state


def build_graph():
    from langgraph.graph import END, StateGraph
    graph = StateGraph(InsightState)
    graph.add_node('retrieve_context', _retrieve_context)
    graph.add_node('topic_summary', _topic_summary)
    graph.add_node('opinion_clustering', _opinion_clustering)
    graph.add_node('sentiment_analysis', _sentiment_analysis)
    graph.add_node('risk_detection', _risk_detection)
    graph.add_node('conclusion_generation', _conclusion_generation)
    graph.set_entry_point('retrieve_context')
    for head, tail in (('retrieve_context', 'topic_summary'), ('topic_summary', 'opinion_clustering'), ('opinion_clustering', 'sentiment_analysis'), ('sentiment_analysis', 'risk_detection'), ('risk_detection', 'conclusion_generation'), ('conclusion_generation', END)):
        graph.add_edge(head, tail)
    return graph.compile()


_compiled = None


def _get_graph():
    global _compiled
    if _compiled is None:
        _compiled = build_graph()
    return _compiled


def run_insight_graph(query, sources, rule_insight):
    """Run the six-node LLM insight graph; unavailable when no credentials."""
    state = {'query': query, 'sources': sources, 'rule_insight': rule_insight}
    if not _has_llm():
        return _fallback(state)
    try:
        state['model'] = _resolve_model()
        return _get_graph().invoke(state)
    except Exception as exc:
        return {'query': query, 'llm_status': 'unavailable', 'error': str(exc)[:1000]}

"""Analysis pipeline orchestrated as a LangGraph state machine.

Each analysis stage is a node in a linear graph, so stages are observable,
retryable, and independently replaceable while ``analyze()`` keeps the same
signature and return shape for existing callers (Worker and tests).
"""
from datetime import datetime
from typing import List, TypedDict

from input import collect_all
from .topic_search import run as topic_search
from .text_pipeline import run as text_process
from .multimedia_search import run as multimedia_search
from .forum_collection import run as forum_collection
from .insight_analysis import run as insight_analysis
from .report_generation import run as report_generation
from .graph_query import run as graph_query
from .vector_retrieval import index_sources
from .llm_graph import run_insight_graph
from utils.observability import METRICS, emit_event, get_trace_id, set_trace_id


class AnalysisState(TypedDict, total=False):
    query: str
    source_types: List[str]
    report_id: str
    executed_at: str
    sources: List[dict]
    input_errors: List[dict]
    text_stage: dict
    vector_stage: dict
    search: dict
    multimedia: list
    forum: list
    insight: dict
    llm: dict
    stages: dict
    report: str
    graph: dict
    result: dict


def _collect(state):
    sources, input_errors = collect_all(state['query'], state['source_types'])
    return {'sources': sources, 'input_errors': input_errors}


def _text(state):
    text_stage = text_process(state['sources'])
    return {'text_stage': text_stage, 'sources': text_stage['sources']}


def _vector(state):
    try:
        vector_stage = index_sources(state['sources'], state['report_id'])
    except Exception as exc:
        vector_stage = {'status': 'unavailable', 'indexed': 0, 'error': str(exc)[:500]}
    return {'vector_stage': vector_stage}


def _search(state):
    search = topic_search(state['query'], state['sources'])
    multimedia = multimedia_search(state['sources'])
    forum = forum_collection(state['sources'])
    return {'search': search, 'sources': search['sources'], 'multimedia': multimedia, 'forum': forum}


def _insight(state):
    insight = insight_analysis(state['sources'], state['input_errors'])
    llm = run_insight_graph(state['query'], state['sources'], insight)
    if llm.get('llm_status') == 'available':
        insight['llm'] = llm
    return {'insight': insight, 'llm': llm}


def _report(state):
    stages = {
        'text_processing': state['text_stage'],
        'embedding': state['vector_stage'],
        'topic_search': state['search'],
        'multimedia_search': {'count': len(state['multimedia'])},
        'forum_collection': {'count': len(state['forum'])},
        'insight_analysis': state['insight'],
        'llm_insight': state['llm'],
    }
    report = report_generation(state['query'], state['report_id'], state['executed_at'], state['sources'], state['insight'], stages)
    graph = graph_query(state['query'], state['sources'], state['insight'])
    graph.update({'report_id': state['report_id'], 'created_at': state['executed_at']})
    return {'stages': stages, 'report': report, 'graph': graph}


def _finalize(state):
    result = {
        'report_id': state['report_id'],
        'query': state['query'],
        'executed_at': state['executed_at'],
        'sources': state['sources'],
        'timeline': state['search']['timeline'],
        'sentiment': state['insight']['sentiment'],
        'summary': state['insight']['summary'],
        'input_errors': state['input_errors'],
        'source_stats': {kind: sum(1 for s in state['sources'] if s.get('type') == kind) for kind in ('news', 'image', 'video', 'forum_post', 'internal_data')},
        'claims': state['insight'].get('claims', []),
        'risk_points': state['insight'].get('risk_points', []),
        'evidence_ids': sorted({c.get('source_id') for c in state['insight'].get('claims', []) if c.get('source_id')}),
        'source_quality': state['insight'].get('source_quality', {}),
        'llm': state['llm'],
        'events': state['insight'].get('events', []),
        'entities': state['insight'].get('entities', []),
        'text_processing': state['text_stage']['stats'],
        'analysis_stages': list(state['stages']),
        'evidence_quality': state['insight'].get('confidence', 'low'),
        'graph_path': f"/api/graph/{state['report_id']}",
    }
    return {'result': result}


def _timed_node(name, function):
    """Wrap a LangGraph node with stage timing and structured events."""
    def run(state):
        started = __import__('time').perf_counter()
        status = 'success'
        try:
            return function(state)
        except Exception:
            status = 'error'
            raise
        finally:
            elapsed = __import__('time').perf_counter() - started
            METRICS.observe('opinion_analysis_stage_seconds', elapsed, {'stage': name, 'status': status})
            emit_event('analysis_stage', stage=name, status=status, report_id=state.get('report_id'),
                       duration_ms=round(elapsed * 1000, 3))
    return run


def build_graph():
    from langgraph.graph import END, StateGraph
    graph = StateGraph(AnalysisState)
    graph.add_node('collect', _timed_node('collect', _collect))
    graph.add_node('text', _timed_node('text_processing', _text))
    graph.add_node('vector', _timed_node('embedding', _vector))
    graph.add_node('search', _timed_node('retrieval', _search))
    graph.add_node('insight', _timed_node('insight', _insight))
    graph.add_node('report', _timed_node('report', _report))
    graph.add_node('finalize', _timed_node('finalize', _finalize))
    graph.set_entry_point('collect')
    graph.add_edge('collect', 'text')
    graph.add_edge('text', 'vector')
    graph.add_edge('vector', 'search')
    graph.add_edge('search', 'insight')
    graph.add_edge('insight', 'report')
    graph.add_edge('report', 'finalize')
    graph.add_edge('finalize', END)
    return graph.compile()


_compiled = None


def _get_graph():
    global _compiled
    if _compiled is None:
        _compiled = build_graph()
    return _compiled


def analyze(query, source_types, report_id):
    """Run the full analysis pipeline and return ``{result, report, graph}``.

    Signature and return shape are kept stable for Worker and tests.
    """
    executed_at = datetime.now().astimezone().isoformat(timespec='microseconds')
    initial = {'query': query, 'source_types': source_types, 'report_id': report_id, 'executed_at': executed_at}
    started = __import__('time').perf_counter()
    incoming_trace = get_trace_id()
    trace_token = set_trace_id(None if incoming_trace == 'tr-untracked' else incoming_trace)
    try:
        # LangGraph remains the source of truth; wrappers emit stage timings.
        final = _get_graph().invoke(initial)
        METRICS.inc('opinion_analysis_total', {'status': 'success'})
        emit_event('analysis_succeeded', report_id=report_id, query=query[:120], duration_ms=round((__import__('time').perf_counter() - started) * 1000, 3))
        return {'result': final['result'], 'report': final['report'], 'graph': final['graph']}
    except Exception as exc:
        METRICS.inc('opinion_analysis_total', {'status': 'error'})
        emit_event('analysis_failed', report_id=report_id, query=query[:120], error=str(exc)[:500], duration_ms=round((__import__('time').perf_counter() - started) * 1000, 3))
        raise
    finally:
        from utils.observability import reset_trace_id
        reset_trace_id(trace_token)

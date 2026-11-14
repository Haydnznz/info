import json
import unittest
from unittest.mock import patch

from processor.llm_graph import _extract_json, build_graph, run_insight_graph


class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _FakeModel:
    def __init__(self, content):
        self.content = content

    def invoke(self, messages, **kwargs):
        return _FakeResponse(self.content)


class LLMGraphTests(unittest.TestCase):
    def test_extract_json_from_markdown_fence(self):
        self.assertEqual({'a': 1}, _extract_json('```json\n{"a": 1}\n```'))
        self.assertEqual({'a': 1}, _extract_json('前缀 {"a": 1} 后缀'))
        self.assertEqual([1, 2], _extract_json('[1, 2]'))

    def test_graph_runs_with_injected_model(self):
        payload = {
            'topic_summary': '摘要',
            'opinion_clusters': [{'label': '观点', 'source_id': 's1'}],
            'sentiment': {'positive': 50, 'neutral': 30, 'negative': 20},
            'risks': ['风险1'],
            'conclusion': '结论',
        }
        model = _FakeModel(json.dumps(payload, ensure_ascii=False))
        state = build_graph().invoke({
            'query': '主题',
            'sources': [{'source_id': 's1', 'title': 't', 'summary': 's'}],
            'rule_insight': {'claims': [], 'sentiment': {}, 'risk_points': [], 'summary': ''},
            'model': model,
        })
        self.assertEqual('available', state['llm_status'])
        self.assertEqual([{'label': '观点', 'source_id': 's1'}], state['opinion_clusters'])
        self.assertEqual({'positive': 50, 'neutral': 30, 'negative': 20}, state['sentiment'])
        self.assertEqual(['风险1'], state['risks'])
        self.assertEqual('结论', state['conclusion'])

    def test_unavailable_without_credentials(self):
        with patch.dict('os.environ', {'LLM_ENABLED': 'false', 'OPENAI_API_KEY': '', 'DASHSCOPE_API_KEY': ''}, clear=False):
            result = run_insight_graph('q', [], {})
        self.assertEqual('unavailable', result['llm_status'])


if __name__ == '__main__':
    unittest.main()

import os
import unittest
from unittest.mock import patch

from input.mcp_search import _extract, _tool_arguments, search


class MCPSearchTests(unittest.TestCase):
    def test_extract_structured_citations(self):
        values = _extract({'results': [{'title': '来源 A', 'url': 'https://example.com/a', 'snippet': '摘要'}]})
        self.assertEqual(1, len(values))
        self.assertEqual('https://example.com/a', values[0]['url'])
        self.assertEqual('volcengine_mcp_search', values[0]['provider'])

    def test_extract_volcengine_web_results(self):
        values = _extract({'result': {'Result': {'ResultCount': 2, 'WebResults': [
            {'Title': '标题A', 'Url': 'https://example.com/a', 'Snippet': '摘要A', 'PublishTime': '2026-11-06T00:00:00+08:00'},
            {'Title': '标题B', 'Url': 'https://example.com/b', 'Summary': '摘要B'},
        ]}}})
        self.assertEqual(2, len(values))
        self.assertEqual('https://example.com/a', values[0]['url'])
        self.assertEqual('标题A', values[0]['title'])
        self.assertEqual('2026-11-06T00:00:00+08:00', values[0]['published_at'])
        self.assertEqual('news', values[0]['type'])

    def test_tool_arguments_follow_schema(self):
        class Tool:
            inputSchema = {'properties': {'search_query': {'type': 'string'}, 'max_results': {'type': 'integer'}}}
        self.assertEqual({'search_query': 'q', 'max_results': 5}, _tool_arguments(Tool(), 'q', 5))

    def test_stdio_requires_mcp_credentials(self):
        with patch.dict(os.environ, {'MCP_SEARCH_MODE': 'stdio', 'ASK_ECHO_SEARCH_INFINITY_API_KEY': '', 'VOLCENGINE_ACCESS_KEY': '', 'VOLCENGINE_SECRET_KEY': ''}, clear=False):
            items, error = search('q', {'collection': {'max_records': 3}})
        self.assertEqual([], items)
        self.assertIn('ASK_ECHO_SEARCH_INFINITY_API_KEY', error)


if __name__ == '__main__':
    unittest.main()

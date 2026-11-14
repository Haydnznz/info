"""MCP client for Volcengine AskEcho Search Infinity.

The official server is launched with ``uv run`` in stdio mode by default. SSE
and Streamable HTTP are supported for deployments where the MCP server is
managed separately. This module keeps the async MCP SDK behind a synchronous
collector boundary used by the existing input pipeline.
"""
import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse


SERVER_PACKAGE = 'mcp-server-askecho-search-infinity'


def _text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ''.join(_text(x) for x in value)
    if isinstance(value, dict):
        return _text(value.get('text') or value.get('content') or value.get('value') or '')
    text = getattr(value, 'text', None)
    if isinstance(text, str):
        return text
    content = getattr(value, 'content', None)
    if content is not None:
        return _text(content)
    return ''


def _source(title, url, summary, published_at=None):
    title = (title or '豆包联网搜索结果').strip()
    url = (url or '').strip()
    raw = f'mcp-volcengine|{url}|{title}'.encode('utf-8')
    return {'source_id': hashlib.sha256(raw).hexdigest()[:20], 'type': 'news', 'title': title[:500], 'url': url,
            'summary': _text(summary).strip()[:2000], 'published_at': published_at,
            'collected_at': datetime.now(timezone.utc).isoformat(), 'input_family': 'public_info',
            'provider': 'volcengine_mcp_search'}


def _norm(name):
    """Normalize a field name so camelCase, snake_case and kebab-case match."""
    return str(name).lower().replace('_', '').replace('-', '').replace(' ', '')


def _extract(value, limit=20):
    """Extract URLs and structured citations from arbitrary MCP tool output."""
    results = []
    if isinstance(value, dict):
        index = {_norm(k): k for k in value}
        # Recurse into known container fields; Volcengine Search Infinity nests
        # results under result -> Result -> WebResults (camelCase).
        containers = ('results', 'webresults', 'imageresults', 'items', 'data', 'citations', 'sources', 'result')
        for name in containers:
            if name in index and isinstance(value[index[name]], (dict, list)):
                results.extend(_extract(value[index[name]], limit))
        url = value.get(index.get('url')) or value.get(index.get('link')) or value.get(index.get('sourceurl'))
        if url:
            title = value.get(index.get('title')) or value.get(index.get('name')) or value.get(index.get('sitename'))
            summary = (value.get(index.get('snippet')) or value.get(index.get('summary'))
                       or value.get(index.get('content')) or value.get(index.get('text'))
                       or value.get(index.get('description')))
            published = (value.get(index.get('publishedat')) or value.get(index.get('publishtime'))
                         or value.get(index.get('date')))
            results.append(_source(title, url, summary, published))
        if not results:
            results.extend(_extract(_text(value), limit))
    elif isinstance(value, list):
        for item in value:
            results.extend(_extract(item, limit))
    elif isinstance(value, str):
        # Preserve URLs even when the server returns plain text/markdown.
        for url in re.findall(r'https?://[^\s)\]}>]+', value):
            results.append(_source(urlparse(url).netloc, url.rstrip('.,;'), value))
    unique = {}
    for item in results:
        unique[item['source_id']] = item
    return list(unique.values())[:limit]


def _tool_arguments(tool, query, limit):
    schema = getattr(tool, 'inputSchema', None) or {}
    properties = schema.get('properties', {}) if isinstance(schema, dict) else {}
    normalized = {str(key).lower(): key for key in properties}
    args = {}
    for key in ('query', 'search_query', 'keyword', 'keywords', 'q', 'question'):
        if key in normalized:
            args[normalized[key]] = query
            break
    for key in ('limit', 'max_results', 'count', 'top_k'):
        if key in normalized:
            args[normalized[key]] = limit
            break
    if 'count' in normalized and normalized['count'] not in args:
        args[normalized['count']] = limit
    return args or {'query': query, 'limit': limit}


async def _call_stdio(query, limit):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    uv = os.getenv('MCP_UV_COMMAND', 'uv')
    cwd = os.getenv('MCP_SERVER_CWD', '').strip() or None
    if not cwd:
        raise RuntimeError('MCP_SERVER_CWD 未配置，请填写 mcp-server/server/mcp_server_askecho_search_infinity 目录')
    env = dict(os.environ)
    cache_dir = os.getenv('MCP_UV_CACHE_DIR', '').strip()
    if cache_dir:
        if not os.path.isabs(cache_dir):
            from utils.settings import ROOT
            cache_dir = str(ROOT / cache_dir)
        env['UV_CACHE_DIR'] = cache_dir
    else:
        from utils.settings import ROOT
        local_cache = str(ROOT / 'runtime' / 'uv-cache')
        os.makedirs(local_cache, exist_ok=True)
        env['UV_CACHE_DIR'] = local_cache
    # Official docs use this key; support AK/SK as an alternative.
    params = StdioServerParameters(command=uv, args=['run', SERVER_PACKAGE], env=env, cwd=cwd)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            preferred = os.getenv('MCP_SEARCH_TOOL', '').strip()
            tool = next((x for x in tools.tools if x.name == preferred), None) if preferred else next((x for x in tools.tools if any(k in x.name.lower() for k in ('search', 'askecho', 'web'))), None)
            if not tool:
                raise RuntimeError('MCP Server 未发现搜索工具；可设置 MCP_SEARCH_TOOL')
            result = await session.call_tool(tool.name, _tool_arguments(tool, query, limit))
            return result


async def _call_remote(query, limit):
    from mcp import ClientSession
    from mcp.client.sse import sse_client
    from mcp.client.streamable_http import streamable_http_client
    url = os.getenv('MCP_SEARCH_URL', '').strip()
    headers = {}
    if os.getenv('ASK_ECHO_SEARCH_INFINITY_API_KEY'):
        headers['Authorization'] = 'Bearer ' + os.getenv('ASK_ECHO_SEARCH_INFINITY_API_KEY')
    transport = os.getenv('MCP_SEARCH_TRANSPORT', 'sse').lower()
    factory = sse_client(url, headers=headers) if transport == 'sse' else streamable_http_client(url)
    async with factory as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            preferred = os.getenv('MCP_SEARCH_TOOL', '').strip()
            tool = next((x for x in tools.tools if x.name == preferred), None) if preferred else next((x for x in tools.tools if 'search' in x.name.lower()), None)
            if not tool:
                raise RuntimeError('MCP Server 未发现搜索工具；可设置 MCP_SEARCH_TOOL')
            return await session.call_tool(tool.name, _tool_arguments(tool, query, limit))


def _run(coro):
    try:
        return asyncio.run(coro)
    except RuntimeError as exc:
        if 'asyncio.run() cannot be called' in str(exc):
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()
        raise


def search(query, settings=None):
    settings = settings or {}
    limit = max(1, min(int(settings.get('collection', {}).get('max_records', 20)), 50))
    mode = os.getenv('MCP_SEARCH_MODE', 'stdio').lower()
    if mode in ('sse', 'streamable-http', 'streamable_http'):
        if not os.getenv('MCP_SEARCH_URL'):
            return [], 'MCP_SEARCH_URL 未配置'
        caller = _call_remote(query, limit)
    else:
        if not os.getenv('ASK_ECHO_SEARCH_INFINITY_API_KEY') and not (os.getenv('VOLCENGINE_ACCESS_KEY') and os.getenv('VOLCENGINE_SECRET_KEY')):
            return [], '未配置 ASK_ECHO_SEARCH_INFINITY_API_KEY 或 VOLCENGINE_ACCESS_KEY/VOLCENGINE_SECRET_KEY'
        caller = _call_stdio(query, limit)
    try:
        result = _run(caller)
        payload = getattr(result, 'structuredContent', None) or {'content': getattr(result, 'content', [])}
        if getattr(result, 'isError', False):
            details = _text(payload)
            if not details:
                details = '；'.join(_text(x) for x in (getattr(result, 'content', []) or []))
            return [], '豆包 MCP 搜索工具返回错误：' + (details or '未知错误')[:800]
        return _extract(payload, limit), None
    except Exception as exc:
        return [], f'豆包 MCP 搜索失败：{type(exc).__name__}: {str(exc)[:800]}'

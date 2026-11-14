# 信息输入层

本目录当前以豆包联网搜索为主要真实信息输入，并将来源证据写入 MongoDB；同时保留可选的 RSS、GDELT 和多媒体适配器。默认不会注入演示内部数据：

- `public_info.py`：豆包联网搜索、GDELT 新闻 API 和配置的 RSS；
- `multimedia.py`：Wikimedia Commons 图片 API、可选 YouTube Data API；
- `internal_data.py`：本地 JSON/JSONL/CSV/Markdown 数据，后续可接 MongoDB。

默认不抓取任意网页，避免 robots、登录态和反爬问题。论坛采集通过 `.env` 的 `FORUM_RSS_URLS` 配置受许可的 RSS/Atom 地址。每次输入运行写入 MongoDB 的 `input_runs`，每条来源写入 `input_sources`，并通过 `run_id` 关联。

豆包搜索配置：

```json
"volcengine_search": {
  "enabled": true,
  "api_key_env": "VOLCENGINE_API_KEY",
  "model": "你的方舟接入点 ID",
  "mode": "responses"
}
```

API Key、endpoint 和模型 ID 均从 `.env` 读取，不写入 `config.json`。如果你的网页文档给出的是 Chat Completions 调用方式，将配置中的 `mode` 改为 `chat`。

输入层不生成报告、图谱或前端输出。统一来源对象后交给 `processor/`。

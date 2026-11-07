# 舆情分析平台

这是一个分布式舆情分析平台。当前环境依赖已记录在 `requirements.txt`，覆盖 FastAPI/Uvicorn、LangGraph/LangChain、OpenAI、Milvus、MinIO、MongoDB、向量模型和数据处理组件。后端拆分为 Coordinator（控制节点）和可水平扩展的 HTTP Worker。

依赖安装：

```powershell
python -m pip install -r requirements.txt
```

真实信息采集目前直接使用已安装的 `requests`、标准库 XML/CSV/JSON 解析；不需要新增依赖。图片使用 Wikimedia API，视频需要配置 YouTube Data API key；内部数据可放入 `data/internal/`。后续如果需要网页渲染、OCR 或音视频转写，再单独安装浏览器、OCR 或 FFmpeg 工具。

豆包搜索接入已切换为官方 MCP Server。默认 stdio 模式会由平台通过 `uv run mcp-server-askecho-search-infinity` 自动拉起服务器；在 `.env` 配置 `ASK_ECHO_SEARCH_INFINITY_API_KEY`，或配置 `VOLCENGINE_ACCESS_KEY`/`VOLCENGINE_SECRET_KEY`。SSE 和 Streamable HTTP 可通过 `MCP_SEARCH_MODE`、`MCP_SEARCH_URL` 和 `MCP_SEARCH_TRANSPORT` 切换。旧的 `VOLCENGINE_API_KEY`/Responses API 不再作为搜索入口。

MCP 本地启动：

```powershell
cd <mcp-server>\server\mcp_server_askecho_search_infinity
uv run mcp-server-askecho-search-infinity
```

项目启动：

```powershell
uv run python app.py
```

代码按职责拆分为 `input/`（三类信息输入）、`processor/`（六类分析处理）、`web/`（接口）、`frontend/`（页面）、`config/`（配置）、`tool/`（Worker）、`utils/`（通用能力）和 `test/`（测试）。

## 启动

```powershell
python app.py
```

这会在本机启动 1 个 Coordinator 和 2 个 Worker。打开 http://127.0.0.1:9000 。首次启动会自动生成 `config/config.json`、`runtime/`、`outputs/`、`reports/` 和 `graphs/` 目录。

分开启动节点：

```powershell
$env:OPINION_WORKER_TOKEN = 'replace-with-a-long-random-secret'
python app.py api --host 0.0.0.0 --port 9000
python app.py worker --coordinator http://127.0.0.1:9000 --worker-id worker-1
python app.py worker --coordinator http://127.0.0.1:9000 --worker-id worker-2
```

Docker Compose：

```powershell
$env:OPINION_WORKER_TOKEN = 'replace-with-a-long-random-secret'
docker compose up --build --scale worker=3
```

主题检索现在默认异步执行：`POST /api/search` 返回 `task_id`，前端轮询 `GET /api/tasks/{task_id}`。FastAPI 是唯一 HTTP 入口：`uvicorn web.fastapi_app:app --host 0.0.0.0 --port 9000`（`python app.py api` 也启动同一服务）。

## BGE-M3 与 Milvus

`.env` 支持本地 BGE-M3：`BGE_M3_PATH`、`BGE_M3`、`BGE_DEVICE` 和 `BGE_FP16`。当前本机已验证本地模型可输出 1024 维向量。Milvus 连接优先读取 `MILVUS_URL`，也兼容 `MILVUS_URI` 或 `MILVUS_HOST`/`MILVUS_PORT`；服务不可达时任务会保留规则分析，并在 `analysis_stages.embedding` 标记 `unavailable`，不会伪造向量检索结果。`D:\\Ubuntu\\2` 是 Milvus Compose 文件目录。

LLM LangGraph 由 `LLM_ENABLED=true` 和 `OPENAI_API_KEY` 显式启用；未配置时结果中的 `llm_status` 为 `unavailable`。多媒体处理位于 `processor/media_pipeline.py`，关键帧需要 FFmpeg，OCR/ASR 通过注入客户端或 OpenAI 兼容接口接入。

## 接口

已实现：`GET /api/status`、`GET /api/start/{app_name}`、`GET /api/stop/{app_name}`、`GET /api/output/{app_name}`、`GET /api/test_log/{app_name}`、`POST /api/search`、`GET /api/tasks/{task_id}`、`GET /api/workers`、`GET /api/events`、`GET /api/forum/start`、`GET /api/forum/stop`、`GET /api/forum/log`、`POST /api/forum/log/history`、`GET/POST /api/config`、`GET /api/system/status`、`POST /api/system/start`、`POST /api/system/shutdown`、`GET /api/graph/latest`、`GET /api/graph/{report_id}`、`POST /api/graph/query`、`GET /graph-viewer`。

Worker 内部接口位于 `/internal/*`，必须使用 `Authorization: Bearer $OPINION_WORKER_TOKEN`，不应暴露给公网。

报告包含事件概述、时间线、传播渠道、主要观点、情绪倾向、风险判断、重点证据和结论摘要；图谱节点、关系均带来源引用。

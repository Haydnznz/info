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

## 鉴权与角色权限

公开 API 默认启用 Bearer Token 鉴权。先在 `.env` 设置 `AUTH_SECRET`、管理员和其他角色密码，然后登录：

```powershell
$body = @{username='admin'; password='change-me-admin'} | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:9000/api/auth/login -Method Post -ContentType 'application/json' -Body $body
```

登录响应中的 `access_token` 通过 `Authorization: Bearer <token>` 发送。角色权限如下：

| 角色 | 权限 |
| --- | --- |
| `operator` 操作用户 | 发起主题分析、启停采集应用、查看任务、报告和图谱 |
| `report_viewer` 报告查看人 | 查看任务、报告、证据、论坛日志和图谱 |
| `admin` 系统管理员 | 包含上述权限，并可修改配置、整体启停、删除报告和回滚配置 |

配置、整体启停、报告删除等管理动作会写入 `runtime/audit.log`。Worker 内部接口仍使用独立的 `OPINION_WORKER_TOKEN`。本地临时调试可设置 `AUTH_ENABLED=false`，部署环境必须保持开启并更换默认密码。

浏览器访问平台时会先进入独立登录页 `/login`，登录成功后自动返回原访问地址。登录页提供“忘记密码”窗口；管理员需在服务端配置 `AUTH_PASSWORD_RESET_TOKEN`，重置请求才会生效。重置仅支持默认三个环境账号；使用 `AUTH_USERS_JSON` 的账号由管理员在用户目录中维护。

## BGE-M3 与 Milvus

`.env` 支持本地 BGE-M3：`BGE_M3_PATH`、`BGE_M3`、`BGE_DEVICE` 和 `BGE_FP16`。当前本机已验证本地模型可输出 1024 维向量。Milvus 连接优先读取 `MILVUS_URL`，也兼容 `MILVUS_URI` 或 `MILVUS_HOST`/`MILVUS_PORT`；服务不可达时任务会保留规则分析，并在 `analysis_stages.embedding` 标记 `unavailable`，不会伪造向量检索结果。`D:\\Ubuntu\\2` 是 Milvus Compose 文件目录。

LLM LangGraph 由 `LLM_ENABLED=true` 和 `OPENAI_API_KEY` 显式启用；未配置时结果中的 `llm_status` 为 `unavailable`。多媒体处理位于 `processor/media_pipeline.py`，关键帧需要 FFmpeg，OCR/ASR 通过注入客户端或 OpenAI 兼容接口接入。

## 接口

已实现：`POST /api/auth/login`、`GET /api/auth/me`、`GET /api/status`、`GET/POST /api/start/{app_name}`、`GET/POST /api/stop/{app_name}`、`GET /api/output/{app_name}`、`GET /api/test_log/{app_name}`、`POST /api/search`、`GET /api/tasks/{task_id}`、`GET /api/workers`、`GET /api/events`、`GET/POST /api/forum/start`、`GET/POST /api/forum/stop`、`GET /api/forum/log`、`POST /api/forum/log/history`、`GET/POST /api/config`、`GET /api/system/status`、`POST /api/system/start`、`POST /api/system/shutdown`、`GET /api/graph/latest`、`GET /api/graph/{report_id}`、`POST /api/graph/query`、`GET /graph-viewer`。除健康检查、登录和 Worker 内部接口外，API 均需要用户 Bearer Token。

Worker 内部接口位于 `/internal/*`，必须使用 `Authorization: Bearer $OPINION_WORKER_TOKEN`，不应暴露给公网。

报告包含事件概述、时间线、传播渠道、主要观点、情绪倾向、风险判断、重点证据和结论摘要；图谱节点、关系均带来源引用。

## 可观测性

平台现在提供三类观测数据：

- `X-Trace-Id`：每次 HTTP 请求自动生成或透传，并写入任务负载，用于串联请求、任务、Worker 和分析阶段。
- `/metrics`：Prometheus 文本格式，包含请求数、请求耗时、队列任务数、Worker 数、任务成功/失败数、分析阶段耗时和重试数。
- `GET /api/observability`：管理员查看当前队列、Worker 和进程内指标快照。

结构化 JSONL 事件写入：

```text
runtime/observability.jsonl
```

事件包含 HTTP 请求、任务入队/成功/失败、Worker 执行和 LangGraph 分析阶段耗时。指标注册表是进程级的，Prometheus 应分别抓取 Coordinator；任务状态和业务结果仍以 SQLite、报告和图谱文件为准。

## 质量检查

GitHub Actions 工作流位于 `.github/workflows/quality.yml`，在 `main`、`dev` 分支的 push 和 pull request 上执行 Ruff、现有单元/集成测试和 Playwright Chromium 浏览器冒烟测试。CI 使用轻量的 `requirements-ci.txt`，不会下载 BGE 等本地模型依赖。

本机运行浏览器冒烟测试：

```powershell
python -m pip install -r requirements-ci.txt
python -m playwright install chromium
python test/e2e/test_login_flow.py
```

浏览器测试会在临时数据目录启动服务，验证未登录跳转、登录、报告 Markdown 新页面显示和退出登录。当前 Git remote 指向 Gitee；GitHub Actions 只有在代码推送到 GitHub 仓库后才会自动触发。你需要将仓库推送/镜像到 GitHub，并在 GitHub Actions 页面确认工作流已启用。

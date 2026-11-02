# 舆情分析平台开发文档

## 1. 文档目的

本文档用于说明当前代码的实现方式、运行方法、接口契约、数据目录、扩展边界和后续开发建议。当前版本是一个可运行的单进程演示版，目标是先打通“采集/分析请求 → 报告 → 图谱 → Web 查看”的闭环，后续可以将模拟逻辑替换为真实采集器、模型服务和持久化数据库。

## 2. 当前版本能力

当前版本使用 Python 标准库，不需要安装第三方依赖，包含：

- Web 控制台：控制台、主题检索、论坛监控、图谱查看、配置管理。
- 单功能应用控制：`topic_search`、`multimedia_search`、`insight_analysis`、`report_generator`。
- 应用状态：`stopped`、`starting`、`running`、`stopping`、`failed`（当前演示逻辑主要使用前四种）。
- 论坛状态：`idle`、`running`、`stopped`。
- 系统状态：`offline`、`online`、`shutting_down`。
- 主题分析：按来源类型生成示例来源、时间线、情绪、风险、报告和图谱。
- 文件输出：应用日志、论坛日志、报告 Markdown、来源 JSON、图谱 JSON、应用最近输出。
- 分布式执行：Coordinator 统一接收请求和保存结果，多个无状态 Worker 通过 HTTP 领取分析任务。
- 可靠任务机制：SQLite 事务领取、租约、心跳、过期重投、最多 3 次尝试、结果提交幂等。

## 3. 快速启动

环境要求：Python 3.9 或更高版本。当前环境依赖清单见 `requirements.txt`，包含 FastAPI、Uvicorn、Pydantic、LangGraph、LangChain、OpenAI、Milvus、MinIO、MongoDB、FlagEmbedding、sentence-transformers、Transformers、DashScope、MCP、NumPy 和 Pandas 等。安装命令为 `python -m pip install -r requirements.txt`。

```powershell
cd "D:\AI learn\info"
python app.py
```

服务默认监听 `0.0.0.0:9000`，浏览器打开：

```text
http://127.0.0.1:9000
```

停止服务使用 `Ctrl+C`。首次运行会创建配置和运行目录。端口读取自 `config/config/config.json` 的 `system.port`。

## 3.1 按职责划分的工程结构

项目目录按截图所示的职责方式组织：

```text
info/
├─ config/                       # 配置文件、配置校验和环境配置
├─ input/                        # 信息输入层（三类来源）
│  ├─ public_info.py              # 公开信息输入
│  ├─ multimedia.py               # 多媒体内容输入
│  └─ internal_data.py             # 内部沉淀数据输入
├─ processor/                    # 信息处理分析层
│  ├─ topic_search.py              # 主题检索
│  ├─ multimedia_search.py        # 多媒体检索
│  ├─ forum_collection.py         # 论坛采集
│  ├─ insight_analysis.py         # 洞察分析
│  ├─ report_generation.py        # 报告生成
│  └─ graph_query.py              # 图谱查询
├─ tool/                         # 可独立运行的 Worker 和运维工具
├─ utils/                        # 存储、时间、JSON 和通用工具
├─ web/                          # Web API、路由和控制层
├─ frontend/                     # 页面、脚本和样式
├─ test/                         # 自动化测试
├─ reports/ graphs/ outputs/     # 业务结果目录
├─ runtime/                      # 运行日志和 SQLite 状态库
├─ app.py                        # 统一启动入口
├─ .env.example                  # 环境变量模板
├─ pyproject.toml                # 项目元数据和工具配置
└─ requirements.txt              # 依赖清单
```

三类输入只存在于 `input/`，通过统一来源对象交给 `processor/`；六类处理能力只存在于 `processor/`；`frontend` 只负责展示和交互，`web` 只负责 HTTP 接口，`config`、`utils` 不承载页面或业务分析。旧的 `backend/inputs`、`backend/processing` 和 `backend/analysis.py` 已移除。

## 4. 项目结构

```text
info/
├─ app.py                         # 后端、状态管理、文件存储和 HTTP 路由
├─ config.json                    # 系统配置
├─ README.md                      # 快速说明
├─ DEVELOPMENT.md                # 本开发文档
├─ static/
│  └─ index.html                  # 单页 Web 控制台
├─ runtime/
│  ├─ apps/{app_name}.log         # 单功能应用日志
│  └─ forum/
│     ├─ latest.log               # 论坛最新日志（JSON Lines）
│     └─ history/{date}.json      # 按日期保存的论坛历史日志
├─ outputs/
│  └─ {app_name}/latest.txt       # 应用最近一次输出
├─ reports/
│  └─ {report_id}/
│     ├─ report.md                # 结构化报告
│     └─ sources.json             # 本次检索的来源证据
└─ graphs/
   └─ {report_id}/graph.json      # 报告对应的节点和边
```

历史运行中可能存在旧版生成的 `outputs/*latest.txt` 文件；新代码使用 `outputs/{app_name}/latest.txt`，后续可以删除旧文件。

## 5. 后端架构

当前后端拆分为 `backend/` 包，`app.py` 仅负责启动角色：

1. **`backend/api.py`**：Coordinator HTTP 控制层、公开 API 和 Worker 内部 API。
2. **`backend/storage.py`**：SQLite 任务队列、状态、Worker 心跳和事件表。
3. **`backend/services.py`**：控制层编排、应用状态、任务提交、结果提交和文件落盘。
4. **`backend/configuration.py`**：配置读写和字段校验。
5. **`backend/forum.py`**：论坛任务和历史日志。
6. **`backend/graph.py`**：最新图谱、报告图谱和查询。
7. **`backend/analysis.py`**：纯分析适配器，Worker 执行后只返回 JSON/Markdown。
8. **`backend/worker.py`**：独立 HTTP Worker，包含领取、心跳、分析、提交和失败重试。
9. **`utils/settings.py`**：数据根目录、时钟、JSON 原子写入和默认配置。

### 5.1 分布式拓扑

```text
浏览器 / API 客户端
          │ :9000
          ▼
Coordinator（HTTP + SQLite + 文件存储）
     │ /internal/claim、heartbeat、complete
     ├──────── Worker 1（无状态）
     ├──────── Worker 2（无状态）
     └──────── Worker N（无状态）
```

Coordinator 是唯一的写入者：任务状态、报告、图谱、论坛日志和应用输出都在 `OPINION_DATA_DIR` 下落盘。Worker 不需要共享文件系统，只需要能访问 Coordinator 的 HTTP 地址和相同的 Worker Token。生产环境建议把 SQLite 替换为 PostgreSQL，把内部 HTTP 替换为 mTLS，并把对象文件迁移到 S3/MinIO。

### 5.2 任务生命周期

```text
queued → running → succeeded
             │
             ├─ Worker 错误 → failed
             └─ lease 超时 → queued（最多 3 次）→ failed
```

`POST /api/search` 只负责入队。Worker 领取任务时获得 `token` 和 30 秒租约，每 5 秒发送心跳；提交结果时 Coordinator 校验任务、Worker、token 和租约。重复提交同一成功任务返回 `duplicate=true`。

推荐的下一步拆分结构：

```text
backend/
├─ main.py              # 启动入口
├─ api/routes.py        # 路由和请求校验
├─ services/apps.py     # 任务/进程管理
├─ services/search.py   # 搜索和分析编排
├─ services/forum.py    # 论坛采集
├─ services/graph.py    # 图谱读写和查询
├─ services/report.py   # 报告模板和导出
├─ storage/files.py     # 当前文件存储适配器
└─ models.py            # 数据结构和状态枚举
```

## 6. 状态模型

### 6.1 单功能应用状态

`app_state[app_name]` 的结构如下：

```json
{
  "status": "running",
  "output": "主题：人工智能大模型",
  "error": "",
  "started_at": "2026-11-01T20:14:48+08:00"
}
```

启动时：`stopped → starting → running`。停止时：`running → stopping → stopped`。真实实现中应在工作线程异常时设置 `failed`，并保存错误堆栈和退出码。

### 6.2 系统状态

当前整体启动接口将系统设置为 `online`，并异步启动所有应用；关闭接口停止所有应用并设置为 `shutting_down`。生产实现建议增加：

- 启动超时和失败回滚；
- 所有应用确认停止后再设置 `offline`；
- 服务进程 PID、心跳时间和资源占用；
- 多进程或任务队列，而不是仅依赖线程。

## 7. 配置说明

`config/config/config.json` 当前结构：

```json
{
  "system": {"name": "舆情分析平台", "port": 9000},
  "apps": {
    "topic_search": {"enabled": true},
    "multimedia_search": {"enabled": true},
    "insight_analysis": {"enabled": true},
    "report_generator": {"enabled": true}
  },
  "collection": {
    "keywords": ["人工智能", "大模型"],
    "interval_seconds": 5
  },
  "runtime": {"demo_mode": true}
}
```

配置更新目前是整文件覆盖并立即写入。后续应增加字段校验、版本号、敏感字段保护、并发更新控制和配置变更审计。

## 8. API 接口

所有 JSON 接口使用 UTF-8。错误处理建议统一为 `{"ok": false, "error": {"code": "...", "message": "..."}}`；当前演示版本部分错误会返回空对象或 HTTP 404。

### 8.1 状态和应用

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/status` | 返回系统状态、应用状态、论坛状态和最近错误 |
| GET | `/api/system/status` | 返回系统状态和运行中的应用 |
| GET | `/api/start/{app_name}` | 启动单个应用 |
| GET | `/api/stop/{app_name}` | 停止单个应用 |
| GET | `/api/output/{app_name}` | 返回应用最近输出 |
| GET | `/api/test_log/{app_name}` | 返回应用日志文本 |
| POST | `/api/system/start` | 启动整体服务 |
| POST | `/api/system/shutdown` | 关闭整体服务 |

`app_name` 可选：`topic_search`、`multimedia_search`、`insight_analysis`、`report_generator`。

### 8.2 主题检索

请求：

```http
POST /api/search
Content-Type: application/json

{"query":"人工智能大模型","source_types":["news","forum_post","internal_data"]}
```

返回字段包括：`report_id`、`query`、`executed_at`、`sources`、`timeline`、`sentiment`、`summary`、`graph_path`。支持的来源类型：`news`、`image`、`video`、`forum_post`、`internal_data`。

每次请求会创建：

- `reports/{report_id}/report.md`
- `reports/{report_id}/sources.json`
- `graphs/{report_id}/graph.json`
- 每个应用的 `outputs/{app_name}/latest.txt`

### 8.3 论坛

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/forum/start` | 启动论坛采集 |
| GET | `/api/forum/stop` | 停止论坛采集 |
| GET | `/api/forum/log` | 返回最新日志 |
| POST | `/api/forum/log/history` | 请求体 `{"date":"2026-11-01"}`，返回日期历史 |

论坛事件结构：

```json
{"time":"2026-11-01T20:14:48+08:00","event_type":"collector","message_text":"论坛采集任务已启动","task_status":"running"}
```

### 8.4 图谱

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/graph/latest` | 返回按目录排序的最新图谱 |
| GET | `/api/graph/{report_id}` | 返回指定报告图谱 |
| POST | `/api/graph/query` | 按报告编号和节点标签过滤 |

图谱结构：

```json
{
  "report_id":"R20261101201448-bdec",
  "created_at":"2026-11-01T20:14:48+08:00",
  "nodes":[{"id":"topic","label":"人工智能大模型","type":"topic"}],
  "edges":[{"source":"topic","target":"risk","type":"关联风险","source_ref":"https://example.com/news/ai-001"}]
}
```

## 9. 报告规范

报告模板目前包含：事件概述、时间线、传播渠道、主要观点、情绪倾向、风险判断、重点证据、结论摘要。后续接入真实数据时，建议每个结论增加证据 ID，例如 `evidence_ids: ["src-001", "forum-021"]`，使结论到来源的追溯可以机器校验。

## 10. 接入真实采集与模型服务

建议把 `run_search` 拆成以下流水线：

1. 参数校验和任务创建，返回 `task_id`。
2. 新闻、图片、视频、论坛、内部数据并行采集。
3. 统一规范化：标题、正文、发布时间、作者、URL、媒体地址、来源类型。
4. 去重、语言识别、敏感词过滤和时间窗口过滤。
5. 情绪分类、观点聚类、实体识别、事件抽取和风险检测。
6. 生成带证据引用的报告和图谱。
7. 持久化任务状态、原始内容、分析结果和审计日志。

推荐统一来源对象：

```json
{
  "source_id":"src-001",
  "source_type":"news",
  "title":"标题",
  "url":"https://example.com/item",
  "published_at":"2026-11-01T12:00:00+08:00",
  "author":"来源作者",
  "content":"正文或摘要",
  "content_hash":"sha256...",
  "collected_at":"2026-11-01T12:05:00+08:00"
}
```

真实采集必须遵守目标站点 robots、服务条款、访问频率限制和隐私合规要求；内部数据接入应增加权限控制和脱敏流程。

## 11. 前端扩展建议

当前 `frontend/index.html` 是无构建步骤的单页页面。后续可迁移到 Vue/React，并保留现有 API 契约。优先增加：

- 应用状态自动刷新和 WebSocket/SSE 实时消息；
- 任务进度、任务历史和取消任务；
- 报告列表、报告详情和 Markdown/PDF 导出；
- 图谱可视化库、节点搜索、边过滤和来源跳转；
- 论坛日志分页、日期筛选和关键词高亮；
- 配置表单校验、保存前预览和变更审计。

实时消息建议统一为：

```json
{"type":"app_status","timestamp":"...","payload":{"app_name":"topic_search","status":"running"}}
```

支持的消息类型：`app_status`、`app_output`、`forum_log`、`system_status`、`graph_ready`、`error`。

## 12. 测试建议

当前已完成 8 项自动化测试，覆盖两个独立 OS Worker 的 HTTP 集成、并发领取、租约恢复、重试上限、重复提交幂等、停止后的入队保护、失败记录和论坛历史持久化。运行：

```powershell
python -m unittest discover -s test -v
```

继续开发时建议增加：

- 配置读取、缺失目录自动创建测试；
- 应用启停状态转换测试；
- `/api/search` 请求校验和报告文件落盘测试；
- 报告编号与图谱编号一致性测试；
- 论坛日志最新文件和历史文件测试；
- 图谱节点、边和来源引用查询测试；
- 并发启动/停止和并发检索测试；
- 前端页面的接口失败、空数据和超时场景测试。

手工冒烟流程：启动服务 → 打开控制台 → 启动整体服务 → 主题检索输入主题 → 检查报告目录和图谱目录 → 打开图谱页 → 启停论坛采集 → 查询历史日志 → 修改并保存配置。

## 13. 当前限制与生产化清单

当前版本的模拟采集数据固定写在 `backend/analysis.py` 中，分析 Worker 仍是演示实现；Coordinator 使用 SQLite 和本地文件，Worker 内部接口使用共享 Bearer Token，尚未接入 mTLS、OAuth、真实多媒体采集和模型服务。

生产化建议按以下顺序推进：

1. 将 SQLite 换为 PostgreSQL，并把文件换为 S3/MinIO；生产环境禁止多 Coordinator 共享 SQLite。
2. 将 Bearer Token 换为 mTLS/短期服务身份，限制 `/internal/*` 网络访问。
3. 用 Redis Streams、RabbitMQ 或 Kafka 替换 SQLite 任务队列，并保留幂等键。
4. 增加用户登录、角色权限、租户隔离、限流和审计。
5. 接入真实数据源和模型服务，并记录模型版本、提示词版本和证据引用。
6. 增加 Docker、健康检查、结构化日志、指标监控、备份和滚动升级策略。
7. 增加安全响应头、输入校验、URL 白名单和 SSRF 防护。

## 14. 开发约定

- 时间统一保存为带时区的 ISO 8601 字符串。
- 报告编号、图谱目录和来源文件使用同一个 `report_id`。
- 所有分析结论必须能关联来源 ID、论坛事件或图谱节点。
- 文件写入前创建父目录，JSON 使用 UTF-8 和 `ensure_ascii=false`。
- 生产实现中避免把长时间任务直接放在 HTTP 请求线程中。

# 舆情分析平台

这是一个分布式舆情分析平台。当前环境依赖已记录在 `requirements.txt`，覆盖 FastAPI/Uvicorn、LangGraph/LangChain、OpenAI、Milvus、MinIO、MongoDB、向量模型和数据处理组件。后端拆分为 Coordinator（控制节点）和可水平扩展的 HTTP Worker。

依赖安装：

```powershell
python -m pip install -r requirements.txt
```

真实信息采集目前直接使用已安装的 `requests`、标准库 XML/CSV/JSON 解析；不需要新增依赖。图片使用 Wikimedia API，视频需要配置 YouTube Data API key；内部数据可放入 `data/internal/`。后续如果需要网页渲染、OCR 或音视频转写，再单独安装浏览器、OCR 或 FFmpeg 工具。

代码按职责拆分为 `input/`（三类信息输入）、`processor/`（六类分析处理）、`web/`（接口）、`frontend/`（页面）、`config/`（配置）、`tool/`（Worker）、`utils/`（通用能力）和 `test/`（测试）。

## 启动

```powershell
python app.py
```

这会在本机启动 1 个 Coordinator 和 2 个 Worker。打开 http://127.0.0.1:9000 。首次启动会自动生成 `config/config/config.json`、`runtime/`、`outputs/`、`reports/` 和 `graphs/` 目录。

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

主题检索现在默认异步执行：`POST /api/search` 返回 `task_id`，前端轮询 `GET /api/tasks/{task_id}`；传入 `async:false` 时会等待最多 10 秒以兼容旧调用方。

## 接口

已实现：`GET /api/status`、`GET /api/start/{app_name}`、`GET /api/stop/{app_name}`、`GET /api/output/{app_name}`、`GET /api/test_log/{app_name}`、`POST /api/search`、`GET /api/tasks/{task_id}`、`GET /api/workers`、`GET /api/events`、`GET /api/forum/start`、`GET /api/forum/stop`、`GET /api/forum/log`、`POST /api/forum/log/history`、`GET/POST /api/config`、`GET /api/system/status`、`POST /api/system/start`、`POST /api/system/shutdown`、`GET /api/graph/latest`、`GET /api/graph/{report_id}`、`POST /api/graph/query`、`GET /graph-viewer`。

Worker 内部接口位于 `/internal/*`，必须使用 `Authorization: Bearer $OPINION_WORKER_TOKEN`，不应暴露给公网。

报告包含事件概述、时间线、传播渠道、主要观点、情绪倾向、风险判断、重点证据和结论摘要；图谱节点、关系均带来源引用。

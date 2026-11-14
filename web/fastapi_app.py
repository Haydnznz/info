"""FastAPI control plane for the coordinator.

Run ``uvicorn web.fastapi_app:app --host 0.0.0.0 --port 9000``.
"""
import asyncio
import hmac
import json
import logging
import os
import time
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse, RedirectResponse, Response
from .services import Service
from .schemas import ConfigRequest, ForgotPasswordRequest, GraphQueryRequest, HistoryRequest, LoginRequest, ReportDeleteRequest, ReportUpdateRequest, SearchRequest
from .auth import audit_event, authenticate, auth_enabled, current_user, issue_token, require_roles, reset_password, verify_token
from utils.settings import APPS, ROOT
from utils.observability import METRICS, emit_event, new_trace_id, reset_trace_id, set_trace_id, get_trace_id, normalize_path


class _QuietWorkerPolling(logging.Filter):
    """Keep Uvicorn logs useful by filtering only high-frequency idle polling."""
    def filter(self, record):
        message = record.getMessage()
        return '/internal/claim' not in message and '/internal/heartbeat' not in message


logging.getLogger('uvicorn.access').addFilter(_QuietWorkerPolling())

service = Service()
app = FastAPI(title='舆情分析平台 API', version='0.3.0')
app.add_middleware(CORSMiddleware, allow_origins=os.getenv('CORS_ORIGINS', 'http://127.0.0.1:9000').split(','), allow_credentials=True, allow_methods=['*'], allow_headers=['*'])


@app.middleware('http')
async def user_authentication(request: Request, call_next):
    """Protect public API routes while leaving health, login and Worker APIs separate."""
    path = request.url.path
    trace_header = request.headers.get('X-Trace-Id', '')[:128]
    trace_token = set_trace_id(trace_header or None)
    request.state.trace_id = get_trace_id()
    started = time.perf_counter()
    public = {'/api/health', '/api/auth/login', '/api/auth/forgot-password'}
    if auth_enabled() and path.startswith('/api/') and path not in public and not path.startswith('/internal/'):
        authorization = request.headers.get('Authorization', '')
        scheme, _, token = authorization.partition(' ')
        if scheme.lower() != 'bearer' or not token:
            response = JSONResponse(status_code=401, content={'ok': False, 'error': {'code': 'unauthorized', 'message': '需要登录令牌'},
                                                              'login_url': '/api/auth/login'})
            response.headers['WWW-Authenticate'] = 'Bearer'
            response.headers['X-Trace-Id'] = request.state.trace_id
            METRICS.inc('opinion_http_requests_total', {'route': normalize_path(path), 'method': request.method, 'status': '401'})
            return response
        try:
            request.state.user = verify_token(token)
        except HTTPException as exc:
            response = JSONResponse(status_code=exc.status_code, content={'ok': False, 'error': {'code': 'unauthorized', 'message': exc.detail}})
            response.headers['WWW-Authenticate'] = 'Bearer'
            response.headers['X-Trace-Id'] = request.state.trace_id
            METRICS.inc('opinion_http_requests_total', {'route': normalize_path(path), 'method': request.method, 'status': str(exc.status_code)})
            return response
    if auth_enabled() and (path in ('/', '/index.html', '/graph-viewer', '/graph-viewer/') or path.startswith(('/graph-viewer/', '/report-viewer/', '/report-file/'))):
        token = request.cookies.get('info_auth_token') or request.headers.get('Authorization', '').removeprefix('Bearer ').strip()
        try:
            if not token:
                raise ValueError
            verify_token(token)
        except (ValueError, HTTPException):
            return RedirectResponse('/login?redirect=' + request.url.path, status_code=307)
    try:
        response = await call_next(request)
        response.headers['X-Trace-Id'] = request.state.trace_id
        METRICS.inc('opinion_http_requests_total', {'route': normalize_path(path), 'method': request.method, 'status': str(response.status_code)})
        METRICS.observe('opinion_http_request_seconds', time.perf_counter() - started, {'route': normalize_path(path), 'method': request.method, 'status': str(response.status_code)})
        emit_event('http_request', method=request.method, path=normalize_path(path), status=response.status_code,
                   duration_ms=round((time.perf_counter() - started) * 1000, 3))
        return response
    except Exception as exc:
        METRICS.inc('opinion_http_requests_total', {'route': normalize_path(path), 'method': request.method, 'status': '500'})
        emit_event('http_request_error', method=request.method, path=normalize_path(path), status=500, error=str(exc)[:500])
        raise
    finally:
        reset_trace_id(trace_token)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content={'ok': False, 'error': {'code': 'invalid_request', 'message': str(exc)}})


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={'ok': False, 'error': {'code': 'invalid_request', 'message': str(exc)}})


@app.exception_handler(KeyError)
async def key_error_handler(request: Request, exc: KeyError):
    return JSONResponse(status_code=404, content={'ok': False, 'error': {'code': 'not_found', 'message': str(exc).strip(chr(39))}})


def worker_auth(authorization: str = Header(default='')):
    secret = os.getenv('OPINION_WORKER_TOKEN', '')
    if not secret or not hmac.compare_digest(authorization, 'Bearer ' + secret):
        raise HTTPException(401, 'worker token required')


@app.post('/api/auth/login')
def login(request: LoginRequest):
    user = authenticate(request.username, request.password)
    if not user:
        raise HTTPException(status_code=401, detail='用户名或密码错误')
    token, expires_at = issue_token(user)
    audit_event(user, 'login')
    response = JSONResponse({'ok': True, 'access_token': token, 'token_type': 'bearer', 'expires_at': expires_at, 'user': user})
    response.set_cookie('info_auth_token', token, max_age=max(300, expires_at - int(time.time())), httponly=True, samesite='lax')
    return response


@app.post('/api/auth/logout')
def logout():
    response = JSONResponse({'ok': True, 'message': '已退出登录'})
    response.delete_cookie('info_auth_token')
    return response


@app.post('/api/auth/forgot-password')
def forgot_password(request: ForgotPasswordRequest):
    user = reset_password(request.username, request.reset_token, request.new_password)
    if not user:
        raise HTTPException(status_code=400, detail='用户名、重置令牌或新密码不正确')
    audit_event(user, 'password_reset')
    return {'ok': True, 'message': '密码已重置，请使用新密码登录'}


@app.get('/api/auth/me')
def me(user: dict = Depends(current_user)):
    return {'user': user, 'auth_enabled': auth_enabled()}


@app.get('/api/health')
def health():
    return {'ok': True, 'role': 'coordinator', 'trace_id': get_trace_id(), 'workers': service.store.workers()}


@app.get('/metrics', include_in_schema=False)
def metrics():
    """Prometheus scrape endpoint; keep it unauthenticated for a local scraper."""
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(METRICS.prometheus(), media_type='text/plain; version=0.0.4')


@app.get('/api/observability')
def observability(user: dict = Depends(require_roles('admin'))):
    return {'trace_id': get_trace_id(), 'metrics': METRICS.snapshot(), 'queue': service.store.task_counts(),
            'workers': service.store.workers()}


@app.get('/api/workers')
def workers(user: dict = Depends(require_roles('operator', 'report_viewer', 'admin'))):
    return {'workers': service.store.workers()}


@app.get('/api/status')
@app.get('/api/system/status')
def status(user: dict = Depends(current_user)): return service.status()


@app.get('/api/start/{app_name}')
@app.post('/api/start/{app_name}')
def start_app(app_name: str, user: dict = Depends(require_roles('operator', 'admin'))):
    result = service.application(app_name, True)
    audit_event(user, 'start_app', app_name)
    return result


@app.get('/api/stop/{app_name}')
@app.post('/api/stop/{app_name}')
def stop_app(app_name: str, user: dict = Depends(require_roles('operator', 'admin'))):
    result = service.application(app_name, False)
    audit_event(user, 'stop_app', app_name)
    return result


@app.get('/api/output/{app_name}')
def output(app_name: str, user: dict = Depends(require_roles('operator', 'report_viewer', 'admin'))):
    if app_name not in APPS: raise HTTPException(404, '未知应用')
    return {'app_name': app_name, 'output_text': service.store.get('app:' + app_name)['output']}


@app.get('/api/test_log/{app_name}')
def test_log(app_name: str, user: dict = Depends(require_roles('operator', 'report_viewer', 'admin'))):
    if app_name not in APPS: raise HTTPException(404, '未知应用')
    path = ROOT / 'runtime/apps' / (app_name + '.log')
    return {'app_name': app_name, 'log': path.read_text(encoding='utf-8') if path.exists() else ''}


@app.post('/api/search')
def search(request: SearchRequest, user: dict = Depends(require_roles('operator', 'admin'))):
    audit_event(user, 'search', request.query)
    task = service.submit(request.model_dump(by_alias=True))
    if request.async_:
        return JSONResponse(status_code=202, content=task)
    # Synchronous compatibility: wait up to 10s for a worker to finish.
    until = time.monotonic() + 10
    while time.monotonic() < until:
        state = service.store.task(task['task_id'])
        if state['status'] == 'succeeded':
            return dict(state['result'], task_id=task['task_id'])
        if state['status'] == 'failed':
            raise HTTPException(500, state.get('error') or '分析失败')
        time.sleep(0.1)
    return JSONResponse(status_code=202, content=task)


@app.get('/api/tasks/{task_id}')
def task(task_id: str, user: dict = Depends(require_roles('operator', 'report_viewer', 'admin'))):
    value = service.store.task(task_id)
    if not value: raise HTTPException(404, '任务不存在')
    return value


@app.get('/api/tasks/{task_id}/logs')
def task_logs(task_id: str, limit: int = Query(200, ge=1, le=1000), user: dict = Depends(require_roles('operator', 'report_viewer', 'admin'))):
    return service.task_logs(task_id, limit)


@app.get('/api/tasks')
def tasks(limit: int = Query(100, ge=1, le=500), user: dict = Depends(require_roles('operator', 'report_viewer', 'admin'))):
    return {'tasks': service.tasks(limit)}


@app.post('/api/tasks/{task_id}/cancel')
def cancel_task(task_id: str, user: dict = Depends(require_roles('operator', 'admin'))):
    audit_event(user, 'cancel_task', task_id)
    return service.cancel_task(task_id)


@app.get('/api/events')
def events(after: int = Query(0, ge=0), user: dict = Depends(require_roles('operator', 'report_viewer', 'admin'))): return {'events': service.store.events(after)}


@app.get('/api/tasks/{task_id}/stream')
async def task_stream(task_id: str, user: dict = Depends(require_roles('operator', 'report_viewer', 'admin'))):
    async def stream():
        last = 0
        for _ in range(300):
            state = service.store.task(task_id)
            if state:
                yield 'event: task\ndata: ' + json.dumps(state, ensure_ascii=False) + '\n\n'
                if state['status'] in ('succeeded', 'failed'): break
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type='text/event-stream')


@app.get('/api/forum/start')
@app.post('/api/forum/start')
def forum_start(user: dict = Depends(require_roles('operator', 'admin'))):
    audit_event(user, 'forum_start')
    return service.forum(True)


@app.get('/api/forum/stop')
@app.post('/api/forum/stop')
def forum_stop(user: dict = Depends(require_roles('operator', 'admin'))):
    audit_event(user, 'forum_stop')
    return service.forum(False)


@app.get('/api/forum/log')
def forum_log(user: dict = Depends(require_roles('operator', 'report_viewer', 'admin'))): return service.forum_logs()


@app.post('/api/forum/log/history')
def forum_history(request: HistoryRequest, user: dict = Depends(require_roles('operator', 'report_viewer', 'admin'))): return service.forum_logs(request.date)


@app.get('/api/config')
def get_config(user: dict = Depends(require_roles('admin'))): return service.configuration()


@app.post('/api/config')
def save_config(request: ConfigRequest, user: dict = Depends(require_roles('admin'))):
    audit_event(user, 'save_config')
    return service.save_configuration(request.model_dump())


@app.get('/api/config/history')
def config_history(user: dict = Depends(require_roles('admin'))): return {'history': service.configuration_service.history()}


@app.post('/api/config/default')
def config_default(user: dict = Depends(require_roles('admin'))):
    audit_event(user, 'restore_default_config')
    return service.configuration_service.restore_default()


@app.post('/api/config/rollback/{version}')
def config_rollback(version: str, user: dict = Depends(require_roles('admin'))):
    audit_event(user, 'rollback_config', version)
    return service.configuration_service.rollback(version)


@app.post('/api/system/start')
def system_start(user: dict = Depends(require_roles('admin'))):
    audit_event(user, 'system_start')
    return service.system(True)


@app.post('/api/system/shutdown')
def system_shutdown(user: dict = Depends(require_roles('admin'))):
    audit_event(user, 'system_shutdown')
    return service.system(False)


@app.get('/api/graph/latest')
def graph_latest(user: dict = Depends(require_roles('operator', 'report_viewer', 'admin'))): return service.graph('latest')


@app.get('/api/graph/{report_id}')
def graph(report_id: str, user: dict = Depends(require_roles('operator', 'report_viewer', 'admin'))): return service.graph(report_id)


@app.get('/api/reports')
def reports(user: dict = Depends(require_roles('operator', 'report_viewer', 'admin'))): return {'reports': service.reports()}


@app.get('/api/report/{report_id}')
def report(report_id: str, user: dict = Depends(require_roles('operator', 'report_viewer', 'admin'))): return service.report(report_id)


@app.post('/api/report/{report_id}/update')
def update_report(report_id: str, request: ReportUpdateRequest, user: dict = Depends(require_roles('operator', 'admin'))):
    audit_event(user, 'update_report', report_id)
    return service.update_report(report_id, request.model_dump(exclude_unset=True))


@app.post('/api/reports/delete')
def delete_reports(request: ReportDeleteRequest, user: dict = Depends(require_roles('admin'))):
    audit_event(user, 'delete_reports', ','.join(request.report_ids))
    return service.delete_reports(request.report_ids)


@app.post('/api/graph/query')
def graph_query(request: GraphQueryRequest, user: dict = Depends(require_roles('operator', 'report_viewer', 'admin'))):
    graph_data = service.graph(request.report_id)
    q = request.query.casefold()
    nodes = [n for n in graph_data.get('nodes', []) if not q or q in n.get('label', '').casefold() or q == n.get('id', '').casefold()]
    ids = {n['id'] for n in nodes}
    edges = [e for e in graph_data.get('edges', []) if (not q or e['source'] in ids or e['target'] in ids) and (not request.relation_type or e.get('type') == request.relation_type)]
    return {'nodes': nodes, 'edges': edges}


@app.post('/internal/heartbeat', dependencies=[Depends(worker_auth)])
def heartbeat(payload: dict): return {'ok': service.store.heartbeat(payload.get('worker_id'), payload.get('info', {}), payload.get('task_id'), payload.get('token'))}


@app.post('/internal/claim', dependencies=[Depends(worker_auth)])
def claim(payload: dict):
    service.store.heartbeat(payload.get('worker_id'), payload.get('info', {}))
    return {'task': service.store.claim(payload.get('worker_id'))}


@app.post('/internal/complete', dependencies=[Depends(worker_auth)])
def complete(payload: dict): return service.complete(payload)


@app.get('/')
@app.get('/index.html')
@app.get('/graph-viewer')
@app.get('/graph-viewer/{report_id}')
@app.get('/report-viewer/{report_id}')
def frontend(report_id: str = ''): return FileResponse(ROOT / 'frontend/index.html', headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/report-file/{report_id}')
def report_file(report_id: str):
    if not report_id or '/' in report_id or '\\' in report_id or not report_id.replace('-', '').replace('_', '').isalnum():
        raise HTTPException(400, '非法报告编号')
    path = service.root / 'reports' / report_id / 'report.md'
    if not path.exists():
        raise HTTPException(404, '报告文件不存在')
    # 以 inline 文本响应打开实际 report.md，避免 FileResponse 的 filename
    # 触发 attachment 下载，用户点击后可直接在新页面阅读报告内容。
    return Response(
        content=path.read_text(encoding='utf-8', errors='replace'),
        media_type='text/plain',
        headers={
            'Content-Disposition': f'inline; filename="{report_id}.md"',
            'Cache-Control': 'no-store',
            'X-Content-Type-Options': 'nosniff',
        },
    )


@app.get('/login')
@app.get('/login.html')
def login_page():
    return FileResponse(ROOT / 'frontend/login.html', headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/app.js')
def frontend_script():
    return FileResponse(ROOT / 'frontend/app.js', media_type='application/javascript', headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/styles.css')
def frontend_styles():
    return FileResponse(ROOT / 'frontend/styles.css', media_type='text/css', headers={'Cache-Control': 'no-store, max-age=0'})


def serve(host='127.0.0.1', port=9000):
    import uvicorn
    uvicorn.run(app, host=host, port=port, access_log=False)

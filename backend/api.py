"""HTTP control node and authenticated worker protocol."""
import hmac
import json
import mimetypes
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from .services import Service
from .settings import APPS, ROOT

class Handler(BaseHTTPRequestHandler):
    def reply(self, value, status=200, content_type='application/json; charset=utf-8'):
        raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def handle_request(self):
        try:
            self.dispatch()
        except (ValueError, TypeError, AttributeError) as e:
            self.reply({'ok': False, 'error': {'code': 'invalid_request', 'message': str(e)}}, 400)
        except KeyError as e:
            self.reply({'ok': False, 'error': {'code': 'not_found', 'message': str(e)}}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            import traceback
            traceback.print_exc()
            self.reply({'ok': False, 'error': {'code': 'internal_error', 'message': '服务内部错误，请查看控制节点日志'}}, 500)

    def dispatch(self):
        s = self.server.service
        p = urlparse(self.path)
        path, method = p.path, self.command
        body = {}
        if method == 'POST':
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 <= size <= 8 * 1024 * 1024:
                raise ValueError('请求体不得超过 8MB')
            body = json.loads(self.rfile.read(size) or b'{}')
            if not isinstance(body, dict):
                raise ValueError('请求体必须是 JSON 对象')
        if path.startswith('/internal/'):
            secret = os.environ.get('OPINION_WORKER_TOKEN', '')
            if not secret or not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + secret):
                return self.reply({'error': 'worker token required'}, 401)
            if method != 'POST':
                return self.reply({'error': 'POST required'}, 405)
            worker = body.get('worker_id')
            if not isinstance(worker, str) or not 1 <= len(worker) <= 100:
                raise ValueError('worker_id 无效')
            if path == '/internal/heartbeat':
                return self.reply({'ok': s.store.heartbeat(worker, body.get('info', {}), body.get('task_id'), body.get('token'))})
            if path == '/internal/claim':
                s.store.heartbeat(worker, body.get('info', {}))
                return self.reply({'task': s.store.claim(worker)})
            if path == '/internal/complete':
                return self.reply(s.complete(body))
            raise KeyError('内部接口不存在')
        if method == 'GET':
            if path == '/api/health':
                return self.reply({'ok': True, 'role': 'coordinator', 'workers': s.store.workers()})
            if path in ('/api/status', '/api/system/status'):
                return self.reply(s.status())
            if path == '/api/workers':
                return self.reply({'workers': s.store.workers()})
            if path.startswith('/api/tasks/'):
                task = s.store.task(path.split('/')[-1])
                if not task:
                    raise KeyError('任务不存在')
                return self.reply(task)
            if path == '/api/events':
                after = int(parse_qs(p.query).get('after', ['0'])[0])
                return self.reply({'events': s.store.events(after)})
            for prefix, start in (('/api/start/', True), ('/api/stop/', False)):
                if path.startswith(prefix):
                    return self.reply(s.application(path.split('/')[-1], start))
            for prefix in ('/api/output/', '/api/test_log/'):
                if path.startswith(prefix):
                    a = path.split('/')[-1]
                    if a not in APPS:
                        raise KeyError('未知应用')
                    if prefix == '/api/output/':
                        return self.reply({'app_name': a, 'output_text': s.store.get('app:' + a)['output']})
                    fp = s.root / 'runtime/apps' / (a + '.log')
                    return self.reply({'app_name': a, 'log': fp.read_text(encoding='utf-8') if fp.exists() else ''})
            if path in ('/api/forum/start', '/api/forum/stop'):
                return self.reply(s.forum(path.endswith('/start')))
            if path == '/api/forum/log':
                return self.reply(s.forum_logs())
            if path == '/api/config':
                return self.reply(s.configuration())
            if path.startswith('/api/graph/'):
                return self.reply(s.graph(path.split('/')[-1]))
            if path == '/' or path == '/graph-viewer' or path.startswith('/graph-viewer/'):
                path = '/index.html'
            fp = (ROOT / 'static' / path.lstrip('/')).resolve()
            if ROOT / 'static' not in fp.parents or not fp.is_file():
                raise KeyError('页面不存在')
            return self.reply(fp.read_bytes(), content_type=(mimetypes.guess_type(fp)[0] or 'application/octet-stream') + '; charset=utf-8')
        if method == 'POST':
            if path == '/api/search':
                task = s.submit(body)
                if not body.get('async', False):
                    until = time.monotonic() + 10
                    while time.monotonic() < until:
                        state = s.store.task(task['task_id'])
                        if state['status'] == 'succeeded':
                            return self.reply(dict(state['result'], task_id=task['task_id']))
                        if state['status'] == 'failed':
                            return self.reply(state, 500)
                        time.sleep(.1)
                return self.reply(task, 202)
            if path == '/api/system/start':
                return self.reply(s.system(True))
            if path == '/api/system/shutdown':
                return self.reply(s.system(False))
            if path == '/api/config':
                return self.reply(s.save_configuration(body))
            if path == '/api/forum/log/history':
                return self.reply(s.forum_logs(body.get('date')))
            if path == '/api/graph/query':
                g = s.graph(body.get('report_id', 'latest'))
                q = str(body.get('query', '')).casefold()
                relation = body.get('relation_type')
                nodes = [n for n in g.get('nodes', []) if q in n['label'].casefold() or q == n['id'].casefold()]
                ids = {n['id'] for n in nodes}
                edges = [e for e in g.get('edges', []) if (not q or e['source'] in ids or e['target'] in ids) and (not relation or e['type'] == relation)]
                return self.reply({'nodes': nodes, 'edges': edges})
        raise KeyError('接口不存在')

def serve(host='127.0.0.1', port=9000):
    server = ThreadingHTTPServer((host, port), Handler)
    server.service = Service()
    print(f'Coordinator: http://{host}:{port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

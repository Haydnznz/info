"""Coordinator domain services. All durable changes happen here."""
import json
import threading
import time
from .settings import APPS, SOURCE_TYPES, DATA, config, now, read_json, write_json
from .storage import Store
from .configuration import Configuration
from .forum import ForumService
from .graph import GraphService

class Service:
    def __init__(self, root=DATA):
        self.root = root
        self.store = Store(root)
        self.lock = threading.RLock()
        self.configuration_service = Configuration(root, self.lock)
        self.forum_service = ForumService(root, self.store, self.lock)
        self.graph_service = GraphService(root, self.store)
        if not (root / 'config.json').exists():
            write_json(root / 'config.json', config())

    def configuration(self):
        return self.configuration_service.configuration()

    def save_configuration(self, value):
        return self.configuration_service.save_configuration(value)

    def status(self):
        apps = {a: self.store.get('app:' + a) for a in APPS}
        return {'system_status': self.store.get('system'), 'apps': apps,
                'forum': self.store.get('forum'), 'workers': self.store.workers(),
                'running_apps': [a for a in APPS if apps[a]['status'] == 'running'],
                'recent_errors': [v['error'] for v in apps.values() if v['error']]}

    def log(self, app, text, error=False):
        path = self.root / 'runtime' / 'apps' / (app + '.log')
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as f:
            f.write(f'[{now()}] [{"stderr" if error else "stdout"}] {text}\n')

    def application(self, app, start):
        if app not in APPS:
            raise KeyError('未知应用')
        with self.lock:
            if start and self.store.get('system') != 'online':
                raise ValueError('系统尚未 online')
            if start and not self.configuration()['apps'][app]['enabled']:
                raise ValueError('该应用已在配置中禁用')
            value = self.store.get('app:' + app)
            value['status'] = 'running' if start else 'stopped'
            if start:
                value['started_at'] = now()
            # Serialized with enqueue through SQLite transaction.
            self.store.put('app:' + app, value)
            self.log(app, '已允许接收任务' if start else '已停止接收新任务；已接收任务继续完成')
            self.store.event('app_status', {'app_name': app, 'status': value['status']})
        return {'ok': True, 'app_name': app, 'status': value['status']}

    def system(self, start):
        with self.lock:
            self.store.put('system', 'starting' if start else 'shutting_down')
            self.store.event('system_status', {'system_status': self.store.get('system'), 'running_apps': []})
            if start:
                self.store.put('system', 'online')
            cfg = self.configuration()
            for a in APPS:
                self.application(a, start and cfg['apps'][a]['enabled'])
            if not start:
                self.forum(False)
                self.store.put('system', 'offline')
            s = self.status()
            self.store.event('system_status', {'system_status': s['system_status'], 'running_apps': s['running_apps']})
            return s

    def submit(self, body):
        query = body.get('query')
        types = body.get('source_types', list(SOURCE_TYPES))
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ValueError('query 不能为空，且长度不得超过 500')
        if not isinstance(types, list) or not types or any(t not in SOURCE_TYPES for t in types):
            raise ValueError('source_types 必须是非空的合法来源类型数组')
        if not self.configuration()['apps']['topic_search']['enabled']:
            raise ValueError('topic_search 配置已禁用')
        tid = self.store.enqueue({'query': query.strip(), 'source_types': list(dict.fromkeys(types))})
        self.log('topic_search', '已入队 ' + tid)
        return {'task_id': tid, 'status': 'queued', 'status_url': '/api/tasks/' + tid}

    def complete(self, body):
        tid, token, worker = body['task_id'], body['token'], body['worker_id']
        with self.lock, self.store.transaction() as db:
            row = db.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
            if row and row['status'] == 'succeeded' and row['token'] == token and row['worker_id'] == worker:
                return {'ok': True, 'duplicate': True}
            if not row or row['status'] != 'running' or row['token'] != token or row['worker_id'] != worker or row['lease_until'] < time.time():
                raise ValueError('租约已失效，不能提交结果')
            error = body.get('error')
            if error:
                error = str(error)[:8000]
                db.execute("UPDATE tasks SET status='failed',error=?,updated_at=? WHERE id=?", (error, now(), tid))
                app = json.loads(db.execute("SELECT value FROM kv WHERE key='app:topic_search'").fetchone()[0])
                app['error'] = error
                db.execute("UPDATE kv SET value=? WHERE key='app:topic_search'", (json.dumps(app),))
                self.log('topic_search', error, True)
                event, payload = 'error', {'module_name': 'analysis', 'error_message': error}
            else:
                artifacts = body['artifacts']
                rid = 'R' + tid[1:]
                result = artifacts['result']
                if result.get('report_id') != rid or artifacts['graph'].get('report_id') != rid:
                    raise ValueError('report_id 不匹配')
                report_dir = self.root / 'reports' / rid
                report_dir.mkdir(parents=True, exist_ok=True)
                (report_dir / 'report.md').write_text(artifacts['report'], encoding='utf-8')
                write_json(report_dir / 'sources.json', {k: result[k] for k in ('query', 'executed_at', 'sources')})
                write_json(self.root / 'graphs' / rid / 'graph.json', artifacts['graph'])
                output = f"主题：{result['query']}\n报告编号：{rid}\n来源：{len(result['sources'])} 条\n{result['summary']}"
                for a in APPS:
                    value = json.loads(db.execute('SELECT value FROM kv WHERE key=?', ('app:' + a,)).fetchone()[0])
                    value['output'] = output
                    db.execute('UPDATE kv SET value=? WHERE key=?', (json.dumps(value, ensure_ascii=False), 'app:' + a))
                    path = self.root / 'outputs' / a / 'latest.txt'
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(output, encoding='utf-8')
                    db.execute('INSERT INTO events(type,payload,timestamp) VALUES (?,?,?)',
                               ('app_output', json.dumps({'app_name': a, 'output_text': output}, ensure_ascii=False), now()))
                db.execute("UPDATE tasks SET status='succeeded',result=?,updated_at=? WHERE id=?", (json.dumps(result, ensure_ascii=False), now(), tid))
                self.log('topic_search', '任务完成 ' + tid)
                event, payload = 'graph_ready', {'report_id': rid, 'graph_path': '/api/graph/' + rid}
            db.execute('INSERT INTO events(type,payload,timestamp) VALUES (?,?,?)', (event, json.dumps(payload, ensure_ascii=False), now()))
        return {'ok': True}

    def forum(self, start):
        return self.forum_service.forum(start)

    def forum_logs(self, date=None):
        return self.forum_service.forum_logs(date)

    def graph(self, rid='latest'):
        return self.graph_service.graph(rid)

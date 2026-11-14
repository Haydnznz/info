import concurrent.futures
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from web.services import Service
from utils.settings import ROOT


def _free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _fake_artifacts(report_id):
    """Deterministic artifacts so queue tests never touch network/DB/ML."""
    result = {'report_id': report_id, 'query': '人工智能', 'executed_at': '2026-01-01T00:00:00+08:00',
              'sources': [], 'summary': '测试摘要', 'sentiment': {'positive': 0, 'neutral': 100, 'negative': 0}, 'timeline': []}
    report = f'# 人工智能 舆情分析报告\n\n- 报告编号：{report_id}\n'
    graph = {'report_id': report_id, 'nodes': [], 'edges': [], 'created_at': '2026-01-01T00:00:00+08:00'}
    return {'result': result, 'report': report, 'graph': graph}


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.service = Service(self.root)
        self.service.system(True)

    def tearDown(self):
        self.tmp.cleanup()

    def submit(self):
        return self.service.submit({'query': '人工智能', 'source_types': ['news']})['task_id']

    def completion(self, task, worker):
        return {'worker_id': worker, 'task_id': task['task_id'], 'token': task['token'],
                'artifacts': _fake_artifacts(task['report_id'])}

    def test_concurrent_claims_are_unique(self):
        ids = {self.submit() for _ in range(16)}
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            tasks = list(pool.map(self.service.store.claim, ['w' + str(i) for i in range(24)]))
        self.assertEqual(ids, {t['task_id'] for t in tasks if t})
        self.assertEqual(16, sum(t is not None for t in tasks))

    def test_lease_recovery_and_stale_submission(self):
        tid = self.submit()
        old = self.service.store.claim('old')
        with self.service.store.transaction() as db:
            db.execute('UPDATE tasks SET lease_until=0 WHERE id=?', (tid,))
        new = self.service.store.claim('new')
        self.assertEqual(tid, new['task_id'])
        self.assertNotEqual(old['token'], new['token'])
        with self.assertRaises(ValueError):
            self.service.complete(self.completion(old, 'old'))
        self.service.complete(self.completion(new, 'new'))
        self.assertEqual('succeeded', self.service.store.task(tid)['status'])

    def test_retry_limit(self):
        tid = self.submit()
        for i in range(3):
            self.assertIsNotNone(self.service.store.claim('worker'))
            with self.service.store.transaction() as db:
                db.execute('UPDATE tasks SET lease_until=0 WHERE id=?', (tid,))
        self.assertIsNone(self.service.store.claim('worker'))
        self.assertEqual('failed', self.service.store.task(tid)['status'])

    def test_completion_is_idempotent_and_persists(self):
        tid = self.submit()
        task = self.service.store.claim('w')
        payload = self.completion(task, 'w')
        self.service.complete(payload)
        self.assertTrue(self.service.complete(payload)['duplicate'])
        restored = Service(self.root)
        self.assertEqual('succeeded', restored.store.task(tid)['status'])
        self.assertEqual(task['report_id'], restored.graph()['report_id'])
        self.assertTrue((self.root / 'reports' / task['report_id'] / 'report.md').exists())
        self.assertTrue((self.root / 'outputs/topic_search/latest.txt').exists())
        events = restored.store.events()
        self.assertEqual(1, sum(e['type'] == 'graph_ready' for e in events))

    def test_stop_rejects_new_but_drains_accepted(self):
        self.submit()
        self.service.application('topic_search', False)
        with self.assertRaises(ValueError):
            self.submit()
        task = self.service.store.claim('w')
        self.service.complete(self.completion(task, 'w'))
        self.assertEqual('stopped', self.service.status()['apps']['topic_search']['status'])

    def test_failure_records_error(self):
        tid = self.submit()
        t = self.service.store.claim('w')
        self.service.complete({'worker_id': 'w', 'task_id': tid, 'token': t['token'], 'error': 'analysis unavailable'})
        self.assertEqual('failed', self.service.store.task(tid)['status'])
        self.assertIn('analysis unavailable', self.service.status()['recent_errors'])

    def test_forum_history_persists(self):
        self.service.forum(True)
        self.service.forum(False)
        restored = Service(self.root)
        self.assertEqual(2, len(restored.forum_logs()['logs']))


class HTTPClusterTests(unittest.TestCase):
    def test_real_worker_processes_over_http(self):
        with tempfile.TemporaryDirectory() as temp:
            import uvicorn
            from web import fastapi_app
            service = Service(Path(temp))
            fastapi_app.service = service
            port = _free_port()
            server = uvicorn.Server(uvicorn.Config(fastapi_app.app, host='127.0.0.1', port=port, log_level='error'))
            base = f'http://127.0.0.1:{port}'
            secret = 'integration-test-worker-secret'
            old_token = os.environ.get('OPINION_WORKER_TOKEN')
            old_mongo = os.environ.get('MONGO_ENABLED')
            old_llm = os.environ.get('LLM_ENABLED')
            old_auth_enabled = os.environ.get('AUTH_ENABLED')
            old_auth_secret = os.environ.get('AUTH_SECRET')
            old_admin_password = os.environ.get('AUTH_ADMIN_PASSWORD')
            os.environ['OPINION_WORKER_TOKEN'] = secret
            os.environ['MONGO_ENABLED'] = 'false'
            os.environ['LLM_ENABLED'] = 'false'
            os.environ['AUTH_ENABLED'] = 'true'
            os.environ['AUTH_SECRET'] = 'integration-test-auth-secret'
            os.environ['AUTH_ADMIN_PASSWORD'] = 'change-me-admin'
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            processes = []

            def request(path, body=None, token=None):
                headers = {'Content-Type': 'application/json'}
                if token:
                    headers['Authorization'] = 'Bearer ' + token
                raw = json.dumps(body).encode() if body is not None else None
                with urlopen(Request(base + path, raw, headers), timeout=15) as r:
                    return json.load(r)

            try:
                for _ in range(100):
                    try:
                        request('/api/health')
                        break
                    except Exception:
                        time.sleep(0.1)
                with self.assertRaises(HTTPError) as err:
                    request('/internal/claim', {'worker_id': 'bad'})
                self.assertEqual(401, err.exception.code)
                err.exception.close()
                login = request('/api/auth/login', {'username': 'admin', 'password': 'change-me-admin'})
                api_token = login['access_token']
                request('/api/system/start', {}, api_token)
                ids = [request('/api/search', {'query': '跨进程测试', 'source_types': ['internal_data'], 'async': True}, api_token)['task_id'] for _ in range(6)]
                for i in range(2):
                    processes.append(subprocess.Popen([sys.executable, 'app.py', 'worker', '--coordinator', base, '--worker-id', 'http-worker-' + str(i)],
                        cwd=ROOT, env=dict(os.environ), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
                deadline = time.monotonic() + 30
                states = []
                while time.monotonic() < deadline:
                    states = [request('/api/tasks/' + tid, token=api_token) for tid in ids]
                    workers = request('/api/workers', token=api_token)['workers']
                    if all(t['status'] == 'succeeded' for t in states) and len(workers) == 2:
                        break
                    time.sleep(.1)
                self.assertTrue(all(t['status'] == 'succeeded' for t in states))
                self.assertEqual(2, len(workers))
                rid = states[-1]['result']['report_id']
                self.assertEqual(rid, request('/api/graph/' + rid, token=api_token)['report_id'])
                sync = request('/api/search', {'query': '同步兼容', 'source_types': ['internal_data']}, api_token)
                self.assertIn('report_id', sync)
                self.assertIn('events', request('/api/events?after=0', token=api_token))
                with self.assertRaises(HTTPError) as err:
                    request('/api/search', {'query': ''}, api_token)
                self.assertEqual(400, err.exception.code)
                err.exception.close()
            finally:
                for process in processes:
                    process.terminate()
                    process.wait(timeout=5)
                server.should_exit = True
                thread.join(timeout=5)
                if old_token is None:
                    os.environ.pop('OPINION_WORKER_TOKEN', None)
                else:
                    os.environ['OPINION_WORKER_TOKEN'] = old_token
                if old_mongo is None:
                    os.environ.pop('MONGO_ENABLED', None)
                else:
                    os.environ['MONGO_ENABLED'] = old_mongo
                if old_llm is None:
                    os.environ.pop('LLM_ENABLED', None)
                else:
                    os.environ['LLM_ENABLED'] = old_llm
                for key, value in (('AUTH_ENABLED', old_auth_enabled), ('AUTH_SECRET', old_auth_secret),
                                   ('AUTH_ADMIN_PASSWORD', old_admin_password)):
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value


if __name__ == '__main__':
    unittest.main()

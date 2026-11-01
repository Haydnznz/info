import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend.analysis import analyze
from backend.api import Handler
from backend.services import Service
from backend.settings import ROOT


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
                'artifacts': analyze('人工智能', ['news'], task['report_id'])}

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
            service = Service(Path(temp))
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            server.service = service
            base = f'http://127.0.0.1:{server.server_port}'
            secret = 'integration-test-worker-secret'
            old = os.environ.get('OPINION_WORKER_TOKEN')
            os.environ['OPINION_WORKER_TOKEN'] = secret
            thread = threading.Thread(target=server.serve_forever, daemon=True)
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
                with self.assertRaises(HTTPError) as err:
                    request('/internal/claim', {'worker_id': 'bad'})
                self.assertEqual(401, err.exception.code)
                err.exception.close()
                request('/api/system/start', {})
                ids = [request('/api/search', {'query': '跨进程测试', 'source_types': ['news'], 'async': True})['task_id'] for _ in range(6)]
                # Separate OS processes use only the coordinator HTTP protocol.
                for i in range(2):
                    processes.append(subprocess.Popen([sys.executable, 'app.py', 'worker', '--coordinator', base, '--worker-id', 'http-worker-' + str(i)],
                        cwd=ROOT, env=dict(os.environ), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    states = [request('/api/tasks/' + tid) for tid in ids]
                    workers = request('/api/workers')['workers']
                    if all(t['status'] == 'succeeded' for t in states) and len(workers) == 2:
                        break
                    time.sleep(.1)
                self.assertTrue(all(t['status'] == 'succeeded' for t in states))
                self.assertEqual(2, len(workers))
                rid = states[-1]['result']['report_id']
                self.assertEqual(rid, request('/api/graph/' + rid)['report_id'])
                sync = request('/api/search', {'query': '同步兼容'})
                self.assertIn('report_id', sync)
                self.assertIn('events', request('/api/events?after=0'))
                with self.assertRaises(HTTPError) as err:
                    request('/api/search', {'query': ''})
                self.assertEqual(400, err.exception.code)
                err.exception.close()
            finally:
                for process in processes:
                    process.terminate()
                    process.wait(timeout=5)
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
                if old is None:
                    os.environ.pop('OPINION_WORKER_TOKEN', None)
                else:
                    os.environ['OPINION_WORKER_TOKEN'] = old

if __name__ == '__main__':
    unittest.main()

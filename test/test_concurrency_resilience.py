import concurrent.futures
import tempfile
import unittest
from pathlib import Path

from utils.storage import Store
from web.services import Service


class ConcurrencyResilienceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = Service(Path(self.tmp.name))
        self.service.system(True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_concurrent_enqueue_and_claim_are_unique(self):
        def submit(i):
            return self.service.submit({'query': f'并发主题{i}', 'source_types': ['internal_data']})['task_id']

        # 500 个并发提交任务，覆盖高并发写入与 SQLite 锁重试路径。
        with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
            task_ids = list(pool.map(submit, range(500)))
        self.assertEqual(500, len(task_ids))
        self.assertEqual(500, len(set(task_ids)))
        self.assertEqual(500, len(self.service.store.tasks(500)))

        # 使用更多领取者，验证同一任务在竞争领取时只会被一个 Worker 成功领取。
        with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
            claimed = list(pool.map(self.service.store.claim, [f'worker-{i}' for i in range(600)]))
        claimed_ids = [x['task_id'] for x in claimed if x]
        self.assertEqual(500, len(claimed_ids))
        self.assertEqual(500, len(set(claimed_ids)))

    def test_task_logs_capture_lifecycle_and_failure(self):
        task_id = self.service.submit({'query': '日志容错', 'source_types': ['internal_data']})['task_id']
        task = self.service.store.claim('log-worker')
        self.service.complete({'worker_id': 'log-worker', 'task_id': task_id, 'token': task['token'], 'error': '模拟分析失败'})
        logs = self.service.task_logs(task_id)['logs']
        self.assertEqual(['queued', 'claimed', 'failed'], [x['event'] for x in logs])
        self.assertEqual('ERROR', logs[-1]['level'])


if __name__ == '__main__':
    unittest.main()

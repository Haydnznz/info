import unittest
from unittest.mock import patch

from tool.worker import Worker


class WorkerPollingTests(unittest.TestCase):
    def test_idle_polling_uses_backoff(self):
        worker = Worker.__new__(Worker)
        worker.id = 'test-worker'
        worker.url = 'http://coordinator'
        worker.secret = 'test-secret'
        calls = []

        def claim(*args, **kwargs):
            calls.append(1)
            if len(calls) >= 4:
                raise KeyboardInterrupt
            return {'task': None}

        worker.call = claim
        sleeps = []
        with patch('tool.worker.time.sleep', side_effect=lambda value: sleeps.append(value)):
            with self.assertRaises(KeyboardInterrupt):
                worker.run()
        self.assertEqual([1.0, 1.5, 2.25], sleeps)


if __name__ == '__main__':
    unittest.main()

"""Independent HTTP worker: no shared filesystem required."""
import json
import os
import socket
import threading
import time
import uuid
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from processor.pipeline import analyze
from utils.observability import METRICS, emit_event, set_trace_id, reset_trace_id, timer

logger = logging.getLogger('opinion.worker')

class Worker:
    def __init__(self, coordinator, worker_id=None):
        self.url = coordinator.rstrip('/')
        self.id = worker_id or socket.gethostname() + '-' + uuid.uuid4().hex[:8]
        self.secret = os.environ.get('OPINION_WORKER_TOKEN')
        if not self.secret:
            raise ValueError('请设置 OPINION_WORKER_TOKEN，与控制节点一致')

    def call(self, route, payload=None):
        body = {'worker_id': self.id, **(payload or {})}
        request = Request(self.url + '/internal/' + route, json.dumps(body, ensure_ascii=False).encode('utf-8'),
                          {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.secret})
        with urlopen(request, timeout=15) as response:
            return json.load(response)

    def execute(self, task):
        stop = threading.Event()
        lost = threading.Event()
        def heartbeat():
            while not stop.wait(5):
                try:
                    if not self.call('heartbeat', {'task_id': task['task_id'], 'token': task['token']})['ok']:
                        lost.set()
                        return
                except (OSError, URLError):
                    pass
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        trace_token = set_trace_id(task.get('trace_id') or None)
        started = time.perf_counter()
        try:
            logger.info('task_start task_id=%s worker_id=%s attempt=%s', task['task_id'], self.id, task.get('attempts', 'unknown'))
            emit_event('worker_task_started', task_id=task['task_id'], worker_id=self.id)
            payload = {'task_id': task['task_id'], 'token': task['token']}
            try:
                with timer('opinion_worker_analysis_seconds', {'worker_id': self.id}, 'worker_analysis', task_id=task['task_id'], worker_id=self.id):
                    payload['artifacts'] = analyze(task['payload']['query'], task['payload']['source_types'], task['report_id'])
            except Exception as exc:
                payload['error'] = f'{type(exc).__name__}: {exc}'
                METRICS.inc('opinion_worker_task_failures_total', {'worker_id': self.id})
            for attempt in range(3):
                if lost.is_set():
                    break
                try:
                    self.call('complete', payload)
                    METRICS.inc('opinion_worker_completions_total', {'status': 'failed' if payload.get('error') else 'succeeded'})
                    emit_event('worker_task_completed', task_id=task['task_id'], worker_id=self.id,
                               status='failed' if payload.get('error') else 'succeeded',
                               duration_ms=round((time.perf_counter() - started) * 1000, 3))
                    print(f'{self.id}: completed {task["task_id"]}', flush=True)
                    break
                except HTTPError as exc:
                    exc.close()
                    if exc.code < 500:
                        print(f'Completion rejected: HTTP {exc.code}', flush=True)
                        break
                except (OSError, URLError):
                    METRICS.inc('opinion_worker_completion_retries_total', {'worker_id': self.id})
                    logger.warning('completion_retry task_id=%s attempt=%s', task['task_id'], attempt + 1)
                stop.wait(min(8, (2 ** attempt)) + (0.1 * attempt))
        finally:
            stop.set()
            thread.join(timeout=2)
            reset_trace_id(trace_token)

    def run(self):
        print(f'Worker {self.id} -> {self.url}', flush=True)
        idle_wait = 1.0
        while True:
            try:
                task = self.call('claim', {'info': {'host': socket.gethostname(), 'pid': os.getpid()}})['task']
                if task:
                    idle_wait = 1.0
                    self.execute(task)
                else:
                    # Avoid hammering the coordinator while the queue is empty.
                    # A newly submitted task is still picked up quickly because
                    # the first idle interval is one second.
                    time.sleep(idle_wait)
                    idle_wait = min(idle_wait * 1.5, 10.0)
            except (OSError, URLError) as exc:
                logger.error('coordinator_unavailable error=%s', exc)
                time.sleep(min(idle_wait, 10.0))
                idle_wait = min(idle_wait * 1.5, 10.0)

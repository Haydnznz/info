"""Independent HTTP worker: no shared filesystem required."""
import json
import os
import socket
import threading
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from .analysis import analyze

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
        try:
            payload = {'task_id': task['task_id'], 'token': task['token']}
            try:
                payload['artifacts'] = analyze(task['payload']['query'], task['payload']['source_types'], task['report_id'])
            except Exception as exc:
                payload['error'] = f'{type(exc).__name__}: {exc}'
            for attempt in range(3):
                if lost.is_set():
                    break
                try:
                    self.call('complete', payload)
                    print(f'{self.id}: completed {task["task_id"]}', flush=True)
                    break
                except HTTPError as exc:
                    exc.close()
                    if exc.code < 500:
                        print(f'Completion rejected: HTTP {exc.code}', flush=True)
                        break
                except (OSError, URLError):
                    pass
                stop.wait(1 + attempt)
        finally:
            stop.set()
            thread.join(timeout=2)

    def run(self):
        print(f'Worker {self.id} -> {self.url}', flush=True)
        while True:
            try:
                task = self.call('claim', {'info': {'host': socket.gethostname(), 'pid': os.getpid()}})['task']
                if task:
                    self.execute(task)
                else:
                    time.sleep(1)
            except (OSError, URLError) as exc:
                print(f'Coordinator unavailable: {exc}', flush=True)
                time.sleep(2)

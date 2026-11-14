"""Small dependency-free observability primitives for the coordinator and workers.

The registry is intentionally process-local: Prometheus can scrape each API or
worker process, while durable task state remains in SQLite.  Structured events
are appended as JSONL so a deployment can ship the file to a log collector.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path


_trace_id: ContextVar[str] = ContextVar('opinion_trace_id', default='')


def new_trace_id() -> str:
    return 'tr-' + uuid.uuid4().hex


def set_trace_id(value: str | None = None):
    return _trace_id.set(str(value or new_trace_id()))


def reset_trace_id(token) -> None:
    _trace_id.reset(token)


def get_trace_id() -> str:
    return _trace_id.get() or 'tr-untracked'


def _safe_label(value: object) -> str:
    value = str(value if value is not None else 'unknown')
    return value[:80].replace('\n', ' ').replace('\r', ' ')


def normalize_path(path: str) -> str:
    """Avoid high-cardinality task/report IDs in request metrics."""
    path = re.sub(r'/api/tasks/[^/]+', '/api/tasks/{task_id}', path)
    path = re.sub(r'/api/report/[^/]+', '/api/report/{report_id}', path)
    path = re.sub(r'/api/graph/[^/]+', '/api/graph/{report_id}', path)
    path = re.sub(r'/report-(?:viewer|file)/[^/]+', '/report-viewer/{report_id}', path)
    return path[:160]


class MetricsRegistry:
    def __init__(self):
        self._lock = threading.RLock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._histograms: dict[tuple[str, tuple[tuple[str, str], ...]], dict] = {}
        self._started = time.time()

    @staticmethod
    def _key(labels: dict | None):
        return tuple(sorted((str(k), _safe_label(v)) for k, v in (labels or {}).items()))

    def inc(self, name: str, labels: dict | None = None, value: float = 1) -> None:
        with self._lock:
            key = (name, self._key(labels))
            self._counters[key] = self._counters.get(key, 0) + value

    def set_gauge(self, name: str, value: float, labels: dict | None = None) -> None:
        with self._lock:
            self._gauges[(name, self._key(labels))] = value

    def observe(self, name: str, value: float, labels: dict | None = None,
                buckets: tuple[float, ...] = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)) -> None:
        with self._lock:
            key = (name, self._key(labels))
            item = self._histograms.setdefault(key, {'count': 0, 'sum': 0.0, 'buckets': {b: 0 for b in buckets}})
            item['count'] += 1
            item['sum'] += float(value)
            for bucket in item['buckets']:
                if value <= bucket:
                    item['buckets'][bucket] += 1

    def set_queue_gauges(self, queued: int, running: int, failed: int, workers: int) -> None:
        self.set_gauge('opinion_tasks', queued, {'status': 'queued'})
        self.set_gauge('opinion_tasks', running, {'status': 'running'})
        self.set_gauge('opinion_tasks', failed, {'status': 'failed'})
        self.set_gauge('opinion_workers', workers, {'state': 'known'})

    def snapshot(self) -> dict:
        with self._lock:
            return {
                'uptime_seconds': round(time.time() - self._started, 3),
                'counters': {name: [{'labels': dict(labels), 'value': value} for (metric, labels), value in self._counters.items() if metric == name]
                             for name in sorted({metric for metric, _ in self._counters})},
                'gauges': {name: [{'labels': dict(labels), 'value': value} for (metric, labels), value in self._gauges.items() if metric == name]
                           for name in sorted({metric for metric, _ in self._gauges})},
                'histograms': {name: [{'labels': dict(labels), 'count': item['count'], 'sum': item['sum']}
                                      for (metric, labels), item in self._histograms.items() if metric == name]
                               for name in sorted({metric for metric, _ in self._histograms})},
            }

    @staticmethod
    def _labels(labels: tuple[tuple[str, str], ...]) -> str:
        if not labels:
            return ''
        def quote(value):
            return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'
        return '{' + ','.join(f'{key}={quote(value)}' for key, value in labels) + '}'

    def prometheus(self) -> str:
        lines = [f'# TYPE opinion_process_uptime_seconds gauge', f'opinion_process_uptime_seconds {time.time() - self._started:.3f}']
        with self._lock:
            for (name, labels), value in sorted(self._counters.items()):
                lines.extend((f'# TYPE {name} counter', f'{name}{self._labels(labels)} {value}'))
            for (name, labels), value in sorted(self._gauges.items()):
                lines.extend((f'# TYPE {name} gauge', f'{name}{self._labels(labels)} {value}'))
            for (name, labels), item in sorted(self._histograms.items()):
                lines.append(f'# TYPE {name} histogram')
                for bucket, count in item['buckets'].items():
                    bucket_labels = dict(labels)
                    bucket_labels['le'] = str(bucket)
                    lines.append(f'{name}_bucket{self._labels(tuple(sorted(bucket_labels.items())))} {count}')
                bucket_labels = dict(labels)
                bucket_labels['le'] = '+Inf'
                lines.append(f'{name}_bucket{self._labels(tuple(sorted(bucket_labels.items())))} {item["count"]}')
                lines.append(f'{name}_count{self._labels(labels)} {item["count"]}')
                lines.append(f'{name}_sum{self._labels(labels)} {item["sum"]:.6f}')
        return '\n'.join(lines) + '\n'


METRICS = MetricsRegistry()
_event_lock = threading.Lock()


def emit_event(event: str, **fields) -> dict:
    record = {'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'event': event, 'trace_id': get_trace_id(), **fields}
    root = Path(os.getenv('OPINION_DATA_DIR', '.')).resolve()
    path = root / 'runtime' / 'observability.jsonl'
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _event_lock, path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n')
    except OSError:
        # Observability must never break the business path.
        pass
    return record


@contextmanager
def timer(metric: str, labels: dict | None = None, event: str | None = None, **fields):
    started = time.perf_counter()
    status = 'success'
    try:
        yield
    except Exception:
        status = 'error'
        raise
    finally:
        elapsed = time.perf_counter() - started
        all_labels = dict(labels or {})
        all_labels['status'] = status
        METRICS.observe(metric, elapsed, all_labels)
        if event:
            emit_event(event, duration_ms=round(elapsed * 1000, 3), status=status, **fields)

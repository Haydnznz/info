"""Coordinator domain services. All durable changes happen here."""
import json
import re
import shutil
import threading
import time
from utils.observability import METRICS, emit_event, get_trace_id, timer
from utils.settings import APPS, SOURCE_TYPES, DATA, config, now, read_json, write_json
from utils.storage import Store
from config.configuration import Configuration
from web.forum import ForumService
from web.graph import GraphService

class Service:
    def __init__(self, root=DATA):
        self.root = root
        self.store = Store(root)
        self.lock = threading.RLock()
        self.configuration_service = Configuration(root, self.lock)
        self.forum_service = ForumService(root, self.store, self.lock)
        self.graph_service = GraphService(root, self.store)
        if not (root / 'config' / 'config.json').exists():
            write_json(root / 'config' / 'config.json', config())

    def configuration(self):
        return self.configuration_service.configuration()

    def save_configuration(self, value):
        return self.configuration_service.save_configuration(value)

    def status(self):
        apps = {a: self.store.get('app:' + a) for a in APPS}
        workers = self.store.workers()
        counts = self.store.task_counts()
        METRICS.set_queue_gauges(counts['queued'], counts['running'], counts['failed'], len(workers))
        return {'system_status': self.store.get('system'), 'apps': apps,
                'forum': self.store.get('forum'), 'workers': workers,
                'queue': counts,
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
        payload = {'query': query.strip(), 'source_types': list(dict.fromkeys(types)), 'trace_id': get_trace_id()}
        for key in ('start_time', 'end_time', 'confidence', 'sentiment', 'entity_type', 'exclude_words', 'task_name'):
            if body.get(key) not in (None, ''):
                payload[key] = body[key]
        started = time.perf_counter()
        tid = self.store.enqueue(payload)
        elapsed = time.perf_counter() - started
        METRICS.inc('opinion_tasks_submitted_total', {'source': 'api'})
        METRICS.observe('opinion_task_enqueue_seconds', elapsed, {'status': 'success'})
        emit_event('task_queued', task_id=tid, query=query[:120], source_types=payload['source_types'])
        self.log('topic_search', '已入队 ' + tid)
        return {'task_id': tid, 'status': 'queued', 'status_url': '/api/tasks/' + tid}

    def tasks(self, limit=100):
        return self.store.tasks(limit)

    def task_logs(self, task_id, limit=200):
        if not self.store.task(task_id):
            raise KeyError('任务不存在')
        return {'task_id': task_id, 'logs': self.store.task_logs(task_id, limit)}

    def cancel_task(self, task_id):
        status = self.store.cancel(task_id)
        if status is None:
            raise KeyError('任务不存在')
        return {'ok': True, 'task_id': task_id, 'status': status}

    def complete(self, body):
        tid, token, worker = body['task_id'], body['token'], body['worker_id']
        completion_started = time.perf_counter()
        with self.lock, self.store.transaction() as db:
            row = db.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
            if row and row['status'] == 'succeeded' and row['token'] == token and row['worker_id'] == worker:
                METRICS.inc('opinion_task_completions_total', {'status': 'duplicate'})
                return {'ok': True, 'duplicate': True}
            if not row or row['status'] != 'running' or row['token'] != token or row['worker_id'] != worker or row['lease_until'] < time.time():
                raise ValueError('租约已失效，不能提交结果')
            error = body.get('error')
            if error:
                error = str(error)[:8000]
                db.execute("UPDATE tasks SET status='failed',error=?,updated_at=? WHERE id=?", (error, now(), tid))
                db.execute('INSERT INTO task_logs(task_id,level,event,message,timestamp,context) VALUES (?,?,?,?,?,?)',
                           (tid, 'ERROR', 'failed', error, now(), json.dumps({'worker_id': worker}, ensure_ascii=False)))
                app = json.loads(db.execute("SELECT value FROM kv WHERE key='app:topic_search'").fetchone()[0])
                app['error'] = error
                db.execute("UPDATE kv SET value=? WHERE key='app:topic_search'", (json.dumps(app),))
                self.log('topic_search', error, True)
                event, payload = 'error', {'module_name': 'analysis', 'error_message': error}
                METRICS.inc('opinion_task_completions_total', {'status': 'failed'})
                METRICS.inc('opinion_task_failures_total', {'module': 'analysis'})
                emit_event('task_failed', task_id=tid, worker_id=worker, error=error[:500])
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
                write_json(report_dir / 'metadata.json', {
                    'created_at': result.get('executed_at', now()),
                    'status': '待核验',
                    'risk_level': '高' if result.get('risk_points') else '低',
                    'confidence': result.get('evidence_quality', 'low'),
                    'sentiment': _sentiment_label(result.get('sentiment', {})),
                    'summary': result.get('summary', ''),
                    'starred': False,
                })
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
                db.execute('INSERT INTO task_logs(task_id,level,event,message,timestamp,context) VALUES (?,?,?,?,?,?)',
                           (tid, 'INFO', 'succeeded', '任务处理完成', now(), json.dumps({'worker_id': worker, 'report_id': rid}, ensure_ascii=False)))
                self.log('topic_search', '任务完成 ' + tid)
                event, payload = 'graph_ready', {'report_id': rid, 'graph_path': '/api/graph/' + rid}
                METRICS.inc('opinion_task_completions_total', {'status': 'succeeded'})
                emit_event('task_succeeded', task_id=tid, report_id=rid, worker_id=worker)
            db.execute('INSERT INTO events(type,payload,timestamp) VALUES (?,?,?)', (event, json.dumps(payload, ensure_ascii=False), now()))
        METRICS.observe('opinion_task_completion_seconds', time.perf_counter() - completion_started, {'status': 'success'})
        return {'ok': True}

    def forum(self, start):
        return self.forum_service.forum(start)

    def forum_logs(self, date=None):
        return self.forum_service.forum_logs(date)

    def graph(self, rid='latest'):
        return self.graph_service.graph(rid)

    def reports(self):
        """Return report cards for the report management view."""
        reports = []
        report_root = self.root / 'reports'
        if not report_root.exists():
            return reports
        for directory in sorted((p for p in report_root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True):
            report_id = directory.name
            report_file = directory / 'report.md'
            sources_file = directory / 'sources.json'
            if not report_file.exists():
                continue
            text = report_file.read_text(encoding='utf-8', errors='replace')
            source_data = read_json(sources_file, {}) or {}
            metadata = read_json(directory / 'metadata.json', {}) or {}
            sources = source_data.get('sources', [])
            reports.append(self._report_card(report_id, text, source_data, metadata, sources))
        return reports

    def report(self, report_id):
        if not report_id or '/' in report_id or '\\' in report_id:
            raise ValueError('非法报告编号')
        directory = self.root / 'reports' / report_id
        report_file = directory / 'report.md'
        if not report_file.exists():
            raise KeyError('报告不存在')
        markdown = report_file.read_text(encoding='utf-8', errors='replace')
        source_data = read_json(directory / 'sources.json', {}) or {}
        metadata = read_json(directory / 'metadata.json', {}) or {}
        sources = source_data.get('sources', [])
        card = self._report_card(report_id, markdown, source_data, metadata, sources)
        return {'report_id': report_id, 'markdown': markdown, 'sources': source_data,
                'graph': self.graph(report_id), 'metadata': metadata, 'card': card,
                'sections': self._report_sections(markdown, sources)}

    def update_report(self, report_id, value):
        if not report_id or '/' in report_id or '\\' in report_id:
            raise ValueError('非法报告编号')
        directory = self.root / 'reports' / report_id
        report_file = directory / 'report.md'
        if not report_file.exists():
            raise KeyError('报告不存在')
        allowed = {'status', 'risk_level', 'analyst_opinion', 'manual_conclusion', 'starred'}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError('不支持的报告字段：' + ', '.join(sorted(unknown)))
        metadata = read_json(directory / 'metadata.json', {}) or {}
        if value.get('status') is not None:
            if value['status'] not in ('待核验', '研判中', '已采用', '已归档'):
                raise ValueError('报告状态必须是：待核验、研判中、已采用或已归档')
            metadata['status'] = value['status']
        if value.get('risk_level') is not None:
            if value['risk_level'] not in ('高', '中', '低'):
                raise ValueError('风险等级必须是：高、中或低')
            metadata['risk_level'] = value['risk_level']
        for key in ('analyst_opinion', 'manual_conclusion'):
            if key in value and value[key] is not None:
                metadata[key] = str(value[key])[:20000]
        if value.get('starred') is not None:
            metadata['starred'] = bool(value['starred'])
        metadata['updated_at'] = now()
        write_json(directory / 'metadata.json', metadata)
        return self.report(report_id)

    def delete_reports(self, report_ids):
        if not isinstance(report_ids, list) or not report_ids:
            raise ValueError('至少选择一份报告')
        if len(report_ids) > 100:
            raise ValueError('单次最多删除 100 份报告')
        deleted = []
        for report_id in dict.fromkeys(report_ids):
            if not isinstance(report_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', report_id):
                raise ValueError('非法报告编号')
            report_dir = self.root / 'reports' / report_id
            if not report_dir.is_dir() or not (report_dir / 'report.md').exists():
                continue
            shutil.rmtree(report_dir)
            graph_dir = self.root / 'graphs' / report_id
            if graph_dir.is_dir():
                shutil.rmtree(graph_dir)
            deleted.append(report_id)
        return {'ok': True, 'deleted': deleted, 'count': len(deleted)}

    @staticmethod
    def _report_card(report_id, text, source_data, metadata, sources):
        summary = next((line.strip() for line in text.splitlines() if line.startswith('当前') or line.startswith('本报告围绕')), '')
        sentiment = metadata.get('sentiment') or '中性'
        created_at = source_data.get('executed_at') or metadata.get('created_at', '')
        return {'report_id': report_id,
                'query': source_data.get('query', text.splitlines()[0].lstrip('# ') if text else report_id),
                'created_at': created_at,
                'source_count': len(sources), 'risk_level': metadata.get('risk_level', '高' if '高风险' in text else '低'),
                'status': metadata.get('status', '待核验'), 'confidence': metadata.get('confidence') or _markdown_value(text, '分析置信度') or 'low',
                'sentiment': sentiment, 'summary': metadata.get('summary') or summary or '暂无事件摘要',
                'starred': bool(metadata.get('starred', False)), 'path': f'/api/report/{report_id}'}

    @staticmethod
    def _report_sections(markdown, sources):
        sections = {}
        current = None
        lines = []
        for line in markdown.splitlines():
            if line.startswith('## '):
                if current is not None:
                    sections[current] = '\n'.join(lines).strip()
                current, lines = line[3:].strip(), []
            elif current is not None:
                lines.append(line)
        if current is not None:
            sections[current] = '\n'.join(lines).strip()
        sections['证据列表'] = sources
        return sections


def datetime_from_mtime(path):
    from datetime import datetime
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec='seconds')


def _markdown_value(text, label):
    prefix = '- ' + label + '：'
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ''


def _sentiment_label(value):
    if not isinstance(value, dict):
        return '中性'
    key = max(('positive', 'neutral', 'negative'), key=lambda item: value.get(item, 0))
    return {'positive': '正向', 'neutral': '中性', 'negative': '负向'}[key]

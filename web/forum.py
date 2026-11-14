"""Dedicated forum domain module."""
import json
import re
from utils.settings import now, write_json

class ForumService:
    def __init__(self, root, store, lock):
        self.root = root
        self.store = store
        self.lock = lock

    def forum(self, start):
        with self.lock:
            if start and self.store.get('system') != 'online':
                raise ValueError('请先启动系统')
            status = 'running' if start else 'stopped'
            self.store.put('forum', status)
            e = {'time': now(), 'event_type': 'collector', 'message_text': '演示论坛任务已' + ('启动' if start else '停止'), 'task_status': status}
            date = now()[:10]
            with self.store.transaction() as db:
                db.execute('INSERT INTO forum(date,payload) VALUES (?,?)', (date, json.dumps(e, ensure_ascii=False)))
            path = self.root / 'runtime/forum/latest.log'
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('a', encoding='utf-8') as f:
                f.write(json.dumps(e, ensure_ascii=False) + '\n')
            write_json(self.root / 'runtime/forum/history' / (date + '.json'), self.forum_logs(date)['logs'])
            self.store.event('forum_log', e)
            return self.forum_logs()


    def forum_logs(self, date=None):
        if date and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
            raise ValueError('日期格式为 YYYY-MM-DD')
        with self.store.transaction() as db:
            rows = db.execute('SELECT payload FROM forum WHERE date=? ORDER BY id', (date,)).fetchall() if date else db.execute('SELECT payload FROM forum ORDER BY id DESC LIMIT 50').fetchall()[::-1]
        return {'status': self.store.get('forum'), 'date': date, 'logs': [json.loads(r[0]) for r in rows]}



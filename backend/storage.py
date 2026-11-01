"""SQLite transactions serialize queue claims; workers never open this database."""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from .settings import APPS, DATA, LEASE_SECONDS, now

class Store:
    def __init__(self, root=DATA):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / 'runtime' / 'platform.db'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL,
                    created_at TEXT, updated_at TEXT, worker_id TEXT, token TEXT,
                    lease_until REAL DEFAULT 0, attempts INTEGER DEFAULT 0,
                    result TEXT, error TEXT DEFAULT '');
                CREATE TABLE IF NOT EXISTS workers (id TEXT PRIMARY KEY, seen REAL, info TEXT);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, payload TEXT, timestamp TEXT);
                CREATE TABLE IF NOT EXISTS forum (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, payload TEXT);
            ''')
            for k, v in [('system', 'offline'), ('forum', 'idle')] + [
                ('app:' + a, {'status': 'stopped', 'output': '', 'error': '', 'started_at': None}) for a in APPS]:
                db.execute('INSERT OR IGNORE INTO kv VALUES (?,?)', (k, json.dumps(v)))

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, key):
        with self.transaction() as db:
            return json.loads(db.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()[0])

    def put(self, key, value):
        with self.transaction() as db:
            db.execute('INSERT OR REPLACE INTO kv VALUES (?,?)', (key, json.dumps(value, ensure_ascii=False)))

    def event(self, kind, payload):
        with self.transaction() as db:
            db.execute('INSERT INTO events(type,payload,timestamp) VALUES (?,?,?)', (kind, json.dumps(payload, ensure_ascii=False), now()))

    def events(self, after=0):
        with self.transaction() as db:
            return [dict(id=r['id'], type=r['type'], timestamp=r['timestamp'], payload=json.loads(r['payload']))
                    for r in db.execute('SELECT * FROM events WHERE id>? ORDER BY id LIMIT 200', (after,))]

    def enqueue(self, payload):
        tid = 'T' + uuid.uuid4().hex
        with self.transaction() as db:
            # Admission check and insert must be in the same transaction as stop.
            system = json.loads(db.execute("SELECT value FROM kv WHERE key='system'").fetchone()[0])
            app = json.loads(db.execute("SELECT value FROM kv WHERE key='app:topic_search'").fetchone()[0])
            if system != 'online' or app['status'] != 'running':
                raise ValueError('请先启动系统和 topic_search 应用')
            db.execute('INSERT INTO tasks(id,payload,status,created_at,updated_at) VALUES (?,?,?,?,?)',
                       (tid, json.dumps(payload, ensure_ascii=False), 'queued', now(), now()))
        return tid

    def task(self, tid):
        with self.transaction() as db:
            row = db.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
        if not row:
            return None
        r = dict(row)
        r.pop('token', None)
        for k in ('payload', 'result'):
            r[k] = json.loads(r[k]) if r[k] else None
        return r

    def claim(self, worker):
        with self.transaction() as db:
            # At least once delivery: expired work can be reclaimed, bounded to 3 attempts.
            db.execute("UPDATE tasks SET status='failed',error='worker lease expired after 3 attempts' WHERE status='running' AND lease_until<? AND attempts>=3", (time.time(),))
            db.execute("UPDATE tasks SET status='queued',worker_id=NULL,token=NULL WHERE status='running' AND lease_until<? AND attempts<3", (time.time(),))
            row = db.execute("SELECT * FROM tasks WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
            if not row:
                return None
            token = uuid.uuid4().hex
            db.execute("UPDATE tasks SET status='running',worker_id=?,token=?,lease_until=?,attempts=attempts+1,updated_at=? WHERE id=?",
                       (worker, token, time.time() + LEASE_SECONDS, now(), row['id']))
            return {'task_id': row['id'], 'payload': json.loads(row['payload']), 'token': token,
                    'report_id': 'R' + row['id'][1:], 'lease_seconds': LEASE_SECONDS}

    def heartbeat(self, worker, info, task_id=None, token=None):
        with self.transaction() as db:
            db.execute('INSERT OR REPLACE INTO workers VALUES (?,?,?)', (worker, time.time(), json.dumps(info)))
            if task_id:
                cur = db.execute("UPDATE tasks SET lease_until=? WHERE id=? AND token=? AND worker_id=? AND status='running' AND lease_until>?",
                                 (time.time() + LEASE_SECONDS, task_id, token, worker, time.time()))
                return cur.rowcount == 1
        return True

    def workers(self):
        with self.transaction() as db:
            return [dict(id=r['id'], online=time.time()-r['seen'] < LEASE_SECONDS, last_seen=r['seen'], info=json.loads(r['info']))
                    for r in db.execute('SELECT * FROM workers')]

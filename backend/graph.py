"""Dedicated graph domain module."""
import json
import re
from .settings import read_json

class GraphService:
    def __init__(self, root, store):
        self.root = root
        self.store = store

    def graph(self, rid='latest'):
        if rid == 'latest':
            # DB completion order is authoritative, rather than UUID directory sorting.
            with self.store.transaction() as db:
                row = db.execute("SELECT result FROM tasks WHERE status='succeeded' ORDER BY updated_at DESC,rowid DESC LIMIT 1").fetchone()
            if row:
                rid = json.loads(row[0])['report_id']
            else:
                dirs = sorted((self.root / 'graphs').glob('*/graph.json'))
                return read_json(dirs[-1], {}) if dirs else {}
        if not re.fullmatch(r'[A-Za-z0-9_-]+', rid):
            raise ValueError('非法报告编号')
        g = read_json(self.root / 'graphs' / rid / 'graph.json')
        if g is None:
            raise KeyError('报告图谱不存在')
        return g



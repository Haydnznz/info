"""MongoDB persistence for raw input batches and normalized source evidence."""
from datetime import datetime, timezone
import os

try:
    from pymongo import MongoClient
except ImportError:  # Keep the rest of the app importable for offline tests.
    MongoClient = None


class MongoInputStore:
    def __init__(self):
        self.url = os.getenv('MONGO_URL', '')
        self.db_name = os.getenv('MONGO_DB_NAME', 'info001')
        self.sources_name = os.getenv('MONGO_SOURCES_COLLECTION', 'input_sources')
        self.runs_name = os.getenv('MONGO_RUNS_COLLECTION', 'input_runs')
        self._client = None

    @property
    def enabled(self):
        return os.getenv('MONGO_ENABLED', 'true').lower() in ('1', 'true', 'yes', 'on') and bool(self.url)

    def _db(self):
        if not self.enabled:
            return None
        if MongoClient is None:
            raise RuntimeError('未安装 pymongo，无法写入 MongoDB')
        if self._client is None:
            self._client = MongoClient(self.url, serverSelectionTimeoutMS=int(os.getenv('MONGO_TIMEOUT_MS', '3000')))
            self._client.admin.command('ping')
        return self._client[self.db_name]

    def persist(self, query, sources, errors=None):
        """Persist one collection run; returns an error string or None."""
        if not self.enabled:
            return None
        try:
            db = self._db()
            db[self.sources_name].create_index('source_id')
            db[self.runs_name].create_index([('query', 1), ('collected_at', -1)])
            collected_at = datetime.now(timezone.utc).isoformat()
            run = {'query': query, 'collected_at': collected_at, 'source_count': len(sources), 'errors': errors or [], 'provider': 'volcengine_doubao_search'}
            run_result = db[self.runs_name].insert_one(run)
            documents = []
            for source in sources:
                document = dict(source)
                document.update({'query': query, 'run_id': run_result.inserted_id, 'persisted_at': collected_at})
                documents.append(document)
            if documents:
                db[self.sources_name].insert_many(documents, ordered=False)
            return None
        except Exception as exc:
            # Do not include the MongoDB URL, which may contain credentials.
            message = str(exc).replace(self.url, '[REDACTED_MONGO_URL]')
            return f'MongoDB 写入失败：{message}'

"""Process settings. Only the coordinator owns persistent files."""
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('OPINION_DATA_DIR', str(ROOT))).resolve()
APPS = ('topic_search', 'multimedia_search', 'insight_analysis', 'report_generator')
SOURCE_TYPES = ('news', 'image', 'video', 'forum_post', 'internal_data')
LEASE_SECONDS = 30

def load_dotenv(path=None):
    """Load local .env without overwriting explicitly exported variables."""
    path = Path(path or ROOT / '.env')
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)

load_dotenv()

def now():
    return datetime.now().astimezone().isoformat(timespec='microseconds')

def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default

def config():
    return read_json(DATA / 'config' / 'config.json', {
        'system': {'name': '舆情分析平台', 'port': 9000},
        'apps': {a: {'enabled': True} for a in APPS},
        'collection': {'keywords': ['人工智能'], 'interval_seconds': 5},
        'runtime': {'demo_mode': True},
        'cluster': {'role': 'coordinator', 'worker_lease_seconds': 30, 'max_attempts': 3,
                    'worker_token_env': 'OPINION_WORKER_TOKEN'}})

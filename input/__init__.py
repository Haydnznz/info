"""Information input layer: three independent source families."""
from .public_info import collect as collect_public
from .multimedia import collect as collect_multimedia
from .internal_data import collect as collect_internal
from utils.settings import config
from utils.mongo_store import MongoInputStore

def collect_all(query, source_types):
    """Run the three collectors independently; failures are isolated per input."""
    source_types = set(source_types)
    settings = config()
    results = []
    errors = []
    for name, collector in (('public_info', collect_public), ('multimedia', collect_multimedia), ('internal_data', collect_internal)):
        try:
            collected = collector(query, source_types, settings)
            if isinstance(collected, tuple):
                items, collector_errors = collected
                results.extend(items)
                errors.extend({'input': name, 'error': str(error)} for error in collector_errors)
            else:
                results.extend(collected)
        except Exception as exc:
            errors.append({'input': name, 'error': f'{type(exc).__name__}: {exc}'})
    mongo_error = MongoInputStore().persist(query, results, errors)
    if mongo_error:
        errors.append({'input': 'mongodb', 'error': mongo_error})
    return results, errors

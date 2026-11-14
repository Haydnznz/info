from processor.embedding import embed
from utils.vector_store import MilvusVectorStore, VectorStoreError


def index_sources(sources, report_id=""):
    if not sources:
        return {"indexed": 0, "status": "empty"}
    records = []
    for source in sources:
        item = dict(source)
        item["report_id"] = report_id
        item["text"] = item.get("clean_text") or f"{item.get('title', '')} {item.get('summary', '')}"
        records.append(item)
    vectors = embed([r["text"] for r in records])
    store = MilvusVectorStore(dimension=len(vectors[0]))
    store.upsert(records, vectors)
    return {"indexed": len(records), "status": "ok", "dimension": len(vectors[0])}


def retrieve(query, limit=10, source_type=None):
    vector = embed([query])[0]
    store = MilvusVectorStore(dimension=len(vector))
    hits = store.search(vector, limit=limit, source_type=source_type)
    return {"status": "ok", "hits": hits}

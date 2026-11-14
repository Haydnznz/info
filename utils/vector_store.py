"""Milvus vector store.  External connectivity is explicit and never mocked."""
import json
import os
from datetime import datetime, timezone


class VectorStoreError(RuntimeError):
    pass


class MilvusVectorStore:
    def __init__(self, client=None, collection=None, dimension=None):
        self.collection = collection or os.getenv("MILVUS_COLLECTION", "opinion_sources")
        self.dimension = int(dimension or os.getenv("MILVUS_DIMENSION", "1024"))
        self.timeout = float(os.getenv("MILVUS_TIMEOUT", "3"))
        self.client = client
        self.available = False
        if self.client is None and str(os.getenv("MILVUS_ENABLED", "true")).lower() == "true":
            uri = (os.getenv("MILVUS_URL") or os.getenv("MILVUS_URI") or "").strip()
            if not uri:
                host, port = os.getenv("MILVUS_HOST", "127.0.0.1"), os.getenv("MILVUS_PORT", "19530")
                uri = f"http://{host}:{port}"
            try:
                from pymilvus import MilvusClient
                self.client = MilvusClient(uri=uri, user=os.getenv("MILVUS_USER", ""), password=os.getenv("MILVUS_PASSWORD", ""), token=os.getenv("MILVUS_TOKEN", ""), db_name=os.getenv("MILVUS_DB_NAME", "default"), timeout=self.timeout)
            except Exception as exc:
                raise VectorStoreError(f"Milvus 连接失败: {exc}") from exc
        if self.client is not None:
            self._ensure_collection()
            self.available = True

    def _ensure_collection(self):
        if self.client.has_collection(self.collection):
            return
        from pymilvus import DataType
        schema = self.client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=128)
        schema.add_field("source_id", DataType.VARCHAR, max_length=128)
        schema.add_field("report_id", DataType.VARCHAR, max_length=128)
        schema.add_field("text", DataType.VARCHAR, max_length=8192)
        schema.add_field("source_type", DataType.VARCHAR, max_length=64)
        schema.add_field("metadata", DataType.JSON)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=self.dimension)
        index = self.client.prepare_index_params()
        index.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
        self.client.create_collection(self.collection, schema=schema, index_params=index)

    def upsert(self, records, vectors):
        if not self.available:
            raise VectorStoreError("Milvus 未连接，不能入库")
        if len(records) != len(vectors):
            raise ValueError("records 和 vectors 长度不一致")
        rows = []
        for record, vector in zip(records, vectors):
            rows.append({"id": str(record.get("source_id") or record.get("id")), "source_id": str(record.get("source_id", "")), "report_id": str(record.get("report_id", "")), "text": str(record.get("text", record.get("summary", "")))[:8192], "source_type": str(record.get("type", "source")), "metadata": {"title": record.get("title", ""), "url": record.get("url", ""), "stored_at": datetime.now(timezone.utc).isoformat()}, "vector": vector})
        return self.client.upsert(self.collection, rows, timeout=self.timeout)

    def search(self, vector, limit=10, source_type=None):
        if not self.available:
            raise VectorStoreError("Milvus 未连接，不能检索")
        filt = f'source_type == "{str(source_type).replace(chr(34), chr(92)+chr(34))}"' if source_type else ""
        return self.client.search(self.collection, [vector], filter=filt, limit=limit, output_fields=["source_id", "report_id", "text", "source_type", "metadata"], search_params={"metric_type": "COSINE"}, timeout=self.timeout)[0]

"""Local BGE-M3 embeddings with a safe, lazy loading boundary.

The model is never loaded during module import.  This keeps the API and tests
usable when the model files or CUDA runtime are unavailable.
"""
import os
from pathlib import Path


class EmbeddingError(RuntimeError):
    pass


class BGEEmbedder:
    def __init__(self, model_path=None, device=None, fp16=None, model=None):
        self.model_path = model_path or os.getenv("BGE_M3_PATH") or os.getenv("BGE_M3", "BAAI/bge-m3")
        self.device = device or os.getenv("BGE_DEVICE", "cpu")
        self.fp16 = str(fp16 if fp16 is not None else os.getenv("BGE_FP16", "False")).lower() == "true"
        self._model = model
        self.backend = "injected" if model is not None else None
        self.dimension = None

    def _load(self):
        if self._model is not None:
            return self._model
        path = Path(self.model_path)
        if not path.exists() and Path(self.model_path).drive:
            raise EmbeddingError(f"BGE 模型路径不存在: {self.model_path}")
        device = self.device
        try:
            import torch
            if device.startswith("cuda") and not torch.cuda.is_available():
                device = "cpu"
        except Exception:
            if device.startswith("cuda"):
                device = "cpu"
        # sentence-transformers supports the local BGE-M3 export and is lighter
        # to initialize than the FlagEmbedding wrapper for this service.
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(str(path), device=device)
            self.backend = "sentence-transformers"
        except Exception as exc:
            raise EmbeddingError(f"无法加载 BGE-M3 模型: {exc}") from exc
        if self.fp16 and device.startswith("cuda") and hasattr(self._model, "half"):
            self._model.half()
        return self._model

    def embed(self, texts):
        texts = [str(x or "") for x in texts]
        if not texts:
            return []
        model = self._load()
        try:
            if hasattr(model, "encode"):
                vectors = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
            elif hasattr(model, "encode_queries"):
                vectors = model.encode_queries(texts)
            else:
                vectors = model(texts)
            result = [list(map(float, row)) for row in vectors]
            self.dimension = len(result[0]) if result else self.dimension
            return result
        except Exception as exc:
            raise EmbeddingError(f"向量生成失败: {exc}") from exc


_default = None


def get_embedder():
    global _default
    if _default is None:
        _default = BGEEmbedder()
    return _default


def embed(texts):
    return get_embedder().embed(texts)

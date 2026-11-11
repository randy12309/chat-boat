"""Text embeddings for Qdrant (local ONNX model via fastembed; no API key, no network after first download)."""
import os
import threading
from typing import Callable

MODEL_NAME = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
DIMENSIONS = 384  # the vector size of the Qdrant collections; changing the model means rebuilding them

Embedder = Callable[[list[str]], list[list[float]]]

_model = None
_lock = threading.Lock()
_override: Embedder | None = None


def set_embedder(fn: Embedder | None) -> None:
    """Replace the embedding function (tests use a small deterministic one)."""
    global _override
    _override = fn


def embed(texts: list[str]) -> list[list[float]]:
    if _override is not None:
        return _override(texts)
    global _model
    with _lock:
        if _model is None:
            from fastembed import TextEmbedding

            _model = TextEmbedding(MODEL_NAME)
    return [[float(x) for x in v] for v in _model.embed(texts)]

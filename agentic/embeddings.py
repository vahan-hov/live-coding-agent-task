"""Embedding backends behind a single `Embedder` protocol.

The RAG tool depends only on the protocol, so the embedding model is a swappable
detail:

* `SentenceTransformerEmbedder` — real local embeddings (all-MiniLM-L6-v2). The
  model auto-downloads from the Hugging Face Hub on first use (~80 MB) and is
  cached afterwards, so the demo is reproducible with no manual setup. Used by
  the demo / main.py.
* `HashingEmbedder` — a tiny deterministic embedder with no dependencies and no
  network. Used by the test suite so retrieval mechanics can be exercised
  offline and fast, against the *same* Qdrant pipeline.
"""

from __future__ import annotations

import hashlib
import math
import os
from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    """Turns text into fixed-length, normalized float vectors."""

    @property
    def dim(self) -> int:
        """Dimensionality of the produced vectors."""
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts, preserving order."""
        ...


class SentenceTransformerEmbedder:
    """Real embeddings via sentence-transformers (lazy-loaded, auto-downloads)."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self.model_name = model_name
        self._model = None  # loaded on first embed() call
        self._dim: int | None = None

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        # Quiet the Hugging Face hub's "no HF_TOKEN" notice and download bars: we
        # use a small public model anonymously and don't need a token. Set before
        # importing sentence-transformers (which pulls in huggingface_hub), and
        # only if the user hasn't configured these themselves. The notice is
        # logged at WARNING by the hub, so raising its verbosity to error hides it.
        os.environ.setdefault("HF_HUB_VERBOSITY", "error")
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - environment guard
            raise RuntimeError(
                "sentence-transformers is not installed. Run "
                "`pip install -r requirements.txt`."
            ) from exc
        try:
            # First call downloads the model from the HF Hub and caches it;
            # later calls (and offline runs) load from cache.
            self._model = SentenceTransformer(self.model_name)
        except Exception as exc:  # pragma: no cover - network guard
            raise RuntimeError(
                f"Could not load embedding model '{self.model_name}'. The first "
                "run needs internet to download it (~80 MB); afterwards it works "
                f"offline from cache. Underlying error: {exc}"
            ) from exc
        # Method was renamed across sentence-transformers versions; support both.
        get_dim = getattr(
            self._model,
            "get_embedding_dimension",
            self._model.get_sentence_embedding_dimension,
        )
        self._dim = int(get_dim())

    @property
    def dim(self) -> int:
        self._ensure_model()
        assert self._dim is not None
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._ensure_model()
        assert self._model is not None
        vectors = self._model.encode(
            texts, normalize_embeddings=True, convert_to_numpy=True
        )
        return [v.tolist() for v in vectors]


class HashingEmbedder:
    """Deterministic bag-of-tokens hashing embedder (test/offline use only).

    Not semantically rich, but stable and dependency-free: identical text always
    yields identical vectors, and shared tokens raise cosine similarity, which is
    enough to verify the vector-store + retrieval wiring end to end.
    """

    def __init__(self, dim: int = 256) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        for token in text.lower().split():
            h = hashlib.md5(token.encode("utf-8")).digest()
            idx = int.from_bytes(h[:4], "big") % self._dim
            sign = 1.0 if h[4] & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

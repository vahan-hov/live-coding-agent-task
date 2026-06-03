"""RAG tool: a real retrieval pipeline over a Qdrant vector store.

Indexing (once, at construction):
    load corpus docs -> chunk into overlapping windows -> embed each chunk
    -> upsert vectors + metadata into a Qdrant collection.

Query:
    embed the query -> cosine ANN search in Qdrant -> return top-k chunks as
    Evidence, each carrying its source doc and similarity score.

Qdrant runs in embedded `:memory:` mode by default (no server, no Docker). To
point at a real Qdrant deployment, pass a configured `QdrantClient` instead —
nothing else changes.
"""

from __future__ import annotations

import logging
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from agentic.embeddings import Embedder, SentenceTransformerEmbedder
from agentic.models import Evidence, ToolName
from agentic.tools.base import Tool, ToolUnavailable

logger = logging.getLogger("agentic.tools.rag")

DEFAULT_CORPUS_DIR = Path(__file__).resolve().parent.parent / "corpus"


def chunk_text(text: str, *, max_words: int = 60, overlap: int = 15) -> list[str]:
    """Split text into overlapping word windows.

    Overlap keeps sentences that straddle a boundary retrievable from either
    chunk. Returns the whole text as one chunk when it is short.
    """
    words = text.split()
    if len(words) <= max_words:
        return [text.strip()] if text.strip() else []
    step = max_words - overlap
    chunks = []
    for start in range(0, len(words), step):
        window = words[start : start + max_words]
        if window:
            chunks.append(" ".join(window))
        if start + max_words >= len(words):
            break
    return chunks


class RAGTool(Tool):
    """Semantic search over internal documents."""

    name = ToolName.RAG

    def __init__(
        self,
        *,
        embedder: Embedder | None = None,
        corpus_dir: Path | None = None,
        client: QdrantClient | None = None,
        collection: str = "internal_docs",
        **tool_kwargs,
    ) -> None:
        super().__init__(**tool_kwargs)
        self.embedder = embedder or SentenceTransformerEmbedder()
        self.corpus_dir = corpus_dir or DEFAULT_CORPUS_DIR
        self.collection = collection
        self.client = client or QdrantClient(":memory:")
        self._index_corpus()

    def _index_corpus(self) -> None:
        """Chunk, embed, and upsert every doc in the corpus directory."""
        docs = sorted(self.corpus_dir.glob("*.md"))
        if not docs:
            raise ToolUnavailable(f"no corpus documents found in {self.corpus_dir}")

        chunks: list[str] = []
        sources: list[str] = []
        for doc in docs:
            for chunk in chunk_text(doc.read_text(encoding="utf-8")):
                chunks.append(chunk)
                sources.append(doc.name)

        vectors = self.embedder.embed(chunks)
        if self.client.collection_exists(self.collection):
            self.client.delete_collection(self.collection)
        self.client.create_collection(
            collection_name=self.collection,
            vectors_config=VectorParams(
                size=self.embedder.dim, distance=Distance.COSINE
            ),
        )
        points = [
            PointStruct(
                id=i,
                vector=vectors[i],
                payload={"text": chunks[i], "source": sources[i]},
            )
            for i in range(len(chunks))
        ]
        self.client.upsert(collection_name=self.collection, points=points)
        logger.info(
            "indexed %d chunks from %d docs into '%s'",
            len(chunks),
            len(docs),
            self.collection,
        )

    def _run(self, query: str, *, top_k: int) -> list[Evidence]:
        query_vec = self.embedder.embed([query])[0]
        # `query_points` is the current Qdrant search API; it returns a response
        # object whose `.points` are the scored matches.
        hits = self.client.query_points(
            collection_name=self.collection,
            query=query_vec,
            limit=top_k,
        ).points
        return [
            Evidence(
                text=hit.payload["text"],
                source=hit.payload["source"],
                score=float(hit.score),
                tool=self.name,
                metadata={"point_id": hit.id},
            )
            for hit in hits
        ]

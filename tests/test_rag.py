"""Real retrieval tests against the in-memory Qdrant store (HashingEmbedder)."""

from __future__ import annotations

from agentic.tools.rag import RAGTool, chunk_text


def test_chunking_overlaps_long_text() -> None:
    text = " ".join(f"w{i}" for i in range(200))
    chunks = chunk_text(text, max_words=60, overlap=15)
    assert len(chunks) > 1
    # Consecutive chunks share their overlap region.
    assert chunks[0].split()[-15:] == chunks[1].split()[:15]


def test_short_text_is_single_chunk() -> None:
    assert chunk_text("just a short doc") == ["just a short doc"]


def test_retrieval_finds_relevant_doc(rag_tool: RAGTool) -> None:
    result = rag_tool.call("password manager and MFA requirements", top_k=3)
    assert result.ok
    assert result.evidence
    # The security policy is the doc that talks about passwords/MFA.
    assert any(e.source == "security_policy.md" for e in result.evidence)


def test_results_sorted_by_score(rag_tool: RAGTool) -> None:
    result = rag_tool.call("how many PTO days do I accrue", top_k=4)
    scores = [e.score for e in result.evidence]
    assert scores == sorted(scores, reverse=True)

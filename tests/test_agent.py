"""End-to-end agent tests: citations, multi-turn state, graceful degradation."""

from __future__ import annotations

from agentic.agent import Agent
from agentic.circuit_breaker import CircuitState
from agentic.embeddings import HashingEmbedder
from agentic.models import ToolName
from agentic.tools.rag import RAGTool
from agentic.tools.web_search import WebSearchTool


def test_answer_has_citations(agent: Agent) -> None:
    resp = agent.run("What is the PTO carry-over limit?", session_id="a")
    assert resp.citations
    assert "[1]" in resp.answer
    assert "Sources:" in resp.answer
    assert not resp.degraded


def test_multi_turn_state_is_persisted(agent: Agent) -> None:
    agent.run("What is the meal per diem for travel?", session_id="b")
    state = agent.store.get("b")
    # one user + one assistant turn recorded
    assert len(state.history) == 2
    resp2 = agent.run("tell me more", session_id="b")
    # follow-up was rewritten using the prior turn
    assert "per diem" in resp2.plan.search_query
    assert len(state.history) == 4


def test_web_failure_degrades_gracefully() -> None:
    rag = RAGTool(embedder=HashingEmbedder())
    web = WebSearchTool(failure_rate=1.0, max_retries=1)  # always fails
    agent = Agent({ToolName.RAG: rag, ToolName.WEB_SEARCH: web})

    resp = agent.run("What is the latest weather and the PTO policy?", session_id="c")
    # The web tool was planned (recency signal) but failed; RAG still answered.
    assert resp.plan.tools == [ToolName.RAG, ToolName.WEB_SEARCH]
    assert resp.citations  # RAG evidence still produced citations
    web_result = next(r for r in resp.tool_results if r.tool is ToolName.WEB_SEARCH)
    assert not web_result.ok


def test_web_search_returns_no_evidence_on_miss() -> None:
    # An unmatched query yields no evidence (no placeholder "result" that could
    # otherwise rank into the citations as if it were a real source).
    web = WebSearchTool(failure_rate=0.0)
    result = web.call("an unmatched topic with no canned entry", top_k=5)
    assert result.ok
    assert result.evidence == []


def test_breaker_opens_and_then_fails_fast() -> None:
    rag = RAGTool(embedder=HashingEmbedder())
    # threshold=1 + no retries => one failed call trips the breaker immediately.
    web = WebSearchTool(failure_rate=1.0, max_retries=0)
    web.breaker.failure_threshold = 1
    agent = Agent({ToolName.RAG: rag, ToolName.WEB_SEARCH: web})

    agent.run("latest news", session_id="d")  # trips breaker
    assert web.breaker.state is CircuitState.OPEN

    resp2 = agent.run("latest news again", session_id="d")  # fast-fail path
    web_result = next(r for r in resp2.tool_results if r.tool is ToolName.WEB_SEARCH)
    assert web_result.degraded

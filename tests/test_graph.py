"""Tests for the LangGraph orchestration (agentic/graph.py).

These mirror the hand-rolled agent's behavioural contract — citations, conditional
web routing, multi-turn state via the checkpointer, and graceful degradation — to
prove the two orchestrations are interchangeable.
"""

from __future__ import annotations

import pytest

from agentic.embeddings import HashingEmbedder
from agentic.models import ToolName
from agentic.tools.rag import RAGTool
from agentic.tools.web_search import WebSearchTool

# Skip the whole module if langgraph isn't installed, so the core suite still runs.
pytest.importorskip("langgraph")

from agentic.graph import GraphAgent  # noqa: E402  (after importorskip)


def _agent(*, web_failure_rate: float = 0.0, **web_kwargs) -> GraphAgent:
    rag = RAGTool(embedder=HashingEmbedder())
    web = WebSearchTool(failure_rate=web_failure_rate, **web_kwargs)
    return GraphAgent({ToolName.RAG: rag, ToolName.WEB_SEARCH: web})


def test_knowledge_query_takes_rag_only_branch() -> None:
    resp = _agent().run("What is the PTO carry-over limit?", session_id="a")
    assert resp.plan.tools == [ToolName.RAG]
    # The conditional edge skipped the web node -> exactly one tool result.
    assert len(resp.tool_results) == 1
    assert resp.tool_results[0].tool is ToolName.RAG
    assert resp.citations
    assert "[1]" in resp.answer and "Sources:" in resp.answer


def test_recency_query_takes_web_branch() -> None:
    resp = _agent().run("What is the latest news on AI in 2026?", session_id="b")
    assert ToolName.WEB_SEARCH in resp.plan.tools
    # Both branches ran -> two results, and a web-sourced citation is present.
    assert {r.tool for r in resp.tool_results} == {ToolName.RAG, ToolName.WEB_SEARCH}
    assert any(c.tool is ToolName.WEB_SEARCH for c in resp.citations)


def test_checkpointer_persists_state_across_turns() -> None:
    agent = _agent()
    agent.run("What is the meal per diem for travel?", session_id="c")
    resp2 = agent.run("tell me more", session_id="c")
    # The follow-up was resolved against the prior turn, read back from the
    # checkpointer (LangGraph's native session state).
    assert "per diem" in resp2.plan.search_query
    assert resp2.plan.search_query != "tell me more"


def test_no_cross_turn_result_leakage() -> None:
    agent = _agent()
    # Turn 1 hits both tools; turn 2 hits only RAG. The web result from turn 1
    # must not bleed into turn 2's results.
    agent.run("latest AI news in 2026", session_id="d")
    resp2 = agent.run("What is the PTO carry-over limit?", session_id="d")
    assert len(resp2.tool_results) == 1
    assert resp2.tool_results[0].tool is ToolName.RAG


def test_web_failure_degrades_gracefully() -> None:
    agent = _agent(web_failure_rate=1.0, max_retries=1)  # web always fails
    resp = agent.run("latest weather and the PTO policy", session_id="e")
    assert resp.plan.tools == [ToolName.RAG, ToolName.WEB_SEARCH]
    # Web failed its retries but RAG still produced a cited answer: the turn
    # completes instead of erroring out.
    assert resp.citations
    web_result = next(r for r in resp.tool_results if r.tool is ToolName.WEB_SEARCH)
    assert not web_result.ok


def test_open_breaker_marks_response_degraded() -> None:
    # threshold=1 + no retries => the first failure trips the breaker; the next
    # turn fast-fails with a *degraded* result, which flags the whole response.
    agent = _agent(web_failure_rate=1.0, max_retries=0)
    web = agent.tools[ToolName.WEB_SEARCH]
    web.breaker.failure_threshold = 1

    agent.run("latest news", session_id="f")  # trips breaker
    resp2 = agent.run("latest news again", session_id="f")  # fast-fail path
    web_result = next(r for r in resp2.tool_results if r.tool is ToolName.WEB_SEARCH)
    assert web_result.degraded
    assert resp2.degraded

"""Routing tests for the query analyzer."""

from __future__ import annotations

from agentic.models import ToolName
from agentic.planner import QueryAnalyzer
from agentic.state import ConversationState


def _state() -> ConversationState:
    return ConversationState(session_id="t")


def test_knowledge_query_uses_rag_only() -> None:
    plan = QueryAnalyzer().analyze("What is the PTO carry-over limit?", _state())
    assert plan.tools == [ToolName.RAG]


def test_recency_signal_adds_web_search() -> None:
    plan = QueryAnalyzer().analyze("What is the latest news on AI?", _state())
    assert ToolName.WEB_SEARCH in plan.tools


def test_year_signal_adds_web_search() -> None:
    plan = QueryAnalyzer().analyze("What happened in 2026?", _state())
    assert ToolName.WEB_SEARCH in plan.tools


def test_follow_up_is_rewritten_against_prior_turn() -> None:
    state = _state()
    state.add_user("What is the meal per diem for travel?")
    state.add_assistant("...")
    plan = QueryAnalyzer().analyze("tell me more", state)
    assert "meal per diem" in plan.search_query
    assert plan.search_query != "tell me more"


def test_short_question_is_not_treated_as_follow_up() -> None:
    # A terse but self-contained question must not be folded into the prior turn.
    state = _state()
    state.add_user("What is the meal per diem for travel?")
    state.add_assistant("...")
    plan = QueryAnalyzer().analyze("what is PTO", state)
    assert plan.search_query == "what is PTO"

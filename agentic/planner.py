"""Query analysis -> a tool plan.

`QueryAnalyzer` decides which tools a query needs and why. The heuristics here
stand in for what a production system would do with an LLM "router" call: the
output contract (`ToolPlan`) is identical, so dropping in an LLM planner means
re-implementing one method without touching the agent.

Routing signals:
  * RAG (internal docs) is the default for knowledge questions.
  * Web search is added when the query asks about recency or clearly external
    information ("latest", "news", a recent year, "stock price", ...).
  * Conversational follow-ups ("tell me more", "what about ...") are resolved
    against the prior turn so the search query is self-contained.
"""

from __future__ import annotations

import re

from agentic.models import ToolName, ToolPlan
from agentic.state import ConversationState

# Phrases that signal a need for fresh / external information.
_WEB_SIGNALS = (
    "latest",
    "recent",
    "today",
    "current",
    "news",
    "this year",
    "right now",
    "stock",
    "weather",
    "price of",
)
_YEAR_RE = re.compile(r"\b(202[4-9]|20[3-9]\d)\b")

# Short follow-up phrases that lean on conversational context.
_FOLLOW_UP_SIGNALS = (
    "tell me more",
    "what about",
    "and the",
    "go on",
    "more detail",
    "explain further",
)

# A short query starting with one of these reads as a self-contained question
# ("what is PTO"), so it should not be folded into the prior turn.
_QUESTION_WORDS = frozenset(
    {"what", "who", "when", "where", "why", "how", "which", "is", "are", "can", "do"}
)


class QueryAnalyzer:
    def analyze(self, query: str, state: ConversationState) -> ToolPlan:
        q = query.lower().strip()

        needs_web = any(sig in q for sig in _WEB_SIGNALS) or bool(_YEAR_RE.search(q))

        # A query is a follow-up if it uses an explicit follow-up phrase, or if
        # it is so short that it can only make sense in context. We exclude very
        # short queries that begin with a question word ("what is PTO"), since
        # those read as self-contained questions rather than continuations.
        words = q.split()
        starts_with_question_word = bool(words) and words[0] in _QUESTION_WORDS
        is_terse = len(words) <= 3 and not starts_with_question_word
        is_follow_up = any(sig in q for sig in _FOLLOW_UP_SIGNALS) or is_terse

        # Resolve a terse follow-up against the previous user turn so the tools
        # receive a self-contained query.
        search_query = query
        if is_follow_up and state.last_user_message:
            search_query = f"{state.last_user_message} {query}".strip()

        tools = [ToolName.RAG]  # internal knowledge is always the baseline source
        rationale_parts = ["internal docs are the primary source (RAG)"]
        if needs_web:
            tools.append(ToolName.WEB_SEARCH)
            rationale_parts.append(
                "query signals recency/external info, so web search is added"
            )
        if is_follow_up and state.last_user_message:
            rationale_parts.append("follow-up resolved against prior turn")

        return ToolPlan(
            tools=tools,
            rationale="; ".join(rationale_parts),
            search_query=search_query,
        )

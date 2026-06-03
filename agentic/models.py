"""Shared data structures passed between the agent's stages.

These are plain immutable dataclasses (the wire format of the pipeline). Keeping
them dependency-free means planner, tools, synthesizer, and state all speak the
same vocabulary without importing each other's internals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ToolName(str, Enum):
    """Tools the planner is allowed to route to."""

    RAG = "rag"
    WEB_SEARCH = "web_search"


@dataclass(frozen=True)
class Evidence:
    """A single retrieved piece of information that can back a claim.

    `source` is a human-readable origin (a document path or a URL) and is what
    citations point back to. `score` is the retrieval relevance (higher = better).
    """

    text: str
    source: str
    score: float
    tool: ToolName
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    """The outcome of one tool invocation.

    A tool that degrades (e.g. circuit open) returns `ok=False` with an `error`
    note rather than raising, so the agent can still synthesize a partial answer.
    """

    tool: ToolName
    ok: bool
    evidence: list[Evidence] = field(default_factory=list)
    error: str | None = None
    degraded: bool = False  # True when the tool was skipped due to an open breaker


@dataclass(frozen=True)
class ToolPlan:
    """The planner's decision about which tools to call and why."""

    tools: list[ToolName]
    rationale: str
    # The (possibly rewritten) query to send to the tools, e.g. resolving a
    # follow-up like "tell me more about that" against prior conversation.
    search_query: str


@dataclass(frozen=True)
class Citation:
    """A numbered reference shown to the user, mapping [n] -> a source."""

    index: int
    source: str
    snippet: str
    tool: ToolName


@dataclass(frozen=True)
class AgentResponse:
    """The final, user-facing result of a single turn."""

    answer: str
    citations: list[Citation]
    plan: ToolPlan
    tool_results: list[ToolResult]
    trace: list[str] = field(default_factory=list)

    @property
    def degraded(self) -> bool:
        """True if any planned tool was unavailable, so the answer is partial."""
        return any(r.degraded for r in self.tool_results)

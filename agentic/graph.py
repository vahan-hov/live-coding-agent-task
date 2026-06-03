"""The same agent, expressed as a LangGraph `StateGraph`.

This is a second orchestration of the *identical* components used by the
hand-rolled [`agent.py`](agent.py) — the `QueryAnalyzer`, the resilient `Tool`s,
and the `Synthesizer`. Only the wiring differs: here the pipeline is a declarative
state graph rather than a procedural loop.

    START ─▶ plan ─▶ rag ──(plan includes web?)──▶ web ─▶ synth ─▶ END
                              └──────────(no)───────────────▶ synth ─▶ END

What LangGraph contributes over the procedural version:

* **Declarative routing** — the "should we also hit the web?" decision is a
  conditional edge, so the control flow is data you can inspect/visualize rather
  than branching code buried in a loop.
* **Built-in multi-turn state** — a `MemorySaver` checkpointer persists state per
  `thread_id`, which is LangGraph's native equivalent of the hand-rolled
  `SessionStore`. A "session" is just a `thread_id`.

What it deliberately does *not* own: resilience. The circuit breaker / retry /
timeout live inside each `Tool` (see `tools/base.py`), so they apply unchanged
here — resilience is a property of the dependency, not the orchestrator.

State design note: every channel except `history` uses plain last-write-wins
semantics. The two tool branches write to *separate* fields (`rag_result`,
`web_result`) rather than appending to a shared list, which keeps the state
trivially serializable for the checkpointer and avoids any cross-turn
accumulation. Only `history` uses an additive reducer, because it is the one
thing meant to grow across turns.
"""

from __future__ import annotations

import operator
from collections.abc import Mapping
from typing import Annotated, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph

from agentic.models import (
    AgentResponse,
    Citation,
    Evidence,
    ToolName,
    ToolPlan,
    ToolResult,
)
from agentic.planner import QueryAnalyzer
from agentic.state import ConversationState
from agentic.synthesizer import Synthesizer
from agentic.tools.base import Tool


class GraphState(TypedDict, total=False):
    """The state threaded between nodes (and checkpointed per session)."""

    query: str
    # The running conversation; the only field meant to persist/grow across turns.
    history: Annotated[list[str], operator.add]  # "user: ..." / "assistant: ..."
    plan: ToolPlan
    # Each tool branch writes its own field (last-write-wins), so there is no
    # shared list to reduce and nothing accumulates across turns.
    rag_result: ToolResult | None
    web_result: ToolResult | None
    answer: str
    citations: list[Citation]
    trace: list[str]


class GraphAgent:
    """LangGraph orchestration of the planner / tools / synthesizer."""

    def __init__(
        self,
        tools: Mapping[ToolName, Tool],
        *,
        planner: QueryAnalyzer | None = None,
        synthesizer: Synthesizer | None = None,
        top_k: int = 4,
    ) -> None:
        self.tools = dict(tools)
        self.planner = planner or QueryAnalyzer()
        self.synthesizer = synthesizer or Synthesizer()
        self.top_k = top_k
        # The checkpointer is the state store: state is keyed by thread_id, so
        # multi-turn memory comes for free from the framework. We allow-list our
        # own dataclasses (Plan/Result/Citation/...) for (de)serialization, since
        # they travel through the checkpointed state.
        serde = JsonPlusSerializer(
            allowed_msgpack_modules=[
                ToolName, ToolPlan, ToolResult, Evidence, Citation,
            ]
        )
        self._app = self._build().compile(checkpointer=MemorySaver(serde=serde))

    # --- graph definition --------------------------------------------------

    def _build(self) -> StateGraph:
        g = StateGraph(GraphState)
        g.add_node("plan", self._plan_node)
        g.add_node("rag", self._rag_node)
        g.add_node("web", self._web_node)
        g.add_node("synth", self._synth_node)

        g.add_edge(START, "plan")
        g.add_edge("plan", "rag")
        # The web search is the *optional* branch: a conditional edge consults the
        # plan to decide whether to visit the web node or skip straight to synth.
        g.add_conditional_edges(
            "rag",
            self._route_after_rag,
            {"web": "web", "synth": "synth"},
        )
        g.add_edge("web", "synth")
        g.add_edge("synth", END)
        return g

    def _route_after_rag(self, state: GraphState) -> str:
        """Conditional edge: take the web branch only when the plan asked for it."""
        return "web" if ToolName.WEB_SEARCH in state["plan"].tools else "synth"

    # --- nodes (thin adapters over the shared components) ------------------

    def _plan_node(self, state: GraphState) -> dict:
        # Rebuild a ConversationState from the checkpointed history so the same
        # planner (and its follow-up resolution) works unchanged.
        convo = self._convo_from_history(state.get("history", []))
        plan = self.planner.analyze(state["query"], convo)
        trace = [f"PLAN: tools={[t.value for t in plan.tools]} :: {plan.rationale}"]
        if plan.search_query != state["query"]:
            trace.append(f"REWRITE: '{state['query']}' -> '{plan.search_query}'")
        # Reset both tool slots for this turn (last-write-wins clears prior turn).
        return {"plan": plan, "rag_result": None, "web_result": None, "trace": trace}

    def _rag_node(self, state: GraphState) -> dict:
        result, line = self._invoke_tool(ToolName.RAG, state["plan"])
        return {"rag_result": result, "trace": [*state.get("trace", []), line]}

    def _web_node(self, state: GraphState) -> dict:
        result, line = self._invoke_tool(ToolName.WEB_SEARCH, state["plan"])
        return {"web_result": result, "trace": [*state.get("trace", []), line]}

    def _synth_node(self, state: GraphState) -> dict:
        # Preserve plan order (RAG first, then web) so citation numbering matches
        # the procedural agent.
        results = [r for r in (state.get("rag_result"), state.get("web_result")) if r]
        answer, citations = self.synthesizer.synthesize(state["query"], results)
        history = [f"user: {state['query']}", f"assistant: {answer}"]
        trace = [*state.get("trace", []), f"SYNTH: {len(citations)} citation(s)"]
        return {
            "answer": answer,
            "citations": citations,
            "history": history,
            "trace": trace,
        }

    # --- public API (mirrors Agent.run) ------------------------------------

    def run(self, query: str, *, session_id: str = "default") -> AgentResponse:
        """Execute one turn. `session_id` maps to a LangGraph `thread_id`, so
        state is persisted across calls by the checkpointer."""
        config = {"configurable": {"thread_id": session_id}}
        final = self._app.invoke({"query": query}, config)
        results = [r for r in (final.get("rag_result"), final.get("web_result")) if r]
        return AgentResponse(
            answer=final["answer"],
            citations=final.get("citations", []),
            plan=final["plan"],
            tool_results=results,
            trace=final.get("trace", []),
        )

    # --- helpers -----------------------------------------------------------

    def _invoke_tool(
        self, tool_name: ToolName, plan: ToolPlan
    ) -> tuple[ToolResult | None, str]:
        tool = self.tools.get(tool_name)
        if tool is None:
            return None, f"SKIP: no tool registered for '{tool_name.value}'"
        result = tool.call(plan.search_query, top_k=self.top_k)
        return result, self._describe(tool, result)

    @staticmethod
    def _convo_from_history(history: list[str]) -> ConversationState:
        convo = ConversationState(session_id="_graph")
        for line in history:
            role, _, content = line.partition(": ")
            if role == "user":
                convo.add_user(content)
            elif role == "assistant":
                convo.add_assistant(content)
        return convo

    @staticmethod
    def _describe(tool: Tool, result: ToolResult) -> str:
        breaker = tool.breaker.state.value
        if result.degraded:
            return f"TOOL {result.tool.value}: DEGRADED ({result.error}) [breaker={breaker}]"
        if result.ok:
            return f"TOOL {result.tool.value}: ok, {len(result.evidence)} hits [breaker={breaker}]"
        return f"TOOL {result.tool.value}: FAILED ({result.error}) [breaker={breaker}]"

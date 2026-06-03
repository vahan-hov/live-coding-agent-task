"""The orchestrator that ties the stages together.

`Agent.run(query, session_id)` executes one conversational turn:

    1. load conversation state for the session
    2. analyze the query -> ToolPlan (which tools, rewritten search query)
    3. dispatch the planned tools (each resilient: breaker + retry + timeout)
    4. synthesize a cited answer from the gathered evidence
    5. persist the turn back into the session

It records a human-readable `trace` of every decision so the demo is
self-explaining and the agent's behavior is observable.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from agentic.models import AgentResponse, ToolName, ToolResult
from agentic.planner import QueryAnalyzer
from agentic.state import SessionStore
from agentic.synthesizer import Synthesizer
from agentic.tools.base import Tool

logger = logging.getLogger("agentic.agent")


class Agent:
    def __init__(
        self,
        tools: Mapping[ToolName, Tool],
        *,
        planner: QueryAnalyzer | None = None,
        synthesizer: Synthesizer | None = None,
        store: SessionStore | None = None,
        top_k: int = 4,
    ) -> None:
        self.tools = dict(tools)
        self.planner = planner or QueryAnalyzer()
        self.synthesizer = synthesizer or Synthesizer()
        self.store = store or SessionStore()
        self.top_k = top_k

    def run(self, query: str, *, session_id: str = "default") -> AgentResponse:
        trace: list[str] = []
        state = self.store.get(session_id)

        # 1-2. analyze the query against conversation context.
        plan = self.planner.analyze(query, state)
        trace.append(f"PLAN: tools={[t.value for t in plan.tools]} :: {plan.rationale}")
        if plan.search_query != query:
            trace.append(f"REWRITE: '{query}' -> '{plan.search_query}'")

        # 3. dispatch each planned tool resiliently.
        results: list[ToolResult] = []
        for tool_name in plan.tools:
            tool = self.tools.get(tool_name)
            if tool is None:
                trace.append(f"SKIP: no tool registered for '{tool_name.value}'")
                continue
            result = tool.call(plan.search_query, top_k=self.top_k)
            results.append(result)
            if result.degraded:
                trace.append(
                    f"TOOL {tool_name.value}: DEGRADED ({result.error}) "
                    f"[breaker={tool.breaker.state.value}]"
                )
            elif result.ok:
                trace.append(
                    f"TOOL {tool_name.value}: ok, {len(result.evidence)} hits "
                    f"[breaker={tool.breaker.state.value}]"
                )
            else:
                trace.append(
                    f"TOOL {tool_name.value}: FAILED ({result.error}) "
                    f"[breaker={tool.breaker.state.value}]"
                )

        # 4. synthesize a cited answer.
        answer, citations = self.synthesizer.synthesize(query, results)
        trace.append(f"SYNTH: {len(citations)} citation(s)")

        # 5. persist the turn.
        state.add_user(query)
        state.add_assistant(answer)

        return AgentResponse(
            answer=answer,
            citations=citations,
            plan=plan,
            tool_results=results,
            trace=trace,
        )

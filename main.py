"""Scripted demo of the agentic system.

Runs a few end-to-end scenarios against the real RAG pipeline (Qdrant +
sentence-transformers) plus the mock web-search tool, printing the agent's
decision trace and cited answers for each turn.

    python main.py

The first run downloads the embedding model (~80 MB); later runs are offline.
"""

from __future__ import annotations

import logging
import random
import sys

from agentic.agent import Agent
from agentic.circuit_breaker import CircuitBreaker
from agentic.models import ToolName
from agentic.tools.rag import RAGTool
from agentic.tools.web_search import WebSearchTool

SEP = "=" * 72


def banner(title: str) -> None:
    print(f"\n{SEP}\n  {title}\n{SEP}")


def show(agent: Agent, query: str, session_id: str) -> None:
    print(f"\n>>> [{session_id}] {query}\n")
    resp = agent.run(query, session_id=session_id)
    print("--- trace ---")
    for line in resp.trace:
        print(f"  {line}")
    print("\n--- answer ---")
    print(resp.answer)
    if resp.degraded:
        print("\n(answer was produced in degraded mode)")


def build_agent(web_failure_rate: float = 0.0) -> Agent:
    rag = RAGTool()  # real Qdrant + sentence-transformers
    web = WebSearchTool(
        failure_rate=web_failure_rate,
        rng=random.Random(7),
        # Trip fast and recover slowly so the demo clearly shows the OPEN state.
        breaker=CircuitBreaker("web_search", failure_threshold=2, cooldown_seconds=60),
        max_retries=1,
    )
    return Agent({ToolName.RAG: rag, ToolName.WEB_SEARCH: web})


def main() -> None:
    # Make stdout UTF-8 so the demo prints cleanly on Windows consoles (cp1252).
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):  # pragma: no cover - older/odd streams
        pass
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    banner("Scenario 1 - internal knowledge (RAG only)")
    agent = build_agent()
    show(agent, "How many PTO days do I get and can I carry them over?", "s1")
    show(agent, "What are the password and MFA requirements?", "s1")

    banner("Scenario 2 - multi-turn follow-up (state carry-over)")
    show(agent, "What is the meal per diem for travel?", "s2")
    # Terse follow-up: planner resolves it against the prior turn.
    show(agent, "tell me more", "s2")

    banner("Scenario 3 - recency signal routes to web search")
    show(agent, "What is the latest news on AI in 2026?", "s2")

    banner("Scenario 4 - circuit breaker: web search is down -> graceful degrade")
    flaky = build_agent(web_failure_rate=1.0)  # web always fails
    # First call trips the breaker (after retries); subsequent calls fail fast.
    show(flaky, "What is the current weather and our PTO policy?", "s3")
    show(flaky, "And the latest stock market news plus expense limits?", "s3")
    web_breaker = flaky.tools[ToolName.WEB_SEARCH].breaker
    print(f"\nweb_search breaker state after failures: {web_breaker.state.value.upper()}")

    # Release each tool's timeout worker pool (idempotent; also covered by __del__).
    for built in (agent, flaky):
        for tool in built.tools.values():
            tool.close()


if __name__ == "__main__":
    main()

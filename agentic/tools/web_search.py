"""Web search tool (mock).

Stands in for an external search API. It returns canned results for a few topics
and is intentionally *flaky* — a configurable fraction of calls raise — so the
demo and tests can exercise retries, the circuit breaker, and graceful
degradation without depending on a real network service.

Swapping in a real backend means replacing `_run` with an HTTP call; the breaker
/ retry / timeout machinery in the base class stays the same.
"""

from __future__ import annotations

import random

from agentic.models import Evidence, ToolName
from agentic.tools.base import Tool, ToolUnavailable

# Canned "external" knowledge keyed by topic substrings.
_MOCK_INDEX: dict[str, list[tuple[str, str]]] = {
    "weather": [
        (
            "https://example-news.com/weather",
            "Forecasts for major cities are updated hourly by national weather services.",
        ),
    ],
    "stock": [
        (
            "https://example-finance.com/markets",
            "Major indices closed mixed today amid earnings reports and rate speculation.",
        ),
    ],
    "ai": [
        (
            "https://example-tech.com/ai-2026",
            "Recent industry coverage highlights rapid adoption of agentic AI systems in 2026.",
        ),
    ],
}


class WebSearchTool(Tool):
    """Mock external web search with injectable flakiness."""

    name = ToolName.WEB_SEARCH

    def __init__(
        self,
        *,
        failure_rate: float = 0.0,
        rng: random.Random | None = None,
        **tool_kwargs,
    ) -> None:
        super().__init__(**tool_kwargs)
        self.failure_rate = failure_rate
        self._rng = rng or random.Random()

    def _run(self, query: str, *, top_k: int) -> list[Evidence]:
        if self._rng.random() < self.failure_rate:
            raise ToolUnavailable("web search upstream returned 503")

        q = query.lower()
        results: list[Evidence] = []
        for topic, entries in _MOCK_INDEX.items():
            if topic in q:
                for url, snippet in entries:
                    results.append(
                        Evidence(
                            text=snippet,
                            source=url,
                            score=0.8,
                            tool=self.name,
                        )
                    )
        # No match -> return no evidence. A "nothing found" placeholder would
        # otherwise rank into the citations as if it were a real source; an empty
        # result lets the synthesizer fall back cleanly to the other tools.
        return results[:top_k]

"""Answer synthesis with citations.

Takes the evidence gathered from all tools and produces a user-facing answer in
which every cited source is numbered `[n]`, plus a `Sources:` list mapping each
number back to its document or URL. In a production system the prose would come
from an LLM constrained to cite only the supplied evidence; here we assemble it
deterministically from the evidence so the demo is reproducible and the citation
wiring is easy to verify.
"""

from __future__ import annotations

from agentic.models import Citation, Evidence, ToolName, ToolResult


class Synthesizer:
    def __init__(
        self,
        max_evidence: int = 4,
        snippet_chars: int = 160,
        min_score: float = 0.25,
    ) -> None:
        self.max_evidence = max_evidence
        self.snippet_chars = snippet_chars
        # Evidence below this relevance score is dropped rather than cited, so a
        # query with only one or two on-topic chunks doesn't get padded out to
        # `max_evidence` with weakly related filler. The threshold suits
        # normalized cosine scores (MiniLM ~0.4-0.6 on-topic, ~0.1-0.2 off);
        # tools on a different score scale should set this accordingly.
        self.min_score = min_score

    def synthesize(
        self, query: str, results: list[ToolResult]
    ) -> tuple[str, list[Citation]]:
        evidence = self._rank(results)
        degraded_tools = [r.tool for r in results if r.degraded]

        if not evidence:
            answer = (
                f"I couldn't find information to answer: \"{query}\". "
                "No internal documents or external sources returned usable results."
            )
            return self._append_degradation(answer, degraded_tools), []

        citations: list[Citation] = []
        lines = [f'Here is what I found for: "{query}"', ""]
        for i, ev in enumerate(evidence, start=1):
            citations.append(
                Citation(
                    index=i,
                    source=ev.source,
                    snippet=self._truncate(ev.text),
                    tool=ev.tool,
                )
            )
            lines.append(f"- {self._truncate(ev.text)} [{i}]")

        lines.append("")
        lines.append("Sources:")
        for c in citations:
            origin = "web" if c.tool is ToolName.WEB_SEARCH else "internal doc"
            lines.append(f"  [{c.index}] {c.source} ({origin})")

        answer = "\n".join(lines)
        return self._append_degradation(answer, degraded_tools), citations

    def _rank(self, results: list[ToolResult]) -> list[Evidence]:
        """Flatten successful results, drop weak hits, keep the top scorers."""
        evidence = [
            ev
            for r in results
            if r.ok
            for ev in r.evidence
            if ev.score >= self.min_score
        ]
        evidence.sort(key=lambda e: e.score, reverse=True)
        return evidence[: self.max_evidence]

    def _truncate(self, text: str) -> str:
        text = " ".join(text.split())
        if len(text) <= self.snippet_chars:
            return text
        return text[: self.snippet_chars].rstrip() + "..."

    def _append_degradation(
        self, answer: str, degraded_tools: list[ToolName]
    ) -> str:
        if not degraded_tools:
            return answer
        names = ", ".join(t.value for t in degraded_tools)
        return (
            f"{answer}\n\n"
            f"[!] Note: {names} was unavailable, so this answer may be incomplete."
        )

"""A small, dependency-light agentic system.

Pipeline per turn:
    analyze query -> plan tools -> execute tools (guarded by circuit breakers)
    -> synthesize a cited answer -> persist conversation state.

The RAG tool is a *real* retrieval pipeline (Qdrant vector store + local
sentence-transformers embeddings). The planner, web-search tool, and
synthesizer are deterministic mocks so the demo and tests run offline,
but every seam (embedder, LLM planner, tool backend) is an interface that
can be swapped for a production implementation without touching the
orchestration in `agent.py`.
"""

from agentic.agent import Agent
from agentic.state import SessionStore

__all__ = ["Agent", "SessionStore"]

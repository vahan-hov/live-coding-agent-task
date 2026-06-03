"""Shared fixtures. The whole suite runs offline: RAG uses the deterministic
HashingEmbedder (no model download) against a real in-memory Qdrant store.
"""

from __future__ import annotations

import pytest

from agentic.agent import Agent
from agentic.embeddings import HashingEmbedder
from agentic.models import ToolName
from agentic.tools.rag import RAGTool
from agentic.tools.web_search import WebSearchTool


@pytest.fixture
def rag_tool() -> RAGTool:
    return RAGTool(embedder=HashingEmbedder())


@pytest.fixture
def agent(rag_tool: RAGTool) -> Agent:
    web = WebSearchTool(failure_rate=0.0)
    return Agent({ToolName.RAG: rag_tool, ToolName.WEB_SEARCH: web})

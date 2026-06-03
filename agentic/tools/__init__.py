"""Tool implementations and the shared tool base.

A `Tool` is any external capability the agent can call. The base class wraps
every invocation in a circuit breaker plus retry/backoff/timeout, so concrete
tools only implement their happy-path `_run`.
"""

from agentic.tools.base import Tool, ToolError, ToolUnavailable
from agentic.tools.rag import RAGTool
from agentic.tools.web_search import WebSearchTool

__all__ = ["Tool", "ToolError", "ToolUnavailable", "RAGTool", "WebSearchTool"]

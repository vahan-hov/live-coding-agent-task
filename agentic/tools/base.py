"""The `Tool` base class: resilience wrapped around a simple `_run`.

Every tool invocation goes through `call()`, which layers, from outside in:

    circuit breaker  ->  retry with exponential backoff  ->  timeout  ->  _run

Concrete tools implement only `_run` (the happy path). They never raise to the
agent: `call()` always returns a `ToolResult`, turning failures and open
breakers into a `degraded` result so the agent can still answer partially.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from agentic.circuit_breaker import CircuitBreaker, CircuitOpenError
from agentic.models import Evidence, ToolName, ToolResult

logger = logging.getLogger("agentic.tools")


class ToolError(RuntimeError):
    """A recoverable failure inside a tool's `_run` (will be retried)."""


class ToolUnavailable(RuntimeError):
    """The tool's dependency is down/unreachable (will be retried)."""


class Tool(ABC):
    """Base class providing breaker + retry + timeout around `_run`."""

    name: ToolName

    def __init__(
        self,
        *,
        max_retries: int = 2,
        base_backoff: float = 0.05,
        timeout_seconds: float = 5.0,
        breaker: CircuitBreaker | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.max_retries = max_retries
        self.base_backoff = base_backoff
        self.timeout_seconds = timeout_seconds
        self._sleep = sleep
        self.breaker = breaker or CircuitBreaker(self.name.value)
        # A dedicated single-thread pool lets us enforce a wall-clock timeout
        # on _run even when the underlying call is blocking.
        self._pool = ThreadPoolExecutor(max_workers=1)

    @abstractmethod
    def _run(self, query: str, *, top_k: int) -> list[Evidence]:
        """Do the actual work. Raise on failure; return evidence on success."""

    def _run_with_timeout(self, query: str, top_k: int) -> list[Evidence]:
        future = self._pool.submit(self._run, query, top_k=top_k)
        try:
            return future.result(timeout=self.timeout_seconds)
        except FutureTimeout as exc:
            # Best-effort cancel only: a thread already executing _run cannot be
            # interrupted in Python, so the worker keeps running in the
            # background until it finishes on its own. We give up waiting and
            # surface a timeout; a production tool would instead pass the deadline
            # into a cancellable client (e.g. an HTTP request timeout).
            future.cancel()
            raise ToolUnavailable(
                f"{self.name.value} timed out after {self.timeout_seconds}s"
            ) from exc

    def close(self) -> None:
        """Release the timeout worker pool. Idempotent."""
        self._pool.shutdown(wait=False, cancel_futures=True)

    def __del__(self) -> None:  # pragma: no cover - best-effort cleanup
        # Safety net so a discarded tool doesn't leak its worker thread; explicit
        # close() is preferred.
        try:
            self.close()
        except Exception:
            pass

    def call(self, query: str, *, top_k: int = 5) -> ToolResult:
        """Invoke the tool resiliently. Never raises; returns a `ToolResult`."""
        # Fail fast if the breaker is open: degrade instead of calling downstream.
        if not self.breaker.allow():
            logger.warning("%s breaker OPEN -> degrading", self.name.value)
            return ToolResult(
                tool=self.name,
                ok=False,
                degraded=True,
                error=f"{self.name.value} unavailable (circuit open)",
            )

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                evidence = self.breaker.call(
                    lambda: self._run_with_timeout(query, top_k)
                )
                return ToolResult(tool=self.name, ok=True, evidence=list(evidence))
            except CircuitOpenError as exc:
                # Breaker tripped mid-loop (e.g. retries exhausted its threshold).
                logger.warning("%s tripped open during call: %s", self.name.value, exc)
                return ToolResult(
                    tool=self.name, ok=False, degraded=True, error=str(exc)
                )
            except Exception as exc:  # noqa: BLE001 - tools must not leak exceptions
                last_error = exc
                if attempt < self.max_retries:
                    backoff = self.base_backoff * (2**attempt)
                    logger.info(
                        "%s attempt %d failed (%s); retrying in %.3fs",
                        self.name.value,
                        attempt + 1,
                        exc,
                        backoff,
                    )
                    self._sleep(backoff)

        logger.error("%s exhausted retries: %s", self.name.value, last_error)
        return ToolResult(
            tool=self.name,
            ok=False,
            error=f"{self.name.value} failed: {last_error}",
        )

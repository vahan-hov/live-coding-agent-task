"""A classic three-state circuit breaker.

States:
    CLOSED    -> calls flow through. Consecutive failures are counted; once they
                 reach `failure_threshold` the breaker trips to OPEN.
    OPEN      -> calls are rejected immediately (fail fast) without touching the
                 downstream dependency. After `cooldown_seconds` the breaker
                 moves to HALF_OPEN to probe recovery.
    HALF_OPEN -> a limited number of trial calls are allowed. `success_threshold`
                 consecutive successes close the breaker; any failure re-opens it.

This protects the agent from hammering a flaky dependency and lets it degrade
gracefully (see `tools/base.py`). The breaker is deliberately framework-free and
uses an injectable clock so its time-based transitions are unit-testable.

Thread safety: all access to the breaker's mutable state is guarded by a single
re-entrant lock, so a breaker instance can be shared across the worker threads
that concurrent tool calls run on. An ``RLock`` (rather than a plain ``Lock``)
lets the public methods compose — e.g. ``allow()`` reads ``state``, and the
recorders call ``_maybe_half_open()`` — without deadlocking on re-entry.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from enum import Enum


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """Raised when a call is attempted while the breaker is OPEN."""

    def __init__(self, name: str, retry_after: float) -> None:
        super().__init__(
            f"circuit '{name}' is OPEN; retry after ~{retry_after:.1f}s"
        )
        self.name = name
        self.retry_after = retry_after


class CircuitBreaker:
    """Guards a single dependency. Safe to share across threads."""

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 3,
        success_threshold: int = 1,
        cooldown_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock

        # Guards every read/write of the mutable fields below. Re-entrant so the
        # public methods can call one another while holding it.
        self._lock = threading.RLock()
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._successes = 0
        self._opened_at: float | None = None

    # --- introspection -----------------------------------------------------

    @property
    def state(self) -> CircuitState:
        """Current state, accounting for an elapsed cooldown (OPEN -> HALF_OPEN).

        Reading the state lazily promotes OPEN -> HALF_OPEN once the cooldown has
        elapsed, so callers (and the gate below) see the breaker is ready to
        probe without needing a separate tick. This is the one place that
        transition happens; the recorders below route through it deliberately.
        """
        with self._lock:
            self._maybe_half_open()
            return self._state

    def _maybe_half_open(self) -> None:
        # Caller must hold self._lock.
        if self._state is CircuitState.OPEN and self._cooldown_elapsed():
            self._to_half_open()

    def _cooldown_elapsed(self) -> bool:
        return (
            self._opened_at is not None
            and (self._clock() - self._opened_at) >= self.cooldown_seconds
        )

    def _retry_after(self) -> float:
        if self._opened_at is None:
            return 0.0
        return max(0.0, self.cooldown_seconds - (self._clock() - self._opened_at))

    # --- gate --------------------------------------------------------------

    def allow(self) -> bool:
        """Whether a call may proceed right now (drives fail-fast in the caller)."""
        return self.state is not CircuitState.OPEN

    def call(self, fn: Callable[[], object]) -> object:
        """Run `fn` through the breaker, updating state on success/failure.

        Raises `CircuitOpenError` if the breaker is OPEN, or re-raises whatever
        `fn` raised after recording the failure.
        """
        # Only the gate check and the bookkeeping are locked; `fn` runs *outside*
        # the lock so concurrent calls to a healthy dependency aren't serialized.
        with self._lock:
            self._maybe_half_open()
            if self._state is CircuitState.OPEN:
                raise CircuitOpenError(self.name, self._retry_after())
        try:
            result = fn()
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result

    # --- transitions -------------------------------------------------------

    def record_success(self) -> None:
        with self._lock:
            # Promote a cooled-down breaker before deciding, so a success during
            # the probe window is treated as a HALF_OPEN trial rather than a no-op.
            self._maybe_half_open()
            if self._state is CircuitState.HALF_OPEN:
                self._successes += 1
                if self._successes >= self.success_threshold:
                    self._reset()
            else:
                self._failures = 0

    def record_failure(self) -> None:
        with self._lock:
            self._maybe_half_open()
            # A failure during a half-open probe immediately re-opens the breaker.
            if self._state is CircuitState.HALF_OPEN:
                self._trip()
                return
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._trip()

    # The transition helpers below all mutate shared state and assume the
    # caller already holds self._lock.

    def _trip(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = self._clock()
        self._successes = 0

    def _to_half_open(self) -> None:
        self._state = CircuitState.HALF_OPEN
        self._successes = 0
        self._failures = 0

    def _reset(self) -> None:
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._successes = 0
        self._opened_at = None

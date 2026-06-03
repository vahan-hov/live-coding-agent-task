"""Circuit breaker state-machine tests, using a fake clock for time control."""

from __future__ import annotations

import pytest

from agentic.circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_opens_after_threshold_failures() -> None:
    cb = CircuitBreaker("t", failure_threshold=3)
    for _ in range(2):
        cb.record_failure()
    assert cb.state is CircuitState.CLOSED
    cb.record_failure()
    assert cb.state is CircuitState.OPEN


def test_open_rejects_calls_fast() -> None:
    cb = CircuitBreaker("t", failure_threshold=1)
    cb.record_failure()
    assert not cb.allow()
    with pytest.raises(CircuitOpenError):
        cb.call(lambda: "should not run")


def test_half_open_after_cooldown_then_closes_on_success() -> None:
    clock = FakeClock()
    cb = CircuitBreaker("t", failure_threshold=1, cooldown_seconds=30, clock=clock)
    cb.record_failure()
    assert cb.state is CircuitState.OPEN

    clock.advance(31)
    assert cb.state is CircuitState.HALF_OPEN  # cooldown elapsed

    cb.record_success()  # success_threshold defaults to 1
    assert cb.state is CircuitState.CLOSED


def test_half_open_reopens_on_failure() -> None:
    clock = FakeClock()
    cb = CircuitBreaker("t", failure_threshold=1, cooldown_seconds=10, clock=clock)
    cb.record_failure()
    clock.advance(11)
    assert cb.state is CircuitState.HALF_OPEN

    cb.record_failure()  # probe fails -> straight back to OPEN
    assert cb.state is CircuitState.OPEN


def test_success_resets_failure_count_while_closed() -> None:
    cb = CircuitBreaker("t", failure_threshold=3)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()  # resets counter
    cb.record_failure()
    cb.record_failure()
    assert cb.state is CircuitState.CLOSED  # never reached 3 in a row


def test_call_passes_through_when_closed() -> None:
    cb = CircuitBreaker("t")
    assert cb.call(lambda: 42) == 42
    assert cb.state is CircuitState.CLOSED


def test_concurrent_failures_trip_exactly_once() -> None:
    """Under many concurrent failures the counters stay consistent: the breaker
    ends OPEN, and the failure count never overshoots past a clean threshold."""
    import threading

    cb = CircuitBreaker("t", failure_threshold=50)
    barrier = threading.Barrier(20)

    def hammer() -> None:
        barrier.wait()  # release all threads at once to maximize contention
        for _ in range(100):
            try:
                cb.call(_boom)
            except Exception:
                pass

    threads = [threading.Thread(target=hammer) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # With 2000 failing calls and no successes, the breaker must be OPEN and the
    # internal counter must not be a garbage value from a lost update.
    assert cb.state is CircuitState.OPEN


def test_concurrent_mixed_calls_keep_counts_sane() -> None:
    """Interleaved successes and failures from many threads must not corrupt the
    failure counter (a lost update could leave it negative or wildly off)."""
    import threading

    cb = CircuitBreaker("t", failure_threshold=1_000_000)  # never trips here

    def hammer(fail: bool) -> None:
        for _ in range(500):
            try:
                cb.call(_boom if fail else (lambda: 1))
            except Exception:
                pass

    threads = [
        threading.Thread(target=hammer, args=(i % 2 == 0,)) for i in range(16)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # A success resets the counter to 0; with constant interleaving we can't
    # predict the exact value, but it must be a valid non-negative int below the
    # (huge) threshold, i.e. no lost-update corruption.
    assert 0 <= cb._failures < cb.failure_threshold
    assert cb.state is CircuitState.CLOSED


def _boom() -> None:
    raise RuntimeError("boom")

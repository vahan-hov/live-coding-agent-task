"""Conversation-state tests, including concurrent session access."""

from __future__ import annotations

import threading

from agentic.state import SessionStore


def test_get_is_idempotent_for_a_session() -> None:
    store = SessionStore()
    a = store.get("s1")
    b = store.get("s1")
    assert a is b  # same session id -> same state object


def test_concurrent_get_returns_one_shared_state() -> None:
    """Many threads racing on the first `get` for the same id must all receive
    the *same* state object — otherwise an early turn's history gets clobbered by
    a state created concurrently."""
    store = SessionStore()
    barrier = threading.Barrier(32)
    seen: list[object] = []
    seen_lock = threading.Lock()

    def grab() -> None:
        barrier.wait()
        state = store.get("shared")
        with seen_lock:
            seen.append(state)

    threads = [threading.Thread(target=grab) for _ in range(32)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(seen) == 32
    first = seen[0]
    assert all(s is first for s in seen)

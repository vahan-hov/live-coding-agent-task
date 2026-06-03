"""Multi-turn conversation state.

`ConversationState` holds the message history for one session and exposes the
context the planner/synthesizer need (e.g. the last user message, for resolving
follow-ups). `SessionStore` maps session ids to their state — the seam where a
real system would swap the in-memory dict for Redis or a database.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Literal

Role = Literal["user", "assistant"]


@dataclass
class Turn:
    role: Role
    content: str


@dataclass
class ConversationState:
    """Ordered history for a single conversation."""

    session_id: str
    history: list[Turn] = field(default_factory=list)

    def add_user(self, content: str) -> None:
        self.history.append(Turn("user", content))

    def add_assistant(self, content: str) -> None:
        self.history.append(Turn("assistant", content))

    @property
    def last_user_message(self) -> str | None:
        """The most recent user message *before* the current turn, if any."""
        for turn in reversed(self.history):
            if turn.role == "user":
                return turn.content
        return None

    def recent(self, n: int = 6) -> list[Turn]:
        """The last `n` turns, for compact context windows."""
        return self.history[-n:]


class SessionStore:
    """In-memory registry of conversations keyed by session id.

    The store is safe for concurrent access across sessions: a lock guards the
    lazy get-or-create so two simultaneous first turns for the same session can't
    each create (and clobber) a separate state. Mutation of an individual
    `ConversationState`'s history is the caller's concern — turns within a single
    session are expected to be ordered, not interleaved.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, ConversationState] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> ConversationState:
        """Fetch (or lazily create) the state for a session."""
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                state = ConversationState(session_id=session_id)
                self._sessions[session_id] = state
            return state

    def reset(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

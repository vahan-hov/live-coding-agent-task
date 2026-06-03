# Agentic RAG System

A small, well-factored agentic system that answers questions by deciding which
tools to use, retrieving from internal documents (real RAG over a vector store)
and optionally the web, then synthesizing a **cited** answer — with multi-turn
conversation state and production-style resilience (circuit breakers, retries,
timeouts, graceful degradation).

## What it does, per turn

```
analyze query ──▶ plan tools ──▶ execute tools ──▶ synthesize cited answer ──▶ persist state
   (planner)      (ToolPlan)     (breaker+retry      (inline [n] + sources)     (session store)
                                   +timeout)
```

1. **Analyze the query** to determine which tools are needed.
2. **RAG tool** — semantic search over internal docs (real pipeline).
3. **Web search tool** — called *only* when the query signals recency/external info.
4. **Synthesize** a final answer with numbered citations back to each source.
5. **State management** — per-session history; terse follow-ups are resolved
   against the prior turn.
6. **Circuit breakers + error handling** — every tool call is wrapped in a
   3-state breaker, retries with backoff, and a timeout; an unavailable tool
   degrades gracefully instead of failing the request.

## The RAG pipeline is real

The RAG tool is not a keyword stub. It runs an actual retrieval pipeline:

- **Vector store:** [Qdrant](https://qdrant.tech) in embedded `:memory:` mode
  (no server/Docker). Point it at a real cluster by passing a configured
  `QdrantClient` — nothing else changes.
- **Embeddings:** `sentence-transformers` (`all-MiniLM-L6-v2`), running locally
  on CPU.
- **Flow:** load `agentic/corpus/*.md` → chunk (overlapping windows) → embed →
  upsert → at query time embed the query → cosine ANN search → top-k chunks with
  scores and source metadata.

Everything else (planner, web tool, synthesizer) is a deterministic mock so the
demo and tests are reproducible and run offline — but each is hidden behind an
interface, so swapping in an LLM planner or a real search API doesn't touch the
orchestration in [`agentic/agent.py`](agentic/agent.py).

## Two orchestrations, one set of components

The pipeline is wired up two ways so you can compare the styles directly — both
drive the *same* planner, resilient tools, and synthesizer:

| Orchestration | File | Run | State |
|---|---|---|---|
| Hand-rolled loop | [`agentic/agent.py`](agentic/agent.py) | `python main.py` | `SessionStore` |
| **LangGraph `StateGraph`** | [`agentic/graph.py`](agentic/graph.py) | `python main_graph.py` | `MemorySaver` checkpointer |

The LangGraph version expresses the plan→tool→synthesize flow as a graph with a
**conditional edge** that decides whether to take the optional web-search branch,
and uses a **checkpointer** (keyed by `thread_id`) for multi-turn memory — the
framework-native equivalent of the hand-rolled `SessionStore`. Notably,
resilience (circuit breaker / retry / timeout) lives *inside the tools*, so it
applies unchanged in both orchestrations: it's a property of the dependency, not
of the orchestrator.

## Setup & run

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

> **First run** downloads the embedding model (~80 MB) from the Hugging Face Hub
> and caches it locally; **subsequent runs work offline**. No API keys required.

## Tests

```powershell
pytest -q
```

The suite runs fully offline and needs no model download: it injects a
deterministic `HashingEmbedder` into the *same* Qdrant pipeline, so real
vector-store retrieval mechanics are exercised without the 80 MB model.

## Layout

| File | Responsibility |
|------|----------------|
| [`agentic/agent.py`](agentic/agent.py) | Hand-rolled orchestration of a turn; records a decision trace |
| [`agentic/graph.py`](agentic/graph.py) | LangGraph `StateGraph` orchestration (same components, conditional edge + checkpointer) |
| [`agentic/planner.py`](agentic/planner.py) | Query analysis → `ToolPlan` (which tools + rationale) |
| [`agentic/tools/rag.py`](agentic/tools/rag.py) | Real RAG over Qdrant + embeddings |
| [`agentic/tools/web_search.py`](agentic/tools/web_search.py) | Mock external search (intentionally flaky) |
| [`agentic/tools/base.py`](agentic/tools/base.py) | `Tool` base: breaker + retry + timeout around `_run` |
| [`agentic/circuit_breaker.py`](agentic/circuit_breaker.py) | 3-state breaker (CLOSED/OPEN/HALF_OPEN) |
| [`agentic/embeddings.py`](agentic/embeddings.py) | `Embedder` protocol + sentence-transformers / hashing impls |
| [`agentic/synthesizer.py`](agentic/synthesizer.py) | Cited-answer assembly |
| [`agentic/state.py`](agentic/state.py) | Per-session conversation state |
| [`agentic/models.py`](agentic/models.py) | Shared dataclasses (the pipeline's vocabulary) |

## Design notes

- **Tool abstraction** — adding a tool is subclassing `Tool` and implementing
  `_run`; resilience is inherited from the base class (open/closed principle).
- **Graceful degradation** — an open breaker on web search still yields an
  answer from RAG, flagged as partial.
- **Thread safety** — the circuit breaker and session store are guarded by locks
  so a single agent can serve concurrent turns; breaker bookkeeping is locked but
  the downstream tool call runs *outside* the lock, so healthy calls aren't
  serialized.
- **Observability** — each response carries a `trace` of planner decisions, tool
  outcomes, and breaker states.
- **Determinism** — mock seams + in-memory store make the demo and tests
  repeatable, with no network flakiness.

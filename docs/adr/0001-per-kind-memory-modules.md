# 0001 — Each scoped memory kind is one module behind Renders / Folds

- Status: Accepted
- Date: 2026-06-08
- Issue: #4 (Make each memory kind one deep module)
- Builds on: #1 (file-shape adapters), #2 (single summarize fold)

## Context

A memory **kind** — long-term, episode, session, persona — is smeared across
three files: its IO (`core/memory/store.py` + `adapters.py`), its render +
fold-threshold (`core/memory/manager.py`), and its fold prompt
(`agent/summarizer.py`). Changing one kind means editing three files in lockstep,
and `MemoryManager` knows every kind's file layout (`_long_term_section`,
`_short_term_section`, `_episodes_section`, the `maybe_summarize_*` pair, the
retriever-fallback repeated per kind, the `build_context` order).

The kinds are **not symmetric**:

- **session** and **long-term** *fold* (count-vs-threshold gate + an injected
  summarizer); **episodes** and **persona** do not.
- an **episode** is *written by* session close (the rolling session summary
  becomes a global episode), not by an episode-owned policy.
- **persona** is **global** — not scoped to a user/session — and never folds or
  retrieves. Every other kind is scoped.

A single uniform `write / render / maybe_fold` base would make episodes/persona
wear a no-op `maybe_fold` and force one `write` signature across writes that
genuinely differ (a turn append evicts + buffers; a fact append also embeds).

## Decision

**Split protocols, not a uniform base.** Only the scoped kinds participate:

```python
class Renders(Protocol):
    section_header: str
    def render(self, mem: ScopedMemory, query: str | None) -> str: ...

class Folds(Protocol):
    async def maybe_fold(self, mem: ScopedMemory) -> str | None: ...
```

- `LongTerm(Renders, Folds)`, `Session(Renders, Folds)`, `Episodes(Renders)`.
- **Persona is its own thing** — global, not a `Renders` kind. The manager renders
  it directly as the first context section and owns `evolve_persona` + seed. It
  never takes the scoped `mem`, so it doesn't carry a parameter it ignores.
- **Writes are each kind's own named method** (`remember`, `record_episode`,
  `record_turn`) — different signatures, not faked into one. A kind's write owns
  its retriever `index(...)` (moved off the manager).

**Scope threads as one self-describing value.** `ScopedMemory` exposes `user_id`
and `session_id` (it is already built from them), so every kind method takes just
`mem`: `render(mem, query)`, `maybe_fold(mem)`, `write(mem, payload)`. Kinds are
**stateless across scopes** and never touch the `ContextVar`; the manager resolves
scope (via the `self.mem` property — never cached, see below) and passes `mem` in.
The only kind-held state is `LongTerm`'s fold marker, keyed by `user_id`.

**The manager owns section order + headers; kinds render only their body.**
`build_context` keeps an explicit ordered list — persona (global) first, then the
scoped kinds long-term / episodes / short-term — wrapping each non-empty body in
its header. No per-kind ordering field. Output is byte-for-byte unchanged.

**Close is the one cross-kind lifecycle, orchestrated by the manager.**
`Session.close(mem) -> (dropped, summary | None)` wipes the live window + summary
+ pending and returns the rolling summary (if any) to carry forward; the manager
hands that to `Episodes.record_episode(mem, summary)`. With no summarizer
configured there is no summary — `close` returns `None`, no episode is written,
the window is still wiped, and `dropped` counts the cleared turns. The manager
touches no session files.

**Two invariants stay in exactly one place each** — shared helpers in `kinds.py`:

- `guarded_fold(fn, payload, write_back, label)` — the "summarization must never
  break a chat" try/except (#2). Both `Folds` kinds call it; it is not re-spread.
- the retriever-fallback ("if retriever and query: search this kind's key; else
  whole-file") — one helper that `LongTerm.render` and `Episodes.render` share.

**The fold prompt stays in the agent layer (deviation from the issue text).**
Issue #4 lists the fold prompt as part of the smear, but `core/memory` is
**model-free**: summarizers are injected `SummarizeFn` callables built in
`agent/summarizer.py` because they need a model. So a fold splits — *policy*
(threshold, marker, payload shape, write-back) lives in the kind; *prompt + model
call* stays injected. Moving the prompt into `kinds.py` would drag agno/model
imports into core and break the injection seam #2 depends on. "Owns its fold
policy" is the real acceptance, not "owns its fold prompt."

**Layout & surface.** All protocols, the four kinds' logic, and the two helpers
live in one `core/memory/kinds.py` (locality over fragmentation at this size).
Kinds are **not exported** from `core/memory/__init__.py`; the `MemoryManager`
public API (consumed by `core/conversation.py` + `agent/tools/memory.py`) is
frozen — every method keeps its name/signature and becomes a thin delegator.

## Consequences

- Each scoped kind owns its IO wiring, render (+ search fallback), write (+ index),
  and — if foldable — its threshold, marker, and payload. `MemoryManager` shrinks
  to scope resolution, persona, the ordered section assembly, the close lifecycle,
  and delegation; it stops referencing storage internals.
- `tests/test_memory.py` is the behavioral net (API frozen, so it barely moves);
  each kind also gets tests through its protocol. `build_context` ordering and the
  `!ctx` numbers are unchanged.
- Risk: indirection for the shallow `Episodes` kind. Accepted — it stays a
  one-render module and is not forced to implement `Folds`.

## Concurrency note (do not regress)

`MemoryManager` is a single shared instance across all Discord sessions; scope is
a process-global `ContextVar`. `self.mem` (the `ScopedMemory` bundle) MUST stay a
per-access property rebuilt from the current scope — never cached on the instance,
or two interleaved async sessions leak each other's files.

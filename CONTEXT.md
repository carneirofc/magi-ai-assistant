# Deliberate Memory

The chatbot's memory: durable, inspectable files the model reads and writes on
purpose — never auto-extracted by the framework. One layer assembles these into
the context block shown to the model each turn.

## Language

**Kind**:
One unit of memory with its own storage, render, and (sometimes) fold: long-term,
episode, session, persona. The thing #4 makes into one module.
_Avoid_: type, category.

**Long-term**:
Durable facts the model chooses to keep about a user, accumulated over time.

**Episode**:
One recorded past interaction — the gist of a whole conversation. **Episodes** is
the kind / its context section. Retriever key: `episode`.
_Avoid_: episodic (reserve for the filename `episodic.md`).

**Session**:
The kind holding the live conversation: a capped window of recent turns plus a
rolling summary of turns that rolled out of it. Renders into the **short-term**
section.
_Avoid_: short-term as the kind name (it is only the section header).

**Persona**:
The global personality + evolved behavioral adjustments. The one **global** kind
(not scoped to a user or session); every other kind is scoped.

**Section**:
What a kind renders into the assembled context block, under its header. One kind
→ at most one section. Ordering and headers are owned by the assembler, not the
kind.

**Fold**:
Compressing overflow into compact form: session turns → a rolling summary,
accumulated long-term facts → a condensed profile. Only session and long-term
fold; episodes and persona do not. Long-term folding is the no-curator path —
when the **curator** is on it owns the profile and this fold never fires.
_Avoid_: summarize (the model call is one step of a fold), compact.

**Curate**:
The post-turn pass that owns durable memory: it reads the finished turn against
the current durable facts (each tagged with an id) + persona and revises the
fact sheet PER FACT — ADD / UPDATE / DELETE / NOOP — so it can update/supersede,
not just append, without re-emitting the whole profile each turn. Optionally
records an episode or evolves the persona. Runs off the reply path on a cheap
model; replaces the lead's old inline write tools and the long-term fold.
Model-free `magi/core/memory` calls it as an injected `CurateFn` (`magi/agent/curator.py`),
the same seam the summarizers use.
_Avoid_: remember (the retired append-only lead tool).

**Live window**:
The capped, JSON list of the session's most recent turns.

**Pending buffer**:
Turns evicted from the live window, held until the next session fold consumes them.

**Scope**:
The (user, session) a memory operation belongs to. Set once per message, read via
a process-global ContextVar — never threaded as a tool argument.

**Skill** (`magi/agent/skills`):
One registrable capability unit: what the assistant *knows* (a prompt fragment,
overlay-resolved at `skills/<name>.md` with the manifest's inline default as
fallback) plus what it *can do* (lead tools / a memory-injected lead toolkit /
member tools), behind one `enabled` gate. Registered via `register_skill` at the
entrypoint, composed at team build. Skill prompts are evolution-proposable by
default (per-manifest opt-out).
_Avoid_: using "skill" loosely for a single tool (a tool is one callable; a
skill bundles prompt + tools + gate).

**Knowledge** (`magi/core/knowledge`):
A global, read-only RAG corpus the agent searches via the `search_knowledge` tool
— distinct from **memory**: memory is per-user and conversation-derived (the
curator owns it); knowledge is a shared *document* corpus, chunked + embedded
*faithfully* (no LLM extraction, so retrieval returns source text) into its own
Qdrant collection. Reuses the shared proxy embedder (`magi/core/embeddings`) and the
Qdrant endpoint. Populated out-of-band (`scripts/ingest_knowledge.py`); gated by
`knowledge_enabled`; degrades to no tool when off / Qdrant down. Chunks carry a
`scope` field — `"global"` for the shared corpus, `user:<id>` for one user's own
knowledge (saved via `save_knowledge(personal=True)`). Search and auto-injection
span global + the current user's scope, resolved from the ambient memory scope —
never a tool argument, so cross-user leakage is impossible by construction.
_Avoid_: conflating with memory (different lifecycle, different store).

"""Deliberate, filesystem-backed memory for the model.

Public surface: the `MemoryManager`, the `MemoryScope`, and the factory helpers
`build_memory` / `build_memory_from_config`. The composition root builds one
manager and injects it everywhere it's needed (conversation service + memory
tools) — there is no module-level singleton. The underlying `FileMemoryStore`
stays internal: callers depend on the manager, not the storage layout.

Dependencies are built by these factories and injected — never constructed inside
`MemoryManager.__init__`.
"""

from pathlib import Path
from typing import Optional

from agno.utils.log import log_info

from core.config import config
from core.memory.curation import CurateFn, CurationInput, CurationResult
from core.memory.manager import MemoryManager, MemoryScope, SummarizeFn
from core.memory.semantic import MemoryRetriever, build_semantic_index
from core.memory.store import FileMemoryStore

__all__ = [
    "MemoryManager",
    "MemoryScope",
    "CurateFn",
    "CurationInput",
    "CurationResult",
    "build_memory",
    "build_memory_from_config",
]


def build_memory(
    store: FileMemoryStore,
    *,
    short_term_max: int,
    persona_seed: str = "",
    summarize_session_fn: Optional[SummarizeFn] = None,
    summarize_every: int = 10,
    long_term_recent_raw: int = 5,
    retriever: Optional[MemoryRetriever] = None,
    semantic_top_k: int = 5,
    short_term_turn_max_chars: int = 4_000,
    session_pending_max: int = 30,
    session_summary_max_chars: int = 4_000,
    curate_fn: Optional[CurateFn] = None,
    long_term_summary_max_chars: int = 8_000,
) -> MemoryManager:
    """Assemble a `MemoryManager` from already-built dependencies."""
    return MemoryManager(
        store=store,
        short_term_max=short_term_max,
        persona_seed=persona_seed,
        summarize_session_fn=summarize_session_fn,
        summarize_every=summarize_every,
        long_term_recent_raw=long_term_recent_raw,
        retriever=retriever,
        semantic_top_k=semantic_top_k,
        short_term_turn_max_chars=short_term_turn_max_chars,
        session_pending_max=session_pending_max,
        session_summary_max_chars=session_summary_max_chars,
        curate_fn=curate_fn,
        long_term_summary_max_chars=long_term_summary_max_chars,
    )


def build_memory_from_config(
    *,
    summarize_session_fn: Optional[SummarizeFn] = None,
    curate_fn: Optional[CurateFn] = None,
) -> MemoryManager:
    """Build the manager wired from `config`. The summarizers and the curator are
    injected by the caller (they need a model; core stays model-free)."""
    root = Path(config.memory_dir)
    log_info(
        f"memory: FileMemoryStore at {root.resolve()}, "
        f"short_term_max={config.short_term_max}, persona_seed={len(config.persona_seed)} chars"
    )
    return build_memory(
        store=FileMemoryStore(root),
        short_term_max=config.short_term_max,
        persona_seed=config.persona_seed,
        summarize_session_fn=summarize_session_fn,
        summarize_every=config.summarize_every,
        long_term_recent_raw=config.long_term_recent_raw,
        retriever=build_semantic_index(),  # None unless SEMANTIC_MEMORY is on
        semantic_top_k=config.semantic_top_k,
        short_term_turn_max_chars=config.short_term_turn_max_chars,
        session_pending_max=config.session_pending_max,
        session_summary_max_chars=config.session_summary_max_chars,
        curate_fn=curate_fn,
        long_term_summary_max_chars=config.long_term_summary_max_chars,
    )

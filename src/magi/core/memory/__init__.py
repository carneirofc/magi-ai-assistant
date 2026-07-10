"""Deliberate, filesystem-backed memory for the model.

Public surface: the `MemoryManager`, the `MemoryScope`, and the factory helpers
`build_memory` / `build_memory_from_config`. The composition root builds one
manager and injects it everywhere it's needed (conversation service + memory
tools) — there is no module-level singleton. The underlying `FileMemoryStore`
stays internal: callers depend on the manager, not the storage layout.

Dependencies are built by these factories and injected — never constructed inside
`MemoryManager.__init__`.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agno.utils.log import log_info

from magi.core.config import config
from magi.core.items import ItemArchive, build_item_archive_from_config
from magi.core.memory.admin import (
    InvalidRawJsonError,
    MemoryManagerRequiredError,
    MemoryAdmin,
    SessionRequiredError,
    StaleVersionError,
    TriggerUnavailableError,
    UnknownMemoryFileKindError,
    UserRequiredError,
)
from magi.core.memory.curation import CurateFn, CurationInput, CurationResult, FactOp
from magi.core.memory.git_backend import build_memory_git
from magi.core.memory.manager import MemoryManager, MemoryScope, SummarizeFn
from magi.core.memory.semantic import MemoryRetriever, build_semantic_index
from magi.core.memory.store import FileMemoryStore
from magi.core.settings import MemoryOverrides, OperatorSettingsStore

__all__ = [
    "MemoryManager",
    "MemoryScope",
    "CurateFn",
    "CurationInput",
    "CurationResult",
    "FactOp",
    "MemoryAdmin",
    "MemoryManagerRequiredError",
    "StaleVersionError",
    "TriggerUnavailableError",
    "UnknownMemoryFileKindError",
    "UserRequiredError",
    "SessionRequiredError",
    "InvalidRawJsonError",
    "EffectiveMemorySettings",
    "resolve_memory_settings",
    "operator_settings_store",
    "build_memory",
    "build_memory_from_config",
]


@dataclass(frozen=True)
class EffectiveMemorySettings:
    """The memory settings actually in force: operator overrides overlaid on the code
    `config` defaults. `memory_dir` is `~`-expanded so the store/git backend get a
    real path; `raw_memory_dir` keeps what the operator typed (for round-tripping in
    the admin editor)."""

    memory_dir: str
    raw_memory_dir: str
    git_enabled: bool
    git_author_name: str
    git_author_email: str


def resolve_memory_settings(overrides: MemoryOverrides) -> EffectiveMemorySettings:
    """Overlay operator `overrides` on the `config` defaults — the single place the
    two layers combine, shared by the factory (what to build) and the admin API (what
    to show). A `None` override inherits the code default."""
    raw_dir = overrides.memory_dir or config.memory_dir
    return EffectiveMemorySettings(
        memory_dir=os.path.expanduser(raw_dir),
        raw_memory_dir=raw_dir,
        git_enabled=config.memory_git_enabled if overrides.git_enabled is None else overrides.git_enabled,
        git_author_name=overrides.git_author_name or config.memory_git_author_name,
        git_author_email=overrides.git_author_email or config.memory_git_author_email,
    )


def operator_settings_store() -> OperatorSettingsStore:
    """The operator settings store at the configured path (persisted memory overrides)."""
    return OperatorSettingsStore(Path(config.operator_settings_path))


def build_memory(
    store: FileMemoryStore,
    *,
    short_term_max: int,
    persona_seed: str = "",
    persona_adjustments_max: int = 0,
    summarize_session_fn: Optional[SummarizeFn] = None,
    summarize_every: int = 10,
    long_term_recent_raw: int = 5,
    retriever: Optional[MemoryRetriever] = None,
    semantic_top_k: int = 5,
    short_term_turn_max_chars: int = 4_000,
    session_pending_max: int = 30,
    session_summary_max_chars: int = 4_000,
    curate_fn: Optional[CurateFn] = None,
    long_term_fact_max_chars: int = 1_000,
    long_term_facts_max: int = 200,
    archive: Optional[ItemArchive] = None,
) -> MemoryManager:
    """Assemble a `MemoryManager` from already-built dependencies."""
    return MemoryManager(
        store=store,
        short_term_max=short_term_max,
        persona_seed=persona_seed,
        persona_adjustments_max=persona_adjustments_max,
        summarize_session_fn=summarize_session_fn,
        summarize_every=summarize_every,
        long_term_recent_raw=long_term_recent_raw,
        retriever=retriever,
        semantic_top_k=semantic_top_k,
        short_term_turn_max_chars=short_term_turn_max_chars,
        session_pending_max=session_pending_max,
        session_summary_max_chars=session_summary_max_chars,
        curate_fn=curate_fn,
        long_term_fact_max_chars=long_term_fact_max_chars,
        long_term_facts_max=long_term_facts_max,
        archive=archive,
    )


def build_memory_from_config(
    *,
    summarize_session_fn: Optional[SummarizeFn] = None,
    curate_fn: Optional[CurateFn] = None,
) -> MemoryManager:
    """Build the manager wired from `config`, with operator overrides (memory location
    + git-versioning, from magi/core/settings) overlaid on top. The summarizers and the
    curator are injected by the caller (they need a model; core stays model-free)."""
    settings = resolve_memory_settings(operator_settings_store().read_memory())
    root = Path(settings.memory_dir)
    log_info(
        f"memory: FileMemoryStore at {root.resolve()}, "
        f"short_term_max={config.short_term_max}, persona_seed={len(config.persona_seed)} chars"
    )
    # Make the memory root a git repo and start committing every write (no-op unless
    # git-versioning is on + the `git` extra). Installed BEFORE the store is built so
    # the persona seed and any other startup writes are captured too. The write
    # observer it registers keeps the backend alive for the process; nothing else
    # needs to hold it. Effective (operator-overridden) git settings, not raw config.
    build_memory_git(
        root,
        enabled=settings.git_enabled,
        author_name=settings.git_author_name,
        author_email=settings.git_author_email,
    )
    return build_memory(
        store=FileMemoryStore(root),
        short_term_max=config.short_term_max,
        persona_seed=config.persona_seed,
        persona_adjustments_max=config.persona_adjustments_max,
        summarize_session_fn=summarize_session_fn,
        summarize_every=config.summarize_every,
        long_term_recent_raw=config.long_term_recent_raw,
        retriever=build_semantic_index(),  # None unless SEMANTIC_MEMORY is on
        semantic_top_k=config.semantic_top_k,
        short_term_turn_max_chars=config.short_term_turn_max_chars,
        session_pending_max=config.session_pending_max,
        session_summary_max_chars=config.session_summary_max_chars,
        curate_fn=curate_fn,
        long_term_fact_max_chars=config.long_term_fact_max_chars,
        long_term_facts_max=config.long_term_facts_max,
        archive=build_item_archive_from_config(),  # None unless items_archive_enabled
    )

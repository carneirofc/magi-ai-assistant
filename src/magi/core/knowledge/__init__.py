"""Knowledge layer — a global, read-only RAG corpus the agent retrieves from.

Distinct from memory: memory is per-user and conversation-derived (the curator
owns it); knowledge is a shared document corpus, ingested faithfully out-of-band
and queried at chat time via the `search_knowledge` tool. See `store.py` for the
memory-vs-knowledge contrast and the per-scope hook.

Public surface: the `KnowledgeStore`, the `KnowledgeSearcher` Protocol the tool
layer depends on, the `KnowledgeHit` result, and `build_knowledge_from_config`
(returns None when the feature is off). The composition root builds one store and
injects it into the knowledge tool — no module-level singleton.
"""

from magi.core.knowledge.chunking import chunk_text
from magi.core.knowledge.store import (
    GLOBAL_SCOPE,
    DocumentSummary,
    KnowledgeHit,
    KnowledgeSearcher,
    KnowledgeStore,
    build_knowledge_from_config,
)

__all__ = [
    "GLOBAL_SCOPE",
    "DocumentSummary",
    "KnowledgeHit",
    "KnowledgeSearcher",
    "KnowledgeStore",
    "build_knowledge_from_config",
    "chunk_text",
]

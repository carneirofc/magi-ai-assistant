"""Deliberate, filesystem-backed memory for the model.

Public surface: the `MemoryManager` (and its singleton `get_memory`) plus the
`MemoryScope`. The underlying `FileMemoryStore` stays internal — callers depend
on the manager, not the storage layout, so the backend can change later.
"""

from core.memory.manager import MemoryManager, MemoryScope, get_memory

__all__ = ["MemoryManager", "MemoryScope", "get_memory"]

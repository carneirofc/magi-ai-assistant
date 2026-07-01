"""The item archive — the shared "persist original + index" hook.

Public surface: `ItemArchive` (object-store bytes + a Qdrant vector for an item),
the `ItemHit` search result, the `GLOBAL_SCOPE` constant, and the
`build_item_archive_from_config` factory. See `archive.py` for the rationale and
how each item kind (knowledge / memory / files) wires into it.
"""

from magi.core.items.archive import (
    GLOBAL_SCOPE,
    ItemArchive,
    ItemHit,
    build_item_archive_from_config,
)

__all__ = [
    "GLOBAL_SCOPE",
    "ItemArchive",
    "ItemHit",
    "build_item_archive_from_config",
]

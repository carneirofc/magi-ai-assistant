"""Deterministic text chunking for the knowledge layer — pure, no IO, no model.

A knowledge document is split into overlapping character windows before
embedding. Unlike memory curation, nothing here calls an LLM: chunks are stored
*faithfully* so retrieval returns the source text, not a paraphrase. The split
prefers paragraph and sentence boundaries so a chunk rarely cuts mid-thought, but
falls back to a hard character cut so one giant unbroken blob can't defeat the cap.

Kept pure so it is exhaustively unit-testable without Qdrant or the proxy.
"""

import re

# Break points tried in order of preference: paragraph, then line, then sentence.
# Past these we hard-cut at the size limit so an unbroken blob still chunks.
_BOUNDARY = re.compile(r"\n\n|\n|(?<=[.!?])\s")


def _clean(text: str) -> str:
    """Normalise newlines and strip trailing whitespace per line; collapse blank runs."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _split_point(window: str, size: int) -> int:
    """The index to cut `window` at: the last boundary at/under `size`, else `size`."""
    best = 0
    for match in _BOUNDARY.finditer(window, 0, size):
        best = match.end()
    return best or min(size, len(window))


def chunk_text(text: str, *, size: int, overlap: int) -> list[str]:
    """Split `text` into chunks of ~`size` chars, each overlapping the previous by
    up to `overlap` chars. Returns [] for empty input. Boundaries prefer paragraph/
    line/sentence breaks; `overlap` is clamped below `size` so progress is guaranteed.
    """
    cleaned = _clean(text)
    if not cleaned:
        return []
    size = max(1, size)
    overlap = max(0, min(overlap, size - 1))

    chunks: list[str] = []
    start = 0
    n = len(cleaned)
    while start < n:
        window = cleaned[start:start + size]
        if start + size >= n:
            chunk = window.strip()
            if chunk:
                chunks.append(chunk)
            break
        cut = _split_point(window, size)
        chunk = cleaned[start:start + cut].strip()
        if chunk:
            chunks.append(chunk)
        # Advance past the chunk, then step back `overlap` for continuity. `max`
        # guarantees forward progress even if a boundary landed very early.
        start = max(start + cut - overlap, start + 1)
    return chunks

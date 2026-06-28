"""Ingest documents into the knowledge base (core/knowledge).

Operator tooling — run out-of-band to populate or refresh the global RAG corpus
the agent searches via the `search_knowledge` tool. Ingestion is an explicit
action, so it builds the store directly and does NOT check `knowledge_enabled`
(that flag only gates the chat-time tool). Re-ingesting a file replaces its prior
chunks, keyed by its `doc_id` (the path you pass, relative to --root).

Usage:
    uv run python -m scripts.ingest_knowledge docs/ guide.md --root docs
    uv run python -m scripts.ingest_knowledge notes.txt --scope global

Embeddings and Qdrant must be reachable (same endpoints as semantic memory). Only
text files are read; pass a directory to ingest every matching file under it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.knowledge import GLOBAL_SCOPE, KnowledgeStore

_TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".rst", ".text"}


def _iter_files(paths: list[Path]) -> list[Path]:
    """Expand the given paths to a sorted, de-duplicated list of text files."""
    found: set[Path] = set()
    for path in paths:
        if path.is_dir():
            found.update(p for p in path.rglob("*") if p.suffix.lower() in _TEXT_SUFFIXES)
        elif path.is_file():
            found.add(path)
        else:
            print(f"skip: not found: {path}", file=sys.stderr)
    return sorted(found)


def _doc_id(path: Path, root: Path | None) -> str:
    """A stable id for a document: its path relative to --root (or its name)."""
    if root is not None:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            pass
    return path.as_posix()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest documents into the knowledge base.")
    parser.add_argument("paths", nargs="+", type=Path, help="Files and/or directories to ingest.")
    parser.add_argument(
        "--root", type=Path, default=None,
        help="Base directory for computing each document's id (default: the path itself).",
    )
    parser.add_argument(
        "--scope", default=GLOBAL_SCOPE,
        help=f"Scope to store chunks under (default: {GLOBAL_SCOPE!r}).",
    )
    args = parser.parse_args(argv)

    files = _iter_files(args.paths)
    if not files:
        print("Nothing to ingest.", file=sys.stderr)
        return 1

    store = KnowledgeStore()
    total_chunks = 0
    indexed = 0
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"skip: cannot read {path}: {exc}", file=sys.stderr)
            continue
        doc_id = _doc_id(path, args.root)
        n = store.index_document(doc_id, text, source=path.name, scope=args.scope)
        if n:
            indexed += 1
            total_chunks += n
            print(f"ok: {doc_id} -> {n} chunk(s)")
        else:
            print(f"warn: {doc_id} produced no chunks (empty, or backend unavailable)", file=sys.stderr)

    print(f"\nIngested {indexed}/{len(files)} file(s), {total_chunks} chunk(s) into the knowledge base.")
    return 0 if indexed else 1


if __name__ == "__main__":
    raise SystemExit(main())

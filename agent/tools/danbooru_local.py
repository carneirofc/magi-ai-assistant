"""Local Danbooru data: CSV dumps that answer tag/wiki lookups without the API.

`LocalDanbooru` wraps the two artifact files (config.danbooru_tags_csv /
config.danbooru_wiki_csv):

- tags CSV (`tag,category,count,alias`): ~32k most-used tags, loaded lazily
  into memory once (small) for wildcard search. `alias` is a comma-joined list
  of alternate names (mostly other languages) mapped back to the canonical tag.
- wiki CSV (`id,...,title,body,...,is_deleted`): ~100 MB, too big to hold in
  RAM, so each lookup streams the file and stops at the first title match.
  Slower than a dict but zero memory, and still far cheaper than the
  rate-limited live site.

Missing files are fine: every method reports a miss and the tools fall back to
the live API. Methods are blocking (file I/O) — call via asyncio.to_thread from
async tools. The lazy tag load is not locked; two racing loaders just both do
the work and the last assignment wins.
"""

import csv
import math
import time
from difflib import SequenceMatcher, get_close_matches
from fnmatch import fnmatchcase
from pathlib import Path

from agno.utils.log import log_debug, log_info

# Wiki bodies can exceed csv's default 128 KiB field cap.
csv.field_size_limit(16 * 1024 * 1024)

_TRUE = {"true", "t", "1"}


def _normalize(name: str | None) -> str:
    return (name or "").strip().lower().replace(" ", "_")


def _tokens(q: str) -> list[str]:
    return [t for t in q.split("_") if t]


def _loose_score(q: str, tokens: list[str], name: str) -> float:
    """Rank how well `name` matches a loosely-typed query (higher = better).

    Users type natural phrases ('school girl uniform'), not exact tag names,
    so exact/prefix/substring tiers come first and then token coverage — each
    query word found anywhere in the name, with a crude plural strip so
    'uniforms' still hits 'uniform'. Containment tiers require the contained
    side to be ≥4 chars, or junk like the wiki pages 's' / 'sch' would
    "prefix-match" every query starting with those letters.
    """
    if name == q:
        return 120
    if name.startswith(q):
        return 80
    if len(name) >= 4:
        if q.startswith(name):
            # The longer the shared prefix relative to the query, the better:
            # 'school_uniform' ≈ 'school_uniforms', but 'cola' ≉ 'colarbone'.
            return 60 + 20 * len(name) / len(q)
        if q in name:
            return 70
        if name in q:
            return 65
    elif q in name:
        return 70
    if tokens:
        hits = 0
        for t in tokens:
            if t in name or (len(t) > 3 and t.endswith("s") and t[:-1] in name):
                hits += 1
        if hits == len(tokens):
            return 55
        if hits:
            return 30 + 30 * hits / len(tokens)
    return 0


class LocalDanbooru:
    def __init__(self, tags_csv: str, wiki_csv: str):
        self._tags_path = Path(tags_csv)
        self._wiki_path = Path(wiki_csv)
        self._tags: dict[str, tuple[int, int]] | None = None  # name -> (category, count)
        self._aliases: dict[str, str] = {}  # alias -> canonical name

    # --- tags ---

    @property
    def has_tags(self) -> bool:
        return self._tags is not None or self._tags_path.is_file()

    def _load_tags(self) -> dict[str, tuple[int, int]]:
        if self._tags is None:
            tags: dict[str, tuple[int, int]] = {}
            aliases: dict[str, str] = {}
            with self._tags_path.open(encoding="utf-8-sig", newline="") as fh:
                for row in csv.DictReader(fh):
                    name = _normalize(row.get("tag"))
                    if not name:
                        continue
                    try:
                        category = int(row.get("category") or 0)
                        count = int(row.get("count") or 0)
                    except ValueError:
                        continue
                    tags[name] = (category, count)
                    for alias in (row.get("alias") or "").split(","):
                        alias = _normalize(alias)
                        if alias:
                            aliases.setdefault(alias, name)
            self._tags, self._aliases = tags, aliases
            log_info(
                f"danbooru local: loaded {len(tags)} tags "
                f"(+{len(aliases)} aliases) from {self._tags_path}"
            )
        return self._tags

    def search_tags(self, query: str, limit: int = 20) -> list[tuple[str, int, int]]:
        """Loose tag search → [(name, category, count)], best match first.

        A query with explicit wildcards is honoured verbatim (fnmatch);
        anything else is matched loosely (see _loose_score) over names and
        aliases, with difflib catching outright typos, so 'school girl
        uniform' or 'maids' still find their tags. Ties break on post count.
        """
        q = _normalize(query)
        if not q or not self.has_tags:
            return []
        tags = self._load_tags()
        if "*" in q:
            names = {name for name in tags if fnmatchcase(name, q)}
            ranked = sorted(names, key=lambda n: tags[n][1], reverse=True)
            return [(n, *tags[n]) for n in ranked[:limit]]

        tokens = _tokens(q)
        scored: dict[str, float] = {}
        for name in tags:
            s = _loose_score(q, tokens, name)
            if s:
                scored[name] = s
        for alias, name in self._aliases.items():
            if alias == q:
                scored[name] = max(scored.get(name, 0), 120)
            elif q in alias:
                scored[name] = max(scored.get(name, 0), 55)
        if len(scored) < limit:
            for name in get_close_matches(q, tags.keys(), n=limit, cutoff=0.75):
                ratio = SequenceMatcher(None, q, name).ratio()
                scored.setdefault(name, 40 + 30 * ratio)
        # Blend in popularity so a millions-of-posts tag one tier down still
        # beats an obscure perfect-coverage match (≤ ~21 points at 10M posts).
        ranked = sorted(
            scored, key=lambda n: scored[n] + 3 * math.log10(tags[n][1] + 1), reverse=True
        )
        return [(n, *tags[n]) for n in ranked[:limit]]

    # --- wiki ---

    @property
    def has_wiki(self) -> bool:
        return self._wiki_path.is_file()

    def wiki(self, title: str) -> str | None:
        """Body of the wiki page with this exact (normalized) title, or None."""
        wanted = _normalize(title)
        if not wanted or not self.has_wiki:
            return None
        started = time.perf_counter()
        with self._wiki_path.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                if _normalize(row.get("title")) != wanted:
                    continue
                if (row.get("is_deleted") or "").strip().lower() in _TRUE:
                    return None
                log_debug(
                    f"danbooru local: wiki '{wanted}' found in "
                    f"{time.perf_counter() - started:.2f}s"
                )
                return (row.get("body") or "").strip()
        log_debug(
            f"danbooru local: wiki '{wanted}' not found "
            f"(full scan, {time.perf_counter() - started:.2f}s)"
        )
        return None

    def search_wiki_titles(self, query: str, limit: int = 30) -> list[str]:
        """Titles of live wiki pages loosely matching the query, best first.

        Explicit wildcards are honoured verbatim; anything else ranks the
        whole file by _loose_score (the stream is scanned once either way).
        Equal scores prefer the title whose length is closest to the query —
        'school_uniform' over both 's' and 'multiple_seasonal_school_uniforms'.
        """
        q = _normalize(query)
        if not q or not self.has_wiki:
            return []
        use_glob = "*" in q
        tokens = _tokens(q)
        started = time.perf_counter()
        scored: list[tuple[float, int, str]] = []
        with self._wiki_path.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                title = _normalize(row.get("title"))
                if not title:
                    continue
                if (row.get("is_deleted") or "").strip().lower() in _TRUE:
                    continue
                if use_glob:
                    score = 100.0 if fnmatchcase(title, q) else 0.0
                else:
                    score = _loose_score(q, tokens, title)
                if score > 0:
                    scored.append((score, -abs(len(title) - len(q)), title))
        scored.sort(reverse=True)
        log_debug(
            f"danbooru local: wiki title search '{q}' → {len(scored)} candidates "
            f"in {time.perf_counter() - started:.2f}s"
        )
        return [title for _, _, title in scored[:limit]]

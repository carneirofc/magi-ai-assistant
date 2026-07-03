"""The runtime dependency bundle threaded through the whole build graph.

This is the seam that replaces the former module-global `config` singleton. A
single `AgentContext` is constructed once at the composition root
(`Assistant.create`) from an explicit, immutable `Config`, and injected into
every `build_*` function instead of each module reaching for a process global.

Why an object rather than passing the bare `Config` everywhere:

  - **Instance isolation.** Two assistants with different `Config`s can coexist
    in one process — nothing is read from or written to a shared global.
  - **Shared services live here.** The db (and, later, embedder / object store)
    are genuine per-assistant singletons: built lazily on first use and cached
    on the context, so the whole build graph shares one instance without a
    global cache or repeated construction.
  - **A stable extension seam.** Plugins receive the `AgentContext` in their
    `tools(ctx)` / `members(ctx)` hooks; backend factories are selected from
    `ctx.config`. Growing the runtime means adding a field here, not a new
    global.

`config` is a frozen value object, so a context is effectively immutable in its
inputs; only the lazily-cached services are filled in on first access.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from agno.db.base import BaseDb

from magi.core.config import Config
from magi.core.db import get_db


@dataclass
class AgentContext:
    """Everything the build graph needs, carried explicitly rather than global.

    Construct one per assistant from an explicit `Config`; pass it to the
    builders. Shared services are exposed as cached properties so they are built
    once, on demand, and reused across the graph.
    """

    config: Config

    @cached_property
    def db(self) -> BaseDb:
        """The process-wide persistence backend for this assistant's db_file.

        `get_db` is memoized per distinct path, so two contexts pointed at the
        same `db_file` share one connection while two different files stay
        isolated — exactly the previous singleton behaviour, minus the global
        config read.
        """
        return get_db(self.config.db_file)

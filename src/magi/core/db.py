"""Shared persistence layer.

One db backs everything that must survive a restart:
  - sessions      -> short-term memory (recent conversation history)
  - user memories -> long-term memory (facts the agent learns per user)

`make_db` builds a fresh instance (inject it in tests / for multiple stores).
`get_db` returns a process-wide singleton for normal app wiring. Swap SqliteDb
for PostgresDb here — callers depend on the BaseDb interface, not the backend.
"""

from functools import lru_cache
from pathlib import Path

from agno.db.base import BaseDb
from agno.db.sqlite import SqliteDb
from agno.utils.log import log_info

from magi.core.config import config


def make_db(db_file: str | None = None) -> BaseDb:
    """Build a new db instance. Pass `db_file` to override the configured path."""
    path = db_file or config.db_file
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    log_info(f"db: SqliteDb at {Path(path).resolve()}")
    return SqliteDb(db_file=path)


@lru_cache(maxsize=None)
def get_db(db_file: str | None = None) -> BaseDb:
    """Process-wide singleton db (one per distinct db_file)."""
    return make_db(db_file)

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


def make_db(db_file: str) -> BaseDb:
    """Build a new db instance at `db_file` (inject in tests / for many stores)."""
    Path(db_file).parent.mkdir(parents=True, exist_ok=True)
    log_info(f"db: SqliteDb at {Path(db_file).resolve()}")
    return SqliteDb(db_file=db_file)


@lru_cache(maxsize=None)
def get_db(db_file: str) -> BaseDb:
    """Process-wide singleton db (one per distinct db_file)."""
    return make_db(db_file)

"""Shared persistence layer.

One db backs everything that must survive a restart:
  - sessions     -> short-term memory (recent conversation history)
  - user memories -> long-term memory (facts the agent learns per user)

Swap SqliteDb for PostgresDb (same interface) to move to prod — no agent code
changes. Agents/teams receive this single `db` instance.
"""

from pathlib import Path

from agno.db.sqlite import SqliteDb

from core.config import config

Path(config.db_file).parent.mkdir(parents=True, exist_ok=True)

db = SqliteDb(db_file=config.db_file)

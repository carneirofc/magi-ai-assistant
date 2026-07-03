"""magi — the assistant-core engine (lead + specialist team, memory, knowledge, tools).

Library front door: build a self-contained assistant from an explicit, immutable
`Config` with `Assistant.create(config)`. `AgentContext` is the injected runtime
bundle (config + shared services) threaded through the build graph — there is no
process-global config.
"""

from magi.app import Assistant
from magi.core.config import Config
from magi.core.context import AgentContext

__all__ = ["Assistant", "Config", "AgentContext"]

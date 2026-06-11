"""Shared test fixtures.

Config is code-first (see core/config.py): tests run against the `Config`
dataclass defaults, so nothing on the host can flip them. Only secrets are
read from the environment — pin the one the model tests assert on *before*
`core.config` is imported.
"""

import os

os.environ["LITELLM_MASTER_KEY"] = "test-key"

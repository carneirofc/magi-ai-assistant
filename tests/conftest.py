"""Shared test fixtures.

Set proxy env vars *before* `core.config` is imported, so the module-level
`config` object picks up deterministic values instead of whatever is on the host.
"""

import os

os.environ.setdefault("LITELLM_MASTER_KEY", "test-key")
os.environ.setdefault("LITELLM_BASE_URL", "http://localhost:4000")

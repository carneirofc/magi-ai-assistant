"""Shared test fixtures.

Tests run against the `Config` defaults, so nothing on the host can flip them.
Only secrets are read from the environment — pin the one the model tests assert
on *before* `core.config` is imported. `MAGI_HOME` points at an empty temp dir so
a developer's real `~/.magi` never leaks into a run.
"""

import os
import tempfile

os.environ["LITELLM_MASTER_KEY"] = "test-key"
os.environ["MAGI_HOME"] = tempfile.mkdtemp(prefix="magi-home-")

import pytest  # noqa: E402

from magi.core.config import config, configure  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_config():
    """Restore the config singleton after every test, so a test that calls
    `configure(...)` can never leak settings into the next one."""
    snapshot = config.model_dump()
    yield
    configure(**snapshot)

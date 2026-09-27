"""Run commands as this user, in the workspace directory — no isolation.

Only for a trusted single-operator machine (`magi doctor` warns). It still
scrubs the environment (no secrets reach the command), caps file size and CPU
time, kills the whole process group on timeout, and caps output.
"""

import os
import resource
import signal
import subprocess
from pathlib import Path

from magi.core.sandbox.backend import ExecResult, cap_output

_SAFE_ENV = ("PATH", "LANG", "LC_ALL", "TERM", "TZ")
_MAX_FILE_BYTES = 100 * 1024 * 1024


def scrubbed_env(workspace: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV}
    env["HOME"] = str(workspace)
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    return env


class LocalBackend:
    name = "local"

    def __init__(self, output_limit: int) -> None:
        self.output_limit = output_limit

    def run(self, command: str, *, workspace: Path, timeout: float) -> ExecResult:
        workspace.mkdir(parents=True, exist_ok=True)
        cpu = int(timeout) + 1

        def limits() -> None:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
            resource.setrlimit(resource.RLIMIT_FSIZE, (_MAX_FILE_BYTES, _MAX_FILE_BYTES))

        proc = subprocess.Popen(
            ["bash", "-c", command],
            cwd=workspace,
            env=scrubbed_env(workspace),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # own process group, killed as a whole
            preexec_fn=limits,  # noqa: PLW1509 — rlimits must be set in the child
        )
        try:
            raw, _ = proc.communicate(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            raw, _ = proc.communicate()
            timed_out = True
        text, truncated = cap_output(raw.decode("utf-8", errors="replace"), self.output_limit)
        return ExecResult(
            exit_code=proc.returncode if not timed_out else -9,
            output=text,
            timed_out=timed_out,
            truncated=truncated,
        )

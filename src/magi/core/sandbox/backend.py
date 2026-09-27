"""The execution backend contract (a Protocol, satisfied by shape)."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    output: str  # stdout + stderr, interleaved order not guaranteed; capped
    timed_out: bool = False
    truncated: bool = False


class ExecBackend(Protocol):
    """Runs one shell command inside a per-user workspace directory."""

    name: str

    def run(self, command: str, *, workspace: Path, timeout: float) -> ExecResult: ...


def cap_output(text: str, limit: int) -> tuple[str, bool]:
    """`text` trimmed to `limit` chars (keeping head and tail), and whether it was."""
    if len(text) <= limit:
        return text, False
    half = limit // 2
    return f"{text[:half]}\n… [{len(text) - limit} chars cut] …\n{text[-half:]}", True

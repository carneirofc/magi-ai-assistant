"""Command classification: safe, dangerous (needs approval), or forbidden.

This is a *seatbelt*, not the security boundary — the sandbox backend is.
It errs toward "dangerous": anything it cannot parse, anything that deletes,
installs, reaches the network, escalates, or writes outside the workspace
asks the user first, and a few catastrophic shapes are never run at all.
"""

import re
import shlex
from typing import Literal

type Verdict = Literal["safe", "dangerous", "forbidden"]

_SEPARATORS = re.compile(r"&&|\|\||;|\n|\||&")

_FORBIDDEN_WORDS = {
    "sudo",
    "su",
    "doas",
    "pkexec",
    "mkfs",
    "shutdown",
    "reboot",
    "poweroff",
    "halt",
}
_FORBIDDEN_PATTERNS = [
    re.compile(r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+(/|/\*|~|~/|\$HOME)(\s|$)"),
    re.compile(r"\bdd\b.*\bof=/dev/"),
    re.compile(r">\s*/dev/(sd|nvme|hd|mmcblk)"),
    re.compile(r":\s*\(\s*\)\s*\{.*\|.*&.*\}"),  # fork bomb
    re.compile(r"\b(curl|wget)\b[^|]*\|\s*(ba|z|da)?sh\b"),  # pipe to shell
    re.compile(r"\bchmod\s+-R\s+[0-7]*7[0-7]*\s+/(\s|$)"),
]
_DANGEROUS_WORDS = {
    "rm",
    "rmdir",
    "mv",
    "chmod",
    "chown",
    "chgrp",
    "kill",
    "pkill",
    "killall",
    "curl",
    "wget",
    "ssh",
    "scp",
    "rsync",
    "nc",
    "ncat",
    "telnet",
    "ftp",
    "pip",
    "pip3",
    "uv",
    "npm",
    "npx",
    "yarn",
    "pnpm",
    "apt",
    "apt-get",
    "dnf",
    "yum",
    "pacman",
    "brew",
    "cargo",
    "go",
    "docker",
    "podman",
    "systemctl",
    "crontab",
    "at",
    "nohup",
    "eval",
    "exec",
    "source",
    ".",
    "truncate",
    "shred",
    "git",  # push/reset/clean — reviewed per command below for read-only forms
}
_READ_ONLY_GIT = {"status", "log", "diff", "show", "branch", "ls-files", "rev-parse", "blame"}


def _segments(command: str) -> list[str]:
    return [s.strip() for s in _SEPARATORS.split(command) if s.strip()]


def _command_word(tokens: list[str]) -> tuple[str, list[str]]:
    """Skip leading `VAR=value` assignments; return (word, rest)."""
    i = 0
    while i < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[i]):
        i += 1
    if i >= len(tokens):
        return "", []
    return tokens[i].rsplit("/", 1)[-1], tokens[i + 1 :]


def _writes_outside(command: str) -> bool:
    """A redirect or path argument that targets outside the workspace."""
    for target in re.findall(r">>?\s*([^\s;&|]+)", command):
        if target.startswith(("/", "~")) and not target.startswith("/dev/null"):
            return True
        if ".." in target.split("/"):
            return True
    return False


def classify(command: str) -> Verdict:
    text = command.strip()
    if not text:
        return "dangerous"
    if any(p.search(text) for p in _FORBIDDEN_PATTERNS):
        return "forbidden"
    verdict: Verdict = "safe"
    if _writes_outside(text) or "$(" in text or "`" in text:
        verdict = "dangerous"
    for segment in _segments(text):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            return "dangerous"
        word, rest = _command_word(tokens)
        if word in _FORBIDDEN_WORDS:
            return "forbidden"
        if word == "git" and rest and rest[0] in _READ_ONLY_GIT:
            continue
        if word in _DANGEROUS_WORDS:
            verdict = "dangerous"
        if any(arg.startswith(("/", "~")) or ".." in arg.split("/") for arg in rest):
            verdict = "dangerous"  # touches paths outside the workspace
    return verdict

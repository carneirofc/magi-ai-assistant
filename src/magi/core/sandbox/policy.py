"""Command classification: safe, dangerous (needs approval), or forbidden.

This is a *seatbelt*, not the security boundary — the sandbox backend is.
It errs toward "dangerous": anything it cannot parse, anything that deletes,
installs, reaches the network, escalates, or writes outside the workspace
asks the user first, and a few catastrophic shapes are never run at all.
"""

import posixpath
import re
import shlex
from collections.abc import Iterator
from typing import Literal

type Verdict = Literal["safe", "dangerous", "forbidden"]

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
    "sudoedit",
    "runuser",
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


_RANK: dict[Verdict, int] = {"safe": 0, "dangerous": 1, "forbidden": 2}
_MAX_DEPTH = 4

# Words that only introduce the next command, never run anything themselves.
_KEYWORDS = {"!", "{", "}", "if", "then", "else", "elif", "fi", "do", "done", "while", "until"}
# Commands that run their arguments as another command: name -> (options that
# take a value, positional arguments before the command). Unwrapped so
# `env sudo x` or `xargs rm` is judged by the command they actually run.
_WRAPPERS: dict[str, tuple[frozenset[str], int]] = {
    "env": (frozenset({"-u", "--unset", "-C", "--chdir"}), 0),
    "nice": (frozenset({"-n", "--adjustment"}), 0),
    "nohup": (frozenset(), 0),
    "timeout": (frozenset({"-s", "--signal", "-k", "--kill-after"}), 1),
    "stdbuf": (frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}), 0),
    "ionice": (frozenset({"-c", "--class", "-n", "--classdata", "-p", "--pid", "-P", "-u"}), 0),
    "time": (frozenset({"-f", "--format", "-o", "--output"}), 0),
    "command": (frozenset(), 0),
    "builtin": (frozenset(), 0),
    "exec": (frozenset({"-a"}), 0),
    "setsid": (frozenset(), 0),
    "chrt": (frozenset(), 1),
    "taskset": (frozenset(), 1),
    "unbuffer": (frozenset(), 0),
    "busybox": (frozenset(), 0),
    "xargs": (
        frozenset(
            {"-I", "-n", "-P", "-d", "-L", "-s", "-a", "-E"}
            | {"--max-args", "--max-procs", "--delimiter", "--arg-file", "--max-lines"}
            | {"--max-chars", "--eof"}
        ),
        0,
    ),
}
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "mksh", "fish"}
_FIND_EXEC = {"-exec", "-execdir", "-ok", "-okdir"}
# Command, process, and backtick substitutions (innermost only).
_SUBSTITUTION = re.compile(r"[$<>]\(([^()]*)\)|`([^`]*)`")
# An absolute, home, or parent path anywhere inside an argument
# (`--file=/etc/x`, `open('/etc/passwd')`), not just at its start.
_EMBEDDED_PATH = re.compile(r"""(?:^|[\s'"(=,;:])(?:/(?!/)|~(?:/|$|[\s'"])|\.\.(?:/|$))""")
_FORBIDDEN_MENTION = re.compile(r"\b(?:" + "|".join(map(re.escape, _FORBIDDEN_WORDS)) + r")\b")


def _worst(a: Verdict, b: Verdict) -> Verdict:
    return a if _RANK[a] >= _RANK[b] else b


def _is_operator(token: str) -> bool:
    """A control operator (`;`, `&&`, `|`, `(`, `<(`…) — not a redirect."""
    return token.endswith("(") or (token != "" and all(c in "();&|" for c in token))


def _tokenize(line: str) -> list[str]:
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""  # scan past `#` too: better to over-flag than miss
    return list(lexer)


def _segments(tokens: list[str]) -> Iterator[list[str]]:
    segment: list[str] = []
    for token in tokens:
        if _is_operator(token):
            if segment:
                yield segment
            segment = []
        else:
            segment.append(token)
    if segment:
        yield segment


def _skip_options(tokens: list[str], i: int, takes_value: frozenset[str]) -> int:
    while i < len(tokens) and tokens[i].startswith("-") and tokens[i] != "-":
        token = tokens[i]
        i += 1
        if token == "--":
            break
        if token in takes_value:
            i += 1
    return i


def _writes_outside(command: str) -> bool:
    """A redirect or path argument that targets outside the workspace."""
    for target in re.findall(r">>?\s*([^\s;&|]+)", command):
        if target.startswith(("/", "~")) and not target.startswith("/dev/null"):
            return True
        if ".." in target.split("/"):
            return True
    return False


def _is_root(arg: str) -> bool:
    """`/`, `/*`, `~`, `$HOME/*`, `"/."`… — the whole filesystem or home."""
    for home in ("${HOME}", "$HOME", "~"):
        if arg.startswith(home):
            arg = "/" + arg[len(home) :]
            break
    arg = arg.rstrip("*")
    return arg.startswith("/") and posixpath.normpath(arg) in {"/", "//"}


def _check_args(args: list[str]) -> Verdict:
    for arg in args:
        if "$" in arg or ".." in arg.split("/") or _EMBEDDED_PATH.search(arg):
            return "dangerous"  # expands to, or touches, a path outside the workspace
        if _FORBIDDEN_MENTION.search(arg):
            return "dangerous"  # e.g. a string a later stage may run
    return "safe"


def _classify_segment(tokens: list[str], depth: int) -> Verdict:
    verdict: Verdict = "safe"
    i = 0
    while True:
        while i < len(tokens) and (
            tokens[i] in _KEYWORDS or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[i])
        ):
            i += 1
        if i >= len(tokens):
            return verdict
        if any(c.isspace() for c in tokens[i]):  # a quoted string run as a command
            return _worst(verdict, _classify(tokens[i], depth + 1))
        word = tokens[i].rsplit("/", 1)[-1]
        if word in _FORBIDDEN_WORDS:
            return "forbidden"
        if word not in _WRAPPERS:
            break
        if word in _DANGEROUS_WORDS:
            verdict = "dangerous"
        takes_value, positionals = _WRAPPERS[word]
        i = _skip_options(tokens, i + 1, takes_value) + positionals
    rest = tokens[i + 1 :]
    verdict = _worst(verdict, _check_args(rest))
    if word == "git" and rest and rest[0] in _READ_ONLY_GIT:
        return verdict
    if word in _DANGEROUS_WORDS:
        verdict = "dangerous"
    if word == "rm" and any(
        a == "--recursive" or (a.startswith("-") and not a.startswith("--") and "r" in a.lower())
        for a in rest
    ):
        if any(_is_root(a) for a in rest):
            return "forbidden"
    if word in _SHELLS:
        flag = next(
            (a for a in rest if a.startswith("-") and not a.startswith("--") and "c" in a), None
        )
        if flag is None:
            return "dangerous"  # runs a script or stdin the policy cannot see
        script = rest[rest.index(flag) + 1 :]
        verdict = _worst(verdict, _classify(script[0], depth + 1) if script else "dangerous")
    elif word == "eval" or word == "watch":
        body = (
            rest[_skip_options(rest, 0, frozenset({"-n", "--interval"})) :]
            if word == "watch"
            else rest
        )
        verdict = _worst(verdict, _classify(" ".join(body), depth + 1))
    elif word == "find":
        if "-delete" in rest:
            verdict = "dangerous"
        for j, arg in enumerate(rest):
            if arg in _FIND_EXEC:
                verdict = _worst(verdict, _classify_segment(rest[j + 1 :], depth + 1))
    return verdict


def _classify(command: str, depth: int) -> Verdict:
    text = command.strip()
    if not text or depth > _MAX_DEPTH:
        return "dangerous"
    if any(p.search(text) for p in _FORBIDDEN_PATTERNS):
        return "forbidden"
    verdict: Verdict = "safe"
    if _writes_outside(text) or "$(" in text or "`" in text or "<(" in text or ">(" in text:
        verdict = "dangerous"
    for match in _SUBSTITUTION.finditer(text):
        inner = match.group(1) or match.group(2) or ""
        if inner.strip():
            verdict = _worst(verdict, _classify(inner, depth + 1))
    for line in text.splitlines():
        try:
            tokens = _tokenize(line)
        except ValueError:
            return _worst(verdict, "dangerous")
        for segment in _segments(tokens):
            verdict = _worst(verdict, _classify_segment(segment, depth))
        if verdict == "forbidden":
            return verdict
    return verdict


def classify(command: str) -> Verdict:
    """Judge a shell command. Wrappers (`env`, `nice`, `xargs`, `timeout`…) are
    unwrapped, and `sh -c` / `eval` / `find -exec` / substitution bodies are
    judged recursively, so a forbidden command cannot hide behind them."""
    return _classify(command, 0)

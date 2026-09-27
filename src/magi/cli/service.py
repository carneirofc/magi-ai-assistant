"""`magi gateway install | uninstall | status | logs` — a systemd *user* service.

`render_unit` is pure (tested by snapshot); everything that touches the system
goes through an injected `Runner` so tests never call systemctl. The unit runs
`magi run` with the same config files, as the installing user, restarting on
failure — the gateway exits as a whole when any channel dies (ADR 0005), and
systemd brings it back.
"""

import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

UNIT_NAME = "magi.service"

type Runner = Callable[[Sequence[str]], int]


def _run(cmd: Sequence[str]) -> int:
    return subprocess.run(list(cmd), check=False).returncode


def unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / UNIT_NAME


def magi_command() -> list[str]:
    """How to invoke this same install of magi from a unit file."""
    found = shutil.which("magi")
    if found:
        return [str(Path(found).resolve())]
    return [sys.executable, "-m", "magi.cli"]


def render_unit(
    command: Sequence[str],
    *,
    home: Path,
    workdir: Path,
    config_files: Sequence[Path] = (),
    channels: Sequence[str] = (),
) -> str:
    args = [*command, "run"]
    for path in config_files:
        args += ["-c", str(path)]
    args += list(channels)
    return (
        "[Unit]\n"
        "Description=magi assistant gateway\n"
        "Wants=network-online.target\n"
        "After=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={shlex.join(args)}\n"
        f"WorkingDirectory={workdir}\n"
        f"Environment=MAGI_HOME={home}\n"
        "Environment=PYTHONUNBUFFERED=1\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _require_systemd(out: TextIO) -> bool:
    if shutil.which("systemctl") is None:
        out.write(
            "systemctl not found — `magi gateway` manages a systemd user service (Linux).\n"
            "Run `magi run` under your own supervisor instead.\n"
        )
        return False
    return True


def install(
    *,
    home: Path,
    workdir: Path,
    config_files: Sequence[Path],
    channels: Sequence[str],
    out: TextIO,
    run: Runner = _run,
    target: Path | None = None,
) -> int:
    if run is _run and not _require_systemd(out):
        return 1
    path = target or unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_unit(
            magi_command(),
            home=home,
            workdir=workdir,
            config_files=[p.resolve() for p in config_files],
            channels=channels,
        ),
        encoding="utf-8",
    )
    out.write(f"wrote {path}\n")
    for cmd in (
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", UNIT_NAME],
    ):
        if (code := run(cmd)) != 0:
            out.write(f"`{shlex.join(cmd)}` failed ({code})\n")
            return code
    out.write(
        "magi is running and starts at login.\n"
        "To keep it running while you are logged out: loginctl enable-linger $USER\n"
        "Logs: magi gateway logs\n"
    )
    return 0


def uninstall(*, out: TextIO, run: Runner = _run, target: Path | None = None) -> int:
    if run is _run and not _require_systemd(out):
        return 1
    path = target or unit_path()
    run(["systemctl", "--user", "disable", "--now", UNIT_NAME])
    if path.exists():
        path.unlink()
        out.write(f"removed {path}\n")
    return run(["systemctl", "--user", "daemon-reload"])


def status(*, run: Runner = _run) -> int:
    return run(["systemctl", "--user", "status", UNIT_NAME, "--no-pager"])


def logs(*, follow: bool, lines: int, run: Runner = _run) -> int:
    cmd = ["journalctl", "--user", "-u", UNIT_NAME, "-n", str(lines)]
    return run([*cmd, "-f"] if follow else cmd)

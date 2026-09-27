"""Tests for `magi gateway` (magi/cli/service): the unit file and the systemctl calls."""

import io
from collections.abc import Sequence
from pathlib import Path

from magi.cli import service


def test_render_unit_snapshot():
    unit = service.render_unit(
        ["/opt/magi/bin/magi"],
        home=Path("/home/u/.magi"),
        workdir=Path("/home/u/.magi"),
        config_files=[Path("/etc/magi/my config.yaml")],
        channels=["api", "telegram"],
    )
    assert unit == (
        "[Unit]\n"
        "Description=magi assistant gateway\n"
        "Wants=network-online.target\n"
        "After=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        "ExecStart=/opt/magi/bin/magi run -c '/etc/magi/my config.yaml' api telegram\n"
        "WorkingDirectory=/home/u/.magi\n"
        "Environment=MAGI_HOME=/home/u/.magi\n"
        "Environment=PYTHONUNBUFFERED=1\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


class _Recorder:
    def __init__(self, codes: dict[str, int] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.codes = codes or {}

    def __call__(self, cmd: Sequence[str]) -> int:
        self.calls.append(list(cmd))
        return self.codes.get(" ".join(cmd), 0)


def test_install_writes_unit_and_enables(tmp_path):
    run = _Recorder()
    out = io.StringIO()
    target = tmp_path / "magi.service"
    code = service.install(
        home=tmp_path,
        workdir=tmp_path,
        config_files=[],
        channels=[],
        out=out,
        run=run,
        target=target,
    )
    assert code == 0
    assert "ExecStart=" in target.read_text(encoding="utf-8")
    assert run.calls == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", "magi.service"],
    ]
    assert "enable-linger" in out.getvalue()


def test_install_reports_systemctl_failure(tmp_path):
    run = _Recorder({"systemctl --user enable --now magi.service": 5})
    out = io.StringIO()
    code = service.install(
        home=tmp_path,
        workdir=tmp_path,
        config_files=[],
        channels=[],
        out=out,
        run=run,
        target=tmp_path / "magi.service",
    )
    assert code == 5 and "failed" in out.getvalue()


def test_uninstall_removes_unit(tmp_path):
    target = tmp_path / "magi.service"
    target.write_text("x", encoding="utf-8")
    run = _Recorder()
    assert service.uninstall(out=io.StringIO(), run=run, target=target) == 0
    assert not target.exists()
    assert run.calls[0] == ["systemctl", "--user", "disable", "--now", "magi.service"]


def test_logs_follow_flag():
    run = _Recorder()
    service.logs(follow=True, lines=20, run=run)
    assert run.calls == [["journalctl", "--user", "-u", "magi.service", "-n", "20", "-f"]]

"""The `magi` command line — setup, run, config, doctor.

The CLI is a composition root *above* `channels`: it loads the config file,
then hands off to the same channel runners `main.py` uses. See
src/magi/cli/AGENTS.md.
"""

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import get_args

from agno.utils.log import logger as agno_logger

# Keep management commands quiet: agno logs every prompt load at INFO, which
# fires on import. `run` turns INFO back on before serving. (magi imports stay
# inside functions so this runs first.)
agno_logger.setLevel(logging.WARNING)


def _add_config_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-c",
        "--config",
        action="append",
        type=Path,
        default=[],
        metavar="PATH",
        help="config file(s) to load, later wins (default: ./magi.yaml or $MAGI_HOME/config.yaml)",
    )


def build_parser() -> argparse.ArgumentParser:
    from magi.core.config import ChannelName

    parser = argparse.ArgumentParser(prog="magi", description="Run and manage a magi assistant.")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    sub.add_parser("setup", help="interactive first-run wizard")

    run = sub.add_parser("run", help="serve channels (default: channels.enabled)")
    _add_config_option(run)
    run.add_argument(
        "channels",
        nargs="*",
        choices=get_args(ChannelName.__value__),
        metavar="CHANNEL",
        help=f"any of: {', '.join(get_args(ChannelName.__value__))}",
    )
    run.add_argument(
        "--no-frameless", action="store_true", help="desktop: use a normal titled window"
    )

    doctor = sub.add_parser("doctor", help="check the config, backends, and extras")
    _add_config_option(doctor)

    gw = sub.add_parser("gateway", help="run magi as a systemd user service")
    gw_sub = gw.add_subparsers(dest="action", metavar="ACTION", required=True)
    gw_install = gw_sub.add_parser(
        "install", help="write + enable ~/.config/systemd/user/magi.service"
    )
    _add_config_option(gw_install)
    gw_install.add_argument(
        "channels",
        nargs="*",
        choices=get_args(ChannelName.__value__),
        metavar="CHANNEL",
        help="channels to serve (default: channels.enabled at runtime)",
    )
    gw_sub.add_parser("uninstall", help="stop, disable, and remove the service")
    gw_sub.add_parser("status", help="systemctl --user status magi")
    gw_logs = gw_sub.add_parser("logs", help="journalctl for the service")
    gw_logs.add_argument("-f", "--follow", action="store_true")
    gw_logs.add_argument("-n", "--lines", type=int, default=100)

    cfg = sub.add_parser("config", help="inspect or edit the config file")
    _add_config_option(cfg)
    cfg_sub = cfg.add_subparsers(dest="action", metavar="ACTION", required=True)
    cfg_sub.add_parser("path", help="show MAGI_HOME and the config file in use")
    cfg_sub.add_parser("show", help="print every effective setting (secrets masked)")
    get = cfg_sub.add_parser("get", help="print one effective setting")
    get.add_argument("key")
    set_ = cfg_sub.add_parser("set", help="set a value in the config file (validated)")
    set_.add_argument("key", help="setting name; dotted for groups, e.g. channels.enabled")
    set_.add_argument("value", help="YAML value: 8000, true, '[api, discord]', text")
    set_.add_argument("--file", type=Path, help="config file to edit")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    from magi.core.config_file import ConfigFileError

    parser = build_parser()
    args = parser.parse_args(argv)
    out = sys.stdout
    try:
        match args.command:
            case "setup":
                from magi.cli.doctor import report, run_checks
                from magi.cli.run import prepare
                from magi.cli.setup import TerminalPrompter, run_setup
                from magi.core.config import config
                from magi.core.config_file import magi_home

                result = run_setup(magi_home(), TerminalPrompter(out))
                out.write(f"\nwrote {result.config_path}\n")
                if result.secrets_written:
                    out.write(f"wrote {', '.join(result.secrets_written)} to {result.env_path}\n")
                prepare([result.config_path])
                out.write("\n")
                return report(run_checks(config), out)
            case "run":
                from magi.cli.run import prepare, run_channels

                agno_logger.setLevel(logging.INFO)
                prepare(args.config)
                run_channels(args.channels, frameless=not args.no_frameless)
                return 0
            case "doctor":
                from magi.cli.doctor import report, run_checks
                from magi.cli.run import prepare
                from magi.core.config import config

                path = prepare(args.config)
                out.write(f"config: {path or '(none — defaults only)'}\n\n")
                return report(run_checks(config), out)
            case "gateway":
                from magi.cli import service
                from magi.cli.run import prepare
                from magi.core.config_file import magi_home

                match args.action:
                    case "install":
                        # Resolve before prepare() changes into the data root.
                        given = [p.expanduser().resolve() for p in args.config]
                        primary = prepare(given)
                        files = given or ([primary] if primary else [])
                        return service.install(
                            home=magi_home(),
                            workdir=Path.cwd(),  # prepare() moved us to the data root
                            config_files=files,
                            channels=args.channels,
                            out=out,
                        )
                    case "uninstall":
                        return service.uninstall(out=out)
                    case "status":
                        return service.status()
                    case _:
                        return service.logs(follow=args.follow, lines=args.lines)
            case "config":
                from magi.cli import config_cmd
                from magi.cli.run import prepare

                if args.action == "set":
                    return config_cmd.cmd_set(args.key, args.value, args.file, out)
                if args.action == "path":
                    return config_cmd.cmd_path(out)
                prepare(args.config)
                if args.action == "show":
                    return config_cmd.cmd_show(out)
                return config_cmd.cmd_get(args.key, out)
            case _:
                parser.print_help(out)
                return 0
    except ConfigFileError as exc:
        sys.stderr.write(f"magi: {exc}\n")
        return 2


__all__ = ["build_parser", "main"]

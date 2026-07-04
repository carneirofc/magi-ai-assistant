"""magi.desktop — a frameless, widget-style native shell for the web frontend.

This is the desktop counterpart to the browser: one process that (a) serves the
*same* Next.js frontend as `web/` and (b) renders it in a frameless, translucent,
draggable Qt window with no browser chrome. There is no separately-run web
server — the shell owns a Node child process (`FrontendServer`) bound to
`127.0.0.1` on an ephemeral port, and tears it down on quit.

Three separated concerns, one per module:

  * `server.FrontendServer` — launch/own/stop the Next.js Node child (the local
    "in-process" frontend serving; a managed subprocess since the frontend is a
    Node app, not static assets — see docs/desktop.md for that design note).
  * `bridge.NativeBridge` — the JS <-> Python contract exposed over QWebChannel.
  * `window.FramelessWindow` — the translucent widget window + QWebEngineView.

`app.run_desktop()` is the composition root (single-instance guard, QApplication
bootstrap, wiring, graceful shutdown), wired into `main.py` as the `desktop`
subcommand. Everything is code-first config (magi.core.config, `desktop_*`); the
optional `desktop` extra provides PySide6 (`uv sync --extra desktop`).
"""

from __future__ import annotations

__all__ = ["run_desktop"]


def run_desktop() -> int:
    """Start the desktop shell; returns the process exit code.

    Thin re-export so callers (main.py) don't import PySide6 until the desktop
    channel is actually selected — keeps `python main.py api` free of the Qt
    import cost and lets the base install skip the optional `desktop` extra.
    """
    from magi.desktop.app import run_desktop as _run

    return _run()

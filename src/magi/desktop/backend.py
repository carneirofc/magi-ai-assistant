"""BackendServer — run the chat+admin API in-process for the desktop shell.

The web frontend is only a face: its pages call BFF routes that proxy to the
Python chat-api (`/v1/*`, e.g. the chat stream + the team page's
`/v1/introspection`) and admin-api (`/admin/v1/*`, e.g. the dashboard, memory,
knowledge, subjects, persona pages). Standalone those are `python main.py api`
and `python main.py admin`; here the shell runs them itself so `python main.py
desktop` is one self-contained command with every page live.

It's the exact single-app shape the `api` channel already offers via
`config.admin_enabled` (ADR 0002): one FastAPI app serving `/v1/*` AND
`/admin/v1/*`. We run it on a loopback ephemeral port in a daemon thread (uvicorn
skips signal handlers off the main thread, so the Qt loop keeps the main thread),
mirroring `channels.discord.serve_with_admin`.

If a backend is already running elsewhere (e.g. `python main.py api` in Docker),
set `desktop_serve_backend=False` and point the frontend at it via CHAT_API_URL /
ADMIN_API_URL instead.
"""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request

from agno.utils.log import log_info

from magi.desktop.server import _reserve_free_port


class BackendServerError(RuntimeError):
    """The in-process API backend could not start or become ready in time."""


class BackendServer:
    """Runs the chat+admin FastAPI app in-process on a loopback port."""

    def __init__(self, *, ready_timeout: float = 30.0) -> None:
        self._ready_timeout = ready_timeout
        self._port = _reserve_free_port()
        self._server: object | None = None  # uvicorn.Server (typed loosely to defer import)
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        """The loopback origin the frontend's BFF should proxy to (chat + admin)."""
        return f"http://127.0.0.1:{self._port}"

    def start(self) -> None:
        """Build the app, serve it in a daemon thread, and block until healthy."""
        import uvicorn

        from magi.channels.api import build_api_app

        # build_api_app wires the brain AND mounts /admin/v1/* when
        # config.admin_enabled is set (see main.py configure_desktop).
        app = build_api_app()
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=self._port, log_level="warning")
        )
        self._server = server

        log_info(f"desktop: starting in-process chat+admin backend on {self.base_url}")
        thread = threading.Thread(target=server.run, name="magi-desktop-backend", daemon=True)
        self._thread = thread
        thread.start()

        self._wait_until_ready()

    def stop(self) -> None:
        """Ask uvicorn to exit and join its thread."""
        server = self._server
        thread = self._thread
        if server is None or thread is None:
            return
        log_info("desktop: stopping in-process backend")
        server.should_exit = True  # type: ignore[attr-defined]  # uvicorn.Server
        thread.join(timeout=10)
        self._server = None
        self._thread = None

    def _wait_until_ready(self) -> None:
        """Poll the unauthenticated /healthz until it answers, or time out."""
        deadline = time.monotonic() + self._ready_timeout
        last_err = "no response"
        while time.monotonic() < deadline:
            thread = self._thread
            if thread is not None and not thread.is_alive():
                raise BackendServerError("backend thread exited before serving")
            try:
                with urllib.request.urlopen(f"{self.base_url}/healthz", timeout=1) as resp:
                    if resp.status == 200:
                        log_info(f"desktop: backend ready at {self.base_url}")
                        return
            except (urllib.error.URLError, ConnectionError, OSError) as exc:
                last_err = str(exc)
            time.sleep(0.2)

        self.stop()
        raise BackendServerError(
            f"backend not ready within {self._ready_timeout:.0f}s ({last_err})"
        )

"""FrontendServer — own the Next.js frontend as a loopback child process.

The web frontend (web/) is a Next.js standalone app: server-side route handlers
(the BFF) mean it can't be served as static files, so the "serve the frontend
in-process" requirement is met by a *managed subprocess* the shell fully owns —
one executable launches it, binds it to loopback on an ephemeral port, waits for
readiness, and tears the process tree down on quit. No separately-run web server.

Two runnable layouts are auto-detected (see `_resolve_command`):

  * dev — a plain `next build` leaves static assets under `web/.next/static`, so
    we run Next's own `next start` CLI (serves everything correctly from .next/).
  * assembled / frozen — the Docker/PyInstaller layout colocates `server.js` with
    `.next/static` + `public` under `.next/standalone/`, so we run that directly.

Loopback is enforced by `HOSTNAME=127.0.0.1`; the port is one we reserve in
Python (Next's server.js does `parseInt(PORT)||3000`, so PORT=0 would become 3000
— we can't ask it for an ephemeral port, we assign a concrete free one).
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from agno.utils.log import log_info, log_warning

_IS_WINDOWS = sys.platform.startswith("win")


def _find_web_dir(configured: str | None) -> Path:
    """Locate the built web/ project (the dir containing `.next/`).

    Honors an explicit `desktop_web_dir`; otherwise resolves it relative to the
    source tree, or to PyInstaller's extraction dir (`sys._MEIPASS`) when frozen.
    """
    if configured:
        return Path(configured).expanduser().resolve()

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is not None:  # frozen: assets are bundled under _MEIPASS/web
        return Path(meipass) / "web"

    # From source: src/magi/desktop/server.py -> repo root is parents[3].
    return Path(__file__).resolve().parents[3] / "web"


def _reserve_free_port() -> int:
    """Reserve an ephemeral loopback port and hand back the number.

    Bind :0 to let the OS pick, read it, then close — the small window before the
    child re-binds is the standard, accepted race for this pattern.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class FrontendServerError(RuntimeError):
    """The frontend server could not be located, launched, or reached in time."""


class FrontendServer:
    """Runs and owns the Next.js frontend as a loopback child process."""

    def __init__(
        self,
        web_dir: str | None,
        *,
        node_command: str = "node",
        ready_timeout: float = 30.0,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self._web_dir = _find_web_dir(web_dir)
        self._node = node_command
        self._ready_timeout = ready_timeout
        # Extra env for the child (e.g. CHAT_API_URL / ADMIN_API_URL + tokens
        # pointing the BFF at the shell's in-process backend). Wins over inherited.
        self._extra_env = dict(extra_env or {})
        self._port = _reserve_free_port()
        self._proc: subprocess.Popen[bytes] | None = None

    @property
    def base_url(self) -> str:
        """The loopback origin the window should load once ready."""
        return f"http://127.0.0.1:{self._port}"

    def url_for(self, path: str) -> str:
        """Absolute URL for an app route (e.g. `/chat`)."""
        return f"{self.base_url}/{path.lstrip('/')}"

    # --- lifecycle ------------------------------------------------------------
    def start(self) -> None:
        """Launch the child and block until it answers, or raise."""
        command, cwd = self._resolve_command()
        env = self._child_env()
        log_info(f"desktop: starting frontend `{' '.join(command)}` (cwd={cwd}) on {self.base_url}")

        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if _IS_WINDOWS else 0
        try:
            self._proc = subprocess.Popen(
                command,
                cwd=str(cwd),
                env=env,
                creationflags=creationflags,
                # POSIX: own process group so stop() can signal the whole tree.
                start_new_session=not _IS_WINDOWS,
            )
        except FileNotFoundError as exc:  # node missing / bad command
            raise FrontendServerError(
                f"could not launch Node ({self._node!r}); is Node.js installed and on PATH? ({exc})"
            ) from exc

        self._wait_until_ready()

    def stop(self) -> None:
        """Terminate the child (and its worker tree) and release it."""
        proc = self._proc
        if proc is None or proc.poll() is not None:
            self._proc = None
            return

        log_info("desktop: stopping frontend server")
        if _IS_WINDOWS:
            # next start spawns worker children; taskkill /T ends the whole tree.
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
        else:
            import os
            import signal

            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                proc.terminate()

        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        self._proc = None

    # --- internals ------------------------------------------------------------
    def _resolve_command(self) -> tuple[list[str], Path]:
        """Pick the launch command + working dir for the detected build layout."""
        if not self._web_dir.exists():
            raise FrontendServerError(f"web dir not found: {self._web_dir}")

        standalone = self._web_dir / ".next" / "standalone"
        standalone_server = standalone / "server.js"
        # server.js can only serve assets when they're colocated (Docker/frozen layout).
        colocated_static = (standalone / ".next" / "static").exists()
        if standalone_server.exists() and colocated_static:
            return [self._node, "server.js"], standalone

        # Dev: a plain `next build` — run Next's CLI so it serves from .next/ + public.
        next_cli = self._web_dir / "node_modules" / "next" / "dist" / "bin" / "next"
        if next_cli.exists():
            return (
                [self._node, str(next_cli), "start", "-H", "127.0.0.1", "-p", str(self._port)],
                self._web_dir,
            )

        raise FrontendServerError(
            f"no runnable frontend build under {self._web_dir}. Run `npm install && npm run build`"
            " in web/ (dev), or ship the assembled standalone layout (server.js + .next/static"
            " + public colocated) for a frozen build."
        )

    def _child_env(self) -> dict[str, str]:
        """The child's environment: inherit the parent (the BFF needs CHAT_API_URL,
        API_AUTH_TOKEN, ADMIN_PASSWORD, SESSION_SECRET, … — same as the browser web
        app) and pin the loopback bind + port + production mode on top."""
        import os

        env = dict(os.environ)
        env["PORT"] = str(self._port)
        env["HOSTNAME"] = "127.0.0.1"  # loopback only — never a public interface
        env.setdefault("NODE_ENV", "production")
        env.setdefault("NEXT_TELEMETRY_DISABLED", "1")
        # Point the BFF at the shell's backend (overrides any inherited value).
        env.update(self._extra_env)
        return env

    def _wait_until_ready(self) -> None:
        """Poll the loopback root until the server answers (any HTTP status) or time out."""
        deadline = time.monotonic() + self._ready_timeout
        last_err: str = "no response"
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                raise FrontendServerError(
                    f"frontend server exited early (code {self._proc.returncode}) before serving"
                )
            try:
                # Any HTTP reply (200, or a 307 to /login) means it's up and serving.
                with urllib.request.urlopen(self.base_url, timeout=1):
                    log_info(f"desktop: frontend ready at {self.base_url}")
                    return
            except urllib.error.HTTPError:
                log_info(f"desktop: frontend ready at {self.base_url}")
                return
            except (urllib.error.URLError, ConnectionError, OSError) as exc:
                last_err = str(exc)
                time.sleep(0.25)

        self.stop()
        raise FrontendServerError(
            f"frontend server not ready within {self._ready_timeout:.0f}s ({last_err})"
        )

"""run_desktop — the desktop shell's composition root.

Bootstraps QApplication, enforces a single instance, starts the frontend server,
builds the window, and wires graceful shutdown (stop the Node child, persist
geometry) before handing control to the Qt event loop. Called from ``main.py``'s
``desktop`` subcommand.
"""

from __future__ import annotations

import secrets
import sys

from agno.utils.log import log_error, log_info, log_warning
from PySide6.QtCore import QSettings, Qt
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from magi.core.config import config
from magi.desktop.backend import BackendServer, BackendServerError
from magi.desktop.server import FrontendServer, FrontendServerError
from magi.desktop.window import FramelessWindow

# Per-user single-instance key. QLocalServer name; a second launch that connects
# to it just asks the first instance to surface its window, then exits.
_SINGLE_INSTANCE_KEY = "magi-desktop-shell.single-instance"


def _session_secret() -> str:
    """The HMAC key the frontend signs its session cookie with.

    Sourced from config (env/.env). The frontend REQUIRES it (it throws without
    one), so when unset we generate a random key ONCE and persist it in QSettings —
    stable across launches, so the operator's login cookie keeps verifying.
    """
    if config.session_secret:
        return config.session_secret
    store = QSettings("magi", "desktop-shell")
    saved = store.value("session_secret")
    if isinstance(saved, str) and saved:
        return saved
    generated = secrets.token_hex(32)
    store.setValue("session_secret", generated)
    log_info("desktop: generated a SESSION_SECRET (persisted in QSettings)")
    return generated


def _frontend_env(backend: BackendServer | None) -> dict[str, str]:
    """The full env the web BFF needs, sourced from Python — never from web/.env.

    A frozen build ships no `web/.env`, so the shell is the single source of truth:
    it forwards the backend URLs, upstream bearer tokens, the operator password, and
    the (required) session secret explicitly to the Node child.
    """
    env: dict[str, str] = {}

    if backend is not None:
        # chat + admin share this one in-process app.
        env["CHAT_API_URL"] = backend.base_url
        env["ADMIN_API_URL"] = backend.base_url
    # Bearer tokens the BFF presents upstream (match the backend's; both open when
    # unset). Skip when unset so the BFF sends no Authorization header.
    if config.api_auth_token:
        env["API_AUTH_TOKEN"] = config.api_auth_token
    if config.admin_auth_token:
        env["ADMIN_AUTH_TOKEN"] = config.admin_auth_token

    # Frontend auth (login + cookie signing).
    if config.admin_password:
        env["ADMIN_PASSWORD"] = config.admin_password
    else:
        log_warning(
            "desktop: ADMIN_PASSWORD is unset — the frontend's login cannot succeed; "
            "set it (env/.env) to unlock the UI"
        )
    env["SESSION_SECRET"] = _session_secret()
    return env


def _already_running() -> bool:
    """True if another instance holds the single-instance socket (and was pinged)."""
    probe = QLocalSocket()
    probe.connectToServer(_SINGLE_INSTANCE_KEY)
    if probe.waitForConnected(300):
        probe.write(b"raise")
        probe.flush()
        probe.waitForBytesWritten(300)
        probe.disconnectFromServer()
        return True
    return False


def _install_single_instance_server(window: FramelessWindow) -> QLocalServer:
    """Listen for later launches and raise the existing window when one connects."""
    # A crashed prior instance can leave a stale listener; clear it first.
    QLocalServer.removeServer(_SINGLE_INSTANCE_KEY)
    server = QLocalServer()
    server.listen(_SINGLE_INSTANCE_KEY)

    def _on_new_connection() -> None:
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.readyRead.connect(lambda: window.show_and_raise())
            window.show_and_raise()

    server.newConnection.connect(_on_new_connection)
    return server


def run_desktop() -> int:
    """Start the shell; returns the Qt exit code (0 if a duplicate launch bowed out)."""
    # Share GL contexts across threads — recommended before QApplication when
    # QtWebEngine is in play (multiple web contexts / accelerated compositing).
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

    app = QApplication(sys.argv)
    app.setApplicationName("magi-desktop")
    # Frameless Tool window is the only window; don't quit just because it hides.
    app.setQuitOnLastWindowClosed(False)

    if _already_running():
        log_info("desktop: another instance is running — asked it to surface, exiting")
        return 0

    # 1) The chat+admin API the frontend's pages proxy to. Run it in-process so
    #    every page (dashboard, memory, knowledge, subjects, team, chat) is live
    #    from this one command — unless told to use an external backend.
    backend: BackendServer | None = None
    if config.desktop_serve_backend:
        backend = BackendServer(ready_timeout=config.desktop_server_ready_timeout)
        try:
            backend.start()
        except BackendServerError as exc:
            log_error(f"desktop: {exc}")
            QMessageBox.critical(None, "magi desktop", f"Could not start the backend:\n\n{exc}")
            return 1

    # 2) The frontend itself, as a loopback Node child. It ships no web/.env in a
    #    frozen build, so the shell passes every var its BFF needs explicitly.
    frontend_env = _frontend_env(backend)
    server = FrontendServer(
        config.desktop_web_dir,
        node_command=config.desktop_node_command,
        ready_timeout=config.desktop_server_ready_timeout,
        extra_env=frontend_env,
    )
    try:
        server.start()
    except FrontendServerError as exc:
        log_error(f"desktop: {exc}")
        if backend is not None:
            backend.stop()
        QMessageBox.critical(None, "magi desktop", f"Could not start the frontend:\n\n{exc}")
        return 1

    window = FramelessWindow(server.url_for(config.desktop_start_path), server.base_url)
    single = _install_single_instance_server(window)

    # Graceful shutdown: stop the children (frontend, then backend), persist
    # geometry, drop the listener.
    def _shutdown() -> None:
        window.save_geometry()
        single.close()
        QLocalServer.removeServer(_SINGLE_INSTANCE_KEY)
        server.stop()
        if backend is not None:
            backend.stop()

    app.aboutToQuit.connect(_shutdown)

    window.show_and_raise()
    return app.exec()

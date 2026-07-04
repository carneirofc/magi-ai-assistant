"""NativeBridge — the JS <-> Python contract exposed to the frontend.

Registered on a QWebChannel under the object name ``nativeBridge`` (see
``window.py``), so the page reaches it as ``window.nativeBridge`` once the
channel is up. The frontend is a third-party bundle we don't edit, so the channel
is wired by an *injected* script (``window.py``); a frontend that wants to use the
bridge just waits for the ``nativebridge:ready`` event — see the snippet in
``docs/desktop.md``.

The contract, kept deliberately small and documented:

  @Slot methods (JS -> Python, callable from the page):
    ping()                 -> "pong"                 liveness / channel check
    getAppInfo()           -> JSON string            app + platform + server info
    notify(title, body)    -> None                   native OS notification (a real
                                                      native action, per the brief)
    openExternal(url)      -> bool                    open a URL in the OS browser
    hideWindow()           -> None                    dismiss the frameless window
    closeApp()             -> None                    quit the app

  Signals (Python -> JS, subscribable from the page):
    messageReceived(str)   Python pushes a message to the frontend (demoed by a
                           one-shot welcome after load; call `push()` to emit more).
"""

from __future__ import annotations

import json
from collections.abc import Callable

from agno.utils.log import log_info
from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices


class NativeBridge(QObject):
    """Native capabilities surfaced to the web frontend over QWebChannel."""

    # Python -> JS push. JS subscribes via `nativeBridge.messageReceived.connect(fn)`.
    messageReceived = Signal(str)

    def __init__(
        self,
        *,
        app_name: str,
        app_version: str,
        server_url: str,
        notify: Callable[[str, str], None],
        hide_window: Callable[[], None],
        close_app: Callable[[], None],
    ) -> None:
        super().__init__()
        self._app_name = app_name
        self._app_version = app_version
        self._server_url = server_url
        self._notify = notify
        self._hide_window = hide_window
        self._close_app = close_app

    # --- JS -> Python ---------------------------------------------------------
    @Slot(result=str)
    def ping(self) -> str:
        """Liveness check — returns ``"pong"`` (proves the channel round-trips)."""
        return "pong"

    @Slot(result=str)
    def getAppInfo(self) -> str:
        """App + platform info as a JSON string (JS: ``JSON.parse(...)``)."""
        import platform

        return json.dumps(
            {
                "name": self._app_name,
                "version": self._app_version,
                "serverUrl": self._server_url,
                "platform": platform.system(),
                "platformRelease": platform.release(),
            }
        )

    @Slot(str, str)
    def notify(self, title: str, body: str) -> None:
        """Show a native OS notification (a genuine native action from the page)."""
        self._notify(title, body)

    @Slot(str, result=bool)
    def openExternal(self, url: str) -> bool:
        """Open ``url`` in the OS default browser; returns whether it was handed off."""
        return bool(QDesktopServices.openUrl(QUrl(url)))

    @Slot()
    def hideWindow(self) -> None:
        """Hide the frameless window (there's no OS title bar to do it)."""
        self._hide_window()

    @Slot()
    def closeApp(self) -> None:
        """Quit the whole app from the page."""
        self._close_app()

    # --- Python -> JS ---------------------------------------------------------
    def push(self, message: str) -> None:
        """Emit ``messageReceived`` to any JS subscriber (Python-initiated push)."""
        log_info(f"desktop: bridge push -> {message!r}")
        self.messageReceived.emit(message)

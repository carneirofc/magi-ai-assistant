"""FramelessWindow — the translucent, widget-style shell around the web content.

A borderless ``Qt.Tool`` window (off the taskbar / Alt-Tab) that paints a rounded,
semi-transparent panel and fills it with a chromeless ``QWebEngineView``. Since
there's no OS title bar it wires its own affordances: drag from the translucent
border, a corner ``QSizeGrip`` to resize, a small hide button, a right-click menu,
and a tray icon (Show / Hide / Quit) that doubles as the native-notification host.

The QWebChannel bridge is injected without touching the frontend: an injected
``QWebEngineScript`` loads Qt's ``qwebchannel.js`` and exposes the bridge as
``window.nativeBridge`` at document-creation time (see ``docs/desktop.md`` for the
frontend snippet). Window position/size persist via ``QSettings``.
"""

from __future__ import annotations

from agno.utils.log import log_info, log_warning
from PySide6.QtCore import QFile, QIODevice, QPoint, QRect, QRectF, QSettings, Qt
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QGuiApplication,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QRegion,
    QResizeEvent,
)
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineScript
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QMenu,
    QPushButton,
    QSizeGrip,
    QSystemTrayIcon,
    QWidget,
)

from magi.core.config import config
from magi.desktop.bridge import NativeBridge

_SETTINGS_ORG = "magi"
_SETTINGS_APP = "desktop-shell"
_APP_NAME = "magi desktop"
# Grab zone (px from a window edge) for frameless edge/corner resizing.
_RESIZE_BORDER = 8


def _qwebchannel_js() -> str:
    """Read Qt's bundled ``qwebchannel.js`` from the compiled-in resource.

    Injecting it (rather than asking the frontend to ship it) is what lets the
    bridge work against a third-party bundle unchanged. Empty string on failure —
    a page that loads its own copy still works.
    """
    f = QFile(":/qtwebchannel/qwebchannel.js")
    if not f.open(QIODevice.OpenModeFlag.ReadOnly):
        log_warning("desktop: could not read bundled qwebchannel.js resource")
        return ""
    try:
        return bytes(f.readAll().data()).decode("utf-8")
    finally:
        f.close()


# Wires the channel as soon as the document exists and announces readiness. A
# frontend opts in by listening for `nativebridge:ready` (see docs/desktop.md).
_BRIDGE_INIT_JS = """
(function () {
  function init() {
    new QWebChannel(qt.webChannelTransport, function (channel) {
      window.nativeBridge = channel.objects.nativeBridge;
      window.dispatchEvent(new CustomEvent('nativebridge:ready'));
    });
  }
  if (window.qt && window.qt.webChannelTransport) {
    init();
  } else {
    document.addEventListener('DOMContentLoaded', function () {
      if (window.qt && window.qt.webChannelTransport) init();
    });
  }
})();
"""


class FramelessWindow(QWidget):
    """The frameless, translucent shell hosting the web frontend."""

    def __init__(self, url: str, server_url: str) -> None:
        super().__init__()
        self._server_url_cache = server_url
        self._margin = config.desktop_window_margin
        self._radius = config.desktop_window_radius
        self._frameless = config.desktop_frameless
        self._drag_pos: QPoint | None = None
        # Manual-resize fallback state (used only if native startSystemResize fails).
        self._resize_edges = Qt.Edge(0)
        self._resize_start_geom = self.geometry()
        self._resize_start_mouse = QPoint()
        self._settings = QSettings(_SETTINGS_ORG, _SETTINGS_APP)

        self.setWindowTitle(_APP_NAME)
        # Floor the window size; an app with a fixed-frame desktop-only web layout
        # raises this (via config) to its supported minimum so the shell never
        # shrinks into a "window too small" state.
        self.setMinimumSize(
            config.desktop_window_min_width, config.desktop_window_min_height
        )
        if self._frameless:
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            # Hover events (no button held) so the border can show a resize cursor.
            self.setMouseTracking(True)
        else:
            self._margin = 0  # a normal titled window needs no drag border

        self._tray = self._build_tray()
        self._bridge = self._build_bridge()
        self._view = self._build_web_view(url)
        self._close_btn = self._build_close_button()

        # Resize handle in the bottom-right corner (in addition to edge resizing).
        self._grip = QSizeGrip(self)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        self._restore_geometry()

    # --- construction helpers -------------------------------------------------
    def _build_tray(self) -> QSystemTrayIcon | None:
        """A tray icon for Show/Hide/Quit + native notifications; None if unavailable."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        icon = self.style().standardIcon(self.style().StandardPixmap.SP_ComputerIcon)
        tray = QSystemTrayIcon(icon, self)
        tray.setToolTip(_APP_NAME)
        # Parent the menu to self: QSystemTrayIcon.setContextMenu does NOT take
        # ownership, so a local would be garbage-collected out from under the tray.
        menu = QMenu(self)
        menu.addAction("Show", self.show_and_raise)
        menu.addAction("Hide", self.hide)
        menu.addSeparator()
        menu.addAction("Quit", self._quit)
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        return tray

    def _build_bridge(self) -> NativeBridge:
        return NativeBridge(
            app_name=_APP_NAME,
            app_version=self._app_version(),
            server_url=self._server_url_cache,
            notify=self._notify,
            hide_window=self.hide,
            close_app=self._quit,
        )

    def _build_web_view(self, url: str) -> QWebEngineView:
        # A *named* profile persists cookies/cache, so the frontend's session
        # cookie survives restarts (log in once). Off-the-record would not.
        profile = QWebEngineProfile(_SETTINGS_APP, self)

        # Inject qwebchannel.js + the channel bootstrap at document creation, so
        # the (unmodified) frontend gets window.nativeBridge without any edits.
        script = QWebEngineScript()
        script.setName("magi-native-bridge")
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        script.setSourceCode(_qwebchannel_js() + "\n" + _BRIDGE_INIT_JS)
        profile.scripts().insert(script)

        view = QWebEngineView(self)
        page = QWebEnginePage(profile, view)
        # Let the page background show the translucent panel through where it can.
        page.setBackgroundColor(QColor(Qt.GlobalColor.transparent))
        # Let the frontend start audio without a prior click — the companion's
        # spoken greeting (auto-speak TTS) plays on open, before any gesture.
        # This is our own loopback page, not arbitrary web content.
        from PySide6.QtWebEngineCore import QWebEngineSettings

        page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False
        )
        view.setPage(page)

        channel = QWebChannel(page)
        channel.registerObject("nativeBridge", self._bridge)
        page.setWebChannel(channel)

        self._wire_mic_permission(page)

        view.setUrl(url)
        # One-shot Python -> JS push once the page is up, to demo the signal path.
        view.loadFinished.connect(self._on_load_finished)
        return view

    def _wire_mic_permission(self, page: QWebEnginePage) -> None:
        """Auto-grant microphone capture to the app's own loopback frontend.

        The shell renders a frontend it launched itself on 127.0.0.1, so a mic
        prompt would be asking the user to trust our own page — and a frameless
        window has no chrome to even show the prompt. Grant `MediaAudioCapture`
        for loopback origins (that's what `getUserMedia({audio})` needs for
        recorded speech → /v1/stt); everything else keeps the default deny, the
        same behavior as having no handler at all.

        Qt 6.8 replaced `featurePermissionRequested` with `permissionRequested`
        (QWebEnginePermission); handle whichever this PySide6 exposes.
        """

        def _is_loopback(origin) -> bool:
            return origin.host() in ("127.0.0.1", "localhost", "::1")

        if hasattr(page, "permissionRequested"):  # Qt >= 6.8
            from PySide6.QtWebEngineCore import QWebEnginePermission

            def _on_permission(permission) -> None:
                mic = QWebEnginePermission.PermissionType.MediaAudioCapture
                if permission.permissionType() == mic and _is_loopback(permission.origin()):
                    permission.grant()
                else:
                    permission.deny()

            page.permissionRequested.connect(_on_permission)
        else:  # Qt < 6.8

            def _on_feature(origin, feature) -> None:
                allowed = (
                    feature == QWebEnginePage.Feature.MediaAudioCapture
                    and _is_loopback(origin)
                )
                page.setFeaturePermission(
                    origin,
                    feature,
                    QWebEnginePage.PermissionPolicy.PermissionGrantedByUser
                    if allowed
                    else QWebEnginePage.PermissionPolicy.PermissionDeniedByUser,
                )

            page.featurePermissionRequested.connect(_on_feature)

    def _build_close_button(self) -> QPushButton:
        btn = QPushButton("×", self)
        btn.setToolTip("Hide (right-click the border for more)")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedSize(22, 22)
        btn.setStyleSheet(
            "QPushButton{border:none;border-radius:11px;color:#dfe3ea;"
            "background:rgba(120,124,138,0.35);font-size:15px;}"
            "QPushButton:hover{background:rgba(220,80,90,0.85);color:white;}"
        )
        btn.clicked.connect(self.hide)
        return btn

    # --- geometry / layout ----------------------------------------------------
    def _default_geometry(self) -> None:
        w, h = config.desktop_window_width, config.desktop_window_height
        self.resize(w, h)
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            # Bottom-right corner, widget-style.
            self.move(area.right() - w - 24, area.bottom() - h - 24)

    def _restore_geometry(self) -> None:
        saved = self._settings.value("geometry")
        if saved is not None and self.restoreGeometry(saved):
            return
        self._default_geometry()

    def save_geometry(self) -> None:
        """Persist the current position/size for the next launch."""
        self._settings.setValue("geometry", self.saveGeometry())

    def resizeEvent(self, event: QResizeEvent) -> None:
        m = self._margin
        inner = self.rect().adjusted(m, m, -m, -m)
        self._view.setGeometry(inner)
        if self._frameless and self._radius > 0:
            # Clip the web view to rounded corners matching the painted panel.
            path = QPainterPath()
            path.addRoundedRect(QRectF(self._view.rect()), self._radius, self._radius)
            self._view.setMask(QRegion(path.toFillPolygon().toPolygon()))
        # Pin the close button to the top-right of the content, the grip to bottom-right.
        self._close_btn.move(inner.right() - self._close_btn.width() - 4, inner.top() + 4)
        self._close_btn.raise_()
        self._grip.move(self.rect().right() - self._grip.width(), self.rect().bottom() - self._grip.height())
        self._grip.raise_()
        super().resizeEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        if not self._frameless:
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        painter.fillPath(path, QColor(26, 28, 34, 235))  # dark, semi-transparent panel
        painter.setPen(QColor(255, 255, 255, 28))
        painter.drawPath(path)

    # --- dragging + resizing (no title bar) ----------------------------------
    def _edges_at(self, pos: QPoint) -> Qt.Edge:
        """Which window edges the point is within the grab border of (0 if none)."""
        if not self._frameless or self.isMaximized() or self.isFullScreen():
            return Qt.Edge(0)
        r = self.rect()
        b = _RESIZE_BORDER
        edges = Qt.Edge(0)
        if pos.x() <= r.left() + b:
            edges |= Qt.Edge.LeftEdge
        elif pos.x() >= r.right() - b:
            edges |= Qt.Edge.RightEdge
        if pos.y() <= r.top() + b:
            edges |= Qt.Edge.TopEdge
        elif pos.y() >= r.bottom() - b:
            edges |= Qt.Edge.BottomEdge
        return edges

    @staticmethod
    def _cursor_for_edges(edges: Qt.Edge) -> Qt.CursorShape:
        left, right = Qt.Edge.LeftEdge, Qt.Edge.RightEdge
        top, bottom = Qt.Edge.TopEdge, Qt.Edge.BottomEdge
        if edges in (top | left, bottom | right):
            return Qt.CursorShape.SizeFDiagCursor
        if edges in (top | right, bottom | left):
            return Qt.CursorShape.SizeBDiagCursor
        if edges & (left | right):
            return Qt.CursorShape.SizeHorCursor
        if edges & (top | bottom):
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def _in_drag_zone(self, pos: QPoint) -> bool:
        """True when the press is on the translucent border, not over the web view."""
        return self._frameless and not self._view.geometry().contains(pos)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            edges = self._edges_at(pos)
            if edges != Qt.Edge(0):
                handle = self.windowHandle()
                if handle is not None and handle.startSystemResize(edges):
                    return  # OS drives the resize
                # Fallback: manual resize from the captured start geometry.
                self._resize_edges = edges
                self._resize_start_geom = self.geometry()
                self._resize_start_mouse = event.globalPosition().toPoint()
                return
            if self._in_drag_zone(pos):
                handle = self.windowHandle()
                if handle is not None and handle.startSystemMove():
                    self._drag_pos = None  # OS drives the move
                    return
                # Fallback: manual move relative to the press point.
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._resize_edges != Qt.Edge(0) and event.buttons() & Qt.MouseButton.LeftButton:
            self._resize_to(event.globalPosition().toPoint())
            return
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            return
        if self._frameless and not (event.buttons() & Qt.MouseButton.LeftButton):
            # Hover feedback: show the resize cursor over the grab border.
            self.setCursor(self._cursor_for_edges(self._edges_at(event.position().toPoint())))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_pos = None
        self._resize_edges = Qt.Edge(0)
        super().mouseReleaseEvent(event)

    def _resize_to(self, mouse_global: QPoint) -> None:
        """Manual-resize fallback: grow/shrink from the captured start geometry."""
        dx = mouse_global.x() - self._resize_start_mouse.x()
        dy = mouse_global.y() - self._resize_start_mouse.y()
        g = QRect(self._resize_start_geom)
        minw, minh = self.minimumWidth(), self.minimumHeight()
        if self._resize_edges & Qt.Edge.LeftEdge:
            g.setLeft(min(g.left() + dx, g.right() - minw))
        elif self._resize_edges & Qt.Edge.RightEdge:
            g.setRight(max(g.right() + dx, g.left() + minw))
        if self._resize_edges & Qt.Edge.TopEdge:
            g.setTop(min(g.top() + dy, g.bottom() - minh))
        elif self._resize_edges & Qt.Edge.BottomEdge:
            g.setBottom(max(g.bottom() + dy, g.top() + minh))
        self.setGeometry(g)

    # --- menu / tray / lifecycle ---------------------------------------------
    def _show_context_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        menu.addAction("Hide", self.hide)
        menu.addAction("Quit", self._quit)
        menu.exec(self.mapToGlobal(pos))

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            if self.isVisible():
                self.hide()
            else:
                self.show_and_raise()

    def show_and_raise(self) -> None:
        """Show, un-minimize, and focus the window (used by the tray + 2nd launch)."""
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def _notify(self, title: str, body: str) -> None:
        if self._tray is not None:
            self._tray.showMessage(title or _APP_NAME, body)
        else:
            log_info(f"desktop: notify (no tray) — {title}: {body}")

    def _on_load_finished(self, ok: bool) -> None:
        if ok:
            self._bridge.push(f"Connected to {self._server_url_cache}")

    def _quit(self) -> None:
        self.save_geometry()
        QApplication.quit()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.save_geometry()
        super().closeEvent(event)

    # --- small helpers --------------------------------------------------------
    @staticmethod
    def _app_version() -> str:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("magi")
        except PackageNotFoundError:
            return "0.0.0"

    def bridge(self) -> NativeBridge:
        """The live bridge (for Python-side pushes)."""
        return self._bridge

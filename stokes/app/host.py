"""The desktop host: a frameless Qt window with a bundled Chromium view (ADR-0013).

The window shows the web front end served by the in-process server. A small
QWebChannel object gives the page what a frameless window needs: move, resize,
minimise, maximise, close, the native file and folder choosers (a page cannot
open them with a real path) and, for the paths the user picked there, reading and
writing one small text file (``stokes.app.textfiles`` holds the rules). Everything
else is the web app.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QFileDialog, QMainWindow, QWidget

from stokes.app.icon import load_icon
from stokes.app.textfiles import PickedTextFiles

PREFERRED_SIZE = (1480, 920)
SCREEN_MARGIN = 12

EDGES = {
    "left": Qt.Edge.LeftEdge,
    "right": Qt.Edge.RightEdge,
    "top": Qt.Edge.TopEdge,
    "bottom": Qt.Edge.BottomEdge,
    "top-left": Qt.Edge.TopEdge | Qt.Edge.LeftEdge,
    "top-right": Qt.Edge.TopEdge | Qt.Edge.RightEdge,
    "bottom-left": Qt.Edge.BottomEdge | Qt.Edge.LeftEdge,
    "bottom-right": Qt.Edge.BottomEdge | Qt.Edge.RightEdge,
}


class HostBridge(QObject):
    """Exposed to the page as ``host`` through QWebChannel."""

    maximizedChanged = Signal(bool)  # noqa: N815 - Qt signal naming

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self._window = window
        self._files = PickedTextFiles()

    @Slot()
    def startMove(self) -> None:  # noqa: N802 - JavaScript-facing name
        handle = self._window.windowHandle()
        if handle is not None:
            handle.startSystemMove()

    @Slot(str)
    def startResize(self, edge: str) -> None:  # noqa: N802
        handle = self._window.windowHandle()
        edges = EDGES.get(edge)
        if handle is not None and edges is not None:
            handle.startSystemResize(edges)

    @Slot()
    def minimize(self) -> None:
        self._window.showMinimized()

    @Slot()
    def toggleMaximize(self) -> None:  # noqa: N802
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()
        self.maximizedChanged.emit(self._window.isMaximized())

    @Slot(result=bool)
    def isMaximized(self) -> bool:  # noqa: N802
        return bool(self._window.isMaximized())

    @Slot()
    def close(self) -> None:
        self._window.close()

    @Slot(str, str, result=str)
    def pickFolder(self, title: str, start: str) -> str:  # noqa: N802
        """Native chooser for an existing folder; empty when cancelled."""
        return str(QFileDialog.getExistingDirectory(self._window, title or "Open project", start))

    @Slot(str, str, result=str)
    def pickSaveFolder(self, title: str, start: str) -> str:  # noqa: N802
        """Native chooser for a new or existing .stokes folder; empty when cancelled."""
        path, _ = QFileDialog.getSaveFileName(
            self._window, title or "Save project as", start, "Stokes project (*.stokes)"
        )
        return str(path)

    @Slot(str, str, str, result=str)
    def pickFile(self, title: str, start: str, name_filter: str) -> str:  # noqa: N802
        """Native chooser for an existing file; empty when cancelled.

        ``name_filter`` uses Qt's syntax, e.g. ``"STEP files (*.step *.stp);;All files (*)"``.
        """
        path, _ = QFileDialog.getOpenFileName(
            self._window, title or "Open file", start, name_filter
        )
        self._files.allow_read(str(path))
        return str(path)

    @Slot(str, str, str, result=str)
    def pickSaveFile(self, title: str, start: str, name_filter: str) -> str:  # noqa: N802
        """Native chooser for a file to write; empty when cancelled.

        ``start`` may be a folder or a suggested file name. The chosen path may be written
        with :meth:`writeTextFile` for the rest of the session, and only that path.
        """
        path, _ = QFileDialog.getSaveFileName(
            self._window, title or "Save file", start, name_filter
        )
        self._files.allow_write(str(path))
        return str(path)

    @Slot(str, str, result=bool)
    def writeTextFile(self, path: str, text: str) -> bool:  # noqa: N802
        """Write UTF-8 text to a path the user picked with :meth:`pickSaveFile`.

        False for any other path, for text over 16 MB and for a disk error.
        """
        return self._files.write_text(path, text)

    @Slot(str, result=str)
    def readTextFile(self, path: str) -> str:  # noqa: N802
        """The UTF-8 text of a file the user picked with :meth:`pickFile`.

        Empty for any other path, for a file over 16 MB, for one that is not UTF-8 and for a
        disk error.
        """
        return self._files.read_text(path)


class HostWindow(QMainWindow):
    def __init__(self, url: str, *, frameless: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Stokes")
        self.setWindowIcon(load_icon())
        # Fit the preferred size into the primary screen's work area and centre it there. A
        # window taller than the work area is shrunk by Qt to 80 % on Windows, which made the
        # start-up size (and every review screenshot) depend on the display.
        width, height = PREFERRED_SIZE
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            width = min(width, available.width() - 2 * SCREEN_MARGIN)
            height = min(height, available.height() - 2 * SCREEN_MARGIN)
        self.resize(width, height)
        if screen is not None:
            self.move(screen.availableGeometry().center() - self.rect().center())
        if frameless:
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.view = QWebEngineView(self)
        settings = self.view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
        self.channel = QWebChannel(self)
        self.bridge = HostBridge(self)
        self.channel.registerObject("host", self.bridge)
        self.view.page().setWebChannel(self.channel)
        self.setCentralWidget(self.view)
        self.view.setUrl(QUrl(url))

    def run_on_load(self, script: str) -> None:
        """Run ``script`` in the page once it has loaded (T-088), for a review capture.

        The host has no other way to drive the interface: the bridge goes from the page to the
        host, never back. A capture needs state the page can only reach through its own stores, so
        the script is the caller's (a file given with ``--setup``) and runs in the page's world,
        where a development or review build exposes ``window.__stokes``.
        """
        page = self.view.page()

        def loaded(ok: bool) -> None:
            if ok:
                page.runJavaScript(script)

        page.loadFinished.connect(loaded)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        # Snapping, the title-bar keys and the taskbar change the state without the bridge.
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self.bridge.maximizedChanged.emit(self.isMaximized())

    def screenshot(self, path: str) -> QPixmap:
        """Save the rendered page; returns the pixmap so callers can report its size."""
        pixmap = self.view.grab()
        pixmap.save(path)
        return pixmap

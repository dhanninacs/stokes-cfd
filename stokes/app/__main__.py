"""``python -m stokes.app``: start the local server and open the Stokes window.

Options: ``--project PATH`` (default: the start screen, no project open),
``--theme`` (``auto``, ``dark``,
``light``, ``cupertino``, ``cupertino-dark``, ``cupertino-auto``, ``ubuntu``, ``ubuntu-dark``,
``ubuntu-auto``; without it, the theme of the user's workspace, see :func:`workspace_theme`),
``--dev`` (load the Vite dev server on port 5173 instead of ``web/dist``),
``--browser`` (open the system browser, no Qt window), ``--no-frameless``,
``--screenshot PATH``, ``--setup PATH`` (a JavaScript file run in the page once it loads, so a
capture can show something other than the opening state; a development or review build is what it
can reach, since those expose the stores) and ``--exit-after MS``.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING

from stokes import workspace_root
from stokes.errors import StokesError
from stokes.log import fields, get_logger

if TYPE_CHECKING:
    from stokes.api import Session

DEV_URL = "http://127.0.0.1:5173"
THEMES = (
    "auto",
    "dark",
    "light",
    "cupertino",
    "cupertino-dark",
    "cupertino-auto",
    "ubuntu",
    "ubuntu-dark",
    "ubuntu-auto",
)
log = get_logger(__name__)

DEFAULT_THEME = "auto"
LINUX_DEFAULT_THEME = "ubuntu-auto"
"""The theme used when no theme was ever chosen, on Linux and elsewhere.

On Linux the desktop is a Yaru/Ubuntu look by default, so a fresh window matches it; every other
platform keeps the Stokes family's ``auto``. This is the *no-choice* fallback only: an explicit
``--theme``, and any theme the user's or the project's workspace actually records, win over it
(:func:`chosen_theme` tells those apart from the untouched default).
"""


def default_theme() -> str:
    """The family to prefer when no theme is chosen: Ubuntu's on Linux, Stokes' elsewhere."""
    return LINUX_DEFAULT_THEME if sys.platform.startswith("linux") else DEFAULT_THEME


def chosen_theme(session: Session) -> str | None:
    """The theme a workspace file records, or None when no one has chosen one.

    ``workspace.get`` reports the layer each value came from, which is what makes this exact: a
    fresh install and a user who explicitly picked *Stokes + Auto* both hold ``{"name": "auto"}``,
    but only the second has ``source == "user"``. Returning None for the default is what lets
    :func:`default_theme` apply on Linux without discarding a real choice — reading the value
    alone cannot tell the two apart.

    A custom theme goes by its base, beyond which its tokens follow with the document. Whatever
    cannot be read gives None, leaving the choice to the fallback and then to the document.
    """
    try:
        answer = session.execute("workspace.get", path="theme")
    except StokesError as exc:
        log.warning("the workspace theme could not be read", extra=fields(code=exc.code))
        return None
    if not isinstance(answer, dict) or answer.get("source") == "default":
        return None
    value = answer.get("value")
    if isinstance(value, dict):
        name = value.get("base") or value.get("name")
        if isinstance(name, str) and name in THEMES:
            return name
    return None


def workspace_theme(session: Session) -> str:
    """The theme the workspace asks for, as a value of the page's ``?theme=``.

    The page applies the workspace's theme when its document arrives, a round trip after the
    first paint, so a user whose theme is not ``auto`` would see the default flash by. Passing
    the theme in the address lets the first paint be right.

    With no theme chosen at all this is ``auto``, which leaves the choice to the document; the
    caller substitutes :func:`default_theme` for it when it wants the platform's own family.
    """
    return chosen_theme(session) or DEFAULT_THEME


def _ensure_streams() -> None:
    """Give a console-less launch somewhere to write.

    Started from a desktop shortcut through ``pythonw``, the process has no console, so
    ``sys.stdout`` and ``sys.stderr`` are None and the first log line from the server
    (or anything that probes ``isatty``) raises. Both then go to an appended log file,
    ``<STOKES_HOME or ~/.stokes>/logs/app.log``, which is also where to look when the
    window does not appear.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    home = workspace_root.home()
    log_dir = home / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stream = (log_dir / "app.log").open("a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream
    # Loggers configured during module imports (stokes' own, and any library's) bound their
    # handlers to the missing stream before main() ran: point every such handler at the file.
    loggers = [logging.getLogger()] + [
        logger
        for logger in logging.Logger.manager.loggerDict.values()
        if isinstance(logger, logging.Logger)
    ]
    for logger in loggers:
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler) and handler.stream is None:
                handler.setStream(stream)


def main(argv: list[str] | None = None) -> int:
    _ensure_streams()
    parser = argparse.ArgumentParser(prog="stokes-app")
    parser.add_argument("--project")
    parser.add_argument("--theme", choices=THEMES)
    parser.add_argument("--dev", action="store_true")
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--no-frameless", action="store_true")
    parser.add_argument("--screenshot")
    parser.add_argument(
        "--setup",
        help="a JavaScript file to run in the page once it loads, before --screenshot",
    )
    parser.add_argument(
        "--drive",
        action="store_true",
        help="expose the page's stores to a script, so a harness can drive this window (T-065)",
    )
    parser.add_argument("--exit-after", type=int, default=0, help="milliseconds")
    args = parser.parse_args(argv)

    import stokes.app.demo  # noqa: F401  (registers the Flanged pipe sample's builder)
    from stokes.api import Session
    from stokes.server.app import create_app, new_token
    from stokes.server.runner import ServerThread

    # Without --project no project is opened: the window shows the start screen, so a
    # fresh launch never loads the demo case on its own.
    session = Session()
    if args.project:
        session.execute("project.open", path=args.project)
    token = new_token()
    # A theme given on the command line is for this run only (review captures stay comparable);
    # without one the first paint already has the workspace's theme. When no theme was ever
    # chosen, Linux prefers the Ubuntu family (default_theme), so a fresh window matches the
    # desktop; a theme the user did choose — including Stokes' own "auto" — is used as it is.
    theme = args.theme or chosen_theme(session) or default_theme()

    extra_files: dict[str, tuple[bytes, str]] = {}
    if not args.browser:
        extra_files["/qwebchannel.js"] = (_qwebchannel_js(), "application/javascript")

    server = ServerThread(create_app(session, token=token, extra_files=extra_files))
    api_url = server.start()
    base = DEV_URL if args.dev else api_url
    # `drive=1` exposes the page's stores (`App.tsx`), which is what a harness needs to READ
    # state while it drives; without it the window is a user's window and a script can only look at
    # pixels. `debug=1` adds the scene probe. Both are opt-in per load, never on for a user.
    page = f"{base}/?token={token}&theme={theme}&api={api_url}"
    if args.drive:
        page += "&drive=1&debug=1"

    if args.browser:
        sys.stdout.write(page + "\n")
        webbrowser.open(page)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            server.stop()
        return 0

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from stokes.app.host import HostWindow
    from stokes.app.icon import load_icon, set_app_user_model_id, set_desktop_file_name

    # Before the first window exists: without a name of its own, the taskbar files a pythonw window
    # under another Python program's button and icon.
    set_app_user_model_id()
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication(sys.argv[:1])
    app.setApplicationName("Stokes")
    # The Linux twin of the call above, and also before the first window: it is the Wayland app_id
    # the dock matches a window to a .desktop file with, else the interpreter's name ("python3").
    set_desktop_file_name()
    app.setWindowIcon(load_icon())
    window = HostWindow(
        page + ("&drive=1" if args.setup else "") + "&host=qt", frameless=not args.no_frameless
    )
    window.show()
    window.raise_()
    window.activateWindow()

    if args.setup:
        # A capture of anything but the opening state: the script drives the page before the
        # shutter (T-088). It runs in the page, so a development or review build — the ones that
        # expose `window.__stokes` — is what it can reach.
        try:
            window.run_on_load(Path(args.setup).read_text(encoding="utf-8"))
        except OSError as exc:
            sys.stderr.write(f"setup {args.setup}: {exc}\n")
            return 2
        sys.stdout.write(f"setup {args.setup}: will run in the page once it loads\n")

    if args.screenshot:

        def _shoot() -> None:
            pixmap = window.screenshot(args.screenshot)
            size, screen = window.size(), window.screen()
            geometry = screen.availableGeometry()
            sys.stdout.write(
                f"screenshot {args.screenshot}: window {size.width()}x{size.height()} logical, "
                f"dpr {window.devicePixelRatioF():.2f}, "
                f"image {pixmap.width()}x{pixmap.height()} px, "
                f"screen {screen.name()} {geometry.width()}x{geometry.height()} available\n"
            )
            app.quit()

        QTimer.singleShot(args.exit_after or 6000, _shoot)
    elif args.exit_after:
        QTimer.singleShot(args.exit_after, app.quit)
    code = int(app.exec())
    server.stop()
    return code


def _qwebchannel_js() -> bytes:
    """Qt's client script, read from the Qt resource system."""
    from PySide6.QtCore import QFile, QIODevice
    from PySide6.QtWebChannel import QWebChannel  # noqa: F401 - registers the resource

    file = QFile(":/qtwebchannel/qwebchannel.js")
    if not file.open(QIODevice.OpenModeFlag.ReadOnly):
        return b"// qwebchannel.js unavailable\n"
    try:
        return bytes(file.readAll().data())
    finally:
        file.close()


if __name__ == "__main__":
    sys.exit(main())

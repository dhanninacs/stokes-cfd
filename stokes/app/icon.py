"""The Stokes icon for the window, the application and the desktop shell.

``assets/stokes.ico`` (16 to 256 px) is the icon of the desktop shortcut; its 256-px twin
``assets/stokes.png`` is the page's favicon (``web/public/favicon.png``). The Qt windows take the
same file, found beside this package whatever the working directory is, so the title bar, Alt-Tab,
the taskbar and the shortcut show one mark.

Both desktop shells also want a *name* for the process, and each spells it differently. Windows
(:data:`APP_USER_MODEL_ID`, :func:`set_app_user_model_id`) groups the window by interpreter
without it, so a window of ``pythonw`` sits under the button, the name and the icon of another
program on that interpreter. Linux/Wayland (``app_id``, :func:`set_desktop_file_name`) matches a
window to a ``.desktop`` file by that name; without it the compositor is told the interpreter's
name — measured here as ``"python3"`` — so the dock shows a generic entry rather than Stokes. The
``.desktop`` file itself is M8 packaging and is not in this tree.

Nothing here may stop the app from starting. A missing file or a refused call is a logged warning
and the app runs on with the default icon.

Import rule: Qt is imported inside :func:`load_icon` and :func:`set_desktop_file_name`, so this
module loads without it.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from stokes.log import fields, get_logger

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon

APP_USER_MODEL_ID = "Stokes.Desktop"
"""The taskbar identity the process gives itself at start-up, through :func:`set_app_user_model_id`.

That is what earns the running window its own taskbar button under the Stokes icon instead of a
button borrowed from the interpreter. A **pinned** button shares it only when the pinned shortcut
carries the same ``System.AppUserModel.ID``. Nothing in this repository writes that property: a
hand-made ``.lnk`` — one made with the shortcut wizard or by hand — does not have it, and its
pinned button may then show the interpreter's icon. To get a pinned button that joins the running
window, pin the running window itself (right-click the taskbar button, *Pin to taskbar*): Windows
records the app's own identity as it pins. See ``docs/HANDOVER.md`` §7, "The app icon, the taskbar
and shortcuts"."""

DESKTOP_FILE_NAME = "Stokes"
"""The Linux application name (:func:`set_desktop_file_name`), the ``.desktop`` file's basename.

Wayland carries it to the compositor as the window's ``app_id``, and the shell matches a running
window to ``Stokes.desktop`` by it. M8's Linux installer is what will ship that file; until then
the name at least stops the window being filed under the interpreter, and a launcher the owner
writes by hand can use the same basename. It is the Linux twin of :data:`APP_USER_MODEL_ID`.
"""

ICON_PATH = Path(__file__).resolve().parent / "assets" / "stokes.ico"

log = get_logger(__name__)


def _platform_is(*names: str) -> bool:
    """Whether :data:`sys.platform` is one of ``names``.

    A *call* on purpose. Writing the test inline (``if sys.platform not in ("linux", "freebsd"):
    return False``) makes mypy delete everything after it when it runs on the other platform, and
    reports an unreachable statement — one error on Linux for the Windows guard, another on Windows
    for the Linux one. Behind a call mypy cannot narrow, so both platforms type-check clean.
    """
    return sys.platform in names


def load_icon() -> QIcon:
    """The Stokes icon with all its sizes; a null icon, after a warning, when it cannot be read.

    Needs a running Qt application: Qt aborts the process when an icon is made without one.
    """
    from PySide6.QtGui import QIcon

    if not ICON_PATH.is_file():
        log.warning("the application icon is missing", extra=fields(path=str(ICON_PATH)))
        return QIcon()
    icon = QIcon(str(ICON_PATH))
    if icon.isNull():
        log.warning("the application icon could not be read", extra=fields(path=str(ICON_PATH)))
    return icon


def set_app_user_model_id(app_id: str = APP_USER_MODEL_ID) -> bool:
    """Name this process for the Windows taskbar; True when Windows took the name.

    The taskbar then groups the windows of the process as one app under their own icon. A pinned
    shortcut joins that button only when the shortcut itself carries ``System.AppUserModel.ID``
    set to the same name, and nothing here writes that property into a shortcut; see
    :data:`APP_USER_MODEL_ID`. Call it before the first window exists.
    Off Windows it does nothing and returns False. A failure is logged and never raised.
    """
    if not _platform_is("win32"):
        return False
    try:
        import ctypes

        # `windll` is a Windows-only ctypes attribute and mypy is run on Linux; `getattr` keeps
        # this source type-checking on both hosts.
        windll = vars(ctypes)["windll"]
        result = int(windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id))
    except Exception:  # no ctypes, no shell32 or a bad call: the taskbar keeps Python's icon
        log.warning(
            "the taskbar identity could not be set", extra=fields(app_id=app_id), exc_info=True
        )
        return False
    if result != 0:  # an HRESULT; zero is S_OK
        log.warning(
            "Windows refused the taskbar identity",
            extra=fields(app_id=app_id, hresult=hex(result & 0xFFFFFFFF)),
        )
        return False
    return True


def set_desktop_file_name(name: str = DESKTOP_FILE_NAME) -> bool:
    """Name this process for the Linux desktop shell; True when Qt took the name.

    Wayland's ``app_id`` is what a dock or taskbar matches a window to a ``.desktop`` file with.
    Qt takes it from :func:`QGuiApplication.setDesktopFileName` when set, and otherwise derives it
    from the application or executable name — measured on this machine as ``"python3"``, which is
    why the dock shows a generic entry for Stokes. Setting the name here gives the window the
    Stokes identity; the ``.desktop`` file that the shell then looks up is M8 packaging.

    Needs a running Qt application, because it touches ``QGuiApplication``. Call it before the
    first window exists, beside :func:`set_app_user_model_id`. Off Linux it does nothing and
    returns False. A failure is logged and never raised.
    """
    if not _platform_is("linux", "freebsd"):
        return False
    try:
        from PySide6.QtGui import QGuiApplication

        QGuiApplication.setDesktopFileName(name)
    except Exception:  # no QtGui, or no application yet: the shell keeps the interpreter's name
        log.warning(
            "the desktop file name could not be set", extra=fields(app_id=name), exc_info=True
        )
        return False
    return True

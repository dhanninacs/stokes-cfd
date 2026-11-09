"""The Stokes icon: its file, the Qt icon made from it, the taskbar identity and the favicon."""

from __future__ import annotations

import ctypes
import logging
import struct
import sys
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import stokes.app
from stokes.app import icon

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def logged() -> Iterator[list[logging.LogRecord]]:
    """The warnings of ``stokes.app.icon`` (the ``stokes`` logger does not propagate)."""
    records: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = Collect(level=logging.WARNING)
    logger = logging.getLogger("stokes.app.icon")
    logger.addHandler(handler)
    yield records
    logger.removeHandler(handler)


@pytest.fixture(scope="session")
def qt_app() -> Any:  # Any: PySide6 is optional, so its types are not imported here
    """A Qt application on the offscreen platform, made once; the test skips without PySide6.

    Qt aborts the process when an icon is made before an application exists.
    """
    widgets = pytest.importorskip("PySide6.QtWidgets")
    app = widgets.QApplication.instance()
    if app is None:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("QT_QPA_PLATFORM", "offscreen")
            app = widgets.QApplication([])
    return app


# ------------------------------------------------------------------------------------- the file
def test_the_icon_file_is_found_beside_the_package_whatever_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    package = Path(stokes.app.__file__).resolve().parent
    assert icon.ICON_PATH.relative_to(package) == Path("assets", "stokes.ico")
    assert icon.ICON_PATH.is_absolute()
    assert icon.ICON_PATH.is_file()


def test_the_icon_file_holds_every_size_windows_asks_for() -> None:
    data = icon.ICON_PATH.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data)
    assert (reserved, kind) == (0, 1)  # the header of an .ico file
    sizes = {data[6 + 16 * index] or 256 for index in range(count)}  # a width byte of 0 is 256
    assert {16, 32, 48, 256} <= sizes


@pytest.mark.ui
def test_the_icon_loads_as_a_qt_icon_with_its_sizes(qt_app: Any) -> None:
    qicon = icon.load_icon()
    assert not qicon.isNull()
    sizes = {(size.width(), size.height()) for size in qicon.availableSizes()}
    assert {(16, 16), (32, 32), (256, 256)} <= sizes
    assert not qicon.pixmap(32, 32).isNull()


@pytest.mark.ui
@pytest.mark.parametrize(
    ("content", "message"),
    [
        (None, "the application icon is missing"),
        (b"this is not an icon", "the application icon could not be read"),
    ],
)
def test_an_icon_that_cannot_be_loaded_is_a_null_icon_and_a_warning(
    qt_app: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    logged: list[logging.LogRecord],
    content: bytes | None,
    message: str,
) -> None:
    path = tmp_path / "stokes.ico"
    if content is not None:
        path.write_bytes(content)
    monkeypatch.setattr(icon, "ICON_PATH", path)
    assert icon.load_icon().isNull()
    assert [record.getMessage() for record in logged] == [message]


# ----------------------------------------------------------------------- the taskbar identity
@pytest.fixture
def shell32(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Stands in for ``ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID``."""
    call = MagicMock(return_value=0)
    windll = types.SimpleNamespace(
        shell32=types.SimpleNamespace(SetCurrentProcessExplicitAppUserModelID=call)
    )
    monkeypatch.setattr(ctypes, "windll", windll, raising=False)  # absent off Windows
    return call


def test_on_windows_the_process_is_named_for_the_taskbar(
    monkeypatch: pytest.MonkeyPatch, shell32: MagicMock
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    assert icon.set_app_user_model_id() is True
    shell32.assert_called_once_with("Stokes.Desktop")


def test_off_windows_the_taskbar_is_left_alone(
    monkeypatch: pytest.MonkeyPatch, shell32: MagicMock
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert icon.set_app_user_model_id() is False
    shell32.assert_not_called()


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (-2147024809, "Windows refused the taskbar identity"),  # E_INVALIDARG
        (OSError("shell32 did not load"), "the taskbar identity could not be set"),
        (AttributeError("no such function"), "the taskbar identity could not be set"),
    ],
)
def test_a_failed_call_is_a_warning_and_never_stops_the_start(
    monkeypatch: pytest.MonkeyPatch,
    shell32: MagicMock,
    logged: list[logging.LogRecord],
    failure: int | Exception,
    message: str,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    if isinstance(failure, Exception):
        shell32.side_effect = failure
    else:
        shell32.return_value = failure
    assert icon.set_app_user_model_id() is False
    assert [record.getMessage() for record in logged] == [message]


# ------------------------------------------------------------------ the Linux desktop file name
def _desktop_name_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture the names passed to ``QGuiApplication.setDesktopFileName``."""
    from PySide6.QtGui import QGuiApplication

    calls: list[str] = []
    monkeypatch.setattr(QGuiApplication, "setDesktopFileName", staticmethod(calls.append))
    return calls


@pytest.mark.ui
def test_on_linux_the_desktop_file_name_names_the_window(
    qt_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    calls = _desktop_name_calls(monkeypatch)
    assert icon.set_desktop_file_name() is True
    assert calls == [icon.DESKTOP_FILE_NAME]
    assert icon.DESKTOP_FILE_NAME == "Stokes"


def test_off_linux_the_desktop_file_name_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    calls = _desktop_name_calls(monkeypatch)
    assert icon.set_desktop_file_name() is False
    assert calls == []


@pytest.mark.ui
def test_a_failed_desktop_file_name_is_a_warning_and_never_stops_the_start(
    qt_app: Any, monkeypatch: pytest.MonkeyPatch, logged: list[logging.LogRecord]
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    def boom(name: str) -> None:
        raise RuntimeError("no QGuiApplication")

    from PySide6.QtGui import QGuiApplication

    monkeypatch.setattr(QGuiApplication, "setDesktopFileName", staticmethod(boom))
    assert icon.set_desktop_file_name() is False
    assert [record.getMessage() for record in logged] == ["the desktop file name could not be set"]


# --------------------------------------------------------------------------------- the favicon
def test_the_favicon_is_the_icons_png_twin_and_the_page_links_it() -> None:
    twin = icon.ICON_PATH.with_name("stokes.png").read_bytes()
    assert (ROOT / "web" / "public" / "favicon.png").read_bytes() == twin
    page = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert '<link rel="icon" type="image/png" href="/favicon.png"' in page

"""Text files the page may read and write through the host bridge (ADR-0013).

A web page cannot open a native file dialog with a real path, so the bridge opens the dialog
(``pickFile``, ``pickSaveFile``) and the page then moves one small text file: the workspace
export and import are the users. Letting a page read and write files is the dangerous half of
that, so this module is the policy and nothing else: no Qt, no dialog, easy to test.

* A path is allowed only after the user chose it in a native dialog during this session: a path
  from an open dialog may be read, one from a save dialog may be written, and neither may do
  the other. Every other path is refused, so the page cannot write (or read) anywhere it likes.
* Text is UTF-8 and at most :data:`MAX_TEXT_BYTES`. Anything that goes wrong (not allowed, not a
  file, too large, not UTF-8, a disk error) comes back as ``""`` or ``False``; the page cannot
  tell the reasons apart and so learns nothing about files it was not given.

The bridge runs on the window's own thread, so the cap is also what bounds that thread's wait;
the real users move a few kilobytes.
"""

from __future__ import annotations

import os
from pathlib import Path

from stokes.log import fields, get_logger

log = get_logger(__name__)

MAX_TEXT_BYTES = 1024 * 1024  # CLAUDE.md rule 7: no file IO above 1 MB on the UI thread
"""Largest text the page may read or write in one call."""


def _key(path: str) -> str:
    """The form in which a path is remembered and compared: absolute, resolved, case-folded on
    Windows. Qt reports ``C:/dir/file.json`` and the page sends it back as it got it."""
    return os.path.normcase(str(Path(path).resolve()))


class PickedTextFiles:
    """The paths the user has picked in this session and what may be done with each."""

    def __init__(self) -> None:
        self._readable: set[str] = set()
        self._writable: set[str] = set()

    def allow_read(self, path: str) -> None:
        """The user chose ``path`` in an open dialog: it may be read."""
        if path:
            self._readable.add(_key(path))

    def allow_write(self, path: str) -> None:
        """The user chose ``path`` in a save dialog: it may be written."""
        if path:
            self._writable.add(_key(path))

    def read_text(self, path: str) -> str:
        """The UTF-8 text of a picked file (a leading byte-order mark dropped), or ``""``."""
        if not self._permits(self._readable, path, "read"):
            return ""
        target = Path(path)
        try:
            if not target.is_file():
                return ""
            with target.open("rb") as handle:
                data = handle.read(MAX_TEXT_BYTES + 1)
            if len(data) > MAX_TEXT_BYTES:
                return ""
            return data.decode("utf-8-sig")
        except (OSError, UnicodeDecodeError):
            return ""

    def write_text(self, path: str, text: str) -> bool:
        """Write ``text`` as UTF-8 to a path picked for saving; whether it was written."""
        if not self._permits(self._writable, path, "write"):
            return False
        try:
            data = text.encode("utf-8")
        except UnicodeEncodeError:  # lone surrogates from the page
            return False
        if len(data) > MAX_TEXT_BYTES:
            return False
        try:
            Path(path).write_bytes(data)
        except OSError:
            return False
        return True

    def _permits(self, allowed: set[str], path: str, action: str) -> bool:
        if not path:
            return False
        try:
            permitted = _key(path) in allowed
        except (OSError, ValueError):  # a path the system cannot even resolve
            permitted = False
        if not permitted:
            log.warning(
                "the page asked for a file it was not given",
                extra=fields(action=action, path=path[:260]),
            )
        return permitted

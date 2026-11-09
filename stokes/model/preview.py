"""A project's thumbnail: `preview.png`, validated before it is written (start screen).

**Why this validates rather than trusts.** The bytes arrive from a browser, which means they arrive
from whatever the browser is running - and they are written into the user's project folder, which is
the one place another part of the app will later read. So this checks the signature, the dimensions
and the size **before** anything touches the disk, and it refuses rather than repairing.

**Why the dimensions are bounded at both ends.** A 1x1 PNG is not a preview and would render as a
dot; a 20000x20000 one is a decompression bomb by another name, and the screen would try to draw it
in a tile. The front end sends 320x200; the bounds are wide enough not to fight it and narrow enough
to mean something.

**What it deliberately does not do**: decode the image. A full decode is the expensive, dangerous
part, and the header carries everything needed to decide whether to accept it.
"""

from __future__ import annotations

import base64
import contextlib
import os
import struct
from pathlib import Path

from stokes.errors import StokesError
from stokes.log import get_logger

log = get_logger(__name__)

#: The file inside a project folder.
PREVIEW_FILE = "preview.png"
#: The PNG signature: 8 bytes, the only thing that says a file is a PNG.
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
#: The smallest and largest edge accepted, in pixels.
MIN_EDGE = 16
MAX_EDGE = 1024
#: The most decoded bytes accepted.
MAX_BYTES = 512 * 1024


class PreviewError(StokesError):
    """The image is not one we will store."""

    code = "project.preview_invalid"


def decode(png_base64: str) -> bytes:
    """The PNG bytes from a client's base64, or a refusal.

    **`validate=True`**, as the licence code does: without it Python silently ignores characters
    not in the alphabet, so a truncated or line-wrapped string decodes to *something* rather than
    failing. A malformed image that decodes is worse than one that does not.
    """
    if not isinstance(png_base64, str) or not png_base64:
        raise PreviewError("no image was sent", details={"code": "project.preview_invalid"})
    try:
        raw = base64.b64decode(png_base64, validate=True)
    except (ValueError, TypeError) as exc:
        raise PreviewError(
            "the image is not valid base64", details={"code": "project.preview_invalid"}
        ) from exc
    return check(raw)


def check(raw: bytes) -> bytes:
    """The bytes if they are a PNG we will store, or a refusal. **Pure**, so testable alone."""
    if not raw.startswith(PNG_SIGNATURE):
        raise PreviewError("the image is not a PNG", details={"code": "project.preview_invalid"})
    if len(raw) > MAX_BYTES:
        raise PreviewError(
            f"the image is {len(raw)} bytes; the limit is {MAX_BYTES}",
            details={"code": "project.preview_invalid", "bytes": len(raw)},
        )
    width, height = _size(raw)
    for edge, name in ((width, "width"), (height, "height")):
        if not MIN_EDGE <= edge <= MAX_EDGE:
            raise PreviewError(
                f"the image is {width}x{height}; each edge must be {MIN_EDGE} to {MAX_EDGE}",
                details={"code": "project.preview_invalid", name: edge},
            )
    return raw


def _size(raw: bytes) -> tuple[int, int]:
    """The width and height from the `IHDR` chunk, or a refusal.

    **The header, not a decode.** A PNG's first chunk is always `IHDR` and carries the dimensions
    as two big-endian `u32`s, so the answer is 8 bytes in rather than a full decode of an image a
    client chose - exactly the work this exists to avoid doing before validating.
    """
    # The offsets: 8 signature, 4 chunk length, 4 the IHDR tag, then width and height.
    if len(raw) < 24 or raw[12:16] != b"IHDR":
        raise PreviewError(
            "the image has no IHDR header", details={"code": "project.preview_invalid"}
        )
    width, height = struct.unpack(">II", raw[16:24])
    return width, height


def path_for(folder: Path) -> Path:
    """`<project folder>/preview.png`, refusing a link.

    **A symlink here would be a write somewhere else**, chosen by whoever put the link in the
    project folder - the same rule `imports/` and the results cache follow.
    """
    target = folder / PREVIEW_FILE
    if target.is_symlink():
        raise PreviewError(
            f"{PREVIEW_FILE} is a link; remove it and save again",
            details={"code": "project.preview_invalid"},
        )
    return target


def write(folder: Path, raw: bytes) -> Path:
    """Write the validated image atomically. Returns its path."""
    target = path_for(folder)
    folder.mkdir(parents=True, exist_ok=True)
    temp = folder / f".{PREVIEW_FILE}.tmp"
    try:
        # **0600 like the rest of the project's own files**: it is a picture of the user's model.
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
        temp.replace(target)
    except OSError as exc:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise PreviewError(
            f"cannot write the preview: {exc}", details={"code": "project.preview_invalid"}
        ) from exc
    return target


def exists(folder: Path) -> bool:
    """Whether a project has a preview, for a listing's `has_preview`."""
    with contextlib.suppress(OSError):
        return path_for(folder).is_file()
    return False

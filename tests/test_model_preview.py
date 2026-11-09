"""A project thumbnail: what is accepted, and what is refused before it touches the disk.

**The bytes come from a browser and land in the user's project folder**, which another part of
the app reads later. So the checks happen before the write, they refuse rather than repair, and
the header is read instead of the image decoded - the decode is the expensive and dangerous
part, and it is exactly what a hostile file wants us to do.
"""

from __future__ import annotations

import base64
import struct
import zlib
from pathlib import Path

import pytest

from stokes.model.preview import (
    MAX_BYTES,
    MAX_EDGE,
    MIN_EDGE,
    PreviewError,
    check,
    decode,
    exists,
    path_for,
    write,
)


def a_png(width: int = 320, height: int = 200, *, signature: bytes | None = None) -> bytes:
    """A real PNG header at the given size. **No image data**: the dimensions are all that is read,
    which is the property under test."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    head = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    return (signature if signature is not None else b"\x89PNG\r\n\x1a\n") + head


def test_a_reasonable_png_is_accepted() -> None:
    """**The false-refusal half.** The front end sends 320x200, and it must go through."""
    assert check(a_png()) == a_png()
    assert check(a_png(MIN_EDGE, MIN_EDGE))
    assert check(a_png(MAX_EDGE, MAX_EDGE))


@pytest.mark.parametrize(
    ("raw", "why"),
    [
        (b"GIF89a" + b"x" * 100, "a GIF is not a PNG"),
        (b"", "empty bytes"),
        (b"\x89PNG\r\n\x1a\n" + b"short", "a truncated header"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, "no IHDR chunk"),
    ],
)
def test_something_that_is_not_a_png_is_refused(raw: bytes, why: str) -> None:
    """The signature is the first check, and the header the second - neither is optional."""
    with pytest.raises(PreviewError) as caught:
        check(raw)
    assert caught.value.code == "project.preview_invalid", why


@pytest.mark.parametrize(
    ("width", "height"),
    [(1, 200), (200, 1), (MIN_EDGE - 1, 200), (200, MIN_EDGE - 1)],
)
def test_an_image_too_small_to_be_a_preview_is_refused(width: int, height: int) -> None:
    """A 1x1 PNG is not a preview: it renders as a dot in a tile."""
    with pytest.raises(PreviewError):
        check(a_png(width, height))


@pytest.mark.parametrize(
    ("width", "height"),
    [(MAX_EDGE + 1, 200), (200, MAX_EDGE + 1), (20000, 20000)],
)
def test_an_image_too_large_to_draw_is_refused(width: int, height: int) -> None:
    """**The decompression-bomb case.** The dimensions are read from the header rather than by
    decoding, so an enormous image is refused without ever being expanded."""
    with pytest.raises(PreviewError):
        check(a_png(width, height))


def test_an_image_over_the_size_cap_is_refused() -> None:
    with pytest.raises(PreviewError) as caught:
        check(a_png() + b"x" * MAX_BYTES)
    assert "limit" in str(caught.value)


def test_decoding_requires_valid_base64() -> None:
    """**`validate=True`**, as the licence code does.

    **The payload matters, and my first version of this test did not distinguish the two.** It used
    `"not base64!!"`, which fails on LENGTH whichever way the decode is called - so removing
    `validate=True` left the test passing, and the mutation reported a hole rather than a fix. The
    case that actually tells them apart is junk **of a length that still works out**: the default
    decoder ignores characters outside the alphabet and returns the right bytes anyway, so only
    `validate=True` refuses. Measured.
    """
    encoded = base64.b64encode(a_png()).decode()
    assert decode(encoded) == a_png()

    # Junk in the middle, which Python's decoder IGNORES when it is not told to validate - the
    # characters are not in the alphabet, the padding still works out, and the result is the right
    # bytes. So the two decoders differ on the input but not on the output, and only a refusal here
    # tells a client its encoding was wrong.
    junk = encoded[:8] + "!!!" + encoded[8:]
    assert base64.b64decode(junk) == a_png(), (
        "this payload no longer distinguishes the two decoders, so the test proves nothing"
    )
    with pytest.raises(PreviewError) as caught:
        decode(junk)
    assert caught.value.code == "project.preview_invalid"

    for bad, why in (
        ("", "an empty string"),
        ("not base64!!", "characters outside the alphabet"),
        (base64.b64encode(b"GIF89a" + b"x" * 40).decode(), "valid base64 of a non-PNG"),
    ):
        with pytest.raises(PreviewError):
            decode(bad)
        assert why


def test_the_image_is_written_atomically_and_owner_only(tmp_path: Path) -> None:
    path = write(tmp_path, a_png())
    assert path == tmp_path / "preview.png"
    assert path.read_bytes() == a_png()
    assert path.stat().st_mode & 0o077 == 0, oct(path.stat().st_mode)
    assert exists(tmp_path) is True
    # No temporary left behind: the rename replaced it.
    assert not list(tmp_path.glob(".*tmp"))


def test_a_symlinked_preview_is_refused(tmp_path: Path) -> None:
    """**A link here is a write somewhere else**, chosen by whoever put the link in the project
    folder - the same rule `imports/` and the results cache follow."""
    target = tmp_path / "preview.png"
    target.symlink_to("/tmp/elsewhere.png")
    with pytest.raises(PreviewError) as caught:
        path_for(tmp_path)
    assert caught.value.code == "project.preview_invalid"
    with pytest.raises(PreviewError):
        write(tmp_path, a_png())
    assert not Path("/tmp/elsewhere.png").exists()


def test_a_project_with_no_preview_says_so_rather_than_raising(tmp_path: Path) -> None:
    """`has_preview` in a listing asks this, and a missing thumbnail is the ordinary case for every
    project made before the feature existed."""
    assert exists(tmp_path) is False

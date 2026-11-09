"""Slice planes: how a request names one, and how the cache names the result (slices contract).

**A plane is six numbers** — `origin (ox,oy,oz)` and `normal (nx,ny,nz)`, metres. A client sends
them as `?plane=ox,oy,oz,nx,ny,nz`; omitting the parameter means the run's default mid-plane, so
every existing client and test keeps working unchanged.

**The cache key is a rounded, normalised hash, and both halves matter.**

*Rounded*, because a plane arrives as text from a client that may be dragging a handle:
`0.30000001` and `0.3` are the same plane to anyone looking at it, and keying on the exact float
would compute and store a second copy for every pixel of a drag.

*Normalised*, because `(0,0,1)` and `(0,0,7)` describe the same plane — the cutter takes a
direction, not a magnitude. Hashing the direction as given would let a client fill the cache with
the same geometry at seven scales.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

from stokes.geometry.errors import MeshFormatError

#: Significant digits a plane's numbers are rounded to before hashing (the contract's number).
#: Six is finer than any mesh feature a plane is positioned against and coarser than float noise.
PLANE_DIGITS = 6
#: How many planes are kept per run, least recently used first out (the contract's number).
MAX_CACHED_PLANES = 16
#: At most this many cuts compute at once across the whole server; the rest wait. A cut reads
#: a solved case with VTK, so two already saturate a small machine and more would only queue
#: inside VTK.
MAX_CONCURRENT_CUTS = 2


def parse_plane(text: str) -> dict[str, list[float]]:
    """``"ox,oy,oz,nx,ny,nz"`` as ``{"origin": [...], "normal": [...]}``, canonicalised.

    **Refused rather than repaired**, because every repair is a guess about intent: the wrong count,
    a number that is not finite, a normal of zero length. The route turns this into a 400.

    The normal comes back **unit length**, so two requests that describe one plane hash alike; the
    origin is left where the client put it, because moving it would move the cut.
    """
    parts = [piece.strip() for piece in text.split(",")]
    if len(parts) != 6:
        raise MeshFormatError(
            f"a plane is six numbers (ox,oy,oz,nx,ny,nz); got {len(parts)}",
            details={"plane": text},
        )
    numbers: list[float] = []
    for piece in parts:
        try:
            value = float(piece)
        except ValueError:
            raise MeshFormatError(f"a plane's numbers must be numbers; got {piece!r}") from None
        if not math.isfinite(value):
            raise MeshFormatError(f"a plane's numbers must be finite; got {piece!r}")
        numbers.append(value)
    origin, normal = numbers[:3], numbers[3:]
    # **`math.hypot`, not `sqrt(sum(x*x))`, because the square overflows.** `1e200 * 1e200`
    # which is `inf` in a float, so the sum is `inf`, the length is `inf`, and every
    # `component / inf` is **0.0** - so a normal of `1e200,1e200,0` became `[0, 0, 0]` and was
    # ACCEPTED (Opus M3). The client then sent a zero normal, the agent refused it, and the user got
    # `results.host_unavailable` - **blaming the host for a malformed request**.
    #
    # `hypot` is documented to avoid exactly this: it scales by the largest component first, so the
    # intermediate values stay in range. Measured on this tree: `hypot(1e200, 1e200)` is
    # `1.4142135623730951e+200` where the naive form gives `inf`.
    length = math.hypot(*normal)
    if length == 0.0:
        raise MeshFormatError("a plane's normal must not be the zero vector")
    if not math.isfinite(length):
        # **`hypot` cannot overflow for finite inputs, but it can still be inf if one is**, and the
        # parse above admits any finite float. Belt and braces: the result must be a usable normal
        # rather than an accident of magnitude.
        raise MeshFormatError("a plane's normal is too large to normalise")
    normalised = [component / length for component in normal]
    if not all(math.isfinite(value) for value in normalised):
        raise MeshFormatError("a plane's normal is too large to normalise")
    if all(value == 0.0 for value in normalised):
        # **Refused rather than repaired.** A normal that normalises to zero is not a plane, and
        # guessing one would cut somewhere the user did not ask for.
        raise MeshFormatError("a plane's normal is too small to normalise")
    return {
        "origin": [_round(value) for value in origin],
        "normal": [_round(value) for value in normalised],
    }


def _round(value: float) -> float:
    """``value`` to `PLANE_DIGITS` significant digits, so a drag's noise collapses to one plane."""
    if value == 0.0:
        return 0.0
    return round(value, -math.floor(math.log10(abs(value))) + (PLANE_DIGITS - 1))


def plane_key(plane: dict[str, Any]) -> str:
    """A stable file-name-safe key for a plane. **The cache's identity.**

    Built from the canonical form, so it is the same key for every spelling of one plane. It is a
    hash rather than the numbers because it becomes a path segment, and six floats in a file name is
    a name no one can read and some filesystems cannot hold.
    """
    origin = [float(v) for v in plane["origin"]]
    normal = [float(v) for v in plane["normal"]]
    text = ",".join(f"{value:.{PLANE_DIGITS}g}" for value in (*origin, *normal))
    return hashlib.sha256(text.encode("ascii")).hexdigest()[:32]


def iso_key(field: str, value: float) -> str:
    """A stable file-name-safe key for one iso-surface. **Shares the cut cache with `plane_key`.**

    Iso and plane cuts use one folder, one in-flight map and one set of cut slots, so their keys
    must not be able to meet. Three things keep them apart: the hashed text starts with `iso|`
    (a plane's text is six comma-separated numbers and never contains `|`), it names the **field
    and the value** (so `p = 1` and `T = 1` are different surfaces), and the cache file carries an
    `iso-` prefix and its own suffix. The value is rounded like a plane's numbers, so a slider's
    float noise collapses to one surface.
    """
    if not field or "|" in field:
        raise MeshFormatError(f"{field!r} is not a field name")
    text = f"iso|{field}|{_round(float(value)):.{PLANE_DIGITS}g}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def plane_matches(requested: dict[str, Any], stored: dict[str, Any]) -> bool:
    """Whether two planes are the same cut, by the cache's own identity.

    Used to answer "is this the plane the artifact already holds?" — the artifact's stored plane and
    a request for the same plane can be spelled differently, and comparing the keys is what makes
    the comparison the one the cache uses.
    """
    return plane_key(requested) == plane_key(stored)


def iso_from_header(header: dict[str, Any]) -> tuple[str, float] | None:
    """The `(field, value)` an artifact's iso-surface was cut at, or None when it is not one."""
    entry = header.get("slice")
    iso = entry.get("iso") if isinstance(entry, dict) else None
    if not isinstance(iso, dict):
        return None
    field, value = iso.get("field"), iso.get("value")
    if not isinstance(field, str) or isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return field, float(value)


def plane_from_header(header: dict[str, Any]) -> dict[str, Any] | None:
    """The plane an artifact's slice was cut with, or None when it has no slice section."""
    entry = header.get("slice")
    if not isinstance(entry, dict):
        return None
    origin, normal = entry.get("origin"), entry.get("normal")
    if not isinstance(origin, list) or not isinstance(normal, list):
        return None
    if len(origin) != 3 or len(normal) != 3:
        return None
    try:
        return {
            "origin": [float(v) for v in origin],
            "normal": [float(v) for v in normal],
        }
    except (TypeError, ValueError):
        return None

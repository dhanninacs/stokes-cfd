"""Iso-surfaces of a finished run: the request, its refusals and its summary (ADR-0026 decision 3).

An iso-surface is the surface where one scalar field equals a value. **The agent cuts it** with
`vtkContourFilter` (`iso.cut`), because the solved case and VTK live on the host; this module is
the desktop's half: it turns a request into checked numbers, refuses what cannot be answered, and
describes a cached result. The cut itself goes through `RunStore.iso_artifact_for`, which shares
the plane cache's slots, queue caps and folder (`stokes.run.planes.iso_key` keeps the keys apart).

Import rule: headless. `stokes.units` is imported inside the function that parses the value.

**The range check uses the whole volume** (ADR-0026 amendment 2). The agent measures each scalar
field's minimum and maximum over every cell and point of the internal mesh when it extracts the
fields, and `run.fields` reports it as the entry's `volume`. A value is accepted when it lies in
the union of that and the recorded range (every patch and the default slice), so a value only the
interior reaches is cut. A cache written before the agent measured the volume has no `volume`, and
then the recorded range is all there is.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from stokes.run.errors import RunError

#: The most triangles one iso-surface may have. Past this the surface is refused rather than
#: fetched: two million triangles is about 70 MB of transport, which a viewport can draw but a
#: user cannot read, and a value that produces more is almost always a value inside a noisy band.
MAX_ISO_TRIANGLES = 2_000_000

#: The fields an iso-surface may be cut through: one component each, as a contour requires.
#: `U_mag` has no point array in the case; the agent derives it from `U` before contouring
#: (`contour_surface`), so a surface of constant speed - the figure most people want of a flow -
#: is cut like `p` and `T`.
ISO_FIELDS: tuple[str, ...] = ("p", "T", "U_mag")

#: The cache suffix, distinct from a plane's `.stkslice` so the two LRUs trim separately.
ISO_SUFFIX = ".stkiso"

#: The longest value text a request may carry. A quantity is a number and a short unit.
MAX_VALUE_TEXT = 64

#: The dimension each stored unit belongs to. The agent writes these units into the header, and a
#: value is parsed in the matching dimension so `"27 degC"` and `"300 [K]"` both reach SI.
#: Incompressible `p` is kinematic (`m2/s2`), which is a specific energy dimensionally.
UNIT_DIMENSIONS: dict[str, str] = {
    "K": "temperature",
    "Pa": "pressure",
    "m2/s2": "specific_energy",
    "m/s": "velocity",  # `U_mag`: the route sends its value as text, so it needs the dimension
}


class IsoInvalid(RunError):
    """The request names something that has no iso-surface: a field, a value, or a run."""

    code = "results.iso_invalid"


class IsoOutOfRange(IsoInvalid):
    """The value lies outside the field's range, so the surface would be empty."""

    code = "results.iso_out_of_range"


class IsoTooLarge(RunError):
    """The surface has more than `MAX_ISO_TRIANGLES` triangles."""

    code = "results.iso_too_large"


class IsoUnavailable(RunError):
    """The surface is not cached and the run's host could not cut it."""

    code = "results.host_unavailable"


@dataclass(frozen=True)
class IsoRequest:
    """A checked request: the run, the field, the value in SI, and the range it was checked in."""

    run: str
    field: str
    value: float
    units: str
    low: float
    high: float


def value_in_si(raw: Any, units: str) -> float:  # Any: a JSON number or quantity text
    """``raw`` as an SI number in the dimension of ``units``.

    A bare number is taken in SI (`K`, `Pa`, `m2/s2`, which are what the header stores). Text is a
    quantity, `"300 [K]"` or `"27 degC"`, parsed by `stokes.units` - never evaluated.
    """
    from stokes.errors import ValidationError
    from stokes.units import dimension, parse_quantity, to_si

    if isinstance(raw, bool):
        raise IsoInvalid("the iso value must be a number or a quantity, not a boolean")
    if isinstance(raw, int | float):
        number = float(raw)
    elif isinstance(raw, str):
        if len(raw) > MAX_VALUE_TEXT:
            raise IsoInvalid(f"the iso value is longer than {MAX_VALUE_TEXT} characters")
        name = UNIT_DIMENSIONS.get(units)
        if name is None:
            raise IsoInvalid(f"the field's unit {units!r} has no known dimension")
        dim = dimension(name)
        try:
            number = to_si(parse_quantity(raw.strip(), dim), dim)
        except ValidationError as exc:
            raise IsoInvalid(exc.message, details={"value": raw}) from exc
    else:
        raise IsoInvalid("the iso value must be a number or a quantity")
    if not math.isfinite(number):
        raise IsoInvalid("the iso value must be finite")
    return number


def volume_range(entry: dict[str, Any]) -> tuple[float, float]:
    """The range an iso value is checked in: the recorded range, widened by the volume's.

    The recorded `min`/`max` are over the patches and the default slice; `volume` is over every
    cell and point of the internal mesh. Their union is where the field takes a value at all, and
    a `volume` that is missing or malformed leaves the recorded range as it was.
    """
    low, high = float(entry["min"]), float(entry["max"])
    found = entry.get("volume")
    if isinstance(found, dict):
        try:
            v_low, v_high = float(found["min"]), float(found["max"])
        except (KeyError, TypeError, ValueError):
            return low, high
        if math.isfinite(v_low) and math.isfinite(v_high) and v_low <= v_high:
            low, high = min(low, v_low), max(high, v_high)
    return low, high


def check_request(status: dict[str, Any], field: str, raw: Any) -> IsoRequest:
    """A request checked against what `run.fields` reports for the run.

    ``status`` is `RunStore.fields_status`: the field must be one of `ISO_FIELDS` **and** one the
    run has (an incompressible run has no `T`), and the value must lie in the field's range: the
    recorded range widened by the whole volume's when the entry carries one (`volume_range`).
    """
    run = str(status.get("run") or "")
    if status.get("surfaces") != "ok":
        raise IsoInvalid("this run has no field data to cut", details={"run": run})
    if field not in ISO_FIELDS:
        raise IsoInvalid(
            f"{field!r} cannot be contoured; choose one of {', '.join(ISO_FIELDS)}",
            details={"field": field},
        )
    entry = next((f for f in status.get("fields") or [] if f.get("name") == field), None)
    if entry is None:
        raise IsoInvalid(f"this run has no field {field!r}", details={"run": run, "field": field})
    units = str(entry.get("units") or "")
    low, high = volume_range(entry)
    value = value_in_si(raw, units)
    if not low <= value <= high:
        raise IsoOutOfRange(
            f"{value:.6g} {units} is outside the range of {field}, {low:.6g} to {high:.6g} {units}",
            details={"field": field, "value": value, "min": low, "max": high, "units": units},
        )
    return IsoRequest(run=run, field=field, value=value, units=units, low=low, high=high)


def check_triangles(count: int) -> None:
    """Refuse a surface past `MAX_ISO_TRIANGLES`, before it is fetched or served."""
    if count > MAX_ISO_TRIANGLES:
        raise IsoTooLarge(
            f"the surface has {count} triangles, more than the {MAX_ISO_TRIANGLES} allowed; "
            "choose a value away from where the field is nearly constant",
            details={"triangles": count, "limit": MAX_ISO_TRIANGLES},
        )


def check_reply(reply: dict[str, Any]) -> None:
    """The agent's `iso.cut` reply, refused when the surface is too large to fetch."""
    surface = reply.get("surface") if isinstance(reply, dict) else None
    count = surface.get("triangles") if isinstance(surface, dict) else None
    if isinstance(count, int) and not isinstance(count, bool):
        check_triangles(count)


def result_reference(request: IsoRequest) -> str:
    """Where the surface is served: the route a client fetches it from, without the token."""
    return f"/api/results/iso/{request.run}?field={request.field}&value={request.value!r}"


def summary(request: IsoRequest, header: dict[str, Any], key: str) -> dict[str, Any]:
    """What `run.iso_surface` answers for a surface that is in the cache."""
    found = header.get("slice")
    entry: dict[str, Any] = found if isinstance(found, dict) else {}
    triangles = int(entry.get("triangles") or 0)
    return {
        "run": request.run,
        "field": request.field,
        "value": request.value,
        "units": request.units,
        "state": "ready" if triangles > 0 else "empty",
        "triangles": triangles,
        "points": int(entry.get("points") or 0),
        "range": {"min": request.low, "max": request.high},
        "time": float(header.get("time") or 0.0),
        "key": key,
        "result": result_reference(request),
    }

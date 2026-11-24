"""Time profiles of boundary values (ADR-0022 addendum 2026-11-24): a value that changes with time.

A boundary value (an inlet's speed or mass flow, an outlet's pressure, an inlet's temperature) is
a constant by default. A **profile** replaces the constant with a function of time in one of three
forms:

- ``table``: time and value pairs, at least two, times strictly increasing; linear in between and
  held at the end values outside (OpenFOAM's ``table`` with its default ``clamp``);
- ``sine``: ``mean + amplitude * sin(2 pi frequency t + phase)``;
- ``square`` (a pulse): ``high`` for the first ``duty`` of each ``period``, ``low`` for the rest.

Every number is a quantity (rule 6): stored in SI as ``{"value", "unit", "expr"?}``, the stored
form of a quantity parameter, with the text it was given when that text was an expression. The
value's own dimension is the parameter's (velocity, mass flow, pressure, temperature); a time is
``time``, a frequency ``frequency``, a phase ``angle`` and a duty ``dimensionless``.

The profiles of a boundary live in one ``json`` parameter, ``profiles``, keyed by the value's
parameter name (``{"velocity": {...}}``); no key means the constant. :data:`PROFILED` says which
parameters of which kinds may vary. :class:`TimeProfile` is the resolved, SI form the solve
snapshot, the checks and the case writers read; :func:`build` turns a command's input into the
stored form, :func:`from_stored` reads it back, and :func:`check_stored` is the node parameter's
structural check.

Import rule: the standard library, ``stokes.units.dimensions`` (the dimension names; pint only
when a dimension is asked for its quantity) and this package's errors. No ``stokes.model``;
:func:`varying_in` reads the solve settings and the regions inside the function.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from stokes.errors import StokesError
from stokes.physics.conditions import InvalidValue

FORMS: tuple[str, ...] = ("table", "sine", "square")
"""The forms a profile takes; the constant is the absence of a profile."""

FORM_FIELDS: Mapping[str, tuple[tuple[str, str], ...]] = MappingProxyType(
    {
        "sine": (
            ("mean", "value"),
            ("amplitude", "difference"),
            ("frequency", "frequency"),
            ("phase", "angle"),
        ),
        "square": (
            ("low", "value"),
            ("high", "value"),
            ("period", "time"),
            ("duty", "dimensionless"),
        ),
    }
)
"""Each closed form's fields and their dimensions; ``value`` means the parameter's own and
``difference`` a difference of it (:data:`DIFFERENCE_DIMENSIONS`): a sine's amplitude is a swing
about the mean, not a level, so ``5 [degC]`` there is a swing of 5 K, not 278.15 K. A pulse's low
and high and a table's values are levels (``value``)."""

OPTIONAL: Mapping[str, float] = MappingProxyType({"phase": 0.0})
"""Fields a command may leave out, with the SI value they then take."""

TABLE_COLUMNS: tuple[tuple[str, str], ...] = (("time", "time"), ("value", "value"))
"""A table row's two quantities."""

MAX_ROWS = 2000
"""The most rows a table takes (64 KB of JSON holds about that many)."""

PROFILED: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "velocity_inlet": ("velocity", "temperature"),
        "mass_flow_inlet": ("mass_flow", "temperature"),
        "pressure_outlet": ("pressure",),
    }
)
"""The kinds whose values may vary in time, and which. A velocity inlet's ``velocity`` varies
only with ``direction`` normal or vector (it is not in use with components)."""

VALUE_DIMENSIONS: Mapping[str, str] = MappingProxyType(
    {
        "velocity": "velocity",
        "mass_flow": "mass_flow",
        "pressure": "pressure",
        "temperature": "temperature",
    }
)
"""The dimension of each parameter a profile may replace."""

DIFFERENCE_DIMENSIONS: Mapping[str, str] = MappingProxyType(
    {
        "velocity": "velocity",
        "mass_flow": "mass_flow",
        "pressure": "pressure",
        "temperature": "temperature_difference",
    }
)
"""The dimension of a difference of each parameter: the same as the value's but for a temperature,
whose difference reads an offset unit (degC, degF) as the size of its degree
(``stokes.units.to_si``). The SI unit is the same (K), so the stored form does not change."""

VALUE_RULES: Mapping[str, str] = MappingProxyType(
    {"velocity": "positive", "mass_flow": "positive", "pressure": "any", "temperature": "positive"}
)
"""What the catalogue's rule means for a profile: the time-mean of a ``positive`` flow keeps the
rule (the flow may still reverse for part of a cycle, a warning of the set-up check), and a
temperature stays above zero at every time."""


class InvalidProfile(InvalidValue):
    """A profile the model refuses: ``details.parameter`` names the value and ``details.field``
    the part of the profile (``frequency``, ``rows.2.time``)."""

    code = "boundary.invalid_profile"


Evaluate = Callable[[str, str], tuple[float, str | None]]
"""Text and a dimension name to the SI value and the expression text to keep (None for plain
quantity text). Supplied by the caller, who knows the project's parameters."""


@dataclass(frozen=True)
class TimeProfile:
    """A resolved profile: SI numbers, ready for the case writers and the checks."""

    form: str
    mean: float = 0.0
    amplitude: float = 0.0
    frequency: float = 0.0
    phase: float = 0.0
    low: float = 0.0
    high: float = 0.0
    period: float = 0.0
    duty: float = 0.5
    rows: tuple[tuple[float, float], ...] = field(default_factory=tuple)

    def value_at(self, t: float) -> float:
        """The value at time ``t`` (s), as OpenFOAM's function of the same form computes it."""
        if self.form == "sine":
            return self.mean + self.amplitude * math.sin(
                2 * math.pi * self.frequency * t + self.phase
            )
        if self.form == "square":
            fraction = (t / self.period) % 1.0
            return self.high if fraction < self.duty else self.low
        rows = self.rows
        if t <= rows[0][0]:
            return rows[0][1]
        for (t0, v0), (t1, v1) in itertools.pairwise(rows):
            if t <= t1:
                return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
        return rows[-1][1]

    def time_mean(self) -> float:
        """The mean over one cycle (sine, square) or over the table's span."""
        if self.form == "sine":
            return self.mean
        if self.form == "square":
            return self.low + self.duty * (self.high - self.low)
        area = sum(
            0.5 * (v0 + v1) * (t1 - t0) for (t0, v0), (t1, v1) in itertools.pairwise(self.rows)
        )
        return area / (self.rows[-1][0] - self.rows[0][0])

    def cycle(self) -> float:
        """The time one period takes (sine, square), or the table's last time: the run should
        reach it to see the whole profile."""
        if self.form == "sine":
            return 1.0 / self.frequency
        if self.form == "square":
            return self.period
        return self.rows[-1][0]

    def extremes(self) -> tuple[float, float]:
        """The lowest and the highest value the profile takes."""
        if self.form == "sine":
            return (self.mean - abs(self.amplitude), self.mean + abs(self.amplitude))
        if self.form == "square":
            return (min(self.low, self.high), max(self.low, self.high))
        values = [v for _t, v in self.rows]
        return (min(values), max(values))

    def affine(self, scale: float, offset: float = 0.0) -> TimeProfile:
        """``scale * value + offset`` at every time (a pressure over a density, plus a reference);
        ``scale`` must be positive."""
        if self.form == "sine":
            return TimeProfile(
                "sine",
                mean=self.mean * scale + offset,
                amplitude=self.amplitude * scale,
                frequency=self.frequency,
                phase=self.phase,
            )
        if self.form == "square":
            return TimeProfile(
                "square",
                low=self.low * scale + offset,
                high=self.high * scale + offset,
                period=self.period,
                duty=self.duty,
            )
        return TimeProfile("table", rows=tuple((t, v * scale + offset) for t, v in self.rows))

    def samples(self, end: float, count: int = 200) -> tuple[list[float], list[float]]:
        """``count`` evenly spaced times from 0 to ``end`` and the values there (a plot)."""
        count = max(2, min(int(count), 2000))
        span = end if end > 0 else self.cycle() * 2
        times = [span * i / (count - 1) for i in range(count)]
        if self.form == "table":  # every corner of the table that falls inside, exactly
            times = sorted({*times, *(t for t, _v in self.rows if 0.0 <= t <= span)})
        return times, [self.value_at(t) for t in times]

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        """The SI numbers (``rows`` as pairs): what the API reports beside the stored form."""
        if self.form == "table":
            return {"form": "table", "rows": [[t, v] for t, v in self.rows]}
        return {
            "form": self.form,
            **{name: getattr(self, name) for name, _ in FORM_FIELDS[self.form]},
        }


# ---------------------------------------------------------------- the stored form


def _fail(parameter: str, where: str, message: str) -> InvalidProfile:
    return InvalidProfile(message, details={"parameter": parameter, "field": where})


def _si_unit(dimension_name: str) -> str:
    from stokes.units import dimension

    return dimension(dimension_name).si_unit


def _stored_number(raw: Any, parameter: str, where: str, dim: str) -> float:  # Any: JSON
    """One stored quantity's SI value, checked to be a finite number in the dimension's SI
    unit."""
    if not isinstance(raw, Mapping) or "value" not in raw:
        raise _fail(parameter, where, f"{parameter} profile: {where} must be a quantity")
    value = raw["value"]
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise _fail(parameter, where, f"{parameter} profile: {where} must be a finite number")
    unit = str(raw.get("unit", ""))
    if unit != _si_unit(dim):
        raise _fail(
            parameter,
            where,
            f"{parameter} profile: {where} is stored in {unit!r}, not {_si_unit(dim)!r}",
        )
    return float(value)


def _dimension(parameter: str, column: str) -> str:
    if column == "value":
        return VALUE_DIMENSIONS[parameter]
    if column == "difference":
        return DIFFERENCE_DIMENSIONS[parameter]
    return column


def from_stored(parameter: str, raw: Any) -> TimeProfile:  # Any: the stored JSON
    """The resolved profile of a stored one, checked (:func:`problems`); ``InvalidProfile`` when
    the stored form is not one this module writes."""
    if parameter not in VALUE_DIMENSIONS:
        raise _fail(parameter, "parameter", f"{parameter} cannot vary in time")
    if not isinstance(raw, Mapping):
        raise _fail(parameter, "form", f"{parameter} profile must be an object")
    form = raw.get("form")
    if form not in FORMS:
        raise _fail(
            parameter, "form", f"{parameter} profile: form must be one of {', '.join(FORMS)}"
        )
    if form == "table":
        rows_raw = raw.get("rows")
        if not isinstance(rows_raw, Sequence) or isinstance(rows_raw, str):
            raise _fail(parameter, "rows", f"{parameter} profile: rows must be a list")
        if len(rows_raw) > MAX_ROWS:
            raise _fail(parameter, "rows", f"{parameter} profile: at most {MAX_ROWS} rows")
        rows: list[tuple[float, float]] = []
        for index, row in enumerate(rows_raw):
            if not isinstance(row, Mapping):
                raise _fail(parameter, f"rows.{index}", f"row {index + 1} must be an object")
            time, value = (
                _stored_number(
                    row.get(name), parameter, f"rows.{index}.{name}", _dimension(parameter, name)
                )
                for name, _ in TABLE_COLUMNS
            )
            rows.append((time, value))
        profile = TimeProfile("table", rows=tuple(rows))
    else:
        numbers = {
            name: _stored_number(raw.get(name), parameter, name, _dimension(parameter, column))
            for name, column in FORM_FIELDS[form]
        }
        profile = (
            TimeProfile(
                "sine",
                mean=numbers["mean"],
                amplitude=numbers["amplitude"],
                frequency=numbers["frequency"],
                phase=numbers["phase"],
            )
            if form == "sine"
            else TimeProfile(
                "square",
                low=numbers["low"],
                high=numbers["high"],
                period=numbers["period"],
                duty=numbers["duty"],
            )
        )
    message = problems(parameter, profile)
    if message is not None:
        raise _fail(parameter, message[0], message[1])
    return profile


def problems(parameter: str, profile: TimeProfile) -> tuple[str, str] | None:
    """The first rule the profile breaks, as ``(field, message)``, else None."""
    found = (
        _sine_problem(profile)
        if profile.form == "sine"
        else _square_problem(profile)
        if profile.form == "square"
        else _table_problem(profile)
    )
    return found or _value_problem(parameter, profile)


LEVEL_HINT = (
    "inside an expression or a parameter a degC or degF value is a level (5 [degC] is 278.15 K), "
    "not a swing: give the swing alone ('5 [degC]') or in K"
)
"""Why a temperature amplitude came out as large as a level (ADR-0022 16.3): an offset unit is
read as a difference only when it is the whole text, so ``5 [degC] + 1 [K]``, ``2 * 5 [degC]``
or a parameter holding ``5 [degC]`` is in kelvin from absolute zero."""


def notes(profile: TimeProfile, parameter: str | None = None) -> tuple[dict[str, str], ...]:
    """What is allowed but likely not meant, as ``{field, message}``: a pulse whose high is below
    its low (it runs low for the duty and high for the rest; swap them for the other way), and a
    temperature swing larger than half its mean (an amplitude written as a level,
    :data:`LEVEL_HINT`)."""
    found: list[dict[str, str]] = []
    if profile.form == "square" and profile.high < profile.low:
        found.append(
            {
                "field": "high",
                "message": "high is below low: the pulse is at the lower value for the duty "
                "and at the higher one for the rest",
            }
        )
    if (
        parameter == "temperature"
        and profile.form == "sine"
        and profile.mean > 0
        and profile.amplitude > profile.mean / 2
    ):
        found.append(
            {
                "field": "amplitude",
                "message": f"the swing is {profile.amplitude:.6g} K, more than half the mean "
                f"({profile.mean:.6g} K); {LEVEL_HINT}",
            }
        )
    return tuple(found)


def _sine_problem(profile: TimeProfile) -> tuple[str, str] | None:
    if profile.frequency <= 0:
        return ("frequency", "the frequency must be above zero")
    if profile.amplitude < 0:
        return ("amplitude", "the amplitude must not be negative")
    return None


def _square_problem(profile: TimeProfile) -> tuple[str, str] | None:
    if profile.period <= 0:
        return ("period", "the period must be above zero")
    if not 0 < profile.duty < 1:
        return ("duty", "the duty must lie between 0 and 1, both excluded")
    return None


def _table_problem(profile: TimeProfile) -> tuple[str, str] | None:
    if len(profile.rows) < 2:
        return ("rows", "a table needs at least two rows")
    for index, ((t0, _v0), (t1, _v1)) in enumerate(itertools.pairwise(profile.rows)):
        if t1 <= t0:
            return (
                f"rows.{index + 1}.time",
                f"the times must increase: row {index + 2} is not after row {index + 1}",
            )
    return None


def _value_problem(parameter: str, profile: TimeProfile) -> tuple[str, str] | None:
    if VALUE_RULES.get(parameter, "any") != "positive":
        return None
    if parameter == "temperature":
        return _temperature_problem(profile)
    if profile.time_mean() <= 0:
        return ("value", f"the time-mean {parameter.replace('_', ' ')} must be above zero")
    return None


def _temperature_problem(profile: TimeProfile) -> tuple[str, str] | None:
    """A temperature at or below 0 K at some time, said at the field that takes it there: a
    sine's amplitude (most often a swing written as a level, :data:`LEVEL_HINT`) or its mean, a
    pulse's low or high, a table's first such row."""
    if profile.extremes()[0] > 0:
        return None
    if profile.form == "sine":
        if profile.mean <= 0:
            return ("mean", "the mean temperature must be above 0 K")
        return (
            "amplitude",
            f"the amplitude ({profile.amplitude:.6g} K) is not below the mean "
            f"({profile.mean:.6g} K), so the temperature would fall to 0 K or below; {LEVEL_HINT}",
        )
    if profile.form == "square":
        where = "low" if profile.low <= 0 else "high"
        return (where, f"the {where} temperature must be above 0 K")
    for index, (_t, value) in enumerate(profile.rows):
        if value <= 0:
            return (f"rows.{index}.value", "the temperature must be above 0 K")
    return ("value", "the temperature must stay above 0 K at every time")


def check_stored(node: Any, value: Any) -> Any:  # Any: Node and JSON
    """The ``profiles`` parameter's structural check (``ParamSpec.validate``): null or an object
    of parameter names to stored profiles, each one :func:`from_stored` reads."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise InvalidProfile(
            "profiles must be an object of value names to profiles",
            details={"parameter": "profiles"},
        )
    for parameter, raw in value.items():
        from_stored(str(parameter), raw)
    return dict(value) or None


# ---------------------------------------------------------------- built from a command's input


def _said(where: str) -> str:
    """A field key as a person reads it: ``rows.1.time`` is "row 2 time" (rows counted from one,
    as the editor numbers them); ``frequency`` stays."""
    parts = where.split(".")
    if len(parts) == 3 and parts[0] == "rows" and parts[1].isdigit():
        return f"row {int(parts[1]) + 1} {parts[2]}"
    return where.replace("_", " ")


def _entry(
    raw: Any,  # Any: JSON: SI number, quantity text, an expression, or a stored quantity
    parameter: str,
    where: str,
    dim: str,
    evaluate: Evaluate,
) -> dict[str, Any]:  # Any: JSON
    """One quantity of a command's input in the stored form. A bare number is SI, as everywhere
    in the API; text is quantity text (``2 [m/s]``) or an expression (``U_in * 1.5``)."""
    if isinstance(raw, Mapping):
        expr = raw.get("expr")
        if expr:
            raw = str(expr)
        elif "value" in raw:
            raw = raw["value"] if not raw.get("unit") else f"{raw['value']} [{raw['unit']}]"
    if isinstance(raw, bool) or raw is None:
        raise _fail(parameter, where, f"{_said(where)} needs a value")
    if isinstance(raw, int | float):
        if not math.isfinite(raw):
            raise _fail(parameter, where, f"{_said(where)} must be a finite number")
        return {"value": float(raw), "unit": _si_unit(dim)}
    if not isinstance(raw, str) or not raw.strip():
        raise _fail(parameter, where, f"{_said(where)} needs a value")
    try:
        value, expr = evaluate(raw.strip(), dim)
    except InvalidProfile:
        raise
    except (StokesError, ValueError) as exc:  # units and expression errors, said at the field
        raise _fail(parameter, where, f"{_said(where)}: {exc}") from exc
    if not math.isfinite(value):
        raise _fail(parameter, where, f"{_said(where)} must be a finite number")
    stored: dict[str, Any] = {"value": float(value), "unit": _si_unit(dim)}
    if expr:
        stored["expr"] = expr
    return stored


def build(parameter: str, raw: Any, evaluate: Evaluate) -> dict[str, Any]:  # Any: JSON
    """A command's profile input in the stored form, checked; ``InvalidProfile`` names the field
    that is wrong. ``raw`` is ``{"form", ...fields}`` or, for a table, ``{"form": "table",
    "rows": [{"time", "value"} | [time, value], ...]}``."""
    if parameter not in VALUE_DIMENSIONS:
        raise _fail(parameter, "parameter", f"{parameter} cannot vary in time")
    if not isinstance(raw, Mapping):
        raise _fail(parameter, "form", "the profile must be an object with a form")
    form = raw.get("form")
    if form not in FORMS:
        raise _fail(parameter, "form", f"the form must be one of {', '.join(FORMS)}")
    stored: dict[str, Any]
    if form == "table":
        rows_raw = raw.get("rows")
        if not isinstance(rows_raw, Sequence) or isinstance(rows_raw, str):
            raise _fail(parameter, "rows", "a table needs rows")
        if len(rows_raw) > MAX_ROWS:
            raise _fail(parameter, "rows", f"a table takes at most {MAX_ROWS} rows")
        rows = []
        for index, row in enumerate(rows_raw):
            pair = (
                (row.get("time"), row.get("value"))
                if isinstance(row, Mapping)
                else tuple(row)
                if isinstance(row, Sequence) and not isinstance(row, str) and len(row) == 2
                else None
            )
            if pair is None:
                raise _fail(parameter, f"rows.{index}", f"row {index + 1} needs a time and a value")
            rows.append(
                {
                    name: _entry(
                        item,
                        parameter,
                        f"rows.{index}.{name}",
                        _dimension(parameter, name),
                        evaluate,
                    )
                    for (name, _), item in zip(TABLE_COLUMNS, pair, strict=True)
                }
            )
        stored = {"form": "table", "rows": rows}
    else:
        stored = {"form": form}
        for name, column in FORM_FIELDS[form]:
            given = raw.get(name)
            if (given is None or given == "") and name in OPTIONAL:
                given = OPTIONAL[name]
            stored[name] = _entry(given, parameter, name, _dimension(parameter, column), evaluate)
    from_stored(parameter, stored)  # the rules, against the SI values
    return stored


def resolve(parameter: str, raw: Any, evaluate: Evaluate | None = None) -> TimeProfile:  # Any
    """A stored profile with every expression evaluated again (a parameter it names may have
    changed since it was set); without ``evaluate`` the stored SI values stand."""
    if evaluate is None or not _has_expression(raw):
        return from_stored(parameter, raw)
    fresh = _reevaluated(parameter, raw, evaluate)
    return from_stored(parameter, fresh)


def _has_expression(raw: Any) -> bool:  # Any: JSON
    if isinstance(raw, Mapping):
        return "expr" in raw or any(_has_expression(v) for v in raw.values())
    if isinstance(raw, list):
        return any(_has_expression(v) for v in raw)
    return False


def _reevaluated(parameter: str, raw: Any, evaluate: Evaluate) -> Any:  # Any: JSON
    def one(item: Any, where: str, dim: str) -> Any:  # Any: JSON
        if isinstance(item, Mapping) and item.get("expr"):
            return _entry({"expr": item["expr"]}, parameter, where, dim, evaluate)
        return item

    if not isinstance(raw, Mapping):
        return raw
    if raw.get("form") == "table" and isinstance(raw.get("rows"), list):
        return {
            "form": "table",
            "rows": [
                {
                    name: one(row.get(name), f"rows.{i}.{name}", _dimension(parameter, name))
                    for name, _ in TABLE_COLUMNS
                }
                if isinstance(row, Mapping)
                else row
                for i, row in enumerate(raw["rows"])
            ],
        }
    fields = FORM_FIELDS.get(str(raw.get("form")), ())
    return {
        **raw,
        **{name: one(raw.get(name), name, _dimension(parameter, col)) for name, col in fields},
    }


def stored_profiles(params: Mapping[str, Any]) -> Mapping[str, Any]:  # Any: node params
    """A boundary's ``profiles`` parameter as a mapping (empty when it has none)."""
    value = params.get("profiles")
    return value if isinstance(value, Mapping) else {}


def varying(kind: str, params: Mapping[str, Any]) -> tuple[str, ...]:  # Any: node params
    """The parameters of this boundary that vary in time and are in use: a profile for a
    parameter the kind does not profile, or does not use with its switches, is ignored."""
    from stokes.physics.conditions.catalogue import BY_ID

    spec = BY_ID.get(kind)
    allowed = PROFILED.get(kind, ())
    if spec is None or not allowed:
        return ()
    used = {use.name for use in spec.uses(params)}
    held = stored_profiles(params)
    return tuple(name for name in allowed if name in held and name in used)


def varying_in(project: Any, node: Any) -> tuple[str, ...]:  # Any: Project and Node
    """:func:`varying` with the project's switches: an inlet's temperature varies only while the
    energy equation is solved (on in the solve settings, or a conjugate case, which always solves
    it). What the set-up check, the solve snapshot, Fast mode and ``boundary.list`` act on."""
    names = varying(str(node.params.get("kind")), node.params)
    if "temperature" in names and not solves_energy(project):
        return tuple(name for name in names if name != "temperature")
    return names


def solves_energy(project: Any) -> bool:  # Any: Project
    """Whether the run solves the energy equation: the solve settings say so, or the case is
    multi-region (ADR-0042, which always solves it) by the mesh's own rule
    (``stokes.mesh.conjugate.is_multi_region``: snappyHexMesh, a part domain, more than one body).
    Two bodies meshed by another mesher, or in a box, are one region and follow the switch."""
    from stokes.engines.model import SETTINGS_PATH

    settings = project.find(SETTINGS_PATH)
    if settings is not None and bool(settings.params.get("energy")):
        return True
    from stokes.mesh.conjugate import is_multi_region

    return is_multi_region(project)


def kept_for_kind(
    kind: str, params: Mapping[str, Any]
) -> tuple[dict[str, Any] | None, tuple[str, ...]]:  # Any: JSON
    """The profiles a boundary keeps when its kind becomes ``kind`` and its parameters ``params``
    (the values after the change, switches included): those the kind may vary and uses with those
    switches, or None for none; and the names of the ones it drops, so a profile does not lie
    hidden and come back when the kind or the switch is changed back (a velocity inlet's speed
    profile is dropped when its direction becomes components)."""
    from stokes.physics.conditions.catalogue import BY_ID

    held = stored_profiles(params)
    allowed = set(PROFILED.get(kind, ()))
    spec = BY_ID.get(kind)
    if spec is not None:
        allowed &= {use.name for use in spec.uses(params)}
    kept = {name: value for name, value in held.items() if name in allowed}
    dropped = tuple(name for name in held if name not in allowed)
    return (kept or None), dropped

"""Boundary values that vary in time, written as OpenFOAM 14 functions of time (ADR-0022 addendum
2026-11-24).

A profile (:class:`stokes.physics.conditions.profiles.TimeProfile`) becomes the dictionary form of
one of Foundation's functions of time, given inline as ``<keyword> { type <function>; ... }``:

- ``sine``: ``type sine; amplitude A; frequency f; start s; level m``, which is
  ``m + A sin(2 pi f (t - s))``; a phase ``phi`` is the start ``s = -phi / (2 pi f)``;
- ``square``: ``type square; amplitude A; frequency f; start 0; level m; markSpace r``, which is
  ``m + A`` for the first ``r / (1 + r)`` of each period and ``m - A`` for the rest; so
  ``m = (high + low) / 2``, ``A = (high - low) / 2``, ``f = 1 / period`` and
  ``r = duty / (1 - duty)``;
- ``table``: ``type table; values ((t0 v0) (t1 v1) ...)``, linear between rows and held at the
  end values outside them (OpenFOAM's default).

A vector (a velocity inlet's velocity) is ``type scale; scale <the speed's function>; value
(nx ny nz)``: the unit direction scaled by the speed at each time, so every form needs only its
scalar version. Keywords and their meanings were read from the install's own libraries and
checked on a run of the install (a moving-lid cavity with each form; the written boundary values
at each time were compared with the profile); no OpenFOAM source was read or copied.

Pure functions over the profile; the field writers call them for a patch whose condition carries
a profile.
"""

from __future__ import annotations

import math

from stokes.compile.foam import Dict, Entry, Raw, Word
from stokes.physics.conditions.profiles import TimeProfile


def _number(value: float) -> float:
    """Twelve significant digits, so a profile written twice prints the same text."""
    return float(f"{value:.12g}")


def start_value(profile: TimeProfile) -> float:
    """The profile's value at time zero, to the same twelve digits as the function's numbers, for
    the patch's ``value``, which OpenFOAM writes over at once; kept consistent with it."""
    return _number(profile.value_at(0.0))


def function1(profile: TimeProfile) -> Dict:
    """The scalar function of time for ``profile``, as an inline dictionary."""
    if profile.form == "sine":
        start = -profile.phase / (2 * math.pi * profile.frequency) if profile.phase else 0.0
        return Dict(
            (
                Entry("type", Word("sine")),
                Entry("amplitude", _number(profile.amplitude)),
                Entry("frequency", _number(profile.frequency)),
                Entry("start", _number(start)),
                Entry("level", _number(profile.mean)),
            )
        )
    if profile.form == "square":
        return Dict(
            (
                Entry("type", Word("square")),
                Entry("amplitude", _number((profile.high - profile.low) / 2)),
                Entry("frequency", _number(1.0 / profile.period)),
                Entry("start", 0.0),
                Entry("level", _number((profile.high + profile.low) / 2)),
                Entry("markSpace", _number(profile.duty / (1.0 - profile.duty))),
            )
        )
    rows = " ".join(f"({_number(t)} {_number(v)})" for t, v in profile.rows)
    return Dict((Entry("type", Word("table")), Entry("values", Raw(f"({rows})"))))


def scaled_vector(profile: TimeProfile, direction: tuple[float, float, float]) -> Dict:
    """A vector along ``direction`` (normalised here) whose length follows ``profile``."""
    length = math.sqrt(sum(c * c for c in direction))
    x, y, z = (_number(c / length) for c in direction) if length > 0 else (0.0, 0.0, 0.0)
    return Dict(
        (
            Entry("type", Word("scale")),
            Entry("scale", function1(profile)),
            Entry("value", Raw(f"({x} {y} {z})")),
        )
    )


def uniform_fixed_value(value: Dict, initial: str) -> Dict:
    """A ``uniformFixedValue`` patch entry: the function ``value`` and the field's value at the
    start (``initial``, already in ``uniform ...`` form), which OpenFOAM writes over each time."""
    return Dict(
        (
            Entry("type", Word("uniformFixedValue")),
            Entry("uniformValue", value),
            Entry("value", Raw(initial)),
        )
    )

"""How often a transient run writes its fields, and the run-time rules `solve.configure` keeps
(ADR-0022 addendum 2026-11-24).

A transient run's written times are its result: the time slider, the playback and the animation
export step through them. Writing every 50 steps (the steady default) gave 8 frames of a 0.4 s run
at 1 ms, which made a 5 Hz profile look like a few jumps. So a transient run writes by physical
time (`write_time`) and starts from a suggestion:

* **20 frames per period** of the fastest periodic profile (sine or square) the boundaries have,
  and at least **50 frames over the run**: the interval is the smaller of `period / 20` and
  `end_time / 50`;
* **bounded**: never shorter than one time step, and never more than `MAX_FRAMES` over the run;
* **a whole number of time steps**, so with a fixed step the frames are evenly spaced.

The suggestion is "auto" until the user types their own: `solve.configure` writes it when a run is
switched to transient with no write time, and re-writes it when the end time or the step changes
while the stored one is still the old suggestion.

The rules about the times themselves are refusals here too, so the Run card shows them under the
field: a time step longer than the run, and a write interval shorter than one step or longer than
the run.

Import rule: the standard library; the profiles and the settings path inside the functions.
"""

from __future__ import annotations

import math
from typing import Any

#: Frames per period of the fastest periodic profile, and over the whole run.
FRAMES_PER_PERIOD = 20
FRAMES_PER_RUN = 50
#: The most frames a suggestion asks for (a long run at a short period would fill the disk).
MAX_FRAMES = 500
#: Relative slack when comparing stored times, which went through quantity text and back.
_SLACK = 1e-9


def suggest_write_time(end_time: float, delta_t: float, periods: list[float]) -> float:
    """The write interval in seconds for a transient run (the module's rule)."""
    if not (end_time > 0 and delta_t > 0):
        return 0.0
    interval = end_time / FRAMES_PER_RUN
    shortest = min((p for p in periods if p > 0 and math.isfinite(p)), default=None)
    if shortest is not None:
        interval = min(interval, shortest / FRAMES_PER_PERIOD)
    interval = max(interval, end_time / MAX_FRAMES, delta_t)
    steps = max(1, round(interval / delta_t))
    return float(f"{steps * delta_t:.12g}")


def frames(end_time: float, write_time: float) -> int:
    """How many times a run writes its fields at `write_time` (zero for an interval of zero)."""
    if not (end_time > 0 and write_time > 0):
        return 0
    return math.floor(end_time / write_time * (1 + _SLACK))


def same(a: float, b: float) -> bool:
    """Two stored times equal within the round trip through quantity text."""
    return abs(a - b) <= _SLACK * max(abs(a), abs(b), 1e-300)


def profile_periods(project: Any) -> list[float]:  # Any: Project
    """The periods of every sine and square profile a boundary uses (a table has none)."""
    from stokes.api.commands.boundary_profile import evaluator
    from stokes.physics.conditions.profiles import (
        InvalidProfile,
        resolve,
        stored_profiles,
        varying_in,
    )

    periods: list[float] = []
    for node in project.root.walk():
        if node.type != "boundary":
            continue
        held = stored_profiles(node.params)
        for name in varying_in(project, node):
            try:
                profile = resolve(name, held[name], evaluator(project))
            except (InvalidProfile, KeyError):
                continue
            if profile.form in ("sine", "square"):
                periods.append(profile.cycle())
    return periods


def seconds(value: Any) -> float | None:  # Any: a stored quantity or number
    """A stored time in seconds, or None when it is not a number."""
    found = getattr(value, "value", value)
    return float(found) if isinstance(found, int | float) and not isinstance(found, bool) else None

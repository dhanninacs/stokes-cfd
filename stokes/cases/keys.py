"""Settings of an imported case that are physical quantities (ADR-0029 decision 4, plan T-308).

Rule 6 says a physical value carries a dimension. An imported case's dictionaries are OpenFOAM's,
so most numbers in them are typed tokens with no dimension Stokes can know. **This table is the
exception list**: keys whose meaning is fixed by OpenFOAM 14 itself, so an override of them takes a
quantity with its unit (`0.5 [s]`, `1e-5 [m^2/s]`) and is written in SI.

A key is matched by its file (a regular expression over the case-relative path) and its exact key
path. The table is short on purpose: a wrong dimension is worse than none, because a unit
conversion on a number that is not that quantity changes the case silently.

Import rules: standard library only. Headless.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

_CONTROL = re.compile(r"(?:[^/]+/)?system/controlDict")
_PROPERTIES = re.compile(
    r"(?:[^/]+/)?constant/(?:[^/]+/)?(?:physicalProperties|transportProperties)(?:\.[A-Za-z0-9_]+)?"
)

#: `(file pattern, key path, dimension name in stokes.units.DIMENSIONS)`.
KNOWN: tuple[tuple[re.Pattern[str], tuple[str, ...], str], ...] = (
    (_CONTROL, ("startTime",), "time"),
    (_CONTROL, ("endTime",), "time"),
    (_CONTROL, ("deltaT",), "time"),
    (_CONTROL, ("maxDeltaT",), "time"),
    (_PROPERTIES, ("nu",), "kinematic_viscosity"),
    (_PROPERTIES, ("rho",), "density"),
)


def dimension_of(file: str, path: Sequence[str]) -> str | None:
    """The dimension of the setting at ``file`` and ``path``, or None when it is a plain token."""
    wanted = tuple(path)
    for pattern, key, dimension in KNOWN:
        if key == wanted and pattern.fullmatch(file):
            return dimension
    return None

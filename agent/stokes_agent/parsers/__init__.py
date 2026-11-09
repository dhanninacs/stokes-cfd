"""The registered log parsers (T-202b).

**Empty here on purpose.** `checkMesh`'s quality summary is T-208's and
`foamRun`'s residuals are T-213's;
they register themselves into this dict when they are built:

    from stokes_agent.parsers import PARSERS
    PARSERS["checkMesh"] = CheckMeshParser

A step naming a parser that is not registered is refused at `create` (`agent.bad_params`), so
the failure
is a clear message about a name rather than an AttributeError in a job thread halfway through a
mesh.
"""

from __future__ import annotations

from collections.abc import Callable

from .base import LogParser
from .checkmesh import CheckMeshParser
from .foamrun import FoamRunParser
from .snappy import SnappyParser

#: Parser name (a step's ``parse`` field) → a callable that builds one from ``parse_params``.
#:
#: **Three, and the supervisor knows neither.** `checkMesh` is T-208's (a quality summary at
#: the end); `foamRun` is T-213's (residuals as they fall); `snappy` is the layer coverage
#: table at the end of the layer pass. This package holds the registry and no format.
PARSERS: dict[str, Callable[..., LogParser]] = {
    "checkMesh": CheckMeshParser,
    "foamRun": FoamRunParser,
    "snappy": SnappyParser,
}

__all__ = ["PARSERS", "CheckMeshParser", "FoamRunParser", "LogParser", "SnappyParser"]

"""The solver engines this build can write and run (ADR-0011, T-214a).

**One engine, and the list says why.** The skeleton solves the duct with OpenFOAM 14's
`incompressibleFluid` and nothing else; every other engine joins this tuple with its own task,
which is
why `built` and `why` are fields rather than a name being absent. A caller that asks for an
engine not
here gets `SolveInvalid` naming what is available, rather than a case written for a solver
that does not
exist.

## Why an engine is not just a string

`turbulence` is per engine: `of14.incompressibleFluid` can express a laminar model now, and
`kOmegaSST`
joins it later. A UI that offered every turbulence model for every engine would offer
combinations this
writer refuses, so the catalogue is where the pairing is stated once.
"""

from __future__ import annotations

from dataclasses import dataclass

from stokes.compile.snapshot import KINDS as WRITER_KINDS
from stokes.engines.turbulence import names_for
from stokes.run.errors import SolveInvalid

#: The one engine an energy case may use (ADR-0027 decision 1). Named here so the settings node,
#: the refusal in `solve.configure` and the check in `solve.check` say the same id.
ENERGY_ENGINE = "of14.fluid"


@dataclass(frozen=True)
class EngineSpec:
    """One solver: what it is called, what it can express, and whether it is built."""

    id: str
    label: str
    built: bool
    turbulence: tuple[str, ...]
    why: str = ""
    #: The boundary condition kinds its case writer can express. Taken from the writer's own list
    #: (`stokes.compile.snapshot.KINDS`) so a kind cannot be offered that the writer then refuses;
    #: a writer that differs gets its own tuple.
    kinds: tuple[str, ...] = WRITER_KINDS

    def supports(self, kind: str) -> bool:
        """Whether this engine's writer can express a condition kind."""
        return kind in self.kinds

    @property
    def available(self) -> bool:
        """Whether a case can be written for it now."""
        return self.built


# : Every engine the product knows about. **Unbuilt ones are listed with `why`**, so a UI can
# show what
#: is coming without offering it, and the refusal can say which is which.
ENGINES: tuple[EngineSpec, ...] = (
    EngineSpec(
        "of14.incompressibleFluid",
        "OpenFOAM 14 · Incompressible flow",
        True,
        names_for("of14.incompressibleFluid"),
    ),
    EngineSpec(
        ENERGY_ENGINE, "OpenFOAM 14 · Thermal / compressible flow", True, names_for(ENERGY_ENGINE)
    ),
)


def engine(engine_id: str) -> EngineSpec:
    """The spec for an id, or `SolveInvalid` naming the ones that exist."""
    if engine_id == "of14.kOmegaSST":
        engine_id = "of14.incompressibleFluid"
    for spec in ENGINES:
        if spec.id == engine_id:
            return spec
    known = ", ".join(spec.id for spec in ENGINES)
    raise SolveInvalid(
        f"{engine_id!r} is not a solver this build knows; it has {known}",
        details={"code": "solve.invalid", "engine": engine_id, "known": list(known.split(", "))},
    )


def kinds_for(engine_id: str | None) -> tuple[str, ...]:
    """The condition kinds an engine's writer can express; the shared writer list when the id is
    empty or unknown (the engine itself is refused elsewhere, with a better message)."""
    try:
        return engine(engine_id or "").kinds
    except SolveInvalid:
        return WRITER_KINDS


#: Why a kind is greyed out, in the few words a tooltip has room for.
UNSUPPORTED_KIND_REASON = "Not in this solver yet"


def built_engines() -> tuple[EngineSpec, ...]:
    """The engines a case can be written for now."""
    return tuple(spec for spec in ENGINES if spec.available)

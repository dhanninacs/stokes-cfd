"""Foundation 14 single-phase momentum models and their case requirements.

The registry is plain immutable metadata: importing the API must not load a solver,
numerical package or case writer. Names and engine availability are verified with
the installed Foundation 14 run-time selection tables.
"""

from __future__ import annotations

from dataclasses import dataclass

_INCOMP = "of14.incompressibleFluid"
_FLUID = "of14.fluid"
_BOTH = (_INCOMP, _FLUID)


@dataclass(frozen=True)
class TurbulenceSpec:
    """Initial fields and transported equations of one registered momentum model."""

    name: str
    family: str
    engines: tuple[str, ...]
    fields: tuple[str, ...]
    transported_fields: tuple[str, ...]
    transient_required: bool = False
    libraries: tuple[str, ...] = ()


def _ras(
    name: str,
    fields: tuple[str, ...],
    transported: tuple[str, ...] | None = None,
    *,
    engines: tuple[str, ...] = _BOTH,
    transient: bool = False,
    libraries: tuple[str, ...] = (),
) -> TurbulenceSpec:
    return TurbulenceSpec(
        name, "RAS", engines, (*fields, "nut"), transported or fields, transient, libraries
    )


def _les(name: str, fields: tuple[str, ...] = ()) -> TurbulenceSpec:
    return TurbulenceSpec(name, "LES", _BOTH, (*fields, "nut"), fields, True)


MODELS: tuple[TurbulenceSpec, ...] = (
    TurbulenceSpec("laminar", "laminar", _BOTH, (), ()),
    _ras("kEpsilon", ("k", "epsilon")),
    _ras("RNGkEpsilon", ("k", "epsilon")),
    _ras("realizableKE", ("k", "epsilon")),
    _ras("LaunderSharmaKE", ("k", "epsilon")),
    _ras("LamBremhorstKE", ("k", "epsilon"), engines=(_INCOMP,)),
    _ras("LienCubicKE", ("k", "epsilon"), engines=(_INCOMP,)),
    _ras("LienLeschziner", ("k", "epsilon"), engines=(_INCOMP,)),
    _ras("ShihQuadraticKE", ("k", "epsilon"), engines=(_INCOMP,)),
    _ras("buoyantKEpsilon", ("k", "epsilon"), engines=(_FLUID,)),
    # Registered by `libatmosphericModels`, which a case loads only through a `libs` entry - and
    # the agent refuses `libs` (it loads a shared library). Kept so the name is known, but offered
    # for no engine: measured "Unknown RASModel type kEpsilonLopesdaCosta" on FoamWin 14.
    _ras(
        "kEpsilonLopesdaCosta",
        ("k", "epsilon"),
        engines=(),
        libraries=("libatmosphericModels.so",),
    ),
    _ras("kOmega", ("k", "omega")),
    _ras("kOmega2006", ("k", "omega")),
    _ras("kOmegaSST", ("k", "omega")),
    _ras("kOmegaSSTLM", ("k", "omega", "gammaInt", "ReThetat")),
    _ras("kOmegaSSTSAS", ("k", "omega"), transient=True),
    _ras("SpalartAllmaras", ("nuTilda",)),
    _ras("LRR", ("R", "epsilon")),
    _ras("SSG", ("R", "epsilon")),
    _ras("v2f", ("k", "epsilon", "v2", "f")),
    # qZeta reads k and epsilon, then derives q and zeta before their transport equations.
    _ras("qZeta", ("k", "epsilon"), ("q", "zeta"), engines=(_INCOMP,)),
    _ras("kkLOmega", ("kt", "kl", "omega"), engines=(_INCOMP,)),
    _les("Smagorinsky"),
    _les("WALE"),
    _les("kEqn", ("k",)),
    _les("dynamicKEqn", ("k",)),
    # Offered for no engine: OF14 reads flm/fmm as dimensionless and then fails its own
    # fvMatrix dimension check on the first step, incompressible ("checkMethod", exit 22) and
    # compressible ("incompatible dimensions") alike - measured on FoamWin 14. Kept so the name
    # is known and a case that names it is refused with that reason.
    TurbulenceSpec("dynamicLagrangian", "LES", (), ("flm", "fmm", "nut"), ("flm", "fmm"), True),
    # Incompressible only: with `fluid`, OF14's own R equation mixes rho*R and R terms and stops
    # with "incompatible dimensions" on the first step (measured on FoamWin 14).
    TurbulenceSpec("DeardorffDiffStress", "LES", (_INCOMP,), ("R", "nut"), ("R",), True),
    _les("SpalartAllmarasDES", ("nuTilda",)),
    _les("SpalartAllmarasDDES", ("nuTilda",)),
    _les("SpalartAllmarasIDDES", ("nuTilda",)),
    _les("kOmegaSSTDES", ("k", "omega")),
)
MODEL_NAMES: tuple[str, ...] = tuple(spec.name for spec in MODELS)


def model(name: str) -> TurbulenceSpec:
    """Look up exact OF14 spelling; unknown values are never treated as laminar."""
    for spec in MODELS:
        if spec.name == name:
            return spec
    raise ValueError(f"unknown Foundation 14 turbulence model {name!r}")


def names_for(engine_id: str) -> tuple[str, ...]:
    """Models actually registered for this solver's momentum-transport family."""
    if engine_id == "of14.kOmegaSST":
        engine_id = _INCOMP
    return tuple(spec.name for spec in MODELS if engine_id in spec.engines)

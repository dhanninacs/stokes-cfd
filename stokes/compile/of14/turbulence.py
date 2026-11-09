"""Pure Foundation 14 momentum-model dictionaries and initial fields.

This module writes configuration, not model algorithms. A shared registry supplies
the exact field and engine requirements for both fluid solver modules. Initial
conditions use the boundary intensity/length or viscosity ratio; closed domains use
the explicit initialization quantities carried by the snapshot.
"""

from __future__ import annotations

import math

from stokes.compile.errors import CompileInvalid, CompileUnsupported
from stokes.compile.foam import (
    Comment,
    Dict,
    Entry,
    FuncInclude,
    Include,
    Raw,
    Word,
    render_file,
)
from stokes.compile.snapshot import CONSTRAINT_KINDS, PatchCondition, SolveSnapshot
from stokes.engines.turbulence import TurbulenceSpec, model

_LOW_RE = frozenset({"LaunderSharmaKE", "LamBremhorstKE", "LienLeschziner", "kkLOmega", "qZeta"})
_DIMS = {
    "k": "[0 2 -2 0 0 0 0]",
    "kt": "[0 2 -2 0 0 0 0]",
    "kl": "[0 2 -2 0 0 0 0]",
    "v2": "[0 2 -2 0 0 0 0]",
    "R": "[0 2 -2 0 0 0 0]",
    "epsilon": "[0 2 -3 0 0 0 0]",
    "omega": "[0 0 -1 0 0 0 0]",
    "f": "[0 0 -1 0 0 0 0]",
    "nut": "[0 2 -1 0 0 0 0]",
    "nuTilda": "[0 2 -1 0 0 0 0]",
    "alphat": "[1 -1 -1 0 0 0 0]",
    "gammaInt": "[0 0 0 0 0 0 0]",
    "ReThetat": "[0 0 0 0 0 0 0]",
    "flm": "[0 0 0 0 0 0 0]",
    "fmm": "[0 0 0 0 0 0 0]",
}


def _spec(snap: SolveSnapshot) -> TurbulenceSpec:
    try:
        return model(snap.turbulence)
    except ValueError as exc:
        raise CompileUnsupported(str(exc)) from exc


def validate(snap: SolveSnapshot) -> None:
    """Reject unavailable pairings and invalid initialization before writing files."""
    spec = _spec(snap)
    engine = "of14.incompressibleFluid" if snap.engine == "of14.kOmegaSST" else snap.engine
    if engine not in spec.engines:
        raise CompileUnsupported(f"{spec.name} is not registered for {engine}")
    if spec.transient_required and not snap.transient:
        raise CompileInvalid(f"{spec.name} needs a transient simulation with a physical time step")
    if spec.family == "laminar":
        return
    for name in (
        "initial_velocity",
        "initial_intensity",
        "initial_length_scale",
        "initial_viscosity_ratio",
        "nu",
    ):
        value = getattr(snap, name)
        if not math.isfinite(value) or value <= 0:
            raise CompileInvalid(
                f"{name} must be finite and positive for turbulence initialization"
            )
    if snap.initial_intensity > 1:
        raise CompileInvalid("initial_intensity must be a fraction in (0, 1]")
    if not any(patch.kind == "velocity_inlet" for patch in snap.patches):
        raise CompileInvalid("a turbulence model needs a velocity inlet to start from")
    for patch in snap.patches:
        if patch.kind == "mass_flow_inlet":
            # The inflow k and omega come from a speed, which a mass flow does not give.
            raise CompileUnsupported(
                f"turbulence needs a velocity inlet; {patch.name!r} is a mass-flow inlet",
                details={"code": "compile.unsupported", "kind": patch.kind, "patch": patch.name},
            )
        if patch.kind != "velocity_inlet":
            continue
        if patch.turbulence_mode not in ("intensity_ratio", "intensity_length"):
            raise CompileInvalid(f"inlet {patch.name!r} needs a turbulence input mode")
        if patch.intensity is None or not 0 < patch.intensity <= 1:
            raise CompileInvalid(f"inlet {patch.name!r} needs a turbulence intensity in (0, 1]")
        selected = (
            patch.viscosity_ratio
            if patch.turbulence_mode == "intensity_ratio"
            else patch.length_scale
        )
        if selected is None or not math.isfinite(selected) or selected <= 0:
            raise CompileInvalid(f"inlet {patch.name!r} needs a positive turbulence scale")


def momentum_transport(snap: SolveSnapshot) -> Dict:
    """Momentum family, exact model name, and required LES filter/delta settings."""
    spec = _spec(snap)
    if spec.family == "laminar":
        return Dict((Entry("simulationType", Word("laminar")),))
    controls: list[Entry] = [Entry("model", Word(spec.name)), Entry("turbulence", Word("on"))]
    if spec.family == "LES":
        # OF14 registers the IDDES delta as `IDDESDelta` ("IDDES" is a FOAM FATAL, measured).
        delta = "IDDESDelta" if spec.name == "SpalartAllmarasIDDES" else "cubeRootVol"
        controls.append(Entry("delta", Word(delta)))
    if spec.name == "kOmegaSSTSAS":
        controls.append(Entry("kOmegaSSTSASCoeffs", Dict((Entry("delta", Word("cubeRootVol")),))))
    if spec.name in ("dynamicKEqn", "dynamicLagrangian"):
        controls.append(Entry(f"{spec.name}Coeffs", Dict((Entry("filter", Word("simple")),))))
    return Dict(
        (Entry("simulationType", Word(spec.family)), Entry(spec.family, Dict(tuple(controls))))
    )


def _values(snap: SolveSnapshot, patch: PatchCondition | None = None) -> dict[str, float]:
    speed, intensity = snap.initial_velocity, snap.initial_intensity
    ratio, length = snap.initial_viscosity_ratio, snap.initial_length_scale
    mode = "intensity_ratio"
    if patch is not None:
        speed = max(math.hypot(*(patch.velocity or (0.0, 0.0, 0.0))), speed * 1e-6)
        intensity = patch.intensity or intensity
        mode = patch.turbulence_mode or mode
        ratio, length = patch.viscosity_ratio or ratio, patch.length_scale or length
    k = max(1.5 * (intensity * speed) ** 2, 1e-16)
    omega = (
        k / (ratio * snap.nu) if mode == "intensity_ratio" else math.sqrt(k) / (0.09**0.25 * length)
    )
    epsilon = 0.09 * k * omega
    nut = k / omega
    # Solve the published SA eddy-viscosity relation for its transported variable.
    low, high = 0.0, nut + 8 * snap.nu
    for _ in range(48):
        middle = (low + high) / 2
        chi = middle / snap.nu
        predicted = middle * chi**3 / (chi**3 + 7.1**3)
        if predicted < nut:
            low = middle
        else:
            high = middle
    return {
        "k": k,
        "kt": k,
        "kl": k * 0.1,
        "epsilon": epsilon,
        "omega": omega,
        "nut": nut,
        "nuTilda": (low + high) / 2,
        "v2": 2 * k / 3,
        "f": epsilon / k,
        "R": 2 * k / 3,
        "gammaInt": 1.0,
        "ReThetat": 400.0,
        "flm": 1e-3,
        "fmm": 1.0,
        "alphat": snap.rho * nut / 0.85,
    }


def _uniform(name: str, value: float) -> Raw:
    if name == "R":
        return Raw(f"uniform ({value} 0 0 {value} 0 {value})")
    return Raw(f"uniform {value}")


def _fixed(name: str, value: float, kind: str = "fixedValue") -> Dict:
    return Dict((Entry("type", Word(kind)), Entry("value", _uniform(name, value))))


def _wall(snap: SolveSnapshot, field: str, value: float) -> Dict:  # noqa: PLR0911 - one return per wall function
    name = snap.turbulence
    if field == "alphat":
        return Dict(
            (
                Entry("type", Word("compressible::alphatJayatillekeWallFunction")),
                Entry("Prt", 0.85),
                Entry("value", _uniform(field, 0.0)),
            )
        )
    if field == "nut":
        low_re = name in _LOW_RE or "SpalartAllmaras" in name or _spec(snap).family == "LES"
        return _fixed(field, 0.0, "nutLowReWallFunction" if low_re else "nutkWallFunction")
    if field in ("nuTilda", "kt", "kl"):
        return _fixed(field, 0.0)
    if field in ("gammaInt", "ReThetat", "flm", "fmm"):
        return Dict((Entry("type", Word("zeroGradient")),))
    if field == "omega":
        return Dict(
            (
                Entry("type", Word("omegaWallFunction")),
                Entry("blended", True),
                Entry("value", _uniform(field, value)),
            )
        )
    if field == "epsilon":
        if name in ("LaunderSharmaKE", "LienLeschziner", "qZeta"):
            return _fixed(field, 0.0)
        if name == "LamBremhorstKE":
            return Dict((Entry("type", Word("zeroGradient")),))
        return _fixed(field, value, "epsilonWallFunction")
    if field == "v2":
        return _fixed(field, 0.0, "v2WallFunction")
    if field == "f":
        return _fixed(field, 0.0, "fWallFunction")
    if field == "R" and _spec(snap).family == "LES":
        return _fixed(field, 0.0)
    if name in _LOW_RE:
        return _fixed(field, 0.0)
    return _fixed(field, 0.0, "kqRWallFunction")


def field_files(snap: SolveSnapshot) -> dict[str, str]:
    """Initial scalar/stress fields with patch-complete wall/inlet/outlet conditions."""
    spec = _spec(snap)
    if spec.family == "laminar":
        return {}
    validate(snap)
    initial = _values(snap)
    inlet_values = {
        patch.name: _values(snap, patch) for patch in snap.patches if patch.kind == "velocity_inlet"
    }
    if inlet_values:
        initial = {
            name: sum(values[name] for values in inlet_values.values()) / len(inlet_values)
            for name in initial
        }
    fields = (*spec.fields, "alphat") if snap.energy else spec.fields
    result: dict[str, str] = {}
    for name in fields:
        boundary: list[Entry] = []
        for patch in snap.patches:
            if patch.kind in CONSTRAINT_KINDS:
                condition = Dict((Entry("type", Word(patch.patch_type)),))
            elif patch.kind in ("wall", "unassigned"):
                condition = _wall(snap, name, initial[name])
            elif name in ("nut", "alphat"):
                condition = _fixed(
                    name, initial[name] if patch.kind == "velocity_inlet" else 0.0, "calculated"
                )
            elif patch.kind == "velocity_inlet":
                condition = _fixed(name, inlet_values[patch.name][name])
            else:
                condition = Dict((Entry("type", Word("zeroGradient")),))
            boundary.append(Entry(patch.name, condition))
        body = Dict(
            (
                Entry("dimensions", Raw(_DIMS[name])),
                Entry("internalField", _uniform(name, initial[name])),
                Entry("boundaryField", Dict(tuple(boundary))),
            )
        )
        result[f"0/{name}"] = render_file(
            name, body, cls="volSymmTensorField" if name == "R" else "volScalarField", location="0"
        )
    return result


def _append(base: Dict, key: str, additions: tuple[Entry, ...]) -> Dict:
    output: list[Entry | Include | FuncInclude | Comment] = []
    found = False
    for entry in base.items:
        if isinstance(entry, Entry) and entry.key == key and isinstance(entry.value, Dict):
            present = {item.key for item in entry.value.items if isinstance(item, Entry)}
            output.append(
                Entry(
                    key,
                    Dict(
                        (
                            *entry.value.items,
                            *(item for item in additions if item.key not in present),
                        )
                    ),
                )
            )
            found = True
        else:
            output.append(entry)
    if not found:
        output.append(Entry(key, Dict(additions)))
    return Dict(tuple(output))


def augment_schemes(base: Dict, snap: SolveSnapshot) -> Dict:
    """Append model-specific convection/gradient choices without altering flow/energy schemes."""
    spec = _spec(snap)
    if spec.family == "laminar":
        return base
    fields = spec.transported_fields
    prefix = "" if snap.transient else "bounded "
    # kOmegaSST keeps the scheme ADR-0028 verified (the tutorials' limitedLinear 1); the other
    # models start first order, which is robust and not yet verified against a tutorial.
    scheme = "limitedLinear 1" if spec.name == "kOmegaSST" else "upwind"
    result = _append(
        base,
        "divSchemes",
        tuple(Entry(f"div(phi,{name})", Raw(f"{prefix}Gauss {scheme}")) for name in fields),
    )
    result = _append(
        result,
        "gradSchemes",
        tuple(Entry(f"grad({name})", Raw("cellLimited Gauss linear 1")) for name in fields),
    )
    return _append(result, "wallDist", (Entry("method", Word("meshWave")),))


def augment_solution(base: Dict, snap: SolveSnapshot) -> Dict:
    """Add transported-field solvers and steady convergence criteria to either base solver."""
    spec = _spec(snap)
    if not spec.transported_fields:
        return base
    settings = Dict(
        (
            Entry("solver", Word("smoothSolver")),
            Entry("smoother", Word("symGaussSeidel")),
            Entry("tolerance", 1e-8),
            Entry("relTol", 0.0 if snap.transient else 0.1),
        )
    )
    keys = [f'"({"|".join(spec.transported_fields)})"']
    if snap.transient:
        keys.append(f'"({"|".join(spec.transported_fields)})Final"')
    result = _append(base, "solvers", tuple(Entry(key, settings) for key in keys))
    if not snap.transient:
        simple = next(
            (
                entry.value
                for entry in result.items
                if isinstance(entry, Entry)
                and entry.key == "SIMPLE"
                and isinstance(entry.value, Dict)
            ),
            Dict(()),
        )
        simple = _append(
            simple,
            "residualControl",
            tuple(Entry(name, snap.residual_target) for name in spec.transported_fields),
        )
        result = Dict(
            tuple(
                Entry("SIMPLE", simple)
                if isinstance(entry, Entry) and entry.key == "SIMPLE"
                else entry
                for entry in result.items
            )
        )
    return result

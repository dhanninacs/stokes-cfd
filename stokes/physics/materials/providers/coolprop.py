"""CoolProp's HEOS and INCOMP backends, its mixtures and its REFPROP backend: the provider code
that runs inside the materials worker process (ADR-0018 decision 6; contract "Providers").

Fluid ids are CoolProp's: ``Water`` and ``Air`` (HEOS pure and pseudo-pure), ``R410A.mix``
(a predefined mixture), ``Nitrogen&Oxygen`` with mole fractions ``0.79,0.21`` (an ad-hoc
mixture), ``MEG`` with ``0.5`` by mass (an INCOMP solution; INCOMP takes mass or volume
fractions, never mole), ``DowQ`` (an INCOMP pure liquid), and REFPROP's names (``WATER``,
``R32&R125``) for the REFPROP backend. Evaluation returns density, specific heat, viscosity and
conductivity on a temperature grid at one pressure. A property CoolProp has no model for (no
viscosity correlation for 70 of the 136 HEOS fluids; INCOMP's placeholder 1 Pa s and 0 W/(m K))
is reported missing, never guessed. A state outside the fluid's limits, at saturation, or a
range that crosses saturation with ``phase: auto`` raises :class:`OutOfRange`; ``liquid``,
``gas`` and ``supercritical`` impose the phase (CoolProp's ``specify_phase``) and allow
metastable values, which the fit report flags.

CoolProp holds the GIL in every call and for seconds while it loads its fluid library at
import, which is why this module lives in a worker process (``worker.py``). REFPROP calls go
through :data:`stokes.physics.materials.providers.refprop.LOCK`.

Import rule: CoolProp inside functions only; nothing here is imported by the API process
except for its constants.
"""

from __future__ import annotations

import math
import re
import time
from collections import OrderedDict
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from functools import cache
from typing import Any

from stokes.errors import ValidationError
from stokes.physics.materials.errors import (
    EvaluationFailed,
    FluidUnknown,
    MaterialsError,
    OutOfRange,
    RefpropMissing,
)
from stokes.physics.materials.providers import refprop

PROVIDERS: tuple[str, ...] = ("coolprop", "coolprop_incomp", "refprop")
BACKEND = {"coolprop": "HEOS", "coolprop_incomp": "INCOMP", "refprop": "REFPROP"}
FITTED: tuple[str, ...] = ("density", "viscosity", "specific_heat", "thermal_conductivity")
TRANSPORT: tuple[str, ...] = ("viscosity", "thermal_conductivity")
GAS_LIKE = frozenset({"gas", "supercritical_gas"})
LIQUID_LIKE = frozenset({"liquid", "supercritical_liquid"})
MAX_ID = 200
MAX_STATES = 32
INCOMP_PLACEHOLDER = {"viscosity": 1.0, "thermal_conductivity": 0.0}
INCOMP_ALIASES: dict[str, tuple[str, ...]] = {
    "MEG": ("ethylene glycol", "glycol"),
    "MEG2": ("ethylene glycol",),
    "AEG": ("ethylene glycol",),
    "MPG": ("propylene glycol", "glycol"),
    "MPG2": ("propylene glycol",),
    "APG": ("propylene glycol",),
    "MEA": ("ethanol",),
    "MEA2": ("ethanol",),
    "MMA": ("methanol",),
    "MMA2": ("methanol",),
    "MGL": ("glycerol",),
    "MGL2": ("glycerol",),
    "MNA": ("sodium chloride", "brine"),
    "MNA2": ("sodium chloride", "brine"),
    "MCA": ("calcium chloride", "brine"),
    "MCA2": ("calcium chloride", "brine"),
    "MMG": ("magnesium chloride", "brine"),
    "MMG2": ("magnesium chloride", "brine"),
    "MAM": ("ammonia",),
    "MAM2": ("ammonia",),
    "MKA": ("potassium acetate",),
    "MKC": ("potassium carbonate",),
    "MKF": ("potassium formate",),
    "MLI": ("lithium chloride",),
    "LiBr": ("lithium bromide",),
    "MITSW": ("seawater", "sea water"),
}
"""Search names for the common INCOMP liquids, whose CoolProp ids are abbreviations."""

_states: OrderedDict[tuple[Any, ...], Any] = OrderedDict()  # Any: CoolProp AbstractState


def module() -> Any:  # Any: CoolProp.CoolProp, a nanobind module used untyped on purpose
    import CoolProp.CoolProp

    return CoolProp.CoolProp


def version() -> str:
    return str(module().get_global_param_string("version"))


# ---------------------------------------------------------------- fluid specs


@dataclass(frozen=True)
class Spec:
    """A fluid as one provider takes it. ``kind`` is ``pure``, ``predefined`` (``*.mix``),
    ``mixture`` (``A&B`` with mole fractions) or ``solution`` (INCOMP with one fraction)."""

    provider: str
    fluid: str
    kind: str
    components: tuple[str, ...]
    fractions: tuple[float, ...] = ()
    basis: str = ""

    @property
    def backend(self) -> str:
        return BACKEND[self.provider]

    @property
    def composition(self) -> str:
        return ",".join(repr(f) for f in self.fractions) if self.kind != "predefined" else ""


def _fractions(text: str, count: int, what: str) -> tuple[float, ...]:
    try:
        values = tuple(float(part) for part in text.split(","))
    except ValueError as exc:
        raise ValidationError(
            f"{what}: composition must be numbers separated by commas; got {text!r}"
        ) from exc
    if len(values) != count:
        raise ValidationError(
            f"{what} needs {count} fraction(s), one per component; got {len(values)}",
            details={"composition": text},
        )
    if not all(math.isfinite(v) and 0.0 <= v <= 1.0 for v in values):
        raise ValidationError(f"{what}: fractions must lie between 0 and 1; got {text!r}")
    return values


def parse(provider: str, fluid: str, composition: str = "", basis: str = "") -> Spec:
    """A provider's fluid id, composition and basis, checked (``ValidationError``)."""
    name = fluid.strip()
    composition = composition.strip()
    if not name or len(name) > MAX_ID or len(composition) > 10 * MAX_ID:
        raise ValidationError(f"a {provider} fluid id is 1 to {MAX_ID} characters")
    if provider == "coolprop_incomp":
        return _parse_incomp(name, composition, basis)
    if provider not in ("coolprop", "refprop"):
        raise ValidationError(f"{provider!r} is not a CoolProp provider")
    if "&" in name:
        components = tuple(part.strip() for part in name.split("&"))
        if not all(components) or len(components) < 2:
            raise ValidationError(f"mixture {name!r} needs component names on both sides of &")
        if basis not in ("", "mole"):
            raise ValidationError(
                f"{provider} mixtures take mole fractions; got basis {basis!r}",
                details={"basis": basis},
            )
        if not composition:
            raise ValidationError(
                f"mixture {name!r} needs a composition, e.g. "
                f"{','.join(['0.5'] * len(components))} (mole fractions)"
            )
        values = _fractions(composition, len(components), name)
        if abs(sum(values) - 1.0) > 1e-6:
            raise ValidationError(
                f"the mole fractions of {name!r} must add up to 1; they add up to {sum(values):g}"
            )
        return Spec(provider, name, "mixture", components, values, "mole")
    if composition:
        raise ValidationError(
            f"{name!r} is a pure fluid or a predefined mixture; it takes no composition"
        )
    kind = "predefined" if name.lower().endswith(".mix") else "pure"
    return Spec(provider, name, kind, (name,), (), "mole" if kind == "predefined" else "")


def _parse_incomp(name: str, composition: str, basis: str) -> Spec:
    pure, solutions = incomp_lists()
    if name in pure:
        if composition:
            raise ValidationError(f"INCOMP {name} is a pure liquid; it takes no composition")
        return Spec("coolprop_incomp", name, "pure", (name,))
    if name not in solutions:
        raise FluidUnknown(
            f"CoolProp's INCOMP backend has no liquid {name!r}; see material.library_search",
            details={"provider": "coolprop_incomp", "fluid": name},
        )
    if basis == "mole":
        raise ValidationError(
            "INCOMP solutions take mass or volume fractions, never mole fractions",
            details={"basis": basis},
        )
    if not composition:
        raise ValidationError(f"INCOMP solution {name} needs a fraction, e.g. 0.3")
    (value,) = _fractions(composition, 1, name)
    return Spec("coolprop_incomp", name, "solution", (name,), (value,), basis or "mass")


@cache
def incomp_lists() -> tuple[frozenset[str], frozenset[str]]:
    """INCOMP's pure liquids and solutions."""
    cp = module()
    pure = cp.get_global_param_string("incompressible_list_pure").split(",")
    solutions = cp.get_global_param_string("incompressible_list_solution").split(",")
    return frozenset(p for p in pure if p), frozenset(s for s in solutions if s)


# ---------------------------------------------------------------- errors


def _lock(spec: Spec) -> AbstractContextManager[Any]:  # Any: the lock's own return value
    return refprop.LOCK if spec.backend == "REFPROP" else nullcontext()


def classify(
    exc: Exception, spec: Spec, t: float | None = None, p: float | None = None
) -> MaterialsError:
    """CoolProp's ValueError as the contract's typed error."""
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    lower = text.lower()
    where = f" at {t:g} K and {p:g} Pa" if t is not None and p is not None else ""
    details = {"provider": spec.provider, "fluid": spec.fluid, "T": t, "p": p}
    if "initialize failed" in lower or "cannot use the refpropmixturebackend" in lower:
        return RefpropMissing(f"REFPROP could not be used: {text}", details=details)
    if "was not found" in lower or "unable to load" in lower or "not a valid fluid" in lower:
        return FluidUnknown(
            f"{spec.provider} has no fluid {spec.fluid!r} ({text}); see material.library_search",
            details=details,
        )
    if "composition" in lower:
        return FluidUnknown(f"{spec.fluid}: {text}", details=details)
    if any(
        key in lower
        for key in (
            "tmelt",
            "freezing",
            "not between",
            "minimum",
            "maximum",
            "out of range",
            "saturation",
            "liquid phase only",
            "outside",
        )
    ):
        return OutOfRange(f"{spec.fluid}{where}: {text}", details=details)
    return EvaluationFailed(f"{spec.fluid}{where}: {text}", details=details)


# ---------------------------------------------------------------- states


def state(spec: Spec) -> Any:  # Any: a CoolProp AbstractState
    """A cached AbstractState for the spec, with its fractions set."""
    key = (spec.provider, spec.fluid, spec.fractions, spec.basis)
    found = _states.get(key)
    if found is not None:
        _states.move_to_end(key)
        return found
    cp = module()
    with _lock(spec):
        try:
            made = cp.AbstractState(spec.backend, spec.fluid)
            if spec.kind == "mixture":
                made.set_mole_fractions(list(spec.fractions))
            elif spec.kind == "solution":
                setter = (
                    made.set_volu_fractions if spec.basis == "volume" else made.set_mass_fractions
                )
                setter(list(spec.fractions))
        except (ValueError, RuntimeError) as exc:
            raise classify(exc, spec) from exc
    _states[key] = made
    while len(_states) > MAX_STATES:
        _states.popitem(last=False)
    return made


def phase_name(index: Any) -> str:  # Any: CoolProp's phases enum
    cp = module()
    names = {
        cp.iphase_liquid: "liquid",
        cp.iphase_supercritical_liquid: "supercritical_liquid",
        cp.iphase_gas: "gas",
        cp.iphase_supercritical_gas: "supercritical_gas",
        cp.iphase_supercritical: "supercritical",
        cp.iphase_twophase: "twophase",
    }
    return names.get(index, "unknown")


def crossing(phases: set[str] | frozenset[str]) -> bool:
    """Whether a set of phases met on a range spans saturation."""
    return bool(phases & LIQUID_LIKE and phases & GAS_LIKE) or "twophase" in phases


def _impose(cp: Any, made: Any, phase: str) -> None:  # Any: CoolProp module and state
    forced = {
        "liquid": cp.iphase_liquid,
        "gas": cp.iphase_gas,
        "supercritical": cp.iphase_supercritical,
    }
    if phase in forced:
        made.specify_phase(forced[phase])
    else:
        made.unspecify_phase()


def _transport(getter: Any) -> tuple[float | None, bool]:  # Any: a bound CoolProp method
    """(value, model exists) for a viscosity or conductivity call."""
    try:
        return float(getter()), True
    except (ValueError, RuntimeError) as exc:
        if "not available" in str(exc).lower():
            return None, False
        return None, True


def evaluate(spec: Spec, phase: str, temperatures: list[float], p: float) -> dict[str, Any]:
    """Properties on the grid: ``{values: {kind: [SI or None]}, missing, phases, seconds}``."""
    cp = module()
    made = state(spec)
    incompressible = spec.backend == "INCOMP"
    values: dict[str, list[float | None]] = {kind: [] for kind in FITTED}
    missing: set[str] = set()
    phases: set[str] = set()
    started = time.perf_counter()
    for t in temperatures:
        with _lock(spec):
            try:
                if not incompressible:
                    _impose(cp, made, phase)
                made.update(cp.PT_INPUTS, p, t)
                values["density"].append(float(made.rhomass()))
                values["specific_heat"].append(float(made.cpmass()))
            except (ValueError, RuntimeError) as exc:
                raise classify(exc, spec, t, p) from exc
            for kind, getter in (
                ("viscosity", made.viscosity),
                ("thermal_conductivity", made.conductivity),
            ):
                value, exists = (None, False) if kind in missing else _transport(getter)
                if not exists:
                    missing.add(kind)
                values[kind].append(value)
            if not incompressible:
                phases.add(phase_name(made.phase()))
    if incompressible:
        missing.update(_placeholders(values))
    missing.update(kind for kind in TRANSPORT if all(v is None for v in values[kind]))
    for kind in missing:
        values[kind] = [None] * len(temperatures)
    if phase == "auto" and crossing(phases):
        raise OutOfRange(
            f"{spec.fluid} changes phase between {min(temperatures):g} K and "
            f"{max(temperatures):g} K at {p:g} Pa; narrow the range or force the phase",
            details={"provider": spec.provider, "fluid": spec.fluid, "phases": sorted(phases)},
        )
    return {
        "values": values,
        "missing": sorted(missing),
        "phases": sorted(phases),
        "seconds": time.perf_counter() - started,
    }


def _placeholders(values: dict[str, list[float | None]]) -> set[str]:
    """INCOMP returns 1 Pa s and 0 W/(m K) where it has no correlation: those are missing."""
    found: set[str] = set()
    for kind, placeholder in INCOMP_PLACEHOLDER.items():
        column = values[kind]
        if column and all(v is None or v == placeholder or v <= 0.0 for v in column):
            found.add(kind)
    return found


def saturation_temperature(spec: Spec, p: float) -> float | None:
    """T_sat(p) of a pure or pseudo-pure fluid below its critical pressure, else None."""
    if spec.kind != "pure" or spec.backend == "INCOMP":
        return None
    cp = module()
    made = state(spec)
    with _lock(spec):
        try:
            if p >= float(made.p_critical()):
                return None
            made.unspecify_phase()
            made.update(cp.PQ_INPUTS, p, 0.0)
            return float(made.T())
        except (ValueError, RuntimeError):
            return None


# ---------------------------------------------------------------- the index


def _plain_formula(text: str) -> str:
    """CoolProp writes H_{2}O_{1}; search and display want H2O."""
    plain = re.sub(r"_\{(\d+)\}", r"\1", text)
    return re.sub(r"(?<=[A-Za-z])1(?![0-9])", "", plain)


def _param(name: str, key: str) -> str:
    try:
        return str(module().get_fluid_param_string(name, key)).strip()
    except (ValueError, RuntimeError):
        return ""


def _limits(made: Any) -> dict[str, float | None]:  # Any: a CoolProp AbstractState
    out: dict[str, float | None] = {}
    for key, getter in (
        ("T_min", made.Tmin),
        ("T_max", made.Tmax),
        ("p_max", made.pmax),
        ("T_critical", made.T_critical),
        ("p_critical", made.p_critical),
        ("molar_mass", made.molar_mass),
    ):
        try:
            value = float(getter())
        except (ValueError, RuntimeError):
            value = math.nan
        out[key] = value if math.isfinite(value) and value > 0.0 else None
    return out


def _probe_transport(made: Any, limits: dict[str, float | None]) -> dict[str, bool]:  # Any
    """Which transport models exist, from dilute-gas states (no flash needed).

    A model answers "not available" or gives a value; one that fails at a state (R11's
    conformal-state model above 500 K) is asked again at the next temperature.
    """
    cp = module()
    t_crit = limits.get("T_critical") or 300.0
    low = limits.get("T_min") or 0.5 * t_crit
    high = limits.get("T_max") or 2.0 * t_crit
    found: dict[str, bool | None] = dict.fromkeys(TRANSPORT)
    for t in (min(max(1.2 * t_crit, low), high), 0.5 * (low + high), 0.9 * high, low + 1.0):
        try:
            made.update(cp.DmolarT_INPUTS, 1.0, t)
        except (ValueError, RuntimeError):
            continue
        for kind, getter in (
            ("viscosity", made.viscosity),
            ("thermal_conductivity", made.conductivity),
        ):
            if found[kind] is not None:
                continue
            value, exists = _transport(getter)
            if not exists:
                found[kind] = False
            elif value is not None:
                found[kind] = value > 0.0
        if all(v is not None for v in found.values()):
            break
    return {kind: bool(value) for kind, value in found.items()}


def _heos_record(provider: str, name: str) -> dict[str, Any]:  # Any: JSON
    spec = Spec(provider, name, "pure", (name,))
    made = state(spec)
    with _lock(spec):
        limits = _limits(made)
        transport = _probe_transport(made, limits)
    aliases = [a for a in _param(name, "aliases").split(",") if a and a != name]
    reference = {
        key: value
        for key, value in (
            ("eos", _param(name, "BibTeX-EOS")),
            ("viscosity", _param(name, "BibTeX-VISCOSITY") if transport["viscosity"] else ""),
            (
                "conductivity",
                _param(name, "BibTeX-CONDUCTIVITY") if transport["thermal_conductivity"] else "",
            ),
        )
        if value
    }
    return {
        "provider": provider,
        "fluid": name,
        "name": name,
        "kind": "fluid",
        "formula": _plain_formula(_param(name, "formula")),
        "cas": _param(name, "CAS"),
        "aliases": aliases,
        **limits,
        "properties": {"density": True, "specific_heat": True, **transport},
        "composition": None,
        "basis": None,
        "reference": reference,
        "refprop_name": _param(name, "REFPROP_name") if provider == "coolprop" else "",
    }


def _mixture_record(provider: str, spec: Spec, pure: dict[str, dict[str, Any]]) -> dict[str, Any]:
    made = state(spec)
    with _lock(spec):
        names = [str(n) for n in made.fluid_names()]
        fractions = [float(x) for x in made.get_mole_fractions()]
    parts = [pure.get(n.upper()) for n in names]
    known = [p for p in parts if p is not None]
    props = {"density": True, "specific_heat": True}
    for kind in TRANSPORT:
        props[kind] = len(known) == len(parts) and all(p["properties"][kind] for p in known)
    masses = [float(p["molar_mass"]) for p in known if p.get("molar_mass")]
    molar: float | None = None
    if len(masses) == len(parts):
        molar = sum(x * m for x, m in zip(fractions, masses, strict=True))
    return {
        "provider": provider,
        "fluid": spec.fluid,
        "name": spec.fluid,
        "kind": "fluid",
        "formula": "",
        "cas": "",
        "aliases": [" & ".join(names)],
        "T_min": None,
        "T_max": None,
        "p_max": None,
        "T_critical": None,
        "p_critical": None,
        "molar_mass": molar,
        "properties": props,
        "composition": ",".join(repr(x) for x in fractions),
        "basis": "mole",
        "reference": {"mixture": f"CoolProp {spec.backend} mixture of {', '.join(names)}"},
    }


@cache
def index(provider: str, folder: str | None = None) -> tuple[dict[str, Any], ...]:
    """Every fluid a provider offers, as library records (built once per worker)."""
    if provider == "coolprop":
        return _heos_index()
    if provider == "coolprop_incomp":
        return _incomp_index()
    if provider == "refprop":
        return _refprop_index(folder)
    raise ValidationError(f"{provider!r} is not a CoolProp provider")


def _heos_index() -> tuple[dict[str, Any], ...]:
    cp = module()
    names = [n for n in cp.get_global_param_string("FluidsList").split(",") if n]
    records = [_heos_record("coolprop", name) for name in names]
    by_name = {r["fluid"].upper(): r for r in records}
    seen: set[str] = set()
    for mixture in cp.get_global_param_string("predefined_mixtures").split(","):
        folded = mixture.casefold()
        if not mixture or folded in seen or mixture.upper() == mixture:
            continue
        seen.add(folded)
        try:
            records.append(_mixture_record("coolprop", parse("coolprop", mixture), by_name))
        except MaterialsError:
            continue
    return tuple(records)


def _incomp_record(name: str, solution: bool) -> dict[str, Any] | None:  # Any: JSON
    cp = module()
    try:
        made = cp.AbstractState("INCOMP", name)
        t_min, t_max = float(made.Tmin()), float(made.Tmax())
        basis = ""
        fractions: tuple[float, float] | None = None
        if solution:
            low = float(made.trivial_keyed_output(cp.ifraction_min))
            high = float(made.trivial_keyed_output(cp.ifraction_max))
            fractions = (low, high)
            basis = _native_basis(name, 0.5 * (low + high))
        middle = 0.5 * (t_min + t_max)
        spec = Spec(
            "coolprop_incomp",
            name,
            "solution" if solution else "pure",
            (name,),
            (0.5 * (fractions[0] + fractions[1]),) if fractions else (),
            basis,
        )
        result = evaluate(spec, "auto", [middle], 101325.0)
    except (ValueError, MaterialsError):
        return None
    props = {kind: kind not in result["missing"] for kind in FITTED}
    record: dict[str, Any] = {
        "provider": "coolprop_incomp",
        "fluid": name,
        "name": name,
        "kind": "fluid",
        "formula": "",
        "cas": "",
        "aliases": list(INCOMP_ALIASES.get(name, ())),
        "T_min": t_min,
        "T_max": t_max,
        "p_max": None,
        "T_critical": None,
        "p_critical": None,
        "molar_mass": None,
        "properties": props,
        "composition": None,
        "basis": basis or None,
        "reference": {"data": f"CoolProp INCOMP {name}"},
    }
    if fractions is not None:
        record["fraction_min"], record["fraction_max"] = fractions
    return record


def _native_basis(name: str, fraction: float) -> str:
    """``mass`` or ``volume``: the basis a solution's correlations are written in."""
    cp = module()
    made = cp.AbstractState("INCOMP", name)
    try:
        made.set_mass_fractions([fraction])
        made.update(cp.PT_INPUTS, 101325.0, 0.5 * (float(made.Tmin()) + float(made.Tmax())))
    except (ValueError, RuntimeError) as exc:
        if "mass composition conversion" in str(exc).lower():
            return "volume"
    return "mass"


def _incomp_index() -> tuple[dict[str, Any], ...]:
    pure, solutions = incomp_lists()
    records = [_incomp_record(name, False) for name in sorted(pure)]
    records += [_incomp_record(name, True) for name in sorted(solutions)]
    return tuple(r for r in records if r is not None)


def _refprop_index(folder: str | None) -> tuple[dict[str, Any], ...]:
    if not folder:
        return ()
    records = []
    for name in refprop.fluid_files(folder):
        try:
            record = _heos_record("refprop", name)
        except MaterialsError:
            continue
        record["reference"] = {"source": f"NIST REFPROP ({name}.FLD)"}
        records.append(record)
    return tuple(records)


# ---------------------------------------------------------------- info


def info(spec: Spec, phase: str, t_ref: float, p_ref: float) -> dict[str, Any]:  # Any: JSON
    """A fluid's record, its limits at p_ref and its values at (T_ref, p_ref)."""
    record = _record_for(spec)
    limits: dict[str, Any] = {  # Any: JSON
        key: record.get(key) for key in ("T_min", "T_max", "p_max", "T_critical", "p_critical")
    }
    limits["T_sat"] = saturation_temperature(spec, p_ref)
    if spec.kind == "solution":
        cp = module()
        made = state(spec)
        limits["fraction_min"] = record.get("fraction_min")
        limits["fraction_max"] = record.get("fraction_max")
        limits["basis"] = spec.basis
        try:
            middle = 0.5 * (float(made.Tmin()) + float(made.Tmax()))
            made.update(cp.PT_INPUTS, p_ref, middle)
            limits["T_freeze"] = float(made.keyed_output(cp.iT_freeze))
        except (ValueError, RuntimeError):
            limits["T_freeze"] = None
    at_ref = evaluate(spec, phase, [t_ref], p_ref)
    phases = at_ref["phases"]
    limits["phase_at_T_ref"] = "liquid" if spec.backend == "INCOMP" else (phases or ["unknown"])[0]
    return {
        "record": record,
        "limits": limits,
        "values_at_ref": {kind: column[0] for kind, column in at_ref["values"].items()},
        "missing": at_ref["missing"],
        "outputs": {"speed_of_sound": spec.backend != "INCOMP"},
    }


def _record_for(spec: Spec) -> dict[str, Any]:  # Any: JSON
    if spec.kind == "pure" and spec.backend != "INCOMP":
        return _heos_record(spec.provider, spec.fluid)
    if spec.backend == "INCOMP":
        found = _incomp_record(spec.fluid, spec.kind == "solution")
        if found is None:
            raise FluidUnknown(f"INCOMP {spec.fluid} cannot be evaluated by CoolProp")
        if spec.kind == "solution":
            found["composition"] = spec.composition
            found["basis"] = spec.basis
        return found
    made = state(spec)
    with _lock(spec):
        names = [str(n) for n in made.fluid_names()]
    pure = {name.upper(): _heos_record(spec.provider, name) for name in names}
    return _mixture_record(spec.provider, spec, pure)

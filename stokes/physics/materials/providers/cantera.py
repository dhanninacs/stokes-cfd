"""Cantera ideal-gas mixtures: the provider code that runs inside the materials worker process
(ADR-0018 decision 6; contract "Providers").

A fluid is a YAML mechanism (``air.yaml``, ``gri30.yaml``, ``h2o2.yaml`` bundled with Cantera,
or the absolute path of the user's own ``.yaml``) at a frozen composition, by mole
(``O2:0.21,N2:0.78,AR:0.01``) or mass fractions. Evaluation gives density, specific heat and,
when the mechanism carries transport data, the mixture-averaged viscosity and conductivity;
the range is where the NASA polynomials of every species present are defined, and a
temperature outside it raises :class:`OutOfRange` instead of extrapolating.

The NASA-7 coefficients map onto OpenFOAM's ``janaf``: for a mixture whose species share one
T_common, the coefficients are mole-fraction weighted (c_p of the mixture per kilogram is
(R/M) sum x_k a_k(T)), and a7 carries the ideal entropy of mixing, so the exported ``janaf``
reproduces Cantera's c_p to rounding (:func:`nasa7`).

Cantera's calls hold the GIL too, so it shares the worker process with CoolProp.

Import rule: Cantera inside functions only, and only in the worker process.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from stokes.errors import ValidationError
from stokes.physics.materials.errors import (
    EvaluationFailed,
    FluidUnknown,
    MaterialsError,
    OutOfRange,
)

R_UNIVERSAL = 8.31446261815324
BUNDLED: dict[str, tuple[str, tuple[str, ...], str]] = {
    "air.yaml": ("Air (Cantera air.yaml)", ("air", "dry air"), "O2:0.21,N2:0.78,AR:0.01"),
    "gri30.yaml": (
        "GRI-Mech 3.0 (Cantera gri30.yaml)",
        ("gri30", "gri-mech", "methane", "natural gas"),
        "CH4:1,O2:2,N2:7.52",
    ),
    "h2o2.yaml": ("Hydrogen and oxygen (Cantera h2o2.yaml)", ("hydrogen", "h2o2"), "H2:2,O2:1"),
}
"""The bundled mechanisms offered by name: (display name, search names, an example
composition shown by the library browser)."""
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_SPECIES_TEXT = 4000
_solutions: dict[str, Any] = {}  # Any: cantera.Solution, one per mechanism


def module() -> Any:  # Any: the cantera package, used untyped on purpose
    import cantera

    return cantera


def version() -> str:
    return str(module().__version__)


@dataclass(frozen=True)
class Spec:
    """A mechanism at a composition: ``basis`` is ``mole`` (TPX) or ``mass`` (TPY)."""

    fluid: str
    composition: str
    basis: str


def parse(fluid: str, composition: str = "", basis: str = "") -> Spec:
    name = fluid.strip()
    if not name:
        raise ValidationError("a Cantera fluid is a mechanism file such as air.yaml")
    if name not in BUNDLED:
        path = Path(name)
        if not path.is_absolute() or path.suffix.lower() not in (".yaml", ".yml"):
            raise FluidUnknown(
                f"Cantera fluids are {', '.join(BUNDLED)} or the absolute path of a .yaml "
                f"mechanism; got {name!r}",
                details={"provider": "cantera", "fluid": name},
            )
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            raise FluidUnknown(
                f"{name} is not a readable mechanism file of at most {MAX_FILE_BYTES} bytes",
                details={"provider": "cantera", "fluid": name},
            )
    if basis not in ("", "mole", "mass"):
        raise ValidationError(f"Cantera takes mole or mass fractions; got basis {basis!r}")
    text = composition.strip()
    if not text or len(text) > MAX_SPECIES_TEXT:
        raise ValidationError(
            "a Cantera mixture needs a composition such as O2:0.21,N2:0.78,AR:0.01",
            details={"fluid": name},
        )
    return Spec(name, text, basis or "mole")


def _solution(spec: Spec) -> Any:  # Any: cantera.Solution
    found = _solutions.get(spec.fluid)
    if found is not None:
        return found
    ct = module()
    try:
        made = ct.Solution(spec.fluid)
    except ct.CanteraError as exc:
        raise FluidUnknown(
            f"Cantera cannot load {spec.fluid}: {_first(exc)}",
            details={"provider": "cantera", "fluid": spec.fluid},
        ) from exc
    if made.thermo_model != "ideal-gas":
        raise FluidUnknown(
            f"{spec.fluid} is a {made.thermo_model} phase; the Cantera provider takes ideal gases",
            details={"provider": "cantera", "fluid": spec.fluid},
        )
    _solutions[spec.fluid] = made
    return made


def _first(exc: Exception) -> str:
    lines = [line.strip() for line in str(exc).splitlines() if line.strip() and "***" not in line]
    useful = [line for line in lines if "thrown by" not in line]
    return (useful or lines or [type(exc).__name__])[-1]


def _set(gas: Any, spec: Spec, t: float, p: float) -> None:  # Any: cantera.Solution
    ct = module()
    try:
        if spec.basis == "mass":
            gas.TPY = t, p, spec.composition
        else:
            gas.TPX = t, p, spec.composition
    except ct.CanteraError as exc:
        raise FluidUnknown(
            f"{spec.fluid}: the composition {spec.composition!r} is not valid: {_first(exc)}",
            details={"provider": "cantera", "fluid": spec.fluid},
        ) from exc


def _present(gas: Any) -> list[int]:  # Any: cantera.Solution
    return [k for k, x in enumerate(gas.X) if x > 0.0]


def temperature_range(gas: Any) -> tuple[float, float]:  # Any: cantera.Solution at a state
    """Where the thermo of every species present is defined."""
    present = _present(gas)
    low = max(float(gas.species(k).thermo.min_temp) for k in present)
    high = min(float(gas.species(k).thermo.max_temp) for k in present)
    return low, high


def _has_transport(gas: Any) -> bool:  # Any: cantera.Solution
    try:
        return gas.transport_model not in ("none", "") and gas.viscosity > 0.0
    except module().CanteraError:
        return False


def evaluate(spec: Spec, temperatures: list[float], p: float) -> dict[str, Any]:  # Any: JSON
    """Properties on the grid: ``{values: {kind: [SI or None]}, missing, phases, seconds}``."""
    import time

    ct = module()
    gas = _solution(spec)
    _set(gas, spec, temperatures[0], p)
    low, high = temperature_range(gas)
    outside = [t for t in temperatures if not low <= t <= high]
    if outside:
        raise OutOfRange(
            f"{spec.fluid} at {spec.composition}: the species' thermo is defined from {low:g} K "
            f"to {high:g} K; {outside[0]:g} K is outside",
            details={"provider": "cantera", "fluid": spec.fluid, "T_min": low, "T_max": high},
        )
    transport = _has_transport(gas)
    started = time.perf_counter()
    values: dict[str, list[float | None]] = {
        "density": [],
        "viscosity": [],
        "specific_heat": [],
        "thermal_conductivity": [],
    }
    try:
        for t in temperatures:
            gas.TP = t, p
            values["density"].append(float(gas.density))
            values["specific_heat"].append(float(gas.cp_mass))
            values["viscosity"].append(float(gas.viscosity) if transport else None)
            values["thermal_conductivity"].append(
                float(gas.thermal_conductivity) if transport else None
            )
    except ct.CanteraError as exc:
        raise EvaluationFailed(f"{spec.fluid}: {_first(exc)}") from exc
    return {
        "values": values,
        "missing": [] if transport else ["thermal_conductivity", "viscosity"],
        "phases": ["gas"],
        "seconds": time.perf_counter() - started,
    }


def nasa7(spec: Spec, p: float) -> dict[str, Any] | None:  # Any: JSON
    """The mixture's NASA-7 coefficients as ``janaf`` takes them, or None when the species
    present are not all two-range NASA-7 with one common temperature."""
    gas = _solution(spec)
    _set(gas, spec, 300.0, p)
    present = _present(gas)
    x = gas.X
    rows = []
    for k in present:
        thermo = gas.species(k).thermo
        if type(thermo).__name__ != "NasaPoly2":
            return None
        rows.append((float(x[k]), [float(c) for c in thermo.coeffs]))
    if len({coeffs[0] for _, coeffs in rows}) != 1:
        return None
    t_common = rows[0][1][0]
    high = [sum(w * c[1 + i] for w, c in rows) for i in range(7)]
    low = [sum(w * c[8 + i] for w, c in rows) for i in range(7)]
    mixing = -sum(w * math.log(w) for w, _ in rows if w > 0.0)
    high[6] += mixing
    low[6] += mixing
    t_low, t_high = temperature_range(gas)
    molar_mass = float(gas.mean_molecular_weight) / 1000.0
    t = 298.15
    enthalpy = (
        low[0] * t
        + low[1] * t**2 / 2
        + low[2] * t**3 / 3
        + low[3] * t**4 / 4
        + low[4] * t**5 / 5
        + low[5]
    )
    entropy = (
        low[0] * math.log(t)
        + low[1] * t
        + low[2] * t**2 / 2
        + low[3] * t**3 / 3
        + low[4] * t**4 / 4
        + low[6]
    )
    scale = R_UNIVERSAL / molar_mass
    return {
        "coefficients": low + high,
        "breakpoints": [t_low, t_common, t_high],
        "formation_enthalpy": enthalpy * scale,
        "formation_entropy": entropy * scale,
    }


def _record(fluid: str, gas: Any, composition: str | None, basis: str | None) -> dict[str, Any]:
    name, aliases, _ = BUNDLED.get(fluid, (Path(fluid).name, (), ""))
    present = _present(gas) if composition else list(range(gas.n_species))
    low = max(float(gas.species(k).thermo.min_temp) for k in present)
    high = min(float(gas.species(k).thermo.max_temp) for k in present)
    transport = _has_transport(gas)
    description = str(getattr(gas, "input_header", {}).get("description", "")).strip()
    return {
        "provider": "cantera",
        "fluid": fluid,
        "name": name,
        "kind": "fluid",
        "formula": "",
        "cas": "",
        "aliases": list(aliases),
        "T_min": low,
        "T_max": high,
        "p_max": None,
        "T_critical": None,
        "p_critical": None,
        "molar_mass": float(gas.mean_molecular_weight) / 1000.0 if composition else None,
        "properties": {
            "density": True,
            "specific_heat": True,
            "viscosity": transport,
            "thermal_conductivity": transport,
        },
        "composition": composition,
        "basis": basis,
        "reference": {
            "mechanism": f"Cantera {version()} {fluid}"
            + (f": {description.splitlines()[0]}" if description else ""),
        },
        "species": list(gas.species_names) if len(gas.species_names) <= 60 else [],
    }


@cache
def index() -> tuple[dict[str, Any], ...]:
    """The bundled mechanisms as library records (composition left to the user)."""
    records = []
    for fluid in BUNDLED:
        try:
            gas = _solution(Spec(fluid, "", "mole"))
        except MaterialsError:
            continue
        record = _record(fluid, gas, None, None)
        record["example_composition"] = BUNDLED[fluid][2]
        records.append(record)
    return tuple(records)


def info(spec: Spec, t_ref: float, p_ref: float) -> dict[str, Any]:  # Any: JSON
    gas = _solution(spec)
    _set(gas, spec, t_ref, p_ref)
    record = _record(spec.fluid, gas, spec.composition, spec.basis)
    low, high = temperature_range(gas)
    at_ref: dict[str, Any] = {  # Any: JSON
        "values": {},
        "missing": [],
    }
    if low <= t_ref <= high:
        at_ref = evaluate(spec, [t_ref], p_ref)
    return {
        "record": record,
        "limits": {
            "T_min": low,
            "T_max": high,
            "T_sat": None,
            "phase_at_T_ref": "gas",
            "species": [gas.species_name(k) for k in _present(gas)],
        },
        "values_at_ref": {kind: column[0] for kind, column in at_ref["values"].items()},
        "missing": at_ref["missing"],
        "outputs": {"speed_of_sound": True},
        "nasa7": nasa7(spec, p_ref),
    }

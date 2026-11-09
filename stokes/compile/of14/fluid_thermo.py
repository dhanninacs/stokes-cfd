"""Pure Foundation 14 thermodynamics for constant-density fluids and ideal gases.

The snapshot selects the equation of state explicitly. Material density is never used as a
phase classifier. The ideal-gas path solves sensible enthalpy and absolute pressure; the
constant-density path retains the validated internal-energy and buoyant-pressure formulation.
"""

from __future__ import annotations

import math

from stokes.compile.errors import CompileInvalid, CompileUnsupported
from stokes.compile.foam import Comment, Dict, Entry, Word
from stokes.compile.snapshot import SolveSnapshot

GAS_CONSTANT = 8.31446261815324


def is_gas(snap: SolveSnapshot) -> bool:
    """Whether density depends on pressure and temperature through the ideal-gas law."""
    return snap.density_model == "ideal_gas"


def energy_field(snap: SolveSnapshot) -> str:
    """The field solved by the chosen thermodynamic formulation."""
    return "h" if is_gas(snap) else "e"


def pressure_field(snap: SolveSnapshot) -> str:
    """The pressure equation's field; both formulations store pressure in pascals."""
    return "p" if is_gas(snap) else "p_rgh"


def validate(snap: SolveSnapshot) -> None:
    """Refuse unsupported laws and thermodynamically impossible ideal-gas states."""
    if snap.density_model not in ("constant", "ideal_gas"):
        raise CompileUnsupported(
            f"density model {snap.density_model!r} has no Foundation 14 fluid writer",
            details={"density_model": snap.density_model},
        )
    if not is_gas(snap):
        return
    if any(component != 0.0 for component in snap.gravity):
        raise CompileUnsupported(
            "ideal-gas flow with gravity needs a variable-density buoyant pressure formulation; "
            "this writer supports ideal gases with zero gravity",
            details={"density_model": snap.density_model, "gravity": list(snap.gravity)},
        )
    if not math.isfinite(snap.molar_mass) or snap.molar_mass <= 0:
        raise CompileInvalid("ideal-gas molar mass must be positive and finite")
    gas_constant = GAS_CONSTANT / snap.molar_mass
    if snap.cp <= gas_constant:
        raise CompileInvalid(
            "ideal-gas specific heat Cp must exceed R/M so that Cv is positive",
            details={"cp": snap.cp, "gas_constant": gas_constant},
        )
    for patch in snap.patches:
        if patch.kind == "pressure_outlet":
            absolute = snap.p_ref + (patch.pressure or 0.0)
            if not math.isfinite(absolute) or absolute <= 0:
                raise CompileInvalid(
                    f"pressure outlet {patch.name!r} needs positive absolute pressure",
                    details={"patch": patch.name, "absolute_pressure": absolute},
                )


def physical_properties(snap: SolveSnapshot) -> Dict:
    """Return an original thermo dictionary from SI material inputs.

    OpenFOAM's specie molWeight uses kg/kmol while the model stores kg/mol. Constant-density
    fluids keep Cv = Cp, the established approximation for this energy formulation.
    """
    gas = is_gas(snap)
    entries: list[Comment | Entry] = []
    if not gas:
        entries.append(
            Comment(
                "sensibleInternalEnergy: the liquid form (OpenFOAM water template); "
                "sensibleEnthalpy is the gas form and would need hConst with Cp."
            )
        )
    entries.append(
        Entry(
            "thermoType",
            Dict(
                (
                    Entry("type", Word("hePsiThermo" if gas else "heRhoThermo")),
                    Entry("mixture", Word("pureMixture")),
                    Entry("transport", Word("const")),
                    Entry("thermo", Word("hConst" if gas else "eConst")),
                    Entry("equationOfState", Word("perfectGas" if gas else "rhoConst")),
                    Entry("specie", Word("specie")),
                    Entry(
                        "energy",
                        Word("sensibleEnthalpy" if gas else "sensibleInternalEnergy"),
                    ),
                )
            ),
        )
    )
    mixture = [
        Entry("specie", Dict((Entry("molWeight", snap.molar_mass * 1000 if gas else 1),))),
    ]
    if not gas:
        mixture.append(Entry("equationOfState", Dict((Entry("rho", snap.rho),))))
    mixture.extend(
        (
            Entry("thermodynamics", Dict((Entry("Cp" if gas else "Cv", snap.cp), Entry("hf", 0)))),
            Entry(
                "transport",
                Dict((Entry("mu", snap.mu), Entry("Pr", snap.mu * snap.cp / snap.kappa))),
            ),
        )
    )
    entries.append(Entry("mixture", Dict(tuple(mixture))))
    return Dict(tuple(entries))

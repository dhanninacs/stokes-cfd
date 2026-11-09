"""Named dimensions and display unit systems.

A parameter declares a dimension by name (``"velocity"``); the dimension knows
its SI storage unit and the display unit in each unit system. Dimensionality is
derived from the SI unit string by pint, so the table cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import Any

from stokes.errors import ValidationError
from stokes.units.registry import ureg

UNIT_SYSTEMS: tuple[str, ...] = ("si", "mm", "imperial")


@dataclass(frozen=True)
class Dimension:
    """A physical dimension with its storage unit and display units.

    ``display`` maps unit-system name to the unit shown to the user.
    """

    name: str
    si_unit: str
    display: dict[str, str]

    def unit_for(self, system: str) -> str:
        return self.display.get(system, self.si_unit)

    @property
    def dimensionality(self) -> Any:  # Any: pint UnitsContainer
        return _dimensionality(self.si_unit)


@cache
def _dimensionality(unit: str) -> Any:  # Any: pint UnitsContainer
    if unit == "":
        return ureg().dimensionless.dimensionality
    return ureg().parse_units(unit).dimensionality


def _dim(name: str, si: str, mm: str, imperial: str) -> Dimension:
    return Dimension(name, si, {"si": si, "mm": mm, "imperial": imperial})


DIMENSIONS: dict[str, Dimension] = {
    d.name: d
    for d in (
        _dim("dimensionless", "", "", ""),
        _dim("length", "m", "mm", "in"),
        _dim("area", "m**2", "mm**2", "in**2"),
        _dim("volume", "m**3", "mm**3", "in**3"),
        _dim("time", "s", "s", "s"),
        _dim("mass", "kg", "g", "lb"),
        _dim("temperature", "K", "degC", "degF"),
        _dim("temperature_difference", "K", "K", "delta_degF"),
        _dim("velocity", "m/s", "m/s", "ft/s"),
        _dim("acceleration", "m/s**2", "m/s**2", "ft/s**2"),
        _dim("angle", "rad", "deg", "deg"),
        _dim("angular_velocity", "rad/s", "rpm", "rpm"),
        _dim("force", "N", "N", "lbf"),
        _dim("pressure", "Pa", "kPa", "psi"),
        _dim("energy", "J", "J", "BTU"),
        _dim("power", "W", "W", "BTU/h"),
        _dim("density", "kg/m**3", "kg/m**3", "lb/ft**3"),
        _dim("dynamic_viscosity", "Pa*s", "mPa*s", "lb/(ft*s)"),
        _dim("kinematic_viscosity", "m**2/s", "mm**2/s", "ft**2/s"),
        _dim("thermal_conductivity", "W/(m*K)", "W/(m*K)", "BTU/(h*ft*delta_degF)"),
        _dim("specific_heat", "J/(kg*K)", "J/(kg*K)", "BTU/(lb*delta_degF)"),
        _dim("heat_flux", "W/m**2", "W/m**2", "BTU/(h*ft**2)"),
        _dim("heat_transfer_coefficient", "W/(m**2*K)", "W/(m**2*K)", "BTU/(h*ft**2*delta_degF)"),
        _dim("mass_flow", "kg/s", "g/s", "lb/s"),
        _dim("volume_flow", "m**3/s", "L/min", "gal/min"),
        _dim("frequency", "Hz", "Hz", "Hz"),
        # Materials (ADR-0018 decision 10). Molar mass is shown in g/mol in every system.
        Dimension("molar_mass", "kg/mol", {"si": "g/mol", "mm": "g/mol", "imperial": "g/mol"}),
        _dim("thermal_expansion", "1/K", "1/K", "1/delta_degF"),
        _dim("specific_energy", "J/kg", "J/kg", "BTU/lb"),
        _dim("specific_entropy", "J/(kg*K)", "J/(kg*K)", "BTU/(lb*delta_degF)"),
        _dim("specific_volume", "m**3/kg", "m**3/kg", "ft**3/lb"),
        _dim("sutherland_coefficient", "kg/(m*s*K**0.5)", "kg/(m*s*K**0.5)", "kg/(m*s*K**0.5)"),
        _dim("surface_tension", "N/m", "N/m", "lbf/ft"),
    )
}


def dimension(name: str) -> Dimension:
    """Look up a dimension by name; raise a typed error for unknown names."""
    try:
        return DIMENSIONS[name]
    except KeyError as exc:
        raise ValidationError(
            f"unknown dimension {name!r}; known: {', '.join(sorted(DIMENSIONS))}",
            details={"dimension": name},
        ) from exc


def display_unit(dimension_name: str, system: str) -> str:
    """Unit to show for a dimension in a unit system (falls back to SI)."""
    if system not in UNIT_SYSTEMS:
        raise ValidationError(
            f"unknown unit system {system!r}; known: {', '.join(UNIT_SYSTEMS)}",
            details={"system": system},
        )
    return dimension(dimension_name).unit_for(system)

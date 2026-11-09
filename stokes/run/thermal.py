"""The energy equation's part of the solve snapshot (ADR-0027 step 2).

Pure functions over a boundary's parameters and the solver settings: no session, no tree. The
snapshot builder (`stokes.run.snapshot`) calls them only when ``solver_settings.energy`` is on, so a
flow-only project never reaches this module and its snapshot keeps every energy default.

## The boundary vocabulary meets the writer's

| Boundary | Thermal switch | Snapshot ``thermal`` | Value |
|---|---|---|---|
| ``velocity_inlet`` | (none) | ``fixedValue`` | ``temperature`` |
| ``pressure_outlet`` | (none) | ``inletOutlet`` | ``backflow_temperature`` |
| ``wall``, ``slip_wall`` | ``adiabatic`` | ``zeroGradient`` | |
| ``wall``, ``slip_wall`` | ``temperature`` | ``fixedValue`` | ``temperature`` |
| ``wall``, ``slip_wall`` | ``heat_flux`` | ``heatFlux`` | ``heat_flux`` |
| ``wall``, ``slip_wall`` | ``convection`` | refused: ``solve.thermal_unsupported`` | |
| ``unassigned`` | (none) | ``zeroGradient`` | |
| ``symmetry`` | (none) | ``None``: the patch type carries it | |

**Convection is refused, not approximated.** A convective wall written as adiabatic converges to a
plausible wrong temperature, which is the failure a refusal exists to prevent.
"""

from __future__ import annotations

from typing import Any

from stokes.run.errors import SolveInvalid

#: The condition kinds that take the wall's ``thermal`` switch.
_WALLS: tuple[str, ...] = ("wall", "slip_wall")


def thermal_values(params: dict[str, Any] | None, kind: str, patch: str) -> dict[str, Any]:
    """`PatchCondition` keyword arguments for one patch's temperature condition.

    ``params`` is the boundary node's parameters, or `None` for a patch no boundary holds.
    """
    if kind in ("symmetry", "empty", "wedge"):  # constraint patches carry no thermal
        return {}
    if params is None or kind == "unassigned":
        return {"thermal": "zeroGradient"}
    if kind in ("velocity_inlet", "mass_flow_inlet"):
        return {"thermal": "fixedValue", "temperature": number(params.get("temperature"))}
    if kind == "pressure_outlet":
        return {
            "thermal": "inletOutlet",
            "temperature": number(params.get("backflow_temperature")),
        }
    if kind in _WALLS:
        return _wall(params, patch)
    return {}


def _wall(params: dict[str, Any], patch: str) -> dict[str, Any]:
    """A wall's condition, from its ``thermal`` switch."""
    switch = str(params.get("thermal") or "adiabatic")
    if switch == "adiabatic":
        return {"thermal": "zeroGradient"}
    if switch == "temperature":
        return {"thermal": "fixedValue", "temperature": number(params.get("temperature"))}
    if switch == "heat_flux":
        return {"thermal": "heatFlux", "heat_flux": number(params.get("heat_flux"))}
    raise SolveInvalid(
        f"the wall {patch!r} has thermal {switch!r}, which the energy equation cannot write yet; "
        "use a fixed temperature, a heat flux or adiabatic until convection is supported",
        code="solve.thermal_unsupported",
        details={"patch": patch, "thermal": switch},
    )


def gravity_of(value: Any) -> tuple[float, float, float]:
    """The settings' gravity as three floats; unset is zero, any other length is refused."""
    items = list(value or ())
    if not items:
        return (0.0, 0.0, 0.0)
    if len(items) != 3:
        raise SolveInvalid(
            f"gravity needs three components, not {len(items)}",
            code="solve.invalid",
            details={"gravity": [number(item) for item in items]},
        )
    return (number(items[0]), number(items[1]), number(items[2]))


def number(value: Any) -> float:
    """A parameter's value as a float, unwrapping a `QuantityValue`."""
    return float(getattr(value, "value", value) or 0.0)

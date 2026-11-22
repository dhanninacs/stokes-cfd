"""The solve snapshot of a multi-region mesh (ADR-0042): one case per fluid, the solids between.

The mesh result records its regions (`meta.regions`: name, kind, body, patches) and which pairs
share a wall (`meta.interfaces`). Each fluid region becomes the snapshot `of14.fluid` already
writes, built from that region's body, material and patches, with one more wall per neighbour:
the interface, whose temperature is coupled. Each solid region is its material at its reference
temperature. Headless; the materials are asked through `material.evaluate`, as for one region.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from stokes.compile.snapshot import (
    MULTI_REGION_ENGINE,
    PatchCondition,
    RegionCase,
    SolveSnapshot,
)
from stokes.run.errors import SolveInvalid

if TYPE_CHECKING:  # pragma: no cover - typing only
    from stokes.api.session import Session
    from stokes.model.nodes import Node

#: The boundary kinds whose fixed temperature is a stream's own (`_inlet_temperature`).
INLET_KINDS = ("velocity_inlet", "mass_flow_inlet")

#: The writer every fluid region's case goes through.
_FLUID_ENGINE = "of14.fluid"


def interface_patch(region: str, neighbour: str) -> str:
    """The name `splitMeshRegions` gives the wall ``region`` shares with ``neighbour``."""
    return f"{region}_to_{neighbour}"


def build_multi_region(
    session: Session, settings: Node, mesh_status: dict[str, Any]
) -> SolveSnapshot:
    """The case of a mesh with several regions; `mesh_status` already carries the result's meta."""
    from stokes.run import snapshot as single

    params = settings.params
    if not bool(params.get("energy")):
        raise SolveInvalid(
            "this mesh has several regions, which exchange heat: turn the energy equation on",
            code="solve.regions_need_energy",
        )
    if bool(params.get("transient", False)):
        raise SolveInvalid(
            "several regions are solved steady for now; turn Transient off",
            code="solve.regions_steady",
        )
    project = session.require_project()
    regions = [r for r in mesh_status.get("regions") or [] if isinstance(r, dict)]
    types = dict(mesh_status.get("patch_types") or {})
    neighbours: dict[str, list[str]] = {str(r.get("name")): [] for r in regions}
    for pair in mesh_status.get("interfaces") or []:
        first, second = str(pair[0]), str(pair[1])
        if first in neighbours and second in neighbours:
            neighbours[first].append(second)
            neighbours[second].append(first)

    cases: list[RegionCase] = []
    for region in regions:
        name, kind, part = str(region.get("name")), str(region.get("kind")), str(region.get("part"))
        near = tuple(sorted(neighbours.get(name, ())))
        if kind == "solid":
            rho, cp, kappa, start = _solid(session, project, part, name)
            cases.append(
                RegionCase(
                    name=name,
                    kind="solid",
                    rho=rho,
                    cp=cp,
                    kappa=kappa,
                    T_init=start,
                    patches=tuple(sorted(types)),
                    neighbours=near,
                )
            )
            continue
        held = {str(p) for p in region.get("patches") or ()}
        if "unassigned" in types:
            # A fluid may hold a few untagged faces whatever its own body said: an entry for a
            # patch the region does not have is ignored, a missing one stops the solver.
            held.add("unassigned")
        own = single.build_region_snapshot(
            session,
            settings,
            mesh_status,
            part_id=part,
            held=tuple(sorted(held)),
            energy=True,
            engine=_FLUID_ENGINE,
        )
        walls = tuple(
            PatchCondition(interface_patch(name, other), "wall", "wall", thermal="coupled")
            for other in near
        )
        # **Every other patch of the mesh as an adiabatic wall.** Snapping can give a region a few
        # faces of a patch another body owns (measured: 6 faces of the hot stream's inlet in the
        # cold region, where two mouths meet on one end plane), and a region's fields need an
        # entry for every patch its mesh has, or the solver stops ("Cannot find patchField").
        # In this region such faces are a wall: the other stream's opening is not this one's.
        stray = tuple(
            PatchCondition(patch, str(types[patch]), "wall", thermal="zeroGradient")
            for patch in sorted(types)
            if patch not in held and types[patch] in ("patch", "wall")
        )
        walls = walls + stray
        cases.append(
            RegionCase(
                name=name,
                kind="fluid",
                fluid=replace(own, patches=own.patches + walls, cores=1),
                neighbours=near,
            )
        )
    cases = _start_temperatures(cases)
    fluids = [case.fluid for case in cases if case.fluid is not None]
    if not fluids:
        raise SolveInvalid(
            "none of the mesh's regions is a fluid; a solid alone is not a flow case",
            code="solve.no_fluid_region",
        )
    lead = fluids[0]
    monitors = tuple(name for fluid in fluids for name in fluid.monitor_patches)
    return replace(
        lead,
        engine=MULTI_REGION_ENGINE,
        regions=tuple(cases),
        region_order=tuple(str(name) for name in mesh_status.get("region_order") or ()),
        monitor_patches=monitors,
        # One process for now: decomposing every region is a later step (ADR-0042).
        cores=1,
    )


def _inlet_temperature(fluid: SolveSnapshot) -> float | None:
    """The mean of a fluid's fixed inlet temperatures, or None when it has none."""
    found = [
        float(p.temperature)
        for p in fluid.patches
        if p.thermal == "fixedValue" and p.kind in INLET_KINDS and p.temperature is not None
    ]
    return sum(found) / len(found) if found else None


def _start_temperatures(cases: list[RegionCase]) -> list[RegionCase]:
    """Each region starts at a temperature near the one it will settle at.

    A fluid starts at its own inlet temperature and a solid at the mean of the fluids' inlets.
    Starting everything at the materials' reference temperature (300 K) put a whole stream and
    the wall tens of kelvin from their answer, and the heat the streams exchange took most of the
    run to come out of the wall: on the gyroid core at 524 k cells, the hot stream gave 120 W and
    the cold one took 71 W after 400 iterations, both outlets still moving. The properties were
    already read at the reference temperature; only the starting field changes.
    """
    inlets: list[float] = []
    out: list[RegionCase] = []
    for case in cases:
        start = None if case.fluid is None else _inlet_temperature(case.fluid)
        if case.fluid is not None and start is not None:
            inlets.append(start)
            out.append(replace(case, fluid=replace(case.fluid, T_ref=start)))
        else:
            out.append(case)
    if not inlets:
        return out
    middle = sum(inlets) / len(inlets)
    return [replace(c, T_init=middle) if c.fluid is None else c for c in out]


def _solid(
    session: Session, project: Any, part: str, name: str
) -> tuple[float, float, float, float]:
    """`(rho, cp, kappa, T_ref)` of the solid region's material, or a refusal naming the region."""
    from stokes.run import snapshot as single

    region = single._material_of(project, part)
    if region is None:
        raise SolveInvalid(
            f"the solid region {name!r} has no material; give it one before solving",
            code="solve.no_material",
            details={"region": name},
        )
    material = str(region.params.get("material"))
    start = single._reference_temperature(session, material)
    try:
        found = session.execute(
            "material.evaluate",
            material=material,
            properties=["density", "specific_heat", "thermal_conductivity"],
            T=[start],
        )
    except Exception as exc:
        raise SolveInvalid(
            f"the material of the solid region {name!r} could not be evaluated: {exc}",
            code="solve.no_thermal_material",
            details={"material": material},
        ) from exc
    rho = single._scalar(found, "density")
    cp = single._scalar(found, "specific_heat")
    kappa = single._scalar(found, "thermal_conductivity")
    if not rho or not cp or not kappa:
        raise SolveInvalid(
            f"the material of the solid region {name!r} needs a density, a specific heat and a "
            "thermal conductivity",
            code="solve.no_thermal_material",
            details={"material": material},
        )
    return float(rho), float(cp), float(kappa), float(start)

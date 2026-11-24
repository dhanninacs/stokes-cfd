"""A session's case as Fast mode reads it (ADR-0047 decisions 3 and 4, addendum 2):
``case_from_session``.

**The same reading as a solve.** The bodies are the ones the regions hold, each its own zone as each
is its own mesh region (``stokes.mesh.conjugate.region_bodies``: fluids first, a body left out of
the simulation skipped). A fluid's viscosity and density are its region's material's at its
reference temperature, through ``material.evaluate`` as the solve snapshot does; an inlet's velocity
is worked out as the solver's case gets it (``normal`` is the speed against the tag's outward
normal, from ``tag.anchor``); temperatures and thermal walls are the boundary nodes' own
(``stokes.run.thermal``). So the preview and the real run start from one set-up, and a set-up the
solver would refuse (no material, a zero direction) is refused here with the solver's own error.

**The temperature is solved** when the case has more than one body (they exchange heat, as a
multi-region solve insists) or when the solver settings have the energy equation on.

What is Fast mode's own: every body must be an implicit part (its field is the geometry), and each
inlet and outlet must be one plane (``stokes.fast.ports``). The triangles of a port are the tag's
faces on the body's display mesh, selected as ``tag.anchor`` selects them. A wall held at a
temperature or given a heat flux is found the same way when it is flat; one that is not flat is the
condition of the rest of its body's outside.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from stokes.api.session import Session
from stokes.fast.case import FastCase, Zone
from stokes.fast.errors import (
    FastInvalid,
    FastNotImplicit,
    FastPortNotPlanar,
    FastUnsupportedBoundary,
)
from stokes.fast.ports import (
    INLET,
    OUTLET,
    WALL_FLUX,
    WALL_TEMPERATURE,
    Port,
    port_from_triangles,
)

#: The boundary kinds Fast mode models, and how: a port, or a wall (decision 4).
PORT_KINDS = {"velocity_inlet": INLET, "pressure_outlet": OUTLET}
WALL_KINDS = frozenset({"wall"})


def case_from_session(session: Session) -> tuple[FastCase, float]:
    """The session's case for Fast mode, and the first fluid's density (kg/m3)."""
    from stokes.geometry.operations.implicit import body_of
    from stokes.geometry.opplan import implicit_recipe
    from stokes.implicit.graph import to_text
    from stokes.mesh.conjugate import region_bodies
    from stokes.run.snapshot import _evaluate

    project = session.require_project()
    held = region_bodies(project)
    if not held or held[0][1] == "solid":
        raise FastInvalid(
            "the case has no fluid region: make one (region.create) before starting Fast mode",
            details={"code": "setup.no_region"},
        )
    thermal = len(held) > 1 or _energy_on(project)
    zones: list[Zone] = []
    parts: list[str] = []
    low = [float("inf")] * 3
    high = [float("-inf")] * 3
    smallest = 0.0
    for _node, kind, bodies, name in held:
        part = bodies[0]
        if part.params.get("family") != "implicit":
            raise FastNotImplicit(
                f"{part.path} is not a part made with Create: Fast mode reads each body's "
                "implicit field, and imported bodies come in a later slice",
                details={"part": part.id},
            )
        body = body_of(implicit_recipe(project, part))
        low_mm, high_mm = body.bounded()
        for axis in range(3):
            low[axis] = min(low[axis], float(low_mm[axis]) * 1e-3)
            high[axis] = max(high[axis], float(high_mm[axis]) * 1e-3)
        text = to_text(body.field())
        if kind == "solid":
            zones.append(_solid_zone(session, project, part.id, name, text, thermal))
        else:
            rho, mu = _evaluate(session, project, part.id)
            if rho <= 0.0:
                raise FastInvalid(f"the fluid's density is {rho}; Fast mode needs a positive one")
            cp, kappa = _fluid_thermal(session, project, part.id) if thermal else (0.0, 0.0)
            zones.append(Zone(name, "fluid", text, nu=mu / rho, rho=rho, cp=cp, kappa=kappa))
            if not smallest:
                smallest = float(body.smallest) * 1e-3
        parts.append(part.id)
    ports, zones = _boundaries(session, project, parts, zones, thermal)
    first = zones[0]
    return (
        FastCase(
            field_text=first.field_text,
            low=(low[0], low[1], low[2]),
            high=(high[0], high[1], high[2]),
            ports=tuple(ports),
            nu=first.nu,
            smallest=smallest,
            label=held[0][2][0].path,
            zones=tuple(zones) if len(zones) > 1 or thermal else (),
            thermal=thermal,
            time_means=_time_means(project),
        ),
        first.rho,
    )


def _time_means(project: Any) -> tuple[str, ...]:  # Any: Project
    """The boundaries whose values vary in time, which the steady preview holds at their means."""
    from stokes.physics.conditions.profiles import varying_in

    return tuple(
        node.name
        for node in project.root.walk()
        if getattr(node, "type", "") == "boundary" and varying_in(project, node)
    )


def _means(project: Any, node: Any) -> dict[str, float]:  # Any: Project and Node
    """The time-mean of each value of the boundary that varies in time (ADR-0022 addendum)."""
    from stokes.run.profiles import boundary_profiles

    return {name: p.time_mean() for name, p in boundary_profiles(project, node).items()}


def _energy_on(project: Any) -> bool:  # Any: Project
    from stokes.engines.model import SETTINGS_PATH

    settings = project.find(SETTINGS_PATH)
    return settings is not None and bool(settings.params.get("energy"))


def _fluid_thermal(session: Session, project: Any, part_id: str) -> tuple[float, float]:
    """A fluid's specific heat and conductivity, or the solve's own refusal."""
    from stokes.run.snapshot import _thermal_properties

    _material, cp, kappa = _thermal_properties(session, project, part_id)
    return float(cp), float(kappa)


def _solid_zone(
    session: Session, project: Any, part_id: str, name: str, text: str, thermal: bool
) -> Zone:
    from stokes.run.regions import _solid

    if not thermal:
        return Zone(name, "solid", text)
    rho, cp, kappa, _start = _solid(session, project, part_id, name)
    return Zone(name, "solid", text, rho=rho, cp=cp, kappa=kappa)


def _boundaries(
    session: Session, project: Any, parts: list[str], zones: list[Zone], thermal: bool
) -> tuple[list[Port], list[Zone]]:  # Any: Project
    """Every boundary on the case's bodies: its port, a thermal wall, or nothing for a plain wall;
    refused otherwise. The zones come back with the condition of the rest of their outside."""
    ports: list[Port] = []
    for node in project.root.walk():
        if getattr(node, "type", "") != "boundary":
            continue
        tag = project.find(str(node.params.get("tag") or ""))
        if tag is None:
            continue
        for index, part_id in enumerate(parts):
            triangles = _tag_triangles(session, tag, part_id)
            if triangles.shape[0] == 0:
                continue  # a boundary on another body
            found = _boundary(session, node, tag, triangles, index, zones[index], thermal)
            if isinstance(found, Port):
                ports.append(found)
            elif found is not None:
                zones[index] = found
    fluids = [i for i, zone in enumerate(zones) if zone.kind != "solid"]
    for index in fluids:
        name = zones[index].name
        if not any(p.kind == INLET and p.zone == index for p in ports):
            if len(fluids) == 1:
                raise FastInvalid(
                    "the case has no velocity inlet on the fluid: Fast mode needs one to drive "
                    "the flow",
                    details={"code": "fast.no_inlet"},
                )
            continue  # a fluid body with no opening is a sealed pocket: it rests, and conducts
        if not any(p.kind == OUTLET and p.zone == index for p in ports):
            raise FastInvalid(
                f"{name} has an inlet and no pressure outlet: the flow has nowhere to leave. Give "
                "it a pressure outlet",
                details={"code": "fast.no_outlet", "zone": name},
            )
    if not any(p.kind == INLET for p in ports):
        raise FastInvalid(
            "the case has no velocity inlet on a fluid: Fast mode needs one to drive the flow",
            details={"code": "fast.no_inlet"},
        )
    return ports, zones


def _boundary(
    session: Session,
    node: Any,  # Any: a boundary Node
    tag: Any,  # Any: a tag Node
    triangles: Any,  # Any: (n, 3, 3) ndarray
    index: int,
    zone: Zone,
    thermal: bool,
) -> Port | Zone | None:
    """One boundary on one body: a port, the zone with its outside's condition, or None."""
    from stokes.run.snapshot import _along_normal, _number, _unit

    kind = str(node.params.get("kind") or "wall")
    params = node.params
    if kind in WALL_KINDS:
        if str(params.get("shear") or "no_slip") == "slip":
            raise FastUnsupportedBoundary(
                f"{node.name} is a slip wall; Fast mode models no-slip walls only for now",
                details={"boundary": node.id, "kind": kind},
            )
        return _thermal_wall(session, node, tag, triangles, index, zone) if thermal else None
    if kind not in PORT_KINDS or zone.kind == "solid":
        raise FastUnsupportedBoundary(
            f"{node.name} is a {kind.replace('_', ' ')}; Fast mode models velocity inlets, "
            "pressure outlets and no-slip walls for now",
            details={
                "boundary": node.id,
                "kind": kind,
                "supported": sorted([*PORT_KINDS, *WALL_KINDS]),
            },
        )
    outward = _outward(session, tag.id)
    # A value that varies in time is held at its time-mean: the preview is steady.
    means = _means(session.require_project(), node)
    if kind == "velocity_inlet":
        direction = str(params.get("direction") or "normal")
        speed = means.get("velocity", _number(params.get("velocity")))
        if direction == "components":
            velocity = (
                _number(params.get("velocity_x")),
                _number(params.get("velocity_y")),
                _number(params.get("velocity_z")),
            )
        elif direction == "vector":
            unit = _unit(params.get("direction_vector"))
            velocity = (unit[0] * speed, unit[1] * speed, unit[2] * speed)
        else:
            velocity = _along_normal(session, node, speed)
        temperature = (
            means.get("temperature", _number(params.get("temperature"))) if thermal else 0.0
        )
        return port_from_triangles(
            triangles,
            node.name,
            INLET,
            outward,
            velocity=velocity,
            zone=index,
            temperature=temperature if temperature > 0.0 else None,
        )
    gauge = means.get("pressure", _number(params.get("pressure")))
    pressure = gauge / zone.rho  # gauge Pa to kinematic m2/s2
    return port_from_triangles(triangles, node.name, OUTLET, outward, pressure=pressure, zone=index)


def _thermal_wall(
    session: Session, node: Any, tag: Any, triangles: Any, index: int, zone: Zone
) -> Port | Zone | None:  # Any: Nodes and an ndarray
    """A wall's temperature condition: nothing for an adiabatic one, a thermal wall when its faces
    are one plane, else the condition of the rest of the body's outside."""
    from stokes.run.thermal import thermal_values

    values = thermal_values(node.params, "wall", node.name)
    mode = str(values.get("thermal") or "zeroGradient")
    if mode == "zeroGradient":
        return None
    temperature = float(values.get("temperature") or 0.0)
    flux = float(values.get("heat_flux") or 0.0)
    try:
        if mode == "fixedValue":
            return port_from_triangles(
                triangles,
                node.name,
                WALL_TEMPERATURE,
                _outward(session, tag.id),
                zone=index,
                temperature=temperature,
            )
        return port_from_triangles(
            triangles, node.name, WALL_FLUX, _outward(session, tag.id), zone=index, heat_flux=flux
        )
    except FastPortNotPlanar:
        if zone.wall != "adiabatic":
            raise FastUnsupportedBoundary(
                f"{zone.name} has two heated walls that are not flat ({node.name} is the second); "
                "Fast mode takes one condition for the curved outside of a body, and flat faces "
                "of their own",
                details={"boundary": node.id, "zone": zone.name},
            ) from None
        if mode == "fixedValue":
            return replace(zone, wall="temperature", wall_value=temperature)
        return replace(zone, wall="heat_flux", wall_value=flux)


def offset_metres(value: Any) -> float | None:  # Any: a number, a quantity's text, or None
    """A plane's offset in metres: a bare number is metres, text is a length (``"2 [mm]"``), as the
    section and slice commands read theirs."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise FastInvalid("the plane's offset is a length, not a switch", details={"offset": value})
    if isinstance(value, int | float):
        return float(value)
    from stokes.errors import StokesError
    from stokes.units import dimension, parse_quantity, to_si

    length = dimension("length")
    try:
        return float(to_si(parse_quantity(str(value), length), length))
    except (StokesError, ValueError) as exc:
        raise FastInvalid(
            f"the plane's offset is a length, such as 2 [mm]; got {value!r} ({exc})",
            details={"offset": str(value)},
        ) from exc


def _tag_triangles(session: Session, tag: Any, part_id: str) -> Any:  # Any: Node in, ndarray out
    """The tag's triangles on one body (``(n, 3, 3)``, metres), selected as ``tag.anchor`` selects
    them."""
    import numpy as np

    from stokes.api.commands.selection import _member_triangles, _members
    from stokes.geometry.store import GeometryStore
    from stokes.geometry.topology import part_face_count

    store = GeometryStore.of(session)
    found = []
    for member in _members(tag):
        if member.part != part_id:
            continue
        mesh = store.mesh(member.part)
        if mesh is None:
            raise FastInvalid(
                "a body of the case has no surface yet: wait for its operation to finish",
                details={"part": part_id},
            )
        count = part_face_count(mesh)
        ids = np.arange(count, dtype=np.int64) if member.whole else member.ids(None)
        ids = ids[(ids >= 0) & (ids < count)]
        if ids.size:
            found.append(_member_triangles(mesh, ids, member.whole))
    return np.concatenate(found) if found else np.zeros((0, 3, 3))


def _outward(session: Session, tag_id: str) -> tuple[float, float, float] | None:
    """The tag's normal out of the part (``tag.anchor``), or None when it has none."""
    from stokes.run.snapshot import _anchor

    normal = _anchor(session, tag_id).get("normal")
    if not normal or len(normal) != 3:
        return None
    return (float(normal[0]), float(normal[1]), float(normal[2]))

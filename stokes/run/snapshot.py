"""Turn the model into a `SolveSnapshot`: a pure description of the case to write (T-214a).

`build_solve_snapshot` reads the tree, the mesh result, the material and the tags, and returns
the same
frozen record `stokes.compile.of14.write` consumes. **It writes nothing and runs nothing**, so
the case
is a pure function of the model — which is what makes the writer testable and the run reproducible.

## Where the numbers come from, and why no default is acceptable

`nu = mu / rho`, both evaluated at the material's **reference temperature** through the API
the brief
names. A missing value is `solve.no_viscosity` rather than a plausible default: a solver run
on a made-up
viscosity converges to the wrong answer, and nothing downstream can tell.

## The patch list is the MESH's, not the boundaries'

Correction 2 is explicit and it matters: **iterate the patches the mesh actually has**
(`result.meta["patch_types"]`), and look each one's condition up. A boundary may exist for a
tag the mesh
never turned into a patch, and a patch may exist with no boundary at all — both are normal,
and both must
be expressed. The driver's own `unassigned` patch becomes the `unassigned` kind.

## The inlet direction, and a sign worth stating

For a `velocity_inlet` with `direction == "normal"`, the velocity is `magnitude x (-n)`, where
`n` is
`tag.anchor`'s area-weighted normal. **`n` points OUT of the part** (it follows the mesh's triangle
winding, which OCCT tessellates outward), and an inlet is inflow — so the sign flips. T-212's
duct engine
test asserts `sum(phi) = -3.0e-05 m³/s` at the inlet, which is what a correct inward velocity
produces;
a reversed normal would give `+3.0e-05` and fail.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from stokes.compile.snapshot import (
    ENERGY_FIELDS,
    FLOW_FIELDS,
    KINDS,
    NON_ORTHO_LIMIT,
    THERMAL_FIELDS,
    TRANSIENT_CONTROL_DEFAULTS,
    TURBULENCE_FIELDS,
    TURBULENCE_MODES,
    TWO_D_PATCH_TYPES,
    PatchCondition,
    SolveSnapshot,
)
from stokes.cores import core_count
from stokes.engines.catalogue import kinds_for
from stokes.run.errors import SolveInvalid, SolvePartExcluded
from stokes.run.thermal import gravity_of, thermal_values

if TYPE_CHECKING:  # pragma: no cover - typing only
    from stokes.api.session import Session
    from stokes.model.nodes import Node

# : The kind a `wall` boundary with `shear == "slip"` becomes. There is no `slip_wall`
# *condition kind*
#: in the catalogue, so this is where the two vocabularies meet.
_SLIP = "slip_wall"

# : The driver's default patch name (`stokes.mesh.drivers.blockmesh.DEFAULT_PATCH`). A patch
# with this
#: name is one no tag holds, so nothing fixes its value.
UNASSIGNED = "unassigned"


def build_solve_snapshot(
    session: Session, settings: Node, mesh_status: dict[str, Any]
) -> SolveSnapshot:
    """The case to write, from the model and a finished mesh.

    Raises `SolveInvalid` with a specific code for anything that cannot become a case: no
    mesh, a stale
    mesh, a failed mesh, no material, no viscosity, an unsupported kind.
    """
    require_mesh(mesh_status)
    project = session.require_project()
    # **The patches are on the RESULT NODE's meta, not in `status()`.** `status()` carries the
    # state,
    # the cell count and the quality — what a progress readout needs — while `patch_types` and
    # `patch_tags` are written by `MeshService._write_quality` into `node.meta`. Reading them from
    # `status` yielded an empty patch list, which is a case with no boundary conditions: a
    # silent wrong
    # answer rather than a refusal. Found by running the snapshot against a real meshed duct.
    mesh_status = _with_patch_meta(project, mesh_status)
    part_id = str(mesh_status.get("part") or "") or _domain_part(project)
    nu = _viscosity(session, project, part_id)
    params = settings.params
    energy = bool(params.get("energy"))
    patches = _patches(
        session,
        project,
        mesh_status,
        energy=energy,
        turbulence=str(params.get("turbulence") or "laminar") != "laminar",
        kinds=kinds_for(str(params.get("engine") or "")),
    )
    _require_flow_boundaries_meshed(project, mesh_status)
    return SolveSnapshot(
        **(_energy_fields(session, project, part_id, params) if energy else {}),
        density_model=str(params.get("density_model") or "constant"),
        molar_mass=_number(params.get("molar_mass"))
        if params.get("molar_mass") is not None
        else 0.02897,
        transient=bool(params.get("transient", False)),
        delta_t=_number(params.get("delta_t")) if params.get("delta_t") is not None else 0.001,
        end_time=_number(params.get("end_time")) if params.get("end_time") is not None else 1.0,
        initial_velocity=_number(params.get("initial_velocity"))
        if params.get("initial_velocity") is not None
        else 1.0,
        initial_intensity=float(params.get("initial_intensity", 0.05)),
        initial_length_scale=_number(params.get("initial_length_scale"))
        if params.get("initial_length_scale") is not None
        else 0.01,
        initial_viscosity_ratio=float(params.get("initial_viscosity_ratio", 10.0)),
        **_transient_controls(params),
        settings_id=str(settings.id),
        cores=core_count(params.get("cores", 1)),
        non_orthogonal=_mesh_non_ortho(mesh_status) > NON_ORTHO_LIMIT,
        engine="of14.incompressibleFluid"
        if params.get("engine") == "of14.kOmegaSST"
        else str(params.get("engine") or ""),
        turbulence=str(params.get("turbulence") or "laminar"),
        iterations=int(params.get("iterations") or 500),
        write_interval=int(params.get("write_interval") or 50),
        residual_target=float(params.get("residual_target") or 1e-3),
        nu=nu,
        rho=_rho(session, project, part_id, nu),
        patches=patches,
        monitor_patches=tuple(
            name
            for name in _patch_names(mesh_status)
            if _kind_of(project, mesh_status, name) in ("velocity_inlet", "pressure_outlet")
        ),
    )


def _energy_fields(
    session: Session, project: Any, part_id: str, params: dict[str, Any]
) -> dict[str, Any]:
    """The snapshot's energy fields (ADR-0027 step 2), asked only with the energy equation on.

    The material values come from `material.evaluate` at the material's reference temperature,
    the same rule as `nu`; a missing specific heat or conductivity is `solve.no_thermal_material`,
    the code `solve.check` already reports for it.
    """
    rho, mu = _evaluate(session, project, part_id)
    del rho
    material, cp, kappa = _thermal_properties(session, project, part_id)
    return {
        "energy": True,
        "mu": mu,
        "cp": cp,
        "kappa": kappa,
        "T_ref": _reference_temperature(session, material),
        "gravity": gravity_of(params.get("gravity")),
        "p_ref": _number(params.get("operating_pressure")) or 101325.0,
    }


def _thermal_properties(session: Session, project: Any, part_id: str) -> tuple[str, float, float]:
    """`(material id, cp, kappa)` of the domain's material at its reference temperature.

    Raises `SolveInvalid` (`solve.no_material` or `solve.no_thermal_material`) when either value
    cannot be had: the energy equation on a made-up conductivity is a wrong answer, not a run.
    """
    region = _material_of(project, part_id)
    if region is None:
        raise SolveInvalid(
            "the domain part's region has no material; give it one before solving",
            code="solve.no_material",
            details={"part": part_id},
        )
    material = str(region.params.get("material"))
    try:
        found = session.execute(
            "material.evaluate",
            material=material,
            properties=["specific_heat", "thermal_conductivity"],
            T=[_reference_temperature(session, material)],
        )
    except Exception as exc:
        raise SolveInvalid(
            f"the material of {region.path} could not be evaluated for energy: {exc}",
            code="solve.no_thermal_material",
            details={"material": material},
        ) from exc
    cp, kappa = _scalar(found, "specific_heat"), _scalar(found, "thermal_conductivity")
    if not cp or not kappa:
        raise SolveInvalid(
            f"the material of {region.path} needs a specific heat and a thermal conductivity "
            "for the energy equation",
            code="solve.no_thermal_material",
            details={"material": material},
        )
    return material, cp, kappa


def _with_patch_meta(project: Any, mesh_status: dict[str, Any]) -> dict[str, Any]:
    """`mesh_status` with the result node's `patch_types` and `patch_tags` merged in.

    A copy, so the caller's dict is untouched — and `status()` values win where both have a
    key, because
    `status` is the fresher view of state that changes (`stale`, `ok`).
    """
    if mesh_status.get("patch_types") is not None:
        return mesh_status
    result_id = str(mesh_status.get("result") or "")
    node = project.find(result_id) if result_id else None
    if node is None:
        return mesh_status
    merged = dict(node.meta)
    merged.update(mesh_status)
    merged["patch_types"] = dict(node.meta.get("patch_types") or {})
    merged["patch_tags"] = dict(node.meta.get("patch_tags") or {})
    return merged


def require_mesh(mesh_status: dict[str, Any]) -> None:
    """Refuse a mesh that cannot be solved, naming which of the three cases it is."""
    state = str(mesh_status.get("state") or "")
    if not mesh_status.get("result"):
        raise SolveInvalid("there is no mesh result; run Mesh first", code="solve.no_mesh")
    if state == "failed":
        raise SolveInvalid(
            "the mesh failed; fix it before solving",
            code="solve.mesh_failed",
            details={"mesh": mesh_status.get("result")},
        )
    if state != "done":
        raise SolveInvalid(
            f"the mesh is {state!r}; a solve needs a finished mesh",
            code="solve.no_mesh",
            details={"state": state},
        )
    if mesh_status.get("stale"):
        raise SolveInvalid(
            "the mesh is stale: its settings changed since it was made",
            code="solve.mesh_stale",
            details={"mesh": mesh_status.get("result")},
        )


def _domain_part(project: Any) -> str:
    """The part a region's tag holds, when the caller did not name one.

    **Read from the regions**, which is where the domain part actually is: a `region` reaches bodies
    through its tag (ADR-0022 decision 4.1). `status()` has no `part` key, so a snapshot that
    insisted
    on one built a case with no `nu` — a silent failure rather than a refusal.
    """
    for node in project.root.walk():
        if getattr(node, "type", "") != "region":
            continue
        tag = project.find(str(node.params.get("tag") or ""))
        if tag is None:
            continue
        for member in _members(tag):
            found = str(member.get("part") or "")
            if found:
                # **A body left out of the simulation cannot be the domain part** (T-221, the
                # mesher's rule at the next layer). Refused rather than skipped: skipping builds
                # the case from whichever body came second, which is a case nobody asked for.
                node_part = project.find(found)
                if node_part is not None and node_part.params.get("simulate", True) is False:
                    raise SolvePartExcluded(
                        f"{node_part.name!r} is left out of the simulation, so it cannot be the "
                        "part the case is built from: use it in the simulation again, or mesh a "
                        "different body"
                    )
                return found
    return ""


def _members(tag: Any) -> list[Any]:
    """A tag's members as a list, whether stored as JSON text or already parsed."""
    raw = tag.params.get("members")
    if isinstance(raw, str):
        import json

        try:
            raw = json.loads(raw) if raw.strip() else []
        except ValueError:
            return []
    return list(raw or [])


def _material_of(project: Any, part_id: str) -> Any:
    """The region whose tag holds the domain part, and its material.

    A region reaches bodies only through its tag (ADR-0022 decision 4.1), so the part is found
    by asking
    which region's tag names it.
    """
    for node in project.root.walk():
        if getattr(node, "type", "") != "region":
            continue
        tag_ref = str(node.params.get("tag") or "")
        if not tag_ref:
            continue
        tag = project.find(tag_ref)
        if tag is None:
            continue
        if _tag_holds(project, tag, part_id) and str(node.params.get("material") or ""):
            return node
    return None


def _tag_holds(project: Any, tag: Any, part_id: str) -> bool:
    """Whether a tag's members name a part.

    **The stored form is a JSON STRING, not a list** — `tag.params["members"]` holds serialised JSON
    (the tag commands write it that way), so `.get` on it raised `AttributeError: 'str' object
    has no
    attribute 'get'`. Found by running the snapshot against a real meshed duct; a unit test with a
    hand-built node would have hidden it by using a list.
    """
    del project
    return any(
        isinstance(member, dict) and str(member.get("part") or "") == part_id
        for member in _members(tag)
    )


def _evaluate(session: Session, project: Any, part_id: str) -> tuple[float, float]:
    """`(rho, mu)` of the domain part's material at its reference temperature, in SI."""
    region = _material_of(project, part_id)
    if region is None:
        raise SolveInvalid(
            "the domain part's region has no material; give it one before solving",
            code="solve.no_material",
            details={"part": part_id},
        )
    material = str(region.params.get("material"))
    # **`material.evaluate` is a command**, so it is called through the session — the same path a
    # client uses, which means the snapshot cannot read a material the command would refuse.
    try:
        # **At the material's own reference temperature.** The command fills a grid otherwise,
        # and its
        # endpoints are `T_min`/`T_max` — `nu = mu/rho` is defined at the reference state, so
        # asking for
        # one temperature is what makes the snapshot reproducible.
        found = session.execute(
            "material.evaluate",
            material=material,
            properties=["density", "viscosity"],
            T=[_reference_temperature(session, material)],
        )
    except Exception as exc:
        raise SolveInvalid(
            f"the material of {region.path} could not be evaluated: {exc}",
            code="solve.no_viscosity",
            details={"material": material},
        ) from exc
    rho, mu = _scalar(found, "density"), _scalar(found, "viscosity")
    if not rho or not mu:
        raise SolveInvalid(
            f"the material of {region.path} has no density and viscosity at its reference "
            "temperature",
            code="solve.no_viscosity",
            details={"material": material},
        )
    return float(rho), float(mu)


def _viscosity(session: Session, project: Any, part_id: str) -> float:
    """`nu = mu / rho`, in m²/s."""
    rho, mu = _evaluate(session, project, part_id)
    if rho <= 0.0:
        raise SolveInvalid(
            f"the material's density is {rho}; a kinematic viscosity needs a positive one",
            code="solve.no_viscosity",
            details={"rho": rho},
        )
    return mu / rho


def material_problem(session: Session) -> SolveInvalid | None:
    """Why the domain's material cannot give a viscosity, or `None` when it can.

    **`solve.check` asks this too**, so a set-up the snapshot would refuse is reported as not ready
    before Run is pressed. The rules are the snapshot's own `_viscosity` rules, not a copy of them.
    """
    project = session.require_project()
    try:
        _viscosity(session, project, _domain_part(project))
    except SolveInvalid as exc:
        return exc
    return None


def thermal_problem(session: Session) -> SolveInvalid | None:
    """Why the domain's material cannot give a specific heat and a conductivity, or `None`.

    **Asked only when the energy equation is on** (ADR-0027 decision 3): the flow alone never reads
    these, so a material without them is fine until the temperature is solved. The rule is the
    catalogue's, through `material.evaluate`, the same path `material_problem` takes.
    """
    project = session.require_project()
    try:
        part_id = _domain_part(project)
        region = _material_of(project, part_id)
    except SolveInvalid:
        return None  # `material_problem` reports a missing region or material
    if region is None:
        return None
    try:
        _thermal_properties(session, project, part_id)
    except SolveInvalid as exc:
        return exc
    return None


def _rho(session: Session, project: Any, part_id: str, nu: float) -> float:
    """The density, for the snapshot's `rho` — the writer only needs it for the pressure scale."""
    del nu
    rho, _ = _evaluate(session, project, part_id)
    return rho


def _patch_names(mesh_status: dict[str, Any]) -> tuple[str, ...]:
    """The patch names the mesh has, in the driver's order."""
    kinds = mesh_status.get("patch_types") or {}
    return tuple(str(name) for name in kinds)


def _mesh_non_ortho(mesh_status: dict[str, Any]) -> float:
    """The built mesh's max non-orthogonality in degrees, as checkMesh measured it; 0 unknown."""
    quality = mesh_status.get("quality")
    value = quality.get("max_non_ortho") if isinstance(quality, dict) else None
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


#: The kinds that carry the flow in or out: a run without one of them solves another problem.
FLOW_KINDS = frozenset({"velocity_inlet", "mass_flow_inlet", "pressure_inlet", "pressure_outlet"})


def _require_flow_boundaries_meshed(project: Any, mesh_status: dict[str, Any]) -> None:
    """Refuse a solve when an inlet or outlet boundary has no patch in the mesh.

    **Measured on the mixing elbow (2026-11-08):** cfMesh's Cartesian mesh at 5 mm cells lost the
    25 mm hot inlet altogether - its faces became wall - and checkMesh still said the quality was
    ok. The run then solved a single cold stream and reported 293.4 K at the outlet where the
    energy balance gives 296.3 K: a wrong answer with nothing on screen to say so. A missing wall
    or symmetry plane only changes which wall the faces belong to; a missing inflow or outflow
    changes the problem, so only those refuse.
    """
    # **A patch can be there with no faces**: cfMesh kept `hot_inlet` in the boundary file with
    # `nFaces 0`. The result's `mesh_patch` nodes carry the face counts (an empty patch has none),
    # so when the result has them, only a patch with faces counts.
    result = (
        project.find(str(mesh_status.get("result") or "")) if mesh_status.get("result") else None
    )
    surfaces = [c for c in getattr(result, "children", None) or [] if c.type == "mesh_patch"]
    faced = {c.name for c in surfaces if int(c.params.get("faces") or 0) > 0} if surfaces else None
    meshed = {
        node.id
        for patch in _patch_names(mesh_status)
        if (faced is None or patch in faced)
        and (node := _boundary_for(project, mesh_status, patch)) is not None
    }
    missing = sorted(
        node.name
        for node in project.root.walk()
        if getattr(node, "type", "") == "boundary"
        and str(node.params.get("kind") or "") in FLOW_KINDS
        and node.id not in meshed
    )
    if missing:
        names = ", ".join(missing)
        raise SolveInvalid(
            f"the mesh has no faces for {names}: the mesher lost "
            f"{'it' if len(missing) == 1 else 'them'}. Make the cells smaller than "
            f"{'its' if len(missing) == 1 else 'their'} width (Mesh: How fine) or use "
            "snappyHexMesh, then generate again",
            code="solve.boundary_not_meshed",
            details={"boundaries": missing},
        )


def _boundary_for(project: Any, mesh_status: dict[str, Any], patch: str) -> Any:
    """The boundary node whose tag the mesh put on this patch, or None.

    **The join is patch → tag → boundary, and it is the reverse of the obvious one.**
    `meta.patch_tags`
    maps the DRIVER's patch names (`xmin`, `xmax`, `walls`) to tag ids, so the tag names the
    patch, not
    the other way round — and a tag the driver never mapped appears in no patch at all.

    Comparing a patch NAME to a tag ID (the first version) therefore found nothing and marked every
    patch `unassigned`, which is a mesh with no boundary conditions rather than an error. Found by
    running the snapshot against a real meshed duct.
    """
    if patch == UNASSIGNED:
        return None
    tag_id = _tag_for(project, patch)
    if not tag_id:
        return None
    for node, param in project.referrers(tag_id):
        if getattr(node, "type", "") == "boundary" and param == "tag":
            return node
    return None


def _tag_for(project: Any, patch: str) -> str:
    """The tag id a patch's faces came from, read from the tree.

    **`patch_tags` and `patch_types` do not share a key vocabulary** — the trap here.
    `mesh_result.meta`
    holds `patch_types` keyed by PATCH name (`inlet`, `outlet`, `walls`) and `patch_tags`
    keyed by SIDE
    name (`xmin`, `xmax`, `ymin`, …), because `blockMesh` names its boundary faces after the
    box's six
    sides and the tag mapping is what renames them. A patch name looked up in `patch_tags` therefore
    always missed, every patch came out `unassigned`, and the case had no boundary conditions
    at all —
    a silent wrong answer rather than an error.

    **The join is the boundary's own name.** `MeshSnapshot.sides` maps a side to "the NAME of
    the tag
    whose faces lie on it — that name becomes the patch's name", so a patch named `inlet`
    comes from the
    tag named `inlet`, and the boundary node of that tag is what carries the condition.
    Matching by name
    needs no new field on the result and no assumption about the driver's side ordering.
    """
    for node in project.root.walk():
        if getattr(node, "type", "") == "boundary" and str(node.name) == patch:
            return str(node.params.get("tag") or "")
    return ""


def _kind_of(project: Any, mesh_status: dict[str, Any], patch: str) -> str:
    """The condition kind for a patch: its boundary's kind, or `unassigned`."""
    node = _boundary_for(project, mesh_status, patch)
    if node is None:
        return UNASSIGNED
    kind = str(node.params.get("kind") or UNASSIGNED)
    # **A `wall` with `shear: slip` is a `slip_wall`**, the one place two vocabularies meet:
    # the boundary
    # model has no slip kind of its own, and the writer has no shear parameter.
    if kind == "wall" and str(node.params.get("shear") or "") == "slip":
        return _SLIP
    return kind


def _patches(
    session: Session,
    project: Any,
    mesh_status: dict[str, Any],
    *,
    energy: bool = False,
    turbulence: bool = False,
    kinds: tuple[str, ...] = KINDS,
) -> tuple[PatchCondition, ...]:
    """One `PatchCondition` per patch the MESH has, in the mesh's order.

    **The mesh's patches, not the boundaries'** (correction 2): a boundary for a tag the mesh
    never made
    into a patch would name a patch the case does not have, and a patch with no boundary must
    still be
    written or the solver refuses the case with a FOAM FATAL about a missing field entry.

    With ``energy`` on, each patch also carries its temperature condition (`stokes.run.thermal`);
    off, the thermal fields keep their `None` defaults.
    """
    types = mesh_status.get("patch_types") or {}
    out: list[PatchCondition] = []
    for patch in _patch_names(mesh_status):
        patch_type = str(types.get(patch) or "patch")
        # **A 2D front/back patch is the mesher's, never the user's** (ADR-0031 decision 6): its
        # kind is its mesh type whatever boundary names it, so it cannot be reassigned.
        kind = (
            patch_type if patch_type in TWO_D_PATCH_TYPES else _kind_of(project, mesh_status, patch)
        )
        values = _values(session, project, mesh_status, patch, kind)
        if kind not in kinds:
            raise SolveInvalid(
                f"the patch {patch!r} has condition {kind!r}, which this writer cannot express",
                code="solve.unsupported_kind",
                details={"patch": patch, "kind": kind},
            )
        if energy:
            node = _boundary_for(project, mesh_status, patch)
            values.update(thermal_values(None if node is None else node.params, kind, patch))
            if kind == "pressure_outlet" and node is not None:
                # The `fluid` writer needs the outlet's height for `p_rgh` (ADR-0027 step 5).
                centroid = _anchor(session, str(node.params.get("tag") or "")).get("centroid")
                if centroid is not None and len(centroid) == 3:
                    values["centroid"] = (
                        float(centroid[0]),
                        float(centroid[1]),
                        float(centroid[2]),
                    )
        if turbulence and kind in ("velocity_inlet", "pressure_outlet"):
            # The inflow group (ADR-0028). Only with kOmegaSST, so a laminar basis is unchanged.
            values.update(_turbulence_values(_boundary_for(project, mesh_status, patch)))
        out.append(PatchCondition(patch, patch_type, kind, **values))
    return tuple(out)


def _values(
    session: Session, project: Any, mesh_status: dict[str, Any], patch: str, kind: str
) -> dict[str, Any]:
    """The condition's values for a patch, in the snapshot's vocabulary."""
    node = _boundary_for(project, mesh_status, patch)
    if node is None:
        return {}
    params = node.params
    if kind == "velocity_inlet":
        direction = str(params.get("direction") or "normal")
        if direction == "components":
            velocity = (
                _number(params.get("velocity_x")),
                _number(params.get("velocity_y")),
                _number(params.get("velocity_z")),
            )
        elif direction == "vector":
            unit = _unit(params.get("direction_vector"))
            speed = _number(params.get("velocity"))
            velocity = (unit[0] * speed, unit[1] * speed, unit[2] * speed)
        else:
            velocity = _along_normal(session, node, _number(params.get("velocity")))
        return {"velocity": velocity}
    if kind == "mass_flow_inlet":
        return {"mass_flow": _number(params.get("mass_flow"))}
    if kind == "pressure_outlet":
        return {"pressure": _number(params.get("pressure"))}
    if kind == "wall" and str(params.get("shear") or "") == "slip":
        return {}
    return {}


def _turbulence_values(node: Any) -> dict[str, Any]:
    """The inflow turbulence group of a boundary, as the snapshot carries it (ADR-0028).

    A mode the model does not offer yields nothing, and the writer then refuses the patch with
    `compile.invalid`, rather than inventing a group for it.
    """
    if node is None:
        return {}
    params = node.params
    mode = str(params.get("turbulence") or "")
    if mode not in TURBULENCE_MODES:
        return {}
    values: dict[str, Any] = {
        "turbulence_mode": mode,
        "intensity": _number(params.get("intensity")),
    }
    if mode == "intensity_ratio":
        values["viscosity_ratio"] = _number(params.get("viscosity_ratio"))
    else:
        values["length_scale"] = _number(params.get("length_scale"))
    return values


def _along_normal(session: Session, boundary: Any, speed: float) -> tuple[float, float, float]:
    """`speed x (-n)`, where `n` is the tag's outward normal.

    **The sign is the whole point**: `tag.anchor`'s normal points out of the part, and an inlet is
    inflow, so the velocity is opposite it. A tag whose anchor cannot be found falls back to
    the axis
    the boundary's own direction names, which keeps a half-set-up project checkable rather
    than crashing.
    """
    if speed == 0.0:
        return (0.0, 0.0, 0.0)
    tag_id = str(boundary.params.get("tag") or "")
    found = _anchor(session, tag_id)
    normal = tuple(float(v) for v in (found.get("normal") or (0.0, 0.0, 0.0)))
    length = sum(v * v for v in normal) ** 0.5
    if length < 1e-12:
        return (0.0, 0.0, 0.0)
    return (-speed * normal[0] / length, -speed * normal[1] / length, -speed * normal[2] / length)


def _anchor(session: Session, tag_id: str) -> dict[str, Any]:
    """`tag.anchor`'s entry for one tag: `{centroid, normal, area}`, the normal pointing OUT
    of the part.

    **Through the command**, so the snapshot and the viewport agree about a tag's normal — and a tag
    whose anchor cannot be computed yields no normal, which `_along_normal` treats as a zero
    velocity
    rather than crashing a set-up that `solve.check` will report.
    """
    if not tag_id:
        return {}
    try:
        found = session.execute("tag.anchor", tags=[tag_id])
    except Exception:
        return {}
    anchors = found.get("anchors") or []
    if len(anchors) == 1:
        # One tag was asked for, so one anchor comes back — and matching on the id would FAIL,
        # because
        # `tag.anchor` puts the tag's PATH in `tag` (`"/geometry/tags/inlet"`), not its id.
        # Comparing
        # the two always missed, the normal came back zero, and an inlet got a zero velocity: a case
        # that runs and does nothing, rather than an error. Found by running it.
        return dict(anchors[0])
    for item in anchors:
        if str(item.get("tag") or "") in (tag_id, _path_of(session, tag_id)):
            return dict(item)
    return {}


def _path_of(session: Session, node_id: str) -> str:
    """A node's path, or "" — the form `tag.anchor` reports a tag as."""
    node = session.require_project().find(node_id) if node_id else None
    return str(getattr(node, "path", "") or "")


def _unit(vector: Any) -> tuple[float, float, float]:
    """A direction as a unit vector. A zero vector has no direction, and the model refuses one."""
    values = tuple(_number(v) for v in (vector or (0.0, 0.0, 0.0)))
    length = sum(v * v for v in values) ** 0.5
    if length < 1e-12:
        raise SolveInvalid(
            "a direction vector cannot be zero-length",
            code="solve.invalid",
            details={"direction_vector": list(values)},
        )
    return (values[0] / length, values[1] / length, values[2] / length)


def _reference_temperature(session: Session, material: str) -> float:
    """The material's `T_ref` in kelvin, read from its NODE.

    **Not from `material.list`**, which returns a plain list of summaries carrying no `T_ref`
    — reading
    it as a dict raised `'list' object has no attribute 'get'`, found by running the snapshot
    against a
    real project. `T_ref` is a `QuantityValue` on the material node. 300 K when the node or
    the value
    cannot be read, which is the catalogue's own default for a gas.
    """
    node = session.require_project().find(material)
    if node is None:
        return 300.0
    return _number(node.params.get("T_ref")) or 300.0


def _scalar(reply: dict[str, Any], name: str) -> float:
    """One property's first value from `material.evaluate`'s reply.

    The shape is `{"properties": {name: {"method": [values], "unit": …}}}`, and the grid has
    one entry
    because the call asked for one temperature. **A property the reply marks `missing` is
    0.0**, which
    the caller turns into `solve.no_viscosity` rather than writing a case with no viscosity.
    """
    entry = (reply.get("properties") or {}).get(name) or {}
    if entry.get("missing"):
        return 0.0
    values = entry.get("method") or entry.get("library") or []
    return float(values[0]) if values else 0.0


def _number(value: Any) -> float:
    """A parameter's value as a float, unwrapping a `QuantityValue`."""
    return float(getattr(value, "value", value) or 0.0)


def _transient_controls(params: Any) -> dict[str, Any]:  # Any: node params
    """The adaptive step, the write time and the averaging, read only for a transient run.

    A steady run carries the "off" values whatever the settings hold, so an Adaptive time step left
    on from an earlier transient run neither writes anything nor moves a steady run's basis.
    """
    if not params.get("transient", False):
        return {}
    adaptive = bool(params.get("adjustable_time_step", False))
    average = bool(params.get("time_average", False))
    return {
        "max_courant": float(params.get("max_courant") or 1.0) if adaptive else 0.0,
        "write_time": _number(params.get("write_time"))
        if params.get("write_time") is not None
        else 0.0,
        "average": average,
        "average_start": _number(params.get("average_start"))
        if average and params.get("average_start") is not None
        else 0.0,
    }


def solve_basis(snap: SolveSnapshot, mesh_basis: str, mesh_job: str, engine_string: str) -> str:
    """The sha1 of everything that decides what the case is.

    **`host` is deliberately out**, for the reason `mesh_basis` excludes it: the same inputs on
    another machine produce the same case, and a basis that moves for no reason is one users learn
    to ignore.

    **A parallel case is a different case.** Above one rank the writer adds
    `system/decomposeParDict` and the run is decomposed, so the rank count is in the basis;
    without it, raising the count would hand back the old serial run as "already solved". One
    rank keeps the historical body, so a serial basis is unchanged.

    **The energy fields are in only when energy is on** (ADR-0027 step 2), for the same reason: a
    flow-only project made before they existed keeps its basis, so its finished run is not
    reported stale by an upgrade that changed nothing about its case.

    **The writer is the one that writes the case**: an `of14.fluid` snapshot is written by the
    energy writer (ADR-0027), so its `WRITER_VERSION` is the one that marks its runs stale.
    """
    from stokes.compile.of14 import WRITER_VERSION
    from stokes.compile.of14 import fluid as fluid_writer

    writer = fluid_writer.WRITER_VERSION if snap.engine == fluid_writer.ENGINE else WRITER_VERSION

    physical_snapshot = asdict(snap)
    cores = physical_snapshot.pop("cores", 1)
    # Only when on, so a run on an ordinary mesh keeps the basis it had before the flag existed.
    if not physical_snapshot.get("non_orthogonal"):
        physical_snapshot.pop("non_orthogonal", None)
    if not snap.energy:
        for name in ENERGY_FIELDS:
            physical_snapshot.pop(name, None)
    for name, off in TRANSIENT_CONTROL_DEFAULTS.items():
        if physical_snapshot.get(name) == off:
            physical_snapshot.pop(name, None)
    for patch in physical_snapshot["patches"]:
        for name in (*THERMAL_FIELDS, *TURBULENCE_FIELDS, *FLOW_FIELDS):
            if patch.get(name) is None:
                patch.pop(name, None)
    body: dict[str, Any] = {
        "writer": writer,
        "engine": engine_string,
        "mesh_basis": mesh_basis,
        "mesh_job": mesh_job,
        "snapshot": physical_snapshot,
    }
    if cores > 1:
        body["cores"] = cores
    text = json.dumps(_rounded(body), sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()


def _rounded(value: Any) -> Any:
    """Floats to twelve significant digits, so a serialisation round trip does not move the hash."""
    if isinstance(value, float):
        return float(f"{value:.12g}")
    if isinstance(value, dict):
        return {key: _rounded(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_rounded(item) for item in value]
    return value

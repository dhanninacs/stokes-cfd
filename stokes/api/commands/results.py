"""The `results.*` and `results.function_*` commands: Fluent-style reports, plots and calculations
on a finished run's surfaces (docs/engineering/results-postprocessing-gaps.md).

A **surface** is a boundary patch (a `mesh_patch` node id, its tree path or its patch name) or the
word `slice` - the run's cut plane, or with `plane` (`"ox,oy,oz,nx,ny,nz"`, as the results routes
take it) any plane the run has cut. A **field** is a field the run has (`U_mag`, `p`, `T`...), a
component (`U_x`, `U_y`, `U_z`), a saved field function's name, or an expression over those
(`0.5 * U_mag^2 + p`), evaluated by `stokes.post.calculator`.

Every command here **reads caches and computes in numpy; none goes to a host** except when a
`plane` names a cut that is not cached yet (the store then asks for it once, as the slice route
does). None mutates the model except `results.function_save` and `results.function_remove`, which
are one undo step each.

The geometry is the mesh's surfaces artifact for a patch (outward triangles, so a flow rate is
positive leaving the fluid) and the cut's own triangles for a slice (positive along its normal).
The density for mass flow is the domain material's at its reference temperature, or the
`density` given.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from stokes.api.registry import CommandContext, ParamDef, command
from stokes.errors import ExpressionError, StokesError, ValidationError
from stokes.model import AddNode, Command, Composite, RemoveNode, SetParam

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np

    from stokes.model.nodes import Node
    from stokes.post.integrals import Surface

log = logging.getLogger(__name__)

RUN = ParamDef("run", "string", required=False, description="The run; default: the current one.")
PLANE = ParamDef(
    "plane",
    "string",
    required=False,
    description="For the surface 'slice': which cut, as 'ox,oy,oz,nx,ny,nz' in metres; "
    "default: the run's own mid-plane.",
)
SURFACES = ParamDef(
    "surfaces",
    "array",
    description="Boundary patches (node id, path or patch name) and/or 'slice'.",
)
FIELD = ParamDef(
    "field",
    "string",
    description="A field (U_mag, p, T), a component (U_x), a field function's name or an "
    "expression over them, e.g. '0.5 * U_mag^2 + p'.",
)
DENSITY = ParamDef(
    "density",
    "number",
    required=False,
    description="kg/m3 for mass flow and mass-weighted values; default: the fluid material's.",
)

#: The most surfaces one report names: a report table is read by a person.
MAX_SURFACES = 64


class ResultsUnavailable(StokesError):
    """The run has no field data to compute from (not finished, or its cache is gone)."""

    code = "results.unavailable"


@dataclass
class _Loaded:
    """One surface's geometry and every point field the run holds on it."""

    surface: Surface
    fields: dict[str, np.ndarray]
    units: dict[str, str]
    kind: str  # "patch" | "slice"
    node_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)  # Any: JSON


def _store(ctx: CommandContext) -> Any:  # Any: RunStore (run.py owns its construction)
    from stokes.api.commands.run import _store as run_store

    return run_store(ctx)


def _run_fields(ctx: CommandContext, run: str | None) -> tuple[str, memoryview, dict[str, Any]]:
    """The run's id, its `.stkfld` bytes and header; refused when there are none."""
    from stokes.run.fields import read_header

    store = _store(ctx)
    status = store.fields_status(run)
    if status.get("surfaces") != "ok":
        raise ResultsUnavailable(
            "this run has no field data; finish a run and show its results first"
        )
    run_id = str(status["run"])
    data = memoryview(store.fields_path(run_id).read_bytes())
    return run_id, data, read_header(data)


def _patch_nodes(ctx: CommandContext) -> list[Node]:
    project = ctx.session.require_project()
    return [n for n in project.root.walk() if n.type == "mesh_patch"]


def _find_patch(ctx: CommandContext, ref: str) -> Node:
    project = ctx.session.require_project()
    found = project.find(ref)
    if found is not None and found.type == "mesh_patch":
        return found
    for node in _patch_nodes(ctx):
        if node.name == ref or str(node.params.get("name") or "") == ref:
            return node
    raise ValidationError(
        f"{ref!r} is not a boundary patch of this model, nor 'slice'",
        code="results.no_surface",
        details={"surface": ref},
    )


def _split(
    header_units: dict[str, Any], entry: dict[str, Any], data: Any, header: Any, index: int
) -> tuple[dict[str, np.ndarray], dict[str, str]]:  # Any: buffers
    from stokes.run.fields import field_array

    fields: dict[str, np.ndarray] = {}
    units: dict[str, str] = {}
    for name in entry:
        fields[name] = field_array(data, header, index, name)
        units[name] = str(header_units.get(name, ""))
    return fields, units


def _load(ctx: CommandContext, run: str | None, ref: str, plane: str | None) -> _Loaded:
    """One surface of a run: its triangles and its fields, from the caches."""
    import numpy as np

    from stokes.post.integrals import Surface
    from stokes.run.fields import SLICE_INDEX, read_header, slice_arrays

    run_id, data, header = _run_fields(ctx, run)
    header_units = header.get("units") or {}
    if ref == "slice":
        if plane:
            from stokes.run.planes import parse_plane

            loaded = _store(ctx).slice_artifact_for(run_id, parse_plane(plane))
            if loaded is None:
                raise ResultsUnavailable("that plane could not be cut; is the run's host up?")
            data, header = loaded
            header = read_header(data) if header is None else header
        entry = header.get("slice") or {}
        if not isinstance(entry, dict) or int(entry.get("triangles") or 0) == 0:
            raise ResultsUnavailable("the slice misses the model; move it onto the fluid")
        positions, triangles, _edges = slice_arrays(data, header)
        fields, units = _split(header_units, entry.get("fields") or {}, data, header, SLICE_INDEX)
        extra = {"origin": entry.get("origin"), "normal": entry.get("normal")}
        surface = Surface("slice", np.asarray(positions), np.asarray(triangles))
        return _Loaded(surface, fields, units, "slice", extra=extra)
    from stokes.mesh.service import MeshService
    from stokes.mesh.surfaces import patch_arrays

    node = _find_patch(ctx, ref)
    index = int(node.params.get("index", -1))
    patches = header.get("patches") or []
    if not 0 <= index < len(patches):
        raise ResultsUnavailable(f"the run has no data for {node.name}")
    source = MeshService.of(ctx.session)._patch_source(node.id)
    if source is None:
        raise ResultsUnavailable(f"the surface of {node.name} is not in the mesh cache; remesh")
    _node, mesh_data, mesh_header = source
    positions, triangles, _edges = patch_arrays(mesh_data, mesh_header, index)
    fields, units = _split(header_units, patches[index].get("fields") or {}, data, header, index)
    name = str(node.params.get("name") or node.name)
    surface = Surface(name, np.asarray(positions), np.asarray(triangles))
    return _Loaded(surface, fields, units, "patch", node_id=node.id)


def _density(ctx: CommandContext, given: float | None) -> float | None:
    """The density for mass flow: the one given, else the fluid material's, else None."""
    if given is not None:
        if not given > 0:
            raise ValidationError("density must be a positive number (kg/m3)")
        return float(given)
    try:
        # The snapshot's own lookup, so a report and the solve read one density.
        from stokes.run.snapshot import _domain_part, _evaluate

        project = ctx.session.require_project()
        rho, _mu = _evaluate(ctx.session, project, _domain_part(project))
        return float(rho) if rho > 0 else None
    except StokesError:
        return None
    except Exception:
        log.warning("the density could not be read for a report", exc_info=True)
        return None


def _functions(ctx: CommandContext) -> dict[str, str]:
    """The saved field functions: name -> definition."""
    return {
        str(n.params.get("name")): str(n.params.get("definition") or "")
        for n in _function_nodes(ctx)
    }


def _names(
    loaded: _Loaded, density: float | None
) -> dict[str, tuple[Any, str]]:  # Any: array/float
    """What an expression may read on this surface: scalars, components, coordinates, rho."""
    names: dict[str, tuple[Any, str]] = {}
    for name, values in loaded.fields.items():
        units = loaded.units.get(name, "")
        if values.ndim == 1:
            names[name] = (values, units)
        elif values.ndim == 2 and values.shape[1] == 3:
            for i, axis in enumerate("xyz"):
                names[f"{name}_{axis}"] = (values[:, i], units)
    for i, axis in enumerate("xyz"):
        names[axis] = (loaded.surface.positions[:, i], "m")
    if density is not None:
        names["rho"] = (density, "kg/m3")
    return names


def _values(
    ctx: CommandContext,
    loaded: _Loaded,
    text: str,
    density: float | None,
    functions: dict[str, str] | None = None,
    depth: int = 0,
) -> tuple[np.ndarray, str]:
    """The point values of a field, a component, a field function or an expression, and units."""
    import numpy as np

    from stokes.post.calculator import evaluate_field, names_of

    text = text.strip()
    if not text:
        raise ValidationError("name a field or write an expression", code="results.no_field")
    names = _names(loaded, density)
    if text in names and not isinstance(names[text][0], float):
        values, units = names[text]
        return np.asarray(values, dtype=np.float64), units
    if text in loaded.fields:
        raise ValidationError(
            f"{text} has {loaded.fields[text].shape[1]} components; use {text}_mag or {text}_x",
            code="results.vector_field",
        )
    saved = _functions(ctx) if functions is None else functions
    if depth > 8:
        raise ExpressionError("field functions refer to each other too deeply (a loop?)")
    if text in saved:
        return _values(ctx, loaded, saved[text], density, saved, depth + 1)
    for name in names_of(text):
        if name not in names and name in saved:
            values, units = _values(ctx, loaded, saved[name], density, saved, depth + 1)
            names[name] = (values, units)
    result = evaluate_field(text, names, loaded.surface.positions.shape[0])
    return result.values, result.units


def _velocity(loaded: _Loaded) -> np.ndarray | None:
    found = loaded.fields.get("U")
    return found if found is not None and found.ndim == 2 and found.shape[1] == 3 else None


def _surface_list(surfaces: list[Any]) -> list[str]:
    if not isinstance(surfaces, list) or not surfaces:
        raise ValidationError("name at least one surface", code="results.no_surface")
    if len(surfaces) > MAX_SURFACES:
        raise ValidationError(f"at most {MAX_SURFACES} surfaces in one report")
    if not all(isinstance(s, str) and s for s in surfaces):
        raise ValidationError("each surface is a patch id, path or name, or 'slice'")
    return [str(s) for s in surfaces]


def _integral_units(units: str) -> str:
    return f"{units} m2".strip() if units else "m2"


# ------------------------------------------------------------------------------------- commands


@command("results.surfaces", params=(RUN,), mutates=False)
def results_surfaces(ctx: CommandContext, run: str | None = None) -> dict[str, Any]:  # Any: JSON
    """The surfaces a report can name, with their areas, and the fields and functions it can use.

    Answers `{run, surfaces: [{id, name, kind, points, triangles, area}], fields: [{name,
    units, components}], functions: [{name, definition}], density}`; the slice is listed when the
    run has one.
    """
    import numpy as np

    from stokes.post.integrals import triangle_vectors

    run_id, _data, header = _run_fields(ctx, run)
    out: list[dict[str, Any]] = []
    refs = [n.id for n in _patch_nodes(ctx)]
    slice_entry = header.get("slice")
    if isinstance(slice_entry, dict) and int(slice_entry.get("triangles") or 0) > 0:
        refs.append("slice")
    seen: dict[str, dict[str, Any]] = {}
    for ref in refs:
        try:
            loaded = _load(ctx, run_id, ref, None)
        except StokesError:
            continue
        area = float(np.linalg.norm(triangle_vectors(loaded.surface), axis=1).sum())
        out.append(
            {
                "id": loaded.node_id or "slice",
                "name": loaded.surface.name,
                "kind": loaded.kind,
                "points": int(loaded.surface.positions.shape[0]),
                "triangles": int(loaded.surface.triangles.shape[0]),
                "area": area,
            }
        )
        for name, values in loaded.fields.items():
            seen.setdefault(
                name,
                {
                    "name": name,
                    "units": loaded.units.get(name, ""),
                    "components": 1 if values.ndim == 1 else int(values.shape[1]),
                },
            )
    functions = [{"name": k, "definition": v} for k, v in _functions(ctx).items()]
    return {
        "run": run_id,
        "surfaces": out,
        "fields": list(seen.values()),
        "functions": functions,
        "density": _density(ctx, None),
    }


@command(
    "results.surface_integrals",
    params=(
        RUN,
        SURFACES,
        ParamDef(
            "field",
            "string",
            required=False,
            description="As for every results command; omit it for area and flow only.",
        ),
        PLANE,
        DENSITY,
    ),
    mutates=False,
)
def results_surface_integrals(
    ctx: CommandContext,
    surfaces: list[Any],
    field: str | None = None,
    run: str | None = None,
    plane: str | None = None,
    density: float | None = None,
) -> dict[str, Any]:  # Any: JSON
    """A surface-integral report over one or more surfaces (Fluent's *Reports > Surface Integrals*).

    One row per surface and a *net* row: area, area- and mass-weighted average, integral, flow
    rate, mass flow, facet and vertex min/max/average, standard deviation and the area- and
    mass-weighted uniformity index. Flow rate is positive leaving the fluid on a patch and along
    the normal on a slice. A value that cannot be computed is null (mass flow with no density, a
    uniformity index of a zero average). `units` names each column's units.
    """
    from stokes.post.integrals import QUANTITIES, combine, surface_integrals

    refs = _surface_list(surfaces)
    rho = _density(ctx, density)
    rows: list[dict[str, Any]] = []
    field_units = ""
    functions = _functions(ctx)
    for ref in refs:
        loaded = _load(ctx, run, ref, plane)
        values = None
        if field:
            values, field_units = _values(ctx, loaded, field, rho, functions)
        row = surface_integrals(loaded.surface, values, _velocity(loaded), rho)
        rows.append({"surface": loaded.surface.name, "id": loaded.node_id or "slice", **row})
    net = combine([{k: r.get(k) for k in QUANTITIES} for r in rows])
    value_units = field_units
    units = {
        "area": "m2",
        "integral": _integral_units(value_units),
        "flow_rate": "m3/s",
        "mass_flow": "kg/s",
        "std_dev": value_units,
        "uniformity_area": "",
        "uniformity_mass": "",
    }
    for key in ("area_avg", "mass_avg", "facet_min", "facet_max", "facet_avg"):
        units[key] = value_units
    for key in ("vertex_min", "vertex_max", "vertex_avg"):
        units[key] = value_units
    return {
        "run": _run_fields(ctx, run)[0],
        "field": field or "",
        "field_units": value_units,
        "density": rho,
        "quantities": list(QUANTITIES),
        "units": units,
        "rows": rows,
        "net": net,
    }


@command(
    "results.histogram",
    params=(
        RUN,
        SURFACES,
        FIELD,
        ParamDef("bins", "integer", required=False, default=20, description="1 to 200 bins."),
        ParamDef("low", "number", required=False, description="Lower end; default the minimum."),
        ParamDef("high", "number", required=False, description="Upper end; default the maximum."),
        PLANE,
        DENSITY,
    ),
    mutates=False,
)
def results_histogram(
    ctx: CommandContext,
    surfaces: list[Any],
    field: str,
    bins: int = 20,
    low: float | None = None,
    high: float | None = None,
    run: str | None = None,
    plane: str | None = None,
    density: float | None = None,
) -> dict[str, Any]:  # Any: JSON
    """An area-weighted histogram of a field over surfaces: how much surface is at each value.

    Answers `{field, units, edges, area, fraction, min, max, total}`.
    """
    from stokes.post.integrals import histogram

    rho = _density(ctx, density)
    functions = _functions(ctx)
    pairs: list[tuple[Any, Any]] = []
    units = ""
    for ref in _surface_list(surfaces):
        loaded = _load(ctx, run, ref, plane)
        values, units = _values(ctx, loaded, field, rho, functions)
        pairs.append((loaded.surface, values))
    return {"field": field, "units": units, **histogram(pairs, int(bins), low, high)}


@command(
    "results.surface_plot",
    params=(
        RUN,
        SURFACES,
        FIELD,
        ParamDef(
            "axis",
            "string",
            required=False,
            default="x",
            description="x | y | z | direction (distance along `direction` from `origin`) | "
            "radius (distance from the line through `origin` along `direction`).",
        ),
        ParamDef(
            "direction", "array", required=False, description="[x, y, z], for direction/radius."
        ),
        ParamDef(
            "origin", "array", required=False, description="[x, y, z] metres, for direction/radius."
        ),
        PLANE,
        DENSITY,
    ),
    mutates=False,
)
def results_surface_plot(
    ctx: CommandContext,
    surfaces: list[Any],
    field: str,
    axis: str = "x",
    direction: list[float] | None = None,
    origin: list[float] | None = None,
    run: str | None = None,
    plane: str | None = None,
    density: float | None = None,
) -> dict[str, Any]:  # Any: JSON
    """An XY plot on surfaces (Fluent's *XY Plot* with a plot direction): every point's value
    against its position, one series per surface, sorted by position and thinned past 4000 points.

    `radius` without an `origin` and `direction` measures from each surface's own axis: its
    area-weighted centre along its mean normal, which on a pipe's outlet is the pipe's axis - so
    the plot is the velocity profile. A surface with no mean normal (a curved wall) is refused.

    Answers `{field, units, axis, x_units, series: [{surface, x, y, points}]}`.
    """
    from stokes.post.integrals import ReportInvalid, centre_and_normal, surface_plot

    rho = _density(ctx, density)
    functions = _functions(ctx)
    series: list[dict[str, Any]] = []
    units = ""
    for ref in _surface_list(surfaces):
        loaded = _load(ctx, run, ref, plane)
        values, units = _values(ctx, loaded, field, rho, functions)
        own_origin, own_direction = origin, direction
        if axis == "radius" and (origin is None or direction is None):
            centre, normal = centre_and_normal(loaded.surface)
            if normal is None and direction is None:
                raise ReportInvalid(
                    f"{loaded.surface.name} has no single direction to measure a radius from; "
                    "give a direction"
                )
            own_origin = origin if origin is not None else centre
            own_direction = direction if direction is not None else normal
        series.append(surface_plot(loaded.surface, values, axis, own_direction, own_origin))
    return {"field": field, "units": units, "axis": axis, "x_units": "m", "series": series}


@command(
    "results.range",
    params=(
        RUN,
        SURFACES,
        FIELD,
        ParamDef(
            "share",
            "number",
            required=False,
            default=0.02,
            description="The share of the area left out at each end for low/high (0 to 0.49).",
        ),
        PLANE,
        DENSITY,
    ),
    mutates=False,
)
def results_range(
    ctx: CommandContext,
    surfaces: list[Any],
    field: str,
    share: float = 0.02,
    run: str | None = None,
    plane: str | None = None,
    density: float | None = None,
) -> dict[str, Any]:  # Any: JSON
    """A field's range on the surfaces shown, for a colour map fitted to them (Discovery's fit).

    Answers `{field, units, min, max, low, high}`: `min`/`max` are the values' ends and
    `low`/`high` the area-weighted robust ends, `share` of the area past each, so a thin band of
    no-slip zeros on a cut's edge does not squeeze the core flow into one colour.
    """
    from stokes.post.integrals import weighted_range

    rho = _density(ctx, density)
    functions = _functions(ctx)
    pairs: list[tuple[Any, Any]] = []
    units = ""
    for ref in _surface_list(surfaces):
        loaded = _load(ctx, run, ref, plane)
        values, units = _values(ctx, loaded, field, rho, functions)
        pairs.append((loaded.surface, values))
    return {"field": field, "units": units, **weighted_range(pairs, float(share))}


@command(
    "results.contour_lines",
    params=(
        RUN,
        ParamDef("surface", "string", description="One patch (id, path or name) or 'slice'."),
        FIELD,
        ParamDef(
            "levels",
            "any",
            required=False,
            default=10,
            description="How many evenly spaced levels (1 to 64), or a list of values.",
        ),
        ParamDef(
            "low", "number", required=False, description="Range low end; default the minimum."
        ),
        ParamDef(
            "high", "number", required=False, description="Range high end; default the maximum."
        ),
        PLANE,
        DENSITY,
    ),
    mutates=False,
)
def results_contour_lines(
    ctx: CommandContext,
    surface: str,
    field: str,
    levels: Any = 10,  # Any: an integer count or a list of numbers (JSON)
    low: float | None = None,
    high: float | None = None,
    run: str | None = None,
    plane: str | None = None,
    density: float | None = None,
) -> dict[str, Any]:  # Any: JSON
    """Contour LINES of a field on one surface (Fluent's contours with *Filled* off).

    `levels` as a count spreads that many levels evenly inside `[low, high]` (ends excluded, so
    they fall on a banded legend's band edges); as a list it is those values. Answers `{surface,
    field, units, levels: [{level, segments}]}` with `segments` a flat `[x0,y0,z0,x1,y1,z1,...]`
    in metres.
    """
    from stokes.post.contours import contour_segments, even_levels

    loaded = _load(ctx, run, surface, plane)
    values, units = _values(ctx, loaded, field, _density(ctx, density), None)
    if isinstance(levels, bool) or not isinstance(levels, int | float | list):
        raise ValidationError("levels is a count or a list of numbers")
    if isinstance(levels, list):
        if not all(isinstance(v, int | float) and not isinstance(v, bool) for v in levels):
            raise ValidationError("each level is a number")
        chosen = [float(v) for v in levels]
    else:
        lo = float(values.min()) if low is None else float(low)
        hi = float(values.max()) if high is None else float(high)
        chosen = even_levels(lo, hi, int(levels)) if hi > lo else []
    lines = contour_segments(loaded.surface.positions, loaded.surface.triangles, values, chosen)
    return {"surface": loaded.surface.name, "field": field, "units": units, "levels": lines}


@command(
    "results.evaluate",
    params=(
        RUN,
        ParamDef("surface", "string", description="One patch or 'slice'."),
        FIELD,
        PLANE,
        DENSITY,
    ),
    mutates=False,
)
def results_evaluate(
    ctx: CommandContext,
    surface: str,
    field: str,
    run: str | None = None,
    plane: str | None = None,
    density: float | None = None,
) -> dict[str, Any]:  # Any: JSON
    """Check a field or an expression on one surface: its units and range, without the values.

    The field calculator's *Compute*: answers `{field, units, min, max, points}`, or the
    expression's error (`units.expression`, `units.mismatch`), so a typed definition is
    checked before it is saved.
    """
    import numpy as np

    loaded = _load(ctx, run, surface, plane)
    values, units = _values(ctx, loaded, field, _density(ctx, density), None)
    finite = values[np.isfinite(values)]
    return {
        "field": field,
        "units": units,
        "min": float(finite.min()) if finite.size else None,
        "max": float(finite.max()) if finite.size else None,
        "points": int(values.shape[0]),
    }


def field_values(
    session: Any, run_id: str, target: str, text: str, plane: dict[str, Any] | None
) -> tuple[np.ndarray, str]:  # Any: Session
    """A field or expression's values on a patch (by node id) or the slice, for the field route.

    The route that colours surfaces calls this for a name the run's cache does not hold, so a
    field function colours the model like any field. Raises the same typed errors the commands do.
    """
    from stokes.api.registry import CommandContext as Ctx

    ctx = Ctx(session=session)
    plane_text = None
    if plane is not None:
        origin, normal = plane["origin"], plane["normal"]
        plane_text = ",".join(str(float(v)) for v in [*origin, *normal])
    loaded = _load(ctx, run_id, target, plane_text)
    return _values(ctx, loaded, text, _density(ctx, None), None)


# ------------------------------------------------------------------------------ field functions


def _function_nodes(ctx: CommandContext) -> list[Node]:
    from stokes.model.field_functions import FIELD_FUNCTION_TYPE, FIELD_FUNCTIONS_PATH

    group = ctx.session.require_project().find(FIELD_FUNCTIONS_PATH)
    return [] if group is None else [n for n in group.children if n.type == FIELD_FUNCTION_TYPE]


@command(
    "results.function_save",
    params=(
        ParamDef(
            "name", "string", description="An identifier, e.g. p_total; the same name replaces."
        ),
        ParamDef("definition", "string", description="The expression, e.g. 'p + 0.5 * U_mag^2'."),
        ParamDef("node_id", "string", required=False, description="Reuse an id (journals)."),
    ),
)
def field_function_save(
    ctx: CommandContext, name: str, definition: str, node_id: str | None = None
) -> dict[str, Any]:  # Any: JSON
    """Save a custom field function, as one undo step: adds it, or replaces the one of that name.

    The definition is parsed (not evaluated: no run is needed), so a typo is refused now with
    `expression`; its units are found when it is used. Reply `{id, name, definition, replaced}`.
    Codes: `field_function.name_invalid`, `units.expression`.
    """
    from stokes.model.field_functions import (
        FIELD_FUNCTION_TYPE,
        FIELD_FUNCTIONS_GROUP,
        FIELD_FUNCTIONS_PATH,
        MAX_DEFINITION,
        clean_name,
    )
    from stokes.model.ids import is_id, new_id, stable_id
    from stokes.units.expressions import references, validate

    project = ctx.session.require_project()
    text = clean_name(name)
    body = definition.strip()
    if not body or len(body) > MAX_DEFINITION:
        raise ExpressionError("a definition is 1 to 2000 characters")
    validate(body)
    if text in references(body):
        raise ExpressionError(f"{text} cannot refer to itself")
    if node_id is not None and not is_id(node_id):
        raise ValidationError(f"{node_id!r} is not a node id", code="validation")
    existing = next((n for n in _function_nodes(ctx) if n.params.get("name") == text), None)
    steps: list[Command] = []
    if existing is not None:
        steps.append(SetParam(existing.id, "definition", body))
        chosen = existing.id
    else:
        chosen = node_id or new_id()
        if project.find(chosen) is not None:
            raise ValidationError(f"node id {chosen} is taken", code="validation")
        if project.find(FIELD_FUNCTIONS_PATH) is None:
            steps.append(
                AddNode(
                    "/",
                    "group",
                    FIELD_FUNCTIONS_GROUP,
                    node_id=stable_id("g", FIELD_FUNCTIONS_PATH),
                )
            )
        steps.append(
            AddNode(
                FIELD_FUNCTIONS_PATH,
                FIELD_FUNCTION_TYPE,
                f"f_{chosen[2:]}",
                {"name": text, "definition": body},
                node_id=chosen,
            )
        )
    made_group = any(isinstance(s, AddNode) and s.type == "group" for s in steps)
    ctx.session.undo.push(Composite(label=f"Save field function {text}", commands=steps), project)
    ctx.record(node_id=chosen)
    if made_group:
        ctx.session.emit(
            "node.added", node_id=stable_id("g", FIELD_FUNCTIONS_PATH), path=FIELD_FUNCTIONS_PATH
        )
    if existing is None:
        ctx.session.emit("node.added", node_id=chosen, path=project.get(chosen).path)
    else:
        ctx.session.emit("node.changed", node_id=chosen, path=existing.path, param="definition")
    return {"id": chosen, "name": text, "definition": body, "replaced": existing is not None}


@command("results.function_list", params=(), mutates=False)
def field_function_list(ctx: CommandContext) -> dict[str, Any]:  # Any: JSON
    """The saved field functions: `{functions: [{id, name, definition}]}`."""
    return {
        "functions": [
            {
                "id": n.id,
                "name": str(n.params.get("name") or ""),
                "definition": str(n.params.get("definition") or ""),
            }
            for n in _function_nodes(ctx)
        ]
    }


@command(
    "results.function_remove",
    params=(ParamDef("function", "string", description="A field function's id or name."),),
)
def field_function_remove(ctx: CommandContext, function: str) -> dict[str, Any]:  # Any: JSON
    """Remove a saved field function, as one undo step. Code: `field_function.unknown`."""
    project = ctx.session.require_project()
    target = next(
        (n for n in _function_nodes(ctx) if function in (n.id, n.params.get("name"))), None
    )
    if target is None:
        raise ValidationError(
            f"there is no field function {function!r}", code="field_function.unknown"
        )
    name = str(target.params.get("name") or "")
    cmd = RemoveNode(target.id)
    result = ctx.session.undo.push(cmd, project)
    ctx.session.emit("node.removed", node_id=cmd.node_id, path=result["path"], parent=cmd.parent_id)
    return {"id": target.id, "name": name}

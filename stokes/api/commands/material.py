"""material.* commands (ADR-0018 slices 1 and 2): materials as nodes, the method catalogue,
evaluation, checks, and materials fitted from the library providers.

``material.create`` adds ``/physics/materials/<name>`` with one ``property`` child per property
kind of its material kind, from a template made from the built-in catalogue (decisions log
F6 (a)); ``material.add_from_library`` does the same from a built-in record, or fits each
property from CoolProp, CoolProp INCOMP, Cantera or REFPROP over the material's range at its
reference pressure, with the provenance in each property's ``meta``; ``material.set_method``
changes one property's method and parameters; ``material.set_state`` changes the state and
refits; ``material.refit`` fits again. Each is one undo step, validated against the catalogue
before it is kept. ``node.*`` and ``parameter.set`` work on these nodes as on any other (a
property's ``value`` takes expressions, so a study can vary
``physics.materials.<name>.<property>.value``); ``parameter.set`` on a fitted property marks
it detached from the library (F6 (c), through ``stokes.physics.materials.service``).
``material.evaluate``, ``check``, ``list``, ``methods``, ``providers``, ``library_search``,
``library_info`` and ``cancel_fit`` change nothing.

Fits run in the materials worker process; a fit estimated above 0.2 s on the server, or asked
for with ``wait: false``, runs in the background: the properties are ``stale`` meanwhile and
events follow.

Events::

    node.added {node_id, path}                       the material, then each property
    node.changed {node_id, path, param}              each parameter a command sets; param
                                                     "status" when a property goes stale or back
    material.fitted {material, property, method, max_rel_err, tolerance, ok}
    material.fit_progress {material, done, total}    background fits, at most 10 Hz
    material.fit_failed {material, property, code, message}
    material.providers_changed {providers}           a REFPROP path set, or detection finished

Errors: ``materials.invalid_method``, ``materials.needs_molar_mass``, ``materials.fluid_unknown``,
``materials.provider_missing``, ``materials.refprop_missing``, ``materials.property_unavailable``,
``materials.range``, ``materials.fit_tolerance``, ``materials.busy``, ``materials.cancelled``,
``materials.evaluation``, and ``validation``, ``units.mismatch``, ``model.unknown_node`` as
everywhere. Contract for clients: ``docs/engineering/materials-schema.md``.

Size: one module per command group, as the registry is organised; the library planning, fitting
and staleness rules it calls live in ``stokes.physics.materials.library`` and ``service``.

Import rule: the numpy-free ``stokes.physics.materials.model`` at module level; the catalogue,
evaluation, checks, fits and providers are imported inside the handlers.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from stokes.api.registry import CommandContext, ParamDef, command
from stokes.errors import StokesError, UnknownNode, ValidationError
from stokes.log import fields, get_logger
from stokes.model import QuantityValue
from stokes.physics.materials import adding
from stokes.physics.materials.model import (
    BASES,
    KIND_PROPERTIES,
    MATERIAL_KINDS,
    MATERIALS_PATH,
    PHASES,
    PROPERTY_DIMENSIONS,
    PROPERTY_KINDS,
    PROVIDERS,
)
from stokes.units import DIMENSIONS, format_quantity

if TYPE_CHECKING:
    from stokes.api.session import Session
    from stokes.model import Node, Project
    from stokes.physics.materials.catalogue import MethodSpec
    from stokes.physics.materials.library import FitOutcome, LibraryState
    from stokes.physics.materials.methods import Array
    from stokes.physics.materials.service import MaterialsService

MATERIAL = ParamDef(
    "material", "string", description="Material: id, path (/physics/materials/water) or name."
)
NODE_ID = ParamDef("node_id", "string", required=False, description="Reuse an id (journals).")
QUANTITY_HELP = "A number in SI units, quantity text such as '300 [K]', or an expression."
WAIT = ParamDef(
    "wait",
    "boolean",
    required=False,
    default=True,
    description="Wait for the fit (default). False, or a fit estimated above 0.2 s on the server, "
    "runs it in the background: the properties are stale until material.fitted.",
)
SOURCES = ("method", "library", "both")
OVERRIDES = ("constant", "piecewise_linear")
LIBRARY = adding.LIBRARY
log = get_logger(__name__)
STATE_DIMENSIONS = {
    "T_ref": "temperature",
    "p_ref": "pressure",
    "T_min": "temperature",
    "T_max": "temperature",
}
# ---------------------------------------------------------------- helpers


def _summary(project: Project, node: Node) -> dict[str, Any]:  # Any: JSON
    """Node summary plus display-unit strings for its quantities (as node.get returns it)."""
    data = node.summary()
    data["structural"] = project.is_structural(node)
    displays: dict[str, str] = {}
    for pspec in project.spec_of(node).params:
        if pspec.kind == "quantity" and isinstance(node.params.get(pspec.name), QuantityValue):
            dim = project.param_dimension(node, pspec)
            unit = dim.unit_for(project.display_units) or None
            displays[pspec.name] = format_quantity(project.value_of(node.id, pspec.name), unit)
    if displays:
        data["displays"] = displays
    return data


def _material_summary(project: Project, material: Node) -> dict[str, Any]:  # Any: JSON
    return {
        **_summary(project, material),
        "properties": {c.name: c.id for c in material.children if c.type == "property"},
    }


def _material(project: Project, ref: str) -> Node:
    """A material by id, path or name."""
    node = project.find(ref)
    if node is None:
        group = project.find(MATERIALS_PATH)
        node = group.child(ref) if group is not None else None
    if node is None or node.type != "material":
        raise UnknownNode(f"no material {ref!r}", details={"material": ref})
    return node


def _si(project: Project, raw: Any, dimension: str, what: str) -> float:  # Any: number or text
    """A quantity given as an SI number, quantity text or an expression, in SI."""
    from stokes.units import dimension as named
    from stokes.units import is_quantity_text, parse_quantity, to_si

    dim = named(dimension)
    if isinstance(raw, bool):
        raise ValidationError(f"{what} expects a quantity, got a boolean")
    if isinstance(raw, int | float):
        return float(raw)
    if isinstance(raw, str) and raw.strip():
        text = raw.strip()
        q = parse_quantity(text, dim) if is_quantity_text(text) else project.evaluate(text, dim)
        return to_si(q, dim)
    raise ValidationError(f"{what} expects a quantity; got {raw!r}", details={"parameter": what})


def _unique(project: Project, base: str) -> str:
    group = project.find(MATERIALS_PATH)
    taken = {c.name for c in group.children} if group is not None else set()
    name, number = base, 2
    while name in taken:
        name, number = f"{base}_{number}", number + 1
    return name


def _push(ctx: CommandContext, cmd: Any) -> list[dict[str, Any]]:  # Any: a Checked composite
    from stokes.model import AddNode

    project = ctx.session.require_project()
    results = ctx.session.undo.push(cmd, project)
    for part in cmd.commands:
        if isinstance(part, AddNode):
            node = project.get(str(part.node_id))
            ctx.session.emit("node.added", node_id=node.id, path=node.path)
    return list(results)


def _emit_changes(ctx: CommandContext, cmd: Any) -> None:  # Any: a composite
    from stokes.model import SetParam

    for part in cmd.commands:
        if isinstance(part, SetParam) and part.node_id is not None:
            ctx.session.emit(
                "node.changed", node_id=part.node_id, path=part.node_path, param=part.param
            )


def _emit_fitted(session: Session, material: str, outcome: FitOutcome) -> None:
    for prop, result in outcome.results.items():
        session.emit(
            "material.fitted",
            material=material,
            property=prop,
            method=result.method,
            max_rel_err=result.max_rel_err,
            tolerance=result.tolerance,
            ok=result.ok,
        )


def _emit_group(session: Session, group: Node | None) -> None:
    if group is not None:
        session.emit("node.added", node_id=group.id, path=group.path)


def _service(ctx: CommandContext) -> MaterialsService:
    from stokes.physics.materials.service import MaterialsService

    return MaterialsService.of(ctx.session)


def _param_dimension(prop: str, name: str) -> str | None:
    from stokes.model.schema import DYNAMIC, registry

    spec = registry.get("property").param(name)
    if spec.dimension == DYNAMIC or name == "table_values":
        return PROPERTY_DIMENSIONS[prop]
    return spec.dimension


def _method_json(spec: MethodSpec, material_kind: str) -> dict[str, Any]:  # Any: JSON
    from stokes.model.schema import registry

    node_spec = registry.get("property")
    data = spec.to_json(material_kind)
    data["params"] = [
        {
            **entry,
            "kind": node_spec.param(entry["name"]).kind,
            "dimension": _param_dimension(spec.property_kind, entry["name"]),
            "doc": node_spec.param(entry["name"]).doc,
        }
        for entry in data["params"]
    ]
    data["dimension"] = PROPERTY_DIMENSIONS[spec.property_kind]
    return data


# ---------------------------------------------------------------- the catalogue


@command(
    "material.methods",
    params=(
        ParamDef(
            "kind",
            "string",
            required=False,
            default="fluid",
            description=f"Material kind: {', '.join(MATERIAL_KINDS)}.",
        ),
        ParamDef(
            "property",
            "string",
            required=False,
            description=f"One property: {', '.join(PROPERTY_KINDS)}; default all of the kind's.",
        ),
    ),
    mutates=False,
    requires_project=False,
)
def material_methods(
    ctx: CommandContext,
    kind: str = "fluid",
    property: str | None = None,  # noqa: A002 - the contract's parameter name
) -> list[dict[str, Any]]:  # Any: JSON
    """The method catalogue for a material kind: each method's parameters with their dimensions,
    what it needs, its Foundation 14 selection and Fluent method, and whether it is available."""
    from stokes.physics.materials.catalogue import catalogue

    return [_method_json(spec, kind) for spec in catalogue(kind, property)]


# ---------------------------------------------------------------- providers


def _report_later(session: Session) -> None:
    """Start the worker and fetch the provider report on a thread, announce it, then build the
    search indexes the first ``material.library_search`` would otherwise wait for."""
    from stokes.physics.materials.providers import available_providers, remote

    def fetch() -> None:
        try:
            report = remote.report(wait=True)
        except StokesError as exc:
            session.emit("material.providers_changed", providers=[], error=exc.to_dict())
            return
        session.emit("material.providers_changed", providers=adding.provider_entries(report))
        for name in available_providers():
            if name == "builtin":
                continue
            try:
                remote.index(name)
            except StokesError as exc:
                log.warning("materials index not built", extra=fields(provider=name, code=exc.code))

    threading.Thread(target=fetch, name="stokes-materials-providers", daemon=True).start()


@command(
    "material.providers",
    params=(
        ParamDef(
            "refprop_path",
            "string",
            required=False,
            description="Set the REFPROP folder (Stokes' settings path; empty text clears it). "
            "REFPROP loads once per worker process, so the worker restarts.",
        ),
        ParamDef(
            "wait",
            "boolean",
            required=False,
            default=True,
            description="Wait for the worker's report (default). False answers at once with "
            "what is known and starts the worker; material.providers_changed follows.",
        ),
    ),
    mutates=False,
    requires_project=False,
)
def material_providers(
    ctx: CommandContext, refprop_path: str | None = None, wait: bool = True
) -> list[dict[str, Any]]:  # Any: JSON
    """Every material provider: whether it can answer here, its version, how many fluids it
    offers, and for REFPROP the folder it was found in or why it was not."""
    from stokes.physics.materials.providers import remote

    changed = remote.set_refprop_path(refprop_path) if refprop_path is not None else False
    library = any(remote.installed(name) for name in LIBRARY)
    report = remote.report(wait=wait) if library else None
    if report is None and library and not wait:
        _report_later(ctx.session)
    entries = adding.provider_entries(report)
    if changed:
        ctx.session.emit("material.providers_changed", providers=entries)
    return entries


# ---------------------------------------------------------------- materials, undoable


@command(
    "material.create",
    params=(
        ParamDef("name", "string", description="Material name (letters, digits, underscores)."),
        ParamDef(
            "kind",
            "string",
            required=False,
            default="fluid",
            description=f"One of {', '.join(MATERIAL_KINDS)}.",
        ),
        ParamDef(
            "template",
            "string",
            required=False,
            description="liquid or gas for a fluid (default liquid), solid for a solid; each "
            "made from a built-in record (water_liquid, air, aluminium) at its T_ref.",
        ),
        ParamDef("description", "string", required=False, default=""),
        NODE_ID,
    ),
)
def material_create(
    ctx: CommandContext,
    name: str,
    kind: str = "fluid",
    template: str | None = None,
    description: str = "",
    node_id: str | None = None,
) -> dict[str, Any]:  # Any: JSON
    """Create a material under /physics/materials with its property children, from a template
    made from the built-in catalogue (constant values, an ideal-gas density for a gas), as one
    undo step."""
    from stokes.physics.materials import authoring

    if kind not in MATERIAL_KINDS:
        raise ValidationError(f"kind must be one of {', '.join(MATERIAL_KINDS)}; got {kind!r}")
    chosen = template or ("solid" if kind == "solid" else "liquid")
    kinds = {t: ("solid" if t == "solid" else "fluid") for t in authoring.TEMPLATE_RECORDS}
    if kinds.get(chosen) != kind:
        options = [t for t, k in kinds.items() if k == kind]
        raise ValidationError(
            f"template for a {kind} must be one of {', '.join(options)}; got {chosen!r}"
        )
    project = ctx.session.require_project()
    _service(ctx)
    _emit_group(ctx.session, authoring.ensure_materials_group(project))
    spec = authoring.template(chosen)
    params = {
        "kind": kind,
        "source": "user",
        "provider": "none",
        "description": description,
        **spec["state"],
    }
    cmd = authoring.create_command(
        name,
        kind,
        params,
        spec["properties"],
        node_id=node_id,
        material_meta={"template": {"name": chosen, "record": spec["record"]}},
    )
    results = _push(ctx, cmd)
    ctx.record(node_id=results[0]["id"])
    return _material_summary(project, project.get(results[0]["id"]))


def _state_inputs(
    project: Project, given: dict[str, Any]
) -> dict[str, float | None]:  # Any: quantities in any accepted form
    return {
        key: _si(project, value, STATE_DIMENSIONS[key], key) if value is not None else None
        for key, value in given.items()
    }


def _add_builtin(
    ctx: CommandContext,
    fluid: str,
    name: str | None,
    given: dict[str, Any],  # Any: quantities in any accepted form
    methods: dict[str, Any],  # Any: JSON, checked here
    tolerance: float | None,
    max_degree: int | None,
    node_id: str | None,
) -> dict[str, Any]:  # Any: JSON
    """A material from the built-in catalogue, with the record's methods and provenance."""
    from stokes.physics.materials.authoring import create_command, ensure_materials_group
    from stokes.physics.materials.evaluate import State, molar_mass_of
    from stokes.physics.materials.providers import builtin

    found = builtin.record(fluid)
    project = ctx.session.require_project()
    for prop, form in methods.items():
        if prop not in found["properties"] or form not in OVERRIDES:
            raise ValidationError(
                f"methods maps a property of {found['id']} to one of {', '.join(OVERRIDES)}; "
                f"got {prop!r}: {form!r} (fit other forms from a library provider)",
                details={"property": prop, "method": form},
            )
    si = {
        k: v if v is not None else float(found["state"][k])
        for k, v in _state_inputs(project, given).items()
    }
    state = State(
        kind=found["kind"],
        T_ref=si["T_ref"],
        p_ref=si["p_ref"],
        T_min=si["T_min"],
        T_max=si["T_max"],
        molar_mass=molar_mass_of(builtin.definitions_of(found)),
    )
    properties = adding.record_properties(found, dict(methods), state)
    params: dict[str, Any] = {  # Any: JSON parameter values
        "kind": found["kind"],
        "source": "builtin",
        "provider": "builtin",
        "fluid": found["id"],
        "phase": found.get("phase", "auto"),
        "tolerance": float(
            tolerance if tolerance is not None else found["state"].get("tolerance", 0.01)
        ),
        "max_degree": int(
            max_degree if max_degree is not None else found["state"].get("max_degree", 4)
        ),
        "formula": found.get("formula", ""),
        "cas": found.get("cas", ""),
        "description": found.get("description", ""),
        **{k: (v if v is not None else si[k]) for k, v in given.items()},
    }
    final = name or _unique(project, found["id"])
    _emit_group(ctx.session, ensure_materials_group(project))
    cmd = create_command(
        final,
        found["kind"],
        params,
        properties,
        metas=adding.record_metas(found, properties, state),
        node_id=node_id,
        label=f"add material {final} from the built-in catalogue",
    )
    results = _push(ctx, cmd)
    ctx.record(name=final, node_id=results[0]["id"])
    return {**_material_summary(project, project.get(results[0]["id"])), "record": found["id"]}


def _background(service: MaterialsService, provider: str, fluid: str, wait: bool) -> bool:
    """Whether a fit leaves the command: asked to, or too slow for the server's event loop."""
    if not wait:
        return True
    from stokes.physics.materials.fit import VERIFY_POINTS
    from stokes.physics.materials.providers.remote import cost_per_state
    from stokes.physics.materials.service import INLINE_BUDGET

    slow = cost_per_state(provider, fluid) * VERIFY_POINTS > INLINE_BUDGET
    return slow and service.on_event_loop()


def _add_library(
    ctx: CommandContext,
    provider_name: str,
    fluid: str,
    composition: str,
    basis: str | None,
    name: str | None,
    kind: str | None,
    phase: str,
    given: dict[str, Any],  # Any: quantities in any accepted form
    tolerance: float,
    max_degree: int,
    methods: dict[str, str],
    wait: bool,
    node_id: str | None,
) -> dict[str, Any]:  # Any: JSON
    """A material fitted from a library provider (inline, or in the background)."""
    from stokes.physics.materials import library
    from stokes.physics.materials.authoring import create_command, ensure_materials_group
    from stokes.physics.materials.fit import FITTED_PROPERTIES
    from stokes.physics.materials.service import FitJob

    if kind not in (None, "fluid"):
        raise ValidationError(f"library providers give fluids; got kind {kind!r}")
    project = ctx.session.require_project()
    service = _service(ctx)
    provider, version = adding.library_provider(provider_name)
    si = _state_inputs(project, given)
    info = provider.info(
        fluid,
        composition,
        basis or "",
        T_ref=si["T_ref"] if si["T_ref"] is not None else 300.0,
        p_ref=si["p_ref"] if si["p_ref"] is not None else 101325.0,
        phase=phase,
    )
    record = info.record
    chosen_basis = basis or record.basis or "mole"
    state = adding.library_state(
        provider_name,
        fluid,
        composition,
        chosen_basis,
        phase,
        info,
        si,
        tolerance,
        max_degree,
    )
    final = name or _unique(
        project,
        adding.node_name(record.name if provider_name != "cantera" else fluid.rsplit(".", 1)[0]),
    )
    params: dict[str, Any] = {  # Any: JSON parameter values
        "kind": "fluid",
        "source": "library",
        "provider": provider_name,
        "fluid": fluid,
        "composition": composition,
        "basis": chosen_basis,
        "phase": phase,
        "tolerance": tolerance,
        "max_degree": max_degree,
        "formula": record.formula,
        "cas": record.cas,
        "description": f"{record.name} from {provider_name} {version}".strip(),
        "T_min": state.T_min,
        "T_max": state.T_max,
        "T_ref": given["T_ref"] if given["T_ref"] is not None else state.T_ref,
        "p_ref": given["p_ref"] if given["p_ref"] is not None else state.p_ref,
    }
    background = _background(service, provider_name, fluid, wait)
    used_phase = library.fit_phase(phase, info)
    stand_ins = adding.stand_ins(info, used_phase)
    if background:
        outcome = library.FitOutcome(
            state=state,
            phase=used_phase,
            results={},
            missing={
                k: f"{provider_name} has no {k.replace('_', ' ')} model for {fluid}"
                for k in info.detail.get("missing") or ()
                if k in FITTED_PROPERTIES
            },
            molar_mass=float(record.molar_mass or 0.0),
            formation_enthalpy=float(
                (info.detail.get("nasa7") or {}).get("formation_enthalpy", 0.0)
            ),
            provider_version=version,
            references=dict(info.references),
        )
    else:
        outcome = library.run_fit(provider, state, info, methods=methods, provider_version=version)
    properties = library.entries(outcome, "fluid", stand_ins)
    metas = library.metas(outcome, properties)
    _emit_group(ctx.session, ensure_materials_group(project))
    cmd = create_command(
        final,
        "fluid",
        params,
        properties,
        metas=metas,
        node_id=node_id,
        label=f"add material {final} from {provider_name}",
    )
    with service.quiet():
        results = _push(ctx, cmd)
    material_id = results[0]["id"]
    ctx.record(
        name=final,
        node_id=material_id,
        wait=True,
        T_min=state.T_min,
        T_max=state.T_max,
        basis=chosen_basis,
    )
    material = project.get(material_id)
    pending: list[str] = []
    if background:
        pending = [p for p in FITTED_PROPERTIES if p not in outcome.missing]
        service.start(
            FitJob(
                material=material_id,
                name=final,
                provider=provider,
                state=state,
                info=info,
                properties=pending,
                methods=methods,
                required=list(methods),
                provider_version=version,
            )
        )
    else:
        _emit_fitted(ctx.session, material_id, outcome)
    return {
        **_material_summary(project, material),
        "record": fluid,
        "fit": {prop: result.summary() for prop, result in outcome.results.items()},
        "missing": sorted(outcome.missing),
        "background": background,
        "pending": pending,
    }


@command(
    "material.add_from_library",
    params=(
        ParamDef(
            "fluid",
            "string",
            description="The provider's id: water_liquid (builtin), Water, R410A.mix or "
            "Nitrogen&Oxygen (coolprop), MEG (coolprop_incomp), air.yaml (cantera), WATER "
            "(refprop).",
        ),
        ParamDef(
            "provider",
            "string",
            required=False,
            default="builtin",
            description="builtin, coolprop, coolprop_incomp, cantera or refprop.",
        ),
        ParamDef(
            "composition",
            "string",
            required=False,
            default="",
            description="Mixture or solution fractions: 0.79,0.21 (one per & component), 0.5 "
            "(an INCOMP solution), O2:0.21,N2:0.78,AR:0.01 (Cantera).",
        ),
        ParamDef(
            "basis",
            "string",
            required=False,
            description="mole, mass or volume: HEOS and REFPROP mole; INCOMP mass or volume; "
            "Cantera mole or mass. Default: the provider's.",
        ),
        ParamDef("name", "string", required=False, description="Default: from the record."),
        ParamDef("kind", "string", required=False, description="Default: the record's."),
        ParamDef(
            "phase",
            "string",
            required=False,
            default="auto",
            description="auto (the stable phase at T_ref and p_ref; a range across saturation "
            "is refused), liquid, gas or supercritical (forced; metastable values allowed).",
        ),
        ParamDef(
            "T_ref",
            "any",
            required=False,
            description="Default: the record's or 300 K. " + QUANTITY_HELP,
        ),
        ParamDef(
            "p_ref",
            "any",
            required=False,
            description="Default: the record's or 1 atm. " + QUANTITY_HELP,
        ),
        ParamDef(
            "T_min",
            "any",
            required=False,
            description="Default: the contract's range. " + QUANTITY_HELP,
        ),
        ParamDef(
            "T_max",
            "any",
            required=False,
            description="Default: the contract's range. " + QUANTITY_HELP,
        ),
        ParamDef(
            "tolerance",
            "number",
            required=False,
            description="Largest relative deviation a fit may have (default 0.01).",
        ),
        ParamDef(
            "max_degree",
            "integer",
            required=False,
            description="Highest polynomial degree a fit may use (default 4, at most 7).",
        ),
        ParamDef(
            "methods",
            "object",
            required=False,
            description='Forced forms by property, e.g. {"density": "constant"}: constant takes '
            "the value at T_ref, piecewise_linear the grid; library fits also take "
            "polynomial, log_polynomial, rational_polynomial, sutherland, janaf, ideal_gas.",
        ),
        WAIT,
        NODE_ID,
    ),
)
def material_add_from_library(
    ctx: CommandContext,
    fluid: str,
    provider: str = "builtin",
    composition: str = "",
    basis: str | None = None,
    name: str | None = None,
    kind: str | None = None,
    phase: str = "auto",
    T_ref: Any = None,  # noqa: N803 - the contract's parameter name; Any: a quantity
    p_ref: Any = None,  # Any: a quantity in any accepted form
    T_min: Any = None,  # noqa: N803 - Any: a quantity in any accepted form
    T_max: Any = None,  # noqa: N803 - Any: a quantity in any accepted form
    tolerance: float | None = None,
    max_degree: int | None = None,
    methods: dict[str, Any] | None = None,  # Any: JSON, checked below
    wait: bool = True,
    node_id: str | None = None,
) -> dict[str, Any]:  # Any: JSON
    """Add a material from a library: a built-in record with its methods and provenance, or a
    fluid fitted from CoolProp, CoolProp INCOMP, Cantera or REFPROP over its range at p_ref, as
    one undo step."""
    adding.choice(provider, ("builtin", *LIBRARY), "provider")
    adding.choice(phase, PHASES, "phase")
    if basis is not None:
        adding.choice(basis, BASES, "basis")
    adding.check_fit_settings(tolerance, max_degree)
    given = {"T_ref": T_ref, "p_ref": p_ref, "T_min": T_min, "T_max": T_max}
    _service(ctx)
    if provider == "builtin":
        if composition or basis:
            raise ValidationError("a built-in record takes no composition or basis")
        return _add_builtin(
            ctx, fluid, name, given, dict(methods or {}), tolerance, max_degree, node_id
        )
    return _add_library(
        ctx,
        provider,
        fluid,
        composition,
        basis,
        name,
        kind,
        phase,
        given,
        0.01 if tolerance is None else float(tolerance),
        4 if max_degree is None else int(max_degree),
        adding.check_methods(dict(methods or {})),
        wait,
        node_id,
    )


@command(
    "material.set_method",
    params=(
        MATERIAL,
        ParamDef("property", "string", description=f"One of {', '.join(PROPERTY_KINDS)}."),
        ParamDef("method", "string", description="A method id of material.methods."),
        ParamDef(
            "params",
            "object",
            required=False,
            description="The method's parameters by name: quantity text, SI numbers, "
            "expressions, or SI arrays for coefficients and tables.",
        ),
        ParamDef("valid_min", "any", required=False, description="Valid from. " + QUANTITY_HELP),
        ParamDef("valid_max", "any", required=False, description="Valid to. " + QUANTITY_HELP),
    ),
)
def material_set_method(
    ctx: CommandContext,
    material: str,
    property: str,  # noqa: A002 - the contract's parameter name
    method: str,
    params: dict[str, Any] | None = None,  # Any: JSON values, checked against the catalogue
    valid_min: Any = None,  # Any: a quantity in any accepted form
    valid_max: Any = None,  # Any: a quantity in any accepted form
) -> dict[str, Any]:  # Any: JSON
    """Set one property's method and parameters as one undo step; parameters the method does not
    use are refused, and a library property is marked detached."""
    from stokes.physics.materials import catalogue
    from stokes.physics.materials.authoring import set_method_command
    from stokes.physics.materials.service import statuses

    project = ctx.session.require_project()
    service = _service(ctx)
    node = _material(project, material)
    kind = str(node.params.get("kind"))
    if property not in KIND_PROPERTIES.get(kind, ()):
        raise ValidationError(
            f"a {kind} material has no {property!r} property; it has "
            f"{', '.join(KIND_PROPERTIES.get(kind, ()))}",
            details={"property": property},
        )
    prop = node.child(property)
    if prop is None or prop.type != "property":
        raise UnknownNode(
            f"{node.path} has no {property} property; material.check reports why",
            details={"material": node.path, "property": property},
        )
    spec = catalogue.method(property, method)
    catalogue.check_choice(spec, kind)
    given = dict(params or {})
    unused = sorted(set(given) - set(spec.param_names))
    if unused:
        raise ValidationError(
            f"{property} method {method} does not use {', '.join(unused)}; it uses "
            f"{', '.join(spec.param_names) or 'no parameters'}",
            details={"unused": unused, "method": method},
        )
    for key, value in (("valid_min", valid_min), ("valid_max", valid_max)):
        if value is not None:
            given[key] = value
    provenance = any(key in prop.meta for key in ("library", "fit", "signature"))
    detach = provenance and not prop.meta.get("detached")
    before = statuses(node)
    with service.quiet():
        ctx.session.undo.push(set_method_command(node, prop, method, given, detach=detach), project)
        for key in ("method", *given):
            ctx.session.emit("node.changed", node_id=prop.id, path=prop.path, param=key)
    service.resync(node, before)
    return _summary(project, prop)


def _library_material(project: Project, ref: str, command_name: str) -> Node:
    from stokes.physics.materials.library import is_library

    node = _material(project, ref)
    if not is_library(node):
        raise ValidationError(
            f"{command_name} fits library materials; {node.name} is a "
            f"{node.params.get('source')} material (provider {node.params.get('provider')}). "
            "Set its provider and fluid with material.set_state, or add it with "
            "material.add_from_library",
            details={"material": node.path},
        )
    return node


def _refit(
    ctx: CommandContext,
    node: Node,
    state: LibraryState,
    properties: list[str],
    methods: dict[str, str],
    required: list[str],
    wait: bool,
    prefix: list[Any] | None = None,  # Any: model commands to run first in the same undo step
    label: str = "",
) -> dict[str, Any]:  # Any: JSON
    """Fit ``properties`` at ``state`` and write them (with ``prefix``) as one undo step, or
    start a background fit after writing ``prefix`` alone."""
    from stokes.physics.materials import library
    from stokes.physics.materials.authoring import Checked, check_all
    from stokes.physics.materials.service import FitJob, statuses

    project = ctx.session.require_project()
    service = _service(ctx)
    methods = {**library.remembered_methods(node, properties), **methods}
    provider, version = adding.library_provider(state.provider)
    info = provider.info(
        state.fluid,
        state.composition,
        state.basis,
        T_ref=state.T_ref,
        p_ref=state.p_ref,
        phase=state.phase,
    )
    background = _background(service, state.provider, state.fluid, wait)
    outcome: FitOutcome | None = None
    if not background and properties:
        outcome = library.run_fit(
            provider,
            state,
            info,
            properties,
            methods,
            required=required,
            provider_version=version,
        )
    parts: list[Any] = list(prefix or [])  # Any: model commands
    if outcome is not None:
        parts.extend(library.refit_command(node, outcome).commands)
    before = statuses(node)
    if parts:
        cmd = Checked(label or f"refit {node.name}", parts, check=check_all(node.id))
        with service.quiet():
            ctx.session.undo.push(cmd, project)
            _emit_changes(ctx, cmd)
    if outcome is not None:
        _emit_fitted(ctx.session, node.id, outcome)
    pending: list[str] = []
    if background and properties:
        pending = list(properties)
        service.start(
            FitJob(
                material=node.id,
                name=node.name,
                provider=provider,
                state=state,
                info=info,
                properties=pending,
                methods=methods,
                required=required,
                provider_version=version,
            )
        )
    service.resync(node, before)
    return {
        "fit": {p: r.summary() for p, r in (outcome.results if outcome else {}).items()},
        "missing": sorted(outcome.missing) if outcome else [],
        "background": bool(pending),
        "pending": pending,
    }


@command(
    "material.refit",
    params=(
        MATERIAL,
        ParamDef(
            "properties",
            "array",
            required=False,
            description="Properties to fit again (a detached one re-attaches); default every "
            "library property not edited by hand.",
        ),
        ParamDef(
            "methods",
            "object",
            required=False,
            description='Forced forms by property, e.g. {"viscosity": "piecewise_linear"}.',
        ),
        WAIT,
    ),
)
def material_refit(
    ctx: CommandContext,
    material: str,
    properties: list[Any] | None = None,  # Any: strings, checked below
    methods: dict[str, Any] | None = None,  # Any: JSON, checked below
    wait: bool = True,
) -> dict[str, Any]:  # Any: JSON
    """Fit a library material's properties again from its provider at its present state, as
    one undo step; nothing changes when a property cannot be fitted within the tolerance."""
    from stokes.physics.materials import library

    project = ctx.session.require_project()
    service = _service(ctx)
    node = _library_material(project, material, "material.refit")
    service.require_idle(node.id, node.name)
    forced = adding.check_methods(dict(methods or {}))
    requested = [str(p) for p in properties] if properties else None
    chosen = library.fittable(node, list(dict.fromkeys([*(requested or []), *forced])) or None)
    warnings: list[str] = []
    if not chosen:
        warnings.append(
            "every library property is detached; name them in properties to fit them again"
        )
    result = _refit(
        ctx,
        node,
        library.state_of(node),
        chosen,
        forced,
        list(dict.fromkeys([*(requested or []), *forced])),
        wait,
        label=f"refit {node.name} from {node.params.get('provider')}",
    )
    ctx.record(wait=True)  # a replay fits inline, so the records after it see its result
    return {"material": node.id, "path": node.path, "warnings": warnings, **result}


def _state_after(project: Project, node: Node, changes: dict[str, Any]) -> LibraryState:
    """The library state the material has once ``changes`` apply."""
    import dataclasses

    from stokes.physics.materials.library import state_of

    updates: dict[str, Any] = {  # Any: field values
        key: changes[key]
        for key in ("provider", "fluid", "composition", "basis", "phase")
        if key in changes
    }
    if "tolerance" in changes:
        updates["tolerance"] = float(changes["tolerance"])
    if "max_degree" in changes:
        updates["max_degree"] = int(changes["max_degree"])
    for key, dimension in STATE_DIMENSIONS.items():
        if key in changes:
            updates[key] = _si(project, changes[key], dimension, key)
    return dataclasses.replace(state_of(node), **updates)


@command(
    "material.set_state",
    params=(
        MATERIAL,
        ParamDef("phase", "string", required=False, description=", ".join(PHASES) + "."),
        ParamDef("T_ref", "any", required=False, description=QUANTITY_HELP),
        ParamDef("p_ref", "any", required=False, description=QUANTITY_HELP),
        ParamDef("T_min", "any", required=False, description=QUANTITY_HELP),
        ParamDef("T_max", "any", required=False, description=QUANTITY_HELP),
        ParamDef("tolerance", "number", required=False, description="Fit tolerance."),
        ParamDef("max_degree", "integer", required=False, description="Highest fit degree."),
        ParamDef("fluid", "string", required=False, description="The provider's fluid id."),
        ParamDef("composition", "string", required=False, description="Fractions."),
        ParamDef("basis", "string", required=False, description=", ".join(BASES) + "."),
        ParamDef(
            "provider",
            "string",
            required=False,
            description="A library provider makes the material a library material.",
        ),
        ParamDef(
            "refit",
            "boolean",
            required=False,
            default=True,
            description="Fit the library properties again in the same undo step (default); "
            "false leaves them stale.",
        ),
        WAIT,
    ),
)
def material_set_state(
    ctx: CommandContext,
    material: str,
    phase: str | None = None,
    T_ref: Any = None,  # noqa: N803 - the contract's parameter name; Any: a quantity
    p_ref: Any = None,  # Any: a quantity in any accepted form
    T_min: Any = None,  # noqa: N803 - Any: a quantity in any accepted form
    T_max: Any = None,  # noqa: N803 - Any: a quantity in any accepted form
    tolerance: float | None = None,
    max_degree: int | None = None,
    fluid: str | None = None,
    composition: str | None = None,
    basis: str | None = None,
    provider: str | None = None,
    refit: bool = True,
    wait: bool = True,
) -> dict[str, Any]:  # Any: JSON
    """Change a material's state (phase, reference state, range, fit settings, library fluid);
    with ``refit`` the library properties are fitted again in the same undo step."""
    from stokes.model import SetParam
    from stokes.physics.materials import library
    from stokes.physics.materials.authoring import Checked
    from stokes.physics.materials.service import statuses

    project = ctx.session.require_project()
    service = _service(ctx)
    node = _material(project, material)
    service.require_idle(node.id, node.name)
    if phase is not None:
        adding.choice(phase, PHASES, "phase")
    if basis is not None:
        adding.choice(basis, BASES, "basis")
    if provider is not None:
        adding.choice(provider, PROVIDERS, "provider")
    adding.check_fit_settings(tolerance, max_degree)
    changes: dict[str, Any] = {  # Any: JSON parameter values
        key: value
        for key, value in (
            ("phase", phase),
            ("T_ref", T_ref),
            ("p_ref", p_ref),
            ("T_min", T_min),
            ("T_max", T_max),
            ("tolerance", tolerance),
            ("max_degree", max_degree),
            ("fluid", fluid),
            ("composition", composition),
            ("basis", basis),
            ("provider", provider),
        )
        if value is not None
    }
    if provider in LIBRARY:
        changes["source"] = "library"
    elif provider in ("none", "builtin"):
        changes["source"] = "user" if provider == "none" else "builtin"
    for key, dimension in STATE_DIMENSIONS.items():
        if key in changes:
            _si(project, changes[key], dimension, key)
    if not changes:
        raise ValidationError("material.set_state needs at least one state parameter")
    prefix = [SetParam(node.id, key, value) for key, value in changes.items()]
    label = f"set state of {node.name}"
    source = changes.get("source", node.params.get("source"))
    if (
        refit
        and source == "library"
        and changes.get("provider", node.params.get("provider")) in LIBRARY
    ):
        state = _state_after(project, node, changes)
        chosen = library.fittable(node, None)
        result = _refit(ctx, node, state, chosen, {}, [], wait, prefix=prefix, label=label)
        ctx.record(wait=True)
    else:
        before = statuses(node)
        cmd = Checked(label, list(prefix))
        with service.quiet():
            ctx.session.undo.push(cmd, project)
            _emit_changes(ctx, cmd)
        service.resync(node, before)
        result = {"fit": {}, "missing": [], "background": False, "pending": []}
    return {**_material_summary(project, node), **result}


@command(
    "material.cancel_fit",
    params=(
        ParamDef(
            "material",
            "string",
            required=False,
            description="Material whose background fit to stop; default every one.",
        ),
    ),
    mutates=False,
)
def material_cancel_fit(ctx: CommandContext, material: str | None = None) -> dict[str, Any]:
    """Stop background fits between two chunks; their properties stay stale until a refit."""
    service = _service(ctx)
    if material is None:
        return {"cancelled": service.cancel()}
    node = _material(ctx.session.require_project(), material)
    return {"cancelled": service.cancel(node.id)}


# ---------------------------------------------------------------- queries


@command(
    "material.evaluate",
    params=(
        MATERIAL,
        ParamDef(
            "properties",
            "array",
            required=False,
            description="Property kinds to evaluate; default every one the material has.",
        ),
        ParamDef("T", "array", required=False, description="Temperatures: K or quantity text."),
        ParamDef(
            "T_min", "any", required=False, description="Default the material's. " + QUANTITY_HELP
        ),
        ParamDef(
            "T_max", "any", required=False, description="Default the material's. " + QUANTITY_HELP
        ),
        ParamDef("count", "integer", required=False, default=101, description="Grid points."),
        ParamDef("p", "any", required=False, description="Absolute pressure; default p_ref."),
        ParamDef(
            "source",
            "string",
            required=False,
            default="method",
            description="method (the property's method), library (the provider's values, "
            "cached per session) or both.",
        ),
    ),
    mutates=False,
)
def material_evaluate(
    ctx: CommandContext,
    material: str,
    properties: list[Any] | None = None,  # Any: strings, checked below
    T: list[Any] | None = None,  # noqa: N803 - the contract's name; Any: numbers or text
    T_min: Any = None,  # noqa: N803 - Any: a quantity in any accepted form
    T_max: Any = None,  # noqa: N803 - Any: a quantity in any accepted form
    count: int = 101,
    p: Any = None,  # Any: a quantity in any accepted form
    source: str = "method",
) -> dict[str, Any]:  # Any: JSON
    """A material's properties on a temperature grid at one pressure, SI with each property's
    dimension and display unit; values that cannot be evaluated are null."""
    import numpy as np

    from stokes.physics.materials import evaluate as ev
    from stokes.physics.materials.errors import EvaluationFailed

    if source not in SOURCES:
        raise ValidationError(f"source must be one of {', '.join(SOURCES)}; got {source!r}")
    project = ctx.session.require_project()
    node = _material(project, material)
    if node.status == "failed":
        raise EvaluationFailed(f"{node.name}: {node.meta.get('error', 'an expression failed')}")
    state = ev.state_of(node)
    definitions = ev.definitions_of(node)
    kinds = [str(k) for k in properties] if properties else list(definitions)
    missing_kinds = [k for k in kinds if k not in definitions]
    if missing_kinds:
        raise ValidationError(
            f"{node.name} has no property {', '.join(missing_kinds)}; it has "
            f"{', '.join(definitions)}",
            details={"properties": missing_kinds},
        )
    for kind in kinds:
        child = node.child(kind)
        if child is not None and child.status == "failed":
            raise EvaluationFailed(
                f"{node.name}.{kind}: {child.meta.get('error', 'an expression failed')}",
                details={"property": kind},
            )
    if T is not None:
        if not T or len(T) > ev.MAX_POINTS:
            raise ValidationError(f"T needs 1 to {ev.MAX_POINTS} temperatures")
        grid: Array = np.array([_si(project, t, "temperature", "T") for t in T], dtype=np.float64)
        if not np.all(grid > 0.0):
            raise ValidationError("temperatures must be positive (kelvin)")
    else:
        low = _si(project, T_min, "temperature", "T_min") if T_min is not None else state.T_min
        high = _si(project, T_max, "temperature", "T_max") if T_max is not None else state.T_max
        grid = ev.temperature_grid(low, high, count)
    pressure = _si(project, p, "pressure", "p") if p is not None else state.p_ref
    if pressure <= 0.0:
        raise ValidationError("p is an absolute pressure and must be positive")
    pressures = np.full_like(grid, pressure)
    by_method = (
        ev.evaluate_all(definitions, state, kinds, grid, pressures) if source != "library" else {}
    )
    library, missing = (
        _library_values(ctx, node, kinds, grid, pressure) if source != "method" else ({}, [])
    )
    result: dict[str, Any] = {}  # Any: JSON
    for kind in kinds:
        dim = DIMENSIONS[PROPERTY_DIMENSIONS[kind]]
        values = by_method.get(kind)
        other = library.get(kind)
        entry: dict[str, Any] = {  # Any: JSON
            "dimension": dim.name,
            "unit": dim.si_unit,
            "display_unit": dim.unit_for(project.display_units),
            "method_id": definitions[kind].method,
            "method": ev.json_values(values) if values is not None else None,
            "library": ev.json_values(other) if other is not None else None,
            "missing": kind in missing,
            "max_rel_err": _deviation(values, other),
        }
        if values is not None and values.ndim > 1:
            entry["components"] = ["k1", "k2", "k3"]
        result[kind] = entry
    return {
        "material": node.id,
        "path": node.path,
        "name": node.name,
        "T": [float(t) for t in grid],
        "p": pressure,
        "source": source,
        "properties": result,
    }


def _library_values(
    ctx: CommandContext, node: Node, kinds: list[str], grid: Array, pressure: float
) -> tuple[dict[str, Array], list[str]]:
    """The provider's values for a library or built-in material (cached); none for a user
    material. A molar mass or formation enthalpy comes from the material itself."""
    from stokes.physics.materials.providers import get_provider

    provider = str(node.params.get("provider", "none"))
    if provider == "none":
        return {}, []
    found = _service(ctx).library_grid(provider, get_provider(provider), node, grid, pressure)
    values = {k: v for k, v in found.values.items() if k in kinds}
    return values, [k for k in kinds if k in found.missing]


def _deviation(values: Array | None, other: Array | None) -> float | None:
    import numpy as np

    if values is None or other is None or values.shape != other.shape:
        return None
    with np.errstate(all="ignore"):
        rel = np.abs(values - other) / np.abs(other)
    finite = rel[np.isfinite(rel)]
    return float(finite.max()) if finite.size else None


@command("material.check", params=(MATERIAL,), mutates=False)
def material_check(ctx: CommandContext, material: str) -> dict[str, Any]:  # Any: JSON
    """The consistency findings of one material (the rows that need no engine)."""
    from stokes.physics.materials.checks import check_material

    project = ctx.session.require_project()
    service = _service(ctx)
    node = _material(project, material)
    findings = [f.to_json() for f in check_material(node, pending=service.pending(node.id))]
    return {
        "material": node.id,
        "path": node.path,
        "name": node.name,
        "findings": findings,
        "ok": not any(f["severity"] == "error" for f in findings),
    }


@command("material.list", mutates=False)
def material_list(ctx: CommandContext) -> list[dict[str, Any]]:  # Any: JSON
    """The project's materials with their kind, source, provider, status and the regions that
    use each one (ADR-0022 decision 4.1)."""
    from stokes.api.commands.setup_plan import region_nodes
    from stokes.physics.materials.library import is_detached, stale_properties

    project = ctx.session.require_project()
    service = _service(ctx)
    group = project.find(MATERIALS_PATH)
    regions: dict[str, list[str]] = {}
    for region in region_nodes(project):
        held = region.params.get("material")
        if isinstance(held, str):
            regions.setdefault(held, []).append(region.id)
    items: list[dict[str, Any]] = []
    for node in group.children if group is not None else []:
        if node.type != "material":
            continue
        statuses = {node.status, *(c.status for c in node.children)}
        if service.busy(node.id) or stale_properties(node):
            statuses.add("stale")
        status = next((s for s in ("failed", "warning", "stale", "new") if s in statuses), "ok")
        items.append(
            {
                "name": node.name,
                "id": node.id,
                "path": node.path,
                "kind": node.params.get("kind"),
                "source": node.params.get("source"),
                "provider": node.params.get("provider"),
                "fluid": node.params.get("fluid"),
                "status": status,
                "fitting": service.busy(node.id),
                "detached": [
                    c.name for c in node.children if c.type == "property" and is_detached(c)
                ],
                "regions": regions.get(node.id, []),
            }
        )
    return items


# ---------------------------------------------------------------- the library


@command(
    "material.library_search",
    params=(
        ParamDef(
            "query",
            "string",
            required=False,
            default="",
            description="Name, alias, formula or CAS number; empty lists everything.",
        ),
        ParamDef(
            "providers",
            "array",
            required=False,
            description="Providers to search; default every available one.",
        ),
        ParamDef(
            "kind",
            "string",
            required=False,
            default="any",
            description="fluid, solid or any.",
        ),
        ParamDef("limit", "integer", required=False, default=50, description="Most records."),
    ),
    mutates=False,
    requires_project=False,
)
def material_library_search(
    ctx: CommandContext,
    query: str = "",
    providers: list[Any] | None = None,  # Any: strings, checked by get_provider
    kind: str = "any",
    limit: int = 50,
) -> dict[str, Any]:  # Any: JSON
    """Search the material libraries: exact names and aliases first, then CAS numbers and
    formulas, then substrings, across providers (each record says which transport models its
    provider has)."""
    from stokes.physics.materials.providers import available_providers, get_provider
    from stokes.physics.materials.providers.base import rank

    if limit < 1:
        raise ValidationError("limit must be at least 1")
    if kind not in ("fluid", "solid", "any"):
        raise ValidationError(f"kind must be fluid, solid or any; got {kind!r}")
    names = [str(n) for n in providers] if providers else available_providers()
    folded = query.strip().casefold()
    found: list[tuple[int, int, str, dict[str, Any]]] = []  # Any: JSON
    for order, name in enumerate(names):
        for record in get_provider(name).search(query, kind):
            position = rank(record, folded)
            found.append(
                (
                    2 if position is None else position,
                    order,
                    record.name.casefold(),
                    record.to_json(),
                )
            )
    found.sort(key=lambda item: item[:3])
    records = [item[3] for item in found]
    return {"records": records[:limit], "total": len(records)}


@command(
    "material.library_info",
    params=(
        ParamDef("fluid", "string", description="The record's id in the provider."),
        ParamDef("provider", "string", required=False, default="builtin", description="Provider."),
        ParamDef("composition", "string", required=False, default="", description="Fractions."),
        ParamDef("basis", "string", required=False, default="", description="Basis."),
        ParamDef("phase", "string", required=False, default="auto", description="Phase."),
        ParamDef("T_ref", "number", required=False, default=300.0, description="K."),
        ParamDef("p_ref", "number", required=False, default=101325.0, description="Pa."),
    ),
    mutates=False,
    requires_project=False,
)
def material_library_info(
    ctx: CommandContext,
    fluid: str,
    provider: str = "builtin",
    composition: str = "",
    basis: str = "",
    phase: str = "auto",
    T_ref: float = 300.0,  # noqa: N803 - the contract's parameter name
    p_ref: float = 101325.0,
) -> dict[str, Any]:  # Any: JSON
    """Everything a provider knows about one material: the record, its limits at p_ref
    (saturation temperature, phase at T_ref, fraction limits, freezing point), the values at
    (T_ref, p_ref) and the references; for the built-in catalogue the record's methods,
    provenance and check values."""
    from stokes.physics.materials.providers import get_provider

    adding.choice(phase, PHASES, "phase")
    return (
        get_provider(provider)
        .info(fluid, composition, basis, T_ref=float(T_ref), p_ref=float(p_ref), phase=phase)
        .to_json()
    )

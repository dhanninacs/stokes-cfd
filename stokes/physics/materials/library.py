"""Library materials (ADR-0018 decision 4; contract "Library materials"): what a material fitted
from a provider stores, how its fit is planned, run and applied, and when it is stale or
detached.

A library material keeps its state on the material node (provider, fluid, composition, basis,
phase, T_ref, p_ref, T_min, T_max, tolerance, max_degree). :func:`run_fit` asks the provider for
the 201-point verification grid at p_ref and fits each property with
:func:`stokes.physics.materials.fit.fit_property`; the outcome becomes property entries and
provenance (:func:`entries`, :func:`metas`) for a new material, or one undo step for an existing
one (:func:`refit_command`).

Provenance in each property's ``meta``: ``fit`` (provider, version, fluid, state, method,
degree, errors, tolerance, date, references, and the state it was fitted at) and ``signature``
(the names of the parameters the fit wrote and a hash of their values). A property is
**detached** once ``meta.detached`` is set (``material.set_method``, or ``parameter.set``
through the session's listener in ``service.py``) or its parameters no longer hash to the
signature: a hand edit made while no listener ran is still seen, and an undo re-attaches it.
A library property is **stale** when the material's state differs from the one its fit
recorded, or a background fit for it is pending; ``material.refit`` makes it current.

Import rule: numpy, ``stokes.model`` and this package.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from stokes.errors import ValidationError
from stokes.model.commands import Command, SetParam
from stokes.model.nodes import Node, QuantityValue
from stokes.physics.materials.authoring import Checked, SetMeta, check_all
from stokes.physics.materials.errors import OutOfRange, PropertyUnavailable
from stokes.physics.materials.fit import (
    FITTED_PROPERTIES,
    POINTS,
    FitInput,
    FitResult,
    Nasa7,
    fit_property,
    verification_grid,
)
from stokes.physics.materials.model import KIND_PROPERTIES
from stokes.physics.materials.providers.base import LibraryInfo

LIBRARY_PROVIDERS: tuple[str, ...] = ("coolprop", "coolprop_incomp", "cantera", "refprop")
STATE_KEYS: tuple[str, ...] = (
    "provider",
    "fluid",
    "composition",
    "basis",
    "phase",
    "T_ref",
    "p_ref",
    "T_min",
    "T_max",
)
"""The material parameters a fit depends on: changing one makes the library properties stale."""
QUANTITY_STATE: tuple[str, ...] = ("T_ref", "p_ref", "T_min", "T_max")
LIQUID_PHASES = frozenset({"liquid", "supercritical_liquid"})


@dataclass(frozen=True)
class LibraryState:
    """A library material's state, SI."""

    provider: str
    fluid: str
    composition: str = ""
    basis: str = ""
    phase: str = "auto"
    T_ref: float = 300.0
    p_ref: float = 101325.0
    T_min: float = 0.0
    T_max: float = 0.0
    tolerance: float = 0.01
    max_degree: int = 4

    def signature(self) -> dict[str, Any]:  # Any: JSON
        return {key: getattr(self, key) for key in STATE_KEYS}


def _si(node: Node, name: str, default: float) -> float:
    value = node.params.get(name)
    return float(value.value) if isinstance(value, QuantityValue) else default


def state_of(material: Node) -> LibraryState:
    params = material.params
    return LibraryState(
        provider=str(params.get("provider", "none")),
        fluid=str(params.get("fluid", "")),
        composition=str(params.get("composition", "")),
        basis=str(params.get("basis", "")),
        phase=str(params.get("phase", "auto")),
        T_ref=_si(material, "T_ref", 300.0),
        p_ref=_si(material, "p_ref", 101325.0),
        T_min=_si(material, "T_min", 273.15),
        T_max=_si(material, "T_max", 373.15),
        tolerance=float(params.get("tolerance", 0.01)),
        max_degree=int(params.get("max_degree", 4)),
    )


def is_library(material: Node) -> bool:
    return (
        material.params.get("source") == "library"
        and str(material.params.get("provider")) in LIBRARY_PROVIDERS
    )


# ---------------------------------------------------------------- signatures


def _canonical(value: Any) -> Any:  # Any: a stored parameter value in, JSON out
    if isinstance(value, QuantityValue):
        return [repr(float(value.value)), value.expr]
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return [repr(float(value)), None]
    if isinstance(value, list | tuple):
        return [repr(float(v)) for v in value]
    return str(value)


def signature_hash(method: str, params: Mapping[str, Any]) -> str:  # Any: parameter values
    """A short hash of a method and parameter values, the same for an entry and the node it
    became (numbers and quantities compare by their SI value; an expression always differs)."""
    canonical = {"method": method, **{k: _canonical(v) for k, v in sorted(params.items())}}
    text = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def signature_of_entry(entry: Mapping[str, Any]) -> dict[str, Any]:  # Any: JSON
    params = {k: v for k, v in entry.items() if k != "method"}
    return {"params": sorted(params), "hash": signature_hash(str(entry["method"]), params)}


def signature_matches(prop: Node) -> bool | None:
    """Whether the property still holds what its provenance wrote; None without a signature."""
    signature = prop.meta.get("signature")
    if not isinstance(signature, dict):
        return None
    names = [str(n) for n in signature.get("params", ())]
    current = {name: prop.params.get(name) for name in names}
    return signature_hash(str(prop.params.get("method")), current) == signature.get("hash")


def is_detached(prop: Node) -> bool:
    """Edited by hand since a provider or the catalogue wrote it."""
    return bool(prop.meta.get("detached")) or signature_matches(prop) is False


def fit_is_current(material: Node, prop: Node) -> bool:
    """Whether the property's fit was made at the material's present state (a fit that does not
    record its state, the built-in catalogue's, is not a fit of this library material)."""
    fit = prop.meta.get("fit")
    if not isinstance(fit, dict):
        return False
    recorded = fit.get("state")
    if not isinstance(recorded, dict):
        return False
    now = state_of(material).signature()
    for key in STATE_KEYS:
        mine, theirs = now.get(key), recorded.get(key)
        if isinstance(mine, float) and isinstance(theirs, int | float):
            if not math.isclose(mine, float(theirs), rel_tol=1e-12, abs_tol=1e-12):
                return False
        elif mine != theirs:
            return False
    return True


def stale_properties(material: Node) -> list[Node]:
    """The library properties of a material that wait for a fit: never fitted (a background fit
    pending, failed or cancelled), or fitted at another state than the present one. A property
    the provider has no model for, or one edited by hand, is not waiting for anything."""
    if not is_library(material):
        return []
    return [
        prop
        for prop in material.children
        if prop.type == "property"
        and prop.name in FITTED_PROPERTIES
        and "missing" not in prop.meta
        and not is_detached(prop)
        and not fit_is_current(material, prop)
    ]


# ---------------------------------------------------------------- planning


def fit_phase(phase: str, info: LibraryInfo) -> str:
    """``gas`` or ``liquid``: which candidate order the fit uses."""
    if phase == "liquid":
        return "liquid"
    if phase in ("gas", "supercritical"):
        return "gas"
    found = str(info.limits.get("phase_at_T_ref") or "")
    return "liquid" if found in LIQUID_PHASES else "gas"


def default_range(
    provider: str, phase: str, info: LibraryInfo, t_ref: float
) -> tuple[float, float]:
    """The contract's default fit range for a library material."""
    limits = info.limits
    low_limit = _number(limits.get("T_min")) or 1.0
    high_limit = _number(limits.get("T_max")) or math.inf
    t_sat = _number(limits.get("T_sat"))
    if provider == "coolprop_incomp":
        freeze = _number(limits.get("T_freeze"))
        low = max(low_limit, (freeze + 1.0) if freeze else low_limit, t_ref - 60.0)
        high = min(high_limit, t_ref + 60.0)
    elif phase == "liquid":
        low = max(low_limit + 1.0, t_ref - 30.0)
        high = min((t_sat - 0.5) if t_sat else math.inf, t_ref + 70.0, high_limit)
    else:
        low = max((t_sat + 0.5) if t_sat and t_sat < t_ref else 0.0, t_ref - 50.0, low_limit)
        high = min(t_ref + 450.0, high_limit)
    if not (math.isfinite(low) and math.isfinite(high) and 0.0 < low < high):
        raise OutOfRange(
            f"no default range around T_ref = {t_ref:g} K fits the provider's limits "
            f"({low_limit:g} K to {high_limit:g} K); give T_min and T_max",
            details={"T_ref": t_ref, "limits": dict(limits)},
        )
    return low, high


def _number(value: Any) -> float | None:  # Any: JSON value
    if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


# ---------------------------------------------------------------- running


@dataclass
class FitOutcome:
    """Everything a library fit produced for one material."""

    state: LibraryState
    phase: str
    results: dict[str, FitResult]
    missing: dict[str, str]
    molar_mass: float
    formation_enthalpy: float
    provider_version: str
    references: dict[str, str]
    T_sat: float | None = None
    phases: list[str] = field(default_factory=list)
    generated: str = field(default_factory=lambda: date.today().isoformat())


Progress = Callable[[int, int], None]


def run_fit(
    provider: Any,  # Any: a Provider; RemoteProvider adds chunked evaluation
    state: LibraryState,
    info: LibraryInfo,
    properties: Sequence[str] = FITTED_PROPERTIES,
    methods: Mapping[str, str] | None = None,
    *,
    required: Sequence[str] = (),
    provider_version: str = "",
    on_progress: Progress | None = None,
    cancel: threading.Event | None = None,
) -> FitOutcome:
    """Evaluate the provider over the state's range at p_ref and fit each property.

    A property the provider has no model for is ``missing``, unless it is ``required`` (asked
    for by name) or has a forced method: then ``materials.property_unavailable``. A property no
    form fits raises ``materials.fit_tolerance``.
    """
    forced = dict(methods or {})
    needed = set(required) | set(forced)
    t = verification_grid(state.T_min, state.T_max)
    if hasattr(provider, "evaluate_chunked"):
        grid = provider.evaluate_chunked(
            state.fluid,
            state.composition,
            state.basis,
            state.phase,
            t,
            state.p_ref,
            on_progress=on_progress,
            cancel=cancel,
        )
    else:
        grid = provider.evaluate(
            state.fluid, state.composition, state.basis, state.phase, t, state.p_ref
        )
    record = info.record
    at_ref = dict(info.detail.get("values_at_ref") or {})
    nasa = info.detail.get("nasa7")
    molar_mass = float(record.molar_mass or 0.0)
    enthalpy = float(nasa["formation_enthalpy"]) if isinstance(nasa, dict) else 0.0
    entropy = float(nasa["formation_entropy"]) if isinstance(nasa, dict) else 0.0
    phase = fit_phase(state.phase, info)
    results: dict[str, FitResult] = {}
    missing: dict[str, str] = {}
    for prop in properties:
        if prop in grid.missing or prop not in grid.values:
            reason = f"{state.provider} has no {prop.replace('_', ' ')} model for {state.fluid}"
            if prop in needed:
                raise PropertyUnavailable(
                    reason + "; enter it with material.set_method or use REFPROP",
                    details={"property": prop, "provider": state.provider, "fluid": state.fluid},
                )
            missing[prop] = reason
            continue
        value_at_ref = at_ref.get(prop)
        inp = FitInput(
            kind=prop,
            phase=phase,
            t=t,
            values=grid.values[prop],
            p_ref=state.p_ref,
            T_ref=state.T_ref,
            at_ref=float(value_at_ref) if isinstance(value_at_ref, int | float) else math.nan,
            molar_mass=molar_mass,
            tolerance=state.tolerance,
            max_degree=state.max_degree,
            method=forced.get(prop),
            formation_enthalpy=enthalpy,
            formation_entropy=entropy,
            nasa7=_nasa7(nasa) if prop == "specific_heat" else None,
        )
        results[prop] = fit_property(inp)
    return FitOutcome(
        state=state,
        phase=phase,
        results=results,
        missing=missing,
        molar_mass=molar_mass,
        formation_enthalpy=enthalpy,
        provider_version=provider_version,
        references=dict(info.references),
        T_sat=_number(info.limits.get("T_sat")),
        phases=list(grid.phases),
    )


def _nasa7(raw: Any) -> Nasa7 | None:  # Any: JSON from the worker
    if not isinstance(raw, dict):
        return None
    coefficients = raw.get("coefficients")
    breakpoints = raw.get("breakpoints")
    if not (isinstance(coefficients, list) and len(coefficients) == 14):
        return None
    if not (isinstance(breakpoints, list) and len(breakpoints) == 3):
        return None
    low, middle, high = (float(b) for b in breakpoints)
    return Nasa7(tuple(float(c) for c in coefficients), (low, middle, high))


# ---------------------------------------------------------------- applying


def fit_record(outcome: FitOutcome, result: FitResult) -> dict[str, Any]:  # Any: JSON
    """``meta.fit`` of one property (contract "property", meta)."""
    state = outcome.state
    record = {
        "provider": state.provider,
        "provider_version": outcome.provider_version,
        "fluid": state.fluid,
        "composition": state.composition,
        "basis": state.basis,
        "phase": state.phase,
        "phase_used": outcome.phase,
        "p_ref": state.p_ref,
        "T_min": state.T_min,
        "T_max": state.T_max,
        "points": POINTS,
        **result.summary(),
        "generated": outcome.generated,
        "reference": dict(outcome.references),
        "state": state.signature(),
    }
    if outcome.T_sat is not None:
        record["T_sat"] = outcome.T_sat
    return record


def entries(
    outcome: FitOutcome, kind: str, placeholders: Mapping[str, float]
) -> dict[str, dict[str, Any]]:  # Any: JSON property entries
    """Every property entry of the material: the fits, the molar mass and formation enthalpy,
    and, for a property the provider lacks, a constant ``placeholders`` gives (marked missing)."""
    found: dict[str, dict[str, Any]] = {}
    for prop in KIND_PROPERTIES[kind]:
        if prop in outcome.results:
            found[prop] = outcome.results[prop].entry()
        elif prop == "molar_mass":
            found[prop] = {"method": "constant", "value": outcome.molar_mass}
        elif prop == "formation_enthalpy":
            found[prop] = {"method": "constant", "value": outcome.formation_enthalpy}
        else:
            found[prop] = {"method": "constant", "value": float(placeholders[prop])}
    return found


def metas(
    outcome: FitOutcome, properties: Mapping[str, Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:  # Any: JSON meta values
    """Each property's provenance: ``signature`` for every fitted kind (so a hand edit is seen
    even on a stand-in), ``fit`` for the fitted ones, ``missing`` for what the provider lacks."""
    found: dict[str, dict[str, Any]] = {}
    for prop, entry in properties.items():
        if prop not in FITTED_PROPERTIES:
            continue
        meta: dict[str, Any] = {"signature": signature_of_entry(entry)}
        if prop in outcome.results:
            meta["fit"] = fit_record(outcome, outcome.results[prop])
        elif prop in outcome.missing:
            meta["missing"] = {"provider": outcome.state.provider, "reason": outcome.missing[prop]}
        found[prop] = meta
    return found


def _reset_unused(node: Node, prop: str, entry: Mapping[str, Any]) -> list[Command]:
    """Parameters the property's previous method used and the fitted one does not, back to their
    defaults, so a property holds the same values however its fit arrived."""
    from stokes.model.schema import registry
    from stokes.physics.materials import catalogue
    from stokes.physics.materials.errors import InvalidMethod

    previous = str(node.params.get("method"))
    if previous == entry["method"]:
        return []
    try:
        names = catalogue.method(prop, previous).param_names
    except InvalidMethod:
        return []
    spec = registry.get("property")
    resets: list[Command] = []
    for name in names:
        if name in entry or name in ("valid_min", "valid_max"):
            continue
        param = spec.param(name)
        default: Any = param.default  # Any: the parameter's JSON default
        if param.kind == "quantity":
            default = 0.0 if default is None else float(default)
        elif param.kind == "array":
            default = list(default or ())
        if default is not None:
            resets.append(SetParam(node.id, name, default))
    return resets


def refit_command(material: Node, outcome: FitOutcome, label: str = "") -> Checked:
    """One undo step that writes a fit into an existing material's properties: each fitted
    property's method and parameters, its ``meta.fit`` and ``signature``, and re-attaches it."""
    commands: list[Command] = []
    for prop, result in outcome.results.items():
        node = material.child(prop)
        if node is None or node.type != "property":
            continue
        entry = result.entry()
        commands.append(SetParam(node.id, "method", entry["method"]))
        commands.extend(SetParam(node.id, k, v) for k, v in entry.items() if k != "method")
        commands.extend(_reset_unused(node, prop, entry))
        commands.append(SetMeta(node.id, "fit", fit_record(outcome, result)))
        commands.append(SetMeta(node.id, "signature", signature_of_entry(entry)))
        commands.append(SetMeta(node.id, "missing", None))
        if node.meta.get("detached"):
            commands.append(SetMeta(node.id, "detached", None))
    molar = material.child("molar_mass")
    if molar is not None and outcome.molar_mass > 0.0:
        commands.append(SetParam(molar.id, "value", outcome.molar_mass))
    enthalpy = material.child("formation_enthalpy")
    if enthalpy is not None and outcome.state.provider == "cantera":
        commands.append(SetParam(enthalpy.id, "value", outcome.formation_enthalpy))
    return Checked(
        label or f"refit {material.name} from {outcome.state.provider}",
        commands,
        check=check_all(material.id),
    )


def fittable(material: Node, requested: Sequence[str] | None) -> list[str]:
    """The properties a refit writes: those asked for (detached ones re-attach), or every
    fitted kind not edited by hand; ``validation`` for a property the material kind lacks."""
    kind = str(material.params.get("kind", "fluid"))
    if requested:
        unknown = [
            p for p in requested if p not in FITTED_PROPERTIES or p not in KIND_PROPERTIES[kind]
        ]
        if unknown:
            raise ValidationError(
                f"only {', '.join(FITTED_PROPERTIES)} are fitted from a library; got "
                f"{', '.join(unknown)}",
                details={"properties": unknown},
            )
        return list(dict.fromkeys(requested))
    chosen = []
    for prop in FITTED_PROPERTIES:
        node = material.child(prop)
        if node is not None and node.type == "property" and not is_detached(node):
            chosen.append(prop)
    return chosen


def remembered_methods(material: Node, properties: Sequence[str]) -> dict[str, str]:
    """The forms a user forced on earlier fits (``meta.fit.forced``), which a refit keeps."""
    found: dict[str, str] = {}
    for prop in properties:
        node = material.child(prop)
        fit = node.meta.get("fit") if node is not None else None
        if isinstance(fit, dict) and fit.get("forced") and isinstance(fit.get("method"), str):
            found[prop] = fit["method"]
    return found

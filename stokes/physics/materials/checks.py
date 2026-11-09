"""Consistency checks of a material (ADR-0018 decision 7; contract "Checks").

:func:`check_material` returns findings ``{code, severity, material, property, message,
suggestion}`` for one material; ``material.check`` reports them and the compiler (slice 3) will
refuse on any error. This slice has the rows that need no engine:

- ``materials.structure`` (error): a property child is missing, extra, or its kind or dimension
  disagrees with its name (``node.*`` can still remove or rename them).
- ``materials.range`` (error): the material's range or reference state is not physical.
- ``materials.invalid_method`` (error): the method is foreign to the property or the material
  kind, or not available in this release.
- ``materials.needs_molar_mass`` (error): the method needs the molar mass and it is 0.
- ``materials.evaluation`` (error): the parameters are malformed, an expression failed, or the
  method gives a non-finite or non-positive value inside [``T_min``, ``T_max``] at ``p_ref``.
- ``materials.stale_fit`` (error): a property is ``stale``: marked so, fitted at another state
  of the material, or waiting for a background fit.
- ``materials.property_unavailable`` (error): the provider has no model for the property and
  it still holds the stand-in value it was created with.
- ``materials.boussinesq_range`` (warning): beta (T - T_ref) exceeds 0.1 inside the range.
- ``materials.extrapolation`` (warning): the method's validity range does not cover the
  material's range.
- ``materials.phase_forced`` (info): the phase is forced and the range crosses saturation at
  p_ref, so the values beyond it are metastable.

Engine rows (``energy_required``, ``unsupported_by_engine``, ``runtime_compiled``, ``tabulated``,
``conductivity_*``, ``energy_unused``, ``non_newtonian_laminar``) and the case rows
(``kind_mismatch``, ``pressure_mismatch``) arrive with the slices that bring engines and
regions.

Import rule: numpy, ``stokes.model`` and this package.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

import numpy as np

from stokes.errors import StokesError
from stokes.model.nodes import Node
from stokes.physics.materials import catalogue
from stokes.physics.materials.errors import InvalidMethod, NeedsMolarMass
from stokes.physics.materials.evaluate import (
    POSITIVE_PROPERTIES,
    Definition,
    State,
    definitions_of,
    evaluate_all,
    state_of,
    validate,
    validity,
)
from stokes.physics.materials.methods import Array
from stokes.physics.materials.model import KIND_PROPERTIES, PROPERTY_DIMENSIONS

SEVERITIES: tuple[str, ...] = ("error", "warning", "info")
GRID_POINTS = 51
BOUSSINESQ_LIMIT = 0.1
"""Largest beta (T - T_ref) the Boussinesq approximation is trusted with [fu p1433]."""


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    material: str
    property: str | None
    message: str
    suggestion: str | None = None

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return {
            "code": self.code,
            "severity": self.severity,
            "material": self.material,
            "property": self.property,
            "message": self.message,
            "suggestion": self.suggestion,
        }


class _Report:
    def __init__(self, material: Node) -> None:
        self.material = material
        self.findings: list[Finding] = []

    def add(
        self,
        code: str,
        severity: str,
        message: str,
        prop: str | None = None,
        suggestion: str | None = None,
    ) -> None:
        self.findings.append(Finding(code, severity, self.material.id, prop, message, suggestion))


def _structure(report: _Report, material: Node) -> list[str]:
    """Report children that do not match the material kind; returns the sound property kinds."""
    kind = str(material.params.get("kind"))
    expected = KIND_PROPERTIES.get(kind, ())
    sound: list[str] = []
    for extra in material.children:
        if extra.type != "property" or extra.name not in expected:
            report.add(
                "materials.structure",
                "error",
                f"{extra.path} is not a property a {kind} material has",
                suggestion="remove it; material.* commands own a material's children",
            )
    for prop in expected:
        child = material.child(prop)
        if child is None or child.type != "property":
            report.add(
                "materials.structure",
                "error",
                f"{material.name} lacks its {prop} property",
                prop,
                "undo the change that removed it, or create the material again",
            )
        elif (
            child.params.get("kind") != prop
            or child.params.get("dimension") != PROPERTY_DIMENSIONS[prop]
        ):
            report.add(
                "materials.structure",
                "error",
                f"{child.path} has kind {child.params.get('kind')!r} and dimension "
                f"{child.params.get('dimension')!r}; its name says {prop}",
                prop,
            )
        else:
            sound.append(prop)
    return sound


def _state(report: _Report, material: Node, state: State) -> bool:
    if material.status == "failed":
        report.add(
            "materials.evaluation",
            "error",
            f"{material.name}: {material.meta.get('error', 'an expression failed')}",
        )
    ok = 0.0 < state.T_min < state.T_max and state.T_ref > 0.0 and state.p_ref > 0.0
    if not ok:
        report.add(
            "materials.range",
            "error",
            f"{material.name} needs 0 < T_min < T_max and positive T_ref and p_ref; got "
            f"{state.T_min:g} K to {state.T_max:g} K, T_ref {state.T_ref:g} K, "
            f"p_ref {state.p_ref:g} Pa",
        )
    return ok


def _method(
    report: _Report, node: Node, definition: Definition, state: State, stale: bool = False
) -> bool:
    prop = definition.property
    if node.status == "stale" or stale:
        report.add(
            "materials.stale_fit",
            "error",
            f"{prop} of {node.parent.name if node.parent else '?'} waits for a refit",
            prop,
            "run material.refit",
        )
    if node.status == "failed":
        report.add("materials.evaluation", "error", str(node.meta.get("error", "failed")), prop)
        return False
    try:
        spec = catalogue.method(prop, definition.method)
        catalogue.check_choice(spec, state.kind)
        validate(definition, spec, state)
    except NeedsMolarMass as exc:
        report.add("materials.needs_molar_mass", "error", exc.message, prop, "set the molar mass")
        return False
    except InvalidMethod as exc:
        report.add("materials.invalid_method", "error", exc.message, prop)
        return False
    except StokesError as exc:
        report.add("materials.evaluation", "error", exc.message, prop)
        return False
    return True


def _values(report: _Report, prop: str, values: Array, t: Array) -> None:
    flat = values if values.ndim == 1 else values.min(axis=0)
    bad = ~np.isfinite(flat)
    if np.any(bad):
        at = float(t[np.argmax(bad)])
        report.add(
            "materials.evaluation",
            "error",
            f"{prop} cannot be evaluated at {at:g} K (no finite value)",
            prop,
            "check the method's parameters or narrow the range",
        )
        return
    if prop in POSITIVE_PROPERTIES and np.any(flat <= 0.0):
        at = float(t[np.argmax(flat <= 0.0)])
        report.add(
            "materials.evaluation",
            "error",
            f"{prop} is not positive at {at:g} K",
            prop,
            "check the method's parameters or narrow the range",
        )


def _warnings(report: _Report, definition: Definition, state: State) -> None:
    prop = definition.property
    if definition.method == "boussinesq":
        beta = definition.values.get("expansion_coefficient", 0.0)
        worst = abs(beta) * max(abs(state.T_max - state.T_ref), abs(state.T_min - state.T_ref))
        if worst > BOUSSINESQ_LIMIT:
            report.add(
                "materials.boussinesq_range",
                "warning",
                f"beta (T - T_ref) reaches {worst:.3g} inside the range; the Boussinesq "
                f"approximation is trusted below {BOUSSINESQ_LIMIT}",
                prop,
                "narrow the range or use a temperature-dependent density",
            )
    trusted = validity(definition)
    if trusted is not None and (trusted[0] > state.T_min or trusted[1] < state.T_max):
        report.add(
            "materials.extrapolation",
            "warning",
            f"{prop}: the method is trusted from {trusted[0]:g} K to {trusted[1]:g} K, the "
            f"material's range is {state.T_min:g} K to {state.T_max:g} K",
            prop,
            "narrow the material's range or extend the method",
        )


def _library_rows(report: _Report, material: Node, state: State) -> None:
    """The provider rows: a stand-in value for a property the provider lacks, a forced phase
    across saturation."""
    from stokes.physics.materials.library import signature_matches

    phase = str(material.params.get("phase", "auto"))
    crossings: set[float] = set()
    for node in material.children:
        missing = node.meta.get("missing")
        if node.type == "property" and isinstance(missing, dict) and signature_matches(node):
            report.add(
                "materials.property_unavailable",
                "error",
                f"{node.name} of {material.name}: {missing.get('reason') or 'no model'}; the "
                "value is a stand-in",
                node.name,
                "enter it with material.set_method, or use REFPROP",
            )
        fit = node.meta.get("fit")
        t_sat = fit.get("T_sat") if isinstance(fit, dict) else None
        if isinstance(t_sat, int | float) and state.T_min < t_sat < state.T_max:
            crossings.add(float(t_sat))
    if phase in ("liquid", "gas") and crossings:
        t_sat = min(crossings)
        report.add(
            "materials.phase_forced",
            "info",
            f"{material.name} is forced {phase} from {state.T_min:g} K to {state.T_max:g} K; "
            f"it boils at {t_sat:g} K at {state.p_ref:g} Pa, so the values beyond are metastable",
            suggestion="narrow the range, or set phase auto",
        )


def check_material(material: Node, *, pending: Collection[str] = ()) -> list[Finding]:
    """Every engine-free finding for one ``material`` node, errors first in tree order.
    ``pending``: the properties a running background fit will write."""
    from stokes.physics.materials.library import stale_properties

    report = _Report(material)
    sound = _structure(report, material)
    state = state_of(material)
    range_ok = _state(report, material, state)
    definitions = definitions_of(material)
    stale = {p.id for p in stale_properties(material)} | {
        c.id for c in material.children if c.type == "property" and c.name in pending
    }
    usable: list[str] = []
    for prop in sound:
        node = material.child(prop)
        if node is not None and _method(
            report, node, definitions[prop], state, stale=node.id in stale
        ):
            usable.append(prop)
    _library_rows(report, material, state)
    if range_ok and usable:
        t = np.linspace(state.T_min, state.T_max, GRID_POINTS)
        p = np.full_like(t, state.p_ref)
        for prop in usable:
            try:
                values = evaluate_all(definitions, state, [prop], t, p)[prop]
            except StokesError as exc:
                report.add("materials.evaluation", "error", f"{prop}: {exc.message}", prop)
                continue
            _values(report, prop, values, t)
            _warnings(report, definitions[prop], state)
    order = {severity: rank for rank, severity in enumerate(SEVERITIES)}
    return sorted(report.findings, key=lambda f: order[f.severity])

"""The undo steps the ``material.*`` commands push (ADR-0018 decision 9), and the template
defaults of ``material.create``.

A material and its property children are added as one :class:`Checked` composite, which
validates every property's method once it has applied and undoes itself when one cannot be
evaluated, so no command leaves a material behind that its methods cannot describe. Property
ids derive from the material's id (:func:`property_id`), so a journal replays exactly.
:class:`SetMeta` records a library property's provenance (``meta.library``, ``meta.fit``) and
``detached`` inside the same undo step as the change that causes it.

The templates of ``material.create`` come from the built-in catalogue (decisions log F6 (a)): a
liquid from ``water_liquid``, a gas from ``air``, a solid from ``aluminium``, each property a
constant at the record's T_ref (fixed values, F6 (b)) except a gas's density, which keeps the
record's ideal-gas law. Nothing comes from Fluent's materials database.

Import rule: ``stokes.model`` and this package's ``model``, ``evaluate`` and built-in provider.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar

from stokes.errors import StokesError
from stokes.model.commands import AddNode, Command, Composite, SetParam
from stokes.model.ids import new_id, stable_id
from stokes.model.nodes import Node
from stokes.model.project import Project
from stokes.physics.materials.evaluate import definitions_of, resolve, state_of
from stokes.physics.materials.model import KIND_PROPERTIES, MATERIALS_PATH, PROPERTY_DIMENSIONS

Params = Mapping[str, Any]  # Any: JSON parameter values (numbers, quantity text, arrays)

TEMPLATE_RECORDS: dict[str, str] = {"liquid": "water_liquid", "gas": "air", "solid": "aluminium"}
"""``material.create``'s templates and the built-in record each one is made from."""
KEPT_LAWS: frozenset[str] = frozenset({"ideal_gas"})
"""Record methods a template keeps as they are: laws with no fitted coefficient."""


def template(name: str) -> dict[str, Any]:  # Any: JSON
    """A template from its built-in record: ``{kind, record, state, properties}``, every
    property a constant at the record's T_ref except the laws in :data:`KEPT_LAWS`."""
    import numpy as np

    from stokes.physics.materials.evaluate import evaluate_all
    from stokes.physics.materials.providers import builtin

    found = builtin.record(TEMPLATE_RECORDS[name])
    state = builtin.state_of(found)
    at = np.array([state.T_ref])
    values = evaluate_all(
        builtin.definitions_of(found),
        state,
        list(found["properties"]),
        at,
        np.full_like(at, state.p_ref),
    )
    properties: dict[str, dict[str, Any]] = {}
    for prop, entry in found["properties"].items():
        if entry["method"] in KEPT_LAWS:
            properties[prop] = {"method": entry["method"]}
        else:
            properties[prop] = {"method": "constant", "value": float(values[prop][0])}
    return {
        "kind": found["kind"],
        "record": found["id"],
        "state": {"T_ref": state.T_ref, "T_min": state.T_min, "T_max": state.T_max},
        "properties": properties,
    }


@dataclass
class SetMeta(Command):
    """Set one key of a node's ``meta`` (``None`` removes it), undoably."""

    name: ClassVar[str] = "set_meta"
    node: str
    key: str
    value: Any = None  # Any: JSON value
    old: Any = field(default=None, repr=False)  # Any: the previous JSON value
    had: bool = field(default=False, repr=False)

    def apply(self, project: Project) -> Any:  # Any: JSON node summary
        target = project.get(self.node)
        self.had = self.key in target.meta
        self.old = copy.deepcopy(target.meta.get(self.key))
        if self.value is None:
            target.meta.pop(self.key, None)
        else:
            target.meta[self.key] = copy.deepcopy(self.value)
        return target.summary()

    def revert(self, project: Project) -> None:
        target = project.get(self.node)
        if self.had:
            target.meta[self.key] = copy.deepcopy(self.old)
        else:
            target.meta.pop(self.key, None)

    def to_record(self) -> dict[str, Any]:  # Any: JSON
        return {"command": self.name, "node": self.node, "key": self.key, "value": self.value}

    def describe(self) -> str:
        return f"set {self.key}"


@dataclass
class Checked(Composite):
    """A composite that runs ``check`` once it has applied and reverts itself when the check
    raises a typed error, on redo as on the first apply."""

    check: Callable[[Project], None] | None = field(default=None, repr=False, compare=False)

    def apply(self, project: Project) -> Any:  # Any: JSON results of the parts
        results = super().apply(project)
        if self.check is not None:
            try:
                self.check(project)
            except StokesError:
                super().revert(project)
                self.applied = 0
                raise
        return results


def property_id(material_id: str, kind: str) -> str:
    """The id of a material's property child, derived from the material's."""
    return stable_id("n", f"{material_id}/{kind}")


def ensure_materials_group(project: Project) -> Node | None:
    """Add ``/physics/materials`` to a project that lacks it; the new group, or None.

    It is part of the skeleton, so it is added outside the undo stack with the structural id a
    new project gives it.
    """
    if project.find(MATERIALS_PATH) is not None:
        return None
    return project.add("/physics", "group", "materials", node_id=stable_id("g", MATERIALS_PATH))


def check_all(material_id: str) -> Callable[[Project], None]:
    """A check that every property of the material resolves against the catalogue."""

    def check(project: Project) -> None:
        material = project.get(material_id)
        state = state_of(material)
        for definition in definitions_of(material).values():
            resolve(definition, state)

    return check


def check_one(material_id: str, kind: str) -> Callable[[Project], None]:
    """A check that one property of the material resolves against the catalogue."""

    def check(project: Project) -> None:
        material = project.get(material_id)
        definition = definitions_of(material).get(kind)
        if definition is not None:
            resolve(definition, state_of(material))

    return check


def create_command(
    name: str,
    kind: str,
    material_params: Params,
    properties: Mapping[str, Params],
    *,
    metas: Mapping[str, Mapping[str, Any]] | None = None,  # Any: JSON meta values
    node_id: str | None = None,
    label: str = "",
    material_meta: Mapping[str, Any] | None = None,  # Any: JSON meta values
) -> Checked:
    """One undo step: the material under ``/physics/materials``, one property child per kind
    the material kind takes (with its method and parameters), and their ``meta``."""
    material_id = node_id or new_id()
    commands: list[Command] = [
        AddNode(MATERIALS_PATH, "material", name, dict(material_params), node_id=material_id)
    ]
    for key, value in (material_meta or {}).items():
        commands.append(SetMeta(material_id, key, value))
    for prop in KIND_PROPERTIES[kind]:
        pid = property_id(material_id, prop)
        params = {"kind": prop, "dimension": PROPERTY_DIMENSIONS[prop], **properties[prop]}
        commands.append(AddNode(material_id, "property", prop, params, node_id=pid))
        for key, value in (metas or {}).get(prop, {}).items():
            commands.append(SetMeta(pid, key, value))
    return Checked(label or f"create material {name}", commands, check=check_all(material_id))


def set_method_command(
    material: Node,
    prop: Node,
    method: str,
    params: Params,
    *,
    detach: bool,
) -> Checked:
    """One undo step: the method, then its parameters, then (for a library property) the
    ``detached`` mark; checked once all are in."""
    commands: list[Command] = [SetParam(prop.id, "method", method)]
    commands.extend(SetParam(prop.id, key, value) for key, value in params.items())
    if detach:
        commands.append(SetMeta(prop.id, "detached", True))
    if "missing" in prop.meta:
        commands.append(SetMeta(prop.id, "missing", None))
    return Checked(
        f"set {prop.name} method of {material.name}",
        commands,
        check=check_one(material.id, prop.name),
    )

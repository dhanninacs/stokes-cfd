"""The condition catalogue (ADR-0022 decisions 2.2 to 2.7): every kind of boundary condition, the
``boundary`` parameters it uses and when, and what it means for the solver, the check and the
viewport.

:data:`KINDS` holds the eight rows of the ADR's kinds table, in its order. One ``boundary`` node
type carries the union of their parameters (``stokes.geometry.model``), as one ``property`` type
carries every material method's; a kind reads only the parameters its :class:`ParamUse` entries
name, and a parameter with a ``when`` only while each switch it names holds one of the listed
choices: ``direction: components`` uses ``velocity_x``, ``velocity_y`` and ``velocity_z`` instead
of ``velocity``. Unused parameters keep their values and are ignored. The kind ids and the choices
of the boundary's other enums belong to the node type (``CONDITION_KINDS``, ``DIRECTIONS``,
``TURBULENCE_INPUTS``, ``SHEARS`` and ``THERMALS`` in ``stokes.geometry.model``); the catalogue
imports them, so that the schema and the ``when`` mappings share one vocabulary, and refuses to
load when its kinds differ from ``CONDITION_KINDS``.

Who reads what: ``boundary.kinds`` returns the table with each parameter's JSON schema;
``boundary.assign`` and ``boundary.set`` (T-036) refuse kinds and values through :func:`kind`,
:meth:`ConditionSpec.uses` and :func:`check_rule`; ``setup.check`` (T-037) applies the rules to
the parameters in use, reports ``needs`` as information until the physics models exist (M3) and
reads ``fields`` to tell whether a fluid region fixes its pressure; the value panel (T-044) shows
what :meth:`ConditionSpec.uses` returns; the glyph layer (T-046) draws ``glyph`` in ``color``; and
M3's compiler maps each kind to per-field entries (``docs/05-openfoam-integration.md`` section 2).
The contract is ``docs/engineering/setup-schema.md``.

Import rule: the standard library, this package's errors and ``stokes.geometry.model`` (the node
vocabulary; standard library only) at module level: nothing of ``stokes.model``, nothing heavy.
``import stokes.api`` does not load this module, because registering the node types needs only the
vocabulary; the command modules load it (``boundary.kinds``).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, get_args

from stokes.geometry.model import (
    CONDITION_KINDS,
    DIRECTIONS,
    SELECTION_PALETTE,
    THERMALS,
    TURBULENCE_INPUTS,
)
from stokes.physics.conditions import ConditionError, InvalidKind

Rule = Literal["any", "positive", "nonnegative", "nonzero", "fraction"]
Family = Literal["inlet", "outlet", "wall", "constraint", "interface"]
Glyph = Literal["arrow_in", "arrow_out", "plane", "pair", "diamond", "none"]

RULES: tuple[str, ...] = get_args(Rule)
"""Checks on a parameter's SI value (:func:`check_rule`): ``fraction`` is 0 < x < 1, and a vector
is judged by its length."""
FAMILIES: tuple[str, ...] = get_args(Family)
GLYPHS: tuple[str, ...] = get_args(Glyph)
"""The viewport markers of ADR-0022 decision 8.1: one instance per boundary, never per face;
``none`` draws nothing (the tag's tint suffices)."""
FIELDS: tuple[str, ...] = ("U", "p", "T", "turbulence")
"""The solver fields a kind may fix; ``turbulence`` stands for k, epsilon, omega and nut."""
PHYSICS: tuple[str, ...] = ("energy", "turbulence")
"""The physics models a parameter may need (decision 2.7); the models node arrives in M3."""

SWITCHES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {"direction": DIRECTIONS, "turbulence": TURBULENCE_INPUTS, "thermal": THERMALS}
)
"""The enum parameters of ``boundary`` that a ``when`` may name, with their choices. The first
choice is the parameter's default, and :meth:`ParamUse.applies` assumes it when a switch is not
given."""
VECTOR_SIZE = 3
"""Components of the one vector parameter, ``direction_vector``."""
INLET_COLOR = "#d64f4f"
OUTLET_COLOR = "#3b82c4"
"""Red is the inlet family and blue the outlet, as ``web/DESIGN.md`` reserves them; every kind's
colour is a member of the tag palette (``SELECTION_PALETTE``), so no new colour enters the
viewport."""


@dataclass(frozen=True)
class ParamUse:
    """A ``boundary`` parameter that a kind reads.

    ``rule`` checks the parameter's SI value (:func:`check_rule`). ``required`` marks a parameter
    whose default makes no working condition (a velocity or mass flow of 0, three zero velocity
    components, no partner): the value panel asks for it first and ``setup.check`` reports it until
    it is given. Every other parameter's default is a usable value. ``when`` maps a switch (a key of
    :data:`SWITCHES`) to the choices under which the parameter is used; every entry must hold, and
    an empty ``when`` means always.
    """

    name: str
    rule: Rule = "any"
    required: bool = False
    when: Mapping[str, tuple[str, ...]] = MappingProxyType({})

    def __post_init__(self) -> None:
        if self.rule not in RULES:
            raise ConditionError(f"unknown rule {self.rule!r} for {self.name!r}")
        when = {switch: tuple(choices) for switch, choices in self.when.items()}
        for switch, choices in when.items():
            known = SWITCHES.get(switch)
            if known is None or switch == self.name:
                raise ConditionError(f"{self.name!r} depends on {switch!r}, which is not a switch")
            if not choices or not set(choices) <= set(known):
                raise ConditionError(
                    f"{self.name!r} names choices of {switch} it does not have: {choices!r}"
                )
        object.__setattr__(self, "when", MappingProxyType(when))  # frozen: a read-only copy

    def applies(self, values: Mapping[str, Any]) -> bool:  # Any: parameter values as JSON
        """Whether the parameter is in use for these boundary values. A switch missing from
        ``values`` counts as its default."""
        return all(
            values.get(switch, SWITCHES[switch][0]) in choices
            for switch, choices in self.when.items()
        )


@dataclass(frozen=True)
class ConditionSpec:
    """One kind of boundary condition: a row of ADR-0022's kinds table (decision 2.3)."""

    id: str
    label: str
    family: Family
    params: tuple[ParamUse, ...]
    fields: tuple[str, ...]
    """The solver fields whose boundary value the kind fixes. A constraint kind (symmetry, cyclic)
    fixes none: every field takes the constraint. ``setup.check`` reads ``p`` here to tell whether
    a fluid region fixes its pressure."""
    needs: Mapping[str, tuple[str, ...]]
    """Physics model to the parameters that need it: recorded now, judged against the models node
    in M3 (decision 2.7). A parameter here matters only when it is in use."""
    color: str
    """The colour the kind gives its tag (#rrggbb, from the tag palette)."""
    glyph: Glyph
    arrives: str = ""
    """Empty when the kind can be chosen now; otherwise the milestone that brings it."""
    note: str = ""

    def __post_init__(self) -> None:
        if self.family not in FAMILIES:
            raise ConditionError(f"unknown family {self.family!r} for {self.id!r}")
        if self.glyph not in GLYPHS:
            raise ConditionError(f"unknown glyph {self.glyph!r} for {self.id!r}")
        if self.color not in SELECTION_PALETTE:
            raise ConditionError(f"colour of {self.id!r} is not a tag colour: {self.color!r}")
        if not set(self.fields) <= set(FIELDS):
            raise ConditionError(f"unknown fields in {self.id!r}: {self.fields!r}")
        names = self.param_names
        if len(names) != len(set(names)):
            raise ConditionError(f"duplicate parameters in {self.id!r}")
        needs = {physics: tuple(params) for physics, params in self.needs.items()}
        for physics, params in needs.items():
            if physics not in PHYSICS or not params or not set(params) <= set(names):
                raise ConditionError(f"{self.id!r} needs {physics!r} for {params!r}")
        object.__setattr__(self, "needs", MappingProxyType(needs))  # frozen: a read-only copy

    @property
    def available(self) -> bool:
        return not self.arrives

    @property
    def param_names(self) -> tuple[str, ...]:
        """Every parameter the kind may use, whatever its switches hold."""
        return tuple(use.name for use in self.params)

    def uses(self, values: Mapping[str, Any]) -> tuple[ParamUse, ...]:  # Any: values as JSON
        """The parameters the kind uses for these boundary values, in table order: those whose
        ``when`` holds. ``values`` may be partial; a switch it lacks counts as its default."""
        return tuple(use for use in self.params if use.applies(values))

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        """The entry as ``boundary.kinds`` returns it, less the parameters' JSON schemas, which
        the command takes from the ``boundary`` node type."""
        return {
            "id": self.id,
            "label": self.label,
            "family": self.family,
            "fields": list(self.fields),
            "needs": {physics: list(params) for physics, params in self.needs.items()},
            "color": self.color,
            "glyph": self.glyph,
            "arrives": self.arrives or None,
            "available": self.available,
            "note": self.note,
            "params": [
                {
                    "name": use.name,
                    "rule": use.rule,
                    "required": use.required,
                    "when": {switch: list(choices) for switch, choices in use.when.items()},
                }
                for use in self.params
            ],
        }


def _use(
    name: str, rule: Rule = "any", *, required: bool = False, **when: tuple[str, ...]
) -> ParamUse:
    return ParamUse(name, rule, required, MappingProxyType(when))


_TURBULENCE: tuple[ParamUse, ...] = (
    _use("turbulence"),
    _use("intensity", "fraction"),
    _use("viscosity_ratio", "positive", turbulence=("intensity_ratio",)),
    _use("length_scale", "positive", turbulence=("intensity_length",)),
)
"""The turbulence group of the inlets and of the outlet's backflow."""
_TURBULENCE_NAMES = tuple(use.name for use in _TURBULENCE)
_INFLOW_NEEDS = {"energy": ("temperature",), "turbulence": _TURBULENCE_NAMES}
_COMPONENTS = ("components",)

KINDS: tuple[ConditionSpec, ...] = (
    ConditionSpec(
        "velocity_inlet",
        "Velocity inlet",
        "inlet",
        (
            _use("direction"),
            _use("velocity", "positive", required=True, direction=("normal", "vector")),
            _use("velocity_x", required=True, direction=_COMPONENTS),
            _use("velocity_y", required=True, direction=_COMPONENTS),
            _use("velocity_z", required=True, direction=_COMPONENTS),
            _use("direction_vector", "nonzero", direction=("vector",)),
            _use("temperature", "positive"),
            *_TURBULENCE,
        ),
        ("U", "T", "turbulence"),
        _INFLOW_NEEDS,
        INLET_COLOR,
        "arrow_in",
        note="velocity_x, velocity_y and velocity_z may each be zero, but not all three: "
        "setup.check reports three zero components as setup.value_invalid. With direction "
        "normal the velocity points into the region, normal to the faces.",
    ),
    ConditionSpec(
        "mass_flow_inlet",
        "Mass-flow inlet",
        "inlet",
        (
            _use("mass_flow", "positive", required=True),
            _use("temperature", "positive"),
            *_TURBULENCE,
        ),
        ("U", "T", "turbulence"),
        _INFLOW_NEEDS,
        INLET_COLOR,
        "arrow_in",
        note="mass_flow is the total through every face of the tag.",
    ),
    ConditionSpec(
        "pressure_inlet",
        "Pressure inlet",
        "inlet",
        (_use("total_pressure"), _use("temperature", "positive"), *_TURBULENCE),
        ("p", "T", "turbulence"),
        _INFLOW_NEEDS,
        INLET_COLOR,
        "arrow_in",
        note="total_pressure is gauge, relative to the operating pressure.",
    ),
    ConditionSpec(
        "pressure_outlet",
        "Pressure outlet",
        "outlet",
        (_use("pressure"), _use("backflow_temperature", "positive"), *_TURBULENCE),
        ("p", "T", "turbulence"),
        {"energy": ("backflow_temperature",), "turbulence": _TURBULENCE_NAMES},
        OUTLET_COLOR,
        "arrow_out",
        note="pressure is gauge, relative to the operating pressure; backflow_temperature "
        "and the turbulence parameters apply to fluid that flows back in.",
    ),
    ConditionSpec(
        "wall",
        "Wall",
        "wall",
        (
            _use("shear"),
            _use("thermal"),
            _use("temperature", "positive", thermal=("temperature",)),
            _use("heat_flux", thermal=("heat_flux",)),
            _use("heat_transfer_coefficient", "positive", thermal=("convection",)),
            _use("ambient_temperature", "positive", thermal=("convection",)),
        ),
        ("U", "T"),
        {
            "energy": (
                "temperature",
                "heat_flux",
                "heat_transfer_coefficient",
                "ambient_temperature",
            )
        },
        "#7f8c99",
        "none",
        note="A heat flux into the fluid is positive. Faces of a region's bodies that no "
        "boundary tag holds become no-slip walls when the case is compiled.",
    ),
    ConditionSpec("symmetry", "Symmetry", "constraint", (), (), {}, "#a275c9", "plane"),
    ConditionSpec(
        "cyclic",
        "Cyclic",
        "constraint",
        (_use("partner", required=True),),
        (),
        {},
        "#e0803a",
        "pair",
        note="partner is the matching boundary, and the two must name each other "
        "(setup.partner_missing otherwise).",
    ),
    ConditionSpec(
        "interface",
        "Fluid-solid interface",
        "interface",
        (_use("partner", required=True),),
        ("U", "T"),
        {},
        "#5aa452",
        "diamond",
        arrives="a later release",
        note="Arrives with fluid-solid interface detection; partner is the boundary on the "
        "other region's side.",
    ),
)
"""The eight kinds of ADR-0022 decision 2.3, in its order."""

BY_ID: Mapping[str, ConditionSpec] = MappingProxyType({spec.id: spec for spec in KINDS})
KIND_IDS: tuple[str, ...] = tuple(BY_ID)
"""The ids in table order: the choices of the ``boundary`` node's ``kind``."""
if KIND_IDS != CONDITION_KINDS:  # the node type's choices and this table must be one list
    raise ConditionError(
        f"the catalogue's kinds {KIND_IDS!r} differ from boundary.kind's {CONDITION_KINDS!r}"
    )


def kind(kind_id: str) -> ConditionSpec:
    """The catalogue entry, or :class:`InvalidKind` (``boundary.invalid_kind``) for an unknown id.
    A kind that is not available yet is returned too: its ``arrives`` says when."""
    found = BY_ID.get(kind_id)
    if found is None:
        raise InvalidKind(
            f"no condition kind {kind_id!r}; known: {', '.join(KIND_IDS)}",
            details={"kind": kind_id, "known": list(KIND_IDS)},
        )
    return found


_RULE_TEXT: Mapping[str, str] = MappingProxyType(
    {
        "positive": "must be positive",
        "nonnegative": "must not be negative",
        "nonzero": "must not be zero",
        "fraction": "must lie between 0 and 1, both excluded",
    }
)


def _breaks(rule: str, value: float) -> bool:
    if rule == "positive":
        return value <= 0.0
    if rule == "nonnegative":
        return value < 0.0
    if rule == "nonzero":
        return value == 0.0
    if rule == "fraction":
        return not 0.0 < value < 1.0
    return False  # "any"


def check_rule(use: ParamUse, value: float | Sequence[float]) -> str | None:
    """The message when ``value`` (SI) breaks the rule of ``use``, else None.

    Every value must be finite, whatever the rule. A sequence is a vector (``direction_vector``):
    it needs :data:`VECTOR_SIZE` finite numbers and the rule applies to its length. The message
    names the parameter and the rule, ready for ``boundary.invalid_value`` (T-036) and
    ``setup.value_invalid`` (T-037). The cross-parameter rule of the velocity components (not all
    three zero) is the check's; see the velocity inlet's ``note``.
    """
    if isinstance(value, Sequence):
        return _check_vector(use, value)
    if not math.isfinite(value):
        return f"{use.name} must be a finite number; got {value!r}"
    if _breaks(use.rule, value):
        return f"{use.name} {_RULE_TEXT[use.rule]}; got {value:g}"
    return None


def _check_vector(use: ParamUse, value: Sequence[float]) -> str | None:
    if len(value) != VECTOR_SIZE:
        return f"{use.name} needs {VECTOR_SIZE} components; got {len(value)}"
    if not all(math.isfinite(component) for component in value):
        return f"{use.name} must hold finite numbers"
    length = math.hypot(*value)
    if _breaks(use.rule, length):
        return f"the length of {use.name} {_RULE_TEXT[use.rule]}; got {length:g}"
    return None

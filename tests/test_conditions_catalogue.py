"""The condition catalogue (ADR-0022 decisions 2.2 to 2.7; contract "The catalogue" in
docs/engineering/setup-schema.md): the eight kinds against the ``boundary`` node type, the ``when``
switches, the rules, the colours, and ``boundary.kinds``."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from stokes.api import Journal, Session, default_registry
from stokes.errors import StokesError, ValidationError
from stokes.geometry.model import CONDITION_KINDS, DEFAULT_CONDITION, SELECTION_PALETTE, SHEARS
from stokes.model import registry
from stokes.physics.conditions import ConditionError, InvalidKind, InvalidValue, catalogue
from stokes.physics.conditions.catalogue import KINDS, ConditionSpec, ParamUse, check_rule

ADR_KINDS = [  # ADR-0022 decision 2.3: id, label, family, tag colour, glyph
    ("velocity_inlet", "Velocity inlet", "inlet", "#d64f4f", "arrow_in"),
    ("mass_flow_inlet", "Mass-flow inlet", "inlet", "#d64f4f", "arrow_in"),
    ("pressure_inlet", "Pressure inlet", "inlet", "#d64f4f", "arrow_in"),
    ("pressure_outlet", "Pressure outlet", "outlet", "#3b82c4", "arrow_out"),
    ("wall", "Wall", "wall", "#7f8c99", "none"),
    ("symmetry", "Symmetry", "constraint", "#a275c9", "plane"),
    ("cyclic", "Cyclic", "constraint", "#e0803a", "pair"),
    ("interface", "Fluid-solid interface", "interface", "#5aa452", "diamond"),
]
TURBULENCE = ("turbulence", "intensity", "viscosity_ratio", "length_scale")
BOUNDARY = registry.get("boundary")


def names(uses: tuple[ParamUse, ...]) -> list[str]:
    return [use.name for use in uses]


# ---------------------------------------------------------------- the table


def test_the_eight_kinds_of_the_adr_in_its_order() -> None:
    assert [(k.id, k.label, k.family, k.color, k.glyph) for k in KINDS] == ADR_KINDS
    assert list(catalogue.BY_ID) == list(catalogue.KIND_IDS) == [row[0] for row in ADR_KINDS]
    assert all(catalogue.BY_ID[spec.id] is spec for spec in KINDS)
    assert [spec.id for spec in KINDS if not spec.available] == ["interface"]
    # **A release name, not a milestone code** (claude-win2's package-two verdict, leftover 2):
    # "M5" reached a user through the kind's note and through this field, and the user-text
    # gate refuses the codes now - so the value is a phrase a person reads.
    assert catalogue.kind("interface").arrives == "a later release"
    assert DEFAULT_CONDITION == BOUNDARY.param("kind").default == "wall"
    assert BOUNDARY.param("kind").choices == catalogue.KIND_IDS == CONDITION_KINDS


def test_the_parameters_each_kind_uses() -> None:
    """ADR-0022 decision 2.3's "parameters used" column, in the node type's order."""
    used = {spec.id: spec.param_names for spec in KINDS}
    assert used == {
        "velocity_inlet": (
            "direction",
            "velocity",
            "velocity_x",
            "velocity_y",
            "velocity_z",
            "direction_vector",
            "temperature",
            *TURBULENCE,
        ),
        "mass_flow_inlet": ("mass_flow", "temperature", *TURBULENCE),
        "pressure_inlet": ("total_pressure", "temperature", *TURBULENCE),
        "pressure_outlet": ("pressure", "backflow_temperature", *TURBULENCE),
        "wall": (
            "shear",
            "thermal",
            "temperature",
            "heat_flux",
            "heat_transfer_coefficient",
            "ambient_temperature",
        ),
        "symmetry": (),
        "cyclic": ("partner",),
        "interface": ("partner",),
    }


def test_every_parameter_a_kind_uses_exists_on_boundary() -> None:
    params = [p.name for p in BOUNDARY.params]
    for spec in KINDS:
        assert set(spec.param_names) <= set(params), spec.id
        order = [params.index(name) for name in spec.param_names if name != "temperature"]
        assert order == sorted(order), spec.id  # the node type's order, but the wall's temperature
    every = {name for spec in KINDS for name in spec.param_names}
    # `layers` and `mesh_size` are overrides no condition kind uses (briefs M-504 and M-503):
    # neither is part of any kind's parameter list, so both are exempt here like the type's own
    # keys are.
    assert every == set(params) - {"kind", "tag", "layers", "mesh_size"}  # no unused parameter


def test_every_when_names_an_enum_parameter_and_valid_choices() -> None:
    seen: set[str] = set()
    for spec in KINDS:
        for use in spec.params:
            for switch, choices in use.when.items():
                param = BOUNDARY.param(switch)
                assert param.kind == "enum", (spec.id, use.name, switch)
                assert choices and set(choices) <= set(param.choices), (spec.id, use.name)
                assert switch in spec.param_names, (spec.id, switch)  # the kind shows its switch
                seen.add(switch)
    assert seen == set(catalogue.SWITCHES) == {"direction", "turbulence", "thermal"}
    for switch, choices in catalogue.SWITCHES.items():
        param = BOUNDARY.param(switch)
        assert param.choices == choices and param.default == choices[0], switch
    assert BOUNDARY.param("shear").choices == SHEARS == ("no_slip", "slip")


def test_uses_follows_the_switches() -> None:
    inlet = catalogue.kind("velocity_inlet")
    components = names(inlet.uses({"direction": "components"}))
    assert {"velocity_x", "velocity_y", "velocity_z"} <= set(components)
    assert "velocity" not in components and "direction_vector" not in components
    normal = names(inlet.uses({"direction": "normal"}))
    assert "velocity" in normal and "velocity_x" not in normal and "direction_vector" not in normal
    vector = names(inlet.uses({"direction": "vector"}))
    assert {"velocity", "direction_vector"} <= set(vector) and "velocity_y" not in vector
    assert names(inlet.uses({})) == normal  # a switch that is not given counts as its default
    assert "viscosity_ratio" in normal and "length_scale" not in normal
    length = names(inlet.uses({"turbulence": "intensity_length", "direction": "normal"}))
    assert "length_scale" in length and "viscosity_ratio" not in length
    wall = catalogue.kind("wall")
    assert names(wall.uses({"direction": "components"})) == ["shear", "thermal"]
    assert names(wall.uses({"thermal": "temperature"})) == ["shear", "thermal", "temperature"]
    assert names(wall.uses({"thermal": "heat_flux"})) == ["shear", "thermal", "heat_flux"]
    assert names(wall.uses({"thermal": "convection"})) == [
        "shear",
        "thermal",
        "heat_transfer_coefficient",
        "ambient_temperature",
    ]
    assert catalogue.kind("symmetry").uses({"direction": "vector"}) == ()
    assert names(catalogue.kind("cyclic").uses({})) == ["partner"]


def test_needs_name_the_physics_and_fields_name_what_a_kind_fixes() -> None:
    for kind_id in ("velocity_inlet", "mass_flow_inlet", "pressure_inlet"):
        spec = catalogue.kind(kind_id)
        assert dict(spec.needs) == {"energy": ("temperature",), "turbulence": TURBULENCE}
    outlet = catalogue.kind("pressure_outlet")
    assert dict(outlet.needs) == {"energy": ("backflow_temperature",), "turbulence": TURBULENCE}
    wall = catalogue.kind("wall")
    assert set(wall.needs) == {"energy"}
    # "thermal is not adiabatic: energy" (decision 2.3): an adiabatic wall uses nothing that
    # needs the energy equation, every other thermal choice does.
    assert not set(names(wall.uses({}))) & set(wall.needs["energy"])
    for thermal in ("temperature", "heat_flux", "convection"):
        assert set(names(wall.uses({"thermal": thermal}))) & set(wall.needs["energy"]), thermal
    for spec in KINDS:
        assert set(spec.needs) <= set(catalogue.PHYSICS), spec.id
        assert set(spec.fields) <= set(catalogue.FIELDS), spec.id
    assert [k.id for k in KINDS if "p" in k.fields] == ["pressure_inlet", "pressure_outlet"]
    assert catalogue.kind("symmetry").fields == catalogue.kind("cyclic").fields == ()


def test_colours_come_from_the_tag_palette() -> None:
    for spec in KINDS:
        assert spec.color in SELECTION_PALETTE, spec.id
    assert {k.color for k in KINDS if k.family == "inlet"} == {"#d64f4f"}
    assert {k.color for k in KINDS if k.family == "outlet"} == {"#3b82c4"}
    others = {k.color for k in KINDS if k.family not in ("inlet", "outlet")}
    assert not others & {"#d64f4f", "#3b82c4"}  # red and blue stay the inlet's and the outlet's
    assert catalogue.INLET_COLOR == "#d64f4f" and catalogue.OUTLET_COLOR == "#3b82c4"
    assert set(catalogue.GLYPHS) == {k.glyph for k in KINDS}


def test_required_marks_the_parameters_whose_default_makes_no_condition() -> None:
    """Required: the default does not work (a velocity of 0, no partner); every other default
    passes its rule as it is."""
    for spec in KINDS:
        for use in spec.params:
            param = BOUNDARY.param(use.name)
            if param.kind == "ref":  # partner: null until set
                assert use.required and param.default is None, spec.id
            elif use.name in ("velocity_x", "velocity_y", "velocity_z"):
                assert use.required and param.default == 0.0  # all three zero: no velocity
            elif param.kind == "enum":
                assert not use.required, (spec.id, use.name)
            else:
                broken = check_rule(use, param.default) is not None
                assert use.required == broken, (spec.id, use.name)
    required = {(k.id, u.name) for k in KINDS for u in k.params if u.required}
    assert {name for _, name in required} == {
        "velocity",
        "velocity_x",
        "velocity_y",
        "velocity_z",
        "mass_flow",
        "partner",
    }


def test_the_velocity_inlet_note_names_the_rule_of_the_components() -> None:
    note = catalogue.kind("velocity_inlet").note
    assert "not all three" in note and "setup.value_invalid" in note
    components = [
        u for u in catalogue.kind("velocity_inlet").params if u.name.startswith("velocity_")
    ]
    assert [u.rule for u in components] == ["any", "any", "any"]


def test_the_api_registers_boundary_without_loading_the_catalogue() -> None:
    """The node type takes its vocabulary from stokes.geometry.model (decision 2.1); the
    catalogue loads with the command modules, so import stokes.api stays as light as before."""
    code = (
        "import sys, stokes.api; "
        "from stokes.model import registry; "
        "assert registry.get('boundary').param('kind').default == 'wall'; "
        "print(sorted(m for m in sys.modules if m.startswith('stokes.physics.conditions')))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False, timeout=180
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "[]"


# ---------------------------------------------------------------- lookups and rules


def test_kind_refuses_an_unknown_id() -> None:
    assert catalogue.kind("wall") is KINDS[4]
    with pytest.raises(InvalidKind) as caught:
        catalogue.kind("fan")
    assert caught.value.code == "boundary.invalid_kind"
    assert caught.value.details["kind"] == "fan"
    assert "velocity_inlet" in caught.value.message
    assert isinstance(caught.value, ConditionError)
    assert issubclass(ConditionError, ValidationError) and issubclass(ConditionError, StokesError)
    assert InvalidValue("x").code == "boundary.invalid_value"
    assert ConditionError("x").code == "boundary.error"


def rule(name: str) -> ParamUse:
    return ParamUse("x", name)  # type: ignore[arg-type]


def test_check_rule_applies_each_rule() -> None:
    assert check_rule(rule("any"), -5.0) is None
    assert check_rule(rule("positive"), 1e-9) is None
    assert check_rule(rule("positive"), 0.0) == "x must be positive; got 0"
    assert check_rule(rule("positive"), -2.5) == "x must be positive; got -2.5"
    assert check_rule(rule("nonnegative"), 0.0) is None
    assert check_rule(rule("nonnegative"), -1.0) == "x must not be negative; got -1"
    assert check_rule(rule("nonzero"), -1.0) is None
    assert check_rule(rule("nonzero"), 0.0) == "x must not be zero; got 0"
    assert check_rule(rule("fraction"), 0.05) is None
    assert check_rule(rule("fraction"), 0.999) is None
    for bad in (0.0, 1.0, 1.5, -0.1):
        message = check_rule(rule("fraction"), bad)
        assert message is not None and "between 0 and 1" in message, bad
    for name in catalogue.RULES:
        for value in (math.inf, -math.inf, math.nan):
            message = check_rule(rule(name), value)
            assert message is not None and "finite" in message, (name, value)
    assert set(catalogue.RULES) == {"any", "positive", "nonnegative", "nonzero", "fraction"}


def test_check_rule_judges_a_vector_by_its_length() -> None:
    vector = next(
        u for u in catalogue.kind("velocity_inlet").params if u.name == "direction_vector"
    )
    assert vector.rule == "nonzero"
    assert check_rule(vector, (0.0, 0.0, 2.0)) is None
    assert check_rule(vector, [0.0, -1e-3, 0.0]) is None
    message = check_rule(vector, [0.0, 0.0, 0.0])
    assert message == "the length of direction_vector must not be zero; got 0"
    message = check_rule(vector, (1.0, 0.0))
    assert message is not None and "3 components" in message
    message = check_rule(vector, (1.0, math.nan, 0.0))
    assert message is not None and "finite" in message


def test_the_messages_name_the_parameter_and_the_rule() -> None:
    inlet = catalogue.kind("velocity_inlet")
    velocity, intensity = (
        next(u for u in inlet.params if u.name == n) for n in ("velocity", "intensity")
    )
    assert check_rule(velocity, 0.0) == "velocity must be positive; got 0"
    assert (
        check_rule(intensity, 1.5) == "intensity must lie between 0 and 1, both excluded; got 1.5"
    )


def test_definitions_are_checked_when_they_are_built() -> None:
    with pytest.raises(ConditionError):
        rule("sideways")
    with pytest.raises(ConditionError):  # shear switches nothing
        ParamUse("x", when={"shear": ("slip",)})
    with pytest.raises(ConditionError):
        ParamUse("x", when={"direction": ("diagonal",)})
    with pytest.raises(ConditionError):
        ParamUse("x", when={"direction": ()})
    with pytest.raises(ConditionError):  # a switch cannot depend on itself
        ParamUse("direction", when={"direction": ("normal",)})
    good: dict[str, Any] = {
        "id": "k",
        "label": "K",
        "family": "wall",
        "params": (ParamUse("shear"), ParamUse("heat_flux")),
        "fields": ("U",),
        "needs": {"energy": ("heat_flux",)},
        "color": "#7f8c99",
        "glyph": "none",
    }
    ConditionSpec(**good)
    for change in (
        {"family": "fan"},
        {"glyph": "star"},
        {"color": "red"},
        {"color": "#D64F4F"},
        {"color": "#123456"},  # well formed, but not a tag colour
        {"fields": ("rho",)},
        {"needs": {"energy": ("temperature",)}},  # a parameter the kind does not use
        {"needs": {"radiation": ("heat_flux",)}},
        {"needs": {"energy": ()}},
        {"params": (ParamUse("shear"), ParamUse("shear"))},
    ):
        with pytest.raises(ConditionError):
            ConditionSpec(**{**good, **change})


def test_the_catalogue_cannot_be_changed_in_place() -> None:
    use = ParamUse("x", when={"direction": ["vector"]})  # type: ignore[dict-item]
    assert use.when == {"direction": ("vector",)}
    with pytest.raises(TypeError):
        use.when["direction"] = ("normal",)  # type: ignore[index]
    with pytest.raises(TypeError):
        catalogue.kind("wall").needs["energy"] = ()  # type: ignore[index]
    with pytest.raises(TypeError):
        catalogue.BY_ID["fan"] = KINDS[0]  # type: ignore[index]
    with pytest.raises(TypeError):
        catalogue.SWITCHES["shear"] = SHEARS  # type: ignore[index]


# ---------------------------------------------------------------- boundary.kinds


def test_boundary_kinds_lists_the_catalogue_without_a_project(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    session = Session(journal_path=journal)
    kinds = session.execute("boundary.kinds")
    assert [k["id"] for k in kinds] == list(catalogue.KIND_IDS)
    inlet = kinds[0]
    assert (inlet["label"], inlet["family"], inlet["color"], inlet["glyph"]) == (
        "Velocity inlet",
        "inlet",
        "#d64f4f",
        "arrow_in",
    )
    assert inlet["fields"] == ["U", "T", "turbulence"]
    assert inlet["needs"] == {"energy": ["temperature"], "turbulence": list(TURBULENCE)}
    assert inlet["available"] is True and inlet["arrives"] is None
    assert "not all three" in inlet["note"]
    params = {p["name"]: p for p in inlet["params"]}
    assert list(params) == list(catalogue.kind("velocity_inlet").param_names)
    velocity = params["velocity"]
    assert set(velocity) == {"name", "rule", "required", "when", "schema"}
    assert (velocity["rule"], velocity["required"]) == ("positive", True)
    assert velocity["when"] == {"direction": ["normal", "vector"]}
    assert velocity["schema"] == BOUNDARY.param("velocity").json_schema()
    assert velocity["schema"]["x-dimension"] == "velocity"
    assert params["direction"]["when"] == {}
    assert params["direction"]["schema"]["enum"] == ["normal", "components", "vector"]
    assert params["intensity"]["schema"]["default"] == 0.05
    interface = kinds[-1]
    assert interface["available"] is False and interface["arrives"] == "a later release"
    assert kinds[5]["params"] == [] and kinds[5]["glyph"] == "plane"  # symmetry
    assert all(k["supported"] and k["unsupported_reason"] is None for k in kinds)  # no engine
    assert json.loads(json.dumps(kinds))[0]["id"] == "velocity_inlet"
    assert not journal.exists()  # a query: nothing is journaled


def test_boundary_kinds_is_a_query_in_the_command_schema(session: Session, tmp_path: Path) -> None:
    schema = {c["name"]: c for c in default_registry().json_schema()}
    entry = schema["boundary.kinds"]
    assert entry["mutates"] is False and entry["requires_project"] is False
    assert entry["description"]
    assert entry["input_schema"]["required"] == []
    assert set(entry["input_schema"]["properties"]) == {"engine"}
    session.execute("boundary.kinds")  # with a project open, still nothing in the journal
    records = Journal.read(tmp_path / "journal.jsonl")
    assert [r["command"] for r in records] == ["project.new"]

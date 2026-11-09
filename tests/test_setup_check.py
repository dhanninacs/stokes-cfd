"""``setup.check`` end to end (ADR-0022 decisions 5.1 to 5.4, 2.7, 2.10; contract:
docs/engineering/setup-schema.md, Checks): the four readiness states, every code of the ADR
table with its severity and fix, the counts, the sorting, the purity of ``check_setup`` and the
command's silence (no event, no journal, no undo step). Synthetic cubes, no OCP."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from stokes.api import Session
from stokes.geometry.files import SourceStamp
from stokes.physics.conditions.checks import CODES, COMPLETE, STATES, check_setup
from tests.selection_support import (
    STAMP,
    add_part,
    cube,
    events_of,
    new_session,
    part,
    permuted,
    put_mesh,
)

FLUID = "/regions/fluid"
BOUNDARIES = f"{FLUID}/boundaries"
TAGS = "/geometry/tags"
DOC = Path(__file__).resolve().parents[1] / "docs" / "engineering" / "setup-schema.md"
KEYS = ("regions", "materials", "boundaries", "uncovered_faces", "legacy")

SEVERITY_OF: dict[str, str] = {
    "setup.no_region": "error",
    "setup.no_material": "error",
    "setup.material_kind": "error",
    "setup.no_inlet": "error",
    "setup.no_outlet": "error",
    "setup.no_pressure": "warning",
    "setup.value_invalid": "error",
    "setup.tag_missing": "error",
    "setup.empty_tag": "warning",
    "setup.stale_tag": "warning",
    "setup.overlap": "warning",
    "setup.uncovered_faces": "info",
    "setup.partner_missing": "error",
    "setup.needs_physics": "info",
    "setup.legacy": "warning",
    "setup.empty_region": "warning",
    "setup.excluded_body": "warning",
    "setup.no_inlet_temperature": "warning",
    "setup.kind_unsupported": "error",
}
"""The ADR table's rows: code to severity, in the order the table lists them."""

COVERS: dict[str, str] = {
    "setup.no_region": "test_an_empty_project_is_not_defined",
    "setup.no_material": "test_a_region_with_bodies_and_no_material_is_incomplete",
    "setup.material_kind": "test_a_material_the_regions_kind_contradicts",
    "setup.no_inlet": "test_a_fluid_region_with_only_a_wall",
    "setup.no_outlet": "test_a_fluid_region_with_only_a_wall",
    "setup.no_pressure": "test_a_fluid_region_with_only_a_wall",
    "setup.value_invalid": "test_a_velocity_that_is_not_a_velocity",
    "setup.tag_missing": "test_a_boundary_whose_tag_was_removed_by_hand",
    "setup.empty_tag": "test_a_tag_that_holds_nothing",
    "setup.stale_tag": "test_a_tag_whose_part_was_read_again",
    "setup.overlap": "test_a_face_in_two_boundary_tags",
    "setup.uncovered_faces": "test_uncovered_faces_on_a_box_with_one_face_tagged",
    "setup.partner_missing": "test_a_cyclic_boundary_without_a_partner",
    "setup.needs_physics": "test_a_complete_set_up_is_ready",
    "setup.legacy": "test_plain_groups_from_before_adr_0022",
    "setup.empty_region": "test_a_region_with_a_material_but_no_bodies",
    "setup.no_inlet_temperature": "test_an_inlet_at_default_temperature_is_named",
    "setup.kind_unsupported": "test_a_kind_the_solver_cannot_write_is_an_error",
}
"""The test that makes each code of the ADR table appear."""


# ---------------------------------------------------------------- helpers


def check(session: Session) -> dict[str, Any]:  # Any: JSON
    return session.execute("setup.check")


def codes(result: dict[str, Any]) -> list[str]:  # Any: JSON
    return [str(one["code"]) for one in result["findings"]]


def findings(result: dict[str, Any], code: str) -> list[dict[str, Any]]:  # Any: JSON
    """Every finding with ``code``, each with the severity the ADR table gives it."""
    found = [one for one in result["findings"] if one["code"] == code]
    assert found, f"no {code} in {codes(result)}"
    assert {one["severity"] for one in found} == {SEVERITY_OF[code]}
    return found


def finding(result: dict[str, Any], code: str) -> dict[str, Any]:  # Any: JSON
    """The one finding with ``code``, for the codes a project has once."""
    found = findings(result, code)
    assert len(found) == 1, f"expected one {code}, got {codes(result)}"
    return found[0]


def pick(part_ref: str, *faces: int) -> list[dict[str, Any]]:
    """The live pick of faces of one part, in the tag commands' input form."""
    return [{"part": part_ref, "faces": list(faces)}]


def cube_part(session: Session, name: str = "a", at: tuple[float, float, float] = (0, 0, 0)) -> str:
    """One meshed six-face cube, added as a ``part`` node."""
    return add_part(session, name, part(cube(0.01, at, name=name)))


def fluid(session: Session, *bodies: str) -> None:
    """The ``fluid`` region with its bodies and ``water`` as its material."""
    if session.require_project().find("/physics/materials/water") is None:
        session.execute("material.create", name="water")
    session.execute("region.create", name="fluid", bodies=list(bodies), material="water")


def complete(session: Session, body: str) -> None:
    """The complete set-up of the brief: an inlet, an outlet and walls over the rest."""
    session.execute(
        "boundary.assign",
        target=pick(body, 0),
        kind="velocity_inlet",
        region="fluid",
        values={"velocity": "2 [m/s]"},
    )
    session.execute("boundary.assign", target=pick(body, 1), kind="pressure_outlet", region="fluid")
    session.execute("boundary.assign", target=pick(body, 2, 3, 4, 5), kind="wall", region="fluid")


def boundary_id(session: Session, name: str) -> str:
    return str(session.require_project().get(f"{BOUNDARIES}/{name}").id)


def tag_of(session: Session, boundary: str) -> str:
    node = session.require_project().get(f"{BOUNDARIES}/{boundary}")
    return str(node.params["tag"])


# ---------------------------------------------------------------- the four states


def test_an_empty_project_is_not_defined(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    result = check(session)
    assert result["state"] == "not_defined" and result["state"] in STATES
    assert codes(result) == ["setup.no_region"]
    missing = finding(result, "setup.no_region")
    assert result["next"] == missing["suggestion"]
    assert "Material" in result["next"]
    assert missing["fix"] == {"action": "material.assign"} and missing["nodes"] == []
    assert result["counts"] == dict.fromkeys(KEYS, 0)


def test_a_region_with_bodies_and_no_material_is_incomplete(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    body = cube_part(session)
    session.execute("region.create", name="fluid", bodies=[body])
    result = check(session)
    assert result["state"] == "incomplete"
    missing = finding(result, "setup.no_material")
    assert "no material" in missing["message"] and missing["nodes"]
    assert result["next"] == missing["suggestion"] and "Material" in result["next"]
    assert result["counts"] == {
        "regions": 1,
        "materials": 0,
        "boundaries": 0,
        "uncovered_faces": 6,
        "legacy": 0,
    }


def test_an_inlet_at_default_temperature_is_named(tmp_path: Path) -> None:
    # **The silent 300 K is named once energy is on**, with the inlet's own name (ADR-0027).
    session = new_session(tmp_path)
    body = cube_part(session)
    fluid(session, body)
    complete(session, body)
    session.execute("solve.configure", engine="of14.fluid", values={"energy": True})
    named = findings(check(session), "setup.no_inlet_temperature")
    assert [one["nodes"][0] for one in named] == [boundary_id(session, "inlet")]
    assert all(one["severity"] == "warning" for one in named)
    assert "300 K" in named[0]["message"] and "inlet" in named[0]["message"]


def test_a_kind_the_solver_cannot_write_is_an_error(tmp_path: Path) -> None:
    # **Said before the solve**: the writer has no pressure inlet, so the set-up is incomplete
    # now rather than refused by `solve.unsupported_kind` at run time.
    session = new_session(tmp_path)
    body = cube_part(session)
    fluid(session, body)
    complete(session, body)
    assert "setup.kind_unsupported" not in codes(check(session))
    session.execute("boundary.set", boundary=boundary_id(session, "inlet"), kind="pressure_inlet")
    result = check(session)
    named = findings(result, "setup.kind_unsupported")
    assert [one["nodes"][0] for one in named] == [boundary_id(session, "inlet")]
    assert "pressure inlet" in named[0]["message"] and "inlet" in named[0]["message"]
    assert result["state"] == "incomplete"


def test_the_energy_row_goes_once_the_solver_solves_energy(tmp_path: Path) -> None:
    # **The solver setting, not the boundary, decides**: a case that solves energy no longer
    # asks the user to choose the energy equation in Physics (ADR-0027 decision 2).
    session = new_session(tmp_path)
    body = cube_part(session)
    fluid(session, body)
    complete(session, body)
    assert codes(check(session)).count("setup.needs_physics") == 2
    session.execute("solve.configure", engine="of14.fluid", values={"energy": True})
    result = check(session)
    # The turbulence rows remain; only the energy sentence is gone.
    texts = [one["message"] for one in findings(result, "setup.needs_physics")]
    assert texts and all("energy equation" not in text for text in texts)
    assert all("turbulence models" in text for text in texts)
    assert result["state"] == "ready"


def test_the_turbulence_row_goes_once_a_turbulence_model_is_chosen(tmp_path: Path) -> None:
    # Choosing k-omega SST in the Flow model must silence "choose the turbulence models in
    # Physics" on every inlet and outlet; it said so after the model was chosen, which read as
    # turbulence not being connected (ADR-0028).
    session = new_session(tmp_path)
    body = cube_part(session)
    fluid(session, body)
    complete(session, body)
    session.execute("solve.configure", values={"turbulence": "kOmegaSST"})
    texts = [one["message"] for one in findings(check(session), "setup.needs_physics")]
    assert texts and all("turbulence models" not in text for text in texts)
    session.execute("solve.configure", engine="of14.fluid", values={"energy": True})
    assert "setup.needs_physics" not in codes(check(session))
    session.execute("solve.configure", values={"turbulence": "laminar"})
    texts = [one["message"] for one in findings(check(session), "setup.needs_physics")]
    assert texts and all("turbulence models" in text for text in texts)


def test_a_complete_set_up_is_ready(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    body = cube_part(session)
    fluid(session, body)
    complete(session, body)
    result = check(session)
    assert result["state"] == "ready" and result["next"] == COMPLETE
    assert [one["severity"] for one in result["findings"]] == ["info", "info"]
    assert codes(result) == ["setup.needs_physics", "setup.needs_physics"]
    physics = findings(result, "setup.needs_physics")
    assert [one["nodes"][0] for one in physics] == [
        boundary_id(session, "inlet"),
        boundary_id(session, "outlet"),
    ]
    # **No milestone code and no raw path** (claude-win2's package-two verdict, leftover 2): the
    # suggestion read "set the physics models in M3", and the message opened with
    # "/regions/fluid/boundaries/inlet". Both were fixed, so this pins the new wording - and the
    # user-text gate refuses the codes in any case.
    assert {one["suggestion"] for one in physics} == {"choose the flow model in the Physics stage"}
    assert all("/regions/" not in one["message"] for one in physics), physics[0]["message"]
    assert {one["fix"] for one in physics} == {None}
    assert "the energy equation and the turbulence models" in physics[0]["message"]
    assert result["counts"] == {
        "regions": 1,
        "materials": 1,
        "boundaries": 3,
        "uncovered_faces": 0,
        "legacy": 0,
    }
    json.dumps(result, allow_nan=False)


# ---------------------------------------------------------------- every code


def test_a_boundary_whose_tag_was_removed_by_hand(tmp_path: Path) -> None:
    """The tree built with ``node.add`` and its tag pulled away by hand: ``stale``."""
    session = new_session(tmp_path)
    body = cube_part(session)
    other = cube_part(session, "b", (0.02, 0.0, 0.0))
    fluid(session, body)
    complete(session, body)
    loose = session.execute("tag.create", name="loose", members=pick(other, 3))
    session.execute(
        "node.add",
        parent=BOUNDARIES,
        type="boundary",
        name="loose",
        params={"kind": "wall", "tag": loose["id"]},
    )
    project = session.require_project()
    project.remove(str(loose["id"]))  # a hand edit: node.remove would refuse it
    result = check(session)
    gone = finding(result, "setup.tag_missing")
    assert result["state"] == "stale" and codes(result).count("setup.tag_missing") == 1
    assert gone["nodes"] == [boundary_id(session, "loose")]
    assert "gone" in gone["message"] and gone["suggestion"]
    assert gone["fix"] == {"action": "condition.edit", "node": gone["nodes"][0]}
    assert result["next"] == gone["suggestion"]


def test_a_region_whose_tag_was_removed_by_hand(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    body = cube_part(session)
    sink = cube_part(session, "b", (0.02, 0.0, 0.0))
    fluid(session, body)
    complete(session, body)
    session.execute("material.create", name="alu", kind="solid")
    session.execute("region.create", name="block", bodies=[sink], material="alu", kind="solid")
    project = session.require_project()
    project.remove(str(project.get("/regions/block").params["tag"]))
    result = check(session)
    assert result["state"] == "stale"
    assert "/regions/block" in finding(result, "setup.tag_missing")["message"]
    assert "setup.empty_region" not in codes(result)  # the missing tag says it already
    assert result["counts"] == {
        "regions": 2,
        "materials": 2,
        "boundaries": 3,
        "uncovered_faces": 0,
        "legacy": 0,
    }


def test_a_tag_that_holds_nothing(tmp_path: Path) -> None:
    """Both rows the table gives ``setup.empty_tag``: a boundary's tag and a region's tag."""
    session = new_session(tmp_path)
    body = cube_part(session)
    session.execute("region.create", name="fluid", bodies=[body])  # no material yet
    session.execute("tag.create", name="hole")  # a tag with no faces at all
    session.execute("boundary.assign", target="hole", kind="wall", region="fluid")
    one = finding(check(session), "setup.empty_tag")
    assert "the tag of" in one["message"] and "no faces" in one["message"]
    assert one["fix"] == {"action": "condition.edit", "node": one["nodes"][0]}
    session.execute("region.set", region="fluid", members=[])
    both = findings(check(session), "setup.empty_tag")
    region = [found for found in both if "holds no bodies" in found["message"]]
    assert len(both) == 2 and len(region) == 1
    assert region[0]["fix"] is None
    assert region[0]["nodes"] == [session.require_project().get(FLUID).id]


def test_a_region_with_a_material_but_no_bodies(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    session.execute("material.create", name="water")
    session.execute("region.create", name="fluid", material="water")
    result = check(session)
    empty = finding(result, "setup.empty_region")
    assert "has a material but no bodies" in empty["message"] and empty["suggestion"]
    assert "region.set" in empty["suggestion"]
    assert result["state"] == "not_defined"
    assert result["counts"] == {
        "regions": 1,
        "materials": 1,
        "boundaries": 0,
        "uncovered_faces": 0,
        "legacy": 0,
    }


def test_a_material_the_regions_kind_contradicts(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    body = cube_part(session)
    fluid(session, body)
    complete(session, body)
    session.execute("parameter.set", node=FLUID, name="kind", value="solid")
    result = check(session)
    mismatch = finding(result, "setup.material_kind")
    assert result["state"] == "incomplete" and result["next"] == mismatch["suggestion"]
    assert mismatch["message"] == ("/regions/fluid is solid, but /physics/materials/water is fluid")
    assert mismatch["fix"] == {
        "command": "region.set",
        "params": {"region": session.require_project().get(FLUID).id, "kind": "fluid"},
    }


def test_a_fluid_region_with_only_a_wall(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    body = cube_part(session)
    fluid(session, body)
    session.execute("boundary.assign", target=pick(body, 0), kind="wall", region="fluid")
    result = check(session)
    assert result["state"] == "incomplete"
    inlet = finding(result, "setup.no_inlet")
    outlet = finding(result, "setup.no_outlet")
    pressure = finding(result, "setup.no_pressure")
    assert result["next"] == inlet["suggestion"] == "add an inlet boundary"
    assert outlet["suggestion"] == "add an outlet boundary"
    assert pressure["suggestion"] == "add a pressure outlet"
    assert inlet["fix"] == outlet["fix"] == {"action": "condition.add"}
    assert pressure["fix"] == {"action": "condition.add"}
    assert "no boundary of /regions/fluid fixes the pressure" in pressure["message"]


def test_a_velocity_that_is_not_a_velocity(tmp_path: Path) -> None:
    """A value that is not positive, and three zero components, both on their SI value."""
    session = new_session(tmp_path)
    body = cube_part(session)
    fluid(session, body)
    session.execute("boundary.assign", target=pick(body, 0), kind="velocity_inlet", region="fluid")
    speed = finding(check(session), "setup.value_invalid")
    assert speed["message"] == ("/regions/fluid/boundaries/inlet: velocity must be positive; got 0")
    assert speed["fix"] == {"action": "condition.edit", "node": speed["nodes"][0]}
    session.execute(
        "boundary.set",
        boundary=f"{BOUNDARIES}/inlet",
        values={"direction": "components", "velocity_x": 0},
    )
    components = finding(check(session), "setup.value_invalid")
    assert components["message"].endswith("velocity_x and velocity_y and velocity_z are all zero")
    assert "inlet" in components["message"]


def test_a_face_in_two_boundary_tags(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    body = cube_part(session)
    fluid(session, body)
    session.execute("boundary.assign", target=pick(body, 0), kind="velocity_inlet", region="fluid")
    session.execute(
        "boundary.assign", target=pick(body, 0), kind="wall", name="second", region="fluid"
    )
    result = check(session)
    overlap = finding(result, "setup.overlap")
    assert overlap["message"] == "inlet and second share 1 face of /geometry/parts/a"
    assert overlap["suggestion"] == "edit the tags so that every face is in one tag"
    assert overlap["fix"] is None
    assert set(overlap["nodes"]) == {
        boundary_id(session, "inlet"),
        boundary_id(session, "second"),
    }


def test_uncovered_faces_on_a_box_with_one_face_tagged(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    body = cube_part(session)
    fluid(session, body)
    session.execute("boundary.assign", target=pick(body, 0), kind="wall", region="fluid")
    result = check(session)
    assert result["counts"]["uncovered_faces"] == 5
    uncovered = finding(result, "setup.uncovered_faces")
    assert uncovered["message"] == "5 faces of /regions/fluid are in no boundary tag"
    assert uncovered["fix"] == {"action": "condition.add"}
    assert uncovered["suggestion"].startswith("pick them and add a wall")
    session.execute("boundary.assign", target=pick(body, 1, 2, 3, 4, 5), kind="wall", name="rest")
    after = check(session)
    assert after["counts"]["uncovered_faces"] == 0
    assert "setup.uncovered_faces" not in codes(after)


def test_uncovered_faces_are_unknown_without_a_face_count(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    body = cube_part(session)
    unknown = add_part(session, "c", None)  # a part node with no mesh, so no face count
    fluid(session, body, unknown)
    session.execute("boundary.assign", target=pick(body, 0), kind="wall", region="fluid")
    assert check(session)["counts"]["uncovered_faces"] is None


def test_a_cyclic_boundary_without_a_partner(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    body = cube_part(session)
    fluid(session, body)
    session.execute("boundary.assign", target=pick(body, 0), kind="cyclic", region="fluid")
    session.execute(
        "boundary.assign", target=pick(body, 1), kind="cyclic", name="other", region="fluid"
    )
    project = session.require_project()
    here, there = boundary_id(session, "cyclic"), boundary_id(session, "other")
    result = check(session)
    assert len(findings(result, "setup.partner_missing")) == 2
    project.set_param(here, "partner", there)  # one names the other, which does not name it back
    assert len(findings(check(session), "setup.partner_missing")) == 2
    project.set_param(there, "partner", here)
    assert "setup.partner_missing" not in codes(check(session))
    project.set_param(there, "partner", None)
    alone = [
        one for one in findings(check(session), "setup.partner_missing") if one["nodes"] == [here]
    ]
    assert len(alone) == 1 and alone[0]["fix"] == {"action": "condition.edit", "node": here}


def test_plain_groups_from_before_adr_0022(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    body = cube_part(session)
    fluid(session, body)
    session.execute("node.add", parent="/regions", type="group", name="old")
    session.execute("node.add", parent=BOUNDARIES, type="group", name="older")
    result = check(session)
    assert result["counts"]["legacy"] == 2
    legacy = findings(result, "setup.legacy")
    assert [one["message"] for one in legacy] == [
        f"{BOUNDARIES}/older is a plain group from an older project",
        "/regions/old is a plain group from an older project",
    ]
    assert "boundary.assign" in legacy[0]["suggestion"]
    assert "region.create" in legacy[1]["suggestion"]


def test_a_tag_whose_part_was_read_again(tmp_path: Path) -> None:
    """Staleness the session knows (decision 5.1): the same faces at a newer source."""
    session = new_session(tmp_path)
    body = cube_part(session)
    fluid(session, body)
    complete(session, body)
    assert "setup.stale_tag" not in codes(check(session))  # nothing is stale yet
    newer = SourceStamp(STAMP.name, STAMP.size + 1, STAMP.mtime_ns + 1)
    put_mesh(session, body, part(permuted(cube(0.01, name="a"), [3, 0, 5, 1, 2, 4]), source=newer))
    result = check(session)
    stale = findings(result, "setup.stale_tag")
    assert result["state"] == "stale" and len(stale) >= 3
    assert all(one["suggestion"] == "run tag.rematch" for one in stale)
    assert result["next"] == "run tag.rematch"
    inlet = [one for one in stale if one["nodes"] == [boundary_id(session, "inlet")]]
    assert len(inlet) == 1
    assert inlet[0]["fix"] == {
        "command": "tag.rematch",
        "params": {"tag": tag_of(session, "inlet")},
    }


def test_the_staleness_is_a_callers_argument(tmp_path: Path) -> None:
    """``check_setup`` is pure: staleness arrives as an argument, so it is judged either way."""
    session = new_session(tmp_path)
    body = cube_part(session)
    fluid(session, body)
    session.execute("boundary.assign", target=pick(body, 0), kind="wall", region="fluid")
    project = session.require_project()
    tag = tag_of(session, "wall")
    assert "setup.stale_tag" not in codes(check_setup(project, set()).to_json())
    marked = check_setup(project, {tag}).to_json()
    stale = findings(marked, "setup.stale_tag")
    assert stale[0]["fix"] == {"command": "tag.rematch", "params": {"tag": tag}}
    assert marked["state"] == "incomplete"  # a wall alone is no inlet either


# ---------------------------------------------------------------- shape, sorting, silence


def test_the_result_is_json_with_the_keys_of_the_contract(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    body = cube_part(session)
    fluid(session, body)
    complete(session, body)
    result = check(session)
    assert list(result) == ["state", "next", "counts", "findings"]
    assert list(result["counts"]) == list(KEYS)
    assert all(
        set(one) == {"code", "severity", "message", "suggestion", "nodes", "fix"}
        for one in result["findings"]
    )
    assert all(isinstance(one["message"], str) and one["message"] for one in result["findings"])
    json.dumps(result, allow_nan=False)  # no id, path or fix may need a custom encoder


def test_findings_are_sorted_errors_warnings_info_then_tree_order(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    first = cube_part(session)
    second = cube_part(session, "b", (0.02, 0.0, 0.0))
    fluid(session, first)
    session.execute("node.add", parent="/regions", type="group", name="old")
    session.execute("boundary.assign", target=pick(second, 0), kind="wall", region="fluid")
    result = check(session)
    assert {one["severity"] for one in result["findings"]} == {"error", "warning", "info"}
    rank = {"error": 0, "warning": 1, "info": 2}
    order = {node.id: index for index, node in enumerate(session.require_project().nodes())}
    places = [
        (
            rank[str(one["severity"])],
            -1 if not one["nodes"] else order.get(one["nodes"][0], len(order)),
        )
        for one in result["findings"]
    ]
    assert places == sorted(places)


def test_the_command_emits_nothing_and_journals_nothing(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    seen = events_of(session)
    journal = tmp_path / "demo.jsonl"
    before = journal.read_text(encoding="utf-8") if journal.exists() else ""
    state = session.execute("history.state")
    result = check(session)
    assert result["state"] == "not_defined"
    assert seen == [], f"setup.check emitted {seen}"
    assert (journal.read_text(encoding="utf-8") if journal.exists() else "") == before
    assert session.execute("history.state") == state  # no undo step
    assert session.registry.get("setup.check").mutates is False


# ---------------------------------------------------------------- the table itself


def _documented() -> list[tuple[str, str]]:
    """The rows of the Checks table of the contract: ``(code, severity)``."""
    text = DOC.read_text(encoding="utf-8")
    section = text.split("## Checks (T-037", 1)[1].split("\n## ", 1)[0]
    return [
        (cells[0].strip("`"), cells[1])
        for line in section.splitlines()
        if line.startswith("| `setup.")
        for cells in [[cell.strip() for cell in line.strip("|").split("|")]]
    ]


def test_the_documented_table_is_the_code_table() -> None:
    # **Sixteen from the ADR's table, plus what was added since** (T-221's excluded body). The count
    # is
    # written here rather than derived, so a new code is a deliberate edit to this line - which is
    # the
    # point of the test: `report.add` DROPS a code it does not know, so an unregistered one is a
    # finding nobody sees rather than an error.
    assert tuple(SEVERITY_OF) == CODES
    assert len(CODES) == 19
    # **The ADR's table, which this test verifies word for word**, still lists sixteen - and the
    # comparison is against the SEVERITIES map minus what was added since, so the doc can be checked
    # exactly while a later code does not make the check unfalsifiable.
    added_since = {
        "setup.excluded_body",
        "setup.no_inlet_temperature",
        "setup.kind_unsupported",
    }
    assert len(_documented()) == 16, "the ADR's table still lists sixteen"
    assert _documented() == [
        (code, sev) for code, sev in SEVERITY_OF.items() if code not in added_since
    ]
    # A code added after the table is not in it, and that is expected rather than a gap.
    assert added_since.isdisjoint(dict(_documented()))


def test_every_adr_table_code_has_a_test() -> None:
    # **Every code, including the ones added after the ADR's table**: a code with no test is a
    # finding
    # nobody has ever seen produce.
    assert tuple(code for code in COVERS) == tuple(code for code in CODES if code in COVERS)
    assert set(CODES) - set(COVERS) == {"setup.excluded_body"}
    assert COVERS["setup.no_inlet_temperature"] == "test_an_inlet_at_default_temperature_is_named"
    for code, name in COVERS.items():
        assert name.startswith("test_"), code
        assert callable(globals().get(name)), f"{code} is only covered by {name}"


def _region_holding(tmp_path: Path) -> tuple[Session, list[str]]:
    """A session with a fluid region whose tag holds ``part_ids``.

    **Built here rather than borrowed from a fixture**, because the fixture in this file is a bare
    project: what is under test is the EXCLUSION finding, so the smallest thing that has a region is
    the right input, and building it names exactly what the finding reads.
    """
    session = new_session(tmp_path)
    part_ids: list[str] = []
    session.execute("node.add", parent="/geometry", type="group", name="parts")
    # **The ids the model gave**, not the ones asked for: `node.add` honours a requested `node_id`
    # only in some paths, and a region's tag validates its members against real part ids - which is
    # how the first version of this helper failed with "not a node id".
    for name in ("housing", "fan"):
        created = session.execute("node.add", parent="/geometry/parts", type="part", name=name)
        part_ids.append(str(created["id"]))
    session.execute("material.create", name="water", kind="fluid")
    session.execute(
        "region.create",
        name="fluid",
        kind="fluid",
        bodies=part_ids,
        material="/physics/materials/water",
    )
    return session, part_ids


def test_a_region_holding_an_EXCLUDED_body_is_reported(tmp_path: Path) -> None:
    """T-221: a body left out of the simulation while a region still holds it.

    **A contradiction worth reporting rather than an error**: the mesh will be right about what it
    was
    told and wrong about what was meant - a region with a hole - and the fix is one of two clicks,
    so
    naming both is the useful part.

    **Through `check_setup` rather than the helper**, because a helper that returns the right pair
    while nothing reports it protects nobody.
    """
    session, ids = _region_holding(tmp_path)
    p = session.require_project()
    session.execute("geometry.simulate", nodes=[ids[0]], simulate=False)

    result = check_setup(p, ())
    codes = [finding.code for finding in result.findings]
    assert "setup.excluded_body" in codes, codes
    told = next(f for f in result.findings if f.code == "setup.excluded_body")
    # **Both clicks are named**: the body and the region it is still in.
    assert len(told.nodes) == 2
    assert "left out of the simulation" in told.message


def test_a_region_whose_bodies_are_all_in_reports_nothing(tmp_path: Path) -> None:
    """**The other half**, so the rule is not simply reporting every region: a body that is in the
    simulation is not reported, and putting it back clears the finding.
    """
    session, ids = _region_holding(tmp_path)
    p = session.require_project()
    session.execute("geometry.simulate", nodes=[ids[0]], simulate=False)
    assert "setup.excluded_body" in [f.code for f in check_setup(p, ()).findings]
    # Back in: the finding goes, which is what tells the check is the FLAG and not a one-way latch.
    session.execute("geometry.simulate", nodes=[ids[0]], simulate=True)
    assert "setup.excluded_body" not in [f.code for f in check_setup(p, ()).findings]

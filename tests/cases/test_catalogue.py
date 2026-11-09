"""Catalogue classification on synthetic case folders (ADR-0029, plan T-304)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from stokes.cases import facts as facts_module
from stokes.cases.catalogue import (
    STATUSES,
    Inventory,
    catalogue,
    classify,
    counts,
    find_cases,
    has_case,
)
from stokes.cases.facts import library_name, read_allrun, read_case

from .synthetic import GUARDS, HEADER, INVENTORY, build_tree, make_case


@pytest.fixture
def inventory() -> Inventory:
    found = Inventory.from_record(INVENTORY)
    assert found is not None
    return found


def test_every_synthetic_case_gets_its_status(tmp_path: Path, inventory: Inventory) -> None:
    expected = build_tree(tmp_path)
    entries = {entry["id"]: entry for entry in catalogue(tmp_path, inventory)}
    assert {key: entry["status"] for key, entry in entries.items()} == expected
    assert set(expected.values()) == set(STATUSES), "one synthetic case per status"
    for entry in entries.values():
        assert entry["status"] == "ready" or entry["why"], entry


def test_entry_shape_and_fields(tmp_path: Path, inventory: Inventory) -> None:
    build_tree(tmp_path)
    entries = {entry["id"]: entry for entry in catalogue(tmp_path, inventory)}
    ready = entries["fluid/ready"]
    assert ready == {
        "id": "fluid/ready",
        "category": "fluid",
        "path": str(tmp_path / "fluid" / "ready"),
        "application": "foamRun",
        "solver": "incompressibleFluid",
        "regions": None,
        "recipe_kind": "default",
        "status": "ready",
        "why": "",
    }
    assert entries["fluid/readyAllrun"]["recipe_kind"] == "allrun"
    heater = entries["multiRegion/heater"]
    assert heater["solver"] == {"air": "fluid", "wall": "solid"}
    assert heater["regions"] == ["air", "wall"]
    assert "solid" in heater["why"]
    assert entries["legacy/potential"]["solver"] == "potentialFoam"
    assert "extrudeMesh" in entries["fluid/noUtility"]["why"]
    assert "fancy" in entries["fluid/noLibrary"]["why"]
    assert "absentFunc" in entries["fluid/noEtcFunction"]["why"]
    assert "codedFixedValue" in entries["fluid/coded"]["why"]
    assert "cp -r 0.orig 0" in entries["fluid/needsRecipe"]["why"]


def test_without_a_probe_everything_but_coded_is_unverified(tmp_path: Path) -> None:
    build_tree(tmp_path)
    statuses = {entry["id"]: entry["status"] for entry in catalogue(tmp_path, None)}
    assert statuses.pop("fluid/coded") == "coded"
    assert set(statuses.values()) == {"unverified"}


def test_filters_by_category_and_text(tmp_path: Path, inventory: Inventory) -> None:
    build_tree(tmp_path)
    assert [e["id"] for e in catalogue(tmp_path, inventory, category="legacy")] == [
        "legacy/potential"
    ]
    found = [e["id"] for e in catalogue(tmp_path, inventory, text="SOLID")]
    assert found == ["multiRegion/heater"]
    assert [e["id"] for e in catalogue(tmp_path, inventory, text="xifluid")] == ["fluid/noModule"]
    assert catalogue(tmp_path, inventory, category="nothing") == []


def test_counts_cover_every_status(tmp_path: Path, inventory: Inventory) -> None:
    build_tree(tmp_path)
    tally = counts(catalogue(tmp_path, inventory))
    assert set(tally) == set(STATUSES)
    assert tally["ready"] == 3 and tally["unverified"] == 2 and sum(tally.values()) == 13


def test_the_walk_stops_at_a_case_and_skips_hidden_folders(tmp_path: Path) -> None:
    outer = make_case(tmp_path, "a/outer")
    make_case(outer, "inner")  # a case inside a case is part of it, not a second entry
    make_case(tmp_path, ".hidden/case")
    assert find_cases(tmp_path) == [outer]
    assert has_case(tmp_path)
    assert not has_case(tmp_path / ".hidden" / "case" / "system")


def test_a_root_that_is_itself_a_case(tmp_path: Path, inventory: Inventory) -> None:
    make_case(tmp_path, "")
    (entry,) = catalogue(tmp_path, inventory)
    assert entry["id"] == "." and entry["category"] == "."
    assert classify(tmp_path, tmp_path, inventory)["status"] == "ready"


def test_inventory_from_record_needs_the_t304_keys() -> None:
    assert Inventory.from_record(None) is None
    assert Inventory.from_record({"installs": []}) is None
    found = Inventory.from_record({**INVENTORY, "etc_functions": "not a list"})
    assert found is not None and found.etc_functions == frozenset()
    assert found.libraries == frozenset({"fieldFunctionObjects", "extra"})


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('"libfoo.so"', "foo"),
        ("libfoo.dll", "foo"),
        ("bar.dylib", "bar"),
        ("lib/sub/libbaz.so", "baz"),
        ("plain", "plain"),
        ('""', None),
        ("lib", None),
    ],
)
def test_library_names(text: str, expected: str | None) -> None:
    assert library_name(text) == expected


def test_runtime_code_constructs_are_all_found(tmp_path: Path) -> None:
    case = make_case(
        tmp_path,
        "c",
        # `#calc "1+1"` would be evaluated by the import (ADR-0033); a call it cannot do stays code.
        'application foamRun;\nsolver fluid;\nx #calc "Foam::mag(vector(1,0,0))";\n',
        files={
            "system/fvModels": HEADER + "m { type systemCall; }\n",
            "constant/sub/dict": HEADER + "code #{ int x; #};\nv #codeStream { };\n",
            "constant/polyMesh/points": "ignored ( #codeStream )",
            "constant/blob.gz": "\x1f\x8b binary",
        },
    )
    found = read_case(case).coded
    assert any("#calc" in item for item in found)
    assert any("systemCall" in item for item in found)
    assert any("#{" in item for item in found)
    assert any("#codeStream" in item for item in found)
    assert not any("polyMesh" in item for item in found)


def test_etc_includes_and_libs_lists(tmp_path: Path, inventory: Inventory) -> None:
    case = make_case(
        tmp_path,
        "c",
        'application foamRun;\nsolver fluid;\nlibs ("libfieldFunctionObjects.so" extra);\n'
        '#includeEtc "caseDicts/setConstraintTypes"\n',
    )
    facts = read_case(case)
    assert facts.libraries == {"fieldFunctionObjects", "extra"}
    assert facts.etc_includes == {"caseDicts/setConstraintTypes"}
    assert classify(case, tmp_path, inventory)["status"] == "ready"
    (case / "system" / "controlDict").write_text(
        HEADER + 'application foamRun;\nsolver fluid;\n#includeEtc "caseDicts/absent"\n',
        encoding="utf-8",
    )
    assert classify(case, tmp_path, inventory)["status"] == "missing_library"


def test_foamrun_without_a_solver_is_unverified_but_allrun_solver_counts(
    tmp_path: Path, inventory: Inventory
) -> None:
    bare = make_case(tmp_path, "bare", "application foamRun;\n")
    assert classify(bare, tmp_path, inventory)["status"] == "unverified"
    flagged = make_case(
        tmp_path,
        "flagged",
        "application foamRun;\n",
        allrun=GUARDS + "runApplication blockMesh\nrunParallel -np 4 foamRun -solver fluid\n",
    )
    entry = classify(flagged, tmp_path, inventory)
    assert (entry["status"], entry["solver"]) == ("ready", "fluid")


def test_a_legacy_case_takes_its_application_from_the_folder(
    tmp_path: Path, inventory: Inventory
) -> None:
    case = make_case(tmp_path, "legacy/group/potentialFoam/demo/demo", "endTime 1;\n")
    entry = classify(case, tmp_path, inventory)
    assert (entry["application"], entry["status"]) == ("potentialFoam", "ready")
    other = make_case(tmp_path, "legacy/group/absentFoam/demo", "endTime 1;\n")
    assert classify(other, tmp_path, inventory)["status"] == "missing_utility"


def test_a_function_the_case_ships_needs_no_etc_file(tmp_path: Path, inventory: Inventory) -> None:
    case = make_case(
        tmp_path,
        "c",
        "application foamRun;\nsolver fluid;\nfunctions\n{\n    #includeFunc localFunc\n}\n",
        files={"system/localFunc": HEADER + "type surfaces;\n"},
    )
    assert read_case(case).etc_functions == set()
    assert classify(case, tmp_path, inventory)["status"] == "ready"


def test_a_coded_resource_the_allrun_copies_makes_the_case_coded(
    tmp_path: Path, inventory: Inventory
) -> None:
    # angledDuct: clean case files, but Allrun copies a #codeStream blockMeshDict from resources/.
    (tmp_path / "resources" / "blockMesh").mkdir(parents=True)
    (tmp_path / "resources" / "blockMesh" / "duct").write_text(
        HEADER + "x #codeStream { code #{ os << 1; #}; };\n", encoding="utf-8"
    )
    (tmp_path / "resources" / "blockMesh" / "plain").write_text(
        HEADER + 'n 4;\nm #calc "$n * 2";\n', encoding="utf-8"
    )
    coded = make_case(
        tmp_path,
        "fluid/duct",
        "application foamRun;\nsolver fluid;\n",
        allrun=GUARDS + "runApplication blockMesh -dict $FOAM_TUTORIALS/resources/blockMesh/duct\n"
        "runApplication $(getApplication)\n",
    )
    entry = classify(coded, tmp_path, inventory)
    assert entry["status"] == "coded"
    assert "(from resources)" in entry["why"]
    # A #calc the import can evaluate is not runtime code, in a resource or in the case.
    plain = make_case(
        tmp_path,
        "fluid/plain",
        "application foamRun;\nsolver fluid;\n",
        allrun=GUARDS + "runApplication blockMesh -dict $FOAM_TUTORIALS/resources/blockMesh/plain\n"
        "runApplication $(getApplication)\n",
    )
    assert classify(plain, tmp_path, inventory)["status"] == "ready"


def test_a_meshing_only_case_needs_no_solver(tmp_path: Path, inventory: Inventory) -> None:
    case = make_case(
        tmp_path,
        "mesh/m",
        "endTime 0;\n",
        allrun=GUARDS + "runApplication blockMesh\nrunApplication foamRun -solver $(getSolver)\n",
    )
    entry = classify(case, tmp_path, inventory)
    assert (entry["application"], entry["status"]) == (None, "ready")
    assert read_case(case).allrun is not None
    assert read_case(case).allrun.solvers == []  # type: ignore[union-attr]


def test_solver_entry_implies_foamrun(tmp_path: Path, inventory: Inventory) -> None:
    case = make_case(tmp_path, "c", "solver incompressibleFluid;\n")
    assert classify(case, tmp_path, inventory)["application"] == "foamRun"
    multi = make_case(tmp_path, "m", "regionSolvers { a fluid; }\n")
    entry = classify(multi, tmp_path, inventory)
    assert (entry["application"], entry["status"]) == ("foamMultiRun", "ready")


def test_allrun_reader_maps_steps_and_keeps_the_rest(tmp_path: Path) -> None:
    path = tmp_path / "Allrun"
    path.write_text(
        GUARDS
        + "runApplication -s one \\\n  topoSet -dict system/topoSetDict\n"
        + "runApplication blockMesh\nrunApplication blockMesh\n"
        + "runApplication foamRun | tee log\n"
        + "runApplication -o\n"
        + "runApplication $(other)\n"
        + "runApplication 'unbalanced\n"
        + "foamDictionary -entry x system/controlDict\n",
        encoding="utf-8",
    )
    found = read_allrun(path)
    assert found.utilities == ["topoSet", "blockMesh"]
    assert len(found.unmapped) == 5
    assert found.unmapped[0] == "runApplication foamRun | tee log"


def test_an_allrun_that_cannot_be_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "Allrun"
    path.write_text("runApplication blockMesh\n", encoding="utf-8")
    monkeypatch.setattr(facts_module, "MAX_ALLRUN_BYTES", 4)
    assert read_allrun(path).unmapped == ["(Allrun could not be read)"]


def test_an_existing_mesh_skips_blockmesh(tmp_path: Path) -> None:
    inventory = Inventory.from_record({**INVENTORY, "utilities": ["foamRun"]})
    case = make_case(tmp_path, "c", files={"constant/polyMesh/faces": "()"})
    assert classify(case, tmp_path, inventory)["status"] == "ready"
    other = make_case(tmp_path, "d")
    assert classify(other, tmp_path, inventory)["status"] == "missing_utility"


def _junction(link: Path, target: Path) -> None:
    """Make ``link`` a Windows junction to ``target``, or skip: `is_symlink` does not see one."""
    if sys.platform != "win32":
        pytest.skip("junctions are a Windows reparse point")
    comspec = os.environ.get("COMSPEC", "cmd.exe")
    result = subprocess.run(
        [comspec, "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0 or not link.is_junction():
        pytest.skip(f"this host refused to create a junction: {result.stderr.strip()}")


def test_a_junction_inside_a_case_is_not_read_through(tmp_path: Path, inventory: Inventory) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "dict").write_text(
        HEADER + 'libs ("libsecret.so");\nv #codeStream { };\n', encoding="utf-8"
    )
    (outside / "controlDict").write_text(
        HEADER + "application foamRun;\nsolver incompressibleFluid;\n", encoding="utf-8"
    )
    case = make_case(tmp_path, "cases/c", files={"constant/transportProperties": HEADER})
    _junction(case / "constant" / "linked", outside)
    assert "libsecret" in (case / "constant" / "linked" / "dict").read_text(encoding="utf-8")
    assert not (case / "constant" / "linked").is_symlink(), "a junction, not a symlink"

    facts = read_case(case)
    assert "secret" not in facts.libraries
    assert facts.coded == []
    (entry,) = catalogue(tmp_path / "cases", inventory)
    assert (entry["id"], entry["status"]) == ("c", "ready")

    # A case whose whole `system/` is a junction: its controlDict is not read through the link.
    other = tmp_path / "cases" / "d"
    other.mkdir()
    _junction(other / "system", outside)
    entry = classify(other, tmp_path / "cases", inventory)
    assert entry["status"] == "unverified"
    assert "controlDict" in entry["why"]


def test_a_dictionary_the_reader_refuses_is_skipped(tmp_path: Path, inventory: Inventory) -> None:
    case = make_case(tmp_path, "c", files={"system/broken": "a { b"})
    assert classify(case, tmp_path, inventory)["status"] == "ready"

"""Synthetic OpenFOAM case folders for the catalogue tests (ADR-0029 decision 2: no tutorial
content in the repository; every file here is written by the test itself)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

HEADER = "FoamFile\n{\n    format ascii;\n    class dictionary;\n}\n"

#: What the fake install can run.
INVENTORY: dict[str, Any] = {
    "solver_modules": ["incompressibleFluid", "fluid"],
    "utilities": ["foamRun", "foamMultiRun", "blockMesh", "snappyHexMesh", "potentialFoam"],
    "libraries": ["fieldFunctionObjects", "libextra.dll"],
    "etc_functions": ["streamlines"],
    "etc_case_dicts": ["caseDicts/setConstraintTypes"],
    "tutorials_root": None,
}


def make_case(
    root: Path,
    rel: str,
    control: str = "application foamRun;\nsolver incompressibleFluid;\n",
    *,
    block_mesh: bool = True,
    allrun: str | None = None,
    files: dict[str, str] | None = None,
) -> Path:
    """Write one case: `system/controlDict`, optionally a blockMeshDict, an Allrun, other files."""
    case = root / rel
    (case / "system").mkdir(parents=True, exist_ok=True)
    (case / "system" / "controlDict").write_text(HEADER + control, encoding="utf-8")
    if block_mesh:
        (case / "system" / "blockMeshDict").write_text(HEADER + "scale 1;\n", encoding="utf-8")
    if allrun is not None:
        (case / "Allrun").write_text(allrun, encoding="utf-8")
    for name, text in (files or {}).items():
        target = case / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return case


GUARDS = (
    "#!/bin/sh\n"
    "cd ${0%/*} || exit 1    # Run from this directory\n"
    ". $WM_PROJECT_DIR/bin/tools/RunFunctions\n"
    "application=$(getApplication)\n"
)


def build_tree(root: Path) -> dict[str, str]:
    """One case per status (and a few variants); returns {id: expected status}."""
    make_case(root, "fluid/ready")
    make_case(
        root,
        "fluid/readyAllrun",
        block_mesh=False,
        allrun=GUARDS + "runApplication blockMesh\nrunApplication $(getApplication)\n",
    )
    make_case(root, "fluid/noModule", "application foamRun;\nsolver XiFluid;\n")
    make_case(
        root,
        "fluid/noUtility",
        allrun=GUARDS + "runApplication -s x extrudeMesh\nrunApplication $application\n",
    )
    make_case(
        root, "fluid/noLibrary", 'application foamRun;\nsolver fluid;\nlibs ("libfancy.so");\n'
    )
    make_case(
        root,
        "fluid/noEtcFunction",
        "application foamRun;\nsolver fluid;\nfunctions\n{\n    #includeFunc absentFunc(U)\n}\n",
    )
    make_case(
        root,
        "fluid/coded",
        files={
            "0/U": HEADER + "boundaryField\n{\n    inlet\n    {\n        type codedFixedValue;"
            "\n    }\n}\n"
        },
    )
    make_case(root, "fluid/needsRecipe", allrun=GUARDS + "cp -r 0.orig 0\nrunApplication foamRun\n")
    make_case(root, "fluid/noMesh", block_mesh=False)
    make_case(root, "fluid/unreadable", "application foamRun;\nsolver {\n")
    make_case(root, "fluid/noApplication", "endTime 1;\n")
    make_case(root, "legacy/potential", "application potentialFoam;\n")
    make_case(
        root,
        "multiRegion/heater",
        "application foamMultiRun;\nregionSolvers\n{\n    air fluid;\n    wall solid;\n}\n",
    )
    return {
        "fluid/ready": "ready",
        "fluid/readyAllrun": "ready",
        "fluid/noModule": "missing_module",
        "fluid/noUtility": "missing_utility",
        "fluid/noLibrary": "missing_library",
        "fluid/noEtcFunction": "missing_library",
        "fluid/coded": "coded",
        "fluid/needsRecipe": "needs_recipe",
        "fluid/noMesh": "needs_recipe",
        "fluid/unreadable": "unverified",
        "fluid/noApplication": "unverified",
        "legacy/potential": "ready",
        "multiRegion/heater": "missing_module",
    }

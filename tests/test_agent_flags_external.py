"""The external-case utility and flag allow-lists (ADR-0029, amending ADR-0024 §1.2; T-305)."""

from __future__ import annotations

import pytest

from stokes_agent import jobs
from stokes_agent.flags import ALLOWED_FLAGS, FORBIDDEN_FLAGS
from stokes_agent.flags_external import (
    EXTERNAL_FLAGS,
    EXTERNAL_UTILITIES,
    ExternalFlagRefused,
    check_external_argv,
)
from stokes_agent.lint_external import ProbedInstall

INSTALL = ProbedInstall.from_lists(
    solver_modules=["incompressibleFluid", "fluid", "multicomponentFluid"],
    utilities=sorted(EXTERNAL_UTILITIES - {"mdFoam"}),
    etc_functions=["patchAverage", "residuals"],
)

ALLOWED = (
    ["foamRun", "-solver", "multicomponentFluid"],
    ["foamRun"],
    ["foamMultiRun"],
    ["foamPostProcess", "-func", "residuals", "-latestTime"],
    ["foamPostProcess", "-func", "patchAverage(patch=inlet, fields=(p U))", "-time", "0:1,5"],
    ["foamPostProcess", "-solver", "fluid", "-region", "fluid_1"],
    ["blockMesh", "-dict", "system/blockMeshDict.extra"],
    ["topoSet", "-dict", "system/topoSetDict.1", "-region", "solid"],
    ["checkMesh", "-allGeometry", "-allTopology", "-writeSets", "-latestTime"],
    ["decomposePar", "-allRegions", "-copyZero", "-force"],
    ["reconstructPar", "-latestTime", "-fields", "(U p alpha.water)"],
    ["reconstructPar", "-noLagrangian"],
    ["reconstructPar", "-newTimes"],
    ["subsetMesh", "c0", "-patch", "hole", "-overwrite"],
    ["subsetMesh", "-noFields"],
    ["transformPoints", "translate=(1 0 0), scale=(2 2 2)"],
    ["foamDictionary", "system/controlDict", "-entry", "startTime", "-value"],
    ["foamDictionary", "constant/physicalProperties", "-entry", "mixture/nu", "-value"],
    ["stitchMesh", "-perfect", "left", "right", "-overwrite"],
    ["icoFoam"],
    ["potentialFoam", "-writep", "-writePhi"],
    ["setFields", "-dict", "system/setFieldsDict"],
    ["setWaves", "-alpha", "alpha.water", "-U", "U", "-gas"],
    ["boxTurb"],
    ["dsmcInitialise"],
    ["mdInitialise"],
)


@pytest.mark.parametrize("argv", ALLOWED, ids=lambda argv: " ".join(argv)[:40])
def test_allowed(argv: list[str]) -> None:
    check_external_argv(argv, INSTALL)


REFUSED = (
    ["foamRun", "-solver", "notInThisInstall"],
    ["foamRun", "-solver", "fluid;x"],
    ["foamRun", "-solver"],
    ["foamRun", "-solver", "../fluid"],
    ["foamPostProcess", "-func", "notInThisInstall"],
    ["foamPostProcess", "-func", "patchAverage(file=/etc/passwd)"],
    ["foamPostProcess", "-func", "patchAverage(x=$HOME)"],
    ["foamPostProcess", "-func", "patchAverage(x=1; #codeStream{})"],
    ["blockMesh", "-dict", "/etc/passwd"],
    ["blockMesh", "-dict", "../outside"],
    ["blockMesh", "-dict", "C:/x"],
    ["blockMesh", "-dict", "constant/geometry/part.obj"],
    ["blockMesh", "-dict", "-case"],
    ["topoSet", "-region", ".."],
    ["setFields", "-dict", "../outside"],
    ["setWaves", "-alpha", "../outside"],
    ["topoSet", "-region", "a/b"],
    ["checkMesh", "-time", "latest;rm"],
    ["subsetMesh", "c0", "extra"],
    ["subsetMesh", "../c0"],
    ["transformPoints", "translate=(1 0 0); x"],
    ["foamDictionary", "system/controlDict", "-set", "startTime=1"],
    ["foamDictionary", "../../etc/passwd", "-value"],
    ["icoFoam", "-foo"],
    ["mdFoam"],  # allow-listed but not in this install
    ["bash", "-c", "x"],
    ["foamToVTK"],
    [],
)


@pytest.mark.parametrize("argv", REFUSED, ids=lambda argv: " ".join(argv)[:40] or "empty")
def test_refused(argv: list[str]) -> None:
    with pytest.raises(ExternalFlagRefused) as caught:
        check_external_argv(argv, INSTALL)
    assert caught.value.code == "agent.bad_params"


@pytest.mark.parametrize("flag", sorted(FORBIDDEN_FLAGS))
@pytest.mark.parametrize("utility", ["foamRun", "snappyHexMesh", "foamPostProcess", "topoSet"])
def test_forbidden_flags_refused_for_every_external_utility(utility: str, flag: str) -> None:
    with pytest.raises(ExternalFlagRefused):
        check_external_argv([utility, flag, "x"], INSTALL)


def test_no_external_flag_list_names_a_forbidden_flag() -> None:
    for utility, flags in EXTERNAL_FLAGS.items():
        assert not set(flags) & FORBIDDEN_FLAGS, utility


def test_current_profile_lists_are_unchanged() -> None:
    current = {
        "blockMesh",
        "checkMesh",
        "snappyHexMesh",
        "surfaceFeatures",
        "foamRun",
        "decomposePar",
        "reconstructPar",
        "extrudeMesh",  # ADR-0031: the 2D snappy recipe
        # ADR-0032: the cfMesh volume fills and the polyhedral dual.
        "cartesianMesh",
        "tetMesh",
        "pMesh",
        "polyDualMesh",
        # The polyhedral type's symmetry planes: flatten, then set back to `symmetry`
        # (`foamDictionary` is pinned to that one edit by `flags.py`).
        "improveSymmetryPlanes",
        "foamDictionary",
    }
    assert current == jobs.ALLOWED_UTILITIES
    assert ALLOWED_FLAGS["foamRun"]["-solver"].pattern == r"^(incompressibleFluid|fluid)$"
    assert ALLOWED_FLAGS["blockMesh"] == {}
    assert "topoSet" not in ALLOWED_FLAGS
    assert "-dict" not in ALLOWED_FLAGS["blockMesh"]


@pytest.mark.parametrize(
    "argv",
    [
        ["decomposePar", "-fields", "-copyZero"],
        ["decomposePar", "-cellProc"],
        ["splitMeshRegions", "-cellZones", "all", "-overwrite"],
        ["splitMeshRegions", "-cellZones", "gas solid"],
        ["splitMeshRegions", "-cellZones", "all", "-defaultRegion", "fluid"],
    ],
)
def test_the_forms_the_tutorials_use_are_allowed(argv: list[str]) -> None:
    """Found by the T-317 sweep: OpenFOAM 14's decomposePar -fields is a switch, and
    splitMeshRegions -cellZones takes `all` or zone names."""
    check_external_argv(argv, INSTALL)


@pytest.mark.parametrize(
    "argv",
    [
        ["splitMeshRegions", "-cellZones", "../etc"],
        ["splitMeshRegions", "-cellZones", "$ZONES"],
        ["splitMeshRegions", "-defaultRegion", "a/b"],
    ],
)
def test_zone_and_region_values_stay_plain_names(argv: list[str]) -> None:
    with pytest.raises(ExternalFlagRefused):
        check_external_argv(argv, INSTALL)

"""The utility and flag allow-lists for an external case (ADR-0029, amending ADR-0024 §1.2).

`flags.ALLOWED_FLAGS` and `jobs.ALLOWED_UTILITIES` stay what they are for cases Stokes writes. An
imported tutorial runs more programs - `foamPostProcess`, `topoSet`, `createBaffles`, the legacy
applications - and passes them more arguments, so this module holds a **second closed list**, used
only when a job is created under the `external` profile.

## The rules are the same as the current list's

* `argv[0]` is a bare name on `EXTERNAL_UTILITIES` **and** in the probed install's utilities.
* Every later item is a listed flag, the value of the one-argument flag before it, or one of the
  utility's listed positional arguments; anything else is `agent.bad_params`.
* `flags.FORBIDDEN_FLAGS` (`-case`, `-libs`, `-fileHandler`, `-roots`, `-hostRoots`, `-parallel`)
  are refused for every utility. The agent adds `-parallel` and the relative `-case` itself
  (ADR-0025 decision 8); a client may not.
* **Every value is checked**: a word is a word, a time is a time list, `-dict` is a path that
  resolves inside the job (`lint_external.unsafe_job_path`), `-solver` is a module the probe found
  (`lib<name>Solver` present), never a free word, and `-func` names a function the install ships
  with arguments that carry no path and no dictionary syntax.

A flag a utility does not actually have is harmless here (the utility refuses it); a flag missing
from this list makes a recipe fail at import with the reason (T-306), which is the safe direction.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from stokes.errors import StokesError

from .flags import FORBIDDEN_FLAGS
from .lint_external import ProbedInstall, unsafe_job_path

#: A value check: ``(value, install) -> why it is refused``, or None.
ValueCheck = Callable[[str, ProbedInstall], "str | None"]

_WORD = r"[A-Za-z_][A-Za-z0-9_.:\-]*"
_WORD_RE = re.compile(rf"^{_WORD}$")
_REGION_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]*$")
_WORDS_RE = re.compile(rf"^\(\s*(?:{_WORD}\s*)*\)$")
_TIMES_RE = re.compile(r"^[0-9eE.+\-:,]+$")
_FUNC_RE = re.compile(r"^(?P<name>[A-Za-z][A-Za-z0-9_]*)(?P<args>\(.*\))?$", re.DOTALL)
#: Characters a `-func` argument list may not hold: paths, expansions and dictionary syntax, which
#: OpenFOAM would splice into the function's dictionary.
_FUNC_BANNED = ("..", "$", "~", "/", "\\", "#", ";", "{", "}", '"', "'", "`", "\n", "\r")
_TRANSFORM_RE = re.compile(
    r"^(?:translate|rotate|scale|Rx|Ry|Rz|rollPitchYaw|yawPitchRoll)=[0-9eE.+\-() ]+"
    r"(?:,\s*(?:translate|rotate|scale|Rx|Ry|Rz|rollPitchYaw|yawPitchRoll)=[0-9eE.+\-() ]+)*$"
)
_SET_FORMATS = frozenset({"vtk", "raw", "ensight", "foam", "csv", "gnuplot", "xmgr"})


class ExternalFlagRefused(StokesError):
    """An argv item of an external-case step is not allowed."""

    code = "agent.bad_params"


def _word(value: str, _install: ProbedInstall) -> str | None:
    if _WORD_RE.match(value) is None or ".." in value:
        return "the value must be a plain name"
    return None


def _region(value: str, _install: ProbedInstall) -> str | None:
    return None if _REGION_RE.match(value) else "a region must be a plain name with no dots"


def _words(value: str, install: ProbedInstall) -> str | None:
    """A single name, or a list of names `(a b)`."""
    if ".." not in value and (_WORDS_RE.match(value) or _WORD_RE.match(value)):
        return None
    return "the value must be a name or a list of names '(a b)'"


def _zones(value: str, _install: ProbedInstall) -> str | None:
    """`splitMeshRegions -cellZones`: `all`, or zone names separated by spaces (`"gas solid"`)."""
    names = value.split()
    if names and all(_WORD_RE.match(name) and ".." not in name for name in names):
        return None
    return "the value must be 'all' or cell zone names separated by spaces"


def _entry(value: str, install: ProbedInstall) -> str | None:
    """A `foamDictionary -entry` path: names joined by `/`."""
    if all(_word(part, install) is None for part in value.split("/")):
        return None
    return "the value must be an entry path of plain names"


def _times(value: str, _install: ProbedInstall) -> str | None:
    return None if _TIMES_RE.match(value) else "the value must be times or ranges ('0:1,5')"


def _job_path(value: str, _install: ProbedInstall) -> str | None:
    return unsafe_job_path(value)


def _solver(value: str, install: ProbedInstall) -> str | None:
    if value not in install.solver_modules:
        return "the solver is not a module this OpenFOAM install provides"
    return None


def _func(value: str, install: ProbedInstall) -> str | None:
    match = _FUNC_RE.match(value)
    if match is None:
        return "the value must be a function name, optionally with an argument list"
    if match.group("name") not in install.etc_functions:
        return "the function is not one this OpenFOAM install provides"
    args = match.group("args") or ""
    if any(bad in args for bad in _FUNC_BANNED):
        return "the arguments name a path, expand a variable or carry dictionary syntax"
    return None


def _transform(value: str, _install: ProbedInstall) -> str | None:
    return None if _TRANSFORM_RE.match(value) else "not a transformation like translate=(1 0 0)"


def _set_format(value: str, _install: ProbedInstall) -> str | None:
    return None if value in _SET_FORMATS else "not a set or surface format this list knows"


@dataclass(frozen=True)
class ExternalFlag:
    """One permitted flag: a switch (``check`` None) or a flag whose value ``check`` judges."""

    check: ValueCheck | None = None
    why: str = ""


SWITCH = ExternalFlag()
_DICT = ExternalFlag(_job_path, "a dictionary path inside the job")
_REGION = {"-region": ExternalFlag(_region, "a mesh region"), "-allRegions": SWITCH}
_TIME = {
    "-latestTime": SWITCH,
    "-time": ExternalFlag(_times, "time ranges"),
    "-constant": SWITCH,
    "-noZero": SWITCH,
    "-withZero": SWITCH,
}
_MESH_EDIT = {"-dict": _DICT, "-overwrite": SWITCH, **_REGION}

#: Every utility an external case may run, with its flags. The 11 legacy applications and
#: `shallowWaterFoam` run from the case directory with no arguments.
EXTERNAL_FLAGS: dict[str, dict[str, ExternalFlag]] = {
    "blockMesh": {"-dict": _DICT, "-region": _REGION["-region"], "-noClean": SWITCH},
    "checkMesh": {
        **_REGION,
        **_TIME,
        "-allGeometry": SWITCH,
        "-allTopology": SWITCH,
        "-meshQuality": SWITCH,
        "-writeSets": SWITCH,
        "-writeSurfaces": SWITCH,
        "-setFormat": ExternalFlag(_set_format, "a set format"),
        "-surfaceFormat": ExternalFlag(_set_format, "a surface format"),
    },
    "snappyHexMesh": {"-overwrite": SWITCH, "-dict": _DICT, "-region": _REGION["-region"]},
    "surfaceFeatures": {"-dict": _DICT},
    "foamRun": {"-solver": ExternalFlag(_solver, "a solver module the install provides")},
    "foamMultiRun": {},
    "foamPostProcess": {
        "-func": ExternalFlag(_func, "a function the install provides"),
        "-solver": ExternalFlag(_solver, "a solver module the install provides"),
        "-dict": _DICT,
        "-fields": ExternalFlag(_words, "field names"),
        **_REGION,
        **_TIME,
    },
    "potentialFoam": {
        # The pressure field to initialise (`-pName p_rgh` in the VoF tutorials): a field name.
        "-pName": ExternalFlag(_word, "a pressure field name"),
        "-writep": SWITCH,
        "-writePhi": SWITCH,
        "-initialiseUBCs": SWITCH,
        "-withFunctionObjects": SWITCH,
    },
    # **`-fields` is a switch here** (decompose only the fields; OpenFOAM 14's tutorials pass
    # `decomposePar -fields -copyZero`), unlike `reconstructPar -fields '(U p)'`.
    "decomposePar": {
        "-noFields": SWITCH,
        "-force": SWITCH,
        "-copyZero": SWITCH,
        "-cellProc": SWITCH,
        "-fields": SWITCH,
        **_REGION,
        **_TIME,
    },
    "reconstructPar": {
        "-noFields": SWITCH,
        "-noLagrangian": SWITCH,
        "-newTimes": SWITCH,
        "-fields": ExternalFlag(_words, "field names"),
        **_REGION,
        **_TIME,
    },
    "topoSet": {"-dict": _DICT, **_REGION, **_TIME},
    "setFields": {
        "-dict": _DICT,
        "-region": _REGION["-region"],
        **{key: _TIME[key] for key in ("-constant", "-latestTime", "-time", "-noZero")},
    },
    "setWaves": {
        "-dict": _DICT,
        "-U": ExternalFlag(_word, "a field name"),
        "-alpha": ExternalFlag(_word, "a field name"),
        "-gas": SWITCH,
        "-region": _REGION["-region"],
        **{key: _TIME[key] for key in ("-latestTime", "-time", "-noZero")},
    },
    "boxTurb": {},
    "dsmcInitialise": {},
    "mdInitialise": {},
    "changeDictionary": {"-dict": _DICT, **_REGION, **_TIME},
    "createZones": {"-dict": _DICT, **_REGION},
    "createNonConformalCouples": _MESH_EDIT,
    "createBaffles": {**_MESH_EDIT, "-fields": SWITCH},
    "createPatch": _MESH_EDIT,
    "extrudeMesh": _MESH_EDIT,
    "extrudeToRegionMesh": _MESH_EDIT,
    "refineMesh": {**_MESH_EDIT, "-all": SWITCH},
    "subsetMesh": {
        **_MESH_EDIT,
        "-noFields": SWITCH,
        "-patch": ExternalFlag(_word, "a patch name"),
        "-patches": ExternalFlag(_words, "patch names"),
        "-cellSet": ExternalFlag(_word, "a cell set"),
    },
    "splitMeshRegions": {
        **_MESH_EDIT,
        "-cellZones": ExternalFlag(_zones, "'all' or cell zone names"),
        "-defaultRegion": ExternalFlag(_region, "the region of cells in no zone"),
        "-cellZonesOnly": SWITCH,
        "-cellSet": ExternalFlag(_word, "a cell set"),
    },
    "splitBaffles": _MESH_EDIT,
    "mirrorMesh": _MESH_EDIT,
    "stitchMesh": {**_MESH_EDIT, "-perfect": SWITCH, "-partial": SWITCH},
    "renumberMesh": {**_MESH_EDIT, **_TIME},
    "collapseEdges": {
        **_MESH_EDIT,
        "-collapseFaces": SWITCH,
        "-collapseFaceSet": ExternalFlag(_word, "a face set"),
    },
    # `-pointZone ball` moves only one zone's points (ballValve's 45-degree ball): a zone name.
    "transformPoints": {
        **_REGION,
        "-allRegions": SWITCH,
        "-pointZone": ExternalFlag(_word, "a point zone name"),
    },
    "mergeMeshes": {"-overwrite": SWITCH, "-region": _REGION["-region"]},
    "zeroDimensionalMesh": {},
    # **Read-only forms only**: `-set`, `-add`, `-remove` and `-merge` write the dictionary and are
    # not here.
    "foamDictionary": {
        "-entry": ExternalFlag(_entry, "an entry path"),
        "-value": SWITCH,
        "-keywords": SWITCH,
        "-expand": SWITCH,
    },
    "shallowWaterFoam": {},
    **{
        legacy: {}
        for legacy in (
            "icoFoam",
            "dsmcFoam",
            "porousSimpleFoam",
            "rhoPorousSimpleFoam",
            "mdEquilibrationFoam",
            "mdFoam",
            "mhdFoam",
            "electrostaticFoam",
            "laplacianFoam",
            "financialFoam",
            "adjointShapeOptimisationFoam",
        )
    },
}

EXTERNAL_UTILITIES: frozenset[str] = frozenset(EXTERNAL_FLAGS)

#: Positional arguments, checked in order. A utility not here takes none.
EXTERNAL_POSITIONAL: dict[str, tuple[ValueCheck, ...]] = {
    "subsetMesh": (_word,),
    "stitchMesh": (_word, _word),
    "transformPoints": (_transform,),
    "foamDictionary": (_job_path,),
    # The two patches to couple (`createNonConformalCouples pipeNonCouple ballNonCouple`).
    "createNonConformalCouples": (_word, _word),
}


def _refuse(message: str, utility: str, **details: str) -> ExternalFlagRefused:
    return ExternalFlagRefused(message, details={"utility": utility, **details})


def check_external_argv(argv: Sequence[str], install: ProbedInstall) -> None:
    """Raise `agent.bad_params` unless ``argv`` is a step an external case may run here."""
    if not argv or not isinstance(argv[0], str):
        raise ExternalFlagRefused("an empty argv")
    utility = argv[0]
    allowed = EXTERNAL_FLAGS.get(utility)
    if allowed is None:
        raise _refuse(f"{utility!r} is not an allowed utility for an external case", utility)
    if utility not in install.utilities:
        raise _refuse(f"{utility} is not in this OpenFOAM install", utility)
    positional = list(EXTERNAL_POSITIONAL.get(utility, ()))
    index = 1
    while index < len(argv):
        item = argv[index]
        if not isinstance(item, str) or "\0" in item:
            raise _refuse("every argv item must be a string with no NUL", utility)
        if item in FORBIDDEN_FLAGS:
            raise _refuse(f"{item} is not allowed for any utility", utility, flag=item)
        spec = allowed.get(item)
        if spec is None and not item.startswith("-") and positional:
            why = positional.pop(0)(item, install)
            if why is not None:
                raise _refuse(f"{item!r} is not allowed: {why}", utility, value=item)
            index += 1
            continue
        if spec is None:
            raise _refuse(
                f"{item!r} is not an allowed argument for {utility}; allowed: "
                f"{', '.join(sorted(allowed)) or 'none'}",
                utility,
                flag=item,
            )
        if spec.check is None:
            index += 1
            continue
        if index + 1 >= len(argv):
            raise _refuse(f"{item} needs a value", utility, flag=item)
        value = argv[index + 1]
        why = spec.check(value, install) if isinstance(value, str) else "not a string"
        if why is not None:
            raise _refuse(f"{item} {value!r} is not allowed: {why}", utility, flag=item)
        index += 2

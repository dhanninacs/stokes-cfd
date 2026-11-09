"""The `external` lint profile (ADR-0029 decision 5, T-305).

Every dictionary here is synthetic and written in this file; no OpenFOAM tutorial content.
Both directions are tested: what the profile still refuses, and what it now admits only when the
probed install has it. The current profile is checked to be unchanged by the same texts.
"""

from __future__ import annotations

import gzip
import inspect
import struct
from pathlib import Path

import pytest

from stokes_agent import lint
from stokes_agent.lint import lint_job, lint_text
from stokes_agent.lint_external import (
    ExternalUnsafeDict,
    ProbedInstall,
    lint_file_external,
    lint_job_external,
    lint_text_external,
    refuse,
    unsafe_job_path,
)

INSTALL = ProbedInstall.from_lists(
    solver_modules=["incompressibleFluid", "fluid"],
    utilities=["blockMesh", "foamRun", "foamPostProcess", "topoSet"],
    libraries=["libfieldFunctionObjects.so", "fvModels"],
    etc_functions=["patchAverage", "residuals"],
    etc_files=["caseDicts/setConstraintTypes", "caseDicts/mesh/generation/meshQualityDict.cfg"],
)

CONTROL = "system/controlDict"


def _ext(text: str, path: str = CONTROL) -> list[lint.Finding]:
    return lint_text_external(path, text, INSTALL)


def _can_symlink() -> bool:
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        try:
            (Path(folder) / "link").symlink_to(Path(folder) / "target")
        except (OSError, NotImplementedError):
            return False
    return True


needs_symlinks = pytest.mark.skipif(
    not _can_symlink(), reason="symlinks need a privilege on this host"
)


# --- what the external profile still refuses -----------------------------------------------------

REFUSED = (
    ("#codeStream\n{\n    code #{ #};\n}\n", "#codeStream"),
    ('deltaT #calc "2+2";', "#calc"),
    ('deltaT #eval "1+1";', "#eval"),
    ("#codeBlock\n#endCodeBlock\n", "#codeBlock"),
    ('#includeIfPresent "a"', "#includeIfPresent"),
    ('#sinclude "a"', "#sinclude"),
    ("inlet { type codedFixedValue; }", "codedFixedValue"),
    ("inlet { type codedMixed; }", "codedMixed"),
    ("t codedMixed; inlet { type $t; }", "type"),
    ('f { type systemCall; executeCalls ("touch x"); }', "systemCall"),
    ('#include "/etc/passwd"', "#include"),
    ('#include "C:/Windows/win.ini"', "#include"),
    ('#include "../../outside"', "#include"),
    ('#include "../../../etc/passwd"', "#include"),
    ('#include "$HOME/x"', "#include"),
    ('#include "~/x"', "#include"),
    ('#include "..\\\\x"', "#include"),
    ('#include "../constant/polyMesh/points"', "#include"),
    ('#include "../constant/geometry/part.obj"', "#include"),
    ('#include "../constant/geometry./part.obj"', "#include"),
    ('#include "../constant/surface.stl"', "#include"),
    ('#includeEtc "caseDicts/notInThisInstall"', "#includeEtc"),
    ('#includeEtc "config.sh"', "#includeEtc"),
    ('#includeEtc "caseDicts/../bashrc"', "#includeEtc"),
    ('#includeEtc "/etc/passwd"', "#includeEtc"),
    ("#includeFunc notInThisInstall", "#includeFunc"),
    ("#includeFunc patchAverage(file=/etc/passwd)", "#includeFunc"),
    ("#includeFunc patchAverage (x=$HOME)", "#includeFunc"),
    ('libs ("libnotProbed.so");', "libs"),
    ('libs ("/tmp/libfieldFunctionObjects.so");', "libs"),
    ('libs ("../libfieldFunctionObjects.so");', "libs"),
    ('libs ("fieldFunctionObjects");', "libs"),
    ("libs ($lib);", "libs"),
    ("libs;", "libs"),
    ('libs ("libfvModels.so") ("libevil.so");', "libs"),
    ("libs libfvModels.so libevil.so;", "libs"),
    ('functionObjectLibs ("libevil.so");', "functionObjectLibs"),
    ('"l[i]bs" ("libfieldFunctionObjects.so");', '"l[i]bs"'),
    ("x $constant/geometry/part.stl!secret;", ""),
    ("timeActivatedFileUpdate { fileToUpdate x; }", "timeActivatedFileUpdate"),
)


@pytest.mark.parametrize(("text", "token"), REFUSED)
def test_external_refuses(text: str, token: str) -> None:
    found = _ext(text)
    assert found, text
    assert any(token.lower() in item.token.lower() for item in found), found


@pytest.mark.parametrize(
    "text",
    (
        '/* a comment */ #codeStream { code "x"; }',
        '// line\nx 1; /**/ #calc "1";',
        'values (1 2 #codeStream { code "x"; } 3);',
        'a "x//y"; #eval "1";',
        'list ( "s" (inner #calc "1") );',
    ),
)
def test_external_refuses_code_hidden_after_a_comment_or_inside_a_list(text: str) -> None:
    assert any(
        item.token.lower().startswith(("#codestream", "#calc", "#eval")) for item in _ext(text)
    )


def test_refusal_names_file_line_and_token() -> None:
    text = 'a 1;\nb 2;\nlibs ("libnotProbed.so");\n'
    (found,) = _ext(text)
    assert (found.file, found.line) == (CONTROL, 3)
    assert "libnotProbed.so" in found.token
    with pytest.raises(ExternalUnsafeDict) as caught:
        refuse([found])
    assert caught.value.code == "agent.unsafe_dict"
    assert caught.value.details["file"] == CONTROL
    assert caught.value.details["line"] == 3
    assert caught.value.details["token"] == found.token


# --- what it admits, and only against the probe --------------------------------------------------

ALLOWED = (
    '#includeEtc "caseDicts/setConstraintTypes"',
    '#includeEtc "caseDicts/mesh/generation/meshQualityDict.cfg"',
    "#includeFunc patchAverage(patch=inlet, fields=(p U))",
    "#includeFunc residuals",
    'libs ("libfieldFunctionObjects.so");',
    "libs (libfvModels.so);",
    'libs ("libfieldFunctionObjects.so" "libfvModels.dll");',
    'f { type surfaces; libs ("libfieldFunctionObjects.so"); }',
    '#include "../constant/physicalProperties"',
    '#include "include/initialConditions"',
    '#include "$FOAM_CASE/constant/physicalProperties"',
    '#include "${FOAM_CASE}/system/blockMeshDict.inc"',
    "#remove (a b)",
    "#includeModel gradP",
    'solvers { "(U|k).*" { solver PBiCGStab; tolerance 1e-6; } }',
)


@pytest.mark.parametrize("text", ALLOWED)
def test_external_admits(text: str) -> None:
    assert _ext(text) == []


@pytest.mark.parametrize(
    "text",
    (
        '#includeEtc "caseDicts/setConstraintTypes"',
        "#includeFunc residuals",
        'libs ("libfieldFunctionObjects.so");',
    ),
)
def test_admission_depends_on_the_probe(text: str) -> None:
    assert _ext(text) == []
    assert lint_text_external(CONTROL, text, ProbedInstall()) != []


@pytest.mark.parametrize(
    "text",
    (
        '#includeEtc "caseDicts/mesh/generation/meshQualityDict.cfg"',
        "#includeFunc residuals",
        'libs ("libfieldFunctionObjects.so");',
        '#include "$FOAM_CASE/constant/physicalProperties"',
        "#remove (a b)",
    ),
)
def test_current_profile_still_refuses_what_external_admits(text: str) -> None:
    assert lint_text(CONTROL, text) != []


def test_current_entry_points_cannot_select_another_profile() -> None:
    for function in (lint.lint_text, lint.lint_file, lint.lint_job):
        names = set(inspect.signature(function).parameters)
        assert not names & {"rules", "profile", "install"}, function.__name__
    assert lint.CURRENT_RULES.directives == lint.ALLOWED_DIRECTIVES
    assert lint.CURRENT_RULES.words is lint.REFUSED_WORDS
    assert "remove" not in lint.ALLOWED_DIRECTIVES


@pytest.mark.parametrize(
    ("target", "including", "ok"),
    (
        ("constant/x", "", True),
        ("../constant/x", "system/controlDict", True),
        ("../../x", "system/controlDict", False),
        ("a/../../x", "", False),
        ("..", "", False),
        ("/abs", "", False),
        ("D:/abs", "", False),
        ("$FOAM_CASE/../x", "", False),
        ("$FOAM_CASE/system/x", "system/sub/deep", True),
        ("x.part", "", False),
        ("constant/polyMesh./faces", "", False),
        ("constant/geometry:s/x", "", False),
    ),
)
def test_path_escape_rules(target: str, including: str, ok: bool) -> None:
    assert (unsafe_job_path(target, including) is None) is ok


def test_probe_record_is_untrusted() -> None:
    install = ProbedInstall.from_lists(
        solver_modules=["fluid", "../x", "a b"],
        libraries=["/tmp/libevil.so", "libgood.so", "libx.so;y"],
        etc_functions=["ok", "a/b"],
        etc_files=["caseDicts/a", "caseDicts/../b", "/etc/passwd", "bashrc"],
    )
    assert install.solver_modules == {"fluid"}
    assert install.libraries == {"good"}
    assert install.etc_functions == {"ok"}
    assert install.etc_files == {"caseDicts/a"}


# --- files: text is linted, data passes by name and header ---------------------------------------


def _write(root: Path, rel: str, data: bytes) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _binary_stl(triangles: int = 2) -> bytes:
    return b"\0" * 80 + struct.pack("<I", triangles) + b"\x01" * (50 * triangles)


def test_binary_surface_under_geometry_passes(tmp_path: Path) -> None:
    rel = "constant/geometry/part.stl"
    assert lint_file_external(rel, _write(tmp_path, rel, _binary_stl()), INSTALL) == []


def test_binary_surface_elsewhere_is_refused(tmp_path: Path) -> None:
    rel = "constant/part.stl"
    assert lint_file_external(rel, _write(tmp_path, rel, _binary_stl()), INSTALL) != []


def test_surface_with_wrong_header_is_refused(tmp_path: Path) -> None:
    rel = "constant/geometry/part.vtk"
    assert lint_file_external(rel, _write(tmp_path, rel, b"\0\1\2junk"), INSTALL) != []
    rel = "constant/geometry/bad.stl"
    assert lint_file_external(rel, _write(tmp_path, rel, b"\0" * 90), INSTALL) != []


def test_obj_comments_are_data(tmp_path: Path) -> None:
    rel = "constant/geometry/part.obj"
    data = b"# a surface\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
    assert lint_file_external(rel, _write(tmp_path, rel, data), INSTALL) == []


def test_gzipped_surface_passes_and_gzipped_dictionary_is_linted(tmp_path: Path) -> None:
    surface = "constant/geometry/part.obj.gz"
    path = _write(tmp_path, surface, gzip.compress(b"# obj\nv 0 0 0\n"))
    assert lint_file_external(surface, path, INSTALL) == []
    fake = "constant/geometry/fake.obj.gz"
    assert lint_file_external(fake, _write(tmp_path, fake, b"not gzip"), INSTALL) != []
    # A name with no inner extension is reachable as `name` (OpenFOAM tries `name.gz`): linted.
    hidden = "constant/geometry/bare.gz"
    path = _write(tmp_path, hidden, gzip.compress(b'#codeStream { code "x"; }\n'))
    assert lint_file_external(hidden, path, INSTALL) != []
    field = "0/U.gz"
    path = _write(tmp_path, field, gzip.compress(b'internalField #calc "1";\n'))
    (found,) = [item for item in lint_file_external(field, path, INSTALL) if item.token]
    assert found.token == "#calc"
    clean = "0/p.gz"
    path = _write(tmp_path, clean, gzip.compress(b"internalField uniform 0;\n"))
    assert lint_file_external(clean, path, INSTALL) == []


def test_binary_openfoam_file_is_refused_with_its_reason(tmp_path: Path) -> None:
    rel = "0/U"
    data = b"FoamFile { format binary; }\ninternalField nonuniform List<vector> 1(\0\0\0\xff);\n"
    (found,) = lint_file_external(rel, _write(tmp_path, rel, data), INSTALL)
    assert "binary-format" in found.why


def test_lint_job_external(tmp_path: Path) -> None:
    _write(tmp_path, CONTROL, b'libs ("libfieldFunctionObjects.so");\n#includeFunc residuals\n')
    _write(tmp_path, "system/fvSolution", b'#include "../constant/extra"\n')
    _write(tmp_path, "constant/extra", b"a 1;\n")
    _write(tmp_path, "constant/geometry/part.stl", _binary_stl())
    _write(tmp_path, "log.foamRun", b"\0binary log is the agent's own\n")
    assert lint_job_external(tmp_path, INSTALL) == []
    # The current profile refuses the same job.
    assert lint_job(tmp_path) != []
    _write(tmp_path, "system/fvSchemes", b'a 1;\n\n#include "../../escape"\n')
    (found,) = lint_job_external(tmp_path, INSTALL)
    assert (found.file, found.line) == ("system/fvSchemes", 3)


@needs_symlinks
def test_symlink_out_of_the_job_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.write_text("secret 1;\n")
    job = tmp_path / "job"
    (job / "constant").mkdir(parents=True)
    (job / "constant" / "link").symlink_to(outside)
    (job / "system").mkdir()
    (job / "system" / "controlDict").write_text('#include "../constant/link"\n')
    found = lint_job_external(job, INSTALL)
    assert any(item.file == "constant/link" and "symbolic link" in item.why for item in found)


def test_a_gzip_surface_that_expands_past_the_cap_is_refused(tmp_path: Path) -> None:
    """Review item 2: a `.gz` surface is decompressed to the end under the text cap, not sniffed."""
    rel = "constant/geometry/bomb.obj.gz"
    data = gzip.compress(b"# obj\n" + b" " * (lint.MAX_SCAN_BYTES + 1), compresslevel=9)
    assert len(data) < 64 * 1024
    (found,) = lint_file_external(rel, _write(tmp_path, rel, data), INSTALL)
    assert "expands past" in found.why
    with pytest.raises(ExternalUnsafeDict):
        refuse([found])


def test_a_gzip_binary_stl_is_judged_on_its_decompressed_size(tmp_path: Path) -> None:
    rel = "constant/geometry/part.stl.gz"
    assert (
        lint_file_external(rel, _write(tmp_path, rel, gzip.compress(_binary_stl())), INSTALL) == []
    )
    short = gzip.compress(_binary_stl()[:-1])
    assert lint_file_external(rel, _write(tmp_path, rel, short), INSTALL) != []


def test_a_quoted_libs_key_is_judged_like_the_bare_one() -> None:
    """Review item 3: `"libs" (…);` was refused as unreadable; it now gets the same name check."""
    text = '"libs" ("libfieldFunctionObjects.so");'
    assert _ext(text) == []
    assert lint_text_external(CONTROL, text, ProbedInstall()) != []
    assert _ext('"libs" ("libnotProbed.so");') != []
    assert _ext('"functionObjectLibs" ("/tmp/libfieldFunctionObjects.so");') != []


def test_include_constraint_is_external_only_and_checked() -> None:
    """OpenFOAM 14's `#includeConstraint limitPressure(...)` (found by the T-317 sweep): the
    external profile admits a name with path-free arguments, and only that."""
    ok = "#includeConstraint limitPressure(minFactor=0.1, maxFactor=2)\n"
    assert lint_text_external("system/fvConstraints", ok, INSTALL) == []
    assert lint_text("system/fvConstraints", ok), "the profile for Stokes' own cases refuses it"
    for bad in (
        "#includeConstraint limitPressure(file=/etc/passwd)\n",
        "#includeConstraint limitPressure(x=$HOME)\n",
        '#includeConstraint "limitPressure"\n',
    ):
        assert lint_text_external("system/fvConstraints", bad, INSTALL), bad

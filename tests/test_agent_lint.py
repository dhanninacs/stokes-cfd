"""The dictionary lint (T-202b).

One test per refused token, plus the two things a lint must get right in the other direction: it
must
**accept** OpenFOAM's own files, and it must not refuse a comment that merely mentions a term.

A false refusal is worse than a missed one, because it teaches users to work around the lint —
so both
directions are tested here, not only the refusals.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes_agent.lint import (
    ALLOWED_INCLUDE_ETC,
    ALLOWED_INCLUDE_FUNC,
    MAX_LINE_CHARS,
    MAX_SCAN_BYTES,
    lint_job,
    lint_text,
    strip_comments,
)

FIXTURES = Path(__file__).parent / "fixtures" / "of14" / "pitzDaily"


def _can_symlink() -> bool:
    """Whether this account may create symlinks (Windows needs a privilege or developer mode)."""
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


@pytest.mark.parametrize(
    ("text", "token"),
    (
        ("#codeStream\n{\n}\n", "#codestream"),
        ('deltaT #calc "2+2";', "#calc"),
        ('value #eval "1";', "#eval"),
        ('#sinclude "a"', "#sinclude"),
        ('#includeIfPresent "a"', "#includeifpresent"),
        ('#includeEtc "config.sh"', "#includeEtc"),
        ('#includeEtc "bashrc"', "#includeEtc"),
        ('#include "/etc/passwd"', "#include"),
        ('#include "../../../etc/passwd"', "#include"),
        ("#includeFunc streamlines", "#includeFunc"),
        ("#includeFunc writeObjects", "#includeFunc"),
        ('systemCall "rm -rf /";', "systemcall"),
        ('libs ("evil.so");', "libs"),
        ("type codedFixedValue;", "type coded"),
        ("type            coded;", "type coded"),
        ('codeInclude "#include <x>"', "codeinclude"),
        ('codeOptions "-O3"', "codeoptions"),
        ('codeLibs ("-lx")', "codelibs"),
    ),
)
def test_each_refused_token(text: str, token: str) -> None:
    """**One case per token the brief lists.** A token with no test is a token
    somebody will delete."""
    found = lint_text("system/x", text)
    assert found, f"{token} was not refused"
    assert token.lower() in found[0].token.lower(), found[0]


@pytest.mark.parametrize(
    ("text", "why"),
    (
        ("", "an empty file"),
        ("deltaT          0.001;\n", "an ordinary entry"),
        ("// a comment\n", "a line comment"),
        ("/* a block comment */\n", "a block comment"),
        ("// libs in a comment only\n", "the word inside a comment"),
        ("deltaT 1; // libs\n", "the word after a real entry"),
        ("// we removed #calc from this file\n", "a directive named in a comment"),
        ('#includeEtc "caseDicts/setConstraintTypes"\n', "an allowed etc name"),
        ('#includeEtc "caseDicts/mesh/generation/meshQualityDict"\n', "the other etc name"),
        ("#includeFunc patchAverage(patch=inlet, fields=(p U))\n", "an allowed function"),
        ("#includeFunc patchFlowRate(patch=outlet)\n", "the other allowed function"),
        ('#include "initialConditions"\n', "a relative include"),
        ('#include "0.orig/U"\n', "a relative include with a dot"),
    ),
)
def test_allowed_content_passes(text: str, why: str) -> None:
    """**The other direction.** A lint that refuses OpenFOAM's own files is one nobody can use."""
    assert lint_text("system/x", text) == [], f"refused {why}"


def test_the_real_pitzdaily_case_passes_untouched() -> None:
    """**The install's own tutorial, verbatim.** This is the strongest acceptance
    check available: if the
    lint refuses pitzDaily, it refuses cases that OpenFOAM ships and works with.

    `system/functions` is the interesting file — its entire body is an
    `#includeFunc patchAverage(...)`,
    which is exactly the shape the lint has to allow.
    """
    files = [path for path in FIXTURES.rglob("*") if path.is_file()]
    assert files, "the pitzDaily fixture is missing"
    refused: list[str] = []
    for path in files:
        rel = path.relative_to(FIXTURES).as_posix()
        if lint_text(rel, path.read_text(errors="replace")):
            refused.append(rel)
    assert refused == [], f"the lint refused OpenFOAM's own files: {refused}"


def test_the_include_func_fixture_is_the_case_being_tested() -> None:
    """Guard against the acceptance test passing because `system/functions` is empty or absent."""
    functions = FIXTURES / "system" / "functions"
    assert functions.is_file(), "the fixture has no system/functions"
    text = functions.read_text()
    assert "#includeFunc" in text, "the fixture no longer exercises the includeFunc path"


def test_strip_comments_blanks_a_comment_and_keeps_the_rest() -> None:
    """The helper the whole 'a comment is not code' rule rests on.

    The result has the same length and the same line breaks, so a line number found in it is
    the line number in the file."""
    assert strip_comments("value 1; // #calc") == "value 1;" + " " * 9
    assert strip_comments("value 1; /* #eval */") == "value 1;" + " " * 12
    assert strip_comments("// all comment") == " " * 14
    assert strip_comments("value 1;") == "value 1;"
    text = "a /* one\ntwo */ b\nc // d\ne"
    stripped = strip_comments(text)
    assert len(stripped) == len(text)
    assert stripped.count("\n") == text.count("\n")
    assert stripped.split("\n")[1].endswith(" b")


def test_strip_comments_keeps_a_string_whole() -> None:
    """A `//` or `/*` inside a double-quoted string is data, and a backslash escapes a quote."""
    assert strip_comments('x "a//b"; y') == 'x "a//b"; y'
    assert strip_comments('x "a/*b"; y') == 'x "a/*b"; y'
    assert strip_comments(r'x "a\"//b"; y') == r'x "a\"//b"; y'


def test_a_directive_is_found_anywhere_on_a_line() -> None:
    """**The bug this test exists for.** The first version checked only the line's FIRST token, so
    `value #calc "2+2";` — the ordinary way `#calc` is written — was allowed while a
    bare `#calc` was
    refused. A directive is a directive wherever it appears."""
    for text in ('value #calc "2+2";', 'x  #calc "1"', '#calc "1"'):
        assert lint_text("system/x", text), f"{text!r} was allowed"


def test_a_directive_before_a_comment_is_still_refused() -> None:
    """The order that matters: stripping the comment must not remove the directive beside it."""
    assert lint_text("system/x", "#codeStream // inline")
    assert lint_text("system/x", 'v #calc "1" // trailing')
    assert lint_text("system/x", "// leading\n#codeStream")


def test_a_word_boundary_is_respected() -> None:
    """`libs` must not match `libsSomething`, and `coded` must not match `encoded` — a
    false refusal is a
    refusal a user has to work around."""
    assert lint_text("system/x", "libsSomething 1;\n") == []
    assert lint_text("system/x", "encoded 1;\n") == []
    assert lint_text("system/x", "codedSomethingElse 1;\n") == []


def test_an_oversized_file_is_refused_rather_than_partly_scanned() -> None:
    """**A partial scan is worse than none.** The bytes that were not read are exactly
    where a directive
    would be put."""
    huge = "a" * (MAX_SCAN_BYTES + 1)
    found = lint_text("system/x", huge)
    assert found and "scan limit" in found[0].why


def test_an_overlong_line_is_refused() -> None:
    """OpenFOAM has no line that long, and a file that does is built to defeat a
    line-anchored scan."""
    found = lint_text("system/x", "x" * (MAX_LINE_CHARS + 1) + "\n")
    assert found and "characters" in found[0].why


def test_a_finding_names_the_file_and_line() -> None:
    """A refusal that only says "unsafe dictionary" leaves a user hunting through a case
    they did not
    write."""
    found = lint_text("system/controlDict", "ok 1;\nok 2;\n#codeStream\n")
    assert len(found) == 1
    assert found[0].file == "system/controlDict"
    assert found[0].line == 3
    assert "codeStream" in found[0].token
    assert found[0].to_json()["line"] == 3


def test_lint_job_skips_only_what_the_agent_wrote(tmp_path: Path) -> None:
    """Every file is scanned **except** the agent's own: the `artifacts/` folder and the
    top-level `log.*` files. (A client cannot stage into either: `check_staged_path`.)"""
    (tmp_path / "system").mkdir()
    (tmp_path / "system" / "controlDict").write_text("#codeStream\n")
    (tmp_path / "notes.txt").write_text("#codeStream\n")
    (tmp_path / "log.mesh").write_text("#codeStream\n")
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "index.json").write_text("#codeStream\n")
    found = lint_job(tmp_path)
    assert sorted(item.file for item in found) == ["notes.txt", "system/controlDict"]


def test_lint_job_gives_polymesh_only_the_no_directive_rule(tmp_path: Path) -> None:
    """**`constant/polyMesh` is machine-written** and copied verbatim by `copy_from`, so it
    gets one rule:
    no directive at all. Its normal content — which mentions `libs` in a `FoamFile`? no — must
    pass, but a
    directive in it must not."""
    poly = tmp_path / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    (poly / "boundary").write_text("FoamFile\n{\n    class dictionary;\n}\n")
    (poly / "points").write_text("FoamFile\n{\n}\n#codeStream\n")
    found = lint_job(tmp_path)
    assert [item.file for item in found] == ["constant/polyMesh/points"]
    assert "no directives" in found[0].why


def test_lint_job_scans_a_left_over_partial_upload(tmp_path: Path) -> None:
    """A `.part` file left behind by an upload that never finished is still a file in the job
    folder, so it is scanned: the old rule skipped it, and nothing then stopped an `#include`
    from reading it. (`lint_job` runs at `create` and `start`, never mid-upload.)"""
    system = tmp_path / "system"
    system.mkdir()
    (system / "controlDict.part").write_text("#codeStream\n")
    assert [item.file for item in lint_job(tmp_path)] == ["system/controlDict.part"]


def test_lint_job_can_be_limited_to_named_files(tmp_path: Path) -> None:
    """`put(final=True)` checks what it just wrote, not the whole case — that is what the
    parameter is
    for."""
    system = tmp_path / "system"
    system.mkdir()
    (system / "a").write_text("#codeStream\n")
    (system / "b").write_text('#calc "1"\n')
    found = lint_job(tmp_path, ["system/b"])
    assert [item.file for item in found] == ["system/b"]


def test_the_allowed_lists_are_the_installs() -> None:
    """`patchFlowRate` and `patchAverage` are what `$FOAM_ETC/caseDicts/functions/`
    provides — the brief
    called the first `flowRatePatch`, which is not a name the install has. `stopAtFile`
    (`caseDicts/functions/control/stopAtFile`) is the graceful Stop every case carries."""
    assert {"patchAverage", "patchFlowRate", "stopAtFile", "fieldAverage"} == ALLOWED_INCLUDE_FUNC
    # ADR-0024 §1.8: two EXACT names, not a prefix a `..` can walk out of.
    assert ALLOWED_INCLUDE_ETC == (
        "caseDicts/setConstraintTypes",
        "caseDicts/mesh/generation/meshQualityDict",
    )
    assert "flowRatePatch" not in ALLOWED_INCLUDE_FUNC


# ------------------------------------------------------------------ FIXES-A A3: the bypasses
#
# Each of these returned NO finding on the line-anchored lint. One case per line, so a regression
# names the bypass that came back.

BYPASSES = (
    ("/**/ #codeStream\n{\n}\n", "an empty block comment before the directive"),
    ('"a//b"; #calc "1";\n', "a // inside a string before the directive"),
    ("x 1; /* one\ntwo */ #codeStream\n", "after a block comment that spans lines"),
    ('"a\\"//b" #eval "1";\n', "after a string with an escaped quote"),
    ('x "y"; #include "/etc/passwd"\n', "an include in the middle of a line"),
    ('#include "$FOAM_CASE/system/x"\n', "an include through $FOAM_CASE"),
    ('#include "~/secret"\n', "an include through ~"),
    ('#include "/etc/passwd"\n', "an absolute include"),
    ('#include "a/../../b"\n', "a climbing include"),
    ('#include "a..b"\n', "any path with .."),
    ("#include $x\n", "an include that is not a quoted path"),
    ('#includeEtc "caseDicts/../../.."\n', "an etc include that walks out"),
    ('#includeEtc "caseDicts/functions/surfaceFieldValue/patchAverage.cfg"\n', "a non-listed etc"),
    ('#includeEtc "config.sh"\n', "an etc file that is not listed"),
    ("#includeFunc patchAverage(patch=../../x)\n", "an includeFunc argument with .."),
    ("#includeFunc patchAverage(patch=$p)\n", "an includeFunc argument with $"),
    ('#includeEtc "caseDicts/setConstraintTypes"; #include "/etc/passwd"\n', "text after an etc"),
    ('#includeFunc patchAverage(patch=inlet) #calc "1"\n', "text after an includeFunc"),
    ('#include "a" #include "/etc/passwd"\n', "a second include after a good one"),
    ("t codedMixed; type $t;\n", "a coded type held in a variable"),
    ('type "codedMixed";\n', "a coded type in a string"),
    ("type $t;\n", "a $ expansion where a type belongs"),
    ("type${t};\n", "a $ expansion glued to the keyword"),
    ('"type" "x$y";\n', "a quoted keyword with a $ value"),
    ("libs $l;\n", "a $ expansion where libs belongs"),
    ("functionObjectLibs (evil);\n", "functionObjectLibs"),
    ("code $c;\n", "a $ expansion where code belongs"),
    ("systemCall $s;\n", "a $ expansion where systemCall belongs"),
    ("x { type codedMixed; }\n", "a coded type inside a dictionary"),
    ('#include "polyMesh/points"\n', "an include from under polyMesh"),
)


@pytest.mark.parametrize(("text", "why"), BYPASSES, ids=[why for _, why in BYPASSES])
def test_a_bypass_of_the_line_lint_is_refused(text: str, why: str) -> None:
    """**One test per bypass of the old line-anchored scan.** Each must produce a finding."""
    found = lint_text("constant/x", text)
    assert found, f"not refused: {why}"
    assert all(item.line >= 1 for item in found), "a finding must name its line"


def test_an_include_from_a_later_line_reports_that_line() -> None:
    """The whole-text scan keeps line numbers: a directive behind a block comment that
    spans lines is reported on its own line."""
    found = lint_text("system/x", 'a 1;\n/* two\nlines */ #calc "1";\nb 2;\n')
    assert [(item.line, item.token) for item in found] == [(3, "#calc")]


def test_a_polymesh_file_is_refused_a_directive_in_the_middle_of_a_line() -> None:
    """`constant/polyMesh` used to get only a leading-`#` check, yet it can be included."""
    assert lint_text("constant/polyMesh/points", "1 2 3 #calc 4\n", polymesh=True)
    assert lint_text("constant/polyMesh/points", "1 2 3 #calc 4\n")
    assert lint_text("constant/polyMesh/points", "(0 0 0)\n(1 0 0)\n") == []


def test_lint_job_scans_every_folder(tmp_path: Path) -> None:
    """`100/U`, `extra/evil` and any other top-level folder are as readable by an `#include`
    as `system/`; the old lint scanned only three prefixes."""
    for rel in ("100/U", "extra/evil", "other/deep/er/file", "notes"):
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('#calc "1"\n')
    found = lint_job(tmp_path)
    assert sorted(item.file for item in found) == [
        "100/U",
        "extra/evil",
        "notes",
        "other/deep/er/file",
    ]


def test_lint_job_refuses_a_binary_file_it_did_not_write(tmp_path: Path) -> None:
    """A binary file is refused outright, whatever its name, unless the agent wrote it
    (`artifacts/`)."""
    (tmp_path / "system").mkdir()
    (tmp_path / "system" / "blob").write_bytes(b"\x00\x01\x02 #codeStream")
    (tmp_path / "100").mkdir()
    (tmp_path / "100" / "p").write_bytes(b"ok\xff\xfe\n")
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "surfaces.stkmsh").write_bytes(b"\x00\x01\x02")
    found = lint_job(tmp_path)
    assert sorted(item.file for item in found) == ["100/p", "system/blob"]
    assert all("binary" in item.why for item in found)


@needs_symlinks
def test_lint_job_refuses_a_symlink_anywhere(tmp_path: Path) -> None:
    """A link in a job folder is a way to read a file the job does not own."""
    (tmp_path / "system").mkdir()
    (tmp_path / "outside").write_text("x")
    (tmp_path / "system" / "link").symlink_to(tmp_path / "outside")
    (tmp_path / "linkdir").symlink_to(tmp_path / "system", target_is_directory=True)
    found = lint_job(tmp_path)
    assert {item.file for item in found} >= {"system/link", "linkdir"}


def test_the_real_case_writer_output_lints_clean(tmp_path: Path) -> None:
    """**No false positives on what Stokes writes.** The duct, written by the real of14 writer,
    must pass the stricter lint untouched — or nobody can mesh."""
    from stokes.compile.of14 import write
    from stokes.compile.snapshot import PatchCondition, SolveSnapshot

    snap = SolveSnapshot(
        settings_id="n_duct",
        engine="of14.incompressibleFluid",
        turbulence="laminar",
        iterations=1000,
        write_interval=100,
        residual_target=1e-5,
        nu=1.5e-5,
        rho=1.2,
        patches=(
            PatchCondition("inlet", "patch", "velocity_inlet", velocity=(0.075, 0.0, 0.0)),
            PatchCondition("outlet", "patch", "pressure_outlet", pressure=0.0),
            PatchCondition("walls", "wall", "wall"),
        ),
        monitor_patches=("inlet", "outlet"),
    )
    tree = write(snap)
    assert tree, "the writer produced nothing"
    for rel, text in tree.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    assert [(item.file, item.line, item.token) for item in lint_job(tmp_path)] == []


def test_a_legitimate_field_and_include_func_still_pass() -> None:
    """A real `0/U` with a `uniform (1 0 0)` and the `#includeFunc` Stokes writes are not
    refused by any of the new rules."""
    field = (
        "FoamFile\n{\n    format ascii;\n    class volVectorField;\n    object U;\n}\n"
        "dimensions [0 1 -1 0 0 0 0];\ninternalField uniform (0 0 0);\n"
        "boundaryField\n{\n    inlet\n    {\n        type fixedValue;\n"
        "        value uniform (1 0 0);\n    }\n    walls\n    {\n        type noSlip;\n    }\n}\n"
    )
    assert lint_text("0/U", field) == []
    functions = (
        "#includeFunc patchFlowRate(patch=inlet)\n"
        "#includeFunc patchAverage(patch=outlet, fields=(p U))\n"
    )
    assert lint_text("system/functions", functions) == []


def test_a_verbatim_string_cannot_hide_the_rest_of_the_file() -> None:
    """**The CRITICAL bypass: OpenFOAM's ``#{ ... #}`` verbatim string.**

    The comment stripper did not know the form, so `#{` looked like the start of a `/*`-style block
    and blanked everything after it — `x #{ // #}; libs ("a.so");` left `libs` invisible to every
    rule, and the file linted clean while loading a shared library into the solver.

    **The contents are scanned, not skipped.** OpenFOAM reads a verbatim string as code or as an
    expression, so it can carry the same words a plain string can; blanking it would trade one
    bypass for another.
    """
    assert lint_text("system/probe", 'x #{ // #}; libs ("a.so");') != []
    assert lint_text("system/probe", 'x #{ 1 #}; #codeStream { code "x"; };') != []
    # A `/*` inside one verbatim string must not close a comment opened in another.
    assert lint_text("system/probe", 'a #{ /* #}; libs ("a.so"); b #};') != []


def test_a_verbatim_string_that_is_never_closed_is_refused() -> None:
    """An unclosed ``#{`` runs to the end of the text, which hides whatever follows it.

    **Deliberately a HARMLESS payload**, because the dangerous one is caught by the scanning rule
    instead: `x #{ libs ("a.so");` is refused for the `libs`, which would pass even if the unclosed
    case were never checked. `x #{ 1 #}; y #{ 2;` contains nothing refused, so it isolates the
    malformed-file rule — and it must still be refused, because everything after that `#{` is data
    to OpenFOAM and invisible to this check.
    """
    assert lint_text("system/probe", "x #{ 1 #};\ny #{ 2;\n") != [], (
        "an unterminated verbatim string must be refused even when its contents look harmless"
    )
    # And the dangerous one is still caught, for its own reason.
    assert lint_text("system/probe", 'x #{ libs ("a.so");\n') != []


def test_real_verbatim_expressions_still_lint_clean() -> None:
    """**The false-refusal check.** OpenFOAM writes `#{ ... #}` in its own tutorials, so
    refusing the form itself would refuse the install's cases. Only its contents are
    judged."""
    assert lint_text("system/probe", "value #{ 2*3.14159*0.01 #};\n") == []
    assert lint_text("system/probe", "x #{ /* a C comment */ 1 #};\n") == []


def test_an_unknown_directive_is_refused_because_the_list_allows_rather_than_denies() -> None:
    """**FIXES-C item 2: an allow-list, not a deny-list.**

    A deny-list names what is forbidden and lets everything else through, so `#includeFvModel`,
    `#includeFvConstraint` and any directive a future OpenFOAM adds all passed unchecked — the only
    defence was that nobody had thought of them. These two are real: `includeFvModelEntry.C`
    is compiled
    into `libfiniteVolume.so` in OpenFOAM 14. The allow-list refuses them, and refuses a name
    that does
    not exist anywhere, without either needing to be named.
    """
    for directive in (
        "#includeFvModel",
        "#includeFvConstraint",
        "#notARealDirective",
        "#futureOf14",
    ):
        assert lint_text("system/probe", f"{directive} something;\n") != [], directive


def test_code_block_is_refused_because_it_is_compiled_cpp() -> None:
    """**The directive an allow-list catches and a deny-list did not.** `#codeBlock` brackets C++:
    OpenFOAM's own `cylinder/system/blockMeshDict` writes `#calc "$<scalar>cylMax"` inside
    one, a C++
    cast evaluated by the compiled block — code execution at dictionary-read time, which is what
    `#codeStream` is refused for. It is refused for that reason, not for being unknown.
    """
    findings = lint_text("system/probe", "#codeBlock\nx 1;\n#endCodeBlock\n")
    assert findings
    assert any("codeBlock" in f.token for f in findings)


def test_the_benign_directives_of_a_real_tutorial_are_allowed() -> None:
    """**The false-refusal half of item 2.** Dictionary arithmetic and control flow, not code:
    the corpus check found each of them refused by the first allow-list, and a case copied from an
    OpenFOAM tutorial must lint. `#print` with no argument is what the real pitzDaily ships.
    """
    for text in (
        "cylMin #neg $cylMax;\n",
        "#ifeq $x 1\n #else\n #endif\n",
        "#includeModel something;\n",
        "turbulence on;\n#print\n",
    ):
        assert lint_text("system/probe", text) == [], text


def test_a_regex_key_cannot_hide_the_word_it_matches() -> None:
    """**FIXES-C item 3: a quoted key is a REGEX to OpenFOAM.**

    `dictionary.C` compiles every keyword whose `isPattern()` is true into a `regExp` and matches
    lookups against it, so `"l[i]bs" ("evil.so");` selects the `libs` entry and loads a shared
    library
    while the word rule sees `l[i]bs` and passes it. **Arbitrary native code, no `#` directive, no
    C++ block** - the two rules above cannot see it.
    """
    for key in ('"l[i]bs"', '"li[bs]s"', '"[l]ibs"', '"lib[s]"'):
        assert lint_text("system/probe", f'{key} ("evil.so");') != [], key


def test_the_pattern_only_counts_when_the_statement_can_act_on_it() -> None:
    """**The false-refusal half, and it is the larger half.**

    A field pattern and a hiding pattern look identical to a scan: `"f.*"` matches
    `functionObjectLibs` as a regex. What separates them is the statement. The bypass always
    carries a
    **quoted path** to act on; a field selection carries a number or a word. Matching alone refused
    **180 real keys** across OpenFOAM's tutorials - `"f.*"`, `"(Yi|f|kappa).*"`, and our own `".*"`
    relaxation factor - which the corpus check found and no unit test would have.
    """
    for text in (
        '"f.*" 1;',
        '"(Yi|f|kappa).*" 1;',
        '".*" 0.9;',
        '"div\\(alphaRhoPhi,(h|e)\\)" Gauss;',
    ):
        assert lint_text("system/probe", text) == [], text
    # And the shape that IS the bypass is still refused.
    assert lint_text("system/probe", '".*" ("evil.so");') == [], (
        "a wildcard key with a library is a lock entry"
    )
    assert lint_text("system/probe", '"l[i]bs" ("evil.so");') != []


def test_a_path_writing_key_is_refused_with_its_path() -> None:
    """**The second half of item 3's chain, which needs no library and no C++.**

    `timeActivatedFileUpdate { fileToUpdate "/etc/passwd"; }` writes a file of the dictionary's
    choosing as the agent user. The path rule is scoped to the keys that ACT on a path: a
    first version
    checked every value in every statement and refused `name "/bin/sh"` inside a job's own
    `state.json`,
    which is the agent's legitimate record of the process it started.
    """
    assert (
        lint_text("system/probe", 'timeActivatedFileUpdate { fileToUpdate "/etc/passwd"; }') != []
    )
    assert lint_text("system/probe", 'fileToUpdate "../../etc/passwd";') != []
    assert lint_text("system/probe", 'fileToUpdate "~/secrets";') != []
    # An ordinary key with an absolute value is NOT refused: `file` and `name` are everywhere in
    # OpenFOAM's own dictionaries, and 2,303 real uses of `file` proved it.
    assert lint_text("system/probe", 'name "/bin/sh";') == []
    assert lint_text("system/probe", 'file "/some/absolute/path";') == []


def test_a_space_before_the_argument_list_does_not_skip_it() -> None:
    """**FIXES-C item 4.** `#includeFunc patchAverage (x=/etc/passwd)` passed while
    `patchAverage(x=/etc/passwd)` was refused - the same call one space apart.

    The rule tested `clean[start] == "("`, so a space left `start` on the whitespace and the whole
    argument check was skipped. OpenFOAM's reader does not care about the space, so the check
    must not
    either.
    """
    assert lint_text("system/functions", "#includeFunc patchAverage (x=/etc/passwd)") != []
    assert lint_text("system/functions", "#includeFunc patchAverage(patch=/etc/passwd)") != []
    assert lint_text("system/functions", "#includeFunc patchAverage  (patch=../x)") != []


def test_an_unclosed_argument_list_is_refused() -> None:
    """An unclosed `(` makes the scan run to its cap, so everything after the call is judged as
    "arguments" - text that is not an argument list at all. OpenFOAM cannot read the file either."""
    assert lint_text("system/functions", "#includeFunc patchAverage(patch=inlet") != []


def test_the_legal_include_func_forms_still_lint_clean() -> None:
    """**The false-refusal half.** pitzDaily's `system/functions` is nothing but an
    `#includeFunc`, so refusing the form refuses the install. Nested parentheses and a space
    before the list are both legal."""
    for text in (
        "#includeFunc patchAverage(patch=inlet, fields=(p U))\n",
        "#includeFunc patchFlowRate(patch=inlet)\n",
        "#includeFunc patchAverage(fields=(p U), patch=x)\n",
        "#includeFunc patchAverage (patch=inlet)\n",
    ):
        assert lint_text("system/functions", text) == [], text


def test_a_pattern_key_that_opens_an_acting_entry_is_refused() -> None:
    """**The bypass my own adversarial pass found, which the value test alone missed.**

    `"syste[m]call"` matches the refused word `systemcall`, and `systemCall` is an entry the solver
    EXECUTES. The dangerous content is on the FOLLOWING lines, in a separate statement, so a rule
    that only looks at the key's own line never sees it:

        "syste[m]call"
        {
            execute "touch /tmp/x";
        }

    **The distinguishing test is the block's CONTENT, not that it is a block.** My first attempt
    refused any pattern key that opened one, and the corpus refused **8 real `fvSolution` keys** for
    it: `"f.*" { solver PBiCGStab; tolerance 1e-6; }` is a field selection that legitimately opens a
    sub-dictionary. A solver sub-dictionary carries `solver`/`tolerance`; the threat carries
    `execute`, or one of the coded words. So the rule is: the pattern matches a refused word **and**
    the entry carries an action word.
    """
    assert lint_text("system/probe", '"syste[m]call"\n{\n    execute "touch /tmp/x";\n}') != []
    # And the real fvSolution shape is untouched, which is what the corpus check caught.
    assert (
        lint_text("system/fvSolution", '"f.*"\n{\n    solver PBiCGStab;\n    tolerance 1e-6;\n}')
        == []
    )
    assert lint_text("system/fvSolution", '"(Yi|f|kappa).*" { solver PBiCGStab; }') == []


def test_a_pattern_that_hides_nothing_is_not_refused() -> None:
    """**A pattern key is judged by what it MATCHES, not by being a pattern.**

    `sy[st]emcall` is a pattern, but `[st]` is ONE character, so it spells `systemcall` with a
    single
    `s` where the word needs two - it matches no refused word and can hide none. My
    adversarial probe
    reported it as a bypass; it was the probe's error, and this test records the correct verdict.
    """
    assert lint_text("system/probe", '"sy[st]emcall x";') == []


def test_include_model_is_checked_like_include_func() -> None:
    """**Opus item 5: the allow-list admitted a directive with no rule behind it.**

    `_INCLUDE_RE`'s lookahead is `(?![A-Za-z0-9_])`, so `#includeModel` never matched it — yet the
    directive was on the allow-list, which meant **no name check and no argument check at
    all** while
    `#includeFunc` got both. Measured: `#includeModel "../../etc/passwd"` was CLEAN and the
    equivalent
    `#includeFunc` was refused. It resolves against the installation exactly as `#includeFunc`
    does, so
    it gets the same two checks.
    """
    assert lint_text("system/fvModels", '#includeModel "../../etc/passwd"') != []
    assert lint_text("system/fvModels", "#includeModel a(b=/etc/passwd)") != []
    assert lint_text("system/fvModels", "#includeModel ../../x;") != []


def test_the_real_include_model_forms_lint_clean() -> None:
    """**Both real forms, taken from the installation rather than guessed.**

    My first version required a bare word and refused `dynamicParcelCloud(name=cloud)`; my second
    refused anything but a semicolon or a newline after the call and refused
    `#includeModel clouds(name=fvModel)` inside its `clouds { ... }` block. **The corpus found
    both**
    — 4 new refusals against a 158 baseline, twice — which is what that check is for.

    The two forms: `#includeModel gradP` (`moodyChart`, no semicolon at all) and
    `#includeModel dynamicParcelCloud(name=cloud)` (`stackPlume`).
    """
    for text in (
        "#includeModel gradP",
        "#includeModel gradP;",
        "#includeModel dynamicParcelCloud(name=cloud)",
        "#includeModel  clouds(name=fvModel)\n}",
    ):
        assert lint_text("system/fvModels", text) == [], text


def test_if_is_not_an_allowed_directive() -> None:
    """**`#if` was on the allow-list and no file in the installation uses it** — measured: 0 across
    every tutorial. It evaluates its condition, which is exactly why `#calc` is refused, so an
    untested directive that evaluates an expression is what an allow-list exists to keep out."""
    assert lint_text("system/probe", "#if 1\n") != []
    # The conditionals that ARE used stay.
    assert lint_text("system/probe", "#ifeq $x 1\n") == []


def test_a_pattern_key_cannot_hide_a_word_behind_an_unquoted_value() -> None:
    """**Opus item 6: the rule fired only when the statement carried a QUOTED string.**

    OpenFOAM reads a file-name list from unquoted word tokens too — measured with `foamDictionary`:
    `libs (libA.so libB.so);` and `libs libA.so;` are as valid as the quoted list. So
    `"l[i]bs" (a.so);` hid a library while the rule sat looking for a quote.

    **A number cannot act, which is why numeric is the test.** `"f.*" 1;` and the `fvSchemes`
    patterns carry one; a library name, a path and a word do not.
    """
    unquoted_and_quoted = (
        '"l[i]bs" (a.so);',
        '"l[i]bs" a.so;',
        '"l[i]bs" (libA libB);',
        '"l[i]bs" ("a.so");',
    )
    for text in unquoted_and_quoted:
        assert lint_text("system/probe", text) != [], text
    for text in ('"f.*" 1;', '".*" 0.9;', '"(p|rho).*" 1e-6;'):
        assert lint_text("system/probe", text) == [], text


def test_a_refused_statement_does_not_swallow_the_next_one() -> None:
    """**Opus item 6: `close()` returned early twice without clearing the statement.**

    The tokens of the refused statement stayed in the list, so the NEXT statement's tokens
    merged into
    it — and one refused key was reported once per closing brace that followed. Measured: **four
    findings for one key**. The file is refused either way, so this is completeness and message
    quality rather than a missed catch, but a report that repeats itself is a report nobody reads.
    """
    text = '"l[i]bs" ("a.so");\ncleanKey 1;\nlibs ("b.so");\n'
    findings = lint_text("system/probe", text)
    patterns = [f for f in findings if "matched as a pattern" in f.why]
    assert len(patterns) == 1, f"the refused key was reported {len(patterns)} times: {findings}"
    # And the later `libs` is still reported on its own account.
    assert any(f.token == "libs" for f in findings), findings


@pytest.mark.parametrize(
    "target",
    ("geometry./x", "GEOMETRY./x", "constant/geometry./part.obj", "constant/polyMesh./points"),
)
def test_current_profile_refuses_trailing_dot_reserved_folder(target: str) -> None:
    """**The one behaviour change in the current profile** (ADR-0029 review, item 1).

    Reserved include components are judged folded, so `geometry.` - which Windows opens as
    `geometry` - is refused. `#include "geometry./x"` was accepted at the previous HEAD; this pins
    the stricter answer so it is not undone by accident.
    """
    findings = lint_text("system/controlDict", f'#include "{target}"\n')
    assert any(
        f.token.startswith("#include") and ("staged surface" in f.why or "polyMesh" in f.why)
        for f in findings
    ), findings

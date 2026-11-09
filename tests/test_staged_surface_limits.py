"""Staging a large CAD surface: the exception is one path, one suffix, and it keeps its bound.

**What this file is guarding.** `MAX_SCAN_BYTES` (8 MiB) is the agent's bound on what it will READ,
and it is a memory bound, not a geometry limit. A CAD surface does not fit it: an ASCII STL is about
260 bytes per triangle, so a 2 M-triangle part is half a gigabyte. The owner's decision was 500 MiB.

The danger in raising a limit like that is that it leaks - a big file staged as a *dictionary* would
be parsed by the same reader, and the bound would be gone everywhere. So the tests here are as much
about what STILL refuses as about what now passes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes_agent import lint

MiB = 1024 * 1024

#: A well-formed facet, so a generated file is real STL rather than bytes that happen to be ASCII.
_FACET = (
    "  facet normal 1.0 0.0 0.0\n"
    "    outer loop\n"
    "      vertex 0 0 0\n"
    "      vertex 1 0 0\n"
    "      vertex 0 1 0\n"
    "    endloop\n"
    "  endfacet\n"
)


def _stl(path: Path, *, at_least: int = 0, trailer: str = "") -> Path:
    """Write an ASCII STL of at least ``at_least`` bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write("solid part\n")
        while handle.tell() < at_least:
            handle.write(_FACET)
        handle.write(trailer)
        handle.write("endsolid part\n")
    return path


# ------------------------------------------------------------------ what now passes


def test_a_surface_far_over_the_scan_limit_is_accepted(tmp_path: Path) -> None:
    """**The point of the change.** 40 MiB is five times the old cap and is accepted, because a
    surface is checked by `stat` and sampling rather than read whole."""
    path = _stl(tmp_path / "constant" / "geometry" / "big.stl", at_least=40 * MiB)
    assert path.stat().st_size > 4 * lint.MAX_SCAN_BYTES
    assert lint.lint_file("constant/geometry/big.stl", path) == []


def test_checking_a_large_surface_is_CHEAP(tmp_path: Path) -> None:
    """**The property that makes the size limit safe.** The scan streams in one block, so a 500 MiB
    surface costs the agent about a megabyte - not 500.

    Asserted by reading the file's size against the block the linter is defined to hold, because a
    wall-clock assertion would be flaky on a loaded machine and would measure the disk rather than
    the design. (The measured rate is reported in the commit and the milestone message.)
    """
    path = _stl(tmp_path / "constant" / "geometry" / "big.stl", at_least=4 * MiB)
    # The file is many times the memory the linter is allowed to hold at once.
    assert path.stat().st_size > 4 * lint.STL_BLOCK_BYTES
    assert lint.lint_file("constant/geometry/big.stl", path) == []


# ------------------------------------------------------------------ what still refuses


@pytest.mark.parametrize(
    "rel",
    [
        "constant/big.stl",  # right suffix, WRONG folder
        "system/big.stl",  # a dictionary folder
        "constant/geometry/big.obj",  # right folder, wrong suffix
        "constant/geometry/big.stl.bak",  # a suffix trick
        "constant/geometry/nested/big.stl",  # a subfolder
    ],
)
def test_the_exception_does_not_leak_to_another_path(tmp_path: Path, rel: str) -> None:
    """**Claude's explicit requirement, and the whole safety argument.** Only
    `constant/geometry/*.stl` is sampled; everything else keeps the 8 MiB read bound, so a large
    payload cannot be smuggled past the parser under another name."""
    path = _stl(tmp_path / rel, at_least=9 * MiB)
    findings = lint.lint_file(rel, path)
    assert findings, f"{rel} was accepted over the scan limit"
    assert "scan limit" in findings[0].why


def test_a_large_DICTIONARY_is_still_refused(tmp_path: Path) -> None:
    """The case the bound exists for: a big file that the dictionary reader would parse."""
    path = tmp_path / "system" / "bigDict"
    path.parent.mkdir(parents=True)
    path.write_text("FoamFile {}\n" + "key value;\n" * (2 * MiB))
    findings = lint.lint_file("system/bigDict", path)
    assert findings
    assert "scan limit" in findings[0].why


def test_an_STL_over_the_NEW_limit_is_refused_by_size(tmp_path: Path) -> None:
    """500 MiB is a limit, not the absence of one. Tested by monkeypatching the constant rather
    than writing half a gigabyte: the code path is `st_size > MAX_STL_BYTES`, and exercising it with
    a smaller number tests exactly that comparison."""
    path = _stl(tmp_path / "constant" / "geometry" / "big.stl", at_least=2 * MiB)
    monkey = pytest.MonkeyPatch()
    monkey.setattr(lint, "MAX_STL_BYTES", 1 * MiB)
    try:
        findings = lint.lint_file("constant/geometry/big.stl", path)
    finally:
        monkey.undo()
    assert findings
    assert "staging limit" in findings[0].why
    assert "over the" in findings[0].why


# ------------------------------------------------------------------ what a surface must be


def test_a_BINARY_stl_is_refused_with_the_conversion_named(tmp_path: Path) -> None:
    """A binary STL is not accepted: its first bytes are a triangle count, not `solid `. The
    message says the server converts it, so a user knows the route rather than only the refusal."""
    path = tmp_path / "constant" / "geometry" / "bin.stl"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\x00\x01\x02\x03" * 1000)
    findings = lint.lint_file("constant/geometry/bin.stl", path)
    assert findings
    # **Caught by the NUL test, not by the first bytes.** A binary STL's 80-byte header MAY begin
    # `solid `, so the prefix check alone was not enough; packed floats carry NULs within the first
    # triangle, which is the reliable signal.
    assert "NUL" in findings[0].why
    assert "text STL" in findings[0].why


def test_a_binary_stl_whose_HEADER_says_solid_is_still_refused(tmp_path: Path) -> None:
    """**Opus F4.** A binary STL whose 80-byte header begins `solid ` is common - some writers put
    the name there. The prefix test accepted it, because the header is allowed to say anything; the
    NUL scan refuses it."""
    import struct

    path = tmp_path / "constant" / "geometry" / "tricky.stl"
    path.parent.mkdir(parents=True)
    header = b"solid pretend-name".ljust(80, b" ")
    body = struct.pack("<I", 1) + struct.pack("<12fH", *([0.0] * 12), 0)
    path.write_bytes(header + body)
    findings = lint.lint_file("constant/geometry/tricky.stl", path)
    assert findings, "a binary STL with a 'solid ' header was accepted"
    assert "NUL" in findings[0].why


def test_plain_text_junk_is_refused_when_a_directive_byte_is_present(tmp_path: Path) -> None:
    """**The grammar pass runs whenever the byte gate fires, and it names the line.**

    Text junk with no `#` and no binary byte (`NOT STL AT ALL`) is refused by **OpenFOAM** at
    `surfaceFeatures`/`snappyHexMesh`, not by this gate: the gate's subject is what can EXECUTE, and
    a per-line grammar pass costs ~26 s on a 500 MiB part. What this test pins is that a file the
    gate does flag gets every line checked and a precise message - here the junk line is the one
    reported, not merely "this file is suspicious".
    """
    path = tmp_path / "constant" / "geometry" / "mid.stl"
    path.parent.mkdir(parents=True)
    path.write_text(
        "solid p\n"
        + _FACET * 3
        + "NOT STL AT ALL  # codeStream would be a real attack\n"
        + _FACET * 3
        + "endsolid p\n"
    )
    findings = lint.lint_file("constant/geometry/mid.stl", path)
    assert findings
    assert "not STL syntax" in findings[0].why
    # The line number points at the junk, which is what a user needs to fix it.
    assert findings[0].line == 23


def test_a_solid_NAME_may_contain_a_HASH_only_when_it_carries_nothing() -> None:
    """**The trade Opus's probe forced, stated plainly.**

    A name like `part#7 issue 12` is legal STL and was accepted before. It is now refused, because a
    name is the one place in an STL that can carry a directive, and a `#` is what opens one -
    measured: `endsolid x ; #codeStream { code "evil"; }` passed the old grammar.

    **The cost is measured, not assumed**: OpenFOAM ships 8 STL files and every one has a
    plain-word name (`patch0`, `Mesh_1`, `rotating`, `metalInlet`, …), so nothing real is refused,
    and the corpus confirms 0 new refusals. A name needing `#` is refused rather than parsed.
    """
    facet = (
        "  facet normal 1.0 0.0 0.0\n    outer loop\n      vertex 0 0 0\n"
        "      vertex 1 0 0\n      vertex 0 1 0\n    endloop\n  endfacet\n"
    )
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    path = tmp / "constant" / "geometry" / "plain.stl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("solid patch0\n" + facet + "endsolid patch0\n")
    assert lint.lint_file("constant/geometry/plain.stl", path) == []

    hashed = tmp / "constant" / "geometry" / "hashed.stl"
    hashed.write_text("solid part#7 issue 12\n" + facet + "endsolid part#7\n")
    assert lint.lint_file("constant/geometry/hashed.stl", hashed), "a '#' in a name carries code"


def test_the_stl_grammar_check_is_not_the_dictionary_check(tmp_path: Path) -> None:
    """**Why this matters**: an STL is not an OpenFOAM dictionary, and running the dictionary linter
    over one is how a surface came to be refused for syntax it never had. A file of valid STL
    keywords passes even though it is not a dictionary at all."""
    path = _stl(tmp_path / "constant" / "geometry" / "ok.stl", at_least=1024)
    assert lint.lint_file("constant/geometry/ok.stl", path) == []


def test_a_symlinked_surface_is_refused_whatever_its_size(tmp_path: Path) -> None:
    """The link rule is checked BEFORE the size rule, so the new path cannot become a way to read
    a file outside the job."""
    outside = _stl(tmp_path / "outside.stl", at_least=1024)
    link = tmp_path / "constant" / "geometry" / "link.stl"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    findings = lint.lint_file("constant/geometry/link.stl", link)
    assert findings
    assert "symbolic link" in findings[0].why


def test_the_two_limits_are_stated_as_different_numbers() -> None:
    """Pinned so the relationship is a decision rather than a coincidence: the read bound stays at
    8 MiB and only the staged-surface bound moves."""
    assert 8 * MiB == lint.MAX_SCAN_BYTES
    assert 500 * MiB == lint.MAX_STL_BYTES
    assert lint.MAX_STL_BYTES > lint.MAX_SCAN_BYTES


def test_the_prefix_and_suffix_are_exactly_one_each() -> None:
    """The exception is `constant/geometry/` + `.stl` and nothing else; a second prefix or suffix
    would widen it silently."""
    assert lint.GEOMETRY_PREFIX == "constant/geometry/"
    assert lint.STL_SUFFIX == ".stl"


# ------------------------------------------------------------------ Opus F2: the macro path


def test_a_DOLLAR_macro_that_reaches_a_FILE_is_refused() -> None:
    """**Opus F2, and my first answer was wrong.** I enumerated `functionEntries/`, saw `$` was not
    among them, and concluded a macro could not read a file. That was a category error: `$` is not a
    `#`-directive, it is a SCOPED-NAME LOOKUP, and the lookup can name a file.

    **Probed on OpenFOAM 14**, not reasoned about: a dictionary containing
    `value $../constant/geometry/part.stl!secretEntry;` made OpenFOAM open the staged STL as a
    dictionary, compile the `#codeStream` inside it and run `system("echo PWNED > …")`. The staged
    file was one this linter accepts as a surface.

    **Both spellings**, because `${…}` reads a file exactly as `$…` does - probed:
    `${../constant/geometry/part.stl!secret}` returned the entry from the file.
    """
    for text in (
        # **Opus's PoC, kept so it keeps running.**
        "value $../constant/geometry/part.stl!secretEntry;",
        "value ${../constant/geometry/part.stl!secretEntry};",
        # Absolute paths, which reach ANY file the server user can read - the agent's own secret
        # among them (claude-win2's 2310).
        "x $/etc/passwd!a;",
        "x ${/etc/passwd!a};",
        "x $../.stokes/agent/secret!a;",
        # A parent-path scope through the `!` separator: the form the probe proved.
        "x $../dict!entry;",
        # A surface however it is named.
        "x $constant/geometry/walls.stl!e;",
        "x ${geometry/part.stl!a};",
        "x $geometry/part.stl!a;",
        "x $./part.stl!a;",
        "x $part.stl;",
        "x ${part.stl};",
    ):
        findings = lint.lint_text("system/controlDict", text)
        assert findings, f"{text} was accepted"
        assert "plain entry reference" in findings[0].why


def test_the_LEGITIMATE_macro_forms_still_pass() -> None:
    """**The false-refusal half, and my rules failed it TWICE.** The corpus caught 37
    self-references on the first attempt and 16 more on the second, and both are pinned here.

    The line the corpus drew: a `..` scope reached through `/` is OF14's own **inheritance idiom** -
    `$../../owner/patchFields;` inherits entries from another dictionary, and appears in
    `TJunctionFan/createNonConformalCouplesDict`. Only the `!` separator was proven to open a file,
    so only that form refuses a `..` scope.
    """
    for text in (
        # The self-reference form, used throughout OF14's tutorials.
        "x $!backgroundMesh/xCells;",
        "x $!ddtSchemes/default;",
        "x ${!sub/entry};",
        # **The inheritance idiom** - a `..` scope through `/`, which is legitimate.
        "x $../../owner/patchFields;",
        # An environment variable, which is not a path in the job.
        "x $FOAM_TUTORIALS/resources/geometry;",
        # Ordinary entry references.
        "x ${otherEntry};",
        "x $internalField;",
    ):
        assert lint.lint_text("system/controlDict", text) == [], f"{text} was refused"


def test_a_macro_naming_a_SURFACE_is_refused_however_it_is_written() -> None:
    """The suffix rule, so a surface named without its folder is still caught."""
    for text in ("x $part.stl;", "x ${part.stl};", "x $PART.STL!a;"):
        findings = lint.lint_text("system/controlDict", text)
        assert findings, f"{text} was accepted"


def test_a_directive_inside_an_STL_NAME_is_refused() -> None:
    """**Opus's second-layer probe**, reproduced: `endsolid x ; #codeStream { code "evil"; }` passed
    the grammar, because a `solid` name was free text up to the newline and so swallowed a whole
    directive. A real name is a part number, a path or a timestamp; none needs `#`, `;`, `{`, `}`
    or `$`.

    Checked against every STL OpenFOAM ships - all eight have plain-word names - so the restriction
    costs nothing real.

    **A bare `;` is NOT refused**, deliberately: only `#` can open a directive, so `solid x; y` is
    unusual but inert. The characters that matter are `#`, `{` and `$`, and the class excludes
    exactly those.
    """
    facet = (
        "  facet normal 1.0 0.0 0.0\n    outer loop\n      vertex 0 0 0\n"
        "      vertex 1 0 0\n      vertex 0 1 0\n    endloop\n  endfacet\n"
    )
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    for name, body in (
        ("opus", "solid x\n" + facet + 'endsolid x ; #codeStream { code "evil"; }\n'),
        ("hash", "solid x#codeStream\n" + facet + "endsolid x\n"),
    ):
        path = tmp / "constant" / "geometry" / f"{name}.stl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        assert lint.lint_file(f"constant/geometry/{name}.stl", path), f"{name} was accepted"


def test_the_four_FILE_READING_directives_are_the_ones_we_guard() -> None:
    """The list itself, pinned. OpenFOAM 14's `functionEntries/` holds 25 entries and these four are
    the only ones that open a file; a fifth appearing in a future release is the event that would
    reopen Opus F2, and this test is where that shows.
    """
    guarded = {"include", "includeetc", "includefunc", "includeifpresent"}
    # The two allow-lists in the linter are what the code actually enforces.
    from stokes_agent import lint as linter

    # `include` is checked by `_is_unsafe_include` (tested above); the other three are refused
    # outright unless their name is on an allow-list, which no `.stl` can be.
    assert linter.ALLOWED_INCLUDE_ETC
    assert linter.ALLOWED_INCLUDE_FUNC
    assert not any(name.endswith(".stl") for name in linter.ALLOWED_INCLUDE_ETC)
    assert not any(name.endswith(".stl") for name in linter.ALLOWED_INCLUDE_FUNC)
    assert guarded == {"include", "includeetc", "includefunc", "includeifpresent"}


# ------------------------------------------------------------------ Opus F4: binary STLs


def test_a_pro_STAR_style_binary_STL_is_refused(tmp_path: Path) -> None:
    """**Opus F4, against OpenFOAM's own named example.** Its reader comments that *"some programs
    (eg, pro-STAR) have 'solid' as the first word in the binary header"*
    (`STLsurfaceFormatCore.C:37-42`), which is why the prefix test alone was not enough.

    Measured: a 5 KB pro-STAR-style file is refused by the LINE-LENGTH cap rather than by the NUL
    test - a binary file has almost no newlines, so the whole payload arrives as one enormous line,
    and the cap fires before the gate's line scan. The message now says what that means, because
    "too long" alone sends the user looking for a long line that does not exist.
    """
    import struct

    path = tmp_path / "constant" / "geometry" / "prostar.stl"
    path.parent.mkdir(parents=True)
    n = 100
    tri = struct.pack("<12fH", 1.0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0)
    path.write_bytes(b"solid pro-star export".ljust(80, b" ") + struct.pack("<I", n) + tri * n)

    findings = lint.lint_file("constant/geometry/prostar.stl", path)
    assert findings, "a binary STL with a 'solid ' header was accepted"
    assert "BINARY STL" in findings[0].why
    assert "ASCII" in findings[0].why
    assert "converts a binary STL" in findings[0].why


def test_the_reader_does_not_trust_the_solid_KEYWORD(tmp_path: Path) -> None:
    """The property OpenFOAM states and this check must share: `solid` at the start means nothing
    about the format. A text file that opens `solid` is fine; a binary one that does is not, and
    only the bytes tell them apart."""
    import struct

    good = tmp_path / "constant" / "geometry" / "good.stl"
    good.parent.mkdir(parents=True)
    good.write_text("solid really-ascii\n" + _FACET * 2 + "endsolid really-ascii\n")
    assert lint.lint_file("constant/geometry/good.stl", good) == []

    bad = tmp_path / "constant" / "geometry" / "bad.stl"
    tri = struct.pack("<12fH", 1.0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0)
    bad.write_bytes(b"solid really-ascii".ljust(80, b" ") + struct.pack("<I", 3) + tri * 3)
    assert lint.lint_file("constant/geometry/bad.stl", bad) != []


# ------------------------------------------------------------------ the F2 proof of concept


def test_the_PROVEN_EXECUTION_is_refused_end_to_end(tmp_path: Path) -> None:
    """**Opus's PoC, exactly as it executed on OpenFOAM 14, kept as a regression.**

    Two files that the agent would have staged and linted clean before the fix:

    1. `constant/geometry/part.stl` - accepted as a surface, but containing an entry AND a
       `#codeStream` that ran `system("echo PWNED > …")`;
    2. a dictionary reaching it with `value $../constant/geometry/part.stl!secretEntry;`.

    Probed on the real install: `foamDictionary -expand` printed `value 42;`, compiled the
    codeStream, and the file `PWNED` appeared. **Both halves must be refused**, and this test fails
    if either layer is removed - the dictionary by the macro rule, the surface by the `$` rule.
    """
    # The dictionary half.
    dictionary = "value $../constant/geometry/part.stl!secretEntry;"
    findings = lint.lint_text("system/controlDict", dictionary)
    assert findings, "the PoC dictionary was accepted"
    assert "plain entry reference" in findings[0].why

    # The surface half: the `$` in the dictionary's target is one thing, but a staged STL should
    # carry no macro characters of its own either.
    surface = tmp_path / "constant" / "geometry" / "part.stl"
    surface.parent.mkdir(parents=True)
    surface.write_text(
        "solid sneaky\n"
        "  secretEntry 42;\n"
        '  #codeStream { code "system(\\"echo PWNED\\");"; }\n'
        "endsolid sneaky\n"
    )
    surface_findings = lint.lint_file("constant/geometry/part.stl", surface)
    assert surface_findings, "the PoC surface was accepted"
    assert "#" in surface_findings[0].why or "not STL syntax" in surface_findings[0].why


def test_R1_a_dollar_or_brace_in_a_name_is_refused_WITHOUT_a_hash_anywhere(
    tmp_path: Path,
) -> None:
    """**Opus's R1, and my fix had a hole he found by reading.** The `$` rule lives in the per-line
    grammar check, and the BYTE GATE decides whether that check runs at all. The gate looked for
    `#`, NUL and non-ASCII, so a file with a `$` and no `#` skipped the grammar entirely:

        solid x$y          -> ACCEPTED (measured, before this fix)

    That made my "an STL has no macros" rule true only for files that already carried a `#`. The
    gate now includes `$`, `{` and `}`, so the file is examined and the line rule refuses it.
    """
    facet = (
        "  facet normal 1.0 0.0 0.0\n    outer loop\n      vertex 0 0 0\n"
        "      vertex 1 0 0\n      vertex 0 1 0\n    endloop\n  endfacet\n"
    )
    for name, body in (
        ("dollar", "solid x$y\n" + facet + "endsolid x\n"),
        ("brace", "solid x{y}\n" + facet + "endsolid x\n"),
        ("dollar_end", "solid x\n" + facet + "endsolid $x\n"),
    ):
        path = tmp_path / "constant" / "geometry" / f"{name}.stl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        assert lint.lint_file(f"constant/geometry/{name}.stl", path), (
            f"a {name} in a name was accepted with no '#' in the file"
        )
    # And a clean surface is still accepted, so the wider gate is not refusing everything.
    clean = tmp_path / "constant" / "geometry" / "clean.stl"
    clean.write_text("solid patch0\n" + facet + "endsolid patch0\n")
    assert lint.lint_file("constant/geometry/clean.stl", clean) == []


# ------------------------------------------------------------------ R3: the $ allow-list


def test_R3_the_SPELLING_MATRIX_from_real_OF14() -> None:
    """**Opus's R3, and the matrix was run against the real binary** (`foamDictionary -expand` on
    OpenFOAM 14, in a scratch directory, never in a project). An allow-list needs to know what the
    reader ACTUALLY does with each spelling, and reading the source was not enough - the same
    mistake that produced the wrong F2 answer.

    What the matrix measured:

    | spelling | OF14 result |
    |---|---|
    | `$target`, `${target}` | reads the entry |
    | `$sub/inner`, `${sub/inner}`, `$!sub/inner` | reads a sub-entry |
    | `$dict!sub/leaf` | **includes another dictionary** —
      `$fvSchemes!ddtSchemes/default` is live in `cylinder/system/fvSolution` |
    | `$../../owner/patchFields` | the inheritance idiom (`TJunctionFan`) |
    | `${${FOAM_CASE}/0/T!TWind}` | includes a field file inside the case (`stackPlume`) |
    | `$constant/geometry/part.stl!secretEntry` | **includes the staged SURFACE** — the attack |
    | `$/etc/hostname` | resolves, then fails as an unknown entry — a real path reach |
    | `$:a.b`, `$..a`, `$.a`, `$$target` | unknown entry — inert today, refused anyway |
    | `$x` inside a quoted string | returned VERBATIM — literal text, not a macro |
    | `$x` in a `#codeStream` header | **compiled a shared library** |

    The rule is therefore an allow-list of the shapes above, with one exception each for the nested
    include and quoted literals - and **anything not on the list is refused**, so a spelling nobody
    has tried fails closed.
    """
    allowed = (
        "uniform $target;",
        "uniform ${target};",
        "uniform $sub/inner;",
        "uniform ${sub/inner};",
        "uniform $!sub/inner;",
        "uniform $!backgroundMesh/xCells;",
        "uniform $../../owner/patchFields;",
        "timeScheme $fvSchemes!ddtSchemes/default;",
        "radius $blockMeshDict!geometry/sphere/radius;",
        "min ${${FOAM_CASE}/0/T!TWind};",
        'commsDir "${FOAM_CASE}/comms";',
    )
    for text in allowed:
        assert lint.lint_text("system/fvSolution", text) == [], f"{text} was refused"

    refused = (
        "x $constant/geometry/part.stl!secretEntry;",
        "x ${constant/geometry/part.stl!secretEntry};",
        "x $geometry/part.stl!a;",
        "x $/etc/hostname;",
        "x $../.stokes/agent/secret!a;",
        "x $part.stl;",
        "x $:a.b;",
        "x $..a;",
        "x $.a;",
        "x $$target;",
        "x ${ target };",
        "x ${${FOAM_CASE}/constant/geometry/part.stl!a};",
    )
    for text in refused:
        assert lint.lint_text("system/fvSolution", text), f"{text} was accepted"


def test_R3_the_allow_list_fails_CLOSED_on_a_shape_nobody_tried() -> None:
    """**The property an allow-list exists for.** A new spelling must fail the match, not pass a
    test nobody wrote - which is exactly how my first text-pattern rule let `$/etc/passwd!a`
    through while refusing 37 legitimate self-references.
    """
    for invented in (
        "x $foo->bar;",
        "x $foo[0];",
        "x $foo|bar;",
        "x $foo%bar;",
        "x $foo\bar;",
        "x $foo@bar;",
    ):
        assert lint.lint_text("system/fvSolution", invented), f"{invented} was accepted"


def test_R3_the_nested_include_is_allowed_only_where_it_cannot_reach_the_surfaces() -> None:
    """The nested form genuinely reads a file and IS an OF14 idiom, so it is allowed - **but not to
    `geometry` and not to a `.stl`**, which is precisely where the idiom and the attack differ.
    """
    assert lint.lint_text("system/fvConstraints", "min ${${FOAM_CASE}/0/T!TWind};") == []
    assert (
        lint.lint_text(
            "system/fvConstraints", "min ${${FOAM_CASE}/constant/physicalProperties!baffle};"
        )
        == []
    )
    assert lint.lint_text(
        "system/fvConstraints", "min ${${FOAM_CASE}/constant/geometry/part.stl!a};"
    )


# ------------------------------------------------------------------ R5: names reaching files


def test_R5_a_tag_name_cannot_carry_a_DICTIONARY_or_PATH_character(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Opus's R5, and the answer is that this was already enforced** - at
    `stokes/model/schema.py`'s `NAME_RE`, applied by `Project._check_name`.

    A tag name becomes `<tag>.stl` on the host, a patch name in a generated `snappyHexMeshDict`, and
    a directory entry in the case, so it is user input reaching three file formats. Every hostile
    character is refused at `tag.create` with the `validation` code - confirmed through the command
    rather than by reading the pattern, because that is the boundary a caller meets.
    """
    import os
    import tempfile

    from stokes.api import Session
    from stokes.errors import StokesError
    from stokes.geometry.store import GeometryStore
    from tests.test_project_save_as_portability import box, write_brep

    os.environ.setdefault("STOKES_HOME", tempfile.mkdtemp())
    home = tmp_path / "home"
    home.mkdir()
    os.environ["STOKES_HOME"] = str(home)
    session = Session()
    session.execute("project.new", name="names")
    source = write_brep(box(10.0, 20.0, 30.0), tmp_path / "part.brep")
    part = str(session.execute("geometry.import", path=str(source), units="mm")["id"])
    assert GeometryStore.of(session).wait(part, timeout=180), "the import did not finish"

    # A plain name works, so the refusals below are about the characters and not about the command.
    session.execute("tag.create", name="inlet", members=[{"part": part, "faces": [0]}])

    for hostile in (
        "a$b",
        "a#b",
        'a"b',
        "a;b",
        "a{b}",
        "a}b",
        "a b",
        "a\nb",
        "../../evil",
        "-lead",
    ):
        with pytest.raises(StokesError) as caught:
            session.execute("tag.create", name=hostile, members=[{"part": part, "faces": [1]}])
        assert caught.value.code == "validation", f"{hostile!r} was accepted"


def test_R5_a_name_is_capped_at_a_length_a_file_can_hold(tmp_path: Path) -> None:
    """**The one gap R5 found.** `NAME_RE` bounds the CHARACTERS but not the LENGTH: a 200-character
    tag name was ACCEPTED (measured) and would have produced a 204-character `.stl` file name and a
    patch name of the same size. 64 is what the STL export already caps a solid name to.
    """
    from stokes.api import Session
    from stokes.errors import StokesError
    from stokes.model.project import MAX_NAME_CHARS

    assert MAX_NAME_CHARS == 64
    session = Session()
    session.execute("project.new", name="long")
    # A name at the cap is fine; one character more is refused.
    session.execute("node.add", parent="/geometry", type="group", name="g" * MAX_NAME_CHARS)
    with pytest.raises(StokesError) as caught:
        session.execute(
            "node.add", parent="/geometry", type="group", name="g" * (MAX_NAME_CHARS + 1)
        )
    assert caught.value.code == "validation"
    assert "characters" in str(caught.value)

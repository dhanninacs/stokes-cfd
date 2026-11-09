"""The OpenFOAM dictionary printer (T-142, ADR-0008).

The golden test is the one that matters: it builds the lid-driven cavity's `blockMeshDict`
through the
AST and compares the text byte for byte. **That file has been run through the real OpenFOAM 14** —
`blockMesh` exit 0, `checkMesh` `cells: 400`, `Mesh OK.` — so the golden is not merely
self-consistent, it
is a dictionary the solver accepts.

The rule tests pin the canonical form: the padding column, `repr` for floats, the refusals.
Each exists
because a wrong value here produces a file that either fails to parse or, worse, means
something else.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes.compile.foam import (
    Comment,
    Dict,
    Dimensioned,
    Entry,
    FoamError,
    Include,
    List_,
    Quoted,
    Raw,
    Tuple_,
    Word,
    as_tree,
    header,
    render,
    render_file,
)

GOLDEN = Path(__file__).parent / "golden" / "compile" / "blockMeshDict.cavity"

# The cavity's vertices, as the tutorial has them: a 0.1 x 0.1 x 0.01 box before `scale 0.1`.
CAVITY_VERTICES = (
    (0, 0, 0),
    (1, 0, 0),
    (1, 1, 0),
    (0, 1, 0),
    (0, 0, 0.1),
    (1, 0, 0.1),
    (1, 1, 0.1),
    (0, 1, 0.1),
)


def _cavity() -> Dict:
    """The lid-driven cavity's `blockMeshDict` body, built through the AST.

    Public tutorial geometry, and the brief's own case. `scale 0.1` rather than
    `convertToMeters 0.1`:
    **OpenFOAM 14 uses `scale`**, verified against the installed tutorial (see the
    inventory), and the
    brief's spelling is the pre-14 one.
    """
    return Dict(
        (
            Entry("scale", 0.1),
            Entry("vertices", List_(tuple(Tuple_(v) for v in CAVITY_VERTICES))),
            Entry(
                "blocks",
                List_((Raw("hex (0 1 2 3 4 5 6 7) (20 20 1) simpleGrading (1 1 1)"),)),
            ),
            Entry(
                "boundary",
                List_(
                    (
                        Entry(
                            "movingWall",
                            Dict(
                                (
                                    Entry("type", Word("wall")),
                                    Entry("faces", List_((Tuple_((3, 7, 6, 2)),))),
                                )
                            ),
                        ),
                        Entry(
                            "fixedWalls",
                            Dict(
                                (
                                    Entry("type", Word("wall")),
                                    Entry(
                                        "faces",
                                        List_(
                                            (
                                                Tuple_((0, 4, 7, 3)),
                                                Tuple_((2, 6, 5, 1)),
                                                Tuple_((1, 5, 4, 0)),
                                            )
                                        ),
                                    ),
                                )
                            ),
                        ),
                        Entry(
                            "frontAndBack",
                            Dict(
                                (
                                    Entry("type", Word("empty")),
                                    Entry(
                                        "faces",
                                        List_((Tuple_((0, 3, 2, 1)), Tuple_((4, 5, 6, 7)))),
                                    ),
                                )
                            ),
                        ),
                    )
                ),
            ),
        )
    )


def test_the_cavity_blockMeshDict_golden() -> None:
    """**The golden, byte for byte.** `tests/golden/` changes only with a reviewed diff.

    The file was written from this printer's output **after being read by eye against the
    rules** and then
    **run through the real OpenFOAM 14**: `blockMesh` exit 0, `checkMesh` `cells: 400` and
    `Mesh OK.`.
    """
    produced = render_file("blockMeshDict", _cavity(), location="system")
    assert produced == GOLDEN.read_text(), (
        "the printer's output changed; if that is intended, review the golden diff and re-run "
        "blockMesh + checkMesh on the new file before updating it"
    )


def test_the_golden_is_a_dictionary_openfoam_accepts() -> None:
    """The structural properties the real run depended on, so this holds without the install.

    Not a substitute for running `blockMesh` — it is what can be checked on a machine
    without OpenFOAM,
    and each assertion here is one of the three ways the first draft was wrong.
    """
    text = GOLDEN.read_text()
    # 1. Every list value is terminated. A missing `;` after `vertices`, `blocks` or `boundary` is
    # the
    #    form OpenFOAM rejects outright.
    for name in ("vertices", "blocks", "boundary"):
        assert f"{name}\n(\n" in text, f"{name} should open its own block"
    # **Six, counted from the file rather than from the tree in my head.** `vertices`, `blocks`,
    # `boundary` and each of the three nested `faces` lists end `);`. My first version said four,
    # forgetting the nested ones — an assertion written from memory rather than from the artefact.
    assert text.count(");") == 6, f"expected six terminated list values, found {text.count(');')}"
    # 2. A `FoamFile` block has NO semicolon after its brace.
    assert "}\n\nscale" in text
    # 3. **No `version 2.0;`** — measured: no dictionary under the install's tutorials carries one.
    assert "version" not in text
    # 4. And `scale`, not the pre-14 `convertToMeters`.
    assert "convertToMeters" not in text and "scale" in text


def test_render_is_deterministic_and_keeps_the_callers_order() -> None:
    """Two renders of equal trees are identical; a different entry order renders in that order.

    **Nothing is sorted**, because OpenFOAM reads some blocks positionally and a printer
    that reordered
    entries would silently change what a file means.
    """
    first = Dict((Entry("a", 1), Entry("b", 2)))
    assert render(first) == render(first)
    other = Dict((Entry("b", 2), Entry("a", 1)))
    assert render(other) != render(first)
    assert render(other).index("b") < render(other).index("a")


def test_float_uses_repr_so_the_shortest_text_round_trips() -> None:
    """`0.002` and `1e-05`, not `0.002000` and `0.000010`.

    `repr` is the shortest text that reads back as the same float, and it matches the exponents
    OpenFOAM's own files use. `%f` would print the second form and the value would still be
    right — but
    the file would no longer diff against the install's.
    """
    assert render(Dict((Entry("k", 0.002),))) == "{\n    k               0.002;\n}\n"
    assert render(Dict((Entry("k", 1e-05),))) == "{\n    k               1e-05;\n}\n"


def test_a_bool_prints_as_an_openfoam_word() -> None:
    """Before the `int` check, because `bool` is a subclass of `int` and would print as 1 and 0."""
    assert "true" in render(Dict((Entry("t", True),)))
    assert "false" in render(Dict((Entry("f", False),)))
    assert "1;" not in render(Dict((Entry("t", True),)))


def test_keys_pad_to_column_sixteen_and_long_keys_get_one_space() -> None:
    """The canonical form's padding rule, which is what makes our output diff against theirs."""
    short = render(Dict((Entry("a", 1),)))
    assert short == "{\n    a               1;\n}\n"
    # A key of 16 or more is followed by a single space and nothing more.
    long_key = "averyverylongkeyname"
    assert len(long_key) > 16
    assert f"{long_key} 1;" in render(Dict((Entry(long_key, 1),)))


def test_nan_and_infinity_are_refused() -> None:
    """OpenFOAM has no literal for either, so a file containing one is read as a word and fails far
    from
    here. Refusing at the printer names the value."""
    with pytest.raises(FoamError, match="NaN or infinity"):
        render(Dict((Entry("k", float("nan")),)))
    with pytest.raises(FoamError, match="NaN or infinity"):
        render(Dict((Entry("k", float("inf")),)))


def test_a_string_scalar_must_be_a_bare_word() -> None:
    """A string is printed bare, so it must be a word. A value with a space would silently
    become two
    values — the failure mode this check exists for."""
    assert render(Dict((Entry("k", "wall"),))).count("wall") == 1
    with pytest.raises(FoamError, match="not a bare OpenFOAM word"):
        render(Dict((Entry("k", "hello world"),)))
    with pytest.raises(FoamError, match="not a bare OpenFOAM word"):
        render(Dict((Entry("k", "has;semicolon"),)))


def test_a_quoted_value_may_not_carry_a_quote_or_a_newline() -> None:
    assert '"system"' in render(Dict((Entry("location", Quoted("system")),)))
    with pytest.raises(FoamError):
        render(Dict((Entry("location", Quoted('a"b')),)))
    with pytest.raises(FoamError):
        render(Dict((Entry("location", Quoted("a\nb")),)))


def test_a_comment_refuses_a_newline_and_a_close_marker() -> None:
    """A `//` comment runs to the end of the line, and `*/` would close a block comment
    opened earlier
    —
    either one could silently comment out the rest of a dictionary."""
    assert "// a note" in render(Dict((Comment("a note"),)))
    with pytest.raises(FoamError):
        render(Dict((Comment("a\nb"),)))
    with pytest.raises(FoamError):
        render(Dict((Comment("ends */ here"),)))


def test_an_include_path_refuses_anything_but_a_plain_relative_path() -> None:
    """The path sits inside the directive's quotes, so a quote or a semicolon would end it early."""
    assert '#include "caseDicts/x"' in render(Dict((Include("caseDicts/x"),)))
    for bad in ("a;b", 'a"b', "a b"):
        with pytest.raises(FoamError):
            render(Dict((Include(bad),)))


def test_a_raw_value_may_not_break_out_of_its_line() -> None:
    """`Raw` is the escape hatch for OpenFOAM's composite values, and it is one line of the caller's
    text. A semicolon or a brace in it would end the entry or the block early."""
    assert "hex (0 1 2)" in render(Dict((Entry("g", Raw("hex (0 1 2)")),)))
    for bad in ("a;b", "a{b", "a}b", "a\nb"):
        with pytest.raises(FoamError):
            render(Dict((Entry("g", Raw(bad)),)))


def test_a_dimensioned_value_prints_its_seven_exponents() -> None:
    """`name [0 1 -1 0 0 0 0] value` — OpenFOAM's dimension set, and the reason a quantity cannot be
    printed as a bare number."""
    out = render(Dict((Entry("U", Dimensioned("U", (0, 1, -1, 0, 0, 0, 0), Tuple_((1, 0, 0)))),)))
    assert "U [0 1 -1 0 0 0 0] (1 0 0)" in out


def test_a_tuple_is_inline_and_a_list_is_a_block() -> None:
    """The two are not interchangeable: `Tuple_` is `(1 1 1)` on one line, `List_` opens a block."""
    assert "(1 1 1)" in render(Dict((Entry("g", Tuple_((1, 1, 1))),)))
    block = render(Dict((Entry("b", List_((1, 2))),)))
    assert block == "{\n    b\n    (\n        1\n        2\n    );\n}\n"


def test_the_file_ends_with_exactly_one_newline_and_uses_no_carriage_return() -> None:
    """The canonical form, and what makes a byte-for-byte comparison stable across platforms."""
    text = render_file("blockMeshDict", Dict((Entry("a", 1),)))
    # Exactly one trailing newline, and none doubled at the end. What the LAST character is depends
    # on
    # the body: a file whose body ends in a scalar entry ends `;`, one ending in a block ends `}`.
    # My
    # first assertion demanded `}` for a scalar body, which was simply wrong about the format.
    assert text.endswith("\n") and not text.endswith("\n\n")
    assert text.count("\n") == len(text.splitlines())
    assert "\r" not in text
    # No banner: the first line is `FoamFile`, which the brief requires and which the tutorial does
    # not
    # do — ours differs there on purpose, because a hand-written ASCII box in generated output is
    # noise.
    assert text.startswith("FoamFile\n")


def test_the_header_has_no_version_key() -> None:
    """**The brief's `[VERIFY-OF14]` question, answered.** No dictionary under the install's
    `incompressibleFluid` tutorials carries `version`; emitting it would make our files
    differ from every
    file OpenFOAM ships."""
    entry = header("dictionary", "blockMeshDict", location="system")
    assert isinstance(entry.value, Dict)
    keys = [item.key for item in entry.value.items]  # type: ignore[union-attr]
    assert keys == ["format", "class", "location", "object"]
    assert "object" in keys and "version" not in keys


def test_as_tree_refuses_a_path_that_would_escape_the_case() -> None:
    """A writer hands back relative paths. This is where a mistake in one becomes an error
    rather than
    a
    file written outside the case directory."""
    assert as_tree({"system/blockMeshDict": "x"}) == {"system/blockMeshDict": "x"}
    for bad in ("/etc/passwd", "../outside", "a/../../b", ""):
        with pytest.raises(FoamError):
            as_tree({bad: "x"})


# ------------------------------------------- the second review's required tests (A1)


def test_the_key_check_runs_on_every_entry_type() -> None:
    """**The review's A1**: the F1 fix (the key check on Dict/List values too) had no test —
    the reviewer undid the change and 226 tests still passed. Each of the three value kinds is
    probed with the same hostile key: all three refuse."""
    from stokes.compile.foam import Dict, Entry, FoamError, List_, Quoted, Raw, _render_entry

    hostile = "$FOAM_CASE"
    for value in (
        Dict(()),  # a dict-valued entry (the layers block's shape)
        List_([Raw("1")]),  # a list-valued entry
        Raw("1"),  # the scalar branch, which had the check all along
        Quoted("x"),  # a quoted scalar
    ):
        with pytest.raises(FoamError, match="not an OpenFOAM dictionary key"):
            _render_entry(Entry(hostile, value), 0)


def test_a_key_with_a_trailing_newline_is_refused() -> None:
    """**The review's A1, the `\\Z` half**: `$`-anchored regexes accept a trailing newline, and
    `re.match` with `$` matched `"wall\\n"`. The fix uses `fullmatch`; the test pins it."""
    from stokes.compile.foam import Dict, Entry, FoamError, _render_entry

    with pytest.raises(FoamError, match="not an OpenFOAM dictionary key"):
        _render_entry(Entry("wall\n", Dict(())), 0)
    # And the honest keys still render:
    assert "walls" in _render_entry(Entry("walls", Dict(())), 0)


def test_the_pressure_work_scheme_key_renders_and_a_bare_bar_does_not() -> None:
    """`div(phi,(p|rho))` is the `fluid` solver's `e`-equation scheme (ADR-0027 step 3): the bar
    is allowed inside a key's parentheses only, and hostile characters there are still refused."""
    from stokes.compile.foam import Entry, FoamError, Raw, _render_entry

    line = _render_entry(Entry("div(phi,(p|rho))", Raw("bounded Gauss upwind")), 0)
    assert line == "div(phi,(p|rho)) bounded Gauss upwind;"
    for bad in ("p|rho", "div(phi,(p|rho;))", "div(phi,$p)", "div(phi,{p})"):
        with pytest.raises(FoamError, match="not an OpenFOAM dictionary key"):
            _render_entry(Entry(bad, Raw("x")), 0)

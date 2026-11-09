"""The external-case writer (plan T-308): overrides spliced into a case's own bytes.

**What must hold.** A file no override names stages byte-identical; an overridden file differs
only in the overridden value; a value can never carry a directive, a variable or a second entry.
Every dictionary here is synthetic, written by the test (ADR-0029 decision 2).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from stokes.compile.foam_read import parse
from stokes.compile.of14.external import (
    Override,
    OverrideError,
    Scalar,
    Tokens,
    apply,
    entry_text,
    render,
    rows,
    write,
)

GOLDEN = Path(__file__).parent.parent / "golden" / "external"

CONTROL = b"""/* a synthetic banner, kept byte for byte */
FoamFile
{
    format      ascii;
    class       dictionary;
    object      controlDict;
}

application     foamRun;   // the solver driver
solver          incompressibleFluid;

startTime       0;
endTime         0.5;
deltaT          0.005;

PISO
{
    nCorrectors     2;
}

functions
{
}
// end
"""

PROPERTIES = b"""FoamFile
{
    format      ascii;
    class       dictionary;
}

viscosityModel  constant;
nu              [0 2 -1 0 0 0 0] 0.01;
"""


def _set(data: bytes, path: tuple[str, ...], value: object) -> bytes:
    return apply(data, [Override("f", path, value)])  # type: ignore[arg-type]


def test_no_override_is_the_input_itself() -> None:
    assert apply(CONTROL, []) is CONTROL
    files = {"system/controlDict": CONTROL, "0/U": b"\x00binary\x01"}
    staged = write(files, [Override("system/controlDict", ("endTime",), 1)])
    assert staged["0/U"] is files["0/U"], "an untouched file must pass through as it is"


def test_an_override_changes_only_its_value() -> None:
    staged = _set(CONTROL, ("endTime",), 2.5)
    assert staged == CONTROL.replace(b"endTime         0.5;", b"endTime         2.5;")
    assert entry_text(staged, ("endTime",)) == "2.5"


def test_the_golden_endtime_and_piso_edit() -> None:
    """**Byte for byte.** `tests/golden/` changes only with a reviewed diff."""
    staged = apply(
        CONTROL,
        [
            Override("system/controlDict", ("endTime",), Scalar(1.0)),
            Override("system/controlDict", ("PISO", "nCorrectors"), 3),
            Override("system/controlDict", ("PISO", "momentumPredictor"), False),
            Override("system/controlDict", ("writeControl",), "timeStep"),
        ],
    )
    assert staged == (GOLDEN / "controlDict").read_bytes()
    parse(staged)  # and it is still a dictionary


def test_a_quantity_keeps_the_dimension_set() -> None:
    staged = _set(PROPERTIES, ("nu",), Scalar(1e-05))
    assert b"nu              [0 2 -1 0 0 0 0] 1e-05;" in staged
    # A plain token replaces the whole value: the user asked for exactly that text.
    assert b"nu              0.02;" in _set(PROPERTIES, ("nu",), 0.02)


def test_missing_keys_and_blocks_are_added_where_they_belong() -> None:
    staged = _set(CONTROL, ("PIMPLE", "nOuterCorrectors"), 2)
    assert staged.endswith(b"// end\nPIMPLE\n{\n    nOuterCorrectors 2;\n}\n")
    inside = _set(CONTROL, ("functions", "probe", "type"), "probes")
    tree = parse(inside)
    assert entry_text(inside, ("functions", "probe", "type")) == "probes"
    assert (
        tree is not None
        and b"functions\n{\n    probe\n    {\n        type probes;\n    }\n}" in inside
    )


def test_values_render_as_typed_tokens() -> None:
    assert render(Tokens(("uniform", (1, 0, 0)))) == "uniform (1 0 0)"
    assert render(("Gauss", "linear")) == "(Gauss linear)"
    assert render(True) == "true" and render(1e-5) == "1e-05" and render(3.0) == "3"
    assert render("div(phi,U)") == "div(phi,U)"


@pytest.mark.parametrize(
    "value",
    [
        "#include x",
        "$var",
        "a;b",
        '"quoted"',
        "a{b}",
        "un(balanced",
        "a)(",
        "x//y",
        "x/*y",
        "",
        float("nan"),
        float("inf"),
        Tokens(()),
    ],
)
def test_a_value_cannot_smuggle_syntax(value: object) -> None:
    with pytest.raises(OverrideError):
        _set(CONTROL, ("endTime",), value)


def test_lists_nest_only_so_deep() -> None:
    deep: object = 1
    for _ in range(6):
        deep = (deep,)
    with pytest.raises(OverrideError):
        render(deep)  # type: ignore[arg-type]


def test_a_block_cannot_become_a_value_nor_a_value_a_block() -> None:
    with pytest.raises(OverrideError) as caught:
        _set(CONTROL, ("PISO",), 3)
    assert caught.value.code == "case.override_block"
    with pytest.raises(OverrideError) as caught:
        _set(CONTROL, ("endTime", "inner"), 3)
    assert caught.value.code == "case.override_path"


@pytest.mark.parametrize("path", [(), ("#include",), ("$x",), ("a b",), ("a;",)])
def test_a_path_must_be_keys(path: tuple[str, ...]) -> None:
    with pytest.raises(OverrideError):
        _set(CONTROL, path, 1)


def test_an_override_for_a_file_the_case_lacks_is_refused() -> None:
    with pytest.raises(OverrideError) as caught:
        write({"system/controlDict": CONTROL}, [Override("system/fvSchemes", ("a",), 1)])
    assert caught.value.code == "case.override_file"


def test_rows_list_entries_blocks_and_directives() -> None:
    data = CONTROL + b"#includeFunc probes(U)\n"
    found = {(tuple(row["path"]), row["kind"]): row["text"] for row in rows(data)}
    assert found[(("endTime",), "entry")] == "0.5"
    assert found[(("PISO",), "block")] == ""
    assert found[(("PISO", "nCorrectors"), "entry")] == "2"
    assert found[((), "directive")] == "#includeFunc probes(U)"
    assert found[(("FoamFile", "class"), "entry")] == "dictionary"


def test_the_last_duplicate_key_is_the_one_changed() -> None:
    data = b"a 1;\na 2;\n"
    assert _set(data, ("a",), 9) == b"a 1;\na 9;\n"


_words = st.from_regex(r"[A-Za-z_][A-Za-z0-9_]{0,12}", fullmatch=True)
_atoms = st.one_of(
    _words, st.integers(-(10**9), 10**9), st.floats(allow_nan=False, allow_infinity=False, width=64)
)


@given(key=_words, value=st.one_of(_atoms, st.lists(_atoms, max_size=5).map(tuple)))
def test_any_override_reads_back_as_written(key: str, value: object) -> None:
    staged = _set(CONTROL, (key,), value)
    assert entry_text(staged, (key,)) == render(value)  # type: ignore[arg-type]
    untouched = [row for row in rows(CONTROL) if row["path"] != [key]]
    after = {tuple(row["path"]): row["text"] for row in rows(staged)}
    for row in untouched:
        if row["kind"] == "entry":
            assert after[tuple(row["path"])] == row["text"]

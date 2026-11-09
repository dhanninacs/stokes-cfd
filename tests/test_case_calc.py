"""`stokes.cases.calc`: plain-arithmetic `#calc` resolved in a case's copy (ADR-0033)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from stokes.cases.calc import resolve_calc

DEFS = "m 3;\nradius 4;\nb 0.5;\nrho 2;\ne 1;\nangle 60;\n"


def last_line(expr: str) -> str:
    return resolve_calc(f'{DEFS}x #calc "{expr}";\n').text.splitlines()[-1]


def test_the_ball_valve_bend_radius_is_resolved() -> None:
    text = 'rBall 18;\nrPipe 10;\nrBend #calc "sqrt(scalar($rBall*$rBall - $rPipe*$rPipe))";\n'
    out = resolve_calc(text)
    assert out.left == [] and len(out.resolved) == 1
    value = float(out.text.splitlines()[-1].split()[1].rstrip(";"))
    assert value == pytest.approx(math.sqrt(18 * 18 - 10 * 10))
    assert "#calc" not in out.text


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("8 * pow(label(2), $<label>m - 1)", "x 32;"),
        ("$<scalar>{radius}/(2 - $<scalar>b)", "x 2.6666666666666665;"),
        ("$m*6", "x 18;"),
        ("1.0/300.0", "x 0.0033333333333333335;"),
    ],
)
def test_arithmetic_over_the_files_own_numbers(expr: str, expected: str) -> None:
    assert last_line(expr) == expected


@pytest.mark.parametrize(
    "expr",
    [
        "$rho*cmptProduct($<vector>L)",  # a vector function
        "$blockMeshDict.cylinder!x0b - $e",  # another dictionary's entry
        "$undefined * 2",  # a name the file does not define as a number
        "system(1)",  # anything that is not the grammar
    ],
)
def test_anything_else_is_left_for_the_lint(expr: str) -> None:
    out = resolve_calc(f'{DEFS}x #calc "{expr}";\n')
    assert out.resolved == [] and out.left == [expr]
    assert f'#calc "{expr}"' in out.text


def test_a_resolved_entry_feeds_the_next() -> None:
    out = resolve_calc('a 2;\nb #calc "$a*3";\nc #calc "$b+1";\n')
    assert out.text.splitlines()[1:] == ["b 6;", "c 7;"]


def test_text_without_calc_is_returned_unchanged() -> None:
    text = "a 1;\nb $a;\n"
    assert resolve_calc(text).text is text


def test_the_real_ball_valve_resource_resolves(tmp_path: Path) -> None:
    """Against the install's own tutorials when `FOAM_TUTORIALS` names them; skipped otherwise."""
    from os import environ

    root = environ.get("FOAM_TUTORIALS", "")
    source = Path(root) / "resources" / "blockMesh" / "ballValve"
    if not root or not source.is_file():
        pytest.skip("no OpenFOAM tutorials on this machine")
    out = resolve_calc(source.read_text(encoding="utf-8"))
    assert out.left == [] and "#calc" not in out.text

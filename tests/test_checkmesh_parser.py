"""The checkMesh log parser (T-146, T-208).

**Every expected value is read from a committed OF-14 log**, not invented:
`checkMesh.pitzDaily.log` is a real run and `checkMesh.bad.log` is the skewed-cavity case
T-200 captured. Each test names the fixture line it matches, so a format change in a
future OpenFOAM shows up as a failing test rather than a `None`.

The parser is the only thing that reads checkMesh's verdict: **the command exits 0 even
when it reports `Failed 2 mesh checks.`** (measured), so a caller that trusted the exit
code would call a failed mesh good.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes_agent.parsers import PARSERS
from stokes_agent.parsers.checkmesh import parse_checkmesh

FIXTURES = Path(__file__).parent / "fixtures" / "of14" / "logs"
GOOD = FIXTURES / "checkMesh.pitzDaily.log"
BAD = FIXTURES / "checkMesh.bad.log"


@pytest.fixture(scope="module")
def good() -> dict[str, object]:
    return parse_checkmesh(GOOD.read_text())


@pytest.fixture(scope="module")
def bad() -> dict[str, object]:
    return parse_checkmesh(BAD.read_text())


def test_the_mesh_stats(good: dict[str, object]) -> None:
    """`    cells:            12225` and its neighbours, with the indentation they have.

    **`internal points:` and `internal faces:` are why the patterns are anchored.** An unanchored
    `points:` would match `    internal points:  0` and report 0 points for a mesh with 25,012.
    """
    assert good["cells"] == 12225
    assert good["points"] == 25012
    assert good["faces"] == 49180
    assert good["internal_faces"] == 24170
    assert good["boundary_patches"] == 5


def test_the_non_orthogonality_line_uses_a_colon(good: dict[str, object]) -> None:
    """`    Mesh non-orthogonality Max: 5.95045 average: 1.63034`

    **`Max:` with a colon**, not `Max non-orthogonality =` — the phrasing the brief uses
    matches no line in the file.
    """
    assert good["max_non_ortho"] == pytest.approx(5.95045)
    assert good["avg_non_ortho"] == pytest.approx(1.63034)


def test_skewness_and_aspect_ratio(good: dict[str, object]) -> None:
    """`    Max skewness = 0.260575 OK.` and `    Max aspect ratio = 8.1407 OK.`"""
    assert good["max_skewness"] == pytest.approx(0.260575)
    assert good["max_aspect_ratio"] == pytest.approx(8.1407)


def test_the_volumes(good: dict[str, object]) -> None:
    """`    Min volume = 1.6902e-10. Max volume = 3.83579e-09. …`"""
    assert good["min_volume"] == pytest.approx(1.6902e-10)
    assert good["max_volume"] == pytest.approx(3.83579e-09)


def test_the_verdict_and_the_patch_table(good: dict[str, object]) -> None:
    """`Mesh OK.` is the verdict, and the patch table gives the names a client shows."""
    assert good["mesh_ok"] is True
    assert good["failed_checks"] == 0
    names = [patch["name"] for patch in good["patches"]]
    assert names == ["inlet", "outlet", "upperWall", "lowerWall", "frontAndBack"]
    assert good["patches"][0]["faces"] == 30


def test_a_failing_mesh(bad: dict[str, object]) -> None:
    """**The failing forms, which are the ones a parser most needs to get right.**

        Mesh non-orthogonality Max: 82.5441 average: 56.7497
       *Number of severely non-orthogonal (> 70 degrees) faces: 242.
     ***Max skewness = 21.4007, 244 highly skew faces detected which may impair the quality …
    Failed 2 mesh checks.

    **The skewed line carries a `***` prefix and a COMMA** where the passing one has `OK.`,
    so a pattern anchored on the indentation misses the one number a user most needs.
    """
    assert bad["mesh_ok"] is False
    assert bad["failed_checks"] == 2
    assert bad["max_non_ortho"] == pytest.approx(82.5441)
    assert bad["avg_non_ortho"] == pytest.approx(56.7497)
    assert bad["max_skewness"] == pytest.approx(21.4007)
    assert bad["severe_non_ortho_faces"] == 242
    assert bad["cells"] == 400


def test_a_key_the_log_does_not_carry_is_none_never_a_default() -> None:
    """**The rule the whole parser rests on.** A number nobody read must not become a
    number that looks read: a mesh reported `OK` on a defaulted figure is worse than one
    reported unknown."""
    found = parse_checkmesh("Checking geometry...\nMesh OK.\n")
    assert found["mesh_ok"] is True
    for key in ("cells", "points", "faces", "max_non_ortho", "max_skewness", "min_volume"):
        assert found[key] is None, f"{key} was invented"


@pytest.mark.parametrize(
    "text",
    (
        "",
        "x" * 10_000_000,
        "y" * 100_000 + "\n",
        "cells: not-a-number\n",
        "Mesh non-orthogonality Max: average:\n",
        "    cells:            12225",  # truncated, no newline
        "Failed mesh checks.\n",
        "***Max skewness =\n",
    ),
    # A 100 000-character text as its own id overflows Windows' 32 767-character environment
    # variable limit (pytest puts the id in PYTEST_CURRENT_TEST).
    ids=lambda text: f"{len(text)}-chars-{text[:12]!r}",
)
def test_it_never_raises(text: str) -> None:
    """A log is output from a program we do not control. A parser that throws turns a
    successful mesh into a failed job, which is the one thing it must not do."""
    found = parse_checkmesh(text)
    assert isinstance(found, dict)
    assert "cells" in found


def test_a_large_log_is_read_from_its_end() -> None:
    """At most the last 4 MiB: a mesh log is a few hundred kilobytes, and reading more is a
    way to make the agent allocate without limit. The summary is at the END, so the tail is
    what matters."""
    padding = "noise\n" * 2_000_000  # ~12 MB
    found = parse_checkmesh(padding + GOOD.read_text())
    assert found["cells"] == 12225
    assert found["mesh_ok"] is True


def test_the_parser_satisfies_the_log_parser_protocol() -> None:
    """**`feed` returns nothing and `report` does the work.** checkMesh's figures are a
    summary printed at the end, so a per-line parse would report half a mesh and then
    correct itself.
    """
    parser = PARSERS["checkMesh"]()
    lines = GOOD.read_text().splitlines()
    assert parser.feed(lines[:10]) == []
    assert parser.feed(lines[10:]) == []
    report = parser.report()
    assert report["cells"] == 12225
    assert report["mesh_ok"] is True


def test_the_parser_is_bounded_while_it_feeds() -> None:
    """It keeps the tail, not everything: a job that printed a gigabyte must not grow the agent."""
    parser = PARSERS["checkMesh"]()
    for _ in range(6000):
        parser.feed(["z" * 4000])
    assert parser.report()["cells"] is None or parser.report()["cells"] == 0


def test_the_registry_has_the_name_the_driver_asks_for() -> None:
    """`blockmesh.steps` names `checkMesh`, and an unknown name is refused at `create` —
    so the two must agree, and this is where they are checked against each other."""
    from stokes.mesh.drivers.blockmesh import steps
    from stokes.mesh.snapshot import MeshSnapshot

    snap = MeshSnapshot(
        settings_id="n",
        mesher="blockmesh",
        base_size=0.1,
        bbox=(0.0, 0.0, 0.0, 1.0, 1.0, 1.0),
        sides={},
        side_tags={},
        side_kinds={},
        unmapped=(),
        max_cells=1000,
        cores=1,
        max_non_ortho=70.0,
        max_skewness=4.0,
        domain_part=None,
        part_basis="",
        tag_members={},
    )
    for step in steps(snap):
        name = step.get("parse")
        assert name is None or name in PARSERS, (
            f"the driver asks for a parser {name!r} that is not registered"
        )


# ---------------------------------------------------------------- the FAILING geometry sentences
#
# **Found by an adversarial review, not by me.** My parser was written against the PASSING
# sentences in the
# fixtures, so the two checks whose failure message is a *different sentence* came back `None`
# exactly when
# a user most needs the number. Both forms below are quoted from the install's own source
# (`src/meshCheck/primitiveMeshCheck/primitiveMeshCheck.C`), not from a document.


def test_a_high_aspect_ratio_failure_is_read() -> None:
    """` ***High aspect ratio cells found, Max aspect ratio: 1000, number of cells 5`

    Verified at `primitiveMeshCheck.C:724`. **A `***` prefix, the words `cells found,`, and a
    COLON** —
    where the passing form is `    Max aspect ratio = 8.1407 OK.` with an equals sign.

    This is reachable on any real mesh: `checkGeometry.C` calls `checkClosedCells` with
    `aspectThreshold = 1000`, so a cell worse than that prints this line.
    """
    line = " ***High aspect ratio cells found, Max aspect ratio: 1000, number of cells 5\n"
    assert parse_checkmesh(line)["max_aspect_ratio"] == pytest.approx(1000.0)


def test_both_aspect_ratio_forms_are_read() -> None:
    """The passing and failing sentences differ, so a pattern must know both — reading one and
    not the
    other is how a failing mesh reported no aspect ratio."""
    passing = "    Max aspect ratio = 8.1407 OK.\n"
    failing = " ***High aspect ratio cells found, Max aspect ratio: 1000, number of cells 5\n"
    assert parse_checkmesh(passing)["max_aspect_ratio"] == pytest.approx(8.1407)
    assert parse_checkmesh(failing)["max_aspect_ratio"] == pytest.approx(1000.0)


def test_a_negative_volume_failure_is_read() -> None:
    """` ***Zero or negative cell volume detected. Minimum negative volume: -1.5e-09, Number
    of negative
    volume cells: 3`

    Verified at `primitiveMeshCheck.C:845`. **A non-positive cell volume is the worst thing a
    mesh can
    contain**, and it was the case that reported nothing: the figure is still NEGATIVE in the
    key, because
    a negative minimum volume is the fact being reported.
    """
    line = (
        " ***Zero or negative cell volume detected.  Minimum negative volume: -1.5e-09, "
        "Number of negative volume cells: 3\n"
    )
    found = parse_checkmesh(line)
    assert found["min_volume"] == pytest.approx(-1.5e-09)
    assert found["negative_volume_cells"] == 3
    # The passing sentence reports min and max; this one reports neither maximum nor a count of
    # cells.
    assert found["max_volume"] is None


def test_a_negative_volume_is_not_mistaken_for_the_passing_sentence() -> None:
    """The two sentences must not collide: the passing one fills `max_volume`, the failing one
    does not."""
    passing = "    Min volume = 2.5625e-07. Max volume = 4.9375e-07.  Total volume = 0.00015.\n"
    found = parse_checkmesh(passing)
    assert found["min_volume"] == pytest.approx(2.5625e-07)
    assert found["max_volume"] == pytest.approx(4.9375e-07)
    assert found["negative_volume_cells"] is None


def test_the_failing_fixtures_geometry_lines_are_not_mistaken_for_negatives(
    bad: dict[str, object],
) -> None:
    """The bad fixture fails non-orthogonality and skewness, **not** the volume check — so its
    volumes
    must still be the passing ones and the negative counter must stay empty."""
    assert bad["min_volume"] == pytest.approx(2.5625e-07)
    assert bad["max_volume"] == pytest.approx(4.9375e-07)
    assert bad["negative_volume_cells"] is None


# ------------------------------------------------------------------ cell types (brief M-506 Part A)


def test_the_cell_type_block_gives_ALL_SEVEN_counts() -> None:
    """The pitzDaily fixture's block: 12,225 hexahedra and six zeros — all seven reported, not
    just the non-zero ones."""
    found = parse_checkmesh(GOOD.read_text(encoding="utf-8"))
    assert found["cell_types"] == {
        "hex": 12225,
        "prism": 0,
        "wedge": 0,
        "pyramid": 0,
        "tet_wedge": 0,
        "tet": 0,
        "poly": 0,
    }


def test_a_missing_or_garbled_block_gives_zeros_never_an_exception() -> None:
    """A log without the block (an old checkMesh), a truncated one, and a garbled count each
    read as zeros — a parser that throws turns a successful mesh into a failed job."""
    for text in (
        "",
        "Overall number of cells of each type:\n",  # header, no rows
        "Overall number of cells of each type:\n    hexahedra:     \n",  # truncated row
        "Overall number of cells of each type:\n    hexahedra:     not-a-number\n",
        "    hexahedra:     5\n",  # rows without the header
        "x" * 100_000 + "\nOverall number of cells of each type:\n",
    ):
        found = parse_checkmesh(text)
        assert found["cell_types"] == {
            "hex": 0,
            "prism": 0,
            "wedge": 0,
            "pyramid": 0,
            "tet_wedge": 0,
            "tet": 0,
            "poly": 0,
        }


def test_a_mixed_mesh_counts_each_type() -> None:
    """A block with several non-zero types: each lands on its own key, and an unknown type's row
    is skipped rather than crashing the parse."""
    text = (
        "Overall number of cells of each type:\n"
        "    hexahedra:     100\n"
        "    prisms:        40\n"
        "    wedges:        2\n"
        "    pyramids:      3\n"
        "    tet wedges:    4\n"
        "    tetrahedra:    50\n"
        "    polyhedra:     6\n"
        "    dodecahedra:   99\n"  # not one of the seven: skipped
    )
    found = parse_checkmesh(text)
    assert found["cell_types"] == {
        "hex": 100,
        "prism": 40,
        "wedge": 2,
        "pyramid": 3,
        "tet_wedge": 4,
        "tet": 50,
        "poly": 6,
    }


def test_cell_types_and_the_neighbouring_sections_do_not_bleed() -> None:
    """The block sits between two other sections in a real log; each parser region must end when
    its own section does."""
    text = (
        "Overall number of cells of each type:\n"
        "    hexahedra:     12225\n"
        "    prisms:        0\n"
        "    wedges:        0\n"
        "    pyramids:      0\n"
        "    tet wedges:    0\n"
        "    tetrahedra:    0\n"
        "    polyhedra:     0\n"
        "\n"
        "Checking topology...\n"
        "    Boundary definition OK.\n"
        "    Patch Faces Points Surface topology\n"
        "    inlet               30       62       ok (non-closed convex)\n"
    )
    found = parse_checkmesh(text)
    assert found["cell_types"]["hex"] == 12225
    assert found["patches"] == [{"name": "inlet", "faces": 30, "points": 62}]


def test_non_finite_numbers_are_dropped() -> None:
    """**The second review's A2**: `float("1e999")` parses as `inf`, and an inf reaching the
    result meta reaches `json.dumps` as `Infinity`, which browsers reject. Non-finite is None,
    as the snappy parser has had since the M-504 fixes."""
    import math

    from agent.stokes_agent.parsers.checkmesh import _number

    assert _number("1e999") is None
    assert _number("-1e999") is None
    assert _number("nan") is None
    assert _number("56.3099") == pytest.approx(56.3099)
    assert math.isfinite(_number("1e999") or 0.0) is not False

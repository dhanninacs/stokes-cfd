"""The snappy log parser (brief M-504): the final layer table, read from a real log.

The fixture is **a real run** — the pipe (a cylinder), 3 layers on its walls, through the whole
service on this install — committed at `tests/fixtures/of14/logs/snappy-layers.log`; its
provenance is in `tests/fixtures/of14/inventory-thinkyboy.md`. The patterns were written from
the install's source (`snappyLayerDriver.C:2456`, `printLayerData`) and then checked against
that log, which is why every number asserted here is the log's own.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes_agent.parsers.snappy import MAX_BYTES, SnappyParser, parse_snappy_layers

FIXTURE = Path(__file__).parent / "fixtures" / "of14" / "logs" / "snappy-layers.log"


def test_the_real_log_parses_to_the_coverage_table() -> None:
    """The pipe's walls: 2454 faces, all 3 layers, 99.5 % of the wanted thickness."""
    rows = parse_snappy_layers(FIXTURE.read_text(encoding="utf-8"))
    assert rows == [
        {
            "patch": "walls",
            "faces": 2454,
            "layers_mean": 3.0,
            "thickness_mean": 0.000319,
            "coverage_percent": 99.5,
        }
    ]


def test_a_log_with_no_table_gives_an_empty_list() -> None:
    """A run that never reached the layer pass (or a parser pointed at the wrong log): an empty
    list, never an exception - the reader decides what absent means."""
    assert parse_snappy_layers("Layer mesh : cells:100\n") == []
    assert parse_snappy_layers("") == []


def test_a_truncated_table_gives_what_it_holds_and_no_exception() -> None:
    """A table cut after the header but before any row: an empty list. One cut after a row:
    that row, and nothing invented."""
    text = "patch faces    layers   overall thickness\n                    [m]       [%]\n"
    assert parse_snappy_layers(text) == []
    text += "----- -----    ------   ---       ---\n"
    assert parse_snappy_layers(text) == []
    text += "walls 2454     3        0.000319  99.5\n"
    rows = parse_snappy_layers(text)
    assert len(rows) == 1 and rows[0]["patch"] == "walls"


def test_a_row_with_broken_numbers_is_skipped_not_reported() -> None:
    """A line that matches the row shape but carries garbage in a number column is not a row:
    a partial figure would be worse than none."""
    text = (
        "patch faces    layers   overall thickness\n"
        "                    [m]       [%]\n"
        "----- -----    ------   ---       ---\n"
        "walls not-a-number 3        0.000319  99.5\n"
        "walls 2454     3        0.000319  99.5\n"
    )
    rows = parse_snappy_layers(text)
    assert [row["faces"] for row in rows] == [2454]


def test_a_MiB_long_line_is_skipped() -> None:
    """One enormous line - corrupt, or built to defeat an anchored scan - is skipped; the table
    around it still reads."""
    big = "x" * (MAX_BYTES // 2)
    text = (
        f"{big}\n"
        "patch faces    layers   overall thickness\n"
        "                    [m]       [%]\n"
        "----- -----    ------   ---       ---\n"
        "walls 2454     3        0.000319  99.5\n"
    )
    rows = parse_snappy_layers(text)
    assert [row["patch"] for row in rows] == ["walls"]


def test_a_later_table_wins() -> None:
    """A log can carry more than one table (a retried run in one file); the FINAL table is the
    state the mesh ended in, so it is the one reported."""
    row = "walls 2454     3        0.000319  99.5\n"
    head = (
        "patch faces    layers   overall thickness\n"
        "                    [m]       [%]\n"
        "----- -----    ------   ---       ---\n"
    )
    text = head + row + "\n" + head + "walls 2454     2        0.000200  60.0\n"
    rows = parse_snappy_layers(text)
    assert rows == [
        {
            "patch": "walls",
            "faces": 2454,
            "layers_mean": 2.0,
            "thickness_mean": 0.0002,
            "coverage_percent": 60.0,
        }
    ]


def test_a_patch_name_with_spaces_is_its_own_token() -> None:
    """Patch names come from tags, and a tag with a space... cannot exist (NAME_RE bounds the
    characters), but snappy's own regex keys can match several patches - a name is still one
    token in the row, so the parse holds for any name the mesher prints."""
    text = (
        "patch faces    layers   overall thickness\n"
        "                    [m]       [%]\n"
        "----- -----    ------   ---       ---\n"
        '"(walls|shells).*" 2454     3        0.000319  99.5\n'
    )
    rows = parse_snappy_layers(text)
    assert rows[0]["patch"] == '"(walls|shells).*"'


def test_the_parser_object_feeds_and_reports_like_the_others() -> None:
    """The `LogParser` protocol: feed in batches, report once at the end, nothing on the way.

    **The lines come without their newline ends** - that is the shape the supervisor feeds
    (`jobs.py` reads chunks and `splitlines()` them), and it is the shape `report`'s join
    assumes: lines with their ends would come back double-spaced.
    """
    parser = SnappyParser()
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    for index in range(0, len(lines), 200):
        assert parser.feed(lines[index : index + 200]) == []
    report = parser.report()
    assert report == {"layers": parse_snappy_layers(FIXTURE.read_text(encoding="utf-8"))}


def test_the_parser_is_registered_for_the_snappy_step() -> None:
    from stokes_agent.parsers import PARSERS

    assert PARSERS["snappy"] is SnappyParser


# ----------------------------------------------------------------- bounds and hostile input


@pytest.mark.parametrize(
    "text",
    (
        "",
        "x" * 10_000_000,
        "y" * 100_000 + "\n",
        "walls not-a-number not-a-number not-a-number not-a-number\n",
        "patch faces    layers   overall thickness\n\x00\x01binary noise\n",
        "patch faces    layers   overall thickness",  # truncated, no newline
        "walls 2454     3        0.000319\n",  # a row short of its columns
    ),
    # **Short ids**: the inputs can be 10 MB, and pytest puts the id into PYTEST_CURRENT_TEST,
    # which Windows caps at 32767 characters, so the default id made these cases error at setup.
    ids=(
        "empty",
        "ten-megabyte-line",
        "hundred-kilobyte-line",
        "garbage-numbers",
        "binary-noise",
        "truncated-header",
        "short-row",
    ),
)
def test_it_never_raises(text: str) -> None:
    """A log is output from a program we do not control. A parser that throws turns a
    successful mesh into a failed job, which is the one thing it must not do."""
    rows = parse_snappy_layers(text)
    assert isinstance(rows, list)


def test_a_large_log_is_read_from_its_end() -> None:
    """At most the last 4 MiB, like the checkMesh parser: the table is at the END, so the tail
    is what matters, and a gigabyte log must not make the agent allocate."""
    padding = "noise\n" * 2_000_000  # ~12 MB
    rows = parse_snappy_layers(padding + FIXTURE.read_text(encoding="utf-8"))
    assert rows == parse_snappy_layers(FIXTURE.read_text(encoding="utf-8"))


def test_the_parser_is_bounded_while_it_feeds() -> None:
    """It keeps the tail, not everything: a job that printed a gigabyte must not grow the agent."""
    parser = SnappyParser()
    for _ in range(6000):
        parser.feed(["z" * 4000])
    assert parser.report() == {"layers": []}


# ----------------------------------------------------------------- the F3 fixes (M-504 review)


def test_nan_and_inf_rows_are_dropped() -> None:
    """**Non-finite is None is dropped** (the review's F3): a `nan` reaching `meta.layers`
    reaches `json.dumps` as `NaN`, which browsers' `JSON.parse` rejects."""
    text = (
        "patch faces    layers   overall thickness\n"
        "                    [m]       [%]\n"
        "----- -----    ------   ---       ---\n"
        "walls 2454     nan      0.000319  99.5\n"
        "walls 2454     3        inf       99.5\n"
        "walls 2454     3        0.000319  nan\n"
        "walls 2454     3        0.000319  99.5\n"
    )
    rows = parse_snappy_layers(text)
    assert rows == [
        {
            "patch": "walls",
            "faces": 2454,
            "layers_mean": 3.0,
            "thickness_mean": 0.000319,
            "coverage_percent": 99.5,
        }
    ]


def test_the_rows_are_capped() -> None:
    """A 200,000-row table would otherwise fill `meta.layers`, persisted and sent in events
    (the review measured exactly that). The cap keeps the TAIL — the final state."""
    row = "walls 2454     3        0.000319  99.5\n"
    head = (
        "patch faces    layers   overall thickness\n"
        "                    [m]       [%]\n"
        "----- -----    ------   ---       ---\n"
    )
    rows = parse_snappy_layers(head + row * 2000)
    assert len(rows) == 1024  # MAX_ROWS
    assert rows[-1]["faces"] == 2454  # the tail, not the head


def test_the_tail_trim_is_linear() -> None:
    """**60 MB of 60-byte lines in bounded CPU** (the review measured 18 s with `pop(0)`): the
    deque trim is O(1) per dropped line, so a megabyte-scale feed finishes in the test budget —
    the assertion is functional (it completes and keeps the bound), not a timer."""
    import time

    parser = SnappyParser()
    line = "x" * 59 + "\n"
    started = time.monotonic()
    for _ in range(1_000_000):
        parser.feed([line])
    elapsed = time.monotonic() - started
    assert parser.report() == {"layers": []}
    assert elapsed < 10.0, f"the tail trim is not linear: {elapsed:.1f}s for 1M lines"

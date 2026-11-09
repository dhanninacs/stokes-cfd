"""The `foamRun` log parser (T-213, checkpoint A).

**Every expected value comes from a committed OF-14 log**, never from memory:
`tests/fixtures/of14/logs/foamRun.pitzDaily.log` is a real 17,219-line run (exit 0, 54.21 s) and
`foamRun.fatal.log` is the same tutorial with `0/U` deleted (exit 1).

Two format facts decide the patterns, and both were checked against the fixture rather than assumed:

* **`Time = 0.000119048s` has a UNIT SUFFIX** (correction 1). A pattern ending at the number matches
  nothing.
* **`smoothSolver:  Solving for Ux, …` has TWO SPACES after the colon**, and the solver name varies
  (`GAMG` for `p`). A pattern for one name or one space reads half the fields.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stokes_agent.parsers import PARSERS
from stokes_agent.parsers.foamrun import HISTORY_NAME, FoamRunParser

FIXTURES = Path(__file__).parent / "fixtures" / "of14" / "logs"
GOOD = FIXTURES / "foamRun.pitzDaily.log"
FATAL = FIXTURES / "foamRun.fatal.log"


def parse_all(text: str, **kwargs: object) -> tuple[FoamRunParser, list[tuple[str, dict]]]:
    """Feed a whole log in batches, as the job thread does, and collect the events."""
    parser = FoamRunParser(**kwargs)  # type: ignore[arg-type]
    lines = text.splitlines()
    events: list[tuple[str, dict]] = []
    for start in range(0, len(lines), 500):
        events += parser.feed(lines[start : start + 500])
    return parser, events


@pytest.fixture(scope="module")
def good() -> tuple[FoamRunParser, list[tuple[str, dict]]]:
    return parse_all(GOOD.read_text(), end_time=0.3)


# ---------------------------------------------------------------- the counts, from the log


def test_the_iteration_count_matches_the_log(
    good: tuple[FoamRunParser, list[tuple[str, dict]]],
) -> None:
    """**1,225**, which is the number of `Time = …` lines in the file — checked independently with
    `grep -c '^Time = '`, not by trusting the parser."""
    parser, _ = good
    assert parser.report()["iterations"] == 1225


def test_the_last_time_matches_the_log(good: tuple[FoamRunParser, list[tuple[str, dict]]]) -> None:
    """The last `Time` line is `Time = 0.3s`, so the tutorial ran to its `endTime`."""
    parser, _ = good
    report = parser.report()
    assert report["last_time"] == pytest.approx(0.3)
    assert report["ended"] is True


def test_the_unit_suffix_is_parsed() -> None:
    """`Time = 0.000119048s` — the form correction 1 warned about. A bare-number pattern gets 0."""
    parser = FoamRunParser(end_time=1.0)
    parser.feed(["Time = 12s", "ExecutionTime = 0.5 s  ClockTime = 1 s"])
    assert parser.report()["last_time"] == pytest.approx(12.0)


def test_indented_multi_region_time_records_progress_without_merging_residuals() -> None:
    parser = FoamRunParser(end_time=2.0)
    events = parser.feed(
        [
            "      Time = 0.5s",
            "fluid DILUPBiCGStab:  Solving for e, Initial residual = 0.2, "
            "Final residual = 0.01, No Iterations 2",
            "solid DICPCG:  Solving for e, Initial residual = 0.3, "
            "Final residual = 0.02, No Iterations 3",
            "      ExecutionTime = 2 s  ClockTime = 3 s",
        ]
    )
    report = parser.report()
    assert report["iterations"] == 1 and report["last_time"] == pytest.approx(0.5)
    assert events[0][1]["residuals"] == {}


def test_the_two_space_solve_line_and_a_varying_solver_name() -> None:
    """**`smoothSolver` for U/k/epsilon and `GAMG` for p**, with two spaces after the colon. A
    pattern
    anchored on one name would read only half the fields."""
    parser = FoamRunParser(end_time=1.0)
    parser.feed(
        [
            "Time = 1s",
            "smoothSolver:  Solving for Ux, Initial residual = 0.5, Final residual = 1e-06, "
            "No Iterations 3",
            "GAMG:  Solving for p, Initial residual = 0.25, "
            "Final residual = 2e-07, No Iterations 9",
            "ExecutionTime = 1.0 s  ClockTime = 1 s",
        ]
    )
    report = parser.report()
    assert report["last_residuals"] == {"Ux": 0.5, "p": 0.25}


def test_the_first_occurrence_of_a_field_wins() -> None:
    """The solver solves `p` several times per step; the **initial** residual of the FIRST solve
    is the
    iteration's convergence measure, so a later one must not overwrite it."""
    parser = FoamRunParser(end_time=1.0)
    parser.feed(
        [
            "Time = 1s",
            "GAMG:  Solving for p, Initial residual = 0.8, Final residual = 1e-08, No Iterations 5",
            "GAMG:  Solving for p, Initial residual = 0.02, "
            "Final residual = 1e-09, No Iterations 9",
            "ExecutionTime = 1.0 s  ClockTime = 1 s",
        ]
    )
    assert parser.report()["last_residuals"] == {"p": 0.8}


def test_continuity_cumulative_is_optional() -> None:
    """`cumulative` appears only on the last `p` solve of a step (fixture lines 74 and 76)."""
    parser = FoamRunParser(end_time=1.0)
    parser.feed(
        [
            "Time = 1s",
            "time step continuity errors : sum local = 1.7e-05, global = 4.3e-06",
            "ExecutionTime = 1.0 s  ClockTime = 1 s",
        ]
    )
    assert parser.report()["last_continuity"] == {
        "local": pytest.approx(1.7e-05),
        "global": pytest.approx(4.3e-06),
    }


def test_continuity_cumulative_is_read_when_present() -> None:
    parser = FoamRunParser(end_time=1.0)
    parser.feed(
        [
            "Time = 1s",
            "time step continuity errors : sum local = 1e-08, global = -2e-09, cumulative = -2e-09",
            "ExecutionTime = 1.0 s  ClockTime = 1 s",
        ]
    )
    assert parser.report()["last_continuity"]["cumulative"] == pytest.approx(-2e-09)


# ---------------------------------------------------------------- events


def test_one_event_per_call_carrying_every_block(
    good: tuple[FoamRunParser, list[tuple[str, dict]]],
) -> None:
    """**Correction 2: no lost points.** `feed` returns at most ONE `monitor.residual`, whose
    `blocks`
    list holds every block completed in that batch — because the job thread's limiter keeps
    only the last
    event per second, and one event per block would drop samples from a live plot."""
    _, events = good
    residuals = [data for name, data in events if name == "monitor.residual"]
    assert residuals, "no residual events"
    carried = [b for data in residuals for b in data["blocks"]]
    assert len(carried) == 1225, "every block must be carried exactly once"
    # And the iteration numbers are a complete run, in order.
    assert [b["iteration"] for b in carried] == list(range(1, 1226))


def test_each_block_has_the_named_keys() -> None:
    """The block shape T-214 reads: `iteration`, `time`, `residuals`, `continuity`,
    `execution_s`."""
    parser = FoamRunParser(end_time=1.0)
    events = parser.feed(
        [
            "Time = 0.5s",
            "smoothSolver:  Solving for Ux, Initial residual = 0.1, Final residual = 1e-06, "
            "No Iterations 2",
            "time step continuity errors : sum local = 1e-05, global = 1e-07",
            "ExecutionTime = 1.5 s  ClockTime = 2 s",
        ]
    )
    block = events[0][1]["blocks"][0]
    assert block["iteration"] == 1
    assert block["time"] == pytest.approx(0.5)
    assert block["residuals"] == {"Ux": 0.1}
    assert block["continuity"] == {"local": pytest.approx(1e-05), "global": pytest.approx(1e-07)}
    assert block["execution_s"] == pytest.approx(1.5)


def test_progress_is_capped_at_one() -> None:
    """`fraction = min(1, time / end_time)`: a run that overshoots its `endTime` must not report
    150%."""
    parser = FoamRunParser(end_time=1.0)
    events = parser.feed(["Time = 5s", "ExecutionTime = 1.0 s  ClockTime = 1 s"])
    progress = [data for name, data in events if name == "job.progress"]
    assert progress and progress[0]["fraction"] == 1.0


# ---------------------------------------------------------------- the history file


def test_every_block_is_appended_to_the_history(tmp_path: Path) -> None:
    """**The file is what survives a coalesced event.** One JSON object per line, so `job.read` can
    fetch the whole run (T-203)."""
    parser, _ = parse_all(GOOD.read_text(), end_time=0.3, job_dir=tmp_path)
    path = tmp_path / HISTORY_NAME
    assert path.is_file(), "the history must be written"
    written = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(written) == 1225
    assert written[0]["iteration"] == 1 and written[-1]["iteration"] == 1225
    assert parser.report()["history_file"] == HISTORY_NAME
    assert parser.report()["history_truncated"] is False


def test_the_history_is_appended_not_truncated(tmp_path: Path) -> None:
    """Append mode: a second run's blocks follow the first's rather than replacing them."""
    for _ in range(2):
        parse_all(
            "Time = 1s\nExecutionTime = 1 s  ClockTime = 1 s\n", end_time=1.0, job_dir=tmp_path
        )
    lines = (tmp_path / HISTORY_NAME).read_text().splitlines()
    assert len(lines) == 2


def test_a_job_dir_of_none_writes_nothing() -> None:
    """The unit tests pass None, and the report must then name no history file rather than a
    missing one."""
    parser, _ = parse_all("Time = 1s\nExecutionTime = 1 s  ClockTime = 1 s\n", end_time=1.0)
    assert parser.report()["history_file"] is None


def test_an_unwritable_history_does_not_fail_the_parse(tmp_path: Path) -> None:
    """**A parser may not fail a job** (`base.py`). A job folder that cannot be written costs the
    history, not the run — and the report says so rather than naming a file that is not there."""
    # A job folder that is a FILE cannot hold `monitors/` on any host (a `/proc` path is writable
    # on Windows, where it is just another folder on the current drive).
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    parser = FoamRunParser(end_time=1.0, job_dir=blocker)
    parser.feed(["Time = 1s", "ExecutionTime = 1.0 s  ClockTime = 1 s"])
    report = parser.report()
    assert report["iterations"] == 1, "the block was still parsed"
    assert report["history_file"] is None, "and the report does not name a file that is not there"


# ---------------------------------------------------------------- the fatal path


def test_the_fatal_log_is_read() -> None:
    """`foamRun.fatal.log` is the real failing run: exit 1, `cannot find file ".../0/U"`."""
    parser, _ = parse_all(FATAL.read_text(), end_time=1.0)
    report = parser.report()
    assert report["fatal"] is True
    assert report["ended"] is False
    assert report["iterations"] == 0
    assert "FOAM FATAL ERROR" in report["fatal_text"]
    assert "0/U" in report["fatal_text"]


def test_fatal_text_is_bounded() -> None:
    """A failure carries at most `MAX_FATAL_CHARS`, so a report never becomes a log."""
    parser = FoamRunParser(end_time=1.0)
    parser.feed(["FOAM FATAL ERROR: boom"] + [f"line {i} " + "x" * 200 for i in range(100)])
    assert len(parser.report()["fatal_text"]) <= 2000


# ---------------------------------------------------------------- nothing is invented


def test_an_empty_log_yields_nothing_invented() -> None:
    """A log with no blocks reports zeros and empty dicts — never a guessed number."""
    parser = FoamRunParser(end_time=1.0)
    parser.feed([])
    report = parser.report()
    assert report["iterations"] == 0
    assert report["last_residuals"] == {}
    assert report["last_continuity"] == {}
    assert report["last_time"] == 0.0
    assert report["converged"] is False and report["converged_at"] is None


def test_a_field_absent_from_the_log_is_absent_from_the_dicts() -> None:
    """Only the fields the log names appear. `Uz` is not in the pitzDaily log's last block."""
    parser = FoamRunParser(end_time=1.0)
    parser.feed(
        [
            "Time = 1s",
            "smoothSolver:  Solving for Ux, Initial residual = 0.1, Final residual = 1e-06, "
            "No Iterations 2",
            "ExecutionTime = 1.0 s  ClockTime = 1 s",
        ]
    )
    assert set(parser.report()["last_residuals"]) == {"Ux"}


@pytest.mark.parametrize(
    "line",
    (
        "Time = notanumber",
        "Time =",
        "smoothSolver: Solving for Ux, Initial residual = x, Final residual = y, No Iterations z",
        "ExecutionTime =  s  ClockTime =  s",
        "time step continuity errors : sum local = , global = ",
        "x" * 5000,
        "",
    ),
)
def test_a_malformed_line_never_raises(line: str) -> None:
    """A log is output from a program we do not control; a parser that throws turns a finished
    run into
    a failed job."""
    parser = FoamRunParser(end_time=1.0)
    assert parser.feed([line]) == [] or True
    parser.report()


def test_the_convergence_line_is_read_when_present() -> None:
    """**The fixture does NOT contain it** — the tutorial is a transient PIMPLE run, so
    `SIMPLE solution converged` cannot appear (see `.coordination/T213-VERIFY-FORMATS.md`).
    This checks
    the pattern against the text T-212's steady duct run prints."""
    parser = FoamRunParser(end_time=1.0)
    parser.feed(["SIMPLE solution converged in 64 iterations"])
    report = parser.report()
    assert report["converged"] is True
    assert report["converged_at"] == 64


def test_the_fixture_has_no_convergence_line(
    good: tuple[FoamRunParser, list[tuple[str, dict]]],
) -> None:
    """The claim above, asserted against the fixture: a transient run never converges, so
    `converged` stays False and `converged_at` stays None."""
    parser, _ = good
    report = parser.report()
    assert report["converged"] is False
    assert report["converged_at"] is None


# ---------------------------------------------------------------- energy (ADR-0027 step 4)

#: **Synthetic, written by hand** to mirror OpenFOAM 14 `foamRun -solver fluid` line forms; no
#: real energy run exists on this machine yet (ADR-0027 step 5 runs one).
ENERGY = FIXTURES / "foamRun.fluidEnergy.synthetic.log"


def test_the_energy_log_reads_e_and_drops_the_constant_rho() -> None:
    """`Solving for e` is the energy residual; the zero-residual `rho` rows are not a solve."""
    parser, events = parse_all(ENERGY.read_text(), end_time=3.0)
    report = parser.report()
    assert report["iterations"] == 3
    assert report["ended"] is True
    assert report["last_residuals"] == {
        "Ux": pytest.approx(0.0874),
        "Uy": pytest.approx(0.0791),
        "Uz": pytest.approx(0.0823),
        "e": pytest.approx(0.0462),
        "p_rgh": pytest.approx(0.0318),
    }
    blocks = [b for name, data in events if name == "monitor.residual" for b in data["blocks"]]
    assert all("rho" not in b["residuals"] for b in blocks)
    assert [b["residuals"]["e"] for b in blocks] == pytest.approx([1.0, 0.226, 0.0462])


def test_the_energy_residual_is_labelled_energy() -> None:
    parser, events = parse_all(ENERGY.read_text(), end_time=3.0)
    assert parser.report()["labels"] == {"e": "Energy"}
    residual = [data for name, data in events if name == "monitor.residual"]
    assert residual and residual[-1]["labels"] == {"e": "Energy"}


def test_a_solved_rho_is_kept() -> None:
    """Only a zero initial residual marks rho as unsolved; a real rho solve stays visible."""
    parser = FoamRunParser(end_time=1.0)
    parser.feed(
        [
            "Time = 1s",
            "diagonal:  Solving for rho, Initial residual = 0.01, Final residual = 0, "
            "No Iterations 1",
            "ExecutionTime = 1.0 s  ClockTime = 1 s",
        ]
    )
    assert parser.report()["last_residuals"] == {"rho": pytest.approx(0.01)}


def test_an_incompressible_log_is_unchanged(
    good: tuple[FoamRunParser, list[tuple[str, dict]]],
) -> None:
    """The pitzDaily run carries no labels key anywhere, so its report and events are as before."""
    parser, events = good
    report = parser.report()
    assert "labels" not in report
    assert set(report["last_residuals"]) == {"Ux", "Uy", "p", "k", "epsilon"}
    assert all("labels" not in data for name, data in events if name == "monitor.residual")


# ---------------------------------------------------------------- the protocol


def test_it_satisfies_the_log_parser_protocol() -> None:
    """`PARSERS["foamRun"]` builds one from `parse_params`, which is what the job thread does."""
    parser = PARSERS["foamRun"](end_time=0.3)
    assert isinstance(parser, FoamRunParser)
    assert parser.feed([]) == []
    assert isinstance(parser.report(), dict)

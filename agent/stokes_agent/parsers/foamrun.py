"""Parse a `foamRun` log into residual events and one report (T-213, ADR-0005).

**The agent parses logs, not the desktop.** While `foamRun` runs, an engineer must watch the
residuals
fall; afterwards the run record needs the iteration count, the final residuals and whether the
solver
converged. This turns the solver's output into events as lines arrive and into a report at the end.

## Every pattern comes from the fixture, not from memory

`tests/fixtures/of14/logs/foamRun.pitzDaily.log` is a real 17,219-line run (exit 0, 54.21 s),
and the
brief demands each expression quote a line from it. Two forms are not what one would guess:

* **`Time = 0.000119048s` carries a UNIT SUFFIX.** A pattern for `Time = <number>$` matches nothing.
* **There are TWO SPACES after the solver's colon** — `smoothSolver:  Solving for Ux, …` — and the
  solver NAME varies (`smoothSolver` for U/k/epsilon/omega, `GAMG` for p). A pattern anchored on one
  name or one space reads half the fields.

## Nothing is lost, on disk or on the wire

`feed` returns **at most one** `monitor.residual` per call, carrying every block that
completed in that
batch in a `blocks` list. The job thread's rate limiter keeps the last event per second, so a
duct at
10 to 40 blocks/s would otherwise drop samples from the live plot — batching means the
categories that
matter (`Ux`, `Uy`, `p`) all arrive, and **every block is also appended to
`<job_dir>/monitors/residuals.jsonl`**, so the full history survives even a dropped event.

## A parser may not fail a job

`feed` raising becomes `{"error": …}` in the report and nothing more (see `base.py`). The file
write is
therefore guarded: a read-only job folder costs history, not the run.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

#: The file every block is appended to, relative to the job folder. Named in the report so a reader
#: does not have to know it.
HISTORY_NAME = "monitors/residuals.jsonl"

# : After this many lines the history stops growing and the report says so. 200,000 blocks is
# far more
#: than any skeleton run and bounds the file at roughly 40 MiB.
MAX_HISTORY_LINES = 200_000

#: A line longer than this is skipped: `jobs.MAX_LINE_CHARS` cuts them anyway, and a pattern run
#: against a 4 KB line is not a parse.
MAX_LINE = 4096

# : How long `report` waits for the log to stop growing, and how often it looks. **Bounded**,
# because a
# : writer that is genuinely stuck must not hang the job — and short, because the wait happens
# only when
#: there IS unseen data and the file has just grown.
TAIL_SETTLE_S = 0.5
TAIL_POLL_S = 0.02

# : How much of a `FOAM FATAL` block is kept, so a report explains a failure without carrying
# the log.
MAX_FATAL_CHARS = 2000
FATAL_TAIL_LINES = 20

# **Every pattern below is anchored, has no nested quantifier, and quotes the fixture line it
# matches.**
#
# `Time = 0.000119048s` — **a unit suffix**, which correction 1 called out.
_TIME = re.compile(r"^Time = ([-0-9.eE+]+)([A-Za-z]*)\s*$")
# `smoothSolver: Solving for Ux, Initial residual = 1, Final residual = 2.88483e-06, No
# Iterations 6`
# — **two spaces** after the colon, and the solver name is not fixed (`GAMG` for p).
_SOLVE = re.compile(
    r"^\S+:\s+Solving for (\w+), Initial residual = ([-0-9.eE+]+), "
    r"Final residual = ([-0-9.eE+]+), No Iterations (\d+)\s*$"
)
# `time step continuity errors : sum local = 1.75187e-05, global = 4.35307e-06`
# **`cumulative` is present only on the LAST p-solve of a step**, so it is optional here.
_CONTINUITY = re.compile(
    r"^time step continuity errors : sum local = ([-0-9.eE+]+), "
    r"global = ([-0-9.eE+]+)(?:, cumulative = ([-0-9.eE+]+))?\s*$"
)
# `ExecutionTime = 0.346341 s ClockTime = 1 s` — **two spaces** before `ClockTime`. This
# closes a block.
_EXECUTION = re.compile(r"^ExecutionTime = ([-0-9.eE+]+) s\s+ClockTime = ([-0-9.eE+]+) s\s*$")
# `SIMPLE solution converged in 64 iterations` — **not present in the fixture**, which is a
# transient
# PIMPLE run. T-212's duct run is the steady one and does print it; see T213-VERIFY-FORMATS.md.
_CONVERGED = re.compile(r"converged in (\d+) iterations")
_END = re.compile(r"^End\s*$")
_FATAL = re.compile(r"FOAM FATAL")

#: A field name the solver may report: `Ux`, `Uy`, `Uz`, `p`, `k`, `epsilon`, `omega`, `nut`, …
FIELD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{0,31}$")

#: Display names for residual fields whose solver name says nothing to a user (ADR-0027 step 4).
#: `e` is the `fluid` solver's sensible internal energy. **Reported beside the residuals, never
#: instead of them**: the residual keys stay the solver's own names, so history files and existing
#: clients keep reading the same keys, and only a log that solves a labelled field carries labels.
RESIDUAL_LABELS: dict[str, str] = {"e": "Energy"}

#: Fields the `fluid` solver prints but does not solve. With `rhoConst` OpenFOAM 14 still prints a
#: `Solving for rho` row whose initial residual is exactly 0; plotting it would put a flat line at
#: zero on a log axis. A row with a non-zero residual is kept, since then rho really was solved.
_UNSOLVED_WHEN_ZERO = frozenset({"rho"})


class FoamRunParser:
    """`foamRun`'s residuals, one block per iteration.

    ``end_time`` is the run's `endTime`, used only for the progress fraction. ``job_dir`` is
    where the
    history is appended; **None means nothing is written**, which is what the unit tests pass.
    """

    def __init__(
        self, *, end_time: float = 0.0, job_dir: Path | None = None, log_name: str = "log.solve"
    ) -> None:
        self._end_time = float(end_time)
        self._job_dir = Path(job_dir) if job_dir is not None else None
        #: The step's log, which `read_tail` re-reads. The supervisor names it `log.<step>`.
        self._log_name = str(log_name)
        # : How far `read_tail` has consumed. `feed` does not track this — it is given lines,
        # not bytes.
        self._read_offset = 0
        #: How many log lines `feed` has been given, so `read_tail` can feed only the new ones.
        self._lines_fed = 0
        self._blocks = 0
        self._last_time = 0.0
        self._converged = False
        self._converged_at: int | None = None
        self._ended = False
        self._fatal = False
        self._fatal_text = ""
        self._fatal_lines: list[str] = []
        self._last_residuals: dict[str, float] = {}
        self._last_continuity: dict[str, float] = {}
        self._execution_s = 0.0
        self._truncated = False
        self._history_failed = False
        # The block in progress. A block is complete when `ExecutionTime` arrives.
        self._time = 0.0
        self._residuals: dict[str, float] = {}
        self._continuity: dict[str, float] = {}
        #: Display labels for the fields seen so far that have one (`e` -> `Energy`). Empty for an
        #: incompressible log, and then the reports carry no `labels` key at all.
        self._labels: dict[str, str] = {}

    # ------------------------------------------------------------------ feed

    def feed(self, lines: list[str]) -> list[tuple[str, dict[str, Any]]]:
        """Consume new lines; return the events for at most ONE completed block.

        **One event per call, with every completed block inside it.** The job thread keeps
        only the last
        event per second per name, so returning one event per block would drop samples on a
        fast run;
        batching them into `blocks` means the limiter can coalesce without losing any.
        """
        blocks: list[dict[str, Any]] = []
        self._lines_fed += len(lines)
        for raw_line in lines:
            if len(raw_line) > MAX_LINE:
                continue
            # Multi-region logs indent global time and execution lines. Region
            # residuals remain separate and are not merged into a single field.
            line = raw_line.lstrip()
            if self._fatal:
                # Keep a bounded tail of the failure, then nothing more matters.
                if len(self._fatal_lines) < FATAL_TAIL_LINES:
                    self._fatal_lines.append(line)
                continue
            match = _TIME.match(line)
            if match is not None:
                self._time = float(match.group(1))
                self._residuals = {}
                self._continuity = {}
                continue
            match = _SOLVE.match(line)
            if match is not None:
                self._take_solve(match.group(1), float(match.group(2)))
                continue
            match = _CONTINUITY.match(line)
            if match is not None:
                self._continuity = {
                    "local": float(match.group(1)),
                    "global": float(match.group(2)),
                }
                if match.group(3) is not None:
                    self._continuity["cumulative"] = float(match.group(3))
                continue
            match = _EXECUTION.match(line)
            if match is not None:
                blocks.append(self._close_block(float(match.group(1))))
                continue
            match = _CONVERGED.search(line)
            if match is not None:
                self._converged = True
                self._converged_at = int(match.group(1))
                continue
            if _END.match(line):
                self._ended = True
                continue
            if _FATAL.search(line):
                self._fatal = True
                self._fatal_lines = [line]
                continue
        if not blocks:
            return []
        payload = {"time": self._last_time, "blocks": blocks}
        fraction = 1.0 if self._end_time <= 0 else min(1.0, self._last_time / self._end_time)
        payload["residuals"] = dict(self._last_residuals)
        payload["continuity"] = dict(self._last_continuity)
        payload["execution_s"] = self._execution_s
        if self._labels:
            payload["labels"] = dict(self._labels)
        return [
            ("monitor.residual", payload),
            (
                "job.progress",
                {"fraction": fraction, "message": f"iteration {self._blocks}"},
            ),
        ]

    def flush(self) -> list[tuple[str, dict[str, Any]]]:
        """The events a rate limiter is holding. **The last samples of a run are the ones a
        report is built from**, so `_flush` calls this rather than letting the limiter drop them.

        Nothing is emitted here: `feed` already returns every block it saw, and the job thread holds
        whatever its limiter deferred. Returning empty is honest — there is no deferred state
        in this
        parser, because the batching is what stops loss.
        """
        return []

    # ------------------------------------------------------------------ report

    def read_tail(self) -> None:
        """Re-read the log and consume anything `feed` has not seen. **Never raises.**

        **It must not raise, and that is why it is wrapped.** `_StepParser.report` calls this
        inside a
        `try/except` that turns ANY exception into `{"error": ...}` — so a `read_tail` that threw
        replaced the whole report, and the symptom was a run with every residual and the iteration
        count correct but **`converged` absent**. Measured at 3 runs in 12. The residual counts
        survived because they are also written to the history file, which is what made the
        failure look
        like a lost flag rather than a lost report.

        **And it reads for a reason.** `report()` is the last thing to run, and the process's final
        lines can still be missing when the supervisor stops reading: for a solve those are
        `SIMPLE solution converged in N iterations` and `End`, which live AFTER the final block. A
        parser with no `job_dir` (a unit test) does nothing, which is why the tests can feed lines
        directly and still see this behaviour.
        """
        try:
            self._read_tail()
        except Exception:
            return
        try:
            self._read_tail()
        except Exception:
            return

    def _read_tail(self) -> None:
        """The body of `read_tail`, which may raise; the caller makes it safe."""
        if self._job_dir is None:
            return
        path = self._job_dir / self._log_name
        try:
            size = path.stat().st_size
        except OSError:
            return
        # **Wait for the writer to stop.** Measured: the process can be reaped while its last lines
        # are still in flight, and `report()` then reads a file that is correct but INCOMPLETE — the
        # iteration count right and the convergence line missing, one run in eight. A bounded
        # wait on
        # the file's size is the difference between a race and a guarantee, and it is bounded so a
        # genuinely stuck writer cannot hang the job.
        deadline = time.monotonic() + TAIL_SETTLE_S
        while time.monotonic() < deadline:
            time.sleep(TAIL_POLL_S)
            try:
                grown = path.stat().st_size
            except OSError:
                break
            if grown <= size:
                break
            size = grown
        try:
            with path.open("rb") as handle:
                handle.seek(0)
                text = handle.read().decode("utf-8", errors="replace")
        except OSError:
            return
        self._read_offset = size
        # **Feed only the lines not already seen.** `feed` is NOT idempotent: every `ExecutionTime`
        # line closes a block, and closing a block **increments the iteration count and
        # APPENDS to the
        # history file**. Re-feeding the whole log therefore wrote the run out again — measured, a
        # 66-iteration solve left 198 lines (3 x 66) and reported `iterations: 198`, which
        # looked from
        # outside like a lost convergence flag and cost most of a round to find.
        #
        # Counting LINES is the honest measure of progress here: `feed` is given lines, not
        # bytes, so
        # the parser cannot know a byte offset, and the supervisor's reads do not align with
        # its own.
        fresh = text.splitlines()[self._lines_fed :]
        if fresh:
            self.feed(fresh)

    def report(self) -> dict[str, Any]:
        """The step's summary, written whatever the exit code."""
        self.read_tail()
        report: dict[str, Any] = {
            "iterations": self._blocks,
            "last_time": self._last_time,
            "converged": self._converged,
            "converged_at": self._converged_at,
            "ended": self._ended,
            "fatal": self._fatal,
            "fatal_text": "".join(self._fatal_lines)[:MAX_FATAL_CHARS],
            "last_residuals": dict(self._last_residuals),
            "last_continuity": dict(self._last_continuity),
            "execution_s": self._execution_s,
            "history_file": HISTORY_NAME if self._history_usable() else None,
            "history_truncated": self._truncated,
        }
        if self._labels:
            report["labels"] = dict(self._labels)
        return report

    # ------------------------------------------------------------------ internals

    def _take_solve(self, field: str, initial: float) -> None:
        """Record one `Solving for <field>` line's initial residual in the block in progress."""
        if field in _UNSOLVED_WHEN_ZERO and initial == 0.0:
            return
        # **The first occurrence in a block wins**: the solver solves p several times, and the
        # *initial* residual of the first solve is the iteration's convergence measure.
        if field not in self._residuals and FIELD_RE.match(field):
            self._residuals[field] = initial
            if field in RESIDUAL_LABELS:
                self._labels[field] = RESIDUAL_LABELS[field]

    def _close_block(self, execution_s: float) -> dict[str, Any]:
        """Finish the block in progress, append it to the history, and return its event data."""
        self._blocks += 1
        self._last_time = self._time
        self._last_residuals = dict(self._residuals)
        self._last_continuity = dict(self._continuity)
        self._execution_s = execution_s
        block: dict[str, Any] = {
            "iteration": self._blocks,
            "time": self._time,
            "residuals": dict(self._residuals),
            "continuity": dict(self._continuity),
            "execution_s": execution_s,
        }
        self._append(block)
        return block

    def _history_usable(self) -> bool:
        return self._job_dir is not None and not self._history_failed

    def _append(self, block: dict[str, Any]) -> None:
        """Append one block to the history file, in append mode and flushed.

        **Guarded on purpose**: a parser may not fail a job (see `base.py`), and a job folder
        that is
        read-only or gone must cost the history file rather than the run. The report then says
        `history_file: null` rather than naming a file that is not there.
        """
        if self._job_dir is None or self._history_failed:
            return
        if self._truncated:
            return
        try:
            path = self._job_dir / HISTORY_NAME
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(block, separators=(",", ":")) + "\n")
                handle.flush()
        except OSError:
            self._history_failed = True
            return
        if self._blocks >= MAX_HISTORY_LINES:
            self._truncated = True

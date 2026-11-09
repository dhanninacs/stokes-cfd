"""Run control shared by both OF14 case writers: the graceful Stop and the resume start.

**Stop writes, it does not kill.** Every case carries `#includeFunc stopAtFile(action=writeNow)`
(`$FOAM_ETC/caseDicts/functions/control/stopAtFile`). When the file named after that function
appears in the case, the solver finishes the time step, writes it and ends with exit 0, so the
run's later steps (reconstructPar) run, its fields can be read, and a later run can resume from
it. The agent's `job.stop` creates the file; its name is the call text, because `stopAtFile`
watches `$FOAM_CASE/<function name>` and an `#includeFunc` with arguments is named by its call
text. `stokes_agent.jobs.STOP_FILE` holds the same text and a test pins the two together.

**Resume starts from the newest written time** (`startFrom latestTime`). The case is otherwise the
one a fresh run writes, so the basis, the boundary conditions and the solver settings are the
model's own; only the start time moves. `endTime` keeps its meaning (the iteration count, or the
physical end time), so a run stopped at 120 of 500 iterations resumes and ends at 500.
"""

from __future__ import annotations

import re

from stokes.compile.errors import CompileInvalid
from stokes.compile.foam import Entry, FuncInclude, Word
from stokes.compile.snapshot import SolveSnapshot
from stokes.engines.turbulence import model as momentum_model

#: The function call the case writes and the name of the file `job.stop` creates.
STOP_FUNC = "stopAtFile(action=writeNow)"

#: The `startFrom` line of a rendered controlDict: the key at the start of a line, one word, `;`.
_START_FROM = re.compile(r"^(?P<lead>[ \t]*startFrom[ \t]+)[A-Za-z]+;", re.MULTILINE)


def stop_function() -> FuncInclude:
    """The `functions` item that lets Stop write the current iteration (see the module doc)."""
    return FuncInclude(STOP_FUNC, "Stop writes the current iteration and ends the run")


def transient_write(snap: SolveSnapshot) -> list[Entry]:
    """`writeControl`/`writeInterval`, and the adaptive step's `adjustTimeStep`/`maxCo`.

    Steady runs, and transient runs with every control off, get exactly the two entries they always
    had (`timeStep`, `write_interval`), so their cases and goldens do not change. With a write
    time the interval is physical seconds - `adjustableRunTime` when the step adapts, so the step
    shortens to land on each write time, `runTime` when it is fixed.
    """
    adaptive = snap.transient and snap.max_courant > 0
    items: list[Entry] = []
    if snap.transient and snap.write_time > 0:
        control = "adjustableRunTime" if adaptive else "runTime"
        items += [Entry("writeControl", Word(control)), Entry("writeInterval", snap.write_time)]
    else:
        items += [
            Entry("writeControl", Word("timeStep")),
            Entry("writeInterval", snap.write_interval),
        ]
    if adaptive:
        items += [Entry("adjustTimeStep", Word("yes")), Entry("maxCo", snap.max_courant)]
    return items


def average_function(snap: SolveSnapshot, energy_field: str | None = None) -> list[FuncInclude]:
    """`#includeFunc fieldAverage(...)` for a transient run that asked for averages, else nothing.

    U and p always; the temperature with energy; `k` for a model that transports it, else `nut`
    for any turbulence model - the averaged eddy viscosity is what an LES result is read with.
    """
    if not (snap.transient and snap.average):
        return []
    fields = ["U", "p"]
    if energy_field:
        fields.append(energy_field)
    spec = momentum_model(snap.turbulence)
    if "k" in spec.fields:
        fields.append("k")
    elif spec.family != "laminar":
        fields.append("nut")
    start = f", timeStart={snap.average_start:g}" if snap.average_start > 0 else ""
    return [
        FuncInclude(
            f"fieldAverage({', '.join(fields)}{start})",
            "time averages from Average from: UMean, pMean, ...",
        )
    ]


def resume_control_text(text: str) -> str:
    """A rendered controlDict with `startFrom latestTime;`, for a run continuing a stopped one.

    Works on the rendered text so the snapshot, and with it the run basis, is unchanged: a resumed
    run is the same case started later. Exactly one `startFrom` line must be there; anything else
    is a writer this function does not know, refused rather than guessed at.
    """
    found = _START_FROM.findall(text)
    if len(found) != 1:
        raise CompileInvalid(
            "the controlDict has no single startFrom entry to resume from",
            details={"code": "compile.invalid", "found": len(found)},
        )
    return _START_FROM.sub(lambda m: f"{m.group('lead')}latestTime;", text)

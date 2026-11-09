"""The run record: one solver run, as the model and the disk see it (ADR-0023, T-214b).

A record is a **plain dataclass**, not a node. ADR-0023's reason is worth keeping in view: a
run is an
*operation* with a lifetime, not a parameter of the model — it is created, it progresses, it
ends, and
undo must not resurrect it. So the tree holds the settings and the mesh result, and the record holds
what happened when the solver ran.

## Why the state list is closed

`RUN_STATES` is the complete set, and every consumer branches on it. A new state without a
consumer is a
run that shows nothing.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

#: Every state a run can be in, and what each means to a user:
#:
#: * `queued` — the job is created, the solver has not started.
#: * `running` — the solver is working.
#: * `cancelling` — a cancel was asked for and the process has not gone yet.
#: * `done` — it finished and the report says it converged.
# : * `done_unconverged` — it finished and did not converge. **Not a failure**: many real runs
# stop at
#:   the iteration limit with residuals still falling, and calling that `failed` would be a lie.
#: * `failed` — a step exited non-zero, or the report says `fatal`.
#: * `cancelled` — a cancel reached it.
#: * `interrupted` — the agent lost the job (a restart, a lost host), so its outcome is unknown.
# : * `skipped` — the host cannot run it: no OpenFOAM, or no solver. **Recorded rather than
# refused**, so
#:   a replay reproduces the same project state on a machine without OpenFOAM.
RUN_STATES: tuple[str, ...] = (
    "queued",
    "running",
    "cancelling",
    "done",
    "done_unconverged",
    "failed",
    "cancelled",
    "interrupted",
    "skipped",
)

#: The states a run never leaves.
FINAL_STATES: tuple[str, ...] = (
    "done",
    "done_unconverged",
    "failed",
    "cancelled",
    "interrupted",
    "skipped",
)

#: The final states whose solved case is worth extracting fields from.
#:
#: **`done_unconverged` is on this list, and leaving it off was a defect.** A run that
#: reached its iteration cap has still solved: it wrote its time directories and its
#: artifacts exist on the host.
#: Asking only for `done` meant `run.fields` answered `surfaces: "missing"` for it, so there was
#: nothing to plot — measured on the demo pipe, 300 time steps and residuals down to 1e-4 with
#: `record.fields == []`. An unconverged run is an ordinary result; its residuals and its last
#: iteration are what a user looks at *because* it did not converge.
#:
#: `failed`, `cancelled`, `interrupted` and `skipped` are correctly absent: there is no
#: solved case to read, so the request would only add a refused round trip.
FIELD_STATES: tuple[str, ...] = ("done", "done_unconverged")

#: The engine of an imported OpenFOAM case's runs (ADR-0029): `stokes.run.external` runs them.
EXTERNAL_ENGINE = "of14.external"

# : A record is capped at this size on disk. A run that wants more is a run whose history
# belongs in the
#: agent's own monitors file, which is what `run.residuals` reads.
MAX_RECORD_BYTES = 256 * 1024


@dataclass
class RunRecord:
    """One run. **Every field is JSON**, because a record is written after every change.

    `basis` is what makes reuse and staleness decidable: two runs with the same basis are the
    same case,
    and a record whose basis no longer matches the model's is stale.
    """

    id: str
    kind: str = "simulation"
    name: str = ""
    state: str = "queued"
    stale: bool = False
    engine: str = ""
    host: str = ""
    job: str = ""
    mesh_result: str = ""
    mesh_basis: str = ""
    mesh_job: str = ""
    basis: str = ""
    settings_id: str = ""
    iterations: int = 0
    converged: bool = False
    last_residuals: dict[str, float] = field(default_factory=dict)
    monitors: dict[str, Any] = field(default_factory=dict)
    fields: list[str] = field(default_factory=list)
    slice: bool = False
    timings: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    created: str = ""
    updated: str = ""
    #: Ended by Stop: the solver wrote the iteration it was on and finished, so the run is a
    #: result (``done_unconverged``) that can be looked at and resumed, not a cancelled one.
    stopped: bool = False
    #: The run this one resumed from (``run.resume``), or "" for a run that started at zero.
    resumed_from: str = ""

    #: **Where this record was read from, and NOT part of its JSON.** The store writes a record to
    #: the folder it came from, which matters when the session has already moved to another
    #: run closed out for the project being left must not land in the incoming one (Opus item 11).
    #: Excluded from `to_dict` deliberately, so the on-disk shape is unchanged.
    origin: Path | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("origin", None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunRecord:
        """A record from JSON, **ignoring keys this build does not know**.

        A record written by a newer build is not corrupt, it is merely newer: refusing it
        would make a
        project un-openable after a downgrade, and the fields that remain are still true.
        """
        known = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in known})

    @property
    def final(self) -> bool:
        """Whether the run has stopped for good."""
        return self.state in FINAL_STATES

    @property
    def successful(self) -> bool:
        """Whether the solver worked, whether or not it converged."""
        return self.state in ("done", "done_unconverged")

    def encode(self) -> str:
        """The record as the JSON that goes on disk, with the cap applied to what is written."""
        text = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        if len(text.encode("utf-8")) <= MAX_RECORD_BYTES:
            return text
        # **The residual history is what makes a record big**, and it is the one part that is
        # redundant:
        # the agent's `monitors/residuals.jsonl` holds every block. So the summary is kept and the
        # history is dropped, with a marker, rather than refusing to write the record at all.
        lean = self.to_dict()
        lean["last_residuals"] = {}
        lean["timings"] = {}
        lean["truncated"] = True
        return json.dumps(lean, sort_keys=True, separators=(",", ":"))

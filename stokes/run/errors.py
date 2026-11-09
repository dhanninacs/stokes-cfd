"""The run-layer error codes (ADR-0005, T-144c).

One place for them, so a client can branch on a code and a test can assert it — the same rule
the mesh
errors follow.
"""

from __future__ import annotations

from stokes.errors import StokesError


class RunError(StokesError):
    """Base for every failure of a runner or a host."""

    code = "run.error"


class RunnerUnavailable(RunError):
    """This machine cannot run the host: no OpenFOAM, or the wrong platform.

    A distinct code from `UnknownHost`, because the two mean different things to a user:
    one is "this machine cannot do it" and the other is "there is no such host".
    """

    code = "run.unavailable"


class UnknownHost(RunError):
    """No host with that name is registered."""

    code = "run.unknown_host"


class AgentCallFailed(RunError):
    """The agent refused or failed the call. Wraps the remote error's code when there is one."""

    code = "run.agent_failed"


class SolveInvalid(RunError):
    """The set-up cannot become a case: no mesh, a stale mesh, no material, an unknown engine.

    A **validation** error rather than a runtime one: `solve.check` reports these before a run
    starts, so
    a user sees what is wrong while they can still change it. The `code` carries the specific reason
    (`solve.no_mesh`, `solve.mesh_stale`, `solve.no_material`, `solve.unsupported_kind`, …)
    because a
    client branches on that, not on the class.
    """

    code = "solve.invalid"


class SolvePartExcluded(SolveInvalid):
    """The domain part is a body the user left out of the simulation (T-221).

    **A subclass rather than a `details={"code": …}`**, the trap that has cost me three
    repetitions: the code is a CLASS attribute, so a code passed in `details` is silently
    ignored and the caller sees the parent's `solve.invalid`. The mesher has the same refusal
    (`mesh.part_excluded`): a body left out cannot be what a case is built from, and the two
    layers must say so in words a caller can branch on rather than in prose.
    """

    code = "solve.part_excluded"


class RunBusy(RunError):
    """A run of this project is already going and the caller asked for one at a time."""

    code = "run.busy"


class RunNotFound(RunError):
    """No run with that id in this project."""

    code = "run.not_found"

"""The mesh error types (ADR-0024, T-143).

One place for the codes, so that a client can branch on them and a test can assert them. Later tasks
(the job runner, the drivers) use `NoHost`, `MeshBusy` and `MeshNotBuilt`; they are defined here now
because a code that appears in two modules is a code that will drift.
"""

from __future__ import annotations

from stokes.errors import StokesError


class MeshError(StokesError):
    """Base for every mesh failure."""

    code = "mesh.error"


class MeshInvalid(MeshError):
    """The request cannot be honoured: an unknown mesher, a bad box, a size that is not positive."""

    code = "mesh.invalid"


class MeshPartExcluded(MeshInvalid):
    """The part the mesh would be built on is left out of the simulation (T-221).

    **A subclass rather than `details={"code": …}`**, which is the trap this file's own pattern
    avoids: `code` is a CLASS attribute on every `StokesError`, so a code passed in `details` is
    ignored and the caller sees the parent's. The first version of this check did exactly that, and
    the test caught it by asserting the code rather than only the message.
    """

    code = "mesh.part_excluded"


class MeshNotBuilt(MeshError):
    """The chosen mesher has no driver yet. The message carries the catalogue's reason."""

    code = "mesh.not_built"


#: What to tell a user whose host has no OpenFOAM. **It says what to DO, not just what is wrong.**
#: The plain "the host has no OpenFOAM install" reads as a hardware fault and leaves the reader with
#: nothing to act on - and the commonest cause by far is a machine whose install is somewhere the
#: agent cannot look: it discovers `/usr/lib/openfoam*`-style prefixes, and a user-space install (a
#: tarball under `~/opt`, which is a normal way to have one) is invisible to it. Measured on this
#: box: with the variable unset, `bashrc_candidates()` is empty; with it set, the install is found.
NO_INSTALL_ADVICE = (
    "the host has no OpenFOAM install: set STOKES_OPENFOAM_BASHRC to your OpenFOAM environment "
    "script (the file that exports WM_PROJECT_DIR), or install OpenFOAM under a standard prefix"
)


class NoHost(MeshError):
    """The named host is not reachable, or has no OpenFOAM. Used from T-144c on."""

    code = "mesh.no_host"


class MeshBusy(MeshError):
    """A mesh job is already running for this project. Used from T-144a on."""

    code = "mesh.busy"

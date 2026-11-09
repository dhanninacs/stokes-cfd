"""Shared case files and serial utilities used to prepare MPI OpenFOAM cases."""

from __future__ import annotations

from stokes.compile.foam import Dict, Entry, Tuple_, Word, render_file
from stokes.cores import core_count
from stokes.errors import ValidationError


def checked_cores(value: object) -> int:
    """Return an accepted process count shared by the case writers.

    The writers raise `MeshInvalid`/`CompileInvalid`, not `ValidationError`, so the
    shared rule arrives here as the value error those messages already expect.
    """
    try:
        return core_count(value)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


def decompose_par_dict(cores: int) -> str:
    """Write a portable geometric decomposition for the requested MPI rank count.

    FoamWin's packaged MPI smoke test uses `simple` decomposition. Keeping all splits on x avoids
    optional graph partitioning libraries and works with the native Windows distribution.
    """
    ranks = checked_cores(cores)
    if ranks < 2:
        raise ValueError("a decomposeParDict is only needed for two or more MPI ranks")
    body = Dict(
        (
            Entry("numberOfSubdomains", ranks),
            Entry("method", Word("simple")),
            Entry(
                "simpleCoeffs",
                Dict((Entry("n", Tuple_((ranks, 1, 1))), Entry("delta", 0.001))),
            ),
        )
    )
    return render_file("decomposeParDict", body, location="system")


def decompose_step(*, no_fields: bool = False) -> dict[str, object]:
    """The serial `decomposePar` step; its MPI ranks are launched separately afterward."""
    argv = ["decomposePar"]
    if no_fields:
        argv.append("-noFields")
    return {"name": "decomposePar", "argv": argv, "timeout_s": 600}


def reconstruct_mesh_step() -> dict[str, object]:
    """Reassemble snappy's processor meshes into the serial mesh artifact Stokes consumes."""
    return {
        "name": "reconstructPar",
        "argv": ["reconstructPar", "-constant", "-noFields"],
        "timeout_s": 900,
    }


def reconstruct_fields_step() -> dict[str, object]:
    """Reassemble all solver time directories for the normal result readers.

    **No `-fields` list on purpose**: `reconstructPar` with no list reconstructs every field the
    processors hold, so an energy run's `T` and `p_rgh` come back with `U` and `p` without the step
    having to know the engine. `reconstruct_fields_check` is what says which ones must be there.
    """
    return {"name": "reconstructPar", "argv": ["reconstructPar"], "timeout_s": 1800}


#: The fields an energy (`of14.fluid`) run must have once reconstructed (ADR-0027 step 4): the
#: initial conditions the writer stages under `0/`, and the solved fields every written time
#: directory holds. `p_rgh` is the solver's own variable; `p` is the absolute pressure it derives.
ENERGY_INITIAL_FILES: tuple[str, ...] = ("0/T", "0/U", "0/p", "0/p_rgh")
ENERGY_TIME_FIELDS: tuple[str, ...] = ("T", "U", "p", "p_rgh")


def reconstruct_fields_check(*, energy: bool) -> dict[str, tuple[str, ...]] | None:
    """What a reconstructed run must hold, or None when there is nothing extra to check.

    **Only an energy run is checked**: an incompressible run's reconstruct is unchanged and has
    always been judged by its readers. For an energy run a missing `T` would otherwise surface
    later as a Results list without temperature, which reads as a display bug, not a lost field.
    """
    if not energy:
        return None
    return {"initial": ENERGY_INITIAL_FILES, "time_fields": ENERGY_TIME_FIELDS}


def missing_reconstructed(
    check: dict[str, tuple[str, ...]] | None,
    initial_files: list[str] | tuple[str, ...],
    time_fields: list[str] | tuple[str, ...],
) -> list[str]:
    """The names `check` requires that the reconstructed case lacks, `0/` files first.

    ``initial_files`` are case-relative paths under `0/`; ``time_fields`` are the field names in
    the latest reconstructed time directory. Pure, so the rule is tested without a run.
    """
    if check is None:
        return []
    have_initial = set(initial_files)
    have_fields = set(time_fields)
    missing = [name for name in check["initial"] if name not in have_initial]
    missing += [name for name in check["time_fields"] if name not in have_fields]
    return missing

"""The shipped sample projects: a **read-only template** and the working copy made from it.

**The owner's requirement**, via claude-win2's 1805 message:

> For the sample project on the start screen, create a test folder and work in it only.

So a sample is never opened in place. Opening one **copies it into a folder of its own**, and that
copy is a normal project: it appears in Recent, it can be renamed by Save as, and whatever the user
does to it the **template is untouched**.

## Why a registry rather than one hard-coded demo

The list is data - id, title, description - so a second sample is a new entry rather than a branch.
**The builder is a callable**, because each sample makes its own content: today one generates a STEP
from public OCP primitives, and a later one might copy fixture files or run a script. Keeping that
behind one field is what stops the copy logic from growing a case per sample.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Where the copies go, under the user's VISIBLE Stokes folder. **One folder per copy**, numbered,
#: so opening a sample twice gives two independent projects rather than one that overwrites itself.
#:
#: **A sibling of `Projects`, not a folder inside it** (the owner's 0700 order, and the path
#: the spec names): a sample copy is a project the user may edit and keep, so it belongs beside
#: their own work rather than hidden inside a folder they did not make.
SAMPLES_FOLDER = "Samples"

#: The builders, registered by the package that owns them.
#:
#: **Registration rather than import** (the layering gate, measured): `stokes.model` is headless and
#: the pipe sample is built from OCP primitives in `stokes.app`, so importing it here - even lazily
#: inside the function - is the violation `tools/check_forbidden_imports.py` refuses. The direction
#: is inverted instead: `stokes.app` registers its builder at import time, and this module only
#: describes the samples.
_BUILDERS: dict[str, Callable[[Any], None]] = {}


def register(sample_id: str, build: Callable[[Any], None]) -> None:
    """Record how to build one sample. **Called by the package that owns the geometry** -
    `stokes.app` for anything needing OCP."""
    _BUILDERS[sample_id] = build


@dataclass(frozen=True)
class Sample:
    """One shipped sample: what to show, and how to make a copy of it."""

    id: str
    title: str
    description: str
    #: Fills a NEW session's project. Called with the session, which is already holding an empty
    #: project at the copy's path.
    build: Callable[[Any], None]


def registry() -> list[Sample]:
    """Every shipped sample, in the order the start screen shows them."""
    return [
        Sample(
            id="pipe",
            title="Flanged pipe",
            description=(
                "A flanged pipe with a flow-through bore, meshed and ready to solve. "
                "Opens as a copy, so you can change anything."
            ),
            build=_BUILDERS.get("pipe", _unregistered),
        )
    ]


def _unregistered(session: Any) -> None:
    """What a sample with no registered builder does: **say so rather than build nothing**.

    A build without the optional `cad` extra has no OCP and so no builder, and a sample that
    silently produced an empty project would look like a bug in everything downstream of it.
    """
    from stokes.errors import StokesError

    raise StokesError(
        "this build cannot make that sample: its geometry needs the optional `cad` extra (OCP)",
        code="project.sample_unavailable",
    )


def find(sample_id: str) -> Sample | None:
    """The sample with this id, or None. **A lookup, so an unknown id is a value to handle** rather
    than an exception raised from a listing."""
    for sample in registry():
        if sample.id == sample_id:
            return sample
    return None


def next_folder(sample_id: str, home: Path) -> Path:
    """`<home>/projects/samples/<id>-<NN>.stokes`, the first free number, **created**.

    **Numbered rather than reused**: opening a sample a second time must give a second independent
    project, so nothing is ever written over an existing copy. The caller gets a folder that exists
    and is empty, which is what `project.new`'s own rules require.

    **`.stokes` is part of the name** because `project_dir` appends it when absent, and preparing a
    folder that is not the one used would check nothing - the mistake measured in `project.new`.
    """
    from stokes.model.folders import prepare
    from stokes.model.persist import project_dir

    base = home / SAMPLES_FOLDER
    number = 1
    while True:
        candidate = project_dir(base / f"{sample_id}-{number}")
        if not candidate.exists():
            return prepare(candidate)
        number += 1


def describe(sample: Sample, home: Path) -> dict[str, Any]:
    """What the start screen needs: the tile, and **where a copy would go**.

    The location is computed rather than reserved, so showing it costs no folder and cannot fail on
    a read-only home. `open` computes it again when the user commits.
    """
    return {
        "id": sample.id,
        "title": sample.title,
        "description": sample.description,
        "folder": str(next_free_path(sample.id, home)),
    }


def next_free_path(sample_id: str, home: Path) -> Path:
    """As `next_folder` but **without creating anything**, for a listing."""
    from stokes.model.persist import project_dir

    base = home / SAMPLES_FOLDER
    number = 1
    while True:
        candidate = project_dir(base / f"{sample_id}-{number}")
        if not candidate.exists():
            return candidate
        number += 1

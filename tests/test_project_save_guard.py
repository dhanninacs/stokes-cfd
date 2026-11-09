"""A save must not write an empty tree over a project file that holds parts (2026-11-01).

`pipe4.stokes` - four parts, a mesh, a converged run - was found as a 1.8 kB file with an empty
geometry group, its `runs/` and `cache/` folders intact. Whatever path wrote it, the guard here is
the one every `project.save` goes through.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes.api import Session
from stokes.errors import StokesError
from stokes.model import Node, Project


def _with_parts(name: str, count: int) -> Project:
    project = Project.new(name)
    parts = project.add("/geometry", "group", "parts")
    for index in range(count):
        node = Node(
            f"n_{index:08x}",
            "part",
            f"solid_{index + 1}",
            {"source": "C:/models/a.step", "solid": index, "units": "mm", "visible": True},
            parent=parts,
        )
        parts.children.append(node)
    return Project(project.root, name=name)


def test_a_save_with_no_parts_over_a_file_with_parts_is_refused(tmp_path: Path) -> None:
    session = Session()
    folder = tmp_path / "p.stokes"
    session.execute("project.new", name="p", path=str(folder))
    session.project = _with_parts("p", 3)
    session.execute("project.save")
    before = (folder / "project.json").read_bytes()
    assert before.count(b'"type": "part"') == 3

    session.project = Project.new("p")  # the geometry group is empty again
    with pytest.raises(StokesError) as caught:
        session.execute("project.save")
    assert caught.value.code == "project.save_would_drop_parts"
    assert (folder / "project.json").read_bytes() == before, "the file was not touched"

    # Save as is the way out: a new folder, the file there says what the memory says.
    elsewhere = tmp_path / "q.stokes"
    session.execute("project.save", path=str(elsewhere))
    assert (folder / "project.json").read_bytes() == before


def test_a_project_that_never_had_parts_saves_as_before(tmp_path: Path) -> None:
    session = Session()
    folder = tmp_path / "e.stokes"
    session.execute("project.new", name="e", path=str(folder))
    session.execute("project.save")
    session.execute("project.save")  # still no parts on either side: nothing to refuse


def test_fewer_parts_is_the_users_business(tmp_path: Path) -> None:
    """Only the all-gone case is refused: deleting one body of three is an ordinary edit."""
    session = Session()
    folder = tmp_path / "f.stokes"
    session.execute("project.new", name="f", path=str(folder))
    session.project = _with_parts("f", 3)
    session.execute("project.save")
    session.project = _with_parts("f", 2)
    session.execute("project.save")
    assert (folder / "project.json").read_bytes().count(b'"type": "part"') == 2


def test_a_long_project_path_is_warned_about_on_windows() -> None:
    import os

    from stokes.api.commands.project import LONG_PATH_WARNING, path_warning

    assert path_warning(None) is None
    assert path_warning("C:/short/p.stokes") is None
    long = "C:/" + "a" * LONG_PATH_WARNING + "/p.stokes"
    # The limit is Windows'; elsewhere nothing is said.
    assert (path_warning(long) is not None) == (os.name == "nt")

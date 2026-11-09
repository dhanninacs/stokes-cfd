"""`run.fields` says whether the mesh is a 2-D case (an `empty` patch), for the first picture."""

from __future__ import annotations

from stokes.api import Session
from stokes.model import Project
from stokes.run.store import RunStore


def test_an_empty_patch_makes_the_run_two_d() -> None:
    session = Session()
    project = Project.new("flat")
    results = project.add("/mesh", "group", "results")
    result = project.add(results.id, "mesh_result", "blockmesh", {"state": "done"})
    project.add(result.id, "mesh_patch", "walls", {"patch_type": "wall"})
    session.project = project
    store = RunStore.of(session)
    assert store.two_d() is False
    project.add(result.id, "mesh_patch", "frontAndBack", {"patch_type": "empty"})
    assert store.two_d() is True


def test_no_project_is_not_two_d() -> None:
    assert RunStore.of(Session()).two_d() is False

"""A solve refuses when an inlet or outlet boundary has no patch in the mesh (the mixing elbow).

cfMesh at 5 mm cells lost the elbow's 25 mm hot inlet, checkMesh said the quality was ok, and the
run solved one cold stream: 293.4 K at the outlet against the 296.3 K energy balance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from stokes.run import snapshot
from stokes.run.errors import SolveInvalid


@dataclass
class _Node:
    id: str
    name: str
    type: str = "boundary"
    params: dict[str, Any] = field(default_factory=dict)


class _Root:
    def __init__(self, nodes: list[_Node]) -> None:
        self.nodes = nodes

    def walk(self) -> list[_Node]:
        return self.nodes


class _Project:
    def __init__(self, nodes: list[_Node], result: Any = None) -> None:
        self.root = _Root(nodes)
        self.result = result

    def find(self, ref: str) -> Any:
        return self.result if ref == "r1" else None


@dataclass
class _Result:
    children: list[_Node]


COLD = _Node("b1", "cold_inlet", params={"kind": "velocity_inlet"})
HOT = _Node("b2", "hot_inlet", params={"kind": "velocity_inlet"})
OUT = _Node("b3", "outlet", params={"kind": "pressure_outlet"})
SYM = _Node("b4", "symmetry", params={"kind": "symmetry"})


def _mesh(monkeypatch: pytest.MonkeyPatch, patches: dict[str, _Node | None]) -> None:
    monkeypatch.setattr(snapshot, "_patch_names", lambda status: tuple(patches))
    monkeypatch.setattr(snapshot, "_boundary_for", lambda project, status, patch: patches[patch])


def test_a_lost_inlet_refuses_the_solve(monkeypatch: pytest.MonkeyPatch) -> None:
    _mesh(monkeypatch, {"cold_inlet": COLD, "outlet": OUT, "unassigned": None})
    with pytest.raises(SolveInvalid) as caught:
        snapshot._require_flow_boundaries_meshed(_Project([COLD, HOT, OUT, SYM]), {})
    assert caught.value.code == "solve.boundary_not_meshed"
    assert caught.value.details == {"boundaries": ["hot_inlet"]}
    assert "hot_inlet" in caught.value.message


def test_a_patch_with_no_faces_is_a_lost_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    """What cfMesh actually wrote: `hot_inlet` in the boundary file with `nFaces 0`."""
    _mesh(monkeypatch, {"cold_inlet": COLD, "hot_inlet": HOT, "outlet": OUT})
    surfaces = [
        _Node("p1", "cold_inlet", "mesh_patch", {"faces": 149}),
        _Node("p3", "outlet", "mesh_patch", {"faces": 144}),
    ]
    project = _Project([COLD, HOT, OUT], _Result(surfaces))
    with pytest.raises(SolveInvalid) as caught:
        snapshot._require_flow_boundaries_meshed(project, {"result": "r1"})
    assert caught.value.details == {"boundaries": ["hot_inlet"]}


def test_a_lost_symmetry_plane_does_not_refuse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Its faces become wall: a different wall, not a different problem."""
    _mesh(monkeypatch, {"cold_inlet": COLD, "hot_inlet": HOT, "outlet": OUT})
    snapshot._require_flow_boundaries_meshed(_Project([COLD, HOT, OUT, SYM]), {})

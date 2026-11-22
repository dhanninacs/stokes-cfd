"""The adjoint wall sensitivity (ADR-0038 decision 3) on a synthetic channel written here: one
column of cells between two walls, with a parabolic U and Ua, so s = -nu (u_P/d)^2 exactly; and the
reader's refusals of a malformed or binary case."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from stokes_agent.foamascii import FoamReadError, read_field, read_mesh
from stokes_agent.sensitivity import wall_sensitivity

HEADER = "FoamFile\n{\n    format ascii;\n    class %s;\n    object %s;\n}\n"
NY, H = 4, 0.25  # cells across, and their height (m): the channel is 1 m high


def _write(path: Path, cls: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER % (cls, path.name) + body, encoding="ascii")


def _points() -> list[tuple[float, float, float]]:
    return [(i, j * H, k) for k in (0, 1) for j in range(NY + 1) for i in (0, 1)]


def _p(i: int, j: int, k: int) -> int:
    return i + 2 * (j + (NY + 1) * k)


def _up(j: int) -> list[int]:
    """The face at y = j H with its normal along +y."""
    return [_p(0, j, 0), _p(0, j, 1), _p(1, j, 1), _p(1, j, 0)]


def channel(case: Path, profile: float = 1.0) -> None:
    faces: list[list[int]] = []
    owner: list[int] = []
    neighbour: list[int] = []
    for j in range(1, NY):  # internal faces first
        faces.append(_up(j))
        owner.append(j - 1)
        neighbour.append(j)
    faces.append(list(reversed(_up(0))))  # lowerWall, normal -y
    owner.append(0)
    faces.append(_up(NY))  # upperWall, normal +y
    owner.append(NY - 1)
    for j in range(NY):  # the four sides of each cell, one empty patch
        y0, y1 = j, j + 1
        for quad in (
            [_p(0, y0, 0), _p(0, y1, 0), _p(0, y1, 1), _p(0, y0, 1)],
            [_p(1, y0, 0), _p(1, y0, 1), _p(1, y1, 1), _p(1, y1, 0)],
            [_p(0, y0, 0), _p(1, y0, 0), _p(1, y1, 0), _p(0, y1, 0)],
            [_p(0, y0, 1), _p(0, y1, 1), _p(1, y1, 1), _p(1, y0, 1)],
        ):
            faces.append(quad)
            owner.append(j)
    mesh = case / "constant" / "polyMesh"
    pts = _points()
    _write(
        mesh / "points",
        "vectorField",
        f"{len(pts)}\n(\n" + "\n".join(f"({x} {y} {z})" for x, y, z in pts) + "\n)\n",
    )
    _write(
        mesh / "faces",
        "faceList",
        f"{len(faces)}\n(\n" + "\n".join(f"4({' '.join(map(str, f))})" for f in faces) + "\n)\n",
    )
    _write(mesh / "owner", "labelList", f"{len(owner)}\n(\n" + "\n".join(map(str, owner)) + "\n)\n")
    _write(
        mesh / "neighbour",
        "labelList",
        f"{len(neighbour)}\n(\n" + "\n".join(map(str, neighbour)) + "\n)\n",
    )
    first = NY - 1
    _write(
        mesh / "boundary",
        "polyBoundaryMesh",
        "3\n(\n"
        f"lowerWall {{ type wall; nFaces 1; startFace {first}; }}\n"
        f"upperWall {{ type wall; nFaces 1; startFace {first + 1}; }}\n"
        f"sides {{ type empty; nFaces {4 * NY}; startFace {first + 2}; }}\n)\n",
    )
    centres = [(j + 0.5) * H for j in range(NY)]
    for name, scale in (("U", 1.0), ("Ua", profile)):
        values = "\n".join(f"({scale * y * (1 - y)} 0 0)" for y in centres)
        _write(
            case / "1" / name,
            "volVectorField",
            "dimensions [0 1 -1 0 0 0 0];\n"
            f"internalField nonuniform List<vector> {NY}\n(\n{values}\n)\n;\n"
            "boundaryField\n{\n lowerWall { type noSlip; }\n upperWall { type noSlip; }\n"
            " sides { type empty; }\n}\n",
        )


def test_the_sensitivity_of_a_channel_is_exact(tmp_path: Path) -> None:
    channel(tmp_path, profile=-2.0)
    nu = 1e-3
    found = wall_sensitivity(tmp_path, "1", nu)
    assert found.patches == ("lowerWall", "upperWall")
    assert found.normals == pytest.approx(np.array([[0, -1, 0], [0, 1, 0]]))
    assert found.areas == pytest.approx([1.0, 1.0])
    y = H / 2
    u_p, d = y * (1 - y), H / 2
    # dU/dn . dUa/dn = (u_P / d)(-2 u_P / d): s = nu * that, negative here.
    assert found.values == pytest.approx([-2 * nu * (u_p / d) ** 2] * 2)


def test_the_reader_checks_what_it_reads(tmp_path: Path) -> None:
    channel(tmp_path)
    mesh = read_mesh(tmp_path)
    assert (mesh.cells, len(mesh.faces), len(mesh.neighbour)) == (NY, 3 + 2 + 4 * NY, 3)
    field = read_field(tmp_path, "1", "U", mesh)
    assert field.width == 3 and field.internal.shape == (NY, 3)
    assert field.patches["lowerWall"] is None and field.types["lowerWall"] == "noSlip"
    broken = (
        (tmp_path / "1" / "U").read_text().replace(f"List<vector> {NY}", f"List<vector> {NY + 1}")
    )
    (tmp_path / "1" / "U").write_text(broken)
    with pytest.raises(FoamReadError, match="cell values"):
        read_field(tmp_path, "1", "U", mesh)
    owner = tmp_path / "constant" / "polyMesh" / "owner"
    owner.write_text(owner.read_text().replace("format ascii", "format binary"))
    with pytest.raises(FoamReadError, match="binary"):
        read_mesh(tmp_path)
    with pytest.raises(FoamReadError, match="not in the case"):
        read_field(tmp_path, "2", "U", mesh)


def test_port_flows_read_phi_and_fall_back_to_u(tmp_path: Path) -> None:
    """ADR-0040: each port's flow, outward positive, from the solver's phi when it is written,
    else from U on the faces (the owner cells' where the patch stores none)."""
    from stokes_agent.sensitivity import port_flows

    channel(tmp_path)
    u = (tmp_path / "1" / "U").read_text()
    (tmp_path / "1" / "U").write_text(
        u.replace(
            "lowerWall { type noSlip; }", "lowerWall { type fixedValue; value uniform (0 -2 0); }"
        )
    )
    found = port_flows(tmp_path, "1", ["lowerWall", "upperWall"])
    # U . S_f with S_f = (0, -1, 0) m²: 2 m³/s out; the upper wall's owner cell flows along x only.
    assert found["lowerWall"].flow == pytest.approx(2.0)
    assert found["upperWall"].flow == pytest.approx(0.0)
    assert found["lowerWall"].pressure is None  # no p written at this time
    sides = " ".join(["0.5"] * (NY * 4 - 1) + ["-0.5"])
    _write(
        tmp_path / "1" / "phi",
        "surfaceScalarField",
        "dimensions [0 3 -1 0 0 0 0];\n"
        "internalField nonuniform List<scalar> 3(0 0 0);\n"
        "boundaryField\n{\n"
        " lowerWall { type calculated; value uniform 0.25; }\n"
        " upperWall { type calculated; value nonuniform List<scalar> 1(-0.75); }\n"
        f" sides {{ type empty; value nonuniform List<scalar> {NY * 4}({sides}); }}\n}}\n",
    )
    found = port_flows(tmp_path, "1", ["lowerWall", "upperWall", "sides"])
    assert found["lowerWall"].flow == pytest.approx(0.25)  # phi wins over U
    assert found["upperWall"].flow == pytest.approx(-0.75) and found["upperWall"].backflow == 0
    assert found["sides"].flow == pytest.approx(0.5 * (NY * 4 - 2))
    # One face of sixteen enters where the net flow leaves: 0.5 of 8 of |flux|.
    assert found["sides"].backflow == pytest.approx(0.5 / (0.5 * NY * 4))
    _write(
        tmp_path / "1" / "p",
        "volScalarField",
        "dimensions [0 2 -2 0 0 0 0];\n"
        "internalField uniform 3;\n"
        "boundaryField\n{\n lowerWall { type fixedValue; value uniform 1; }\n"
        " upperWall { type zeroGradient; }\n sides { type empty; }\n}\n",
    )
    found = port_flows(tmp_path, "1", ["lowerWall", "upperWall"])
    assert found["lowerWall"].pressure == pytest.approx(1.0)
    assert found["upperWall"].pressure == pytest.approx(3.0)  # the owner cell's


# ---------------------------------------------- ADR-0041 decision 8: the solver's own s comes first
def _solver_field(case: Path, lower: str, upper: str, time: str = "1") -> None:
    """A ``shapeSensitivity`` as adjointFoam writes it: zero inside, values on the walls."""
    _write(
        case / time / "shapeSensitivity",
        "volScalarField",
        "dimensions [0 2 -3 0 0 0 0];\ninternalField uniform 0;\n"
        f"boundaryField\n{{\n lowerWall {{ type calculated; value {lower}; }}\n"
        f" upperWall {{ type calculated; value {upper}; }}\n sides {{ type empty; }}\n}}\n",
    )


def test_the_solver_written_sensitivity_is_taken_when_the_case_has_it(tmp_path: Path) -> None:
    channel(tmp_path, profile=-2.0)
    nu = 1e-3
    rebuilt = wall_sensitivity(tmp_path, "1", nu)
    assert rebuilt.source == "reconstructed"
    _solver_field(tmp_path, "nonuniform List<scalar> 1(-7.5)", "uniform 3.25")
    found = wall_sensitivity(tmp_path, "1", nu)
    assert found.source == "solver" and found.to_json()["source"] == "solver"
    # The same layout as the reconstruction: patches, sizes, geometry; only s differs.
    assert found.patches == rebuilt.patches and found.sizes == rebuilt.sizes
    assert found.centres == pytest.approx(rebuilt.centres)
    assert found.normals == pytest.approx(rebuilt.normals)
    assert found.areas == pytest.approx(rebuilt.areas)
    assert found.values == pytest.approx([-7.5, 3.25])
    # A chosen subset and order of patches is honoured.
    upper = wall_sensitivity(tmp_path, "1", nu, ["upperWall"])
    assert upper.patches == ("upperWall",) and upper.values == pytest.approx([3.25])
    # The reconstruction can still be asked for, and is unchanged.
    forced = wall_sensitivity(tmp_path, "1", nu, source="reconstructed")
    assert forced.source == "reconstructed"
    assert forced.values == pytest.approx(rebuilt.values)


def test_the_reconstruction_is_the_fallback(tmp_path: Path) -> None:
    channel(tmp_path, profile=-2.0)
    nu = 1e-3
    with pytest.raises(FoamReadError, match="shapeSensitivity"):
        wall_sensitivity(tmp_path, "1", nu, source="solver")
    # A wall that stores no face value: the whole answer is reconstructed, never mixed.
    _write(
        tmp_path / "1" / "shapeSensitivity",
        "volScalarField",
        "dimensions [0 2 -3 0 0 0 0];\ninternalField uniform 0;\n"
        "boundaryField\n{\n lowerWall { type calculated; value uniform 1; }\n"
        " upperWall { type zeroGradient; }\n sides { type empty; }\n}\n",
    )
    found = wall_sensitivity(tmp_path, "1", nu)
    assert found.source == "reconstructed"
    y = H / 2
    assert found.values == pytest.approx([-2 * nu * (y * (1 - y) / (H / 2)) ** 2] * 2)
    # Another time's field is not this time's.
    (tmp_path / "1" / "shapeSensitivity").unlink()
    _solver_field(tmp_path, "uniform 1", "uniform 1", time="2")
    assert wall_sensitivity(tmp_path, "1", nu).source == "reconstructed"


def test_a_broken_solver_field_is_reported_not_replaced(tmp_path: Path) -> None:
    channel(tmp_path)
    _solver_field(tmp_path, "nonuniform List<scalar> 1(nan)", "uniform 0")
    with pytest.raises(FoamReadError, match="not finite"):
        wall_sensitivity(tmp_path, "1", 1e-3)
    _solver_field(tmp_path, "nonuniform List<scalar> 2(1 2)", "uniform 0")
    with pytest.raises(FoamReadError, match="face values"):
        wall_sensitivity(tmp_path, "1", 1e-3)
    with pytest.raises(ValueError, match="unknown"):
        wall_sensitivity(tmp_path, "1", 1e-3, source="guess")


def test_job_sensitivity_reports_its_source(tmp_path: Path) -> None:
    import asyncio
    from types import SimpleNamespace
    from typing import cast

    from stokes_agent.jobs import JobStore
    from stokes_agent.server import AgentServer

    channel(tmp_path, profile=-2.0)
    _write(
        tmp_path / "1" / "p",
        "volScalarField",
        "dimensions [0 2 -2 0 0 0 0];\ninternalField uniform 0;\n"
        "boundaryField\n{\n lowerWall { type zeroGradient; }\n upperWall { type zeroGradient; }\n"
        " sides { type empty; }\n}\n",
    )
    store = cast(JobStore, SimpleNamespace(job_dir=lambda job: tmp_path))
    server = AgentServer(jobs=store)

    def ask(**kwargs: object) -> dict[str, object]:
        found: dict[str, object] = asyncio.run(server._job_sensitivity("j1", 1e-3, **kwargs))  # type: ignore[arg-type]
        return found

    rebuilt = ask()
    assert rebuilt["source"] == "reconstructed" and rebuilt["patches"] == ["lowerWall", "upperWall"]
    _solver_field(tmp_path, "uniform -4", "uniform 5")
    solved = ask()
    assert solved["source"] == "solver" and solved["values"] == [-4.0, 5.0]
    assert solved["centres"] == rebuilt["centres"] and solved["areas"] == rebuilt["areas"]
    assert ask(adjoint=False)["source"] is None

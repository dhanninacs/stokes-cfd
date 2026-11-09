"""The agent's flatten action: a symmetry plane snapped onto its plane without breaking a cell.

The meshes here are small structured hex blocks written as real ASCII polyMeshes, with a
`symmetry` patch on the z = 0 face, so the module runs on the files OpenFOAM writes and nothing
needs an install. The elbow it was made for (276,113 tets, 78 points off the plane by up to
1.2 mm) is measured in `docs/engineering/performance.md`'s neighbour, `stokes/mesh/flatness.py`.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from stokes_agent import flatten
from stokes_agent.flatten import FlattenSkipped, flatten_patch

HEADER = (
    "FoamFile\n{\n    version 2.0;\n    format ascii;\n    class dummy;\n    object dummy;\n}\n\n"
)


def write_list(path: Path, items: list[str]) -> None:
    path.write_text(HEADER + f"\n{len(items)}\n(\n" + "\n".join(items) + "\n)\n\n// end //\n")


def block(
    root: Path,
    n: tuple[int, int, int] = (9, 9, 2),
    size: float = 1.0,
    lift: dict[tuple[int, int, int], float] | None = None,
    layers: tuple[float, ...] | None = None,
    pad_neighbour: bool = False,
    patch_type: str = "symmetry",
) -> np.ndarray:
    """A hex block with a patch `symmetry` on its z = 0 face and `walls` on the rest.

    ``lift`` moves grid points in z (a point below the plane has a negative lift); ``layers`` sets
    each cell layer's thickness. Returns the points.
    """
    nx, ny, nz = n
    heights = layers if layers is not None else (size,) * nz
    zs = np.concatenate([[0.0], np.cumsum(heights)])
    grid = np.array(
        [
            (i * size, j * size, zs[k])
            for k in range(nz + 1)
            for j in range(ny + 1)
            for i in range(nx + 1)
        ]
    )
    for (i, j, k), dz in (lift or {}).items():
        grid[k * (ny + 1) * (nx + 1) + j * (nx + 1) + i, 2] += dz

    def point(i: int, j: int, k: int) -> int:
        return k * (ny + 1) * (nx + 1) + j * (nx + 1) + i

    def cell(i: int, j: int, k: int) -> int:
        return k * ny * nx + j * nx + i

    internal: list[tuple[tuple[int, ...], int, int]] = []
    bottom: list[tuple[tuple[int, ...], int]] = []
    other: list[tuple[tuple[int, ...], int]] = []
    for k in range(nz):
        for j in range(ny):
            for i in range(nx):
                c = cell(i, j, k)
                quad_lo = (
                    point(i, j, k),
                    point(i, j + 1, k),
                    point(i + 1, j + 1, k),
                    point(i + 1, j, k),
                )
                quad_hi = (
                    point(i, j, k + 1),
                    point(i + 1, j, k + 1),
                    point(i + 1, j + 1, k + 1),
                    point(i, j + 1, k + 1),
                )
                if k == 0:
                    bottom.append((quad_lo, c))
                if k == nz - 1:
                    other.append((quad_hi, c))
                else:
                    internal.append((quad_hi, c, cell(i, j, k + 1)))
                x_lo = (
                    point(i, j, k),
                    point(i, j, k + 1),
                    point(i, j + 1, k + 1),
                    point(i, j + 1, k),
                )
                x_hi = (
                    point(i + 1, j, k),
                    point(i + 1, j + 1, k),
                    point(i + 1, j + 1, k + 1),
                    point(i + 1, j, k + 1),
                )
                y_lo = (
                    point(i, j, k),
                    point(i + 1, j, k),
                    point(i + 1, j, k + 1),
                    point(i, j, k + 1),
                )
                y_hi = (
                    point(i, j + 1, k),
                    point(i, j + 1, k + 1),
                    point(i + 1, j + 1, k + 1),
                    point(i + 1, j + 1, k),
                )
                if i == 0:
                    other.append((x_lo, c))
                if i == nx - 1:
                    other.append((x_hi, c))
                else:
                    internal.append((x_hi, c, cell(i + 1, j, k)))
                if j == 0:
                    other.append((y_lo, c))
                if j == ny - 1:
                    other.append((y_hi, c))
                else:
                    internal.append((y_hi, c, cell(i, j + 1, k)))
    faces = [f for f, _, _ in internal] + [f for f, _ in bottom] + [f for f, _ in other]
    owner = [o for _, o, _ in internal] + [c for _, c in bottom] + [c for _, c in other]
    neighbour = [str(n_) for _, _, n_ in internal]
    if pad_neighbour:  # cfMesh writes the list at every face's length, with -1 on the boundary
        neighbour += ["-1"] * (len(faces) - len(neighbour))
    poly = root / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    write_list(poly / "points", [f"({x:.6g} {y:.6g} {z:.6g})" for x, y, z in grid])
    write_list(poly / "faces", [f"4({' '.join(map(str, f))})" for f in faces])
    write_list(poly / "owner", [str(o) for o in owner])
    write_list(poly / "neighbour", neighbour)
    boundary = (
        f"{HEADER}2\n(\n"
        f"symmetry\n{{\n    type {patch_type};\n    nFaces {len(bottom)};\n"
        f"    startFace {len(internal)};\n}}\n"
        f"walls\n{{\n    type wall;\n    nFaces {len(other)};\n"
        f"    startFace {len(internal) + len(bottom)};\n}}\n)\n"
    )
    (poly / "boundary").write_text(boundary)
    return grid


def read_points(root: Path) -> np.ndarray:
    lines = (root / "constant" / "polyMesh" / "points").read_text().split("\n")
    start = lines.index("(") + 1
    return np.array(
        [[float(v) for v in ln.strip("()").split()] for ln in lines[start:] if ln.startswith("(")]
    )


def bottom_id(i: int, j: int, n: tuple[int, int, int] = (3, 3, 2)) -> int:
    return j * (n[0] + 1) + i


def test_points_below_the_plane_are_put_back_on_it_and_nothing_else_moves(tmp_path: Path) -> None:
    before = block(tmp_path, lift={(1, 1, 0): -0.03, (2, 1, 0): -0.02, (0, 2, 0): 0.01})
    original = (tmp_path / "constant" / "polyMesh" / "points").read_text().split("\n")
    report = flatten_patch(tmp_path, "symmetry")
    after = read_points(tmp_path)
    plane = after[: 10 * 10]
    assert np.allclose(plane[:, 2], 0.0, atol=1e-9), "the whole patch is on z = 0"
    assert report.off_before == 3 and report.off_after == 0 and report.limited == 0
    assert report.moved == 3 and report.max_before == pytest.approx(0.03, abs=1e-6)
    assert np.allclose(after[100:], before[100:]), "points above the patch are where they were"
    # Only the lines that changed were rewritten: every other line is byte for byte the original.
    now = (tmp_path / "constant" / "polyMesh" / "points").read_text().split("\n")
    changed = [i for i, (a, b) in enumerate(zip(original, now, strict=True)) if a != b]
    assert len(changed) == 3
    assert "flatten" in report.text() and "3 off the plane" in report.text()


def test_a_neighbour_list_padded_with_minus_one_reads_the_same(tmp_path: Path) -> None:
    block(tmp_path, lift={(1, 1, 0): -0.03}, pad_neighbour=True)
    report = flatten_patch(tmp_path, "symmetry")
    assert report.off_before == 1 and report.off_after == 0


def test_a_move_that_would_invert_a_cell_is_cut_short_and_every_cell_stays_valid(
    tmp_path: Path,
) -> None:
    """A sliver layer whose far side is lower than the plane: putting its corner on the plane
    would carry it through the other side. The point goes as far as the cells allow, not past."""
    n = (3, 3, 2)
    # Layer 0 is 0.05 thick; its corner at grid (1, 1) sits 0.5 below the plane, and the layer's
    # top face there is lowered with it, so z = 0 is above the whole cell.
    lift = {(1, 1, 0): -0.5, (1, 1, 1): -0.46}
    block(tmp_path, n=n, layers=(0.05, 1.0), lift=lift)
    report = flatten_patch(tmp_path, "symmetry")
    after = read_points(tmp_path)
    moved = after[bottom_id(1, 1)]
    assert -0.5 < moved[2] < 0.0, f"part of the way: z = {moved[2]}"
    assert report.limited >= 1 and report.off_after >= 1 and report.moved >= 1
    # Every cell is still valid: the volume and pyramids the module itself judges by are positive.
    mesh = flatten._load(tmp_path / "constant" / "polyMesh")
    cells = flatten._Cells(mesh, np.ones(len(mesh.points), bool))
    volume, pyramid = cells.health(mesh.points)
    assert (volume > 0).all() and (pyramid > 0).all()


def test_a_plane_that_is_already_flat_is_not_touched(tmp_path: Path) -> None:
    block(tmp_path)
    original = (tmp_path / "constant" / "polyMesh" / "points").read_text()
    report = flatten_patch(tmp_path, "symmetry")
    assert (report.off_before, report.moved) == (0, 0)
    assert (tmp_path / "constant" / "polyMesh" / "points").read_text() == original


@pytest.mark.parametrize(
    ("patch", "match"),
    [("nowhere", "no patch"), ("walls", "not a symmetry plane")],
)
def test_a_patch_that_is_missing_or_not_a_symmetry_plane_is_left_alone(
    tmp_path: Path, patch: str, match: str
) -> None:
    block(tmp_path, lift={(1, 1, 0): -0.03})
    with pytest.raises(FlattenSkipped, match=match):
        flatten_patch(tmp_path, patch)


def test_a_symmetry_plane_type_is_flattened_too(tmp_path: Path) -> None:
    block(tmp_path, lift={(1, 1, 0): -0.03}, patch_type="symmetryPlane")
    assert flatten_patch(tmp_path, "symmetry").off_after == 0


def test_a_curved_symmetry_surface_is_not_flattened(tmp_path: Path) -> None:
    """A symmetry surface that is not a plane has no plane to be off: its points stay."""
    n = (6, 6, 2)
    lift = {(i, j, 0): 0.4 * np.sin(i + j) for i in range(7) for j in range(7)}
    block(tmp_path, n=n, lift=lift)
    before = (tmp_path / "constant" / "polyMesh" / "points").read_text()
    with pytest.raises(FlattenSkipped, match="not a plane"):
        flatten_patch(tmp_path, "symmetry")
    assert (tmp_path / "constant" / "polyMesh" / "points").read_text() == before


def test_a_binary_mesh_or_a_missing_file_is_left_alone(tmp_path: Path) -> None:
    block(tmp_path, lift={(1, 1, 0): -0.03})
    points = tmp_path / "constant" / "polyMesh" / "points"
    points.write_text(points.read_text().replace("format ascii;", "format binary;"))
    with pytest.raises(FlattenSkipped, match="not ASCII"):
        flatten_patch(tmp_path, "symmetry")
    points.unlink()
    with pytest.raises(OSError):  # no mesh at all is an error the action reports, not a skip
        flatten_patch(tmp_path, "symmetry")


def test_a_mesh_over_the_face_limit_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    block(tmp_path, lift={(1, 1, 0): -0.03})
    monkeypatch.setattr(flatten, "MAX_FACES", 10)
    with pytest.raises(FlattenSkipped, match="faces is over"):
        flatten_patch(tmp_path, "symmetry")


# ---- the action


def make_job(tmp_path: Path) -> tuple[object, Path]:
    from stokes_agent.jobs import JobStore

    root = tmp_path / "jobs"
    store = JobStore(root)
    job_dir = tmp_path / "case"
    block(job_dir, lift={(1, 1, 0): -0.03})
    return store, job_dir


def test_the_action_flattens_logs_and_reports_done(tmp_path: Path) -> None:
    from stokes_agent.jobs import Step

    store, job_dir = make_job(tmp_path)
    step = Step("flattenSymmetry", (), action={"op": "flatten", "patch": "symmetry"})
    outcome = store._run_action(job_dir, step, None)  # type: ignore[attr-defined]
    assert outcome == ("done", None)
    assert np.allclose(read_points(job_dir)[: 10 * 10, 2], 0.0, atol=1e-9)
    log = (job_dir / "log.flattenSymmetry").read_text()
    assert "flatten symmetry" in log and "1 off the plane" in log


@pytest.mark.parametrize("patch", ["nowhere", "walls"])
def test_the_action_never_fails_the_job_and_says_why(tmp_path: Path, patch: str) -> None:
    from stokes_agent.jobs import Step

    store, job_dir = make_job(tmp_path)
    step = Step("flattenSymmetry", (), action={"op": "flatten", "patch": patch})
    assert store._run_action(job_dir, step, None) == ("done", None)  # type: ignore[attr-defined]
    assert "left as it is" in (job_dir / "log.flattenSymmetry").read_text()


def test_the_action_with_no_mesh_at_all_still_ends_the_step_cleanly(tmp_path: Path) -> None:
    from stokes_agent.jobs import JobStore, Step

    store = JobStore(tmp_path / "jobs")
    job_dir = tmp_path / "empty"
    job_dir.mkdir()
    step = Step("flattenSymmetry", (), action={"op": "flatten", "patch": "symmetry"})
    assert store._run_action(job_dir, step, None) == ("done", None)
    assert "left as it is" in (job_dir / "log.flattenSymmetry").read_text()

"""`run.probe` on the slice and along a sampled line (R5, plot over line).

**Synthetic, no OpenFOAM**: the slice is a hand-made 1 x 1 sheet with a linear field, so the
right answer is known without the code (`f = 2x + 3y`). The join to a real run's slice cache is
`_slice_cut`, which `run.streamlines` already exercises; it is replaced here by a fixed sheet.
"""

from __future__ import annotations

import numpy as np
import pytest

from stokes.api.commands import run as run_commands
from stokes.post.probe import ProbeInvalid


def _sheet(n: int = 6) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """An `n x n` sheet in z = 0.5 (0..1 in x and y) with its triangles and `f = 2x + 3y`."""
    xs, ys = np.meshgrid(np.linspace(0, 1, n), np.linspace(0, 1, n))
    positions = np.column_stack([xs.ravel(), ys.ravel(), np.full(xs.size, 0.5)])
    tris = []
    for j in range(n - 1):
        for i in range(n - 1):
            a = j * n + i
            tris += [[a, a + 1, a + n], [a + 1, a + n + 1, a + n]]
    values = 2.0 * positions[:, 0] + 3.0 * positions[:, 1]
    return positions, np.asarray(tris, dtype=np.uint32), values


@pytest.fixture
def slice_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    sheet = _sheet()
    monkeypatch.setattr(run_commands, "_slice_cut", lambda _ctx, _run, _field: sheet)


def _probe(**kw: object) -> dict[str, object]:
    return run_commands.run_probe(None, patch="slice", field="p", **kw)  # type: ignore[arg-type]


def test_the_slice_is_a_patch_a_probe_can_name(slice_cut: None) -> None:
    out = _probe(point=[0.25, 0.5, 0.5])
    assert out["patch"] == "slice"
    assert out["values"] == [pytest.approx(2.0 * 0.25 + 3.0 * 0.5, abs=1e-9)]
    assert "points" not in out and "along" not in out


def test_n_samples_evenly_along_the_segment_with_both_ends(slice_cut: None) -> None:
    out = _probe(polyline=[[0.1, 0.1, 0.5], [0.9, 0.5, 0.5]], n=5)
    points = np.asarray(out["points"])
    assert points.shape == (5, 3)
    assert points[0] == pytest.approx([0.1, 0.1, 0.5])
    assert points[-1] == pytest.approx([0.9, 0.5, 0.5])
    assert points[2] == pytest.approx([0.5, 0.3, 0.5])
    length = float(np.hypot(0.8, 0.4))
    along = out["along"]
    assert along[0] == 0.0 and along[-1] == pytest.approx(length)
    assert np.diff(along) == pytest.approx(length / 4)
    expected = 2.0 * points[:, 0] + 3.0 * points[:, 1]
    assert out["values"] == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize("n", [0, 1, -3])
def test_an_n_below_two_is_ignored(slice_cut: None, n: int) -> None:
    line = [[0.1, 0.1, 0.5], [0.9, 0.5, 0.5]]
    out = _probe(polyline=line, n=n)
    assert len(out["values"]) == 2
    assert "points" not in out and "along" not in out


def test_n_only_applies_to_a_two_point_line(slice_cut: None) -> None:
    out = _probe(polyline=[[0.1, 0.1, 0.5], [0.5, 0.5, 0.5], [0.9, 0.9, 0.5]], n=50)
    assert len(out["values"]) == 3


def test_n_is_capped(slice_cut: None) -> None:
    out = _probe(polyline=[[0.1, 0.1, 0.5], [0.9, 0.5, 0.5]], n=10**6)
    assert len(out["values"]) == run_commands.MAX_LINE_SAMPLES


def test_a_line_leaving_the_slice_is_refused(slice_cut: None) -> None:
    with pytest.raises(ProbeInvalid, match="outside"):
        _probe(polyline=[[0.1, 0.1, 0.5], [5.0, 5.0, 0.5]], n=20)


def test_a_hole_in_the_slice_is_outside(monkeypatch: pytest.MonkeyPatch) -> None:
    """The slice's own triangles are used, so a gap in the cut is outside (a Delaunay of the
    points would have filled it and answered a number)."""
    positions, tris, values = _sheet(6)
    centre = positions[tris].mean(axis=1)
    keep = ~((np.abs(centre[:, 0] - 0.5) < 0.15) & (np.abs(centre[:, 1] - 0.5) < 0.15))
    holed = (positions, tris[keep], values)
    monkeypatch.setattr(run_commands, "_slice_cut", lambda _c, _r, _f: holed)
    with pytest.raises(ProbeInvalid, match="outside"):
        _probe(point=[0.5, 0.5, 0.5])
    assert _probe(point=[0.1, 0.1, 0.5])["values"]


def test_a_slice_that_missed_the_model_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    positions, _tris, values = _sheet(6)
    empty = (positions, np.zeros((0, 3), dtype=np.uint32), values)
    monkeypatch.setattr(run_commands, "_slice_cut", lambda _c, _r, _f: empty)
    with pytest.raises(ProbeInvalid, match="misses"):
        _probe(point=[0.5, 0.5, 0.5])


def test_a_vector_field_on_the_slice_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    positions, tris, values = _sheet(6)
    vector = (positions, tris, np.column_stack([values, values, values]))
    monkeypatch.setattr(run_commands, "_slice_cut", lambda _c, _r, _f: vector)
    with pytest.raises(ProbeInvalid, match="components"):
        _probe(point=[0.5, 0.5, 0.5])


def test_a_probe_with_neither_point_nor_line_is_refused(slice_cut: None) -> None:
    with pytest.raises(ProbeInvalid):
        _probe()

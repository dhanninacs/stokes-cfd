"""STEP import budget, tessellation share: a synthetic part with about 20 k faces.

The import budget (docs/engineering/performance.md: 20 k faces in under 20 s, first parts in
under 2 s) covers reading, a coarse pass and a fine pass. This test times the two passes the
way the worker runs them (``tessellate_bodies`` over its batches), in this process, on 2 250
pin fins: a box and a cylinder each, 4 500 solids (4 500 parts once imported), 20 250 faces.

Measured on the development laptop, 2026-10-03, eleven runs at machine factors 1.3 to 3.7 while
other sessions shared it: coarse 10.8 to 17.7 s (252 k triangles), fine 12.8 to 26.7 s (585 k
triangles). BRepMesh costs about 0.5 ms per face there whatever the deflection, so the coarse
pass is barely cheaper than the fine one. The machine factor comes from a single-threaded Python
loop and follows multi-threaded OCCT only loosely, hence targets of 10 s and 12 s: they guard
against regressions and passed every run above. They are not the import budget, which this
part misses: reading it from STEP through OCCT took a further 16 s to parse and 95 s to
transfer on the same laptop.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("OCP")

from stokes.geometry import step
from stokes.geometry.worker_main import _batches
from tests import geometry_fixtures as fx
from tests.perf.harness import budget


def solids_of(shape: object) -> list[step.StepSolid]:
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp_Explorer

    solids = []
    explorer = TopExp_Explorer(shape, TopAbs_ShapeEnum.TopAbs_SOLID)
    while explorer.More():
        body = explorer.Current()
        solids.append(step.StepSolid(f"pin_{len(solids)}", None, body, step.face_count(body)))
        explorer.Next()
    return solids


def run_pass(solids: list[step.StepSolid], quality: step.Quality) -> int:
    triangles = 0
    for batch in _batches(list(enumerate(solids))):
        bodies = [step.Body(s.shape, *step.deflections(s.shape, quality), s.name) for _, s in batch]
        meshes = step.tessellate_bodies(bodies, quality=quality.name)
        triangles += sum(m.triangle_count for m in meshes)
    return triangles


@pytest.mark.perf
@pytest.mark.skipif((os.cpu_count() or 1) < 4, reason="helpers need at least four CPUs")
def test_helpers_tessellate_20k_faces_in_both_passes(tmp_path: Path) -> None:
    """Both passes over the same 4,500 solids through helper processes (``geometry/helpers.py``).

    The in-process passes above cost about 22 s together; four to eight helpers do both in about
    6.4 s on this host (24 CPUs, 2026-11-09), including writing each batch for them. The target
    of 12 s is half the serial figure: it fails if the helpers stop running in parallel (a request
    that cannot be sent, a lock that serialises them) without being so tight that load trips it.
    """
    from stokes.geometry import helpers
    from stokes.geometry.worker_main import _spec_of, _write_batch

    solids = solids_of(fx.pin_grid(50, 45))
    batches = list(_batches(list(enumerate(solids)), helpers.BATCH_FACES))
    pool = helpers.HelperPool.start(min(helpers.MAX_HELPERS, max(1, (os.cpu_count() or 4) - 2)))
    try:
        assert pool.size >= 2
        pool.take(
            pool.submit({"path": "", "bodies": ""}), 120
        )  # a request that fails: helper is up
        with budget("geometry.helpers_tessellate_20k_faces_both_passes", target_ms=12000.0):
            ids = []
            for position, quality in enumerate((step.COARSE, step.FINE)):
                for number, batch in enumerate(batches):
                    target = tmp_path / f"b{number}.bin"
                    described = tmp_path / f"b{number}-{position}.json"
                    if position == 0:
                        _write_batch(target, [s.shape for _, s in batch])
                    described.write_text(
                        json.dumps([_spec_of(i, s, quality) for i, s in batch]), encoding="utf-8"
                    )
                    ids.append(
                        pool.submit(
                            {
                                "path": str(target),
                                "bodies": str(described),
                                "quality": quality.name,
                                "unit": 1e-3,
                                "count": len(solids),
                                "final": position == 1,
                            }
                        )
                    )
            outcomes = [pool.take(i, 300) for i in ids]
    finally:
        pool.close()
    assert all(o.ok for o in outcomes), {o.message for o in outcomes if not o.ok}
    assert sum(len(o.frames) for o in outcomes) == 2 * len(solids) * helpers.FRAMES_PER_SOLID


@pytest.mark.perf
def test_tessellate_20k_faces() -> None:
    solids = solids_of(fx.pin_grid(50, 45))
    assert sum(s.faces for s in solids) == 20_250
    with budget("geometry.tessellate_20k_faces_coarse", target_ms=10000.0):
        coarse = run_pass(solids, step.COARSE)
    with budget("geometry.tessellate_20k_faces_fine", target_ms=12000.0):
        fine = run_pass(solids, step.FINE)
    assert fine > coarse > 20_250

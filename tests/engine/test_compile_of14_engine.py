"""The case writer's output, run by real OpenFOAM (T-212).

Marked `engine` and `slow`: it meshes and solves, so it takes about a minute.

    STOKES_OPENFOAM_BASHRC=<the env script> STOKES_ENGINE_TESTS=1 PYTHONPATH=.:agent \\
        python3 -m pytest -q tests/engine/test_compile_of14_engine.py

**What only this test can check.** The unit tests prove the writer's text is what we intend;
this proves
OpenFOAM *accepts* it and answers physics. Two of the defects found while building the writer were
invisible to the unit tests and fatal to the solver — `dimensions` in parentheses instead of
brackets
(`FOAM FATAL IO ERROR: expected a [ in dimensionSet`), and the printer's key regex refusing
`div(phi,U)`. Both are now impossible to miss, because the case does not run.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from stokes.compile.foam import Dict, Entry, List_, Raw, Tuple_, Word, render_file
from stokes.compile.of14 import write

sys.path.insert(0, str(Path(__file__).parent.parent))
from test_compile_of14 import duct

pytestmark = [pytest.mark.engine, pytest.mark.slow]

#: The duct's box, in metres: 0.5 long, 0.02 square. 250 x 10 x 10 cells.
LENGTH, SIDE = 0.5, 0.02


def duct_block_mesh_dict() -> str:
    """The duct's `blockMeshDict`, **written with the printer** rather than hand-typed text.

    That matters: a hand-written dict would be a second, unverified way of producing a case, and
    if it
    disagreed with the writer the test would be measuring the wrong thing. This is the same
    printer the
    writer uses, so the mesh and the fields come from one place.
    """
    vertices = [
        (0, 0, 0),
        (LENGTH, 0, 0),
        (LENGTH, SIDE, 0),
        (0, SIDE, 0),
        (0, 0, SIDE),
        (LENGTH, 0, SIDE),
        (LENGTH, SIDE, SIDE),
        (0, SIDE, SIDE),
    ]
    body = Dict(
        (
            Entry("scale", 1.0),
            Entry("vertices", List_(tuple(Tuple_(v) for v in vertices))),
            Entry(
                "blocks",
                List_((Raw("hex (0 1 2 3 4 5 6 7) (250 10 10) simpleGrading (1 1 1)"),)),
            ),
            Entry(
                "boundary",
                List_(
                    (
                        Entry(
                            "inlet",
                            Dict(
                                (
                                    Entry("type", Word("patch")),
                                    Entry("faces", List_((Tuple_((0, 4, 7, 3)),))),
                                )
                            ),
                        ),
                        Entry(
                            "outlet",
                            Dict(
                                (
                                    Entry("type", Word("patch")),
                                    Entry("faces", List_((Tuple_((1, 5, 6, 2)),))),
                                )
                            ),
                        ),
                        Entry(
                            "walls",
                            Dict(
                                (
                                    Entry("type", Word("wall")),
                                    Entry(
                                        "faces",
                                        List_(
                                            (
                                                Tuple_((0, 3, 2, 1)),
                                                Tuple_((4, 5, 6, 7)),
                                                Tuple_((0, 1, 5, 4)),
                                                Tuple_((3, 7, 6, 2)),
                                            )
                                        ),
                                    ),
                                )
                            ),
                        ),
                    )
                ),
            ),
        )
    )
    return render_file("blockMeshDict", body, location="system")


def test_the_written_duct_meshes_and_solves(tmp_path: Path) -> None:
    """**The whole chain, verified against physics.**

    The case is written, linted by the agent's own dictionary lint (so the two cannot disagree
    about what
    is safe), meshed, and solved to convergence. And then the numbers are checked: the inlet
    monitor must
    report `0.075 m/s x 0.02 x 0.02 m = 3.0e-05 m3/s`, because a case that converges to the
    wrong answer is
    worse than one that fails.
    """
    from stokes_agent.engine_env import get_env
    from stokes_agent.lint import lint_text

    case = tmp_path / "duct"
    tree = write(duct())
    tree["system/blockMeshDict"] = duct_block_mesh_dict()

    # **The agent's lint first.** A file the writer is happy with and the lint refuses would only be
    # discovered when a job runs, so the two are checked against each other here.
    for rel, text in tree.items():
        found = lint_text(rel, text)
        assert not found, f"the agent's lint refused {rel}: {found[0].to_json()}"

    for rel, text in tree.items():
        target = case / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    env = get_env(None).env

    def run(argv: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            argv,
            cwd=case,
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    mesh = run(["blockMesh"], 300)
    assert mesh.returncode == 0, mesh.stdout[-3000:]
    assert "nCells: 25000" in mesh.stdout, "the duct mesh is not the size the brief specifies"

    check = run(["checkMesh"], 300)
    assert check.returncode == 0, check.stdout[-3000:]
    assert "Mesh OK." in check.stdout

    solve = run(["foamRun", "-solver", "incompressibleFluid"], 1200)
    assert solve.returncode == 0, solve.stdout[-4000:]
    assert "SIMPLE solution converged" in solve.stdout, (
        f"the solver did not converge:\n{solve.stdout[-2000:]}"
    )

    # **And the answer is the right number.** 0.075 m/s through a 0.02 x 0.02 m face.
    monitor = case / "postProcessing" / "patchFlowRate(patch=inlet)" / "0" / "surfaceFieldValue.dat"
    assert monitor.is_file(), "the inlet flow-rate monitor wrote nothing"
    rows = [
        line.split()
        for line in monitor.read_text().splitlines()
        if line and not line.startswith("#")
    ]
    assert rows, "the monitor file has no data rows"
    # The last row is the converged state; OpenFOAM writes `time  sum(phi)`.
    try:
        rate = float(rows[-1][1])
    except (IndexError, ValueError) as exc:  # pragma: no cover - a format change
        pytest.fail(f"could not read the flow rate from {rows[-1]}: {exc}")
    expected = 0.075 * SIDE * SIDE
    assert rate == pytest.approx(-expected, rel=0.02) or rate == pytest.approx(
        expected, rel=0.02
    ), f"the inlet flow rate is {rate}, not +/-{expected} (sign depends on the face normal)"

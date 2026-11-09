"""Mesh the duct from scratch, end to end (T-207, T-145b).

    python3 tools/mesh_slice.py <folder>

Builds a box duct, tags its inlet and outlet, configures blockMesh and runs it through the
agent. Prints
`mesh.status` as JSON, so a shell script or a test can read the outcome without importing anything.

**The duct, not the heat sink.** The heat sink's `walls` lies inside the box, so blockMesh
cannot make it a
patch — the mesh is right and the warning says so, which is a fine thing to demonstrate and a
poor thing to
build a slice on. The duct's three tags are all on sides, so the whole pipeline has something to
carry.

T-214 (`tools/e2e_duct.py`) reuses `duct_project`, so the two scripts cannot disagree about the
case.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))


def main(folder: str | Path) -> int:
    """Build and mesh the duct under ``folder``; return 0 when the mesh is done."""
    from stokes.api import Session
    from tools.e2e_support import duct_project

    target = Path(folder)
    target.mkdir(parents=True, exist_ok=True)
    session = Session()
    ids = duct_project(session, target)
    info = session.execute("mesh.generate", wait=True)
    session.execute("project.save")
    status = session.execute("mesh.status")
    print(json.dumps({"info": info, "status": status, "ids": ids}, indent=1))
    return 0 if status.get("state") == "done" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="mesh_slice")
    parser.add_argument("folder", help="where to create the project")
    sys.exit(main(parser.parse_args().folder))

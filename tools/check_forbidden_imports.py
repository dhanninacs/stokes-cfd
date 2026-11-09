"""Gate: forbidden modules and import layering (CLAUDE.md §1 rules 1, 5 and 8).

Scans ``stokes/`` and ``agent/`` with the ast module. Exit 1 with a list of
``file:line`` violations, exit 0 when clean.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = (ROOT / "stokes", ROOT / "agent")

FORBIDDEN: dict[str, str] = {
    "PyQt5": "GPL; use PySide6",
    "PyQt6": "GPL; use PySide6",
    "gmsh": "GPL; drive the gmsh executable as a process",
    "PyFoam": "GPL; use stokes.compile",
    "fluidfoam": "GPL; use VTK readers",
    "tetgen": "AGPL",
    "CGAL": "GPL",
    "pickle": "unsafe serialization; use JSON or msgpack",
    "shelve": "pickle-based",
    "marshal": "unsafe serialization",
    "dill": "pickle-based",
    "cloudpickle": "pickle-based",
}

HEAVY: tuple[str, ...] = (
    "PySide6",
    "scipy",
    "pymoo",
    "sklearn",
    "SALib",
    "moocore",
    "shiboken6",
    "vtk",
    "vtkmodules",
    "OCP",
    "cadquery",
    "build123d",
    "pyqtgraph",
    "CoolProp",
    "cantera",
)
HEAVY_ALLOWED_PREFIXES: tuple[str, ...] = (
    "stokes.app",  # the Qt/Chromium host window only
    "stokes.geometry",  # OCCT
    "stokes.explore",  # scipy, pymoo, scikit-learn, SALib (ADR-0016), lazily inside functions
    "stokes.post",  # server-side VTK extraction
    "stokes.physics.materials",  # CoolProp and Cantera, inside the materials worker process only
)
AGENT_ALLOWED_HEAVY: tuple[str, ...] = ("vtk", "vtkmodules")

LAYER_RULES: dict[str, tuple[str, ...]] = {
    "stokes.units": ("stokes.model", "stokes.api", "stokes.cli", "stokes.ui", "stokes.app"),
    "stokes.model": (
        "stokes.api",
        "stokes.cli",
        "stokes.server",
        "stokes.mcp",
        "stokes.llm",
        "stokes.ui",
        "stokes.app",
        "stokes.viewport",
        "stokes.geometry",
        "stokes.run",
    ),
    "stokes.license": ("stokes.model", "stokes.api"),
    "stokes.api": (
        "stokes.cli",
        "stokes.server",
        "stokes.mcp",
        "stokes.llm",
        "stokes.ui",
        "stokes.app",
        "stokes.viewport",
    ),
    "stokes.compile": ("stokes.run", "stokes.ui", "stokes.app", "stokes.viewport"),
    "stokes.native": ("stokes.model", "stokes.api"),
    # The workspace (ADR-0015) is user data beside the model, read by api commands.
    "stokes.workspace": (
        "stokes.model",
        "stokes.units",
        "stokes.api",
        "stokes.cli",
        "stokes.server",
        "stokes.mcp",
        "stokes.llm",
        "stokes.ui",
        "stokes.app",
        "stokes.viewport",
        "stokes.geometry",
    ),
    # External OpenFOAM cases (ADR-0029): headless, read by api commands, never driving a runner.
    "stokes.cases": (
        "stokes.api",
        "stokes.run",
        "stokes.cli",
        "stokes.server",
        "stokes.mcp",
        "stokes.llm",
        "stokes.ui",
        "stokes.app",
        "stokes.viewport",
        "stokes.geometry",
        "stokes_agent",
    ),
    "stokes_agent": ("stokes.model", "stokes.api", "stokes.ui", "stokes.app", "stokes.viewport"),
}

LAYER_EXCEPTIONS: tuple[tuple[str, str], ...] = (("stokes.model", "stokes.geometry.model"),)
"""Imports a layering rule bans except for one light module. ``stokes.geometry.model`` declares
the node types that ``register_builtin_types`` registers and imports nothing heavy (ADR-0020);
the rest of ``stokes.geometry`` imports OCP. ``stokes.explore.model`` needs no entry here, because
``stokes.explore`` is not among the packages ``stokes.model`` may not import."""


def module_name(path: Path) -> str:
    rel = path.relative_to(ROOT)
    parts = list(rel.with_suffix("").parts)
    if parts and parts[0] == "agent":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def imported_roots(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.lineno, node.module))
    return found


def check_file(path: Path) -> list[str]:
    violations: list[str] = []
    module = module_name(path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [f"{path}:{exc.lineno}: syntax error: {exc.msg}"]
    for lineno, name in imported_roots(tree):
        root = name.split(".")[0]
        where = f"{path.relative_to(ROOT)}:{lineno}"
        if root in FORBIDDEN:
            violations.append(f"{where}: forbidden import {name!r} ({FORBIDDEN[root]})")
        if root in HEAVY:
            allowed = module.startswith(HEAVY_ALLOWED_PREFIXES) or (
                module.startswith("stokes_agent") and root in AGENT_ALLOWED_HEAVY
            )
            if not allowed:
                violations.append(
                    f"{where}: {name!r} may only be imported inside "
                    f"{', '.join(HEAVY_ALLOWED_PREFIXES)} (module {module})"
                )
        for layer, banned in LAYER_RULES.items():
            if module == layer or module.startswith(layer + "."):
                for target in banned:
                    if name == target or name.startswith(target + "."):
                        if (layer, name) in LAYER_EXCEPTIONS:
                            continue
                        violations.append(f"{where}: {layer} must not import {name!r}")
    return violations


def main() -> int:
    violations: list[str] = []
    for directory in SCAN_DIRS:
        for path in sorted(directory.rglob("*.py")):
            violations.extend(check_file(path))
    if violations:
        print("Forbidden imports or layering violations:")
        for line in violations:
            print("  " + line)
        return 1
    print("imports: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())

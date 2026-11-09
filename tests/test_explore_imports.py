"""Import discipline of stokes.explore (ADR-0016, CLAUDE.md section 1 rule 5 and section 5).

scipy, pymoo, scikit-learn and SALib belong to the exploration numerics: they are imported only
inside ``stokes.explore``, and only when a function that needs them runs. Importing the package
(or ``stokes.api``) must not load them.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESTRICTED = {"scipy", "pymoo", "sklearn", "SALib"}  # only stokes.explore may import these
NOT_LOADED = RESTRICTED | {"pandas", "matplotlib"}  # and importing stokes.explore loads none
MODULES = (
    "stokes.explore",
    "stokes.explore.domain",
    "stokes.explore.space",
    "stokes.explore.table",
    "stokes.explore.sampling",
    "stokes.explore.surrogate",
    "stokes.explore.validation",
    "stokes.explore.sensitivity",
    "stokes.explore.problem",
    "stokes.explore.pareto",
    "stokes.explore.optimise",
    "stokes.explore.functions",
)


def imported(path: Path, *, module_level: bool) -> list[tuple[int, str]]:
    """(line, dotted name) of the imports in a file, only the top-level ones if asked."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = tree.body if module_level else list(ast.walk(tree))
    found: list[tuple[int, str]] = []
    for node in nodes:
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.lineno, node.module))
    return found


def run(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False, timeout=180
    )


def test_numerical_packages_are_imported_only_inside_stokes_explore() -> None:
    offenders: list[str] = []
    for directory in ("stokes", "agent"):
        for path in sorted((ROOT / directory).rglob("*.py")):
            relative = path.relative_to(ROOT)
            if relative.parts[:2] == ("stokes", "explore"):
                continue
            offenders.extend(
                f"{relative}:{line}: imports {name}"
                for line, name in imported(path, module_level=False)
                if name.split(".", maxsplit=1)[0] in RESTRICTED
            )
    assert not offenders, "\n".join(offenders)


def test_stokes_explore_has_no_module_level_numerical_imports() -> None:
    offenders: list[str] = []
    for path in sorted((ROOT / "stokes" / "explore").rglob("*.py")):
        offenders.extend(
            f"{path.relative_to(ROOT)}:{line}: {name}"
            for line, name in imported(path, module_level=True)
            if name.split(".", maxsplit=1)[0] in NOT_LOADED
        )
    assert not offenders, "\n".join(offenders)


def test_importing_every_explore_module_loads_no_numerical_package() -> None:
    imports = "; ".join(f"import {module}" for module in MODULES)
    code = (
        f"import sys; {imports}; "
        f"loaded = sorted(m for m in {sorted(NOT_LOADED)!r} if m in sys.modules); "
        "print(loaded); sys.exit(1 if loaded else 0)"
    )
    completed = run(code)
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_the_api_does_not_pull_in_the_exploration_package() -> None:
    # The study node types (ADR-0016) register with the built-in types, so stokes.explore.model
    # (numpy-free) and the package's docstring-only __init__ load with the model; nothing else.
    node_types = ("stokes.explore", "stokes.explore.model")
    code = (
        "import sys, stokes.api; "
        "bad = sorted(m for m in sys.modules if (m.startswith('stokes.explore') and m not in "
        f"{node_types!r}) or m.split('.')[0] in {sorted(RESTRICTED | {'numpy'})!r}); "
        "print(bad); sys.exit(1 if bad else 0)"
    )
    completed = run(code)
    assert completed.returncode == 0, completed.stdout + completed.stderr

"""Import-time budgets measured on Stokes' own modules.

Interpreter start-up and the standard library vary by an order of magnitude
between machines (endpoint security, network shares), so the budget counts
only the self time of ``stokes*`` modules reported by ``-X importtime``.
The total is recorded for information.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from tests.perf.harness import HEADROOM, RESULTS, machine_factor


def import_profile(module: str) -> tuple[float, float]:
    """(self ms of stokes modules, cumulative ms of ``module``) from -X importtime."""
    completed = subprocess.run(
        [sys.executable, "-X", "importtime", "-c", f"import {module}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    own_us = 0
    total_us = 0
    for line in completed.stderr.splitlines():
        if not line.startswith("import time:"):
            continue
        parts = line.split("|")
        if len(parts) != 3:
            continue
        try:
            self_us = int(parts[0].split(":")[1].strip())
            cumulative_us = int(parts[1].strip())
        except ValueError:
            continue
        name = parts[2].strip()
        if name.startswith("stokes"):
            own_us += self_us
        if name == module:
            total_us = cumulative_us
    return own_us / 1000, total_us / 1000


@pytest.mark.perf
@pytest.mark.parametrize(
    ("module", "own_target_ms"),
    [("stokes.api", 120.0), ("stokes.cli.main", 150.0), ("stokes_agent.protocol", 40.0)],
)
def test_import_budget(module: str, own_target_ms: float) -> None:
    # The best of three: endpoint security and a busy CPU add tens of milliseconds of noise to
    # a single run, and the minimum is the stable estimator of the cost that is ours to control.
    own_ms, total_ms = min((import_profile(module) for _ in range(3)), key=lambda t: t[0])
    limit = own_target_ms * machine_factor() * HEADROOM
    with RESULTS.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"name": f"import.{module}", "own_ms": own_ms, "total_ms": total_ms}) + "\n"
        )
    assert own_ms <= limit, (
        f"import {module}: Stokes modules take {own_ms:.0f} ms (budget {limit:.0f} ms); "
        f"total with stdlib {total_ms:.0f} ms"
    )

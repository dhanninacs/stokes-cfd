"""Budget harness: wall-time budgets scaled by a machine factor.

See docs/engineering/performance.md for the budgets and how they are measured.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from functools import cache
from pathlib import Path

RESULTS = Path(__file__).with_name("results.jsonl")
# Seconds the calibration loop takes on the reference machine; factor = measured / reference.
REFERENCE_SECONDS = 0.045
HEADROOM = 1.25


@cache
def machine_factor() -> float:
    """How much slower this machine is than the reference (never below 1.0)."""
    best = float("inf")
    for _ in range(3):
        start = time.perf_counter()
        total = 0
        for i in range(1_000_000):
            total += i
        best = min(best, time.perf_counter() - start)
    return max(1.0, best / REFERENCE_SECONDS)


@contextmanager
def budget(name: str, target_ms: float) -> Iterator[None]:
    """Fail when the block takes longer than ``target_ms`` times machine factor times headroom."""
    start = time.perf_counter()
    yield
    elapsed_ms = (time.perf_counter() - start) * 1000
    limit_ms = target_ms * machine_factor() * HEADROOM
    with RESULTS.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"name": name, "ms": round(elapsed_ms, 3), "limit_ms": round(limit_ms, 3)})
            + "\n"
        )
    assert elapsed_ms <= limit_ms, (
        f"{name}: {elapsed_ms:.1f} ms exceeds budget {limit_ms:.1f} ms "
        f"(target {target_ms} ms, machine factor {machine_factor():.2f})"
    )

# Testing

`CLAUDE.md` §6 is the summary.

## Pyramid

| Layer | What | Where | Runs in CI |
|---|---|---|---|
| Unit | units, expressions, model, commands, API registry, CLI, licence, protocol | `tests/test_*.py` | always |
| Property | expression grammar round-trips, unit conversions, command undo/redo invariants | `tests/test_*_props.py` with `hypothesis` | always |
| Golden | compiled dictionaries and scripts compared to files in `tests/golden/` | `tests/test_compile_*.py` | always |
| Perf | budgets from `docs/engineering/performance.md` | `tests/perf/` (`-m perf`) | always, calibrated |
| UI | Qt widgets and QML offscreen (`QT_QPA_PLATFORM=offscreen`) | `tests/ui/` (`-m ui`) | when PySide6 is installed |
| Engine | real OpenFOAM runs on the smallest cases | `tests/engine/` (`-m engine`) | nightly on a host with OpenFOAM, skipped otherwise |

## Rules

- Every API command has a test that runs it through `Session.execute`,
  checks the result and the emitted events, undoes it, and replays its
  journal record into a fresh session with the same outcome.
- Every error path has a test asserting the `StokesError.code`.
- Every node type has a schema test (defaults valid, JSON schema generated,
  round-trip through persistence).
- Golden files change only with a reviewed diff and a sentence in the PR
  saying why the output changed.
- Fixtures are public or synthetic: generated geometry, OpenFOAM tutorial
  cases, published benchmark data. The IP gate scans `tests/` too.
- Tests are deterministic: fixed seeds, no wall-clock assertions outside
  `perf`, no network.
- A test that needs OpenFOAM, Fluent, a GPU or a display declares it with a
  marker and skips cleanly when the resource is absent.

## Markers

```
unit    default; no marker needed
perf    budget tests; compare against calibrated targets
ui      needs PySide6; runs offscreen
engine  needs an OpenFOAM installation; skipped unless STOKES_ENGINE_TESTS=1
slow    over 10 s; excluded from the default run with -m "not slow"
```

## Coverage

- `stokes.units`, `stokes.model`, `stokes.api`: at least 90 % lines.
- `stokes.compile`, `stokes.engines`: at least 85 %, plus golden files.
- UI packages: smoke coverage (every window and panel constructs offscreen).

## Writing a budget test

```python
import pytest
from tests.perf.harness import budget


@pytest.mark.perf
def test_parameter_set_latency(session_with_project):
    with budget("api.parameter_set", target_ms=5):
        session_with_project.execute(
            "parameter.set", node="/physics/parameters/U_in", name="value", value="3 [m/s]"
        )
```

`budget` measures wall time, scales the target by the machine factor and
records the number in `tests/perf/results.jsonl` for trend plots.

## Commands

```bash
pytest -q                      # unit + property + golden + perf
pytest -q -m "not perf"        # fast loop
pytest -q -m ui                # offscreen Qt
STOKES_ENGINE_TESTS=1 pytest -q -m engine
pytest --cov=stokes --cov-report=term-missing
```

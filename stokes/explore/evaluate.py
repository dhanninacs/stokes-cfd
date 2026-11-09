"""How design points are evaluated: the study layer's ``Evaluator`` protocol and its records.

An evaluator receives a ``StudyDefinition`` (settings, definitions and the base case as JSON) and
a batch of ``PointRequest`` (a design point's index and its inputs, SI per variable name). It
returns one ``PointResult`` per point it finished: ``done`` with outputs (SI per response name)
or ``failed`` with an error text. A failed point is a result, never an exception; an exception
means the whole batch could not run. Batches let a run queue work on several points at once.
``CancelToken`` stops a batch between points: the points not reached get no result and stay
pending. ``on_start`` and ``on_result`` let the caller stream progress (the ``study.point``
events) and may be called from a worker thread.

The ``function`` engine (``engine_function``) is the only evaluator until M3, when a run-queue
evaluator compiles and runs the base case with each point's overrides (ADR-0016 point 3) behind
this same protocol. ``stokes.explore.optimise.Evaluator`` is the numerics' protocol (arrays of
points to arrays of responses); the adapter between the two belongs to ``study.optimise``.

Import rule: the standard library and ``stokes.explore.definition``; no numpy.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

from stokes.explore.definition import StudyDefinition

ResultStatus = Literal["done", "failed"]


@dataclass(frozen=True)
class PointRequest:
    """One design point to evaluate: its table index and its inputs (SI, by variable name)."""

    index: int
    inputs: Mapping[str, float]


@dataclass(frozen=True)
class PointResult:
    """What became of one point. ``run`` is the run bundle a run-queue evaluator made (ADR-0006);
    the ``function`` engine makes none."""

    index: int
    status: ResultStatus
    outputs: Mapping[str, float] = field(default_factory=dict)
    error: str | None = None
    run: str | None = None


class CancelToken:
    """Set once to stop a batch between points; safe to set from any thread."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


StartCallback = Callable[[int], None]
ResultCallback = Callable[[PointResult], None]


class Evaluator(Protocol):
    """Turns design points into responses."""

    @property
    def name(self) -> str:
        """The engine name recorded with the results (``function``)."""
        ...

    def evaluate(
        self,
        study: StudyDefinition,
        points: Sequence[PointRequest],
        *,
        cancel: CancelToken | None = None,
        on_start: StartCallback | None = None,
        on_result: ResultCallback | None = None,
    ) -> list[PointResult]:
        """Evaluate ``points`` in order; stop between points once ``cancel`` is set."""
        ...

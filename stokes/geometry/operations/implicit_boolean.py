"""The ``implicit_boolean`` operation (ADR-0035 decision 7).

It is built from a recipe by the worker's implicit job; the builder is
:func:`stokes.geometry.operations.implicit.boolean`. This module exists so the operation framework
finds the type built (``operations.is_built``)."""

from __future__ import annotations

from stokes.geometry.operations.implicit import run

__all__ = ["run"]

"""The ``implicit_lattice`` operation (ADR-0035 decision 7, slice 3).

It is built from a recipe by the worker's implicit job; the builder is in
:mod:`stokes.geometry.operations.implicit`. This module exists so the operation framework finds the
type built (``operations.is_built``)."""

from __future__ import annotations

from stokes.geometry.operations.implicit import run

__all__ = ["run"]

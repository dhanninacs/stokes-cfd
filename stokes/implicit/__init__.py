"""Implicit geometry (ADR-0035): parts defined by a closed-form field, negative inside.

The package holds what the modeller needs without a CAD kernel or a display:

- :mod:`.graph`: the expression graph over x, y and z, its limits, and ``to_text``, which writes it
  in the text format of the mesher (Fidget's);
- :mod:`.evaluate`: a numpy evaluator of the graph in float64, the reference for checks and tests;
- :mod:`.frame`: affine frames composed in float64 and applied at the coordinate leaves;
- :mod:`.features`: bodies (field, bounds, smallest feature, metric) and the primitive and boolean
  builders;
- :mod:`.meshfile`: the checked reader of the mesher's output;
- :mod:`.runner`: finding, running and caching the ``stokes-implicit-mesh`` program.

Lengths: the operations hand this package millimetres in a frame chosen by the caller (decision 6);
SI conversion happens in :mod:`stokes.geometry.operations.implicit`.

Import rule (ADR-0035 decision 2): the standard library, :mod:`stokes.errors` and
:mod:`stokes.log` at module level, numpy inside functions; nothing else of ``stokes``, and no Qt,
VTK or OCP. :mod:`stokes.geometry`, :mod:`stokes.explore` and :mod:`stokes.api` may import it.
"""

from __future__ import annotations

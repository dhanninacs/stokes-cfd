"""Physics as declarative tables: materials and boundary conditions now, the physics models later.

Built: ``materials`` (ADR-0018: the ``material`` and ``property`` nodes, the method catalogue,
evaluation, checks and library fits) and ``conditions`` (ADR-0022: the condition catalogue and its
errors; the ``boundary`` and ``region`` node types are declared with the geometry types in
``stokes.geometry.model``). Planned: the set-up check's findings in ``conditions`` (T-037), and
the physics models node with its catalogue (energy, turbulence, transient) in M3.

Import rule: headless; each package's docstring gives its own rule. ``stokes.model.schema`` loads
``materials.model`` at start-up, and the command modules load ``conditions.catalogue``; neither
imports anything heavy at module level.
"""

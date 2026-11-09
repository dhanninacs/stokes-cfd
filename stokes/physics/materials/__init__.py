"""Materials (ADR-0018): ``material`` and ``property`` nodes, the method catalogue, evaluation,
consistency checks and the property providers.

Modules:

- ``model``: the node types, registered with the built-in types by ``stokes.model.schema``.
- ``catalogue``: the declarative table of methods per property and material kind.
- ``methods``: the numpy evaluators of the catalogue's formulas.
- ``evaluate``: reads property nodes (or library records) and evaluates them on a temperature grid.
- ``checks``: the consistency findings ``material.check`` reports.
- ``authoring``: the undo steps the ``material.*`` commands push, and the templates.
- ``adding``: what ``material.create`` and ``material.add_from_library`` build.
- ``fit``: library data fitted to catalogue methods (ADR-0018 slice 2).
- ``library``: library materials: their state, fits, provenance, stale and detached rules.
- ``service``: the session's materials service: background fits, the evaluation cache, the
  listeners that mark hand edits detached and changed states stale.
- ``worker`` and ``worker_main``: the materials worker process, the only place CoolProp and
  Cantera are imported.
- ``providers``: the ``Provider`` protocol, the built-in catalogue (``data/materials.json``) and
  the library providers (CoolProp, CoolProp INCOMP, Cantera, REFPROP through CoolProp).
- ``errors``: the typed ``materials.*`` errors.

Contract for clients: ``docs/engineering/materials-schema.md``.

Import rule: headless. ``model`` and ``catalogue`` import only ``stokes.units`` and the standard
library, because ``stokes.model.schema`` loads ``model`` at start-up; numpy is imported by
``methods``, ``evaluate``, ``checks``, ``fit`` and the providers, which load only when a command
needs them. CoolProp and Cantera are imported only by ``providers.coolprop``,
``providers.cantera`` and ``providers.refprop`` inside the worker process. This package imports
nothing from ``stokes.api`` at run time.
"""
